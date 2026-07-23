import os, sys, subprocess, sysconfig, fcntl, time

def _path_points_to(path, target):
	return os.path.islink(path) and os.path.realpath(path) == os.path.realpath(target)


def _set_python_links(python_paths, target):
	for path in python_paths:
		if os.path.lexists(path):
			os.unlink(path)
		os.symlink(target, path)


# The venv's python/python3/pythonX.Y symlinks are swapped to point at
# defwp-wrapper for the duration of a job (qfw_setup.sh through
# qfw_teardown.sh/qfw_deactivate), and restored afterward. QFW_VENV_PATH is
# shared filesystem state across every concurrently-running job, so without
# coordination two overlapping jobs stomp on each other: one job's teardown
# can restore the venv out from under another job that's still executing
# circuits through the swapped python3, and two jobs swapping in at once can
# leave the backup/live symlinks in a state neither setup_qfw_symlinks() nor
# restore_symlinks() recognizes ("unexpected state").
#
# QFW_RUN_ID is unique per job (see qfw_run_tmp.sh) and is exported into
# every phase/node of that job's lifecycle (main script, remote ssh setup,
# teardown), so it's a stable key for "is this job still using the swapped
# venv" across the several separate setup_qfw_symlinks()/restore_symlinks()
# calls one job makes. We register/deregister a per-job marker file under a
# lock, and only actually swap in on the first active job / restore on the
# last one out.
_STALE_MARKER_SECS = 48 * 60 * 60  # longer than any realistic job walltime


def _swap_session_keys():
	"""All keys this job could plausibly have registered a marker under.

	A single job calls setup_qfw_symlinks() from several different shells
	over its lifetime (the main script's own initial `source qfw_activate`,
	remote ssh phases for PRTE/service setup, ...), and they don't all see
	the same environment: QFW_RUN_ID only exists once qfw_setup.sh has
	created it (so the *first* activation, before that, has nothing to key
	on but SLURM_JOB_ID), while the remote ssh phases have QFW_RUN_ID
	explicitly exported into them. So different phases of one job can end
	up registering under different keys. By the time restore_symlinks()
	runs (in the main script's shell, after qfw_setup.sh has run), both
	QFW_RUN_ID and SLURM_JOB_ID are available there, so it can and should
	clean up whichever one(s) got used, rather than guessing a single key
	and leaving the other orphaned.
	"""
	keys = []
	run_id = os.environ.get('QFW_RUN_ID')
	slurm_id = os.environ.get('SLURM_JOB_ID')
	if run_id:
		keys.append(run_id)
	if slurm_id and slurm_id not in keys:
		keys.append(slurm_id)
	if not keys:
		keys.append(str(os.getppid()))
	return keys


def _swap_session_key():
	# The single preferred key to register a NEW marker under: QFW_RUN_ID
	# when available (stable across every phase of the job that has it),
	# else SLURM_JOB_ID (stable across the whole job in the shells that
	# don't), else the calling process's own parent pid for pure
	# interactive/non-Slurm use.
	return _swap_session_keys()[0]


def _active_markers_dir():
	d = os.path.join(os.environ['QFW_VENV_PATH'], '.qfw_venv_swap_active')
	os.makedirs(d, exist_ok=True)
	return d


def _lock_path():
	return os.path.join(os.environ['QFW_VENV_PATH'], '.qfw_venv_swap.lock')


class _VenvSwapLock:
	def __enter__(self):
		self._fd = os.open(_lock_path(), os.O_CREAT | os.O_RDWR, 0o644)
		fcntl.flock(self._fd, fcntl.LOCK_EX)
		return self

	def __exit__(self, *exc_info):
		fcntl.flock(self._fd, fcntl.LOCK_UN)
		os.close(self._fd)


def _prune_stale_markers(markers_dir, keep):
	now = time.time()
	for name in os.listdir(markers_dir):
		if name == keep:
			continue
		path = os.path.join(markers_dir, name)
		try:
			if now - os.path.getmtime(path) > _STALE_MARKER_SECS:
				os.remove(path)
		except OSError:
			pass


