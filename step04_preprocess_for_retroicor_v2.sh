#!/bin/bash

# ============================================================================
# step04_preprocess_for_retroicor_v2.sh
# Created by Mario Murakami
#
# Filter per-sequence physio .mat files (physioparse step 03 output) to
# prepare them for R-DECO cardiac annotation and RETROICOR (step 05).
#
# What this script does:
#   For each task-*_run-*.mat in the physioparse parsed/ folder:
#     1. Applies highpass + bandpass filtering to RPIEZO (cardiac signal)
#     2. Computes the Hilbert envelope of the filtered signal
#     3. Saves two output files:
#          <subj>_task-*_run-*_filtered.mat   physio struct (for step 05)
#          <subj>_task-*_run-*_rpiezo.mat     plain RPIEZO array (for R-DECO)
#
# STOPS BEFORE R-DECO.
#   After running this script:
#     - Open each *_rpiezo.mat in R-DECO  (utility/r-deco-master/R_DECO.m)
#     - Detect and correct R-peaks
#     - Save R-DECO output as *_rdeco.mat in the same output folder
#     - Then run step05_retroicor_v2.sh
#
# Usage:
#   bash step04_preprocess_for_retroicor_v2.sh <bids_subject_id> [options]
#
# Required:
#   bids_subject_id    BIDS subject ID (e.g. sub-7T1019HC042726)
#
# Optional positional arguments (in order):
#   parsed_dir         physioparse parsed/ folder
#                      default: <sourcedata>/derivatives/physio/<subj>/parsed
#   output_dir         where to write preprocessed mats
#                      default: <sourcedata>/derivatives/physio/<subj>/preprocessed
#   sourcedata_dir     BIDS sourcedata root
#                      default: /autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata
#   matlab_exe         MATLAB executable
#                      default: matlab
#   matlab_code_dir    folder containing preproc_filter_per_sequence.m
#                      default: <script_dir>/utility/matlab_code
#   sr                 physio sampling rate Hz   default: 1000
#   hp_cutoff          highpass cutoff Hz        default: 0.05
#   bp_low             bandpass lower edge Hz    default: 0.5
#   bp_high            bandpass upper edge Hz    default: 2.0
# ============================================================================

set -euo pipefail

# ── Required argument ─────────────────────────────────────────────────────────
if [ $# -lt 1 ]; then
    echo "Usage: $0 <bids_subject_id> [parsed_dir] [output_dir] [sourcedata_dir]"
    echo "         [matlab_exe] [matlab_code_dir] [sr] [hp_cutoff] [bp_low] [bp_high]"
    echo "Example: $0 sub-7T1019HC042726"
    exit 1
fi

BIDS_SUBJ="$1"

# ── Script location ───────────────────────────────────────────────────────────
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# ── Defaults (override via positional arguments) ──────────────────────────────
SOURCEDATA="${4:-/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata}"
PARSED_DIR="${2:-${SOURCEDATA}/derivatives/physio/${BIDS_SUBJ}/parsed}"
OUTPUT_DIR="${3:-${SOURCEDATA}/derivatives/physio/${BIDS_SUBJ}/preprocessed}"
MATLAB_EXE="${5:-matlab}"
MATLAB_CODE_DIR="${6:-${SCRIPT_DIR}/utility/matlab_code}"
SR="${7:-1000}"
HP_CUTOFF="${8:-0.05}"
BP_LOW="${9:-0.5}"
BP_HIGH="${10:-2.0}"

echo "============================================"
echo " STEP 04 — Filter physio for RETROICOR"
echo " Subject:       ${BIDS_SUBJ}"
echo " Input (parsed):${PARSED_DIR}"
echo " Output:        ${OUTPUT_DIR}"
echo " MATLAB:        ${MATLAB_EXE}"
echo " Filter:        HP=${HP_CUTOFF} Hz  BP=[${BP_LOW} ${BP_HIGH}] Hz  SR=${SR} Hz"
echo " Date:          $(date)"
echo "============================================"
echo ""

# ── Validate ──────────────────────────────────────────────────────────────────
if [ ! -d "${PARSED_DIR}" ]; then
    echo "ERROR: Parsed physio directory not found: ${PARSED_DIR}"
    echo "  Run step03_physioparse_v2.sh for this subject first."
    exit 1
fi

if [ ! -d "${MATLAB_CODE_DIR}" ]; then
    echo "ERROR: MATLAB code directory not found: ${MATLAB_CODE_DIR}"
    exit 1
fi

matlab_filter="${MATLAB_CODE_DIR}/preproc_filter_per_sequence.m"
if [ ! -f "${matlab_filter}" ]; then
    echo "ERROR: preproc_filter_per_sequence.m not found in: ${MATLAB_CODE_DIR}"
    exit 1
fi

n_mats=$(find "${PARSED_DIR}" -maxdepth 1 -name "task-*_run-*.mat" | wc -l)
if [ "${n_mats}" -eq 0 ]; then
    echo "ERROR: No task-*_run-*.mat files found in ${PARSED_DIR}"
    echo "  Run physioparse step 03 first."
    exit 1
fi
echo " Found ${n_mats} per-sequence mat(s) to process."
echo ""

# ── Source environment ────────────────────────────────────────────────────────
env_script="${SCRIPT_DIR}/utility/fmriprep_env.sh"
if [ -f "${env_script}" ]; then
    source "${env_script}"
fi

# ── Create output directory ───────────────────────────────────────────────────
mkdir -p "${OUTPUT_DIR}"

# ── Run MATLAB filter ─────────────────────────────────────────────────────────
# Suppress figure windows (prevent hanging on headless cluster)
matlab_cmd="set(0,'DefaultFigureVisible','off'); \
addpath('${MATLAB_CODE_DIR}'); \
preproc_filter_per_sequence( \
    '${PARSED_DIR}', \
    '${OUTPUT_DIR}', \
    '${BIDS_SUBJ}', \
    'SR', ${SR}, \
    'HP', ${HP_CUTOFF}, \
    'BPL', ${BP_LOW}, \
    'BPH', ${BP_HIGH} \
);"

echo "Running MATLAB filter batch..."
"${MATLAB_EXE}" -nodisplay -nosplash -batch "${matlab_cmd}"
matlab_exit=$?

echo ""
if [ ${matlab_exit} -eq 0 ]; then
    echo "============================================"
    echo " Filter complete ✓"
    echo ""
    echo " Preprocessed mats: ${OUTPUT_DIR}"
    echo ""
    echo " Next: R-DECO cardiac annotation"
    echo " ──────────────────────────────────────────"
    echo " For each *_rpiezo.mat file:"
    echo "   1. Open MATLAB"
    echo "   2. addpath('${SCRIPT_DIR}/utility/r-deco-master')"
    echo "   3. R_DECO"
    echo "   4. Load <subj>_task-*_run-*_rpiezo.mat"
    echo "   5. Run auto-detection, correct manually"
    echo "   6. Save output as <subj>_task-*_run-*_rdeco.mat"
    echo "      in: ${OUTPUT_DIR}"
    echo " ──────────────────────────────────────────"
    echo " Then run: bash step05_retroicor_v2.sh ${BIDS_SUBJ}"
    echo "============================================"
else
    echo "ERROR: MATLAB exited with code ${matlab_exit}"
    exit ${matlab_exit}
fi
