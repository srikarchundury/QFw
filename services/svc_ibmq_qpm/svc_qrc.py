import os
import time
import logging
from time import perf_counter

from util.qpm.util_qrc import UTIL_QRC
from qiskit.circuit import QuantumCircuit
from qiskit.quantum_info import SparsePauliOp
from qiskit.transpiler import preset_passmanagers

from defw_exception import DEFwError
from api_events import Event
from qbacmet import collector


class QRC(UTIL_QRC):
	def __init__(self, start=True):
		logging.debug("IBMQ_QRC(Runtime): init starting")
		super().__init__(start=start)
		logging.debug("IBMQ_QRC(Runtime): init done")

	def form_cmd(self, circ, qasm_file):
		logging.debug(f"IBMQ_QRC(Runtime): form_cmd called cid={circ.get_cid()} qasm_file={qasm_file}")
		raise DEFwError("IBMQ_QRC(Runtime) does not use local command execution!")

	# -------------------------
	# Metrics helper
	# -------------------------
	def _attach_metrics(self, r):
		"""
		Attach qbacmet collector metrics into the result dict (best-effort).

		Critical: we attach to BOTH the outer dict and the inner "result"
		dict, but ONLY if "result" is a wrapper dict (has a "counts" key
		already) rather than the raw bitstring -> count mapping returned
		directly. Injecting "qbacmet_metrics" into that flat dict would
		corrupt the counts themselves (a bitstring key whose "count" is a
		metrics dict), breaking result parsing downstream.
		"""
		try:
			m = collector.metrics
			if not isinstance(m, dict) or not m:
				logging.debug("IBMQ_QRC(Runtime): _attach_metrics – collector.metrics empty or not dict, skipping attach")
				return r

			logging.debug(
				"IBMQ_QRC(Runtime): _attach_metrics – attaching qbacmet metrics with top-level keys: %s",
				list(m.keys()),
			)

			if isinstance(r, dict):
				# top-level
				r["qbacmet_metrics"] = m

				# also inside the "result" payload, but only if it's a
				# wrapper dict, not the flat counts dict itself
				res = r.get("result", None)
				if isinstance(res, dict) and ("counts" in res or "statevector" in res):
					res["qbacmet_metrics"] = m
		except Exception as e:
			# Never let metrics attachment break normal flow
			logging.debug(f"IBMQ_QRC(Runtime): _attach_metrics – exception while attaching metrics: {e}")
		return r

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
		logging.debug("IBMQ_QRC(Runtime): _ok_ret – before metrics attach")
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
		logging.debug("IBMQ_QRC(Runtime): _error_ret – before metrics attach, msg=%s", msg)
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
		logging.debug("IBMQ_QRC(Runtime): _finalize_task – before metrics attach, rc=%s", rc)
		r = self._attach_metrics(r)

		if self.push_info:
			try:
				event = Event(self.push_info["evtype"], r)
				self.push_info["class"].put(event)
			except Exception as e:
				logging.critical(f"IBMQ_QRC(Runtime): failed to push event cid={circ.get_cid()}: {e}")
				with self.circuit_results_lock:
					self.circuit_results.append(r)
		else:
			with self.circuit_results_lock:
				self.circuit_results.append(r)

		return r

	# -------------------------
	# Runtime helpers
	# -------------------------
	def _get_service(self, circ):
		service = circ.info.get("qfw_backend", None)
		if service is None:
			raise DEFwError("IBMQ_QRC(Runtime): Runtime service not set in circ.info['qfw_backend']")
		return service

	def _get_backend(self, service, circ):
		qpm_opts = circ.info.get("qpm_options", {}) or {}
		backend_name = qpm_opts.get("backend", None)
		allow_sim = bool(qpm_opts.get("simulator", False))

		try:
			if backend_name:
				b = service.backend(backend_name)
				logging.debug("IBMQ_QRC(Runtime): _get_backend – using requested backend=%s", backend_name)
				return b, backend_name
			backend = service.least_busy(operational=True, simulator=allow_sim)
			name = getattr(backend, "name", "<unknown>")
			logging.debug("IBMQ_QRC(Runtime): _get_backend – using least_busy backend=%s", name)
			return backend, name
		except Exception as e:
			raise DEFwError(f"IBMQ_QRC(Runtime): failed to get backend: {e}")

	def _build_qc(self, circ):
		if "qasm" not in circ.info:
			raise DEFwError("IBMQ_QRC(Runtime): circ.info['qasm'] missing")
		try:
			qc = QuantumCircuit.from_qasm_str(circ.info["qasm"])
			logging.debug(
				"IBMQ_QRC(Runtime): _build_qc – built QC, num_qubits=%s num_clbits=%s",
				qc.num_qubits,
				qc.num_clbits,
			)
			return qc
		except Exception as e:
			raise DEFwError(f"IBMQ_QRC(Runtime): invalid QASM: {e}")

	def _compile(self, qc, backend, circ):
		"""
		Compile using preset pass manager.

		IMPORTANT: we call via the *module* `generate_preset_pass_manager`,
		so qbacmet's monkey-patch on that function actually takes effect.
		"""
		qpm_opts = circ.info.get("qpm_options", {}) or {}
		opt_level = int(qpm_opts.get("optimization_level", 1))
		logging.debug(
			"IBMQ_QRC(Runtime): _compile – calling generate_preset_pass_manager with opt_level=%s, target=%s",
			opt_level,
			type(backend.target),
		)
		pm = preset_passmanagers.generate_preset_pass_manager(target=backend.target, optimization_level=opt_level)
		return pm.run(qc)

	def _primitive(self, circ):
		p = (circ.info.get("primitive", "sampler") or "sampler").strip().lower()
		logging.debug("IBMQ_QRC(Runtime): _primitive – primitive=%s", p)
		return p

	def _apply_primitive_options(self, prim, circ):
		qpm_opts = circ.info.get("qpm_options", {}) or {}
		opts = qpm_opts.get("primitive_options", None)
		if not isinstance(opts, dict):
			return
		for k, v in opts.items():
			try:
				if hasattr(prim.options, k):
					setattr(prim.options, k, v)
					logging.debug("IBMQ_QRC(Runtime): _apply_primitive_options – set %s=%s", k, v)
			except Exception as e:
				logging.debug("IBMQ_QRC(Runtime): _apply_primitive_options – failed to set %s: %s", k, e)

	def _job_id_safe(self, job):
		try:
			jid = job.job_id()
			logging.debug("IBMQ_QRC(Runtime): _job_id_safe – job_id=%s", jid)
			return jid
		except Exception:
			return None

	def _wait_job(self, job, backend_name, poll_s=5, timeout_s=3600):
		t0 = time.time()
		job_id = self._job_id_safe(job)

		while True:
			try:
				if job.done():
					return
			except Exception:
				pass

			try:
				st = job.status()
				st_name = getattr(st, "name", str(st)).upper()
			except Exception as e:
				st_name = f"UNKNOWN({e})"

			elapsed = int(time.time() - t0)
			logging.debug(
				"IBMQ_QRC(Runtime): wait job_id=%s backend=%s status=%s elapsed_s=%s",
				job_id,
				backend_name,
				st_name,
				elapsed,
			)

			if elapsed >= timeout_s:
				raise DEFwError(f"IBMQ_QRC(Runtime): timeout waiting for job_id={job_id} backend={backend_name} (elapsed {elapsed}s)")

			time.sleep(poll_s)

	def _ibmq_mode(self, circ):
		qpm_opts = circ.info.get("qpm_options", {}) or {}
		mode = (qpm_opts.get("ibmq_mode", None) or os.getenv("IBMQ_MODE", "job") or "job").strip().lower()
		final = "session" if mode == "session" else "job"
		logging.debug("IBMQ_QRC(Runtime): _ibmq_mode – ibmq_mode=%s", final)
		return final

	def _timeouts(self, circ):
		qpm_opts = circ.info.get("qpm_options", {}) or {}
		poll_s = int(qpm_opts.get("poll_s", os.getenv("IBMQ_DEFAULT_POLL_S", "5")))
		timeout_s = int(qpm_opts.get("timeout_s", os.getenv("IBMQ_DEFAULT_TIMEOUT_S", "3600")))
		logging.debug("IBMQ_QRC(Runtime): _timeouts – poll_s=%s timeout_s=%s", poll_s, timeout_s)
		return poll_s, timeout_s

	def _make_sampler_job_mode(self, backend):
		from qiskit_ibm_runtime import SamplerV2 as Sampler
		logging.debug("IBMQ_QRC(Runtime): _make_sampler_job_mode – backend=%s", getattr(backend, "name", None))
		return Sampler(mode=backend)

	def _make_estimator_job_mode(self, backend):
		from qiskit_ibm_runtime import EstimatorV2 as Estimator
		try:
			logging.debug("IBMQ_QRC(Runtime): _make_estimator_job_mode – trying Estimator(backend=...)")
			return Estimator(backend=backend)
		except Exception:
			logging.debug("IBMQ_QRC(Runtime): _make_estimator_job_mode – falling back to Estimator(mode=backend)")
			return Estimator(mode=backend)

	# -------------------------
	# Sync execution
	# -------------------------
	def _run_circuit_impl(self, circ):
		"""
		Internal implementation of run_circuit. This contains the original
		IBMQ execution logic and is wrapped by qbacmet.collector in run_circuit().
		"""
		# ---- transpile-only mode: skip all cloud calls ----
		if os.environ.get("QFW_TRANSPILE_ONLY", "").strip() == "1":
			logging.info("IBMQ_QRC(Runtime): QFW_TRANSPILE_ONLY=1 – returning synthetic counts (no cloud calls)")
			qc = self._build_qc(circ)
			shots = int(circ.info.get("num_shots", 1024))
			# Transpile locally without a real backend target
			t0 = perf_counter()
			from qiskit.transpiler import preset_passmanagers
			try:
				pm = preset_passmanagers.generate_preset_pass_manager(
					optimization_level=int(circ.info.get("qpm_options", {}).get("optimization_level", 1))
				)
				isa_qc = pm.run(qc)
			except Exception:
				isa_qc = qc
			compile_time = float(perf_counter() - t0)
			logging.debug("IBMQ_QRC(Runtime): transpile-only compile_time=%.6fs", compile_time)
			try:
				if getattr(collector, "collecting", False):
					collector.alg_layer.metrics.setdefault("framework", "qiskit")
					collector.alg_layer.metrics["num_qubits"] = int(getattr(qc, "num_qubits", 0))
					collector.alg_layer.metrics["num_clbits"] = int(getattr(qc, "num_clbits", 0))
					collector.alg_layer.metrics["shots"] = shots
					prev_tt = collector.execution_layer.metrics.get("transpile_time", 0.0) or 0.0
					collector.execution_layer.metrics["transpile_time"] = float(prev_tt) + compile_time
			except Exception:
				pass
			nq = getattr(isa_qc, "num_qubits", getattr(qc, "num_qubits", 1))
			parsed = {"0" * nq: shots}
			circ.set_launching()
			circ.set_running()
			circ.set_exec_done()
			return self._ok_ret(circ, parsed)

		try:
			from qiskit_ibm_runtime import Session
			from qiskit_ibm_runtime import SamplerV2 as Sampler
			from qiskit_ibm_runtime import EstimatorV2 as Estimator

			service = self._get_service(circ)
			backend, backend_name = self._get_backend(service, circ)
			qc = self._build_qc(circ)

			# compile timing -> collector’s preset-pass-manager hooks should see this
			t0 = perf_counter()
			isa_qc = self._compile(qc, backend, circ)
			t1 = perf_counter()
			compile_time = float(t1 - t0)
			logging.debug("IBMQ_QRC(Runtime): _run_circuit_impl – compile_time=%.6fs", compile_time)

			# ---- minimal direct metrics injection (fallback) ----
			try:
				if getattr(collector, "collecting", False):
					logging.debug("IBMQ_QRC(Runtime): _run_circuit_impl – collector.collecting True, injecting fallback metrics")
					# record transpile time
					prev_tt = collector.execution_layer.metrics.get("transpile_time", 0.0) or 0.0
					collector.execution_layer.metrics["transpile_time"] = float(prev_tt) + compile_time

					# basic algorithm metrics
					try:
						nq = getattr(qc, "num_qubits", None)
						nc = getattr(qc, "num_clbits", None)
						if nq is not None:
							collector.alg_layer.metrics["num_qubits"] = int(nq)
						if nc is not None:
							collector.alg_layer.metrics["num_clbits"] = int(nc)
						collector.alg_layer.metrics.setdefault("framework", "qiskit")
					except Exception as e:
						logging.debug("IBMQ_QRC(Runtime): _run_circuit_impl – fallback alg metrics failed: %s", e)

					# backend details (in case backend-layer hooks didn't see it)
					try:
						collector.backend_layer.set_backend_details(backend)
					except Exception as e:
						logging.debug("IBMQ_QRC(Runtime): _run_circuit_impl – backend details set failed: %s", e)

					logging.debug(
						"IBMQ_QRC(Runtime): _run_circuit_impl – after fallback, alg_layer.metrics=%s, execution_layer.metrics=%s",
						collector.alg_layer.metrics,
						collector.execution_layer.metrics,
					)
				else:
					logging.debug("IBMQ_QRC(Runtime): _run_circuit_impl – collector.collecting False, no fallback injection")
			except Exception as e:
				logging.debug("IBMQ_QRC(Runtime): _run_circuit_impl – exception in fallback metrics injection: %s", e)
			# -----------------------------------------------------

			primitive = self._primitive(circ)
			shots = int(circ.info.get("num_shots", 1024))
			mode = self._ibmq_mode(circ)
			poll_s, timeout_s = self._timeouts(circ)

		except Exception as e:
			circ.set_fail()
			return self._error_ret(circ, str(e))

		circ.set_launching()
		circ.set_running()

		try:
			job = None
			job_id = None
			out = None

			# -------- submit timing
			submit_t0 = perf_counter()

			if mode == "session":
				from qiskit_ibm_runtime import Session
				from qiskit_ibm_runtime import SamplerV2 as Sampler
				from qiskit_ibm_runtime import EstimatorV2 as Estimator

				with Session(backend=backend) as session:
					if primitive == "sampler":
						sampler = Sampler(mode=session)
						self._apply_primitive_options(sampler, circ)
						try:
							sampler.options.default_shots = shots
							job = sampler.run([isa_qc])

						except Exception:
							job = sampler.run([isa_qc], shots=shots)

						job_id = self._job_id_safe(job)
						self._wait_job(job, backend_name, poll_s=poll_s, timeout_s=timeout_s)

						# wait timing
						wait_t0 = perf_counter()
						pub0 = job.result()[0]
						wait_t1 = perf_counter()

						# parse timing
						parse_t0 = perf_counter()
						counts = pub0.join_data().get_counts()
						parse_t1 = perf_counter()

						out = {
							"backend": backend_name,
							"primitive": "sampler",
							"mode": "session",
							"job_id": job_id,
							"counts": counts,
						}

					elif primitive == "estimator":
						if "observables" not in circ.info or "theta" not in circ.info:
							raise DEFwError("IBMQ_QRC(Runtime): estimator needs circ.info['observables'] and ['theta']")

						H = SparsePauliOp.from_list(circ.info["observables"])
						theta = circ.info["theta"]
						try:
							isa_H = H.apply_layout(isa_qc.layout)
						except Exception:
							isa_H = H

						est = Estimator(mode=session)
						self._apply_primitive_options(est, circ)

						job = est.run([(isa_qc, isa_H, theta)])
						job_id = self._job_id_safe(job)
						self._wait_job(job, backend_name, poll_s=poll_s, timeout_s=timeout_s)

						wait_t0 = perf_counter()
						pub0 = job.result()[0]
						wait_t1 = perf_counter()

						parse_t0 = perf_counter()
						out = {
							"backend": backend_name,
							"primitive": "estimator",
							"mode": "session",
							"job_id": job_id,
							"evs": list(pub0.data.evs),
						}
						parse_t1 = perf_counter()

					else:
						raise DEFwError(f"IBMQ_QRC(Runtime): unknown primitive '{primitive}'")

			else:
				# job mode
				if primitive == "sampler":
					sampler = self._make_sampler_job_mode(backend)
					self._apply_primitive_options(sampler, circ)
					try:
						sampler.options.default_shots = shots
						job = sampler.run([isa_qc])
					except Exception:
						job = sampler.run([isa_qc], shots=shots)

					job_id = self._job_id_safe(job)
					self._wait_job(job, backend_name, poll_s=poll_s, timeout_s=timeout_s)

					wait_t0 = perf_counter()
					pub0 = job.result()[0]
					wait_t1 = perf_counter()

					parse_t0 = perf_counter()
					counts = pub0.join_data().get_counts()
					parse_t1 = perf_counter()

					out = {
						"backend": backend_name,
						"primitive": "sampler",
						"mode": "job",
						"job_id": job_id,
						"counts": counts,
					}

				elif primitive == "estimator":
					if "observables" not in circ.info or "theta" not in circ.info:
						raise DEFwError("IBMQ_QRC(Runtime): estimator needs circ.info['observables'] and ['theta']")

					H = SparsePauliOp.from_list(circ.info["observables"])
					theta = circ.info["theta"]
					try:
						isa_H = H.apply_layout(isa_qc.layout)
					except Exception:
						isa_H = H

					est = self._make_estimator_job_mode(backend)
					self._apply_primitive_options(est, circ)

					job = est.run([(isa_qc, isa_H, theta)])
					job_id = self._job_id_safe(job)
					self._wait_job(job, backend_name, poll_s=poll_s, timeout_s=timeout_s)

					wait_t0 = perf_counter()
					pub0 = job.result()[0]
					wait_t1 = perf_counter()

					parse_t0 = perf_counter()
					out = {
						"backend": backend_name,
						"primitive": "estimator",
						"mode": "job",
						"job_id": job_id,
						"evs": list(pub0.data.evs),
					}
					parse_t1 = perf_counter()

				else:
					raise DEFwError(f"IBMQ_QRC(Runtime): unknown primitive '{primitive}'")

			submit_t1 = perf_counter()
			submit_time = float(submit_t1 - submit_t0)
			wait_time = float(wait_t1 - wait_t0) if "wait_t0" in locals() else 0.0
			parse_time = float(parse_t1 - parse_t0) if "parse_t0" in locals() else 0.0

			logging.debug(
				"IBMQ_QRC(Runtime): _run_circuit_impl – submit_time=%.6fs wait_time=%.6fs parse_time=%.6fs",
				submit_time,
				wait_time,
				parse_time,
			)

			circ.set_exec_done()
			return self._ok_ret(circ, out)

		except Exception as e:
			msg = f"IBMQ_QRC(Runtime): run_circuit exception backend={backend_name}: {e}"
			logging.exception(msg)
			circ.set_fail()
			return self._error_ret(circ, msg)

	def run_circuit(self, circ):
		"""
		Sync execution entry point wrapped with qbacmet.collector.
		"""
		logging.debug("IBMQ_QRC(Runtime): run_circuit – entering collector.collect()")
		with collector.collect():
			result = self._run_circuit_impl(circ)
			logging.debug(
				"IBMQ_QRC(Runtime): run_circuit – inside collect, partial collector.metrics keys: %s",
				list(collector.metrics.keys()) if isinstance(collector.metrics, dict) else type(collector.metrics),
			)

		# At this point collector.auto_collect_all() has already run,
		# so collector.metrics is fully populated. Re-attach to ensure
		# we capture final metrics, even if _ok_ret ran earlier.
		logging.debug(
			"IBMQ_QRC(Runtime): run_circuit – after collect, final collector.metrics keys: %s",
			list(collector.metrics.keys()) if isinstance(collector.metrics, dict) else type(collector.metrics),
		)
		result = self._attach_metrics(result)
		logging.debug("IBMQ_QRC(Runtime): run_circuit – result type=%s", type(result))
		return result

	# -------------------------
	# Async execution
	# -------------------------
	def _run_circuit_async_impl(self, circ):
		from qiskit_ibm_runtime import Session
		from qiskit_ibm_runtime import SamplerV2 as Sampler
		from qiskit_ibm_runtime import EstimatorV2 as Estimator

		service = self._get_service(circ)
		backend, backend_name = self._get_backend(service, circ)
		qc = self._build_qc(circ)

		# compile + basic metrics in async path
		t0 = perf_counter()
		isa_qc = self._compile(qc, backend, circ)
		t1 = perf_counter()
		compile_time = float(t1 - t0)
		logging.debug("IBMQ_QRC(Runtime): _run_circuit_async_impl – compile_time=%.6fs", compile_time)

		try:
			if getattr(collector, "collecting", False):
				logging.debug("IBMQ_QRC(Runtime): _run_circuit_async_impl – collector.collecting True, injecting fallback metrics")
				nq = getattr(qc, "num_qubits", None)
				nc = getattr(qc, "num_clbits", None)
				if nq is not None:
					collector.alg_layer.metrics["num_qubits"] = int(nq)
				if nc is not None:
					collector.alg_layer.metrics["num_clbits"] = int(nc)
				collector.alg_layer.metrics.setdefault("framework", "qiskit")

				prev_tt = collector.execution_layer.metrics.get("transpile_time", 0.0) or 0.0
				collector.execution_layer.metrics["transpile_time"] = float(prev_tt) + compile_time

				try:
					collector.backend_layer.set_backend_details(backend)
				except Exception as e:
					logging.debug("IBMQ_QRC(Runtime): _run_circuit_async_impl – backend details set failed: %s", e)

				logging.debug(
					"IBMQ_QRC(Runtime): _run_circuit_async_impl – after fallback, alg_layer.metrics=%s, execution_layer.metrics=%s",
					collector.alg_layer.metrics,
					collector.execution_layer.metrics,
				)
			else:
				logging.debug("IBMQ_QRC(Runtime): _run_circuit_async_impl – collector.collecting False, no fallback injection")
		except Exception as e:
			logging.debug("IBMQ_QRC(Runtime): _run_circuit_async_impl – exception in fallback metrics injection: %s", e)

		primitive = self._primitive(circ)
		shots = int(circ.info.get("num_shots", 1024))
		mode = self._ibmq_mode(circ)

		circ.set_launching()
		circ.set_running()

		try:
			session = None
			submit_t0 = perf_counter()

			if mode == "session":
				session = Session(backend=backend)

				if primitive == "sampler":
					sampler = Sampler(mode=session)
					self._apply_primitive_options(sampler, circ)
					try:
						sampler.options.default_shots = shots
						job = sampler.run([isa_qc])
					except Exception:
						job = sampler.run([isa_qc], shots=shots)

				elif primitive == "estimator":
					if "observables" not in circ.info or "theta" not in circ.info:
						session.close()
						raise DEFwError("IBMQ_QRC(Runtime): estimator needs circ.info['observables'] and ['theta']")
					H = SparsePauliOp.from_list(circ.info["observables"])
					theta = circ.info["theta"]
					try:
						isa_H = H.apply_layout(isa_qc.layout)
					except Exception:
						isa_H = H
					est = Estimator(mode=session)
					self._apply_primitive_options(est, circ)
					job = est.run([(isa_qc, isa_H, theta)])

				else:
					session.close()
					raise DEFwError(f"IBMQ_QRC(Runtime): unknown primitive '{primitive}'")

			else:
				# job mode
				if primitive == "sampler":
					sampler = self._make_sampler_job_mode(backend)
					self._apply_primitive_options(sampler, circ)
					try:
						sampler.options.default_shots = shots
						job = sampler.run([isa_qc])
					except Exception:
						job = sampler.run([isa_qc], shots=shots)

				elif primitive == "estimator":
					if "observables" not in circ.info or "theta" not in circ.info:
						raise DEFwError("IBMQ_QRC(Runtime): estimator needs circ.info['observables'] and ['theta']")
					H = SparsePauliOp.from_list(circ.info["observables"])
					theta = circ.info["theta"]
					try:
						isa_H = H.apply_layout(isa_qc.layout)
					except Exception:
						isa_H = H
					est = self._make_estimator_job_mode(backend)
					self._apply_primitive_options(est, circ)
					job = est.run([(isa_qc, isa_H, theta)])

				else:
					raise DEFwError(f"IBMQ_QRC(Runtime): unknown primitive '{primitive}'")

			submit_t1 = perf_counter()
			submit_time = float(submit_t1 - submit_t0)
			logging.debug("IBMQ_QRC(Runtime): _run_circuit_async_impl – submit_time=%.6fs", submit_time)

			job_id = self._job_id_safe(job)

			info = {
				"circ": circ,
				"job": job,
				"job_id": job_id,
				"backend_name": backend_name,
				"primitive": primitive,
				"mode": mode,
				"session": session,
			}
			return info

		except Exception as e:
			circ.set_fail()
			raise DEFwError(f"IBMQ_QRC(Runtime): submit failed backend={backend_name}: {e}")

	def run_circuit_async(self, circ):
		"""
		Async execution entry point wrapped with qbacmet.collector.
		Metrics collected here (compile/submit) are later attached when
		_finalize_task() is called in check_active_tasks().
		"""
		logging.debug("IBMQ_QRC(Runtime): run_circuit_async – entering collector.collect()")
		with collector.collect():
			info = self._run_circuit_async_impl(circ)
			logging.debug(
				"IBMQ_QRC(Runtime): run_circuit_async – inside collect, partial collector.metrics keys: %s",
				list(collector.metrics.keys()) if isinstance(collector.metrics, dict) else type(collector.metrics),
			)

		# Collector has finished; metrics are ready. Attach them.
		logging.debug(
			"IBMQ_QRC(Runtime): run_circuit_async – after collect, final collector.metrics keys: %s",
			list(collector.metrics.keys()) if isinstance(collector.metrics, dict) else type(collector.metrics),
		)
		info = self._attach_metrics(info)
		logging.debug("IBMQ_QRC(Runtime): run_circuit_async – info type=%s", type(info))
		return info

	def check_active_tasks(self, wid):
		complete = []

		for task_info in self.worker_pool[wid]["active_tasks"]:
			circ = task_info["circ"]
			job = task_info["job"]
			session = task_info.get("session", None)
			backend_name = task_info.get("backend_name", "<unknown>")
			primitive = task_info.get("primitive", "sampler")
			job_id = task_info.get("job_id", None)
			poll_s, timeout_s = self._timeouts(circ)
			now = time.time()

			if "_first_poll_ts" not in task_info:
				task_info["_first_poll_ts"] = now
			if now < task_info.get("_next_poll_ts", 0.0):
				continue

			elapsed = now - float(task_info["_first_poll_ts"])
			if elapsed >= timeout_s:
				msg = (
					f"IBMQ_QRC(Runtime): async timeout backend={backend_name} "
					f"job_id={job_id} cid={circ.get_cid()} elapsed={elapsed:.1f}s"
				)
				logging.error(msg)
				circ.set_fail()
				self._finalize_task(circ, {"timeout_s": timeout_s, "elapsed_s": elapsed, "job_id": job_id}, rc=-1, err_msg=msg)
				complete.append(task_info)
				continue

			try:
				logging.debug(
					"IBMQ_QRC(Runtime): check_active_tasks – wid=%s, cid=%s, backend=%s, primitive=%s, job_id=%s, job_is_none=%s",
					wid,
					circ.get_cid(),
					backend_name,
					primitive,
					job_id,
					job is None,
				)

				if job is None:
					raise DEFwError(f"IBMQ_QRC(Runtime): missing async job handle for backend={backend_name} cid={circ.get_cid()}")

				# quick poll:
				try:
					if hasattr(job, "done") and not job.done():
						continue
				except Exception:
					st = job.status()
					st_name = getattr(st, "name", str(st)).upper()
					if st_name not in ("DONE", "COMPLETED"):
						if st_name in ("ERROR", "FAILED", "CANCELLED", "CANCELED"):
							msg = f"IBMQ_QRC(Runtime): job ended with status={st_name} backend={backend_name} job_id={job_id}"
							logging.debug("IBMQ_QRC(Runtime): check_active_tasks – job error state: %s", msg)
							circ.set_fail()
							self._finalize_task(circ, {"status": st_name, "job_id": job_id}, rc=-1, err_msg=msg)
							complete.append(task_info)
						task_info["_next_poll_ts"] = now + poll_s
						continue

				task_info["_next_poll_ts"] = now + poll_s

				# wait timing (result fetch)
				wait_t0 = perf_counter()
				pub0 = job.result()[0]
				wait_t1 = perf_counter()
				wait_time = float(wait_t1 - wait_t0)

				# parse timing
				parse_t0 = perf_counter()
				if primitive == "sampler":
					counts = pub0.join_data().get_counts()
					out = {"backend": backend_name, "primitive": "sampler", "job_id": job_id, "counts": counts}
				else:
					evs = list(pub0.data.evs)
					out = {"backend": backend_name, "primitive": "estimator", "job_id": job_id, "evs": evs}
				parse_t1 = perf_counter()
				parse_time = float(parse_t1 - parse_t0)

				logging.debug(
					"IBMQ_QRC(Runtime): check_active_tasks – wait_time=%.6fs parse_time=%.6fs",
					wait_time,
					parse_time,
				)

				circ.set_exec_done()
				self._finalize_task(circ, out, rc=0)
				complete.append(task_info)

			except Exception as e:
				msg = f"IBMQ_QRC(Runtime): poll exception backend={backend_name} job_id={job_id}: {e}"
				logging.exception(msg)
				circ.set_fail()
				self._finalize_task(circ, {"exception": str(e), "job_id": job_id}, rc=-1, err_msg=msg)
				complete.append(task_info)

			if task_info in complete and session is not None:
				try:
					session.close()
				except Exception:
					pass

		for t in complete:
			self.worker_pool[wid]["active_tasks"].remove(t)
