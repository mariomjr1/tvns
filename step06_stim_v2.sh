#!/bin/bash

# ============================================================================
# step06_stim_v2.sh
# Created by Mario Murakami
#
# Extract stimulus onset/offset times from physioparse per-sequence mats,
# rename to BIDS format, and assemble everything needed for the first-level GLM.
#
# Replaces steps 16, 17, 18, 19 using physioparse output instead of raw mats.
#
#   PART 1  Extract stim onsets    utility/extract_stim_onsets.py
#           Reads STIMTRIG + MRTRIG from each physioparse parsed mat.
#           Output: <stim_dir>/<subj>_ses-<session>_task-*_run-*_bold_stim.txt
#
#   PART 2  Copy to fMRIPrep func  (replaces manual step 18)
#           Copies stim .txt files to derivatives/fmriprep/<subj>/ses-<session>/func/
#
#   PART 3  Prepare first-level    (replaces step 19)
#           Assembles stim files, motion regressors, and T1w BOLDs into a
#           single first_level/ folder for SPM/AFNI GLM.
#           This part is OPTIONAL — skip with --no-prep-firstlevel.
#
#   PART 4  QC plots               optional (--qc flag)
#           Saves a PNG per run showing STIMTRIG + detected onsets.
#
# Usage:
#   bash step06_stim_v2.sh <bids_subject_id> [options]
#
# Required:
#   bids_subject_id   BIDS subject (e.g. sub-7T1019HC042726)
#
# Optional positional arguments (in order):
#   sourcedata_dir    BIDS sourcedata root
#                     default: /autofs/.../lyme/sourcedata
#   parsed_dir        physioparse parsed/ folder
#                     default: <sourcedata>/derivatives/physio/<subj>/parsed
#   stim_dir          output folder for stim .txt files
#                     default: <sourcedata>/derivatives/physio/<subj>/stimtrigger
#   fmriprep_dir      fMRIPrep derivatives root (for copy-to-func)
#                     default: <sourcedata>/derivatives/fmriprep
#   firstlevel_dir    first-level assembly folder
#                     default: <sourcedata>/derivatives/physio/<subj>/first_level
#   session           BIDS session label           default: 01
#   threshold         STIMTRIG step threshold      default: 1.5
#   debounce          min seconds between events   default: 1.5
#   python_exe        Python interpreter           default: python3
#   qc                1 to generate QC plots, 0 to skip   default: 0
#   no_firstlevel     1 to skip PART 3             default: 0
#   retroicor_output  retroicor output dir (arg 13)
#   fd_thresh         FD spike threshold (mm) for motion regressors  default: 0.5
#
#   PART 3 now GENERATES motion regressors from the fMRIPrep confounds TSVs
#   (6 rigid params + one FD-spike regressor per high-motion volume) via
#   utility/extract_motion_regressors.py, instead of relying on a pre-existing
#   *_motion_regressors.txt. A per-subject .log is written to <first_level>/logs.
# ============================================================================

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <bids_subject_id> [sourcedata_dir] [parsed_dir] ..."
    echo "Example: $0 sub-7T1019HC042726"
    exit 1
fi

BIDS_SUBJ="$1"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

SOURCEDATA="${2:-/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata}"
PARSED_DIR="${3:-${SOURCEDATA}/derivatives/physio/${BIDS_SUBJ}/parsed}"
STIM_DIR="${4:-${SOURCEDATA}/derivatives/physio/${BIDS_SUBJ}/stimtrigger}"
FMRIPREP_DIR="${5:-${SOURCEDATA}/derivatives/fmriprep}"
# Shared first-level folder (NOT per-subject): step07 reads every subject's
# files from one place. Files are named by subject, so they coexist.
FIRSTLEVEL_DIR="${6:-${SOURCEDATA}/derivatives/physio/first_level}"
SESSION="${7:-01}"
THRESHOLD="${8:-1.5}"
DEBOUNCE="${9:-1.5}"
PYTHON="${10:-python3}"
DO_QC="${11:-0}"
SKIP_FIRSTLEVEL="${12:-0}"
RETROICOR_OUTPUT="${13:-${SOURCEDATA}/derivatives/physio/${BIDS_SUBJ}/retroicor/output}"
FD_THRESH="${14:-0.5}"

EXTRACTOR="${SCRIPT_DIR}/utility/extract_stim_onsets.py"
MOTION_EXTRACTOR="${SCRIPT_DIR}/utility/extract_motion_regressors.py"

