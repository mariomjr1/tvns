#!/bin/bash

# ============================================================================
# step09_p_value.sh   (Step 09)
# Created by Mario Murakami
#
# Threshold a second-level group contrast (default Cases > Controls) at
# p < 0.05 and write a binary significance map + thresholded t-map.
#
# Input is a step08b group analysis folder (SPM.mat + spmT_000*.nii), e.g.
#   <group out>/BlockStim   or   <group out>/Combined_Block_Continuous
#
# Usage:
#   bash step09_p_value.sh <analysis_dir> <output_dir> \
#        [spm_dir] [matlab_exe] [matlab_code_dir] [p] [extent] [contrast_idx] [tail] [env_script]
#
# Required:
#   analysis_dir  step08b group folder (SPM.mat + spmT)
#   output_dir    where thresholded maps are written
#
# Optional positional arguments:
#   spm_dir          SPM12 path     default: /autofs/.../Packages/matlab/spm12
#   matlab_exe       MATLAB         default: matlab
#   matlab_code_dir  .m folder      default: <script_dir>/utility/matlab_code
#   p                p-threshold    default: 0.05
#   extent           min cluster (voxels)  default: 0
#   contrast_idx     spmT index     default: 1   (Cases>Controls)
#   tail             pos|neg|two    default: pos
#   env_script       env to source  default: <script_dir>/utility/fmriprep_env.sh
#   correction       none|FWE|FDR   default: none  (optional multiple-comparison
#                    correction; 'none' = uncorrected, fine for a pilot study)
# ============================================================================

set -euo pipefail

if [ $# -lt 2 ]; then
    echo "Usage: $0 <analysis_dir> <output_dir> [spm_dir] [matlab_exe]"
    echo "          [matlab_code_dir] [p] [extent] [contrast_idx] [tail] [env_script]"
    exit 1
fi

ANALYSIS_DIR="$1"
OUTPUT_DIR="$2"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

SPM_DIR="${3:-/autofs/cluster/vagabond/USERS/MARIO/Packages/matlab/spm12}"
MATLAB_EXE="${4:-matlab}"
MATLAB_CODE_DIR="${5:-${SCRIPT_DIR}/utility/matlab_code}"
P="${6:-0.05}"
EXTENT="${7:-0}"
CIDX="${8:-1}"
TAIL="${9:-pos}"
ENV_SCRIPT="${10:-${SCRIPT_DIR}/utility/fmriprep_env.sh}"
CORRECTION="${11:-none}"   # none | FWE | FDR  (optional)
BRAINSTEM_MASK="${12:-}"   # optional explicit brainstem mask (Task 05 C3)

echo "============================================"
echo " STEP 09 — Threshold group map (p<${P})"
echo " Analysis dir: ${ANALYSIS_DIR}"
echo " Output:       ${OUTPUT_DIR}"
echo " Contrast idx: ${CIDX}  Tail: ${TAIL}  Extent: ${EXTENT}  Correction: ${CORRECTION}"
echo " Brainstem mask: ${BRAINSTEM_MASK:-(none)}"
echo " Date:         $(date)"
echo "============================================"
echo ""

if [ ! -f "${ANALYSIS_DIR}/SPM.mat" ]; then
    echo "ERROR: SPM.mat not found in ${ANALYSIS_DIR}"
    exit 1
fi
matlab_fn="${MATLAB_CODE_DIR}/threshold_group_map.m"
if [ ! -f "${matlab_fn}" ]; then
    echo "ERROR: threshold_group_map.m not found: ${matlab_fn}"
    exit 1
fi

# Source env (set +eu so FreeSurfer/FSL env files don't abort)
if [ -n "${ENV_SCRIPT}" ] && [ "${ENV_SCRIPT}" != "none" ] && [ -f "${ENV_SCRIPT}" ]; then
    set +eu; source "${ENV_SCRIPT}"; set -eu
fi

mkdir -p "${OUTPUT_DIR}"

matlab_cmd="addpath('${MATLAB_CODE_DIR}'); \
threshold_group_map('${ANALYSIS_DIR}', '${OUTPUT_DIR}', '${SPM_DIR}', \
    'P', ${P}, 'Extent', ${EXTENT}, 'ContrastIndex', ${CIDX}, 'Tail', '${TAIL}', \
    'Correction', '${CORRECTION}', 'BrainstemMask', '${BRAINSTEM_MASK}');"

echo "Running MATLAB threshold..."
"${MATLAB_EXE}" -nodisplay -nosplash -batch "${matlab_cmd}"
rc=$?

echo ""
if [ ${rc} -eq 0 ]; then
    echo "============================================"
    echo " Threshold complete ✓"
    echo " Output: ${OUTPUT_DIR}"
    echo "   *_p${P}_mask.nii   (binary significance map)"
    echo "   *_p${P}_tmap.nii   (thresholded t-values)"
    echo ""
    echo " Next: step10_ROI.sh — extract values / build spheres at a peak voxel"
    echo "============================================"
else
    echo "ERROR: MATLAB exited with code ${rc}"
    exit ${rc}
fi
