#!/bin/bash

# Created by Mario Murakami
# STEP 1 V2 - Run heudiconv in two passes:
#   Pass 1 (step01): generate conversion codes with -c none
#   Pass 2 (step02): convert DICOMs to NIfTI in BIDS format
# Adapted for the DICOM/raw/ folder structure produced by step00_v2

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

source /autofs/cluster/vagabond/USERS/MARIO/Packages/env/heudiconv/bin/activate

echo " Created by Mario Murakami"
echo " STEP 1 V2 - heudiconv: create codes + convert to BIDS"
echo " Date: $(date)"

# ── Paths ──────────────────────────────────────────────────────────────────────
raw_path=/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/rawdata
sourcedata=/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata
heuristic=/autofs/cluster/vagabond/USERS/MARIO/Pipelines/9_tvns/utility/heuristic.py

SUBJ_LIST=${1:-"${SCRIPT_DIR}/utility/SubjectList.txt"}

# ── Loop subjects ──────────────────────────────────────────────────────────────
# A subject may have one raw folder (.../DICOM/raw) or several from step00
# (.../DICOM/raw_01, raw_02, …). Each raw folder is converted as its own BIDS
# session: plain "raw" → ses-01; "raw_NN" → ses-NN.
while IFS= read -r subj_id; do
    [ -z "${subj_id}" ] && continue

    dcm_root="${raw_path}/${subj_id}/DICOM"

    echo ""
    echo "============================================"
    echo " Subject : ${subj_id}"
    echo "============================================"

    # Collect raw folders (raw + raw_*)
    raw_dirs=()
    [ -d "${dcm_root}/raw" ] && raw_dirs+=("${dcm_root}/raw")
    for d in "${dcm_root}"/raw_*; do
        [ -d "$d" ] && raw_dirs+=("$d")
    done

    if [ ${#raw_dirs[@]} -eq 0 ]; then
        echo "WARNING: no raw DICOM folder for ${subj_id}, skipping"
        continue
    fi

    sess_idx=0
    for dicom_dir in "${raw_dirs[@]}"; do
        sess_idx=$((sess_idx+1))
        base=$(basename "${dicom_dir}")
        # session label: raw_NN -> NN, plain raw -> 01
        if [[ "${base}" =~ ^raw_([0-9]+)$ ]]; then
            ss="${BASH_REMATCH[1]}"
        else
            ss="01"
        fi

        echo "--------------------------------------------"
        echo " DICOMs  : ${dicom_dir}   ->  ses-${ss}"
        echo "--------------------------------------------"

        # ── Pass 1: generate heudiconv conversion codes (dry run) ─────────────
        echo "Pass 1: generating conversion codes..."
        heudiconv --files "${dicom_dir}" \
                  -o "${sourcedata}" \
                  -f convertall -s "${subj_id}" -ss "${ss}" -c none

        # ── Pass 2: convert DICOMs to NIfTI in BIDS format ────────────────────
        echo "Pass 2: converting to BIDS NIfTI..."
        heudiconv --files "${dicom_dir}" \
                  -o "${sourcedata}" \
                  -f "${heuristic}" \
                  -s "${subj_id}" -ss "${ss}" -c dcm2niix -b --overwrite
    done

done < "${SUBJ_LIST}"

echo ""
echo " Done. $(date)"
