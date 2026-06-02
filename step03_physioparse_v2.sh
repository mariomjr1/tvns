#!/bin/bash

# ============================================================================
# step03_physioparse_v2.sh
# Created by Mario Murakami
#
# Runs the physioparse pipeline for a single tVNS subject:
#
#   Step 1 — Pseudotime mapping    (step01_times_acquisition.sh)
#             Anchors the physio recording to BIDS acquisition times.
#             Output: pseudotime_mapping.json
#
#   Step 2 — Quality visualisation (step02_plot_pseudotime_quality.py)
#             Creates signal overview + acquisition timeline plot.
#             Output: pseudotime_plot.png, pseudotime_plot_stats.png
#
#   Step 3 — Parse segments        (step03_parse.py)
#             Cuts the .mat into one .mat per BOLD run.
#             Output: parsed/task-*_run-*.mat  +  parsed/plots/
#
#   Step 4 — Signal QC             (step04_qc.py)
#             SNR, heart-rate, respiration-rate, trigger-jitter metrics.
#             Output: qc/physio_qc_plot.png, qc/physio_qc_metrics.csv
#
# Usage:
#   bash step03_physioparse_v2.sh <mat_file> <bids_subject_id> \
#        [sourcedata_dir] [physioparse_dir] [python_exe]
#
# Arguments:
#   mat_file          Full path to the session .mat file (LabChart export).
#                     This is the only file that must be provided manually.
#   bids_subject_id   BIDS subject label with 'sub-' prefix.
#                     Example: sub-7T1019HC042726
#   sourcedata_dir    Root BIDS directory (contains sub-* and .heudiconv/).
#                     Default: /autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata
#   physioparse_dir   Root of the physioparse code repository.
#                     Default: <script_dir>/../physioparse
#   python_exe        Python interpreter.
#                     Default: python3
#
# Data sourced automatically:
#   JSON sidecars  → sourcedata/<bids_subject>/ses-01/func/*.json
#                    (contain AcquisitionTime — Step 1 anchor)
#   dicominfo.tsv  → sourcedata/.heudiconv/<old_name>/info/dicominfo.tsv
#                    (contain dim4, TR, series_description — Steps 2–3 durations)
#                    Matched to the BIDS subject by normalising both names:
#                    remove underscores + lowercase, then compare.
#
# Output tree:
#   sourcedata/derivatives/physio/<bids_subject_id>/
#     pseudotime_mapping.json
#     pseudotime_plot.png
#     pseudotime_plot_stats.png
#     parsed/
#       task-*_run-*.mat
#       plots/
#     qc/
#       physio_qc_plot.png
#       physio_qc_metrics.csv
# ============================================================================

set -euo pipefail

