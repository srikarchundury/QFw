#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -z "${QFW_SETUP_PATH:-}" ]]; then
	export QFW_SETUP_PATH="${SCRIPT_DIR}"
fi

if [[ -z "${_QFW_ACTIVE:-}" ]]; then
	source $QFW_SETUP_PATH/qfw_activate --skip-patches
fi

source "$QFW_SETUP_PATH/qfw_run_tmp.sh"
qfw_use_current_run_tmp || exit 1

hostname=$(hostname)
export DEFW_CONFIG_PATH=$DEFW_PATH/python/config/defw_generic.yaml
export DEFW_SHELL_TYPE=cmdline
export DEFW_LISTEN_PORT=8095
export DEFW_AGENT_TYPE=agent
export DEFW_LOG_LEVEL=all
export DEFW_LOAD_NO_INIT=svc_launcher
export DEFW_ONLY_LOAD_MODULE=svc_resmgr
export DEFW_DISABLE_RESMGR=yes
export DEFW_AGENT_NAME=qfw_teardown
export DEFW_LOG_DIR=$QFW_RUN_TMP_PATH/${DEFW_AGENT_NAME}_${hostname}

source $QFW_SETUP_PATH/qfw_lib_path.sh
source "$QFW_SETUP_PATH/qfw_allocation.sh"
qfw_detect_allocation --force || exit 1

python3 $QFW_SETUP_PATH/qfw_setup.py --shutdown --groups "$QFW_GROUPS"
teardown_rc=$?
# Deliberately NOT calling qfw_clear_current_run_tmp here: the pointer file
# is scoped per-job (by SLURM_JOB_ID) so there's no cross-job collision risk
# from leaving it in place, and qfw_deactivate (which runs after this script
# exits, back in the job's own shell) still needs to rediscover QFW_RUN_ID
# from it in order to restore the venv's swapped python symlinks under the
# same key setup_qfw_symlinks() registered them under. Clearing it here ran
# in a separate subprocess anyway (this script is exec'd, not sourced, so
# unsetting QFW_RUN_ID here wouldn't affect the caller either way) but the
# file deletion is a real filesystem side effect that broke that lookup.
exit $teardown_rc
