#!/bin/bash

# ============================================================================
# step04_retroicor_v2.sh   (NEW ORDER: runs before fMRIPrep)
# Created by Mario Murakami
#
# Run the complete RETROICOR pipeline for one subject, using:
#   - Preprocessed physio mats from step03 (*_filtered.mat)
#   - R-DECO cardiac annotation outputs (*_rdeco.mat, optional)
# Produces *_retro-corrected.nii.gz (native space) which step05 feeds to fMRIPrep.
#
# Pipeline:
#   PART 1  Generate 1D files    preproc_generate_1D_v2.m
#           Reads physio struct + R-DECO peaks, reads TR from BIDS JSON,
#           writes RETRO-resp_*.1D (+ RETRO-qrs_*.1D if R-DECO available).
#
#   PART 2  Assemble input folder
#           Copies BIDS BOLD (.nii.gz) and sidecars (.json) from sourcedata
#           into the retroicor input folder alongside the 1D files.
#
#   PART 3  Run RETROICOR        retroicor_batch.m
#           Applies physiological noise correction to each BOLD.
#           Outputs *_retro-corrected.nii.gz + regressors + phase files.
#
# Usage:
#   bash step04_retroicor_v2.sh <bids_subject_id> [options]
#
# Required:
#   bids_subject_id   BIDS subject (e.g. sub-7T1019HC042726)
#
# Optional positional arguments (in order):
#   sourcedata_dir      BIDS sourcedata root
#                       default: /autofs/.../lyme/sourcedata
#   preproc_dir         step04 output (filtered + rdeco mats)
#                       default: <sourcedata>/derivatives/physio/<subj>/preprocessed
#   retro_input_dir     assembled BOLD+JSON+1D for retroicor_batch
#                       default: <sourcedata>/derivatives/physio/<subj>/retroicor/input
#   retro_output_dir    corrected BOLDs from retroicor_batch
#                       default: <sourcedata>/derivatives/physio/<subj>/retroicor/output
#   matlab_exe          MATLAB executable          default: matlab
#   matlab_code_dir     folder with .m scripts     default: <script_dir>/utility/matlab_code
#   retro_code_dir      folder with retroicor_main_modi.m
#                       default: <script_dir>/utility/retroicor
#   session             BIDS session label         default: 01
#   sms_flag            SMS (multiband): 1 or 0    default: 1
#   fs_out              output physio rate Hz       default: 40
#   tr_fallback         TR to use if JSON missing   default: 1.19
# ============================================================================

set -euo pipefail

