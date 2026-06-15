#!/bin/bash

# ============================================================================
# step10_ROI.sh   (Step 10)
# Created by Mario Murakami
#
# At a chosen coordinate (e.g. a peak from the step09 significance map):
#   1. Extract each subject's single-voxel wcon value          -> CSV
#   2. Build a 5 mm sphere; extract each subject's mean wcon    -> CSV
#   3. Write 5 mm and 10 mm sphere masks (NIfTI)
#   4. Mask a manually-selected con_0001.nii with the 10 mm sphere
#
# Usage:
#   bash step10_ROI.sh <X> <Y> <Z> <wcon_dir> <output_dir> \
#        [con_file] [r_small] [r_large] [coord_mode] [python_exe] \
#        [group_con] [group_mask]
#
# Required:
#   X Y Z         coordinate (MNI mm by default; see coord_mode)
#   wcon_dir      folder of per-subject wcon images (<subject>.nii),
#                 e.g. a step08 Part-1 task folder
#   output_dir    where CSV + spheres + masked con are written
#
# Optional positional arguments:
#   con_file      con image to mask with the large sphere (manually selected)
#   r_small       small sphere radius mm (per-subject mean)   default: 5
#   r_large       large sphere radius mm (mask the con)        default: 10
#   coord_mode    'mm' (MNI mm) or 'voxel' (indices)           default: mm
#   python_exe    Python interpreter                           default: python3
#   group_con     group-comparison contrast to mask (e.g. spmT_0001.nii)
#   group_mask    mask to apply to group_con (e.g. a 10 mm sphere mask).
#                 Both group_con and group_mask are manually selected.
#   sig_mask      optional significance mask (e.g. a step09 corrected *_mask.nii):
#                 adds a per-subject sphere mean restricted to significant voxels.
# ============================================================================

set -euo pipefail

if [ $# -lt 5 ]; then
    echo "Usage: $0 <X> <Y> <Z> <wcon_dir> <output_dir> [con_file] [r_small] [r_large] [coord_mode] [python_exe]"
    echo "Example (MNI mm): $0 -6 -40 -20 /path/tasks/BlockStim /path/roi_out /path/con_0001.nii 5 10 mm"
    exit 1
fi

X="$1"; Y="$2"; Z="$3"
WCON_DIR="$4"
OUTPUT_DIR="$5"
CON_FILE="${6:-}"
R_SMALL="${7:-5}"
R_LARGE="${8:-10}"
COORD_MODE="${9:-mm}"
PYTHON="${10:-python3}"
GROUP_CON="${11:-}"
GROUP_MASK="${12:-}"
SIG_MASK="${13:-}"     # optional step09 corrected mask
ROI_MASK="${14:-}"     # optional whole-mask ROI (e.g. brainstem) — coordinate-free mean
ROI_ATLAS="${15:-}"    # optional labeled atlas → per-nucleus means
ROI_LABELS="${16:-}"   # space/comma list of atlas label values (with --roi-atlas)
ROI_LABEL_NAMES="${17:-}"  # optional space/comma names for the labels (same order)

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
EXTRACTOR="${SCRIPT_DIR}/utility/roi_extract.py"

echo "============================================"
echo " STEP 10 — ROI extraction + spheres"
echo " Coordinate:  ${X} ${Y} ${Z}  (${COORD_MODE})"
echo " wcon dir:    ${WCON_DIR}"
echo " Output:      ${OUTPUT_DIR}"
echo " Radii:       ${R_SMALL}mm (values), ${R_LARGE}mm (con mask)"
[ -n "${CON_FILE}" ] && echo " Con to mask: ${CON_FILE}"
echo " Date:        $(date)"
echo "============================================"
echo ""

if [ ! -d "${WCON_DIR}" ]; then
    echo "ERROR: wcon directory not found: ${WCON_DIR}"
    exit 1
fi
if [ ! -f "${EXTRACTOR}" ]; then
    echo "ERROR: roi_extract.py not found: ${EXTRACTOR}"
    exit 1
fi

[ -f "${SCRIPT_DIR}/utility/fmriprep_env.sh" ] && { set +eu; source "${SCRIPT_DIR}/utility/fmriprep_env.sh"; set -eu; }

mkdir -p "${OUTPUT_DIR}"

# Build optional args
# --coord only when X/Y/Z are all provided (ROI-mask/atlas runs can be coordinate-free)
coord_arg=""
if [ -n "${X}" ] && [ -n "${Y}" ] && [ -n "${Z}" ]; then
    coord_arg="--coord ${X} ${Y} ${Z}"
fi
voxel_flag=""
[ "${COORD_MODE}" = "voxel" ] && voxel_flag="--voxel"
con_arg=""
[ -n "${CON_FILE}" ] && con_arg="--con ${CON_FILE}"
gcon_arg=""
[ -n "${GROUP_CON}" ]  && gcon_arg="--group-con ${GROUP_CON}"
gmask_arg=""
[ -n "${GROUP_MASK}" ] && gmask_arg="--group-mask ${GROUP_MASK}"
sigmask_arg=""
[ -n "${SIG_MASK}" ] && sigmask_arg="--sig-mask ${SIG_MASK}"
roimask_arg=""
[ -n "${ROI_MASK}" ] && roimask_arg="--roi-mask ${ROI_MASK}"
roiatlas_arg=""
[ -n "${ROI_ATLAS}" ] && roiatlas_arg="--roi-atlas ${ROI_ATLAS}"
roilabels_arg=""
[ -n "${ROI_LABELS}" ] && roilabels_arg="--roi-labels ${ROI_LABELS//,/ }"
roinames_arg=""
[ -n "${ROI_LABEL_NAMES}" ] && roinames_arg="--roi-label-names ${ROI_LABEL_NAMES//,/ }"

"${PYTHON}" "${EXTRACTOR}" \
    ${coord_arg} \
    --wcon-dir "${WCON_DIR}" \
    --output-dir "${OUTPUT_DIR}" \
    --radii "${R_SMALL}" "${R_LARGE}" \
    ${voxel_flag} ${con_arg} ${gcon_arg} ${gmask_arg} ${sigmask_arg} \
    ${roimask_arg} ${roiatlas_arg} ${roilabels_arg} ${roinames_arg}
rc=$?

echo ""
if [ ${rc} -eq 0 ]; then
    echo "============================================"
    echo " ROI extraction complete ✓"
    echo " Output: ${OUTPUT_DIR}"
    echo "   roi_values.csv            (voxel + ${R_SMALL}mm-sphere mean per subject)"
    echo "   sphere_${R_SMALL}mm_*.nii / sphere_${R_LARGE}mm_*.nii   (masks)"
    [ -n "${CON_FILE}" ]  && echo "   *_masked_${R_LARGE}mm.nii   (con masked by ${R_LARGE}mm sphere)"
    [ -n "${GROUP_CON}" ] && echo "   *_groupmasked.nii          (group contrast masked)"
    echo "============================================"
else
    echo "ERROR: roi_extract.py exited with code ${rc}"
    exit ${rc}
fi
