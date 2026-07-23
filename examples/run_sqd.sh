#!/bin/bash

###########################
N_NODES=1
###########################

if [ "$#" -ne 8 ]; then
	echo "Usage: $0 <sim_type> <sub_backend> <device> <run_mode> <n_points> <optimization_level> <n_processes> <basis_set>"
	echo "Example: $0 qiskitaer statevector CPU sync 3 0 2 sto-3g"
	echo "Note: n_processes only affects qiskitaer/nwqsim/tnqvm/qtensor (real"
	echo "      local mpirun launches). ionq/ibmq never read it -- they make"
	echo "      a single direct cloud API call regardless of this value."
	echo "Note: basis_set is the #qubits knob for H2 (electron count is always"
	echo "      (1,1); a bigger basis just adds virtual orbitals):"
	echo "        sto-3g       ->  4 qubits"
	echo "        6-31g        ->  8 qubits"
	echo "        6-311g       -> 12 qubits"
	echo "        cc-pvdz      -> 20 qubits"
	echo "        aug-cc-pvdz  -> 36 qubits"
	exit 1
fi

SIM_QRC=$1
SUB_BACKEND=$2
DEVICE=$3
RUN_MODE=$4
N_POINTS=$5
OPT_LEVEL=$6
N_PROCESSES_PER_NODE=$7
BASIS_SET=$8

valid_sim_types=("nwqsim" "qiskitaer" "tnqvm" "qtensor" "ionq" "ibmq")
declare -A valid_sub_backends
valid_sub_backends["qiskitaer"]="automatic statevector matrix_product_state tensor_network"
valid_sub_backends["nwqsim"]="AMDGPU AMDGPU_MPI OpenMP MPI"
valid_sub_backends["qtensor"]="cupy numpy torch"
valid_sub_backends["tnqvm"]="exatn-mps exatn-ttn exatn-peps"
valid_sub_backends["ionq"]="simulator aria-1 aria-2 forte-1 forte-enterprise-1 harmony ideal"
valid_sub_backends["ibmq"]="ibm_miami ibm_boston"
valid_devices=("CPU" "GPU")
# The PES scan processes one bond distance at a time (SQD post-processing
# needs each point's counts before moving to the next), so only sync mode
# is supported.
valid_run_modes=("sync")
valid_basis_sets=("sto-3g" "6-31g" "6-311g" "cc-pvdz" "aug-cc-pvdz")

if [[ ! " ${valid_sim_types[@]} " =~ " ${SIM_QRC} " ]]; then
	echo "Error: Invalid simulator type '${SIM_QRC}'. Valid options are: ${valid_sim_types[@]}"
	echo "Usage: $0 <sim_type> <sub_backend> <device> <run_mode> <n_points> <optimization_level> <n_processes>"
	exit 1
fi

if [[ -n "${valid_sub_backends[$SIM_QRC]}" ]]; then
	if [[ ! " ${valid_sub_backends[$SIM_QRC]} " =~ " ${SUB_BACKEND} " ]]; then
		echo "Error: Invalid sub-backend '${SUB_BACKEND}' for simulator type '${SIM_QRC}'. Valid options are: ${valid_sub_backends[$SIM_QRC]}"
		echo "Usage: $0 <sim_type> <sub_backend> <device> <run_mode> <n_points> <optimization_level> <n_processes>"
		exit 1
	fi
fi

if [[ ! " ${valid_devices[@]} " =~ " ${DEVICE} " ]]; then
	echo "Error: Invalid device type '${DEVICE}'. Valid options are: ${valid_devices[@]}"
	echo "Usage: $0 <sim_type> <sub_backend> <device> <run_mode> <n_points> <optimization_level> <n_processes>"
	exit 1
fi

if [[ ! " ${valid_run_modes[@]} " =~ " ${RUN_MODE} " ]]; then
	echo "Error: Invalid run mode '${RUN_MODE}'. Valid options are: ${valid_run_modes[@]}"
	echo "Usage: $0 <sim_type> <sub_backend> <device> <run_mode> <n_points> <optimization_level> <n_processes>"
	exit 1