def setup_qfw_symlinks():
	defw = os.path.join(os.environ['DEFW_PATH'], 'src', 'defwp')
	defw_wrapper = os.path.join(os.environ['DEFW_PATH'], 'src', 'defwp-wrapper')
	venv_path = os.path.join(os.environ['QFW_VENV_PATH'], 'bin')

	py_version = sys.version.split()[0].strip()
	rc = subprocess.run([defw, "--py-version"], capture_output=True, text=True)
	if rc.returncode != 0:
		raise ValueError(f"{rc.returncode}: {rc.stderr.strip()}:-- {os.environ['PATH']}:{sys.base_exec_prefix} --")
	defw_version = rc.stdout.strip()
	if py_version != defw_version:
		raise ValueError(f"VENV python version ({py_version}) mismatches with defw version ({defw_version})." \
						  "DEFW must be compiled with the same version as the venv python version")

	py = f"python{sys.version_info.major}.{sys.version_info.minor}"
	python_paths = [
		os.path.join(venv_path, "python"),
		os.path.join(venv_path, "python3"),
		os.path.join(venv_path, py),
	]
	backup_paths = [
		os.path.join(venv_path, "python_defw_orig"),
		os.path.join(venv_path, "python3_defw_orig"),
		os.path.join(venv_path, py+"_defw_orig"),
	]

	with _VenvSwapLock():
		markers_dir = _active_markers_dir()
		key = _swap_session_key()
		marker = os.path.join(markers_dir, key)
		_prune_stale_markers(markers_dir, keep=key)

		if os.path.exists(marker):
			# This same job already registered (and validated/performed)
			# the swap in an earlier phase; nothing more to do.
			os.utime(marker, None)
			return

		other_active = len(os.listdir(markers_dir)) > 0
		open(marker, 'w').close()

		if other_active:
			# Another job already has the venv swapped in for us.
			if not all(_path_points_to(p, defw_wrapper) for p in python_paths):
				raise RuntimeError("System is in an unexpected state")
			return

		backups_exist = [os.path.exists(p) for p in backup_paths]
		if any(backups_exist):
			if all(backups_exist):
				if all(_path_points_to(p, defw_wrapper) for p in python_paths):
					return
				if all(_path_points_to(p, defw) for p in python_paths):
					_set_python_links(python_paths, defw_wrapper)
					return
			raise RuntimeError("System is in an unexpected state")

		try:
			os.replace(os.path.join(venv_path, "python"), os.path.join(venv_path, "python_defw_orig"))
			os.replace(os.path.join(venv_path, "python3"), os.path.join(venv_path, "python3_defw_orig"))
			os.replace(os.path.join(venv_path, py), os.path.join(venv_path, py+"_defw_orig"))
			_set_python_links(python_paths, defw_wrapper)
		except Exception as e:
			print("Failed to configure system properly")
			raise e


def restore_symlinks():
	venv_path = os.path.join(os.environ['QFW_VENV_PATH'], 'bin')
	py = f"python{sys.version_info.major}.{sys.version_info.minor}"

	with _VenvSwapLock():
		markers_dir = _active_markers_dir()
		keys = _swap_session_keys()

		for key in keys:
			marker = os.path.join(markers_dir, key)
			if os.path.exists(marker):
				os.remove(marker)
		_prune_stale_markers(markers_dir, keep=keys[0])

		if os.listdir(markers_dir):
			# Other jobs still depend on the swapped-in venv; leave it.
			return

		if not os.path.exists(os.path.join(venv_path, "python_defw_orig")) or \
		   not os.path.exists(os.path.join(venv_path, "python3_defw_orig")) or \
		   not os.path.exists(os.path.join(venv_path, py+"_defw_orig")):
			   raise RuntimeError("System is in an unexpected state")

		os.replace(os.path.join(venv_path, "python_defw_orig"), os.path.join(venv_path, "python"))
		os.replace(os.path.join(venv_path, "python3_defw_orig"), os.path.join(venv_path, "python3"))
		os.replace(os.path.join(venv_path, py+"_defw_orig"), os.path.join(venv_path, py))
