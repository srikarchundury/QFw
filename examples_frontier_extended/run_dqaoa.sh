#!/bin/bash

###########################
APP_N_NODES=1
QFW_N_NODES=1
N_PROCESSES_PER_NODE=56
###########################

# ------------------ Args ------------------
QUBO_SIZE=$1
SUBQUBO_SIZE=$2
N_SUBQUBOS=$3
SIM_QRC=$4
SUB_BACKEND=$5
DEVICE=$6
RUN_MODE=$7
N_ITRS=$8
# ------------------ Validation ------------------
valid_qubo_sizes=("4" "8" "10" "12" "16" "20" "22" "24" "26" "30" "40" "50" "60" "70" "80" "90" "100" "150" "300" "500" "1000")
valid_sim_types=("nwqsim" "qiskitaer" "tnqvm" "qtensor" "local")
declare -A valid_sub_backends
valid_sub_backends["qiskitaer"]="automatic statevector matrix_product_state tensor_network"
valid_sub_backends["nwqsim"]="AMDGPU AMDGPU_MPI OpenMP MPI"
valid_sub_backends["qtensor"]="cupy numpy torch"
valid_sub_backends["tnqvm"]="exatn-mps exatn-ttn exatn-peps"
valid_sub_backends["local"]="automatic statevector matrix_product_state tensor_network"
valid_devices=("CPU" "GPU")
valid_run_modes=("sync" "async")

if [[ ! " ${valid_qubo_sizes[@]} " =~ " ${QUBO_SIZE} " ]]; then
	echo "Error: Invalid QUBO size '${QUBO_SIZE}'. Valid options: ${valid_qubo_sizes[@]}"
	exit 1
fi
if [[ ${SUBQUBO_SIZE} -le 0 || ${N_SUBQUBOS} -le 0 ]]; then
	echo "Error: subQUBO size and number of subQUBOs must be positive integers."
	exit 1
fi
if [[ ! " ${valid_sim_types[@]} " =~ " ${SIM_QRC} " ]]; then
	echo "Error: Invalid simulator type '${SIM_QRC}'. Valid options: ${valid_sim_types[@]}"
	exit 1
fi
if [[ -n "${valid_sub_backends[$SIM_QRC]}" ]]; then
	if [[ ! " ${valid_sub_backends[$SIM_QRC]} " =~ " ${SUB_BACKEND} " ]]; then
		echo "Error: Invalid sub-backend '${SUB_BACKEND}' for simulator type '${SIM_QRC}'. Valid: ${valid_sub_backends[$SIM_QRC]}"
		exit 1
	fi
fi
if [[ ! " ${valid_devices[@]} " =~ " ${DEVICE} " ]]; then
	echo "Error: Invalid device '${DEVICE}'. Valid options: ${valid_devices[@]}"
	exit 1
fi
if [[ ! " ${valid_run_modes[@]} " =~ " ${RUN_MODE} " ]]; then
	echo "Error: Invalid run mode '${RUN_MODE}'. Valid options: ${valid_run_modes[@]}"
	exit 1
fi
if [[ ${N_ITRS} -le 0 ]]; then
	echo "Error: Iterations must be a positive integer."
	exit 1
fi

# ------------------ Paths ------------------
QFW_ROOT=/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw
RESULT_DIR=/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/results_analysis/raw_data_frontier_extended
mkdir -p "${RESULT_DIR}/dqaoa_data"

# ------------------ Info Print ------------------
echo "###########################"
echo "APP_N_NODES: $APP_N_NODES"
echo "QFW_N_NODES: $QFW_N_NODES"
echo "N_PROCESSES_PER_NODE: $N_PROCESSES_PER_NODE"
echo "###########################"
echo "QUBO_SIZE: $QUBO_SIZE"
echo "SUBQUBO_SIZE: $SUBQUBO_SIZE"
echo "N_SUBQUBOS: $N_SUBQUBOS"
echo "SIM_QRC: $SIM_QRC"
echo "SUB_BACKEND: $SUB_BACKEND"
echo "DEVICE: $DEVICE"
echo "RUN_MODE: $RUN_MODE"
echo "N_ITRS: $N_ITRS"

# ------------------ SLURM Submission ------------------
sbatch <<EOT
#!/bin/bash

#SBATCH --output=${RESULT_DIR}/dqaoa_data/_${QUBO_SIZE}__subqsize_${SUBQUBO_SIZE}__nsubq_${N_SUBQUBOS}__backend_${SIM_QRC}__subbackend_${SUB_BACKEND}__device_${DEVICE}__mode_${RUN_MODE}__appn_${APP_N_NODES}__qfwn_${QFW_N_NODES}__ppn_${N_PROCESSES_PER_NODE}__itrs_${N_ITRS}__%j.out

#SBATCH -A GEN006
#SBATCH -N ${APP_N_NODES}
#SBATCH -t 24:00:00
#SBATCH -J dqaoa_${QUBO_SIZE}
#SBATCH -p batch

#SBATCH hetjob

#SBATCH -A GEN006
#SBATCH -N ${QFW_N_NODES}
#SBATCH -t 24:00:00
#SBATCH -J dqaoa_${QUBO_SIZE}
#SBATCH -p batch

source ${QFW_ROOT}/setup/qfw_activate

uname -a
echo "# START-TIME: \$(date)"
echo "# SLURM_JOBID: \$SLURM_JOBID"
echo "# SLURM_NNODES: \$SLURM_NNODES"
echo "# SLURM_NPROCS: \$SLURM_NPROCS"
echo "# SLURM_JOB_CPUS_PER_NODE: \$SLURM_JOB_CPUS_PER_NODE"
echo "# SLURM_THREADS_PER_CORE: \$SLURM_THREADS_PER_CORE"
echo "#----"

module list
echo "QFW_TMP_PATH: \${QFW_TMP_PATH}"

qfw_setup.sh

echo "Running DQAOA with QUBO size $QUBO_SIZE using $SIM_QRC/$SUB_BACKEND on $DEVICE in $RUN_MODE mode with $N_SUBQUBOS subQUBOs of size $SUBQUBO_SIZE for $N_ITRS iterations."

qfw_srun.sh "\$QFW_PATH/../applications/DQAOA-QFw/dqaoa_qfw.py" \
	$QUBO_SIZE $SUBQUBO_SIZE $N_SUBQUBOS $SIM_QRC $SUB_BACKEND $DEVICE $RUN_MODE $N_ITRS

qfw_teardown.sh

echo "# RC=\$?"
echo "# END-TIME: \$(date)"

qfw_deactivate
EOT
