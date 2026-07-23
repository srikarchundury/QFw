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

build_python=0
build_tnqvm=0
build_nwqsim=0
build_defw=0

build_qrmi=0
build_qdmi=0

usage() {
    cat <<'EOF'
Usage: ./qfw_build.sh [--python] [--tnqvm] [--nwqsim] [--defw] [--qrmi] [--qdmi] [--qpu-libs]

With no flags, qfw_build.sh installs Python requirements and builds all
core dependency targets in the default order. QRMI and QDMI-on-IQM are
optional development targets and are built only when requested.
EOF
}

qfw_build_jobs() {
    if [[ -n "${QFW_MASTER_SETUP_BUILD_JOBS:-}" ]]; then
        echo "${QFW_MASTER_SETUP_BUILD_JOBS}"
    elif command -v nproc >/dev/null 2>&1; then
        nproc
    else
        echo 4
    fi
}

qfw_clone_or_update() {
    local repo="$1"
    local ref="$2"
    local dst="$3"

    mkdir -p "$(dirname "${dst}")"
    if [[ -d "${dst}/.git" ]]; then
        git -C "${dst}" fetch --tags origin
    else
        git clone "${repo}" "${dst}"
    fi
    git -C "${dst}" checkout "${ref}"
    if git -C "${dst}" rev-parse --abbrev-ref --symbolic-full-name "@{u}" >/dev/null 2>&1; then
        git -C "${dst}" pull --ff-only
    fi
}

qfw_build_qrmi() {
    qfw_clone_or_update "${QFW_QRMI_REPO}" "${QFW_QRMI_REF}" "${QFW_QRMI_SRC_DIR}"
    cd "${QFW_QRMI_SRC_DIR}"
    cargo build --locked --release --lib
    mkdir -p "${QFW_QRMI_PREFIX}/lib" "${QFW_QRMI_PREFIX}/include"
    cp target/release/libqrmi.so "${QFW_QRMI_PREFIX}/lib/"
    if [[ -f target/release/libqrmi.a ]]; then
        cp target/release/libqrmi.a "${QFW_QRMI_PREFIX}/lib/"
    fi
    cp qrmi.h "${QFW_QRMI_PREFIX}/include/"
    export QRMI_PREFIX="${QFW_QRMI_PREFIX}"
    export LD_LIBRARY_PATH="${QRMI_PREFIX}/lib:${LD_LIBRARY_PATH:-}"
    if [[ -f "${QFW_QRMI_SRC_DIR}/pyproject.toml" || -f "${QFW_QRMI_SRC_DIR}/setup.py" ]]; then
        if ! python -m pip install "${QFW_QRMI_SRC_DIR}"; then
            python -m pip install "${QFW_QRMI_PYTHON_PACKAGE}"
        fi
    else
        python -m pip install "${QFW_QRMI_PYTHON_PACKAGE}"
    fi
}

qfw_build_qdmi() {
    local qfw_jobs
    qfw_jobs="$(qfw_build_jobs)"
    qfw_clone_or_update "${QFW_MQT_CORE_REPO}" "${QFW_MQT_CORE_REF}" "${QFW_MQT_CORE_SRC_DIR}"
    if [[ -n "${QFW_MQT_CORE_PRETEND_VERSION:-}" ]]; then
        CMAKE_BUILD_PARALLEL_LEVEL="${qfw_jobs}" \
            SETUPTOOLS_SCM_PRETEND_VERSION_FOR_MQT_CORE="${QFW_MQT_CORE_PRETEND_VERSION}" \
            python -m pip install "${QFW_MQT_CORE_SRC_DIR}"
    else
        CMAKE_BUILD_PARALLEL_LEVEL="${qfw_jobs}" \
            python -m pip install "${QFW_MQT_CORE_SRC_DIR}"
    fi
    qfw_clone_or_update "${QFW_IQM_QDMI_REPO}" "${QFW_IQM_QDMI_REF}" "${QFW_IQM_QDMI_SRC_DIR}"
    CMAKE_BUILD_PARALLEL_LEVEL="${qfw_jobs}" \
        python -m pip install "${QFW_IQM_QDMI_SRC_DIR}[qiskit]"
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --python)
            build_python=1
            ;;
        --tnqvm)
            build_tnqvm=1
            ;;
        --nwqsim)
            build_nwqsim=1
            ;;
        --defw)
            build_defw=1
            ;;
        --qrmi)
            build_qrmi=1
            ;;
        --qdmi)
            build_qdmi=1
            ;;
        --qpu-libs)
            build_qrmi=1
            build_qdmi=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 1
            ;;
    esac
    shift
done

if [[ ${build_python} -eq 0 && ${build_tnqvm} -eq 0 && ${build_nwqsim} -eq 0 && ${build_defw} -eq 0 && ${build_qrmi} -eq 0 && ${build_qdmi} -eq 0 ]]; then
    build_python=1
    build_tnqvm=1
    build_nwqsim=1
    build_defw=1
fi

if [[ ${build_python} -eq 1 ]]; then
    qhw_data_dir="/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw/external/qhw-data"
    qhw_iqm_dir="/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw/external/qhw-iqm"
    for qfw_python_submodule in "${qhw_data_dir}" "${qhw_iqm_dir}"; do
        if [[ ! -f "${qfw_python_submodule}/pyproject.toml" ]]; then
            echo "Missing Python submodule: ${qfw_python_submodule}" >&2
            echo "Run: git submodule update --init --recursive" >&2
            exit 1
        fi
    done
    cd "${qhw_data_dir}"
    python -m pip install -e .
    cd "${qhw_iqm_dir}"
    python -m pip install -e .
    cd "/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw/backends"
    pip3 install -e .
    cd "/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw/DEFw"
    python -m pip install -r requirements.txt
    cd "/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw/setup"
    python -m pip install -r requirements.txt
fi

if [[ ${build_qrmi} -eq 1 ]]; then
    qfw_build_qrmi
fi

if [[ ${build_qdmi} -eq 1 ]]; then
    qfw_build_qdmi
fi

if [[ ${build_tnqvm} -eq 1 ]]; then
    "/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw/patches/tnqvm/build.sh"
fi

if [[ ${build_nwqsim} -eq 1 ]]; then
    "/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw/patches/nwqsim/build.sh"
fi

if [[ ${build_defw} -eq 1 ]]; then
    "/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw/DEFw/build.sh"
fi

cd "/lustre/orion/gen006/proj-shared/qhpc/srikar/qfw_related/QFw"
