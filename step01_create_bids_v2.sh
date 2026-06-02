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
while IFS= read -r subj_id; do

    dicom_dir=${raw_path}/${subj_id}/DICOM/raw

    echo ""
    echo "============================================"
    echo " Subject : ${subj_id}"
    echo " DICOMs  : ${dicom_dir}"
    echo "============================================"

    if [ ! -d "${dicom_dir}" ]; then
        echo "WARNING: DICOM directory not found, skipping ${subj_id}"
        continue
    fi

    # ── Pass 1: generate heudiconv conversion codes (dry run) ─────────────────
    echo "Pass 1: generating conversion codes..."
    heudiconv --files ${dicom_dir} \
              -o ${sourcedata} \
              -f convertall -s ${subj_id} -ss 01 -c none

    # ── Pass 2: convert DICOMs to NIfTI in BIDS format ────────────────────────
    echo "Pass 2: converting to BIDS NIfTI..."
    heudiconv --files ${dicom_dir} \
              -o ${sourcedata} \
              -f ${heuristic} \
              -s ${subj_id} -ss 01 -c dcm2niix -b --overwrite

done < "${SUBJ_LIST}"

echo ""
echo " Done. $(date)"
