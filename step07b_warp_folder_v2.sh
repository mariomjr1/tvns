#!/bin/bash

# ============================================================================
# step07b_warp_folder_v2.sh   (Step 07 — optional, single-folder warp)
# Created by Mario Murakami
#
# Take ONE already-done first-level folder (con_*.nii in native T1w space) plus
# the subject T1, segment the T1, and warp the contrasts to MNI (wcon_*.nii).
# No GLM is re-run. For warping the whole step07 <subj>/<task> tree instead,
# use step07_firstlevel_mni_v2.sh with warp_only=1.
#
# Usage:
#   bash step07b_warp_folder_v2.sh <con_dir> <t1_file> <output_dir> \
#        [spm_dir] [matlab_exe] [matlab_code_dir] [con_pattern] [env_script]
#
# Required:
#   con_dir      folder with the first-level contrast images (con_*.nii)
#   t1_file      subject T1 in the SAME native space as the contrasts
#                (.nii or .nii.gz)
#   output_dir   where w*.nii are written ('' / same as con_dir = in place)
#
# Optional positional arguments:
#   spm_dir          SPM12 path     default: /autofs/.../Packages/matlab/spm12
#   matlab_exe       MATLAB         default: matlab
#   matlab_code_dir  .m folder      default: <script_dir>/utility/matlab_code
#   con_pattern      contrast glob  default: con_*.nii
#   env_script       env to source  default: <script_dir>/utility/fmriprep_env.sh
# ============================================================================

set -euo pipefail

if [ $# -lt 3 ]; then
    echo "Usage: $0 <con_dir> <t1_file> <output_dir> [spm_dir] [matlab_exe]"
    echo "          [matlab_code_dir] [con_pattern] [env_script]"
    exit 1
fi

CON_DIR="$1"
T1_FILE="$2"
OUTPUT_DIR="$3"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

SPM_DIR="${4:-/autofs/cluster/vagabond/USERS/MARIO/Packages/matlab/spm12}"
MATLAB_EXE="${5:-matlab}"
MATLAB_CODE_DIR="${6:-${SCRIPT_DIR}/utility/matlab_code}"
CON_PATTERN="${7:-con_*.nii}"
ENV_SCRIPT="${8:-${SCRIPT_DIR}/utility/fmriprep_env.sh}"

echo "============================================"
echo " STEP 07b — Warp a single folder to MNI"
echo " con_dir:    ${CON_DIR}"
echo " T1:         ${T1_FILE}"
echo " Output:     ${OUTPUT_DIR}"
echo " Pattern:    ${CON_PATTERN}"
echo " Date:       $(date)"
echo "============================================"
echo ""

if [ ! -d "${CON_DIR}" ]; then echo "ERROR: con_dir not found: ${CON_DIR}"; exit 1; fi
if [ ! -f "${T1_FILE}" ]; then echo "ERROR: T1 not found: ${T1_FILE}"; exit 1; fi
matlab_fn="${MATLAB_CODE_DIR}/warp_firstlevel_to_mni.m"
if [ ! -f "${matlab_fn}" ]; then
    echo "ERROR: warp_firstlevel_to_mni.m not found: ${matlab_fn}"; exit 1
fi

# Source env (set +eu so FreeSurfer/FSL env files don't abort)
if [ -n "${ENV_SCRIPT}" ] && [ "${ENV_SCRIPT}" != "none" ] && [ -f "${ENV_SCRIPT}" ]; then
    set +eu; source "${ENV_SCRIPT}"; set -eu
fi

mkdir -p "${OUTPUT_DIR}"

matlab_cmd="addpath('${MATLAB_CODE_DIR}'); \
warp_firstlevel_to_mni('${CON_DIR}', '${T1_FILE}', '${OUTPUT_DIR}', '${SPM_DIR}', \
    'ConPattern', '${CON_PATTERN}');"

echo "Running MATLAB warp..."
"${MATLAB_EXE}" -nodisplay -nosplash -batch "${matlab_cmd}"
rc=$?

echo ""
if [ ${rc} -eq 0 ]; then
    echo "============================================"
    echo " Warp complete ✓  ->  ${OUTPUT_DIR}/w<con>.nii"
    echo "============================================"
else
    echo "ERROR: MATLAB exited with code ${rc}"
    exit ${rc}
fi
