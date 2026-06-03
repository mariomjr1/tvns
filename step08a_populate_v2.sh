#!/bin/bash

# ============================================================================
# step08a_populate_v2.sh   (Step 08, Part 1)
# Created by Mario Murakami
#
# Gather first-level contrast images into per-task group folders.
# Step07 writes <firstlevel_root>/<subject>/<task>/<con_name>; this copies each
# into <output_dir>/<task>/<subject>.nii so Part 2 can split by group.
#
# Usage:
#   bash step08a_populate_v2.sh <firstlevel_root> <output_dir> \
#        [spm_dir] [matlab_exe] [matlab_code_dir] [con_name] [env_script]
#
# Required:
#   firstlevel_root  step07 output root (<subject>/<task>/<con_name>)
#   output_dir       per-task group folders are created here
#
# Optional positional arguments:
#   spm_dir          SPM12 path        default: /autofs/.../Packages/matlab/spm12
#   matlab_exe       MATLAB            default: matlab
#   matlab_code_dir  .m folder         default: <script_dir>/utility/matlab_code
#   con_name         contrast file     default: wcon_0001.nii  (MNI-warped)
#   env_script       env to source     default: <script_dir>/utility/fmriprep_env.sh
#                                       ("none" to skip)
# ============================================================================

set -euo pipefail

if [ $# -lt 2 ]; then
    echo "Usage: $0 <firstlevel_root> <output_dir> [spm_dir] [matlab_exe]"
    echo "          [matlab_code_dir] [con_name] [env_script]"
    exit 1
fi

FIRSTLEVEL_ROOT="$1"
OUTPUT_DIR="$2"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

SPM_DIR="${3:-/autofs/cluster/vagabond/USERS/MARIO/Packages/matlab/spm12}"
MATLAB_EXE="${4:-matlab}"
MATLAB_CODE_DIR="${5:-${SCRIPT_DIR}/utility/matlab_code}"
CON_NAME="${6:-wcon_0001.nii}"
ENV_SCRIPT="${7:-${SCRIPT_DIR}/utility/fmriprep_env.sh}"

echo "============================================"
echo " STEP 08a — Populate task folders"
echo " First-level root: ${FIRSTLEVEL_ROOT}"
echo " Output:           ${OUTPUT_DIR}"
echo " Contrast image:   ${CON_NAME}"
echo " Date:             $(date)"
echo "============================================"
echo ""

if [ ! -d "${FIRSTLEVEL_ROOT}" ]; then
    echo "ERROR: First-level root not found: ${FIRSTLEVEL_ROOT}"
    exit 1
fi
matlab_pop="${MATLAB_CODE_DIR}/secondlevel_populate_tasks.m"
if [ ! -f "${matlab_pop}" ]; then
    echo "ERROR: secondlevel_populate_tasks.m not found: ${matlab_pop}"
    exit 1
fi

# Source the environment (set +eu so FreeSurfer/FSL env files don't abort)
if [ -n "${ENV_SCRIPT}" ] && [ "${ENV_SCRIPT}" != "none" ] && [ -f "${ENV_SCRIPT}" ]; then
    set +eu; source "${ENV_SCRIPT}"; set -eu
fi

mkdir -p "${OUTPUT_DIR}"

matlab_cmd="addpath('${MATLAB_CODE_DIR}'); \
secondlevel_populate_tasks('${FIRSTLEVEL_ROOT}', '${OUTPUT_DIR}', \
    'ConName', '${CON_NAME}');"

echo "Running MATLAB populate..."
"${MATLAB_EXE}" -nodisplay -nosplash -batch "${matlab_cmd}"
rc=$?

echo ""
if [ ${rc} -eq 0 ]; then
    echo "============================================"
    echo " Populate complete ✓"
    echo " Per-task folders under: ${OUTPUT_DIR}"
    echo "   <task>/<subject>.nii  +  <task>/_subjects.txt"
    echo ""
    echo " Next: step08b_groups_v2.sh (cases vs controls + contrasts)"
    echo "============================================"
else
    echo "ERROR: MATLAB exited with code ${rc}"
    exit ${rc}
fi
