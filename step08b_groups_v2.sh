#!/bin/bash

# ============================================================================
# step08b_groups_v2.sh   (Step 08, Part 2)
# Created by Mario Murakami
#
# Two-sample (cases vs controls) second-level analysis on the per-task folders
# produced by step08a_populate_v2.sh.
#
# For each task folder it builds a two-sample t-test (cases vs controls) with
# contrasts: Cases>Controls, Controls>Cases, Cases mean, Controls mean.
# A combined Block+Continuous analysis is also run.
#
# Usage:
#   bash step08b_groups_v2.sh <task_root> <cases_list> <controls_list> <output_dir> \
#        [spm_dir] [matlab_exe] [matlab_code_dir] [do_combined] [env_script]
#
# Required:
#   task_root        per-task folders from Part 1 (<task>/<subject>.nii)
#   cases_list       text file, one BIDS subject per line (cases group)
#   controls_list    text file, one BIDS subject per line (controls group)
#   output_dir       group results root (created if absent)
#
# Optional positional arguments:
#   spm_dir          SPM12 path        default: /autofs/.../Packages/matlab/spm12
#   matlab_exe       MATLAB            default: matlab
#   matlab_code_dir  .m folder         default: <script_dir>/utility/matlab_code
#   do_combined      1 or 0            default: 1
#   env_script       env to source     default: <script_dir>/utility/fmriprep_env.sh
#                                       ("none" to skip)
#   combined_mode    average | pool    default: average
#                    'average' = average each subject's Block+Continuous into ONE image
#                                before the two-sample test (one obs/subject — correct).
#                    'pool'    = legacy: enter both conditions per subject (double-counts).
#   covariates       comma list | none default: none  (OPTIONAL nuisance covariates;
#                    e.g. "age,sex,mean_fd" — built from participants.tsv + fMRIPrep
#                    confounds by utility/build_group_covariates.py)
#   participants_tsv BIDS participants.tsv (age/sex)   default: <sourcedata>/participants.tsv
#   fmriprep_dir     fMRIPrep derivatives (mean_fd)    default: <sourcedata>/derivatives/fmriprep
#   sourcedata       BIDS root (to auto-derive the two above)  default: (none)
#   session          BIDS session for mean_fd          default: 01
# ============================================================================

set -euo pipefail

