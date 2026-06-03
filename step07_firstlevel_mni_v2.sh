#!/bin/bash

# ============================================================================
# step07_firstlevel_mni_v2.sh
# Created by Mario Murakami
#
# First-level SPM GLM in native T1w space + warp contrasts to MNI.
# Replaces the old step21 (copy masks) + step22 (first level) + step23 (MNI).
#
# KEY CHANGE: masks and BOLDs are LOCATED in place inside derivatives/fmriprep
# (no copy step). Stim onsets and motion regressors come from the step06
# first_level folder.
#
# Pipeline (all inside glm_spm_firstlevel_mni_v2.m):
#   For each subject x task:
#     1. Locate space-T1w BOLD + brain mask in fmriprep func dir
#     2. Smooth BOLD, reslice mask into BOLD grid
#     3. Specify + estimate first-level GLM (Stim condition + motion nuisance)
#     4. Build contrasts (Stim > baseline, Stim < baseline)
#     5. Segment T1 → warp con_*.nii to MNI (wcon_*.nii)   [optional]
#
# Usage:
#   bash step07_firstlevel_mni_v2.sh [options]
#
# Optional positional arguments (in order):
#   subject_list      BIDS subject list file
#                     default: <script_dir>/utility/SubjectListBIDS.txt
#   sourcedata_dir    BIDS sourcedata root
#                     default: /autofs/.../lyme/sourcedata
#   firstlevel_dir    step06 first_level folder (stim_onsets/ + motion_regressors/)
#                     NOTE: per-subject by default; see firstlevel_mode below
#   output_dir        GLM output root
#                     default: <sourcedata>/derivatives/spm/first_level
#   spm_dir           SPM12 path
#                     default: /autofs/.../Packages/matlab/spm12
#   matlab_exe        MATLAB executable          default: matlab
#   matlab_code_dir   folder with the .m file    default: <script_dir>/utility/matlab_code
#   session           BIDS session               default: 01
#   run               BIDS run                   default: 01
#   tr                TR seconds                 default: 1.19
#   smooth_fwhm       smoothing mm (one number) default: 3
#   do_mni            1 = warp to MNI, 0 = skip  default: 1
#   env_script        environment script to source (MATLAB on PATH, etc.)
#                     default: <script_dir>/utility/fmriprep_env.sh
#                     pass "none" to skip sourcing entirely
#   warp_only         1 = only warp existing con_*.nii, skip GLM; 0 = full GLM
#                     default: 0
#   use_sourcedata    1 = look for stim in sourcedata/physio/<subj>/stimtrigger/
#                     0 = use firstlevel_dir/stim_onsets/; default: 0
# ============================================================================

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

SUBJECT_LIST="${1:-${SCRIPT_DIR}/utility/SubjectListBIDS.txt}"
SOURCEDATA="${2:-/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata}"
FIRSTLEVEL_DIR="${3:-${SOURCEDATA}/derivatives/physio/first_level}"
OUTPUT_DIR="${4:-${SOURCEDATA}/derivatives/spm/first_level}"
SPM_DIR="${5:-/autofs/cluster/vagabond/USERS/MARIO/Packages/matlab/spm12}"
MATLAB_EXE="${6:-matlab}"
MATLAB_CODE_DIR="${7:-${SCRIPT_DIR}/utility/matlab_code}"
SESSION="${8:-01}"
RUN="${9:-01}"
TR="${10:-1.19}"
SMOOTH="${11:-3}"
DO_MNI="${12:-1}"
# Environment script to source (gets MATLAB on PATH, etc.).
# Default: utility/fmriprep_env.sh. Pass "none" (or "") to skip sourcing.
ENV_SCRIPT="${13:-${SCRIPT_DIR}/utility/fmriprep_env.sh}"
WARP_ONLY="${14:-0}"
USE_SOURCEDATA="${15:-0}"

FMRIPREP_DIR="${SOURCEDATA}/derivatives/fmriprep"

echo "============================================"
echo " STEP 07 — First-level GLM + MNI warp"
echo " Subject list:  ${SUBJECT_LIST}"
echo " fMRIPrep:      ${FMRIPREP_DIR}"
if [ "${WARP_ONLY}" = "1" ]; then
    echo " MODE:          Warp-only (skip GLM)"
    echo " Output:        ${OUTPUT_DIR}"
else
    echo " First-level:   ${FIRSTLEVEL_DIR}"
    echo " Output:        ${OUTPUT_DIR}"