# ── Arguments ─────────────────────────────────────────────────────────────────
if [ $# -lt 2 ]; then
    echo "Usage: $0 <mat_file> <bids_subject_id> [sourcedata_dir] [physioparse_dir] [python_exe]"
    echo "Example: $0 /path/to/7T1019HC042726.mat sub-7T1019HC042726"
    exit 1
fi

MAT_FILE="$1"
BIDS_SUBJ="$2"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

SOURCEDATA="${3:-/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata}"
PHYSIOPARSE_DIR="${4:-${SCRIPT_DIR}/../physioparse}"
PYTHON="${5:-python3}"

echo "============================================"
echo " TVNS Physioparse Pipeline"
echo " Subject:      ${BIDS_SUBJ}"
echo " .mat file:    ${MAT_FILE}"
echo " sourcedata:   ${SOURCEDATA}"
echo " physioparse:  ${PHYSIOPARSE_DIR}"
echo " Date:         $(date)"
echo "============================================"
echo ""

# ── Validate inputs ────────────────────────────────────────────────────────────
if [ ! -f "${MAT_FILE}" ]; then
    echo "ERROR: .mat file not found: ${MAT_FILE}"
    exit 1
fi

if [ ! -d "${SOURCEDATA}" ]; then
    echo "ERROR: sourcedata directory not found: ${SOURCEDATA}"
    exit 1
fi

if [ ! -d "${PHYSIOPARSE_DIR}" ]; then
    echo "ERROR: physioparse directory not found: ${PHYSIOPARSE_DIR}"
    exit 1
fi

# ── Locate BIDS func dir (JSON sidecars) ──────────────────────────────────────
# JSON files live in sourcedata/<bids_subject>/ses-01/func/
# They contain AcquisitionTime which Step 1 uses to anchor pseudotime.
bids_func_dir="${SOURCEDATA}/${BIDS_SUBJ}/ses-01/func"

if [ ! -d "${bids_func_dir}" ]; then
    echo "ERROR: BIDS func directory not found: ${bids_func_dir}"
    echo "  Run step01 (heudiconv BIDS conversion) for this subject first."
    exit 1
fi

n_json=$(find "${bids_func_dir}" -maxdepth 1 -name "*_bold.json" | wc -l)
if [ "${n_json}" -eq 0 ]; then
    echo "ERROR: No *_bold.json files found in ${bids_func_dir}"
    exit 1
fi
echo " Found ${n_json} JSON sidecars in ${bids_func_dir}"

# ── Locate dicominfo.tsv in .heudiconv ────────────────────────────────────────
# .heudiconv is created by heudiconv with the OLD scanner subject name
# (e.g. 7T1019HC_042726), while BIDS uses the name without underscores
# (e.g. sub-7T1019HC042726).
#
# Normalise both names — remove underscores and convert to lowercase —
# then compare to find the correct .heudiconv subfolder.
bids_label="${BIDS_SUBJ#sub-}"                         # strip "sub-" prefix
bids_norm=$(echo "${bids_label}" | tr -d '_' | tr '[:upper:]' '[:lower:]')

dicominfo_tsv=""
heudiconv_root="${SOURCEDATA}/.heudiconv"

if [ -d "${heudiconv_root}" ]; then
    for hdir in "${heudiconv_root}"/*/; do
        [ -d "${hdir}" ] || continue
        old_name=$(basename "${hdir}")
        old_norm=$(echo "${old_name}" | tr -d '_' | tr '[:upper:]' '[:lower:]')
        if [ "${old_norm}" = "${bids_norm}" ]; then
            candidate="${hdir}info/dicominfo.tsv"
            if [ -f "${candidate}" ]; then
                dicominfo_tsv="${candidate}"
                echo " Found dicominfo.tsv: ${candidate}"
                echo "   (matched '${old_name}' → '${BIDS_SUBJ}' by normalised name)"
                break
            fi
        fi
    done
fi

if [ -z "${dicominfo_tsv}" ]; then
    echo "WARNING: dicominfo.tsv not found for ${BIDS_SUBJ} under ${heudiconv_root}"
    echo "   Step 3 will fall back to 120 s duration for all sequences."
    echo "   Run step01 (heudiconv Pass 1, -c none) to generate dicominfo.tsv."
fi

# ── Create working directory ───────────────────────────────────────────────────
# All physioparse scripts expect a single folder containing:
#   - the .mat file (or a symlink)
#   - BIDS JSON sidecars
#   - dicominfo_ses-01.tsv
#   - pseudotime_mapping.json (created by Step 1)
work_dir="${SOURCEDATA}/derivatives/physio/${BIDS_SUBJ}"
mkdir -p "${work_dir}"
echo ""
echo " Working directory: ${work_dir}"

# ── Symlink .mat file ─────────────────────────────────────────────────────────
# We symlink (not copy) because the .mat file can be several gigabytes.
mat_basename=$(basename "${MAT_FILE}")
mat_link="${work_dir}/${mat_basename}"

if [ ! -e "${mat_link}" ]; then
    ln -s "${MAT_FILE}" "${mat_link}"
    echo " Symlinked .mat: ${mat_basename}"
else
    echo " .mat symlink already exists: ${mat_basename}"
fi

# ── Copy JSON sidecars ────────────────────────────────────────────────────────
# Copy all *_bold.json files from the BIDS func dir into the working dir.
# Step 1 reads AcquisitionTime from every JSON in the working dir.
echo " Copying JSON sidecars ..."
cp -n "${bids_func_dir}"/*_bold.json "${work_dir}/" 2>/dev/null || true
n_copied=$(find "${work_dir}" -maxdepth 1 -name "*_bold.json" | wc -l)
echo "   ${n_copied} JSON files in working dir"

# ── Copy dicominfo.tsv ────────────────────────────────────────────────────────
# Step 3 (parse) reads dim4 × TR from this file to determine each segment's length.
if [ -n "${dicominfo_tsv}" ]; then
    cp -f "${dicominfo_tsv}" "${work_dir}/dicominfo_ses-01.tsv"
    echo " Copied dicominfo.tsv → ${work_dir}/dicominfo_ses-01.tsv"
fi

# ── Activate environment ──────────────────────────────────────────────────────
env_script="${SCRIPT_DIR}/utility/fmriprep_env.sh"
if [ -f "${env_script}" ]; then
    source "${env_script}"
fi

# physioparse scripts import _common.py from their own directory
export PYTHONPATH="${PHYSIOPARSE_DIR}:${PYTHONPATH:-}"

echo ""
echo "============================================"
echo " Step 1: Pseudotime mapping"
echo "============================================"

# step01_times_acquisition.sh expects: <folder> <mat_filename> [python_exe]
bash "${PHYSIOPARSE_DIR}/step01_times_acquisition.sh" \
    "${work_dir}" "${mat_basename}" "${PYTHON}"

if [ ! -f "${work_dir}/pseudotime_mapping.json" ]; then
    echo "ERROR: Step 1 failed — pseudotime_mapping.json not created."
    exit 1
fi

echo ""
echo "============================================"
echo " Step 2: Quality visualisation"
echo "============================================"

# step02_plot_pseudotime_quality.py: <mat_file> <mapping_json> <output_png>
"${PYTHON}" "${PHYSIOPARSE_DIR}/step02_plot_pseudotime_quality.py" \
    "${mat_link}" \
    "${work_dir}/pseudotime_mapping.json" \
    "${work_dir}/pseudotime_plot.png"

echo ""
echo "============================================"
echo " Step 3: Parse segments"
echo "============================================"

# step03_parse.py: <data_dir> <output_dir>
# data_dir   = work_dir (contains .mat, JSONs, dicominfo, pseudotime_mapping.json)
# output_dir = work_dir/parsed/
parsed_dir="${work_dir}/parsed"
mkdir -p "${parsed_dir}"

"${PYTHON}" "${PHYSIOPARSE_DIR}/step03_parse.py" \
    "${work_dir}" \
    "${parsed_dir}"

echo ""
echo "============================================"
echo " Step 4: Signal QC"
echo "============================================"

# step04_qc.py: <data_dir> [output_dir]
qc_dir="${work_dir}/qc"
mkdir -p "${qc_dir}"

"${PYTHON}" "${PHYSIOPARSE_DIR}/step04_qc.py" \
    "${work_dir}" \
    "${qc_dir}"

echo ""
echo "============================================"
echo " Outputs written to: ${work_dir}"
echo ""
echo "   pseudotime_mapping.json    — timing anchor"
echo "   pseudotime_plot.png        — signal + timeline overview"
echo "   parsed/task-*_run-*.mat    — per-sequence physio segments"
echo "   qc/physio_qc_metrics.csv   — SNR / rate / trigger QC"
echo ""
echo " Next steps:"
echo "   1. Review pseudotime_plot.png to verify sequence alignment"
echo "   2. Use R-DECO on RPIEZO channel in each parsed .mat to detect R-peaks"
echo "   3. Run utility/matlab_code/preproc_generate_1D.m on parsed/ to produce .1D files"
echo "   4. Run step10 (correct_names) to rename .1D files to BIDS format"
echo "   5. Run step12 (retroicor_batch) on the 4_names_corrected/ folder"
echo ""
echo " Done.  $(date)"
echo "============================================"
