#!/bin/bash

###########################
N_NODES=1
###########################

usage() {
	echo "Usage (H2): $0 H2 <sim_type> <sub_backend> <device> <run_mode> <optimization_level> <n_processes> <n_points> <basis_set>"
	echo "Example:    $0 H2 qiskitaer statevector CPU sync 0 2 3 sto-3g"
	echo
	echo "Usage (C):  $0 C <sim_type> <sub_backend> <device> <run_mode> <optimization_level> <n_processes> <scenario>"
	echo "Example:    $0 C qiskitaer statevector CPU sync 0 2 diamond"
	echo
	echo "Note: n_processes only affects qiskitaer/nwqsim/tnqvm/qtensor (real"
	echo "      local mpirun launches). ionq/ibmq never read it -- they make"
	echo "      a single direct cloud API call regardless of this value."
	echo "Note (H2): basis_set is the #qubits knob (electron count is always"
	echo "      (1,1); a bigger basis just adds virtual orbitals):"
	echo "        sto-3g       ->  4 qubits"
	echo "        6-31g        ->  8 qubits"
	echo "        6-311g       -> 12 qubits"
	echo "        cc-pvdz      -> 20 qubits"
	echo "        aug-cc-pvdz  -> 36 qubits"
	echo "Note (C): scenario picks the active space (electron count fixed per scenario):"
	echo "        diamond   -> 16 qubits (8 orbitals, FCI reference)"
	echo "        graphene  -> 16 qubits (8 orbitals, FCI reference)"
	echo "        cnt       -> 48 qubits (24 orbitals, HF reference -- too large for FCI)"
	exit 1
}

if [ "$#" -lt 1 ]; then
	usage
fi

MOLECULE=$1
shift

case "$MOLECULE" in
	H2)
		if [ "$#" -ne 8 ]; then
			usage
		fi
		SIM_QRC=$1
		SUB_BACKEND=$2
		DEVICE=$3
		RUN_MODE=$4
		OPT_LEVEL=$5
		N_PROCESSES_PER_NODE=$6
		N_POINTS=$7
		BASIS_SET=$8
		APP_SCRIPT="H2-sqd-qfw.py"
		;;
	C)
		if [ "$#" -ne 7 ]; then
			usage
		fi
		SIM_QRC=$1
		SUB_BACKEND=$2
		DEVICE=$3
		RUN_MODE=$4
		OPT_LEVEL=$5
		N_PROCESSES_PER_NODE=$6
		SCENARIO=$7
		APP_SCRIPT="Carbon-sqd-qfw.py"
		;;
	*)
		echo "Error: molecule must be 'H2' or 'C'. Got '${MOLECULE}'."
		usage
		;;
esac

valid_sim_types=("nwqsim" "qiskitaer" "tnqvm" "qtensor" "ionq" "ibmq")
declare -A valid_sub_backends
valid_sub_backends["qiskitaer"]="automatic statevector matrix_product_state tensor_network"
valid_sub_backends["nwqsim"]="AMDGPU AMDGPU_MPI OpenMP MPI"
valid_sub_backends["qtensor"]="cupy numpy torch"
valid_sub_backends["tnqvm"]="exatn-mps exatn-ttn exatn-peps"
valid_sub_backends["ionq"]="simulator aria-1 aria-2 forte-1 forte-enterprise-1 harmony ideal"
valid_sub_backends["ibmq"]="ibm_miami ibm_boston ibm_torino"
valid_devices=("CPU" "GPU")
# Both applications' SQD post-processing runs to convergence on a single
# sample set fetched per invocation (H2 additionally loops over bond-distance
# points, one sample set each), so only sync mode is supported.
valid_run_modes=("sync")
valid_basis_sets=("sto-3g" "6-31g" "6-311g" "cc-pvdz" "aug-cc-pvdz")
valid_scenarios=("diamond" "graphene" "cnt")

if [[ ! " ${valid_sim_types[@]} " =~ " ${SIM_QRC} " ]]; then
	echo "Error: Invalid simulator type '${SIM_QRC}'. Valid options are: ${valid_sim_types[@]}"
	usage
fi

if [[ -n "${valid_sub_backends[$SIM_QRC]}" ]]; then
	if [[ ! " ${valid_sub_backends[$SIM_QRC]} " =~ " ${SUB_BACKEND} " ]]; then
		echo "Error: Invalid sub-backend '${SUB_BACKEND}' for simulator type '${SIM_QRC}'. Valid options are: ${valid_sub_backends[$SIM_QRC]}"
		usage
	fi
