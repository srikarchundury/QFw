#!/bin/bash

# ========== CONFIGURABLE PARAMETERS ==========
ITRS=5  # Global iteration count

# SIMULATORS=("qiskitaer" "qtensor" "nwqsim" "tnqvm" "ionq")
SIMULATORS=("qiskitaer" "qtensor" "nwqsim" "tnqvm")
declare -A SUB_BACKENDS=(
    ["qiskitaer"]="statevector matrix_product_state automatic"
    ["qtensor"]="numpy"
    ["nwqsim"]="MPI"
    ["tnqvm"]="exatn-mps"
    # ["ionq"]="simulator"  # NOTE: remove or comment after this is run once
)
# =============================================

bench_type=$1
shift

if [ -z "$bench_type" ]; then
    echo "Usage: $0 <benchmark_type> <args...>"
    echo ""
    echo "Supported benchmark types and required arguments:"
    echo "  tfim <num_qubits>"
    echo "      Run TFIM benchmark for a given number of qubits"
    echo ""
    echo "  qaoa <qubo_size>"
    echo "      Run QAOA benchmark for a given QUBO problem size"
    echo ""
    echo "  dqaoa <qubo_size> <sub_qubo_size> <num_sub_qubos>"
    echo "      Run DQAOA benchmark for given sizes and decomposition"
    echo ""
    echo "  supermarq <circuit_name> <num_qubits>"
    echo "      Run SupermarQ benchmark on specified circuit (e.g., ghz, ham)"
    echo ""
    echo "  hhl <qasm_file> <num_qubits>"
    echo "      Run HHL benchmark on given QASM file with specified qubit count"
    echo ""
    echo "NOTE: Iteration count is set globally in the script as ITRS=$ITRS"
    exit 1
fi

case "$bench_type" in
    tfim)
        qubits=$1
        if [ -z "$qubits" ]; then
            echo "Usage: $0 tfim <num_qubits>"
            exit 1
        fi
        for sim in "${SIMULATORS[@]}"; do
            for sub in ${SUB_BACKENDS[$sim]}; do
                ./run_tfim.sh $qubits $sim $sub CPU sync $ITRS
            done
        done
        ;;
    qaoa)
        qubo_size=$1
        if [ -z "$qubo_size" ]; then
            echo "Usage: $0 qaoa <qubo_size>"
            exit 1
        fi
        for sim in "${SIMULATORS[@]}"; do
            for sub in ${SUB_BACKENDS[$sim]}; do
                ./run_qaoa.sh $qubo_size $sim $sub CPU sync $ITRS
            done
        done
        ;;
    dqaoa)
        qubo_size=$1
        sub_qubo_size=$2
        num_sub_qubos=$3
        if [ -z "$qubo_size" ] || [ -z "$sub_qubo_size" ] || [ -z "$num_sub_qubos" ]; then
            echo "Usage: $0 dqaoa <qubo_size> <sub_qubo_size> <num_sub_qubos>"
            exit 1
        fi
        for sim in "${SIMULATORS[@]}"; do
            for sub in ${SUB_BACKENDS[$sim]}; do
                ./run_dqaoa.sh $qubo_size $sub_qubo_size $num_sub_qubos $sim $sub CPU async $ITRS
            done
        done
        ;;
    supermarq)
        circuit=$1
        qubits=$2
        if [ -z "$circuit" ] || [ -z "$qubits" ]; then
            echo "Usage: $0 supermarq <ghz|ham|...> <num_qubits>"
            exit 1
        fi
        for sim in "${SIMULATORS[@]}"; do
            for sub in ${SUB_BACKENDS[$sim]}; do
                ./run_supermarq.sh $circuit $qubits $sim $sub CPU sync $ITRS
            done
        done
        ;;
    hhl)
        qasm_file=$1
        n_qubits=$2
        if [ -z "$qasm_file" ] || [ -z "$n_qubits" ]; then
            echo "Usage: $0 hhl <qasm_file> <num_qubits>"
            exit 1
        fi
        sim="qiskitaer"
        for sub in ${SUB_BACKENDS[$sim]}; do
            ./run_hhl.sh "$qasm_file" $n_qubits $sim $sub CPU sync $ITRS
        done
        ;;
    *)
        echo "Unknown benchmark type: $bench_type"
        exit 1
        ;;
esac

echo "All benchmarks submitted for $bench_type."
