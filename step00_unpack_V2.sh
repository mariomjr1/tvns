#!/bin/bash

##################################################################################
# STEP 0 V2 - Download raw DICOMs using findsession + rsync
#
# This script:
#    a) Reads a subject ID list from a txt file (one ID per line)
#    b) Uses findsession to locate the Bourget DICOM archive for each subject
#    c) Copies the raw DICOM directory to <out_path>/<subjID>/DICOM/ via rsync
#    d) Opens one screen session per subject (check progress: screen -ls)
#
# NOTE 1: THIS SCRIPT RUNS LOCALLY on the linux workstation.
# NOTE 2: Subject IDs must match what findsession recognizes.
# NOTE 3: When done, "step0_DONE.txt" appears in <out_path>/<subjID>/DICOM/LOG/
#
# Usage:
#   bash step00_unpack_V2.sh
#   bash step00_unpack_V2.sh /path/to/CustomSubjectList.txt
##################################################################################

echo -e " by Mario Murakami"
echo -e " STEP 0 V2 - Download DICOMs via findsession + rsync"
echo -e " Date:\t $(date)\n"

# ── Paths ──────────────────────────────────────────────────────────────────────
out_path=/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/rawdata
subjlistpath=/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/codes

# Allow passing a custom subject list as first argument
subjlist=${1:-"${subjlistpath}/SubjectList.txt"}

echo -e " Subject list: ${subjlist}"
echo -e " Output path:  ${out_path}\n"

# ── Loop subjects ──────────────────────────────────────────────────────────────
declare -a StringArray=($(cat "${subjlist}"))

for subjID in ${StringArray[@]}; do

    echo " Spawning screen session for: ${subjID}"

    screen -S ${subjID}-download -dm bash -c "

        subjID=${subjID}
        out_path=${out_path}
        log_dir=\${out_path}/\${subjID}/DICOM/LOG

        echo '============================================'
        echo ' Subject : '\${subjID}
        echo ' Started : '\$(date)
        echo '============================================'

        mkdir -p \${log_dir}

        # ── 1. Find the DICOM path with findsession ────────────────────────────
        echo 'Running findsession...'
        findsession_out=\$(findsession \${subjID} 2>&1)
        echo \"\${findsession_out}\" | tee \${log_dir}/findsession.txt

        # findsession prints: PATH   :  /cluster/archive/...
        # Use \$NF to grab the last whitespace-separated token on that line
        dcmdir=\$(echo \"\${findsession_out}\" | grep '^PATH' | awk '{print \$NF}')

        if [ -z \"\${dcmdir}\" ] || [ ! -d \"\${dcmdir}\" ]; then
            echo \"ERROR: Could not resolve DICOM directory for \${subjID}\" | tee \${log_dir}/step0_ERROR.txt
            echo \"findsession output was:\"
            echo \"\${findsession_out}\"
            exit 1
        fi

        echo \"DICOM source: \${dcmdir}\"
        dest=\${out_path}/\${subjID}/DICOM/raw
        mkdir -p \${dest}

        # ── 2. Copy raw DICOMs ─────────────────────────────────────────────────
        echo 'Copying DICOMs with rsync...'
        rsync -av --progress \${dcmdir}/ \${dest}/ 2>&1 | tee \${log_dir}/rsync.log

        if [ \${PIPESTATUS[0]} -eq 0 ]; then
            echo 'Finished : '\$(date) | tee \${log_dir}/step0_DONE.txt
        else
            echo 'FAILED at rsync: '\$(date) | tee \${log_dir}/step0_ERROR.txt
        fi
    "

done

echo -e "\n All screen sessions spawned."
echo -e " Monitor  : screen -ls"
echo -e " Attach   : screen -r <subjID>-download"