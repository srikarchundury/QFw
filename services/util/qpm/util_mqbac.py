"""MQBac at the place where QFw decides a circuit's number of processes.

Enabled with QFW_NP_POLICY=mqbac (default: the qubits-per-process rule alone). MQBac predicts the runtime of
the circuit for every process count this QPM can host and picks the fastest; the rule's count is kept unless
another one is predicted clearly faster (see mqbac/select.py). Any failure (MQBac not found, no trained model,
backend unknown to the model, QASM parse error) returns None and the caller keeps the rule.

    QFW_MQBAC_HOME   MQBac checkout (default: the MQBac folder next to QFw)
    MQBAC_MODEL      model file (default: $QFW_MQBAC_HOME/models/current/model_portable.npz, written by
                     ./retrain.sh; a new version is picked up on the next circuit)
    QFW_MQBAC_MAX_NP upper bound on the process counts MQBac may choose
"""
import hashlib
import logging
import os
import sys
import threading
from collections import OrderedDict

_LOCK = threading.Lock()
_STATE = {"modules": None, "warned": set()}


def policy():
	return os.environ.get("QFW_NP_POLICY", "rule").strip().lower()


def _warn_once(key, msg):
	if key not in _STATE["warned"]:
		_STATE["warned"].add(key)
		logging.warning(msg)


def _modules():
	with _LOCK:
		if _STATE["modules"] is None:
			home = os.environ.get("QFW_MQBAC_HOME") or os.path.abspath(
				os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "..", "MQBac"))
			if home not in sys.path:
				sys.path.append(home)
			from mqbac import circuit_features, select   # numpy + qiskit only
			_STATE["modules"] = (circuit_features, select)
		return _STATE["modules"]


def backend_label(info):
	"""MQBac's backend name for this circuit's QPM ('circuit_runner.nwqsim' -> 'nwqsim')."""
	qb = info.get("qfw_backend")
	return qb.rsplit(".", 1)[-1].lower() if isinstance(qb, str) else None


def resource_features(info):
	"""Sub-backend / device as the training data names them."""
	opts = info.get("qpm_options", {}) or {}
	label = backend_label(info)
	if label == "qiskitaer":
		sub = opts.get("backend", "statevector")
	elif label == "nwqsim":
		sub = info.get("backend", "OPENMP")
		sub = "MPI" if str(sub).upper() == "MPI" else sub
	else:
		sub = opts.get("backend") or info.get("backend")
	return {"sub_backend": sub, "device": str(opts.get("device", "CPU")).upper()}


_CACHE = OrderedDict()      # same circuit, resources and model -> same decision (variational loops repeat circuits)
_CACHE_SIZE = 4096


def decide_np(info, rule_np, ppn, capacity):
	"""MQBac's decision dict, or None to keep the rule."""
	label = backend_label(info)
	if not label or not info.get("qasm"):
		return None
	try:
		cf, select = _modules()
	except Exception as e:
		_warn_once("import", f"QFW_NP_POLICY=mqbac but MQBac could not be imported ({e}); using the rule")
		return None
	try:
		model = select.load()                      # reloads by itself when ./retrain.sh publishes a new version
		max_np = os.environ.get("QFW_MQBAC_MAX_NP")
		rf = resource_features(info)
		key = (hashlib.sha1(info["qasm"].encode()).hexdigest(), label, str(rf), rule_np, ppn, capacity, max_np,
		       id(model))
		with _LOCK:
			if key in _CACHE:
				_CACHE.move_to_end(key)
				return dict(_CACHE[key], cached=True)
		feats = cf.flat_features(cf.from_qasm(info["qasm"]))
		d = select.choose_np(feats, label, rule_np=rule_np, ppn=ppn, capacity=capacity,
		                     sub_backend=rf["sub_backend"], device=rf["device"], model=model,
		                     max_np=int(max_np) if max_np else None)
		if d is None:
			_warn_once(("backend", label), f"MQBac model does not know backend '{label}'; using the rule")
			return None
		with _LOCK:
			_CACHE[key] = d
			while len(_CACHE) > _CACHE_SIZE:
				_CACHE.popitem(last=False)
		return d
	except FileNotFoundError as e:
		_warn_once("model", f"no MQBac model ({e}); run MQBac/retrain.sh; using the rule")
	except Exception as e:
		_warn_once(("error", type(e).__name__), f"MQBac np decision failed ({type(e).__name__}: {e}); using the rule")
	return None
