#!/bin/bash

###########################
N_NODES=1
N_PROCESSES_PER_NODE=4
###########################

N_QUBITS=$1
SIM_QRC=$2
SUB_BACKEND=$3
DEVICE=$4
RUN_MODE=$5
N_ITRS=$6

# if number of arguments is not 6, print usage and exit
if [ "$#" -ne 6 ]; then
	echo "Usage: $0 <n_qubits> <sim_type> <sub_backend> <device> <run_mode> <n_iterations>"
	echo "Example: $0 4 nwqsim OpenMP CPU sync 10"
	exit 1
fi

valid_sim_types=("nwqsim" "qiskitaer" "tnqvm" "qtensor" "local" "ionq")
declare -A valid_sub_backends
valid_sub_backends["qiskitaer"]="automatic statevector matrix_product_state tensor_network"
valid_sub_backends["nwqsim"]="AMDGPU AMDGPU_MPI OpenMP MPI"
valid_sub_backends["qtensor"]="cupy numpy torch"
valid_sub_backends["tnqvm"]="exatn-mps exatn-ttn exatn-peps"
valid_sub_backends["ionq"]="simulator"
valid_devices=("CPU" "GPU")
valid_run_modes=("sync" "async")
if [[ ! " ${valid_sim_types[@]} " =~ " ${SIM_QRC} " ]]; then
	echo "Error: Invalid simulator type '${SIM_QRC}'. Valid options are: ${valid_sim_types[@]}"
	echo "Usage: $0 <benchmark> <n_qubits> <sim_type> <sub_backend> <device> <run_mode> <n_iterations>"
	exit 1
fi
if [[ -n "${valid_sub_backends[$SIM_QRC]}" ]]; then
	if [[ ! " ${valid_sub_backends[$SIM_QRC]} " =~ " ${SUB_BACKEND} " ]]; then
		echo "Error: Invalid sub-backend '${SUB_BACKEND}' for simulator type '${SIM_QRC}'. Valid options are: ${valid_sub_backends[$SIM_QRC]}"
		echo "Usage: $0 <benchmark> <n_qubits> <sim_type> <sub_backend> <device> <run_mode> <n_iterations>"
		exit 1
	fi
fi
if [[ ! " ${valid_devices[@]} " =~ " ${DEVICE} " ]]; then
	echo "Error: Invalid device type '${DEVICE}'. Valid options are: ${valid_devices[@]}"
	echo "Usage: $0 <benchmark> <n_qubits> <sim_type> <sub_backend> <device> <run_mode> <n_iterations>"
	exit 1
fi
if [[ ${N_QUBITS} -le 0 ]]; then
	echo "Error: Number of qubits must be a positive integer."
	echo "Usage: $0 <benchmark> <n_qubits> <sim_type> <sub_backend> <device> <run_mode> <n_iterations>"
	exit 1
fi
if [[ ! " ${valid_run_modes[@]} " =~ " ${RUN_MODE} " ]]; then
	echo "Error: Invalid run mode '${RUN_MODE}'. Valid options are: ${valid_run_modes[@]}"
	echo "Usage: $0 <benchmark> <n_qubits> <sim_type> <sub_backend> <device> <run_mode> <n_iterations>"
	exit 1
fi
if [[ ${N_ITRS} -le 0 ]]; then
	echo "Error: Number of iterations must be a positive integer."
	echo "Usage: $0 <benchmark> <n_qubits> <sim_type> <sub_backend> <device> <run_mode> <n_iterations>"
	exit 1
fi

QFW_ROOT=/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw
RESULT_DIR=/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/results_analysis/raw_data_frontier_extended
mkdir -p "${RESULT_DIR}/tfim_data"

echo "###########################"
echo "N_NODES: $N_NODES"
echo "N_PROCESSES_PER_NODE: $N_PROCESSES_PER_NODE"
echo "###########################"

echo "TFIM Run"
echo "N_QUBITS: $N_QUBITS"
echo "SIM_QRC: $SIM_QRC"
echo "SUB_BACKEND: $SUB_BACKEND"
echo "DEVICE: $DEVICE"
echo "RUN_MODE: $RUN_MODE"
echo "N_ITRS: $N_ITRS"

# Submit SLURM job
sbatch <<EOT
#!/bin/bash

#SBATCH --output=${RESULT_DIR}/tfim_data/_${N_QUBITS}_${SIM_QRC}_${SUB_BACKEND}_${DEVICE}_${RUN_MODE}_${N_NODES}_${N_PROCESSES_PER_NODE}_${N_ITRS}_%j.out


# job component 1
#SBATCH -N 1
#SBATCH -J tfim_${N_QUBITS}
#SBATCH -A GEN006
#SBATCH -p extended
#SBATCH -t 24:00:00

#SBATCH hetjob

# job component 2
#SBATCH -N ${N_NODES}
#SBATCH -J tfim_${N_QUBITS}
#SBATCH -A GEN006
#SBATCH -p extended
#SBATCH -t 24:00:00

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

qfw_setup.sh

echo "Running TFIM-$N_QUBITS with $SIM_QRC $SUB_BACKEND on $DEVICE using $RUN_MODE mode on $N_NODES nodes with $N_PROCESSES_PER_NODE processes per node, for $N_ITRS iterations."

qfw_srun.sh "\$QFW_PATH/../applications/TFIM/qfw_qiskit_tfim.py" $N_QUBITS $SIM_QRC $SUB_BACKEND $DEVICE $RUN_MODE $N_ITRS

qfw_teardown.sh

echo "# RC=\$?"
echo "# END-TIME: \$(date)"

qfw_deactivate
EOT
