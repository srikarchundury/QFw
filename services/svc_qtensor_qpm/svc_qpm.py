import logging
from util.qpm.util_qpm import UTIL_QPM
from .svc_qrc import QRC


class QPM(UTIL_QPM):
	def __init__(self, start=True):
		logging.debug("QTENSOR_QPM: init")
		super().__init__(QRC(start=start), start=start)

	def query(self):
		from . import SERVICE_NAME, SERVICE_DESC
		from api_qpm import QPMType, QPMCapability

		info = self.query_helper(
			QPMType.QPM_TYPE_QTENSOR | QPMType.QPM_TYPE_SIMULATOR,
			QPMCapability.QPM_CAP_TENSORNETWORK,
			SERVICE_NAME,
			SERVICE_DESC,
		)
		logging.debug(f"QTENSOR_QPM: {SERVICE_DESC}: {info}")
		return info

	def create_circuit(self, info):
		info["qfw_backend"] = "circuit_runner.qtensor"
		return super().create_circuit(info)

	def test(self):
		return "****QTensor QPM Test Successful****"