echo "============================================"
echo " STEP 06 — Stimulus trigger extraction"
echo " Subject:    ${BIDS_SUBJ}"
echo " Parsed dir: ${PARSED_DIR}"
echo " Stim dir:   ${STIM_DIR}"
echo " Session:    ses-${SESSION}"
echo " Threshold:  ${THRESHOLD}  Debounce: ${DEBOUNCE} s"
echo " QC plots:   ${DO_QC}"
echo " FD spike threshold: ${FD_THRESH} mm"
echo " Retroicor output: ${RETROICOR_OUTPUT}"
echo " Date:       $(date)"
echo "============================================"
echo ""

# ── Validate ──────────────────────────────────────────────────────────────────
if [ ! -d "${PARSED_DIR}" ]; then
    echo "ERROR: Parsed physio directory not found: ${PARSED_DIR}"
    echo "  Run step02_physioparse_v2.sh first."
    exit 1
fi

n_mats=$(find "${PARSED_DIR}" -maxdepth 1 -name "task-*_run-*.mat" | wc -l)
if [ "${n_mats}" -eq 0 ]; then
    echo "ERROR: No task-*_run-*.mat files found in ${PARSED_DIR}"
    exit 1
fi
echo " Found ${n_mats} parsed mat(s)."

if [ ! -f "${EXTRACTOR}" ]; then
    echo "ERROR: extract_stim_onsets.py not found: ${EXTRACTOR}"
    exit 1
fi

[ -f "${SCRIPT_DIR}/utility/fmriprep_env.sh" ] && source "${SCRIPT_DIR}/utility/fmriprep_env.sh"

mkdir -p "${STIM_DIR}"

# ── PART 1: Extract stim onsets ───────────────────────────────────────────────
# Calls extract_stim_onsets.py for every parsed mat (all task types).
# Rest runs typically have no stimulus events — the script handles this gracefully
# by writing a file with a header comment.
echo "============================================"
echo " PART 1: Extract stimulus onsets"
echo "============================================"

qc_args=""
if [ "${DO_QC}" = "1" ]; then
    qc_args="--qc --qc-dir ${STIM_DIR}/qc"
    mkdir -p "${STIM_DIR}/qc"
fi

n_ok=0
n_fail=0
for mat in "${PARSED_DIR}"/task-*_run-*.mat; do
    [ -f "${mat}" ] || continue
    mat_name=$(basename "${mat}")
    echo " Processing: ${mat_name}"

    "${PYTHON}" "${EXTRACTOR}" \
        "${mat}" \
        "${BIDS_SUBJ}" \
        "${STIM_DIR}" \
        --session   "${SESSION}" \
        --threshold "${THRESHOLD}" \
        --debounce  "${DEBOUNCE}" \
        ${qc_args} \
        && n_ok=$((n_ok+1)) \
        || { echo "  FAILED: ${mat_name}"; n_fail=$((n_fail+1)); }
done

echo ""
echo " Part 1 done.  OK: ${n_ok}  |  Failed: ${n_fail}"
echo " Stim files: ${STIM_DIR}"
echo ""

# ── PART 2: Copy stim files to fMRIPrep func folder ───────────────────────────
# Places stim .txt alongside fMRIPrep outputs so step 19 / GLM tools find them
# at the expected location.
echo "============================================"
echo " PART 2: Copy stim files to fMRIPrep func/"
echo "============================================"

fmriprep_func="${FMRIPREP_DIR}/${BIDS_SUBJ}/ses-${SESSION}/func"

if [ ! -d "${fmriprep_func}" ]; then
    echo "WARNING: fMRIPrep func dir not found: ${fmriprep_func}"
    echo "  Skipping copy — run fMRIPrep first or create the folder manually."