fi
echo " SPM:           ${SPM_DIR}"
echo " Env script:    ${ENV_SCRIPT}"
echo " TR=${TR}  Smooth=${SMOOTH}mm  Session=ses-${SESSION}  DoMNI=${DO_MNI}  WarpOnly=${WARP_ONLY}  UseSourcedata=${USE_SOURCEDATA}"
echo " Date:          $(date)"
echo "============================================"
echo ""

# ── Validate ──────────────────────────────────────────────────────────────────
if [ ! -f "${SUBJECT_LIST}" ]; then
    echo "ERROR: Subject list not found: ${SUBJECT_LIST}"
    exit 1
fi
if [ ! -d "${FMRIPREP_DIR}" ]; then
    echo "ERROR: fMRIPrep directory not found: ${FMRIPREP_DIR}"
    exit 1
fi
matlab_glm="${MATLAB_CODE_DIR}/glm_spm_firstlevel_mni_v2.m"
if [ ! -f "${matlab_glm}" ]; then
    echo "ERROR: glm_spm_firstlevel_mni_v2.m not found: ${matlab_glm}"
    exit 1
fi

# Source the chosen environment script.
# FreeSurfer/FSL setup scripts reference unset variables (e.g.
# FS_FREESURFERENV_NO_OUTPUT), which trips `set -u`; and they may return
# non-zero, which trips `set -e`. Disable both only around the source.
if [ -n "${ENV_SCRIPT}" ] && [ "${ENV_SCRIPT}" != "none" ]; then
    if [ -f "${ENV_SCRIPT}" ]; then
        echo " Sourcing environment: ${ENV_SCRIPT}"
        set +eu
        # shellcheck disable=SC1090
        source "${ENV_SCRIPT}"
        set -eu
    else
        echo " WARNING: environment script not found: ${ENV_SCRIPT}"
        echo "          continuing without it (make sure 'matlab' is on PATH)."
    fi
fi

mkdir -p "${OUTPUT_DIR}"

# Convert do_mni (1/0) to MATLAB true/false
if [ "${DO_MNI}" = "1" ]; then DOMNI_ML="true"; else DOMNI_ML="false"; fi
if [ "${WARP_ONLY}" = "1" ]; then WARPONLY_ML="true"; else WARPONLY_ML="false"; fi

# Determine SourceData path if USE_SOURCEDATA is enabled
SOURCEDATA_ARG=""
if [ "${USE_SOURCEDATA}" = "1" ]; then
    SOURCEDATA_ARG="'SourceData', '${SOURCEDATA}', "
fi

# ── Run MATLAB ────────────────────────────────────────────────────────────────
matlab_cmd="set(0,'DefaultFigureVisible','off'); \
addpath('${MATLAB_CODE_DIR}'); \
glm_spm_firstlevel_mni_v2( \
    '${SUBJECT_LIST}', \
    '${FMRIPREP_DIR}', \
    '${FIRSTLEVEL_DIR}', \
    '${OUTPUT_DIR}', \
    '${SPM_DIR}', \
    'TR', ${TR}, \
    'Session', '${SESSION}', \
    'Run', '${RUN}', \
    'SmoothFWHM', [${SMOOTH} ${SMOOTH} ${SMOOTH}], \
    'DoMNI', ${DOMNI_ML}, \
    'WarpOnly', ${WARPONLY_ML}, \
    ${SOURCEDATA_ARG} \
    'SmoothPrefix', 's3' );"

echo "Running MATLAB first-level + MNI..."
"${MATLAB_EXE}" -nodisplay -nosplash -batch "${matlab_cmd}"
rc=$?

echo ""
if [ ${rc} -eq 0 ]; then
    echo "============================================"
    echo " First-level + MNI complete ✓"
    echo " Output: ${OUTPUT_DIR}"
    echo ""
    echo "   <subj>/<task>/SPM.mat"
    echo "   <subj>/<task>/con_0001.nii  (Stim > baseline)"
    echo "   <subj>/<task>/con_0002.nii  (Stim < baseline)"
    [ "${DO_MNI}" = "1" ] && echo "   <subj>/<task>/wcon_000*.nii (MNI-warped)"
    echo ""
    echo " Next step: step24 (second-level group analysis)"
    echo "============================================"
else
    echo "ERROR: MATLAB exited with code ${rc}"
    exit ${rc}
fi
