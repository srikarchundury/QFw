#!/usr/bin/env bash
set -euo pipefail

export QFW_MASTER_SETUP_BASE_DIR="/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related"
export QFW_RUNTIME_MODE="${QFW_RUNTIME_MODE:-cluster}"
export QFW_MPI_TRANSPORT_MODE="${QFW_MPI_TRANSPORT_MODE:-ofi}"
export QFW_DEP_BUILD_VERSION="${QFW_DEP_BUILD_VERSION:-ORNL_STACK_with_PEPS}"
export QFW_QPU_LIB_BUILD_DIR="${QFW_QPU_LIB_BUILD_DIR:-/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/build/ORNL_STACK_with_PEPS/qpu-libs}"
export QFW_QRMI_REPO="${QFW_QRMI_REPO:-https://github.com/qiskit-community/qrmi.git}"
export QFW_QRMI_REF="${QFW_QRMI_REF:-0.17.2}"
export QFW_QRMI_PREFIX="${QFW_QRMI_PREFIX:-/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/install/ORNL_STACK_with_PEPS/QRMI}"
export QFW_QRMI_SRC_DIR="${QFW_QRMI_SRC_DIR:-/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/build/ORNL_STACK_with_PEPS/qpu-libs/qrmi}"
export QFW_MQT_CORE_REPO="${QFW_MQT_CORE_REPO:-https://github.com/munich-quantum-toolkit/core.git}"
export QFW_MQT_CORE_REF="${QFW_MQT_CORE_REF:-v3.6.1}"
export QFW_MQT_CORE_SRC_DIR="${QFW_MQT_CORE_SRC_DIR:-/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/build/ORNL_STACK_with_PEPS/qpu-libs/mqt-core}"
export QFW_MQT_CORE_PRETEND_VERSION="${QFW_MQT_CORE_PRETEND_VERSION:-}"
export QFW_IQM_QDMI_REPO="${QFW_IQM_QDMI_REPO:-https://github.com/iqm-finland/QDMI-on-IQM.git}"
export QFW_IQM_QDMI_REF="${QFW_IQM_QDMI_REF:-v1.1.1}"
export QFW_IQM_QDMI_SRC_DIR="${QFW_IQM_QDMI_SRC_DIR:-/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/build/ORNL_STACK_with_PEPS/qpu-libs/QDMI-on-IQM}"
export QFW_QRMI_PYTHON_PACKAGE="${QFW_QRMI_PYTHON_PACKAGE:-qrmi==${QFW_QRMI_REF}}"
export QRMI_PREFIX="${QRMI_PREFIX:-${QFW_QRMI_PREFIX}}"
if [[ -d "${QRMI_PREFIX}/lib" ]]; then
    export LD_LIBRARY_PATH="${QRMI_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
fi

export QFW_VENV_PATH="/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/qfwVirtEnv"
source "/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/qfwVirtEnv/bin/activate"
source "/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw/setup/qfw_lib_path.sh"
export PYTHON_PATH="${PYTHON_PATH:-$(command -v python3)}"

export _QFW_USED_MODULES=1
module use /lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/modules
module load quantum/qsim
ml
export BLASLIB="${BLASLIB:-OPENBLAS}"
export BLAS_LIB_DIR="${BLAS_LIB_DIR:-$(pkg-config --variable=libdir openblas)}"

source "/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw/environment/qfw_set_envvars.sh"
if [[ -n "${QFW_EXTRA_PATHS:-}" ]]; then
    IFS=':' read -r -a _qfw_extra_paths <<< "${QFW_EXTRA_PATHS}"
    for _qfw_path in "${_qfw_extra_paths[@]}"; do
        [[ -n "${_qfw_path}" ]] || continue
        export PATH="${_qfw_path}:${PATH}"
    done
    unset _qfw_path _qfw_extra_paths
fi
if [[ -n "${QFW_EXTRA_LIBPATHS:-}" ]]; then
    IFS=':' read -r -a _qfw_extra_libpaths <<< "${QFW_EXTRA_LIBPATHS}"
    for _qfw_libpath in "${_qfw_extra_libpaths[@]}"; do
        [[ -n "${_qfw_libpath}" ]] || continue
        export LD_LIBRARY_PATH="${_qfw_libpath}:${LD_LIBRARY_PATH:-}"
    done
    unset _qfw_libpath _qfw_extra_libpaths
fi

