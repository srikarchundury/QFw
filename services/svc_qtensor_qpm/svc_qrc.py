import os
import sys
import shutil
import yaml
import logging
from time import perf_counter

from util.mpi import backend_wrapper, build_mpi_command_string
from util.qpm.util_qrc import UTIL_QRC
from defw_exception import DEFwError, DEFwExecutionError

sys.path.append(os.path.split(os.path.abspath(__file__))[0])


class QRC(UTIL_QRC):
	def __init__(self, start=True):
		logging.debug("QTENSOR_QRC: init")
		super().__init__(start=start)

	# -------------------------
	# Parsing
	# -------------------------
	def parse_result(self, out):
		t0 = perf_counter()

		if out is None:
			raise DEFwError({"Error": "Empty output (None)"})

		if isinstance(out, bytes):
			out_str = out.decode("utf-8", errors="replace")
		else:
			out_str = str(out)

		out_str = out_str.strip()
		if not out_str:
			raise DEFwError({"Error": "Empty output!"})

		counts = None
		for line in out_str.splitlines():
			# expected: counts = {...}
			if "counts" not in line or "=" not in line:
				continue
			try:
				payload = line.split("=", 1)[1].strip()
				counts = yaml.safe_load(payload)
				break
			except Exception as e:
				raise DEFwError({"Error": f"Failed to parse counts line: {e}", "line": line})

		if counts is None:
			raise DEFwError({"Error": "No 'counts = ...' line found in output", "raw": out_str})

		t1 = perf_counter()
		logging.debug(f"QTENSOR_QRC: parse_result took {t1 - t0:.6f}s")
		return counts

	# -------------------------
	# Options
	# -------------------------
	def _get_qpm_options(self, info):
		return info.get("qpm_options", {}) or {}

	def _get_backend_choice(self, info):
		allowed = {"cupy", "numpy", "pytorch"}
		qpm_opts = self._get_qpm_options(info)

		backend = str(qpm_opts.get("backend", "numpy")).lower()
		if backend not in allowed:
			logging.debug(f"QTENSOR_QRC: invalid backend '{backend}', using 'numpy'")
			backend = "numpy"
		return backend

	def _get_device_choice(self, info):
		qpm_opts = self._get_qpm_options(info)
		device = str(qpm_opts.get("device", "CPU")).upper()
		if device not in ("CPU", "GPU"):
			logging.debug(f"QTENSOR_QRC: invalid device '{device}', using 'CPU'")
			device = "CPU"
		return device

	# -------------------------
	# Required UTIL_QRC method
	# -------------------------
	def form_cmd(self, circ, qasm_file):
		info = circ.info

		circuit_runner = shutil.which(info['qfw_backend'])
		if not circuit_runner:
			raise DEFwExecutionError("QTENSOR_QRC: couldn't find circuit_runner. Check paths")

		dvm = os.environ.get("QFW_DVM_URI_PATH", "").strip()
		if dvm and not os.path.exists(dvm):
			raise DEFwExecutionError(f"QTENSOR_QRC: dvm-uri {dvm} doesn't exist")

		backend = self._get_backend_choice(info)
		device = self._get_device_choice(info)  # parsed; only used if runner supports it

		executable = circuit_runner
		executable_args = []
		wrapper = backend_wrapper('qtensor')
		if wrapper:
			executable = shutil.which(wrapper)
			if not executable:
				raise DEFwExecutionError(f"QTENSOR_QRC: couldn't find {wrapper}. Check paths")
			executable_args.extend(['-v', circuit_runner])

		executable_args.extend(['-q', qasm_file, '--backend', backend])
		if "num_shots" in info:
			executable_args.extend(['-s', str(int(info['num_shots']))])

		cmd = build_mpi_command_string(
			executable,
			executable_args=executable_args,
			np=info["np"],
			hosts=info.get("hosts", None),
			dvm_uri=dvm
		)

		logging.debug(f"QTENSOR_QRC: backend={backend} device={device}")
		logging.debug(f"QTENSOR_QRC: CMD={cmd}")
		return cmd

	def test(self):
		return "****Testing the QTensor QRC****"