else
    n_copied=0
    for stim_file in "${STIM_DIR}"/*_bold_stim.txt; do
        [ -f "${stim_file}" ] || continue
        cp -f "${stim_file}" "${fmriprep_func}/"
        n_copied=$((n_copied+1))
    done
    echo " Copied ${n_copied} stim file(s) to ${fmriprep_func}"
fi
echo ""

# ── PART 3: Prepare first-level folder ────────────────────────────────────────
# Assembles stim files, motion regressors (from fMRIPrep confounds), retroicor
# regressors, and T1w-space BOLD files into a self-contained first_level/ folder
# for the GLM. OPTIONAL — skip with SKIP_FIRSTLEVEL=1.
if [ "${SKIP_FIRSTLEVEL}" = "1" ]; then
    echo "PART 3: Skipped (SKIP_FIRSTLEVEL=1)."
else
    echo "============================================"
    echo " PART 3: Prepare first-level folder (optional)"
    echo "============================================"

    # Numbered sub-folders (ordered, self-documenting). The GLM (step07) also
    # accepts the legacy unnumbered names for backward compatibility.
    # NEW PIPELINE (RETROICOR → fMRIPrep): no 03_retroicor_regressors — physio is
    # removed from the image before fMRIPrep, so the GLM uses motion only.
    D_STIM="${FIRSTLEVEL_DIR}/01_stim_onsets"
    D_MOTION="${FIRSTLEVEL_DIR}/02_motion_regressors"
    D_BOLD="${FIRSTLEVEL_DIR}/04_bolds"
    mkdir -p "${D_STIM}" "${D_MOTION}" "${D_BOLD}"

    # Stim files
    n_stim=0
    for f in "${STIM_DIR}"/*_bold_stim.txt; do
        [ -f "$f" ] || continue
        cp -f "$f" "${D_STIM}/"
        n_stim=$((n_stim+1))
    done
    echo " Stim files:      ${n_stim}"

    # Motion regressors: GENERATE from fMRIPrep confounds TSVs
    # (6 rigid params + one FD-spike regressor per volume with FD > FD_THRESH).
    # Then fall back to any pre-existing *_motion_regressors.txt (legacy).
    fmriprep_subj="${FMRIPREP_DIR}/${BIDS_SUBJ}"
    MOTION_LOG_DIR="${FIRSTLEVEL_DIR}/logs"
    if [ -f "${MOTION_EXTRACTOR}" ] && [ -d "${fmriprep_func}" ]; then
        echo " Generating motion regressors (6 rigid + FD>${FD_THRESH} mm spikes)..."
        "${PYTHON}" "${MOTION_EXTRACTOR}" \
            "${fmriprep_func}" \
            "${D_MOTION}" \
            --subject   "${BIDS_SUBJ}" \
            --session   "${SESSION}" \
            --fd-thresh "${FD_THRESH}" \
            --log-dir   "${MOTION_LOG_DIR}" \
            || echo "  WARNING: motion regressor generation failed"
    else
        echo " WARNING: cannot generate motion regressors"
        echo "   (missing ${MOTION_EXTRACTOR} or func dir ${fmriprep_func})"
    fi
    # Legacy fallback: copy pre-existing *_motion_regressors.txt not already present
    for f in "${fmriprep_subj}"/*_motion_regressors.txt \
             "${fmriprep_func}"/*_motion_regressors.txt; do
        [ -f "$f" ] || continue
        bn=$(basename "$f")
        [ -f "${D_MOTION}/${bn}" ] || cp -f "$f" "${D_MOTION}/"
    done
    n_motion=$(find "${D_MOTION}" -maxdepth 1 -name "${BIDS_SUBJ}_*_motion_regressors.txt" 2>/dev/null | wc -l | tr -d ' ')
    echo " Motion regressors: ${n_motion}   (log: ${MOTION_LOG_DIR})"

    # (RETROICOR regressors are no longer assembled — physio removed pre-fMRIPrep.)

    # T1w-space preprocessed BOLD from fMRIPrep (now physio-cleaned)
    n_bolds=0
    if [ -d "${fmriprep_func:-/nonexistent}" ]; then
        for f in "${fmriprep_func}"/*_space-T1w_desc-preproc_bold.nii.gz; do
            [ -f "$f" ] || continue
            cp -f "$f" "${D_BOLD}/"
            n_bolds=$((n_bolds+1))
        done
    fi
    echo " BOLD files:      ${n_bolds} (0 = run fMRIPrep first)"

    echo " First-level dir: ${FIRSTLEVEL_DIR}"
fi
echo ""

# ── Summary ───────────────────────────────────────────────────────────────────
echo "============================================"
echo " Done.  $(date)"
echo ""
echo " Outputs:"
echo "   Stim .txt:    ${STIM_DIR}"
[ "${DO_QC}" = "1" ] && echo "   QC plots:     ${STIM_DIR}/qc"
echo ""
echo " Next step: step22 (first-level GLM)"
echo "============================================"
