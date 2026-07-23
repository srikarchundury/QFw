#!/bin/bash

###########################
N_NODES=1
N_PROCESSES_PER_NODE=4
###########################

if [[ "$#" -ne 8 ]]; then
	echo "Usage: $0 <QASM_FILE> <N_QUBITS> <SIM_QRC> <SUB_BACKEND> <DEVICE> <RUN_MODE> <N_ITRS> <OPT_LEVEL>"
	exit 1
fi

QASM_FILE=$1
N_QUBITS=$2
SIM_QRC=$3
SUB_BACKEND=$4
DEVICE=$5
RUN_MODE=$6
N_ITRS=$7
OPT_LEVEL=$8

QASM_PREPEND_PATH="/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/applications/hhl/HHL_QASM/"

# ------------------ Validations ------------------
if [[ ! -f "${QASM_PREPEND_PATH}${QASM_FILE}" ]]; then
	echo "Error: QASM file not found or not readable: ${QASM_FILE}"
	echo "Usage: $0 <QASM_FILE> <N_QUBITS> <SIM_QRC> <SUB_BACKEND> <DEVICE> <RUN_MODE> <N_ITRS>"
	exit 1
fi

if ! [[ "${N_QUBITS}" =~ ^[0-9]+$ ]] || [[ ${N_QUBITS} -le 0 ]]; then
	echo "Error: N_QUBITS must be a positive integer. Got: ${N_QUBITS}"
	echo "Usage: $0 <QASM_FILE> <N_QUBITS> <SIM_QRC> <SUB_BACKEND> <DEVICE> <RUN_MODE> <N_ITRS>"
	exit 1
fi

valid_sim_types=("nwqsim" "qiskitaer" "tnqvm" "qtensor" "ionq" "ibmq")
declare -A valid_sub_backends
valid_sub_backends["qiskitaer"]="automatic statevector matrix_product_state tensor_network"
valid_sub_backends["nwqsim"]="AMDGPU AMDGPU_MPI OpenMP MPI"
valid_sub_backends["qtensor"]="cupy numpy torch"
valid_sub_backends["tnqvm"]="exatn-mps exatn-ttn exatn-peps"
valid_sub_backends["ionq"]="simulator aria-1 aria-2 forte-1 forte-enterprise-1 harmony ideal"
valid_sub_backends["ibmq"]="ibm_miami ibm_torino ibm_boston"
valid_devices=("CPU" "GPU")
# only sync allowed
valid_run_modes=("sync")

if [[ ! " ${valid_sim_types[@]} " =~ " ${SIM_QRC} " ]]; then
	echo "Error: Invalid simulator type '${SIM_QRC}'. Valid options are: ${valid_sim_types[@]}"
	echo "Usage: $0 <QASM_FILE> <N_QUBITS> <SIM_QRC> <SUB_BACKEND> <DEVICE> <RUN_MODE> <N_ITRS>"
	exit 1
fi

if [[ -n "${valid_sub_backends[$SIM_QRC]}" ]]; then
	if [[ ! " ${valid_sub_backends[$SIM_QRC]} " =~ " ${SUB_BACKEND} " ]]; then
		echo "Error: Invalid sub-backend '${SUB_BACKEND}' for simulator type '${SIM_QRC}'. Valid options are: ${valid_sub_backends[$SIM_QRC]}"
		echo "Usage: $0 <QASM_FILE> <N_QUBITS> <SIM_QRC> <SUB_BACKEND> <DEVICE> <RUN_MODE> <N_ITRS>"
		exit 1
	fi
fi

if [[ ! " ${valid_devices[@]} " =~ " ${DEVICE} " ]]; then
	echo "Error: Invalid device type '${DEVICE}'. Valid options are: ${valid_devices[@]}"
	echo "Usage: $0 <QASM_FILE> <N_QUBITS> <SIM_QRC> <SUB_BACKEND> <DEVICE> <RUN_MODE> <N_ITRS>"
	exit 1
fi

if [[ ! " ${valid_run_modes[@]} " =~ " ${RUN_MODE} " ]]; then
	echo "Error: Invalid run mode '${RUN_MODE}'. Valid options are: ${valid_run_modes[@]}"
	echo "Usage: $0 <QASM_FILE> <N_QUBITS> <SIM_QRC> <SUB_BACKEND> <DEVICE> <RUN_MODE> <N_ITRS>"
	exit 1
fi

if ! [[ "${N_ITRS}" =~ ^[0-9]+$ ]] || [[ ${N_ITRS} -le 0 ]]; then
	echo "Error: Number of iterations must be a positive integer."
	echo "Usage: $0 <QASM_FILE> <N_QUBITS> <SIM_QRC> <SUB_BACKEND> <DEVICE> <RUN_MODE> <N_ITRS>"
	exit 1
fi

# ------------------ Paths ------------------
QFW_ROOT=/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw
RESULT_DIR=/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/results_analysis/raw_data
if [[ -n "${BENCHMARK_RUN_ID:-}" ]]; then
	RESULT_DIR="${RESULT_DIR}/${BENCHMARK_RUN_ID}"
fi
mkdir -p "${RESULT_DIR}/hhl_data"

# ------------------ Info Print ------------------
echo "###########################"
echo "N_NODES: $N_NODES"
echo "N_PROCESSES_PER_NODE: $N_PROCESSES_PER_NODE"
echo "###########################"

echo "QASM_FILE: $QASM_FILE"
echo "N_QUBITS: $N_QUBITS"
echo "SIM_QRC: $SIM_QRC"
echo "SUB_BACKEND: $SUB_BACKEND"
echo "DEVICE: $DEVICE"
echo "RUN_MODE: $RUN_MODE"
echo "N_ITRS: $N_ITRS"

# ------------------ SLURM Job Submission ------------------
sbatch <<EOT
#!/bin/bash

#SBATCH --output=${RESULT_DIR}/hhl_data/_${QASM_FILE}_${N_QUBITS}_${SIM_QRC}_${SUB_BACKEND}_${DEVICE}_${RUN_MODE}_${N_NODES}_${N_PROCESSES_PER_NODE}_${N_ITRS}_opt${OPT_LEVEL}_%j.out

# job component 1
#SBATCH -A GEN006
#SBATCH -N 1
#SBATCH -t 2:00:00
#SBATCH -J HHL_${N_QUBITS}
#SBATCH -p batch

#SBATCH hetjob

# job component 2
#SBATCH -A GEN006
#SBATCH -N ${N_NODES}
#SBATCH -t 2:00:00
#SBATCH -J HHL_${N_QUBITS}
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

# Skip cloud API calls for ionq/ibmq — transpile only, return synthetic counts
if [[ "$SIM_QRC" == "ionq" || "$SIM_QRC" == "ibmq" ]]; then
	export QFW_TRANSPILE_ONLY=1
fi

qfw_setup.sh

echo "Running HHL with qasm file $QASM_FILE size $N_QUBITS using $SIM_QRC $SUB_BACKEND on $DEVICE in $RUN_MODE mode on $N_NODES nodes with $N_PROCESSES_PER_NODE processes per node, for $N_ITRS iterations."

qfw_srun.sh "\$QFW_PATH/../applications/hhl/qfw_hhl.py" $QASM_FILE $N_QUBITS $SIM_QRC $SUB_BACKEND $DEVICE $RUN_MODE $N_ITRS $OPT_LEVEL

qfw_teardown.sh

echo "# RC=\$?"
echo "# END-TIME: \$(date)"

qfw_deactivate
EOT