fi

if [[ ! " ${valid_devices[@]} " =~ " ${DEVICE} " ]]; then
	echo "Error: Invalid device type '${DEVICE}'. Valid options are: ${valid_devices[@]}"
	usage
fi

if [[ ! " ${valid_run_modes[@]} " =~ " ${RUN_MODE} " ]]; then
	echo "Error: Invalid run mode '${RUN_MODE}'. Valid options are: ${valid_run_modes[@]}"
	usage
fi

if ! [[ "${OPT_LEVEL}" =~ ^[0-9]+$ ]]; then
	echo "Error: Optimization level must be a non-negative integer."
	usage
fi

if ! [[ "${N_PROCESSES_PER_NODE}" =~ ^[0-9]+$ ]] || [[ ${N_PROCESSES_PER_NODE} -le 0 ]]; then
	echo "Error: Number of processes must be a positive integer."
	usage
fi

if [ "$MOLECULE" == "H2" ]; then
	if ! [[ "${N_POINTS}" =~ ^[0-9]+$ ]] || [[ ${N_POINTS} -le 0 ]]; then
		echo "Error: Number of PES points must be a positive integer."
		usage
	fi
	if [[ ! " ${valid_basis_sets[@]} " =~ " ${BASIS_SET} " ]]; then
		echo "Error: Invalid basis set '${BASIS_SET}'. Valid options are: ${valid_basis_sets[@]}"
		usage
	fi
else
	if [[ ! " ${valid_scenarios[@]} " =~ " ${SCENARIO} " ]]; then
		echo "Error: Invalid scenario '${SCENARIO}'. Valid options are: ${valid_scenarios[@]}"
		usage
	fi
fi

QFW_ROOT=/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw
RESULT_DIR=/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/results_analysis/raw_data
mkdir -p "${RESULT_DIR}/sqd_data"

if [ "$MOLECULE" == "H2" ]; then
	OUTPUT_TAG="_${SIM_QRC}_${SUB_BACKEND}_${DEVICE}_${RUN_MODE}_${N_NODES}_${N_PROCESSES_PER_NODE}_${BASIS_SET}_pts${N_POINTS}_opt${OPT_LEVEL}"
	APP_ARGS="$SIM_QRC $SUB_BACKEND $DEVICE $RUN_MODE $N_POINTS $OPT_LEVEL $BASIS_SET"
	RUN_DESC="SQD H2 PES scan with $SIM_QRC $SUB_BACKEND on $DEVICE using $RUN_MODE mode on $N_NODES nodes with $N_PROCESSES_PER_NODE processes per node, basis set $BASIS_SET, for $N_POINTS bond-distance points."
else
	OUTPUT_TAG="_carbon_${SIM_QRC}_${SUB_BACKEND}_${DEVICE}_${RUN_MODE}_${N_NODES}_${N_PROCESSES_PER_NODE}_${SCENARIO}_opt${OPT_LEVEL}"
	APP_ARGS="$SIM_QRC $SUB_BACKEND $DEVICE $RUN_MODE $SCENARIO $OPT_LEVEL"
	RUN_DESC="SQD Carbon ($SCENARIO) run with $SIM_QRC $SUB_BACKEND on $DEVICE using $RUN_MODE mode on $N_NODES nodes with $N_PROCESSES_PER_NODE processes per node."
fi

# Real IBM/IonQ hardware jobs wait on a genuine cloud queue, which can
# easily exceed the short walltime used for simulator jobs (kept short
# there so they backfill quickly on a busy partition). A real ibm_boston
# job was seen mid-poll on the actual HTTPS response from IBM's API when
# a 15-minute allocation ran out -- the job was genuinely still queued on
# real hardware, not stuck/broken, it just needed more wall-clock time.
if [[ "$SIM_QRC" == "ionq" || "$SIM_QRC" == "ibmq" ]]; then
	WALLTIME="1:00:00"
else
	WALLTIME="0:15:00"
fi

echo "###########################"
echo "MOLECULE: $MOLECULE"
echo "N_NODES: $N_NODES"
echo "N_PROCESSES_PER_NODE: $N_PROCESSES_PER_NODE"
echo "WALLTIME: $WALLTIME"
echo "###########################"