# ── Required argument ─────────────────────────────────────────────────────────
if [ $# -lt 1 ]; then
    echo "Usage: $0 <bids_subject_id> [sourcedata_dir] [preproc_dir] ..."
    echo "Example: $0 sub-7T1019HC042726"
    exit 1
fi

BIDS_SUBJ="$1"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# ── Defaults ──────────────────────────────────────────────────────────────────
SOURCEDATA="${2:-/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata}"
PREPROC_DIR="${3:-${SOURCEDATA}/derivatives/physio/${BIDS_SUBJ}/preprocessed}"
RETRO_INPUT="${4:-${SOURCEDATA}/derivatives/physio/${BIDS_SUBJ}/retroicor/input}"
RETRO_OUTPUT="${5:-${SOURCEDATA}/derivatives/physio/${BIDS_SUBJ}/retroicor/output}"
MATLAB_EXE="${6:-matlab}"
MATLAB_CODE_DIR="${7:-${SCRIPT_DIR}/utility/matlab_code}"
RETRO_CODE_DIR="${8:-${SCRIPT_DIR}/utility/retroicor}"
SESSION="${9:-01}"
SMS_FLAG="${10:-1}"
FS_OUT="${11:-40}"
TR_FALLBACK="${12:-1.19}"
# Cardiac: 1 = cardiac + respiration (needs R-DECO); 0 = RESPIRATION-ONLY (bad piezo)
CARDIAC="${13:-1}"
# Per-run piezo-QC decision manifest (overrides CARDIAC per run). If not given,
# auto-use the conventional manifest in PREPROC_DIR when it exists.
DECISION_FILE="${14:-}"
if [ -z "${DECISION_FILE}" ] && [ -f "${PREPROC_DIR}/${BIDS_SUBJ}_cardiac_decision.csv" ]; then
    DECISION_FILE="${PREPROC_DIR}/${BIDS_SUBJ}_cardiac_decision.csv"
fi

echo "============================================"
echo " STEP 04 — RETROICOR (native-space physio correction, before fMRIPrep)"
echo " Subject:    ${BIDS_SUBJ}"
echo " Sourcedata: ${SOURCEDATA}"
echo " Preproc:    ${PREPROC_DIR}"
echo " Input dir:  ${RETRO_INPUT}"
echo " Output dir: ${RETRO_OUTPUT}"
echo " SMS=${SMS_FLAG}  FS_OUT=${FS_OUT}  TR_fallback=${TR_FALLBACK}"
echo " Date:       $(date)"
echo "============================================"
echo ""

# ── Validate inputs ───────────────────────────────────────────────────────────
if [ ! -d "${PREPROC_DIR}" ]; then
    echo "ERROR: Preprocessed directory not found: ${PREPROC_DIR}"
    echo "  Run step03_preprocess_for_retroicor_v2.sh first."
    exit 1
fi

n_filt=$(find "${PREPROC_DIR}" -maxdepth 1 -name "${BIDS_SUBJ}_task-*_filtered.mat" | wc -l)
if [ "${n_filt}" -eq 0 ]; then
    echo "ERROR: No *_filtered.mat files found in ${PREPROC_DIR}"
    exit 1
fi
echo " Found ${n_filt} filtered mat(s)."

n_rdeco=$(find "${PREPROC_DIR}" -maxdepth 1 -name "${BIDS_SUBJ}_task-*_rdeco.mat" | wc -l)
if [ "${CARDIAC}" = "1" ]; then
    echo " Found ${n_rdeco} R-DECO file(s) (cardiac regressors)."
    echo " Cardiac mode: ON (cardiac + respiration)"
else
    echo " Cardiac mode: OFF — RESPIRATION-ONLY (R-DECO ignored; use for bad piezo)"
fi
if [ -n "${DECISION_FILE}" ]; then
    echo " Per-run decisions: ${DECISION_FILE} (overrides global cardiac mode per run)"
fi
echo ""

# ── Source environment ────────────────────────────────────────────────────────
env_script="${SCRIPT_DIR}/utility/fmriprep_env.sh"
[ -f "${env_script}" ] && source "${env_script}"

# ── Create directories ────────────────────────────────────────────────────────
mkdir -p "${RETRO_INPUT}"
mkdir -p "${RETRO_OUTPUT}"

# ── PART 1: Generate 1D files ─────────────────────────────────────────────────
# Calls preproc_generate_1D_v2.m which:
#   - reads RESP + MRTRIG from physio struct
#   - incorporates R-DECO R-peaks if *_rdeco.mat exists
#   - reads TR from BIDS JSON sidecar
#   - writes RETRO-resp_*.1D (and RETRO-qrs_*.1D if R-DECO available) to RETRO_INPUT
echo "============================================"
echo " PART 1: Generate 1D files"
echo "============================================"

matlab_cmd_p1="set(0,'DefaultFigureVisible','off'); \
addpath('${MATLAB_CODE_DIR}'); \
preproc_generate_1D_v2( \
    '${PREPROC_DIR}', \
    '${RETRO_INPUT}', \
    '${BIDS_SUBJ}', \
    '${SOURCEDATA}', \
    'SMS', ${SMS_FLAG}, \
    'FS_OUT', ${FS_OUT}, \
    'TR_FALLBACK', ${TR_FALLBACK}, \
    'Cardiac', ${CARDIAC}, \
    'DecisionFile', '${DECISION_FILE}', \
    'SESSION', '${SESSION}');"

# preproc_generate_1D_v2 skips individual bad sequences (flag + log + continue) and
# exits 0; this guard only catches a genuine MATLAB crash (license/syntax/missing
# code). Per-sequence skips are reported in the MATLAB log above, not here.
if ! "${MATLAB_EXE}" -nodisplay -nosplash -batch "${matlab_cmd_p1}"; then
    echo "ERROR: generate_1D_v2 crashed (MATLAB error — check license/paths above)."
    exit 1
fi
echo ""

# ── PART 2: Assemble input folder ─────────────────────────────────────────────
# Copy BIDS BOLD files and JSON sidecars from sourcedata into RETRO_INPUT.
# retroicor_batch.m expects BOLD + JSON + 1D all in the same folder.
echo "============================================"
echo " PART 2: Assemble BOLD + JSON files"
echo "============================================"

bids_func_dir="${SOURCEDATA}/${BIDS_SUBJ}/ses-${SESSION}/func"

if [ ! -d "${bids_func_dir}" ]; then
    echo "ERROR: BIDS func directory not found: ${bids_func_dir}"
    exit 1
fi

n_bolds=$(find "${bids_func_dir}" -maxdepth 1 -name "*_bold.nii.gz" | wc -l)
echo " Copying ${n_bolds} BOLD(s) from ${bids_func_dir} ..."

# Copy BOLD NIfTI files
find "${bids_func_dir}" -maxdepth 1 -name "*_bold.nii.gz" \
    -exec cp -n {} "${RETRO_INPUT}/" \;

# Copy JSON sidecars
find "${bids_func_dir}" -maxdepth 1 -name "*_bold.json" \
    -exec cp -n {} "${RETRO_INPUT}/" \;

echo " Assembled. Contents of input folder:"
ls "${RETRO_INPUT}" | sed 's/^/   /'
echo ""

# ── PART 3: Run RETROICOR ─────────────────────────────────────────────────────
# retroicor_batch.m scans the input folder for:
#   *_bold.nii.gz      BIDS BOLD
#   *.json             BIDS sidecar (needs SliceTiming, RepetitionTime)
#   RETRO-resp_*.1D    respiratory regressor
#   RETRO-qrs_*.1D     cardiac regressor (optional)
echo "============================================"
echo " PART 3: Run RETROICOR"
echo "============================================"

matlab_cmd_p3="set(0,'DefaultFigureVisible','off'); \
addpath('${MATLAB_CODE_DIR}'); \
addpath('${RETRO_CODE_DIR}'); \
retroicor_batch('${RETRO_INPUT}', '${RETRO_OUTPUT}', '${RETRO_CODE_DIR}');"

"${MATLAB_EXE}" -nodisplay -nosplash -batch "${matlab_cmd_p3}"

retro_exit=$?

echo ""
if [ ${retro_exit} -eq 0 ]; then
    n_corrected=$(find "${RETRO_OUTPUT}" -maxdepth 1 -name "*_retro-corrected.nii.gz" 2>/dev/null | wc -l)
    echo "============================================"
    echo " RETROICOR complete ✓"
    echo " Corrected BOLDs:  ${n_corrected}"
    echo " Output:  ${RETRO_OUTPUT}"
    echo ""
    echo " Next steps:"
    echo "   - Review regressors: *_retro-regressors.mat"
    echo "   - Check % variance reduced: *_retro-pctvar.mat"
    echo "   - Inspect *_retro-corrected.nii.gz (these feed fMRIPrep)"
    echo "   - Run step05_fmriprep_v2.sh — fMRIPrep on the corrected BOLD"
    echo "============================================"
else
    echo "ERROR: retroicor_batch exited with code ${retro_exit}"
    exit ${retro_exit}
fi