fi

if ! [[ "${N_POINTS}" =~ ^[0-9]+$ ]] || [[ ${N_POINTS} -le 0 ]]; then
	echo "Error: Number of PES points must be a positive integer."
	echo "Usage: $0 <sim_type> <sub_backend> <device> <run_mode> <n_points> <optimization_level> <n_processes>"
	exit 1
fi

if ! [[ "${N_PROCESSES_PER_NODE}" =~ ^[0-9]+$ ]] || [[ ${N_PROCESSES_PER_NODE} -le 0 ]]; then
	echo "Error: Number of processes must be a positive integer."
	echo "Usage: $0 <sim_type> <sub_backend> <device> <run_mode> <n_points> <optimization_level> <n_processes>"
	exit 1
fi

if [[ ! " ${valid_basis_sets[@]} " =~ " ${BASIS_SET} " ]]; then
	echo "Error: Invalid basis set '${BASIS_SET}'. Valid options are: ${valid_basis_sets[@]}"
	echo "Usage: $0 <sim_type> <sub_backend> <device> <run_mode> <n_points> <optimization_level> <n_processes> <basis_set>"
	exit 1
fi

QFW_ROOT=/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw
RESULT_DIR=/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/results_analysis/raw_data
mkdir -p "${RESULT_DIR}/sqd_data"

echo "###########################"
echo "N_NODES: $N_NODES"
echo "N_PROCESSES_PER_NODE: $N_PROCESSES_PER_NODE"
echo "###########################"

echo "SQD H2 PES Run"
echo "SIM_QRC: $SIM_QRC"
echo "SUB_BACKEND: $SUB_BACKEND"
echo "DEVICE: $DEVICE"
echo "RUN_MODE: $RUN_MODE"
echo "N_POINTS: $N_POINTS"
echo "OPT_LEVEL: $OPT_LEVEL"
echo "N_PROCESSES_PER_NODE: $N_PROCESSES_PER_NODE"
echo "BASIS_SET: $BASIS_SET"

# Submit SLURM job
sbatch <<EOT
#!/bin/bash

#SBATCH --output=${RESULT_DIR}/sqd_data/_${SIM_QRC}_${SUB_BACKEND}_${DEVICE}_${RUN_MODE}_${N_NODES}_${N_PROCESSES_PER_NODE}_${BASIS_SET}_pts${N_POINTS}_opt${OPT_LEVEL}_%j.out

# job component 1
#SBATCH -A GEN006
#SBATCH -N 1
#SBATCH -t 0:15:00
#SBATCH -J sqd_h2
#SBATCH -p batch

#SBATCH hetjob

# job component 2
#SBATCH -A GEN006
#SBATCH -N ${N_NODES}
#SBATCH -t 0:15:00
#SBATCH -J sqd_h2
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
fi

# Force the circuit's local process count for this run (see
# services/util/qpm/util_circuit.py:setup_circuit_run_details). Only
# affects backends that launch a real local mpirun (qiskitaer/nwqsim/
# tnqvm/qtensor); ionq/ibmq never read it.
export QFW_FORCE_NP=${N_PROCESSES_PER_NODE}

qfw_setup.sh

echo "Running SQD H2 PES scan with $SIM_QRC $SUB_BACKEND on $DEVICE using $RUN_MODE mode on $N_NODES nodes with $N_PROCESSES_PER_NODE processes per node, basis set $BASIS_SET, for $N_POINTS bond-distance points."

qfw_srun.sh "\$QFW_PATH/../applications/sqd_related/code/H2-sqd-qfw.py" $SIM_QRC $SUB_BACKEND $DEVICE $RUN_MODE $N_POINTS $OPT_LEVEL $BASIS_SET

qfw_teardown.sh

echo "# RC=\$?"
echo "# END-TIME: \$(date)"

unset QFW_FORCE_NP
qfw_deactivate
EOT