echo "SQD Run"
echo "MOLECULE: $MOLECULE"
echo "SIM_QRC: $SIM_QRC"
echo "SUB_BACKEND: $SUB_BACKEND"
echo "DEVICE: $DEVICE"
echo "RUN_MODE: $RUN_MODE"
echo "OPT_LEVEL: $OPT_LEVEL"
echo "N_PROCESSES_PER_NODE: $N_PROCESSES_PER_NODE"
if [ "$MOLECULE" == "H2" ]; then
	echo "N_POINTS: $N_POINTS"
	echo "BASIS_SET: $BASIS_SET"
else
	echo "SCENARIO: $SCENARIO"
fi

# Submit SLURM job
sbatch <<EOT
#!/bin/bash

#SBATCH --output=${RESULT_DIR}/sqd_data/${OUTPUT_TAG}_%j.out

# job component 1
#SBATCH -A GEN006
#SBATCH -N 1
#SBATCH -t ${WALLTIME}
#SBATCH -J sqd_${MOLECULE,,}
#SBATCH -p batch

#SBATCH hetjob

# job component 2
#SBATCH -A GEN006
#SBATCH -N ${N_NODES}
#SBATCH -t ${WALLTIME}
#SBATCH -J sqd_${MOLECULE,,}
#SBATCH -p batch

source ${QFW_ROOT}/setup/qfw_activate

uname -a
echo "# START-TIME: \$(date)"
echo "#            SLURM_NNODES: \$SLURM_NNODES"
echo "#            SLURM_NPROCS: \$SLURM_NPROCS"
echo "#             SLURM_JOBID: \$SLURM_JOBID"
echo "# SLURM_JOB_CPUS_PER_NODE: \$SLURM_JOB_CPUS_PER_NODE"
echo "#  SLURM_THREADS_PER_CORE: \$SLURM_THREADS_PER_CORE"
echo "#----"

module list
echo "QFW_TMP_PATH: \${QFW_TMP_PATH}"

# Real-run toggle for ionq/ibmq.
# Default behavior is REAL execution (QFW_TRANSPILE_ONLY=0/unset).
# Set QFW_TRANSPILE_ONLY=1 in the caller environment to force transpile-only mode.
if [[ "$SIM_QRC" == "ionq" || "$SIM_QRC" == "ibmq" ]]; then
	if [[ "${QFW_TRANSPILE_ONLY:-0}" == "1" ]]; then
		export QFW_TRANSPILE_ONLY=1
		echo "QFW_TRANSPILE_ONLY=1 (transpile-only mode enabled by caller)"
	else
		unset QFW_TRANSPILE_ONLY
		echo "QFW_TRANSPILE_ONLY disabled (real cloud execution)"
	fi

	# Client-side wait for results (QFwBackend.COMPLETION_TIMEOUT_SEC,
	# backends/qfw_qiskit/qfw_simulator.py) defaults to 200s -- far short of
	# a real hardware queue. A real ibm_boston job was seen still genuinely
	# queued on IBM's API when this fired. Set comfortably under this job's
	# walltime so the app itself reports "still queued" instead of SLURM
	# just killing the job at the wall-clock limit.
	export QFW_FORCE_COMPLETION_TIMEOUT=3300
	echo "QFW_FORCE_COMPLETION_TIMEOUT=3300 (real hardware queue wait)"
fi

# Force the circuit's local process count for this run (see
# services/util/qpm/util_circuit.py:setup_circuit_run_details). Only
# affects backends that launch a real local mpirun (qiskitaer/nwqsim/
# tnqvm/qtensor); ionq/ibmq never read it.
export QFW_FORCE_NP=${N_PROCESSES_PER_NODE}

# frontier.yaml's default MPI binding policy (map-by: ppr:1:l3cache) caps
# single-node process count at the number of L3 cache domains (8 on
# Frontier) -- OpenMPI refuses to spawn more ranks than that under this
# policy. Force core-level binding uniformly across the whole sweep
# (services/util/mpi.py's QFW_FORCE_MAP_BY override) whenever a specific
# process count is being forced, both to let np>8 fit on one node at all,
# and so every point in a process-count sweep uses the same binding policy
# (mixing l3cache-bound and core-bound runs would confound the comparison).
export QFW_FORCE_MAP_BY=core

qfw_setup.sh

echo "Running ${RUN_DESC}"

qfw_srun.sh "\$QFW_PATH/../applications/sqd_related/code/${APP_SCRIPT}" ${APP_ARGS}

qfw_teardown.sh

echo "# RC=\$?"
echo "# END-TIME: \$(date)"

unset QFW_FORCE_NP
unset QFW_FORCE_MAP_BY
qfw_deactivate
EOT
