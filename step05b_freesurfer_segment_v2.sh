#!/bin/bash
# ============================================================================
# step05b_freesurfer_segment_v2.sh   (after step05 recon-all, before step06)
# Created by Mario Murakami
#
# Optional FreeSurfer post-recon-all segmentation extras:
#   - brainstem substructures  (segment_subregions brainstem) ->
#       <SUBJECTS_DIR>/<subj>/mri/brainstemSsLabels*.mgz + volumes.
#       Provides the subject-space brainstem label/mask consumed by step05c
#       (cost-function-masked co-registration refinement).
#   - PGlandsSeg (mri_pglands_seg) -> pituitary (anterior/posterior),
#       infundibulum, pineal volumetry (T1.pglands.mgz + .stats). Needs FS >= 8.1.
#
# These are SEGMENTATION/VOLUMETRY tools — they do NOT change co-registration by
# themselves (the brainstem label feeds step05c, which does). Additive & read-only
# w.r.t. the BOLD pipeline.
#
# Flag + log + continue: a per-subject/per-tool failure is logged and skipped,
# never fatal (exits 0). No hardcoded paths.
#
# Usage:
#   bash step05b_freesurfer_segment_v2.sh <subject_list> <freesurfer_home> \
#        <subjects_dir> [what: both|brainstem|pituitary] [log_dir]
#
#   subject_list     file, one BIDS subject id per line (sub-XXXX)   REQUIRED
#   freesurfer_home  FreeSurfer >= 8.1 home (sourced)                REQUIRED
#   subjects_dir     FreeSurfer SUBJECTS_DIR (recon-all output)      REQUIRED
#   what             both (default) | brainstem | pituitary
#   log_dir          run-log dir (default <subjects_dir>/qc)
# ============================================================================

set -uo pipefail   # NOT -e: we flag + log + continue per subject

if [ $# -lt 3 ]; then
    echo "Usage: $0 <subject_list> <freesurfer_home> <subjects_dir> [both|brainstem|pituitary] [log_dir]"
    exit 1
fi

SUBJLIST="$1"
FS_HOME="$2"
SUBJECTS_DIR_IN="$3"
WHAT="${4:-both}"
LOG_DIR="${5:-${SUBJECTS_DIR_IN}/qc}"

for chk in "SUBJLIST:${SUBJLIST}:f" "FS_HOME:${FS_HOME}:d" "SUBJECTS_DIR:${SUBJECTS_DIR_IN}:d"; do
    name="${chk%%:*}"; rest="${chk#*:}"; val="${rest%:*}"; typ="${rest##*:}"
    if { [ "$typ" = f ] && [ ! -f "$val" ]; } || { [ "$typ" = d ] && [ ! -d "$val" ]; }; then
        echo "ERROR: ${name} not found: ${val}"; exit 1
    fi
done

# ── Source the requested FreeSurfer (>= 8.1) ──────────────────────────────────
export FREESURFER_HOME="${FS_HOME}"
if [ -f "${FREESURFER_HOME}/SetUpFreeSurfer.sh" ]; then
    source "${FREESURFER_HOME}/SetUpFreeSurfer.sh" >/dev/null 2>&1 || \
        echo "WARNING: SetUpFreeSurfer.sh returned non-zero (continuing)."
else
    echo "WARNING: ${FREESURFER_HOME}/SetUpFreeSurfer.sh not found — relying on PATH."
fi
export SUBJECTS_DIR="${SUBJECTS_DIR_IN}"

mkdir -p "${LOG_DIR}"
LOG="${LOG_DIR}/step05b_segment_$(date +%Y%m%d_%H%M%S).log"

echo "============================================"
echo " STEP 05b — FreeSurfer segmentation extras"
echo " FREESURFER_HOME: ${FREESURFER_HOME}"
echo " SUBJECTS_DIR:    ${SUBJECTS_DIR}"
echo " What:            ${WHAT}"
echo " Log:             ${LOG}"
echo " Date:            $(date)"
echo "============================================"

# PGlandsSeg requires FS >= 8.1; detect the binary and flag-skip if absent.
have_pglands=0
command -v mri_pglands_seg >/dev/null 2>&1 && have_pglands=1
# brainstem: prefer the modern command, fall back to the legacy script
bs_cmd=""
if command -v segment_subregions >/dev/null 2>&1; then bs_cmd="segment_subregions"
elif command -v segmentBS.sh   >/dev/null 2>&1; then bs_cmd="segmentBS.sh"; fi

nb_ok=0; nb_fail=0; pit_ok=0; pit_fail=0; pit_skip=0; n_subj=0
while IFS= read -r subj; do
    [ -z "${subj}" ] && continue
    n_subj=$((n_subj+1))
    if [ ! -d "${SUBJECTS_DIR}/${subj}" ]; then
        echo "[FLAG] ${subj}: no recon-all output in SUBJECTS_DIR — skipped"
        continue
    fi

    if [ "${WHAT}" = both ] || [ "${WHAT}" = brainstem ]; then
        echo "[${subj}] brainstem substructures (${bs_cmd:-<none>}) ..."
        if [ -z "${bs_cmd}" ]; then
            echo "[FLAG] ${subj}: no segment_subregions/segmentBS.sh on PATH — brainstem skipped"
            nb_fail=$((nb_fail+1))
        elif [ "${bs_cmd}" = "segment_subregions" ]; then
            if segment_subregions brainstem --cross "${subj}" >>"${LOG}" 2>&1; then
                nb_ok=$((nb_ok+1)); echo "   -> brainstemSsLabels written"
            else
                echo "[FLAG] ${subj}: brainstem segmentation failed (see ${LOG})"; nb_fail=$((nb_fail+1))
            fi
        else
            if segmentBS.sh "${subj}" "${SUBJECTS_DIR}" >>"${LOG}" 2>&1; then
                nb_ok=$((nb_ok+1)); echo "   -> brainstemSsLabels written"
            else
                echo "[FLAG] ${subj}: brainstem segmentation failed (see ${LOG})"; nb_fail=$((nb_fail+1))
            fi
        fi
    fi

    if [ "${WHAT}" = both ] || [ "${WHAT}" = pituitary ]; then
        if [ "${have_pglands}" -eq 1 ]; then
            echo "[${subj}] pituitary/pineal (mri_pglands_seg) ..."
            if mri_pglands_seg --s "${subj}" --sd "${SUBJECTS_DIR}" >>"${LOG}" 2>&1; then
                pit_ok=$((pit_ok+1)); echo "   -> T1.pglands.mgz written"
            else
                echo "[FLAG] ${subj}: PGlandsSeg failed (see ${LOG})"; pit_fail=$((pit_fail+1))
            fi
        else
            echo "[FLAG] ${subj}: mri_pglands_seg not found — needs FreeSurfer >= 8.1; pituitary skipped"
            pit_skip=$((pit_skip+1))
        fi
    fi
done < "${SUBJLIST}"

echo ""
echo "============================================"
echo " Done (${n_subj} subject(s))."
echo "   brainstem:  ok=${nb_ok}  failed=${nb_fail}"
echo "   pituitary:  ok=${pit_ok}  failed=${pit_fail}  skipped(no FS8.1)=${pit_skip}"
echo "   log: ${LOG}"
echo " (flag + log + continue — review FLAG lines above)"
echo "============================================"
exit 0
