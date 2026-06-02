#!/bin/bash

# ============================================================================
# step08_secondlevel_v2.sh
# Created by Mario Murakami
#
# Second-level (group) one-sample t-tests on first-level contrast images.
# Replaces the old step24 (which referenced a missing MATLAB script).
#
# You manually point at the three task folders. Each is searched RECURSIVELY
# for the contrast image (default con_0001.nii — use wcon_0001.nii for MNI).
# One image found per subject = one row in the group test.
#
# Runs four analyses:
#   1. BlockStim         one-sample t-test
#   2. ContinuousStim    one-sample t-test
#   3. rest              one-sample t-test
#   4. Combined          BlockStim + ContinuousStim pooled
#
# Output is written to <output_dir>/<analysis>/ (created if absent).
#
# Usage:
#   bash step08_secondlevel_v2.sh <block_dir> <continuous_dir> <rest_dir> \
#        <output_dir> [spm_dir] [matlab_exe] [matlab_code_dir] [con_name] [do_combined]
#
# Required:
#   block_dir        folder with BlockStim first-level outputs
#   continuous_dir   folder with ContinuousStim first-level outputs
#   rest_dir         folder with rest first-level outputs
#                    (pass "" to skip any one of these three)
#   output_dir       group results root (created if absent)
#
# Optional positional arguments:
#   spm_dir          SPM12 path       default: /autofs/.../Packages/matlab/spm12
#   matlab_exe       MATLAB           default: matlab
#   matlab_code_dir  .m folder        default: <script_dir>/utility/matlab_code
#   con_name         contrast file    default: con_0001.nii  (use wcon_0001.nii for MNI)
#   do_combined      1 or 0           default: 1
# ============================================================================

set -euo pipefail

if [ $# -lt 4 ]; then
    echo "Usage: $0 <block_dir> <continuous_dir> <rest_dir> <output_dir> \\"
    echo "          [spm_dir] [matlab_exe] [matlab_code_dir] [con_name] [do_combined]"
    echo ""
    echo "Pass \"\" to skip a task folder. Example:"
    echo "  $0 /path/BlockStim /path/ContinuousStim /path/rest /path/group_out"
    exit 1
fi

BLOCK_DIR="$1"
CONTINUOUS_DIR="$2"
REST_DIR="$3"
OUTPUT_DIR="$4"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

SPM_DIR="${5:-/autofs/cluster/vagabond/USERS/MARIO/Packages/matlab/spm12}"
MATLAB_EXE="${6:-matlab}"
MATLAB_CODE_DIR="${7:-${SCRIPT_DIR}/utility/matlab_code}"
CON_NAME="${8:-con_0001.nii}"
DO_COMBINED="${9:-1}"

echo "============================================"
echo " STEP 08 — Second-level (group) analysis"
echo " BlockStim dir:      ${BLOCK_DIR:-(skip)}"
echo " ContinuousStim dir: ${CONTINUOUS_DIR:-(skip)}"
echo " rest dir:           ${REST_DIR:-(skip)}"
echo " Output:             ${OUTPUT_DIR}"
echo " Contrast image:     ${CON_NAME}"
echo " Combined:           ${DO_COMBINED}"
echo " Date:               $(date)"
echo "============================================"
echo ""

# ── Validate ──────────────────────────────────────────────────────────────────
matlab_2nd="${MATLAB_CODE_DIR}/glm_spm_secondlevel_v2.m"
if [ ! -f "${matlab_2nd}" ]; then
    echo "ERROR: glm_spm_secondlevel_v2.m not found: ${matlab_2nd}"
    exit 1
fi

[ -f "${SCRIPT_DIR}/utility/fmriprep_env.sh" ] && source "${SCRIPT_DIR}/utility/fmriprep_env.sh"

# mkdir output if it doesn't exist
mkdir -p "${OUTPUT_DIR}"

# Convert do_combined to MATLAB logical
if [ "${DO_COMBINED}" = "1" ]; then COMB_ML="true"; else COMB_ML="false"; fi

# ── Run MATLAB ────────────────────────────────────────────────────────────────
matlab_cmd="set(0,'DefaultFigureVisible','off'); \
addpath('${MATLAB_CODE_DIR}'); \
glm_spm_secondlevel_v2( \
    '${BLOCK_DIR}', \
    '${CONTINUOUS_DIR}', \
    '${REST_DIR}', \
    '${OUTPUT_DIR}', \
    '${SPM_DIR}', \
    'ConName', '${CON_NAME}', \
    'DoCombined', ${COMB_ML} );"

echo "Running MATLAB second-level..."
"${MATLAB_EXE}" -nodisplay -nosplash -batch "${matlab_cmd}"
rc=$?

echo ""
if [ ${rc} -eq 0 ]; then
    echo "============================================"
    echo " Second-level complete ✓"
    echo " Output: ${OUTPUT_DIR}"
    echo ""
    echo "   BlockStim/SPM.mat + spmT_000*.nii"
    echo "   ContinuousStim/SPM.mat + spmT_000*.nii"
    echo "   rest/SPM.mat + spmT_000*.nii"
    [ "${DO_COMBINED}" = "1" ] && echo "   Combined_Block_Continuous/SPM.mat + spmT_000*.nii"
    echo "============================================"
else
    echo "ERROR: MATLAB exited with code ${rc}"
    exit ${rc}
fi
