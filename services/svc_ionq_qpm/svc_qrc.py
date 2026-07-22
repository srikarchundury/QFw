import os
import time
import logging
import yaml
from time import perf_counter

from util.qpm.util_qrc import UTIL_QRC
from qiskit.circuit import QuantumCircuit
from qiskit.transpiler import preset_passmanagers  # <-- NEW
from defw_exception import DEFwError
from api_events import Event

from qbacmet import collector  # <-- QBacMet

CURRENT_PATH = os.path.split(os.path.abspath(__file__))[0]


class QRC(UTIL_QRC):
	def __init__(self, start=True):
		logging.debug("IONQ_QRC: init starting")
		super().__init__(start=start)

		cfg_path = os.path.join(CURRENT_PATH, "ionq_env.yaml")
		self.load_ionq_env_yaml(cfg_path)

		logging.debug("IONQ_QRC: init done")

	def load_ionq_env_yaml(self, path=None):
		if path is None:
			path = os.path.join(CURRENT_PATH, "ionq_env.yaml")

		if not os.path.exists(path):
			raise FileNotFoundError(f"IONQ_QRC: YAML config not found: {path}")

		with open(path, "r") as f:
			cfg = yaml.safe_load(f) or {}

		ionq_cfg = cfg.get("CONFIG", {}) or {}
		if not isinstance(ionq_cfg, dict):
			raise DEFwError("IONQ_QRC: YAML CONFIG section is not a dict")

		for key, value in ionq_cfg.items():
			if value is None:
				logging.warning(f"IONQ_QRC: config field {key} is None (skipping)")
				continue
			os.environ[str(key)] = str(value)

	def form_cmd(self, circ, qasm_file):
		raise DEFwError("IONQ_QRC does not use local command execution!")

	# -------------------------
	# Metrics helper (QBacMet)
	# -------------------------
	def _attach_metrics(self, r):
		"""
		Attach qbacmet collector metrics into the result dict (best-effort).

		We attach to BOTH:
		  - the outer dict
		  - the inner "result" dict (if present)
		so even if QFw only forwards `result`, metrics survive.
		"""
		try:
			m = collector.metrics
			if not isinstance(m, dict) or not m:
				logging.debug("IONQ_QRC: _attach_metrics – collector.metrics empty or not dict, skipping attach")
				return r

			logging.debug(
				"IONQ_QRC: _attach_metrics – attaching qbacmet metrics with top-level keys: %s",
				list(m.keys()),
			)

			if isinstance(r, dict):
				# top-level
				r["qbacmet_metrics"] = m

				# also inside the "result" payload if it's a dict
				res = r.get("result", None)
				if isinstance(res, dict):
					res["qbacmet_metrics"] = m
		except Exception as e:
			# Never let metrics attachment break normal flow
			logging.debug(f"IONQ_QRC: _attach_metrics – exception while attaching metrics: {e}")
		return r

	# -------------------------
	# Result formatting
	# -------------------------
	def parse_result(self, counts):
		try:
			return {str(k): int(v) for k, v in counts.items()}
		except Exception:
			return counts

	# -------------------------
	# Return helpers
	# -------------------------
	def _ok_ret(self, circ, result):
		r = {
			"cid": circ.get_cid(),
			"result": result,
			"rc": 0,
			"launch_time": circ.launch_time,
			"creation_time": circ.creation_time,
			"exec_time": circ.exec_time,
			"completion_time": circ.completion_time,
			"resources_consumed_time": circ.resources_consumed_time,
			"cq_enqueue_time": time.time(),
			"cq_dequeue_time": -1,
		}
		logging.debug("IONQ_QRC: _ok_ret – before metrics attach")
		return self._attach_metrics(r)

	def _error_ret(self, circ, msg, raw=None):
		out = {
			"cid": circ.get_cid(),
			"result": {"error": msg},
			"rc": -1,
			"launch_time": circ.launch_time,
			"creation_time": circ.creation_time,
			"exec_time": circ.exec_time,
			"completion_time": circ.completion_time,
			"resources_consumed_time": circ.resources_consumed_time,
			"cq_enqueue_time": time.time(),
			"cq_dequeue_time": -1,
		}
		if raw is not None:
			out["result"]["raw"] = raw
		logging.debug("IONQ_QRC: _error_ret – before metrics attach, msg=%s", msg)
		return self._attach_metrics(out)

	def _finalize_task(self, circ, result_obj, rc, err_msg=None):
		r = {
			"cid": circ.get_cid(),
			"result": result_obj if err_msg is None else {"error": err_msg, "raw": result_obj},
			"rc": rc,
			"launch_time": circ.launch_time,
			"creation_time": circ.creation_time,
			"exec_time": circ.exec_time,
			"completion_time": circ.completion_time,
			"resources_consumed_time": circ.resources_consumed_time,
			"cq_enqueue_time": time.time(),
			"cq_dequeue_time": -1,
		}
		logging.debug("IONQ_QRC: _finalize_task – before metrics attach, rc=%s", rc)
		r = self._attach_metrics(r)

		if self.push_info:
			try:
				event = Event(self.push_info["evtype"], r)
				self.push_info["class"].put(event)
			except Exception as e:
				logging.critical(f"IONQ_QRC: failed to push event cid={circ.get_cid()}: {e}")
				with self.circuit_results_lock:
					self.circuit_results.append(r)
		else:
			with self.circuit_results_lock:
				self.circuit_results.append(r)

		return r

	# -------------------------
	# Provider/backend helpers
	# -------------------------
	def _get_provider(self, circ):
		provider = circ.info.get("qfw_backend", None)
		if provider is None:
			raise DEFwError("IONQ_QRC: IonQ provider not set in circuit info (qfw_backend)")
		return provider

	def _get_backend(self, provider, circ):
		qpm_opts = circ.info.get("qpm_options", {}) or {}
		backend_name = str(qpm_opts.get("backend", "simulator")).strip()

		# IonQ online naming normalization.
		# Examples from scripts:
		#   simulator, ideal, aria-1, aria-2, forte-1, forte-enterprise-1, harmony
		# We map hardware-like names to qpu.<name> so they become real portal jobs.
		if backend_name in ("simulator", "ideal"):
			resolved_backend_name = "simulator"
		elif backend_name.startswith("qpu."):
			resolved_backend_name = backend_name
		elif backend_name.startswith("ionq_qpu."):
			resolved_backend_name = backend_name.replace("ionq_qpu.", "qpu.", 1)
		elif backend_name in {"aria-1", "aria-2", "forte-1", "forte-enterprise-1", "harmony"}:
			resolved_backend_name = f"qpu.{backend_name}"
		else:
			resolved_backend_name = backend_name

		noise_model = qpm_opts.get("noise_model", "ideal")
		VALID_NOISE_MODELS = {"ideal", "aria-1", "aria-2", "forte-1", "forte-enterprise-1", "harmony"}
		if noise_model not in VALID_NOISE_MODELS:
			logging.warning(
				"IONQ_QRC: noise_model '%s' not in %s — defaulting to 'ideal'.",
				noise_model, VALID_NOISE_MODELS,
			)
			noise_model = "ideal"

		try:
			if resolved_backend_name == "simulator":
				# qiskit-ionq 0.5.x expects get_backend("simulator") without noise_model filtering.
				# Keep noise_model only as metadata for run-time behavior elsewhere.
				backend = provider.get_backend("simulator")
				display_name = f"simulator({noise_model})"
			else:
				backend = provider.get_backend(resolved_backend_name)
				display_name = resolved_backend_name

			logging.info(
				"IONQ_QRC: _get_backend – requested=%s resolved=%s noise_model=%s",
				backend_name,
				resolved_backend_name,
				noise_model,
			)
			return backend, display_name
		except Exception as e:
			raise DEFwError(
				f"IONQ_QRC: failed to get backend requested='{backend_name}' "
				f"resolved='{resolved_backend_name}' noise_model='{noise_model}': {e}"
			)

	def _job_id_safe(self, job):
		try:
			return job.job_id()
		except Exception:
			return None

	def _wait_job(self, job, backend_name, poll_s=5, timeout_s=3600):
		t0 = time.time()
		job_id = self._job_id_safe(job)

		while True:
			try:
				st = job.status()
				st_name = getattr(st, "name", str(st)).upper()
			except Exception as e:
				st_name = f"UNKNOWN({e})"

			elapsed = int(time.time() - t0)
			if st_name in ("DONE", "COMPLETED"):
				return
			if st_name in ("ERROR", "FAILED", "CANCELLED", "CANCELED"):
				detail = ""
				try:
					# IonQ often exposes concrete API-side reasons via result() exception text.
					job.result()
				except Exception as e:
					detail = f" detail={e}"
				raise DEFwError(
					f"IONQ_QRC: job failed backend={backend_name} job_id={job_id} status={st_name}{detail}"
				)

			if elapsed >= timeout_s:
				raise DEFwError(
					f"IONQ_QRC: timeout waiting for backend={backend_name} "
					f"job_id={job_id} elapsed={elapsed}s"
				)

			logging.debug(
				"IONQ_QRC: wait job_id=%s backend=%s status=%s elapsed_s=%s",
				job_id,
				backend_name,
				st_name,
				elapsed,
			)
			time.sleep(poll_s)

	def _build_qc(self, circ):
		if "qasm" not in circ.info:
			raise DEFwError("IONQ_QRC: circ.info['qasm'] missing")
		try:
			return QuantumCircuit.from_qasm_str(circ.info["qasm"])
		except Exception as e:
			raise DEFwError(f"IONQ_QRC: invalid QASM: {e}")

	def _compile(self, qc, backend, circ):
		"""
		Explicit transpilation step so QBacMet sees Layer-2 metrics.

		We call qiskit's preset pass manager. Any QBacMet monkey-patch on
		generate_preset_pass_manager() will hook here automatically.
		"""
		qpm_opts = circ.info.get("qpm_options", {}) or {}
		opt_level = int(qpm_opts.get("optimization_level", 1))
		target = getattr(backend, "target", None)
		logging.debug(
			"IONQ_QRC: _compile – calling generate_preset_pass_manager opt_level=%s target=%s",
			opt_level,
			type(target),
		)
		try:
			pm = preset_passmanagers.generate_preset_pass_manager(
				target=target,
				optimization_level=opt_level
			)
			isa_qc = pm.run(qc)

			# Optional: record transpile features in QBacMet
			try:
				if getattr(collector, "collecting", False):
					collector.transpile_layer.features.setdefault("optimization_level", opt_level)
					collector.transpile_layer.features.setdefault("target", str(type(target)))
			except Exception as e:
				logging.debug("IONQ_QRC: _compile – transpile feature inject failed: %s", e)

			return isa_qc
		except Exception as e:
			logging.debug(
				"IONQ_QRC: _compile – preset_pass_manager failed (%s), using original circuit",
				e,
			)
			return qc

	# -------------------------
	# Sync execution (impl + wrapper)
	# -------------------------
	def _run_circuit_impl(self, circ):
		"""
		Internal implementation of run_circuit.
		Wrapped by qbacmet.collector in run_circuit().
		"""
		logging.debug(f"IONQ_QRC: _run_circuit_impl cid={circ.get_cid()}")
		try:
			provider = self._get_provider(circ)
			backend, backend_name = self._get_backend(provider, circ)
			qc = self._build_qc(circ)
			shots = int(circ.info.get("num_shots", 1024))

			# ---- explicit transpilation ----
			t0 = perf_counter()
			isa_qc = self._compile(qc, backend, circ)
			t1 = perf_counter()
			compile_time = float(t1 - t0)
			logging.debug("IONQ_QRC: _run_circuit_impl – compile_time=%.6fs", compile_time)

			# ---- minimal QBacMet injection (algorithm + backend + transpile_time) ----
			try:
				if getattr(collector, "collecting", False):
					logging.debug("IONQ_QRC: _run_circuit_impl – collector.collecting True, injecting alg/backend metrics")
					# Algorithm layer
					try:
						collector.alg_layer.metrics.setdefault("framework", "qiskit")
						collector.alg_layer.metrics["num_qubits"] = int(getattr(qc, "num_qubits", 0))
						collector.alg_layer.metrics["num_clbits"] = int(getattr(qc, "num_clbits", 0))
						collector.alg_layer.metrics["shots"] = int(shots)
					except Exception as e:
						logging.debug("IONQ_QRC: _run_circuit_impl – alg metrics injection failed: %s", e)

					# Backend features
					try:
						collector.backend_layer.set_backend_details(backend)
					except Exception as e:
						logging.debug("IONQ_QRC: _run_circuit_impl – backend details set failed: %s", e)

					# Transpile time into execution layer (Layer-4)
					try:
						prev_tt = collector.execution_layer.metrics.get("transpile_time", 0.0) or 0.0
						collector.execution_layer.metrics["transpile_time"] = float(prev_tt) + compile_time
					except Exception as e:
						logging.debug("IONQ_QRC: _run_circuit_impl – transpile_time inject failed: %s", e)
				else:
					logging.debug("IONQ_QRC: _run_circuit_impl – collector.collecting False, no alg/backend injection")
			except Exception as e:
				logging.debug("IONQ_QRC: _run_circuit_impl – exception in alg/backend injection: %s", e)
			# ---------------------------------------------------------

		except Exception as e:
			circ.set_fail()
			return self._error_ret(circ, str(e))

		circ.set_launching()
		circ.set_running()

		# ---- transpile-only mode: skip cloud execution entirely ----
		if os.environ.get("QFW_TRANSPILE_ONLY", "").strip() == "1":
			logging.info("IONQ_QRC: QFW_TRANSPILE_ONLY=1 – returning synthetic counts after transpile")
			nq = getattr(isa_qc, "num_qubits", getattr(qc, "num_qubits", 1))
			parsed = {"0" * nq: shots}
			circ.set_exec_done()
			return self._ok_ret(circ, parsed)

		try:
			qpm_opts = circ.info.get("qpm_options", {}) or {}
			poll_s = int(qpm_opts.get("poll_s", os.getenv("IONQ_DEFAULT_POLL_S", "5")))
			timeout_s = int(qpm_opts.get("timeout_s", os.getenv("IONQ_DEFAULT_TIMEOUT_S", "3600")))

			# submit timing
			t0 = perf_counter()
			logging.info("IONQ_QRC: submitting sync job backend=%s shots=%s", backend_name, shots)
			job = backend.run(isa_qc, shots=shots)
			t1 = perf_counter()
			submit_time = float(t1 - t0)
			job_id = self._job_id_safe(job)
			logging.info("IONQ_QRC: submitted sync job backend=%s job_id=%s", backend_name, job_id)

			# wait timing
			t0 = perf_counter()
			self._wait_job(job, backend_name, poll_s=poll_s, timeout_s=timeout_s)
			try:
				counts = job.get_counts()
			except Exception:
				result_obj = job.result()
				counts = result_obj.get_counts()
			t1 = perf_counter()
			execution_time = float(t1 - t0)

			# parse timing
			t0 = perf_counter()
			parsed = self.parse_result(counts)
			t1 = perf_counter()
			result_marshalling_time = float(t1 - t0)

			# ---- execution + postprocess metrics ----
			try:
				if getattr(collector, "collecting", False):
					logging.debug(
						"IONQ_QRC: _run_circuit_impl – injecting timing metrics: submit=%.6fs exec=%.6fs parse=%.6fs",
						submit_time,
						execution_time,
						result_marshalling_time,
					)
					collector.execution_layer.metrics["submit_time"] = submit_time
					collector.execution_layer.metrics["execution_time"] = execution_time
					collector.postprocess_layer.metrics["result_marshalling_time"] = result_marshalling_time
			except Exception as e:
				logging.debug("IONQ_QRC: _run_circuit_impl – exception in timing metrics injection: %s", e)
			# -----------------------------------------

			circ.set_exec_done()
			return self._ok_ret(circ, parsed)

		except Exception as e:
			msg = f"IONQ_QRC: run_circuit exception on backend={backend_name}: {e}"
			logging.exception(msg)
			circ.set_fail()
			return self._error_ret(circ, msg)

	def run_circuit(self, circ):
		"""
		Sync execution entry point wrapped with qbacmet.collector.
		"""
		logging.debug("IONQ_QRC: run_circuit – entering collector.collect()")
		with collector.collect():
			result = self._run_circuit_impl(circ)
			logging.debug(
				"IONQ_QRC: run_circuit – inside collect, partial collector.metrics keys: %s",
				list(collector.metrics.keys()) if isinstance(collector.metrics, dict) else type(collector.metrics),
			)

		# After exiting, collector.auto_collect_all() has run.
		# Re-attach to ensure final metrics are captured.
		logging.debug(
			"IONQ_QRC: run_circuit – after collect, final collector.metrics keys: %s",
			list(collector.metrics.keys()) if isinstance(collector.metrics, dict) else type(collector.metrics),
		)
		result = self._attach_metrics(result)
		logging.debug("IONQ_QRC: run_circuit – result type=%s", type(result))
		return result

	# -------------------------
	# Async execution (impl + wrapper)
	# -------------------------
	def _run_circuit_async_impl(self, circ):
		logging.debug(f"IONQ_QRC: _run_circuit_async_impl cid={circ.get_cid()}")

		provider = self._get_provider(circ)
		backend, backend_name = self._get_backend(provider, circ)
		qc = self._build_qc(circ)
		shots = int(circ.info.get("num_shots", 1024))

		# compile + basic metrics in async path
		t0 = perf_counter()
		isa_qc = self._compile(qc, backend, circ)
		t1 = perf_counter()
		compile_time = float(t1 - t0)
		logging.debug("IONQ_QRC: _run_circuit_async_impl – compile_time=%.6fs", compile_time)

		# minimal alg/backend metrics in async path
		try:
			if getattr(collector, "collecting", False):
				logging.debug("IONQ_QRC: _run_circuit_async_impl – collector.collecting True, injecting alg/backend metrics")
				try:
					collector.alg_layer.metrics.setdefault("framework", "qiskit")
					collector.alg_layer.metrics["num_qubits"] = int(getattr(qc, "num_qubits", 0))
					collector.alg_layer.metrics["num_clbits"] = int(getattr(qc, "num_clbits", 0))
					collector.alg_layer.metrics["shots"] = int(shots)
				except Exception as e:
					logging.debug("IONQ_QRC: _run_circuit_async_impl – alg metrics injection failed: %s", e)

				try:
					collector.backend_layer.set_backend_details(backend)
				except Exception as e:
					logging.debug("IONQ_QRC: _run_circuit_async_impl – backend details set failed: %s", e)

				try:
					prev_tt = collector.execution_layer.metrics.get("transpile_time", 0.0) or 0.0
					collector.execution_layer.metrics["transpile_time"] = float(prev_tt) + compile_time
				except Exception as e:
					logging.debug("IONQ_QRC: _run_circuit_async_impl – transpile_time inject failed: %s", e)
		except Exception as e:
			logging.debug("IONQ_QRC: _run_circuit_async_impl – exception in alg/backend injection: %s", e)

		circ.set_launching()
		circ.set_running()

		try:
			t0 = perf_counter()
			logging.info("IONQ_QRC: submitting async job backend=%s shots=%s", backend_name, shots)
			job = backend.run(isa_qc, shots=shots)
			t1 = perf_counter()
			submit_time = float(t1 - t0)
			job_id = self._job_id_safe(job)
			logging.info("IONQ_QRC: submitted async job backend=%s job_id=%s", backend_name, job_id)

			try:
				if getattr(collector, "collecting", False):
					collector.execution_layer.metrics["submit_time"] = submit_time
			except Exception as e:
				logging.debug("IONQ_QRC: _run_circuit_async_impl – exception in submit_time injection: %s", e)

			return {
				"circ": circ,
				"job": job,
				"job_id": job_id,
				"backend_name": backend_name,
			}

		except Exception as e:
			circ.set_fail()
			raise DEFwError(f"IONQ_QRC: submit failed on backend={backend_name}: {e}")

	def run_circuit_async(self, circ):
		"""
		Async execution entry point wrapped with qbacmet.collector.
		Metrics collected here (submit, basic alg/backend) are later
		attached when _finalize_task() is called in check_active_tasks().
		"""
		logging.debug("IONQ_QRC: run_circuit_async – entering collector.collect()")
		with collector.collect():
			info = self._run_circuit_async_impl(circ)
			logging.debug(
				"IONQ_QRC: run_circuit_async – inside collect, partial collector.metrics keys: %s",
				list(collector.metrics.keys()) if isinstance(collector.metrics, dict) else type(collector.metrics),
			)

		logging.debug(
			"IONQ_QRC: run_circuit_async – after collect, final collector.metrics keys: %s",
			list(collector.metrics.keys()) if isinstance(collector.metrics, dict) else type(collector.metrics),
		)
		info = self._attach_metrics(info)
		logging.debug("IONQ_QRC: run_circuit_async – info type=%s", type(info))
		return info

	def check_active_tasks(self, wid):
		complete = []

		for task_info in self.worker_pool[wid]["active_tasks"]:
			circ = task_info["circ"]
			job = task_info["job"]
			job_id = task_info.get("job_id", None)
			backend_name = task_info.get("backend_name", "<unknown>")
			qpm_opts = circ.info.get("qpm_options", {}) or {}
			poll_s = float(qpm_opts.get("poll_s", os.getenv("IONQ_DEFAULT_POLL_S", "2")))
			timeout_s = float(qpm_opts.get("timeout_s", os.getenv("IONQ_DEFAULT_TIMEOUT_S", "3600")))
			now = time.time()

			if "_first_poll_ts" not in task_info:
				task_info["_first_poll_ts"] = now
			if now < task_info.get("_next_poll_ts", 0.0):
				continue

			elapsed = now - float(task_info["_first_poll_ts"])
			if elapsed >= timeout_s:
				msg = (
					f"IONQ_QRC: async timeout backend={backend_name} "
					f"cid={circ.get_cid()} elapsed={elapsed:.1f}s"
				)
				logging.error(msg)
				circ.set_fail()
				self._finalize_task(circ, {"timeout_s": timeout_s, "elapsed_s": elapsed}, rc=-1, err_msg=msg)
				complete.append(task_info)
				continue

			try:
				st = job.status()
				st_name = getattr(st, "name", str(st)).upper()
				if job_id is None:
					job_id = self._job_id_safe(job)
				if st_name != task_info.get("_last_status"):
					logging.info("IONQ_QRC: poll cid=%s job_id=%s status=%s", circ.get_cid(), job_id, st_name)
					task_info["_last_status"] = st_name

				if st_name in ("DONE", "COMPLETED"):
					# wait timing
					t0 = perf_counter()
					result_obj = job.result()
					t1 = perf_counter()
					wait_time = float(t1 - t0)

					# parse timing
					t0 = perf_counter()
					counts = self.parse_result(result_obj.get_counts())
					t1 = perf_counter()
					parse_time = float(t1 - t0)

					logging.debug(
						"IONQ_QRC: check_active_tasks – wait_time=%.6fs parse_time=%.6fs",
						wait_time,
						parse_time,
					)

					circ.set_exec_done()
					self._finalize_task(circ, counts, rc=0)
					complete.append(task_info)
					continue

				if st_name in ("ERROR", "FAILED", "CANCELLED", "CANCELED"):
					detail = ""
					try:
						# Pull provider-side failure cause when available (e.g., quota exhausted).
						job.result()
					except Exception as e:
						detail = f" detail={e}"
					msg = f"IONQ_QRC: job ended with status={st_name} backend={backend_name}{detail}"
					circ.set_fail()
					self._finalize_task(circ, {"status": st_name}, rc=-1, err_msg=msg)
					complete.append(task_info)
					continue

				task_info["_next_poll_ts"] = now + poll_s

			except Exception as e:
				msg = f"IONQ_QRC: poll exception backend={backend_name}: {e}"
				logging.exception(msg)
				circ.set_fail()
				self._finalize_task(circ, {"exception": str(e)}, rc=-1, err_msg=msg)
				complete.append(task_info)

		for t in complete:
			self.worker_pool[wid]["active_tasks"].remove(t)
