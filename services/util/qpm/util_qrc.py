from api_events import Event
from defw_agent_info import *  # noqa: F401,F403
from defw_util import print_thread_stack_trace_to_logger
import logging
import time
import queue
import threading
import sys
import os
import psutil
from defw_exception import DEFwExecutionError, DEFwInProgress, DEFwOutOfResources
import svc_launcher
import cdefw_global

from time import perf_counter
from collections import OrderedDict

from qbacmet import collector, QBacMetCollector  # noqa: F401  (collector: used by subclasses)

# QBacMet collections kept for circuits still running; bounded so abandoned circuits cannot grow it forever
QBACMET_MAX_PENDING = 10000

sys.path.append(os.path.split(os.path.abspath(__file__))[0])


class UTIL_QRC:
	# max work queue size
	THREAD_STATE_FREE = 0
	THREAD_STATE_BUSY = 1

	def __init__(self, num_workers=8, num_worker_tasks=256, start=True):
		print_thread_stack_trace_to_logger(level='debug')
		self.shutdown_workers = False
		self.circuit_results_lock = threading.Lock()
		self.worker_pool_lock = threading.Lock()
		self.circuit_results = []
		self._qbacmet = OrderedDict()          # cid -> QBacMetCollector of that circuit
		self._qbacmet_lock = threading.Lock()
		self.push_info = {}
		self.module_util = None
		self.worker_pool = []
		self.worker_pool_rr = 0
		self.num_workers = num_workers
		self.num_worker_tasks = num_worker_tasks
		self.num_cores = psutil.cpu_count(logical=False)
		logging.debug(f'num_cores = {self.num_cores} start = {start}')
		if start:
			self.launcher = svc_launcher.Launcher()
			for x in range(0, self.num_workers):
				with self.worker_pool_lock:
					runner = threading.Thread(target=self.runner, args=(x,))
					logging.debug(f"inserting {x} in the worker pool")
					self.worker_pool.append({
						'thread': runner,
						'active_tasks': [],
						'queue': queue.Queue(),
						'state': UTIL_QRC.THREAD_STATE_FREE
					})
					runner.daemon = True
					runner.start()

	def __del__(self):
		print_thread_stack_trace_to_logger(level='debug')
		self.shutdown_workers = True
		with self.worker_pool_lock:
			for v in self.worker_pool:
				v['queue'].put(None)

	# -------------------------
	# QBacMet helper
	# -------------------------
	def _remember_collection(self, circ, qm):
		"""Keep this circuit's QBacMet collection until its result is built (in whichever thread)."""
		with self._qbacmet_lock:
			self._qbacmet[circ.get_cid()] = qm
			while len(self._qbacmet) > QBACMET_MAX_PENDING:
				self._qbacmet.popitem(last=False)

	def _forget_collection(self, circ):
		with self._qbacmet_lock:
			self._qbacmet.pop(circ.get_cid(), None)

	def _collection(self, cid):
		with self._qbacmet_lock:
			return self._qbacmet.get(cid)

	def _qbacmet_backend_features(self, circ):
		"""Layer-3 facts the QRC knows for a locally launched circuit."""
		info = circ.info
		opts = info.get("qpm_options", {}) or {}
		qb = info.get("qfw_backend")
		hosts = info.get("hosts") or {}
		feats = {
			"qpm": type(self).__module__,
			"qfw_backend": qb if isinstance(qb, str) else type(qb).__name__,
			"backend_label": qb.rsplit(".", 1)[-1] if isinstance(qb, str) else None,
			"sub_backend": opts.get("backend") or info.get("backend"),
			"method": info.get("method"),
			"device": opts.get("device"),
			"np": info.get("np"),
			"np_policy": info.get("np_policy"),
			"num_hosts": len(hosts) if isinstance(hosts, dict) else None,
			"hosts": hosts,
			"qpm_options": opts,
		}
		if info.get("mqbac"):
			feats["mqbac"] = info["mqbac"]
		return feats

	def _qpm_load(self):
		"""This QRC's load when a circuit is launched (read fresh each time, in-process, no cost)."""
		try:
			active = sum(len(w['active_tasks']) for w in self.worker_pool)
			queued = sum(w['queue'].qsize() for w in self.worker_pool)
		except Exception:
			active = queued = None
		with self._qbacmet_lock:
			pending = len(self._qbacmet)
		return {"rt_qpm_active_circuits": active, "rt_qpm_queued_circuits": queued,
		        "rt_qpm_circuits_in_flight": pending, "rt_qpm_load_at": time.time()}

	def _start_collection(self, circ):
		"""Local backends run a separate executable, so library hooks see nothing: record what the QRC knows
		(circuit from its QASM, backend, processes) explicitly. Timing is added when the result is built."""
		qm = QBacMetCollector()
		try:
			with qm.collect(hooks=False):
				qm.set_algorithm_from_qasm(circ.info.get("qasm", "") or "", shots=circ.info.get("num_shots"))
				qm.set_backend_features(**self._qbacmet_backend_features(circ))
				qm.execution_layer.metrics["mpi_size"] = circ.info.get("np")
				qm.execution_layer.metrics.update(self._qpm_load())
		except Exception as e:
			logging.debug("UTIL_QRC: QBacMet collection setup failed: %s", e)
		self._remember_collection(circ, qm)
		return qm

	def _attach_metrics(self, r):
		"""
		Attach this circuit's QBacMet metrics to a result dict.

		The metrics come from the circuit's own collection (looked up by cid), never from whatever collection
		ran last in this thread: results are built in worker threads, possibly while other circuits run. A
		final result (has "rc") also gets the circuit's timeline (wait for resources, launch, execution) and
		releases the collection.

		Attached to the outer dict, and to the inner "result" only when that is a wrapper dict (has
		"counts"/"statevector"); several backends return the bare counts dict as r["result"], and a metrics key
		inside it would corrupt the counts. QFw's job object forwards the outer copy (qfw_job.py).
		"""
		try:
			if not isinstance(r, dict):
				return r
			circ = r.get("circ")
			cid = r.get("cid") or (circ.get_cid() if circ is not None else None)
			final = "rc" in r
			with self._qbacmet_lock:
				qm = (self._qbacmet.pop(cid, None) if final else self._qbacmet.get(cid)) if cid else None
			if qm is None:
				return r
			if final:
				qm.set_timeline(created=r.get("creation_time"), resources_ready=r.get("resources_consumed_time"),
				                launched=r.get("launch_time"), started=r.get("exec_time"),
				                completed=r.get("completion_time"))
				qm.execution_layer.metrics["return_code"] = r.get("rc")
			m = qm.finalize()
			r["qbacmet_metrics"] = m
			res = r.get("result", None)
			if isinstance(res, dict) and ("counts" in res or "statevector" in res):
				res["qbacmet_metrics"] = m
		except Exception as e:
			# Never let metrics attachment break normal flow
			logging.debug(f"UTIL_QRC: _attach_metrics – exception while attaching metrics: {e}")
		return r

	@staticmethod
	def _note_result_dequeued(r):
		"""Time the result waited in the completion queue, once the client takes it."""
		try:
			m = r.get("qbacmet_metrics")
			if isinstance(m, dict) and r.get("cq_dequeue_time", -1) > 0:
				ex = m.setdefault("execution", {}).setdefault("metrics", {})
				ex["time_in_result_queue"] = r["cq_dequeue_time"] - r["cq_enqueue_time"]
		except Exception:
			pass

	def parse_task_result(self, stdout, circ, task_info):
		return self.parse_result(stdout)

	def cleanup_task(self, circ, task_info):
		pass

	def check_active_tasks(self, wid):
		complete = []
		for task_info in self.worker_pool[wid]['active_tasks']:
			try:
				#stdout, stderr, rc = task_info['launcher'].status(task_info['pid'])
				stdout, stderr, rc = self.launcher.status(task_info['pid'])
			except DEFwInProgress:
				continue
			except Exception as e:
				raise e

			# at this point, it already has a return code!
			complete.append(task_info)

			circ = task_info['circ']
			cid = circ.get_cid()
			qasm_file = task_info['qasm_file']

			try:
				if rc == 0:
					try:
						t_parse0 = perf_counter()
						output = self.parse_task_result(stdout, circ, task_info)
						t_parse1 = perf_counter()
						parse_time = t_parse1 - t_parse0

						qm = self._collection(cid)
						if qm is not None:
							qm.postprocess_layer.metrics["result_marshalling_time"] = parse_time

						circ.set_exec_done()
					except Exception as e:
						# A real dict with an "error" key, NOT a string that merely
						# looks like one — callers (qfw_job.py) key off "error" to
						# raise a clear failure instead of silently treating this
						# as counts data (which corrupts downstream Counts()).
						logging.critical(f"parse result failure = {e}")
						output = {"error": f"parse result failure: {e}"}
						circ.set_fail()
				else:
					stdout_s = stdout.decode('utf-8', errors='replace')
					stderr_s = stderr.decode('utf-8', errors='replace')
					logging.critical(f"circuit execution failed rc={rc}: stdout={stdout_s} stderr={stderr_s}")
					output = {"error": f"circuit execution failed (rc={rc}): {stdout_s}\n{stderr_s}"}
					circ.set_fail()
			finally:
				self.cleanup_task(circ, task_info)

			try:
				os.remove(qasm_file)
			except OSError:
				pass

			r = {
				'cid': cid,
				'result': output,
				'rc': rc,
				'launch_time': circ.launch_time,
				'creation_time': circ.creation_time,
				'exec_time': circ.exec_time,
				'completion_time': circ.completion_time,
				'resources_consumed_time': circ.resources_consumed_time,
				'cq_enqueue_time': time.time(),
				'cq_dequeue_time': -1
			}
			r = self._attach_metrics(r)

			circ.free_resources(circ)

			# push the result if push info were registered:
			if self.push_info:
				event = Event(self.push_info['evtype'], r)
				try:
					self.push_info['class'].put(event)
				except Exception as e:
					logging.critical(f"Failed to push event to client. Exception encountered {e}")
					raise e
			else:
				with self.circuit_results_lock:
					self.circuit_results.append(r)

		for task_info in complete:
			self.worker_pool[wid]['active_tasks'].remove(task_info)

	def runner(self, my_id):
		# get the next available entry on the queue
		# if one is available run it
		# check on currently running tasks to see if any of them complete
		# Add completed tasks to the results dictionary
		super_affinity = os.sched_getaffinity(0)
		with self.worker_pool_lock:
			my_queue = self.worker_pool[my_id]['queue']
			# bind to core
			# We can enhance this more to avoid core 0 in every l3 cache
			bound_core = (my_id + 1) % self.num_cores
			if bound_core not in super_affinity:
				tmp = bound_core + 1
				while tmp != bound_core:
					if tmp in super_affinity:
						bound_core = tmp
						break
					tmp = (tmp + 1) % len(super_affinity)
			try:
				logging.debug(
					f'binding {threading.get_ident()} to '
					f'{bound_core} from  {os.sched_getaffinity(0)}')
				os.sched_setaffinity(0, {bound_core})
			except Exception as e:
				logging.critical(f'Failed to bind {threading.get_ident()} to {bound_core}')
				raise e

		while not self.shutdown_workers:
			empty = False
			try:
				circ = my_queue.get(timeout=0.001)  # sleep
				if circ is None:
					self.shutdown_workers = True
					continue
			except queue.Empty:
				empty = True

			self.check_active_tasks(my_id)

			if not empty:
				result = None
				try:
					task_info = self.run_circuit_async(circ)
				except Exception as e:
					logging.critical(
						f"Async circuit {circ.get_cid()} failed before launch: {e}")
					circ.set_fail()
					# A real dict with an "error" key, not a string that merely
					# looks like one (see check_active_tasks for why).
					result = {"error": f"{type(e).__name__}: {e}"}
					rc = -1
					pass

				if my_queue.qsize() < self.num_worker_tasks:
					with self.worker_pool_lock:
						self.worker_pool[my_id]['state'] = UTIL_QRC.THREAD_STATE_FREE

				if result:
					r = {
						'cid': circ.get_cid(),
						'result': result,
						'rc': rc,
						'launch_time': circ.launch_time,
						'creation_time': circ.creation_time,
						'exec_time': circ.exec_time,
						'completion_time': circ.completion_time,
						'resources_consumed_time': circ.resources_consumed_time,
						'cq_enqueue_time': time.time(),
						'cq_dequeue_time': -1
					}
					r = self._attach_metrics(r)
					circ.free_resources(circ)
					if self.push_info:
						event = Event(self.push_info['evtype'], r)
						try:
							self.push_info['class'].put(event)
						except Exception as e:
							logging.critical(
								"Failed to push event to client. "
								f"Exception encountered {e}")
							raise e
					else:
						with self.circuit_results_lock:
							self.circuit_results.append(r)
				else:
					self.worker_pool[my_id]['active_tasks'].append(task_info)

	def read_cq(self, cid=None):
		if cid:
			with self.circuit_results_lock:
				i = 0
				for e in self.circuit_results:
					if cid == e['cid']:
						r = self.circuit_results.pop(i)
						r['cq_dequeue_time'] = time.time()
						self._note_result_dequeued(r)
						return r
					i += 1
		else:
			with self.circuit_results_lock:
				if len(self.circuit_results) > 0:
					r = self.circuit_results.pop(0)
					r['cq_dequeue_time'] = time.time()
					self._note_result_dequeued(r)
					return r
		return None

	def peak_cq(self, cid=None):
		if cid:
			with self.circuit_results_lock:
				i = 0
				for e in self.circuit_results:
					if cid == e['cid']:
						return e
					i += 1
		else:
			with self.circuit_results_lock:
				if len(self.circuit_results) > 0:
					return self.circuit_results[0]
		return None

	def run_circuit_async(self, circ):
		"""
		Async entry point. The circuit's QBacMet collection starts here and is finalized (with the run's
		timeline) when check_active_tasks() builds its result.
		"""
		self._start_collection(circ)
		try:
			return self._run_circuit_async_internal(circ)
		except Exception:
			self._forget_collection(circ)
			raise

	def _run_circuit_async_internal(self, circ):
		cid = circ.get_cid()

		tmp_dir = cdefw_global.get_defw_tmp_dir()

		qasm_c = circ.info["qasm"]
		qasm_file = os.path.join(tmp_dir, str(cid) + ".qasm")
		with open(qasm_file, 'w') as f:
			f.write(qasm_c)

		circ.set_launching()
		cmd = self.form_cmd(circ, qasm_file)
		try:
			task_info = {}
			logging.debug(f"Running -- {cmd}")
			pid = self.launcher.launch(cmd)
			logging.debug(f"Running -- {cmd} -- with pid {pid}")
			circ.set_running()
		except Exception as e:
			os.remove(qasm_file)
			logging.critical(f"Failed to launch {cmd}")
			raise e

		task_info['circ'] = circ
		task_info['qasm_file'] = qasm_file
		task_info['pid'] = pid

		return task_info

	def run_circuit(self, circ):
		"""
		Sync entry point: the circuit's QBacMet collection starts here and is finalized with the result.
		"""
		self._start_collection(circ)
		try:
			result = self._run_circuit_internal(circ)
		except Exception:
			self._forget_collection(circ)
			raise
		return self._attach_metrics(result)

	def _run_circuit_internal(self, circ):
		cid = circ.get_cid()

		tmp_dir = cdefw_global.get_defw_tmp_dir()

		qasm_c = circ.info["qasm"]
		qasm_file = os.path.join(tmp_dir, str(cid) + ".qasm")
		with open(qasm_file, 'w') as f:
			f.write(qasm_c)

		circ.set_launching()
		launcher = svc_launcher.Launcher()

		cmd = self.form_cmd(circ, qasm_file)
		try:
			logging.debug(f"Running -- {cmd}")
			circ.set_running()
			output, error, rc = launcher.launch(cmd, wait=True)
			task_info = {'qasm_file': qasm_file}

			t_parse0 = perf_counter()
			output = self.parse_task_result(output, circ, task_info)
			t_parse1 = perf_counter()
			parse_time = t_parse1 - t_parse0

			qm = self._collection(cid)
			if qm is not None:
				qm.postprocess_layer.metrics["result_marshalling_time"] = parse_time

			launcher.shutdown()
			logging.debug(f"Completed -- {cmd} -- returned {rc} -- {output} -- {error}")
		except Exception as e:
			launcher.shutdown()
			self.cleanup_task(circ, {'qasm_file': qasm_file})
			os.remove(qasm_file)
			logging.critical(f"Failed to launch {cmd}")
			raise e

		self.cleanup_task(circ, {'qasm_file': qasm_file})
		os.remove(qasm_file)

		if rc == 0:
			circ.set_exec_done()
			r = {
				'cid': cid,
				'result': output,
				'rc': rc,
				'launch_time': circ.launch_time,
				'creation_time': circ.creation_time,
				'exec_time': circ.exec_time,
				'completion_time': circ.completion_time,
				'resources_consumed_time': circ.resources_consumed_time,
				'cq_enqueue_time': time.time(),
				'cq_dequeue_time': -1
			}
			return r

		circ.set_fail()
		error_str = (
			f"Circuit failed with {rc}:{output.decode('utf8')}:"
			f"{error.decode('utf-8')}:"
			f"total run-time = {circ.exec_time - circ.completion_time}")
		logging.debug(error_str)
		raise DEFwExecutionError(error_str)

	def sync_run(self, circ):
		return self.run_circuit(circ)

	# Round robin over the workers so that they are all busy
	def async_run(self, circ):
		cid = circ.get_cid()
		rr = self.worker_pool_rr
		self.worker_pool_rr += 1
		idx = rr % self.num_workers
		i = idx
		with self.worker_pool_lock:
			while True:
				worker = self.worker_pool[i]
				if worker['state'] == UTIL_QRC.THREAD_STATE_FREE and worker['queue'].qsize() < self.num_worker_tasks:
					worker['queue'].put(circ)
					if worker['queue'].qsize() >= self.num_worker_tasks:
						worker['state'] = UTIL_QRC.THREAD_STATE_BUSY
					return cid
				else:
					i = (i + 1) % self.num_workers
					if i == idx:
						break
		# if we get here then there is no more threads to handle this
		# request. Raise an exception and the circuit will be queued
		raise DEFwOutOfResources(f"No more resource to run {cid}")

	def register_event_notification(self, info):
		self.push_info = info

	def shutdown(self):
		logging.critical("shutdown called")
		self.launcher.shutdown()
		self.shutdown_workers = True