if [ $# -lt 4 ]; then
    echo "Usage: $0 <task_root> <cases_list> <controls_list> <output_dir> \\"
    echo "          [spm_dir] [matlab_exe] [matlab_code_dir] [do_combined] [env_script]"
    exit 1
fi

TASK_ROOT="$1"
CASES_LIST="$2"
CONTROLS_LIST="$3"
OUTPUT_DIR="$4"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

SPM_DIR="${5:-/autofs/cluster/vagabond/USERS/MARIO/Packages/matlab/spm12}"
MATLAB_EXE="${6:-matlab}"
MATLAB_CODE_DIR="${7:-${SCRIPT_DIR}/utility/matlab_code}"
DO_COMBINED="${8:-1}"
ENV_SCRIPT="${9:-${SCRIPT_DIR}/utility/fmriprep_env.sh}"
COMBINED_MODE="${10:-average}"   # average (default) | pool (legacy)
COVARIATES="${11:-none}"         # e.g. "age,sex,mean_fd" or "none"
SOURCEDATA="${14:-}"             # optional BIDS root to auto-derive the two below
PARTICIPANTS_TSV="${12:-${SOURCEDATA:+${SOURCEDATA}/participants.tsv}}"
FMRIPREP_DIR="${13:-${SOURCEDATA:+${SOURCEDATA}/derivatives/fmriprep}}"
COV_SESSION="${15:-01}"
BRAINSTEM_MASK="${16:-}"          # optional explicit brainstem mask (Task 05 C3)

echo "============================================"
echo " STEP 08b — Group analysis (cases vs controls)"
echo " Task root:     ${TASK_ROOT}"
echo " Cases list:    ${CASES_LIST}"
echo " Controls list: ${CONTROLS_LIST}"
echo " Output:        ${OUTPUT_DIR}"
echo " Combined:      ${DO_COMBINED}  (mode: ${COMBINED_MODE})"
echo " Covariates:    ${COVARIATES}"
echo " Brainstem mask: ${BRAINSTEM_MASK:-(none)}"
echo " Date:          $(date)"
echo "============================================"
echo ""

for f in "${TASK_ROOT}" ; do
    [ -d "$f" ] || { echo "ERROR: task root not found: $f"; exit 1; }
done
for f in "${CASES_LIST}" "${CONTROLS_LIST}"; do
    [ -f "$f" ] || { echo "ERROR: subject list not found: $f"; exit 1; }
done
matlab_grp="${MATLAB_CODE_DIR}/glm_spm_secondlevel_groups.m"
if [ ! -f "${matlab_grp}" ]; then
    echo "ERROR: glm_spm_secondlevel_groups.m not found: ${matlab_grp}"
    exit 1
fi

# Source the environment (set +eu so FreeSurfer/FSL env files don't abort)
if [ -n "${ENV_SCRIPT}" ] && [ "${ENV_SCRIPT}" != "none" ] && [ -f "${ENV_SCRIPT}" ]; then
    set +eu; source "${ENV_SCRIPT}"; set -eu
fi

mkdir -p "${OUTPUT_DIR}"

if [ "${DO_COMBINED}" = "1" ]; then COMB_ML="true"; else COMB_ML="false"; fi

# ── Optional covariates (Task 08) ─────────────────────────────────────────────
# Build a clean group_covariates.tsv from participants.tsv + fMRIPrep confounds,
# and turn the comma list into a MATLAB cellstr {'age','sex',...}.
COV_ML="{}"
COV_FILE=""
if [ "${COVARIATES}" != "none" ] && [ -n "${COVARIATES}" ]; then
    COV_FILE="${OUTPUT_DIR}/group_covariates.tsv"
    builder="${SCRIPT_DIR}/utility/build_group_covariates.py"
    echo "Building covariates (${COVARIATES}) -> ${COV_FILE}"
    python3 "${builder}" \
        --participants "${PARTICIPANTS_TSV:-}" \
        --fmriprep-dir "${FMRIPREP_DIR:-}" \
        --output "${COV_FILE}" \
        --session "${COV_SESSION}" \
        || echo "WARNING: covariate build failed — proceeding without covariates"
    IFS=',' read -ra _cv <<< "${COVARIATES}"
    parts=""
    for c in "${_cv[@]}"; do parts="${parts}'${c}',"; done
    COV_ML="{${parts%,}}"
fi

matlab_cmd="addpath('${MATLAB_CODE_DIR}'); \
glm_spm_secondlevel_groups('${TASK_ROOT}', '${CASES_LIST}', '${CONTROLS_LIST}', \
    '${OUTPUT_DIR}', '${SPM_DIR}', 'DoCombined', ${COMB_ML}, \
    'CombinedMode', '${COMBINED_MODE}', \
    'Covariates', ${COV_ML}, 'CovariatesFile', '${COV_FILE}', \
    'BrainstemMask', '${BRAINSTEM_MASK}');"

echo "Running MATLAB group analysis..."
"${MATLAB_EXE}" -nodisplay -nosplash -batch "${matlab_cmd}"
rc=$?

echo ""
if [ ${rc} -eq 0 ]; then
    echo "============================================"
    echo " Group analysis complete ✓"
    echo " Output: ${OUTPUT_DIR}"
    echo "   <task>/SPM.mat + spmT_000*.nii"
    echo "   Contrasts: Cases>Controls, Controls>Cases, Cases mean, Controls mean"
    echo "============================================"
else
    echo "ERROR: MATLAB exited with code ${rc}"
    exit ${rc}
fi
