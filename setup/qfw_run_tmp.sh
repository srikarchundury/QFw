#!/usr/bin/env bash

# Separate script invocations within one job (qfw_setup.sh, qfw_srun.sh,
# qfw_teardown.sh, ...) can't inherit each other's exported QFW_RUN_ID, so
# they rediscover it via a "current run" pointer file under QFW_TMP_PATH.
# That file must be scoped per job (SLURM_JOB_ID, falling back to the PID
# for interactive/non-Slurm use) — otherwise concurrent jobs sharing one
# QFW_TMP_PATH overwrite each other's pointer and end up running against
# the same QFW_RUN_TMP_PATH, corrupting each other's circuits/results.
qfw_run_id_scope_key() {
	printf '%s' "${SLURM_JOB_ID:-$$}"
}

qfw_require_tmp_base() {
	if [[ -z "${QFW_TMP_PATH:-}" ]]; then
		echo "ERROR: QFW_TMP_PATH is not set; source qfw_activate first" >&2
		return 1
	fi
	mkdir -p "${QFW_TMP_PATH}"
}

qfw_set_run_tmp_from_id() {
	if [[ -z "${QFW_RUN_ID:-}" ]]; then
		echo "ERROR: QFW_RUN_ID is not set" >&2
		return 1
	fi
	export QFW_RUN_TMP_PATH="${QFW_TMP_PATH}/${QFW_RUN_ID}"
	mkdir -p "${QFW_RUN_TMP_PATH}"
}

qfw_create_run_tmp() {
	qfw_require_tmp_base || return 1
	if [[ -z "${QFW_RUN_ID:-}" ]]; then
		export QFW_RUN_ID="$(date +%Y%m%d-%H%M%S)-$(qfw_run_id_scope_key)"
	fi
	qfw_set_run_tmp_from_id || return 1
	printf '%s\n' "${QFW_RUN_ID}" > "${QFW_TMP_PATH}/current.$(qfw_run_id_scope_key)"
	printf '%s\n' "${QFW_RUN_ID}" > "${QFW_TMP_PATH}/latest"
}

qfw_use_current_run_tmp() {
	qfw_require_tmp_base || return 1
	if [[ -z "${QFW_RUN_ID:-}" ]]; then
		local scoped="${QFW_TMP_PATH}/current.$(qfw_run_id_scope_key)"
		if [[ -f "${scoped}" ]]; then
			read -r QFW_RUN_ID < "${scoped}"
		elif [[ -f "${QFW_TMP_PATH}/current" ]]; then
			# Fallback for callers with no SLURM_JOB_ID/PID match (e.g. an
			# older single-job layout); not safe under concurrent jobs.
			read -r QFW_RUN_ID < "${QFW_TMP_PATH}/current"
		else
			echo "ERROR: no active QFw run found under ${QFW_TMP_PATH}" >&2
			echo "Run qfw_setup.sh first, or export QFW_RUN_ID." >&2
			return 1
		fi
		export QFW_RUN_ID
	fi
	qfw_set_run_tmp_from_id
}

qfw_clear_current_run_tmp() {
	qfw_require_tmp_base || return 1
	local scoped="${QFW_TMP_PATH}/current.$(qfw_run_id_scope_key)"
	if [[ -f "${scoped}" ]]; then
		local current_id
		read -r current_id < "${scoped}"
		if [[ "${current_id}" == "${QFW_RUN_ID:-}" ]]; then
			rm -f "${scoped}"
		fi
	fi
}
