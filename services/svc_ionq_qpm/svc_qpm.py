import os
import logging
import yaml

from util.qpm.util_qpm import UTIL_QPM
from defw_exception import DEFwError
from .svc_qrc import QRC

CURRENT_PATH = os.path.split(os.path.abspath(__file__))[0]


class QPM(UTIL_QPM):
	def __init__(self, start=True):
		logging.debug("IONQ_QPM: init starting")
		super().__init__(QRC(start=start), start=start)

		self.provider_name = "IONQ"
		self.ionq_provider = None
		self.backends = []

		cfg_path = os.path.join(CURRENT_PATH, "ionq_env.yaml")
		self.load_ionq_env_yaml(cfg_path)

		api_key = os.getenv("IONQ_API_KEY", "").strip()
		if not api_key:
			raise DEFwError("IONQ_API_KEY not set in environment!")

		if not start:
			# The framework probes every registered service class with a
			# disposable start=False instance (see BaseAgentAPI.query())
			# purely to read static capability metadata via query(). That
			# happens repeatedly (e.g. on every get_services() lookup), so
			# it must stay cheap — never make the real network round-trip
			# to IonQ's cloud API here, or every capability probe pays for
			# a full provider/backends() fetch and starves real QPM
			# reservation of the time it needs to register.
			logging.debug("IONQ_QPM: start=False – skipping cloud provider init (metadata probe only)")
			self.ionq_provider = None
			self.backends = []
		elif os.environ.get("QFW_TRANSPILE_ONLY", "").strip() == "1":
			logging.info("IONQ_QPM: QFW_TRANSPILE_ONLY=1 – skipping cloud provider init")
			self.ionq_provider = None
			self.backends = []
		else:
			try:
				from qiskit_ionq import IonQProvider
				self.ionq_provider = IonQProvider(api_key)

				# Can hit network; keep but fail loudly if creds/proxy wrong
				self.backends = self.ionq_provider.backends()
				backend_names = []
				for b in self.backends:
					name = getattr(b, "name", None)
					if name is None:
						try:
							name = b.name()
						except Exception:
							name = str(b)
					backend_names.append(str(name))
				logging.info(
					"IONQ_QPM: initialized with %s backends: %s",
					len(self.backends),
					backend_names,
				)

			except Exception as e:
				logging.critical(f"IONQ_QPM: provider init failed: {e}")
				raise DEFwError(f"IONQ_API_KEY invalid/unreachable: {e}")

		logging.debug("IONQ_QPM: init done")

	def load_ionq_env_yaml(self, path=None):
		if path is None:
			path = os.path.join(CURRENT_PATH, "ionq_env.yaml")

		logging.debug(f"IONQ_QPM: loading env YAML from {path}")

		if not os.path.exists(path):
			raise FileNotFoundError(f"IONQ_QPM: YAML config not found: {path}")

		with open(path, "r") as f:
			cfg = yaml.safe_load(f) or {}

		ionq_cfg = cfg.get("CONFIG", {}) or {}
		if not isinstance(ionq_cfg, dict):
			raise DEFwError("IONQ_QPM: YAML CONFIG section is not a dict")

		for key, value in ionq_cfg.items():
			if value is None:
				logging.warning(f"IONQ_QPM: config field {key} is None (skipping)")
				continue
			os.environ[str(key)] = str(value)
			logging.debug(f"IONQ_QPM: set env {key}=<set>")

	def query(self):
		logging.debug("IONQ_QPM: query")
		from . import SERVICE_NAME, SERVICE_DESC
		from api_qpm import QPMType, QPMCapability

		info = self.query_helper(
			QPMType.QPM_TYPE_IONQ,
			QPMCapability.QPM_CAP_IONTRAP,
			SERVICE_NAME,
			SERVICE_DESC,
		)
		logging.debug(f"IONQ_QPM: {SERVICE_NAME} info: {info}")
		return info

	def create_circuit(self, info):
		# Inject the provider object into the circuit info.
		info["qfw_backend"] = self.ionq_provider
		info["np"] = 0
		info["exec"] = None
		info["modules"] = {}
		return super().create_circuit(info)

	def consume_resources(self, circ):
		# Remote provider: no DEFw node resource consumption needed
		circ.set_resources_consumed()
		logging.debug(f"IONQ_QPM: marked circuit {circ.get_cid()} as resources consumed")

	def test(self):
		if self.ionq_provider is None:
			raise DEFwError("IONQ_QPM not ready: missing ionq_provider")
		return "****IonQ QPM Test Successful****"
