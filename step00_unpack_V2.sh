#!/bin/bash

##################################################################################
# STEP 0 V2 - Download raw DICOMs using findsession + rsync
#
# This script:
#    a) Reads a subject ID list from a txt file (one ID per line)
#    b) Spawns one screen session per subject (check progress: screen -ls)
#    c) Each session uses findsession to locate the DICOM archive(s) and
#       rsync-copies them to <out_path>/<subjID>/DICOM/raw[/_NN]/
#
# Robust behaviour (per subject):
#    - If findsession returns SEVERAL DICOM PATHs for one ID (multiple sessions),
#      each is copied to its own folder: raw_01, raw_02, …  (single → raw)
#    - Subjects/paths with NO ACCESS are skipped (do not abort the run)
#    - Subjects ALREADY downloaded (step0_DONE.txt present) are skipped
#
# NOTE: Runs on the linux workstation (needs findsession + screen + rsync).
#
# Usage:
#   bash step00_unpack_V2.sh
#   bash step00_unpack_V2.sh /path/to/CustomSubjectList.txt
##################################################################################

echo -e " by Mario Murakami"
echo -e " STEP 0 V2 - Download DICOMs via findsession + rsync"
echo -e " Date:\t $(date)\n"

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# ── Paths ──────────────────────────────────────────────────────────────────────
out_path=/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/rawdata

# Allow passing a custom subject list as first argument
subjlist=${1:-"${SCRIPT_DIR}/utility/SubjectList.txt"}

echo -e " Subject list: ${subjlist}"
echo -e " Output path:  ${out_path}\n"

if [ ! -f "${subjlist}" ]; then
    echo "ERROR: subject list not found: ${subjlist}"
    exit 1
fi

# ── Per-subject download helper (run inside each screen) ────────────────────────
# Written once to a temp file; each screen session calls it.
helper=$(mktemp "${TMPDIR:-/tmp}/step00_dl_XXXXXX.sh")
cat > "${helper}" <<'HELPER'
#!/bin/bash
subj="$1"
out_path="$2"
subj_dir="${out_path}/${subj}"
log_dir="${subj_dir}/DICOM/LOG"

echo "============================================"
echo " Subject : ${subj}"
echo " Started : $(date)"
echo "============================================"
mkdir -p "${log_dir}"

# Skip if already downloaded
if [ -f "${log_dir}/step0_DONE.txt" ]; then
    echo " [SKIP] already downloaded (step0_DONE.txt present)"
    exit 0
fi

fs_out=$(findsession "${subj}" 2>&1)
echo "${fs_out}" > "${log_dir}/findsession.txt"

# Collect every DICOM PATH (a subject ID may have multiple sessions)
paths=()
while IFS= read -r line; do
    [ -n "$line" ] && paths+=("$line")
done < <(echo "${fs_out}" | grep '^PATH' | awk '{print $NF}')

n=${#paths[@]}
if [ "$n" -eq 0 ]; then
    echo " [SKIP] no DICOM path / no access for ${subj}"
    date > "${log_dir}/step0_ERROR.txt"
    exit 0
fi

idx=0; ok=0
for src in "${paths[@]}"; do
    idx=$((idx+1))
    nn=$(printf '%02d' "$idx")
    if [ "$n" -eq 1 ]; then
        dest="${subj_dir}/DICOM/raw"
    else
        dest="${subj_dir}/DICOM/raw_${nn}"
    fi
    if [ ! -d "$src" ] || [ ! -r "$src" ]; then
        echo " [SKIP] no access to session ${nn}: ${src}"
        continue
    fi
    echo " DICOM source [${idx}/${n}]: ${src}  ->  ${dest}"
    mkdir -p "${dest}"
    rsync -av --progress "${src}/" "${dest}/" 2>&1 | tee "${log_dir}/rsync_${nn}.log"
    if [ "${PIPESTATUS[0]}" -eq 0 ]; then
        ok=$((ok+1))
    else
        echo " [WARN] rsync failed for session ${nn}"
    fi
done

if [ "$ok" -gt 0 ]; then
    date > "${log_dir}/step0_DONE.txt"
    echo " Done: ${subj} (${ok}/${n} session(s) copied)"
else
    date > "${log_dir}/step0_ERROR.txt"
    echo " [FAIL] no accessible sessions for ${subj}"
fi
exit 0
HELPER
chmod +x "${helper}"

# ── Spawn one screen session per subject ────────────────────────────────────────
while IFS= read -r subjID; do
    [ -z "${subjID}" ] && continue
    echo " Spawning screen session for: ${subjID}"
    screen -S "${subjID}-download" -dm bash "${helper}" "${subjID}" "${out_path}"
done < "${subjlist}"

echo -e "\n All screen sessions spawned."
echo -e " Monitor  : screen -ls"
echo -e " Attach   : screen -r <subjID>-download"
echo -e " (helper:  ${helper})"
