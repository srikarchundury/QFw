import os
import logging
import yaml

from util.qpm.util_qpm import UTIL_QPM
from defw_exception import DEFwError
from .svc_qrc import QRC

CURRENT_PATH = os.path.split(os.path.abspath(__file__))[0]


class QPM(UTIL_QPM):
	def __init__(self, start=True):
		logging.debug("IBMQ_QPM(Runtime): init starting")
		super().__init__(QRC(start=start), start=start)

		self.provider_name = "IBMQ"
		self.service = None
		self.backends = []

		cfg_path = os.path.join(CURRENT_PATH, "ibmq_env.yaml")
		self.load_ibmq_env_yaml(cfg_path)

		api_key = os.getenv("IBMQ_API_KEY", "").strip()
		service_crn = os.getenv("IBMQ_SERVICE_CRN", "").strip()
		if not api_key:
			raise DEFwError("IBMQ_API_KEY not set in environment!")
		if not service_crn:
			raise DEFwError("IBMQ_SERVICE_CRN not set in environment!")

		if not start:
			# The framework probes every registered service class with a
			# disposable start=False instance (see BaseAgentAPI.query())
			# purely to read static capability metadata via query(). That
			# happens repeatedly (e.g. on every get_services() lookup), so
			# it must stay cheap — never make the real network round-trip
			# to IBM Runtime here, or every capability probe pays for a
			# full service/backends() fetch and starves real QPM
			# reservation of the time it needs to register.
			logging.debug("IBMQ_QPM(Runtime): start=False – skipping cloud service init (metadata probe only)")
			self.service = None
			self.backends = []
		elif os.environ.get("QFW_TRANSPILE_ONLY", "").strip() == "1":
			logging.info("IBMQ_QPM(Runtime): QFW_TRANSPILE_ONLY=1 – skipping cloud service init")
			self.service = None
			self.backends = []
		else:
			try:
				from qiskit_ibm_runtime import QiskitRuntimeService

				channel = os.getenv("IBMQ_CHANNEL", "").strip() or "ibm_quantum_platform"

				os.environ["QISKIT_IBM_TOKEN"] = api_key
				os.environ["QISKIT_IBM_INSTANCE"] = service_crn
				os.environ["QISKIT_IBM_CHANNEL"] = channel

				self.service = QiskitRuntimeService(
					channel=channel,
					token=api_key,
					instance=service_crn,
				)

				try:
					self.backends = self.service.backends()
					logging.debug(f"IBMQ_QPM(Runtime): initialized with {len(self.backends)} backends")
				except Exception as e:
					self.backends = []
					logging.warning(f"IBMQ_QPM(Runtime): backends() failed during init (continuing): {e}")

			except Exception as e:
				logging.critical(f"IBMQ_QPM(Runtime): service init failed: {e}")
				raise DEFwError(f"IBMQ runtime init failed (bad proxy/CRN/key?): {e}")

		logging.debug("IBMQ_QPM(Runtime): init done")

	def load_ibmq_env_yaml(self, path=None):
		if path is None:
			path = os.path.join(CURRENT_PATH, "ibmq_env.yaml")

		logging.debug(f"IBMQ_QPM(Runtime): loading env YAML from {path}")

		if not os.path.exists(path):
			raise FileNotFoundError(f"IBMQ_QPM(Runtime): YAML config not found: {path}")

		with open(path, "r") as f:
			cfg = yaml.safe_load(f) or {}

		ibmq_cfg = cfg.get("CONFIG", {}) or {}
		if not isinstance(ibmq_cfg, dict):
			raise DEFwError("IBMQ_QPM(Runtime): YAML CONFIG section is not a dict")

		for key, value in ibmq_cfg.items():
			if value is None:
				logging.warning(f"IBMQ_QPM(Runtime): config field {key} is None (skipping)")
				continue
			os.environ[str(key)] = str(value)
			logging.debug(f"IBMQ_QPM(Runtime): set env {key}=<set>")

	def query(self):
		logging.debug("IBMQ_QPM(Runtime): query")
		from . import SERVICE_NAME, SERVICE_DESC
		from api_qpm import QPMType, QPMCapability

		return self.query_helper(
			QPMType.QPM_TYPE_IBMQ,
			QPMCapability.QPM_CAP_SUPERCONDUCTING,
			SERVICE_NAME,
			SERVICE_DESC,
		)

	def create_circuit(self, info):
		info["qfw_backend"] = self.service
		info["np"] = 0
		info["exec"] = None
		info["modules"] = {}
		return super().create_circuit(info)

	def consume_resources(self, circ):
		circ.set_resources_consumed()
		logging.debug(f"IBMQ_QPM(Runtime): marked circuit {circ.get_cid()} as resources consumed")

	def test(self):
		if self.service is None:
			raise DEFwError("IBMQ_QPM(Runtime) not ready: missing runtime service")
		return "****IBMQ(Runtime) QPM Test Successful****"
