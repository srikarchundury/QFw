import logging
import os
from time import sleep

import defw
from defw_app_util import defw_get_resource_mgr, SYSTEM_UP_TIMEOUT
from defw_common_def import set_rpc_timeout, get_rpc_timeout
from defw_exception import DEFwReserveError

# Which QPM implementation to use when more than one is registered for the
# same device. The resmgr matches QPMs on name+type+caps only and ignores
# service properties (see DEFwServiceInfo.is_match), so the native service
# (svc_iqm_qpm, provider 'iqm') and the QRMI/QDMI shim (svc_lib_qpm,
# provider 'shim') advertise an identical triple and both come back from
# get_services. We disambiguate here on the advertised 'provider' property:
#   QFW_QPM_IMPL=iqm   -> native IQM service (default)
#   QFW_QPM_IMPL=shim  -> QRMI/QDMI bifurcation front-end
QPM_IMPL_ENV = "QFW_QPM_IMPL"
DEFAULT_QPM_IMPL = "iqm"


# Cloud QPM services (IonQ, IBM-Q) make a real network call to the
# provider's API during their own startup (fetching the backend list), which
# is slower and more variable than local simulator services. The default
# SYSTEM_UP_TIMEOUT (40s) is tuned for local service startup and can expire
# before a cloud QPM finishes registering with the resource manager, even
# though the service itself starts successfully a little later.
QPM_RESERVE_TIMEOUT = max(SYSTEM_UP_TIMEOUT, 90)


def _reserve_qpm(rmgr, qpm_type, qpm_cap, timeout=QPM_RESERVE_TIMEOUT):
	want = os.environ.get(QPM_IMPL_ENV, DEFAULT_QPM_IMPL)

	infos = []
	wait = 0
	while wait < timeout:
		infos = rmgr.get_services('QPM', qpm_type, qpm_cap)
		if infos:
			break
		wait += 1
		logging.debug("Waiting to connect to QPM")
		sleep(1)
	if not infos:
		raise DEFwReserveError(
			f"Couldn't connect to a QPM ({qpm_type}, {qpm_cap})")

	# resmgr matching ignores properties, so select the requested impl on the
	# 'provider' property here. Fall back to whatever matched if the requested
	# impl isn't registered (e.g. only one of native/shim is loaded).
	chosen = [i for i in infos if i.get_properties().get('provider') == want]
	if chosen:
		logging.debug(
			f"selected QPM impl '{want}' "
			f"({len(chosen)} of {len(infos)} match(es))")
	else:
		logging.debug(
			f"no QPM with provider '{want}'; using first of "
			f"{len(infos)} match(es)")
		chosen = infos

	# Real instantiation of a cloud QPM (IonQ/IBM-Q) does provider auth and
	# a backends() fetch over a real network round-trip in __init__, which
	# can take longer than DEFw's default 300s RPC timeout. Bump it for
	# this specific instantiate_class RPC and restore the previous value
	# right after, so a longer allowance here doesn't mask genuine hangs
	# in unrelated RPCs elsewhere in the same process.
	prev_timeout = get_rpc_timeout()
	try:
		set_rpc_timeout(max(prev_timeout, 600))
		return defw.connect_to_resource(chosen, 'QPM')[0]
	finally:
		set_rpc_timeout(prev_timeout)


def test_qpm(qpm_api):
	logging.debug("Testing QPM")
	logging.debug(qpm_api.test())


def get_qpm(qpm_type=-1, qpm_cap=-1):
	#Grab a qpm if one exists
	rmgr = defw_get_resource_mgr()
	qpm_api = _reserve_qpm(rmgr, qpm_type, qpm_cap)

	logging.debug(f"got the qpm {qpm_api}")

	try:
		test_qpm(qpm_api)
	except Exception as e:
		logging.debug(f"QPM ran into an exception {e}")
		qpm_api.shutdown()

	return qpm_api
