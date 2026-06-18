#!/bin/bash
# ============================================================================
# step10b_atlas_native_roi_v2.sh   (native-space brainstem-nuclei ROIs)
# Created by Mario Murakami
#
# Brings a brainstem-nuclei atlas (MNI; e.g. Brainstem Navigator) into each
# subject's NATIVE (T1w) space through the COMPOSED transform
#     atlas(MNI)  --(fMRIPrep MNI->T1w)--  ∘  --(step05c brainstem refine)-->  native
# then extracts per-nucleus means from that subject's native-space contrast
# (reusing utility/roi_extract.py). This is the path that makes the step05c
# brainstem co-registration refinement count: ROIs are read where the BOLD is
# BBR-aligned and the atlas is placed with the refined brainstem correspondence.
#
# Requires native-space first-level output (run step07 with Space=T1w or both).
#
# !!! SCAFFOLD — the TRANSFORM DIRECTION/ORDER below is the best-reasoned chain
# !!! but MUST be verified on the cluster with an overlay (atlas-in-native on the
# !!! subject T1w/BOLD). fMRIPrep ships both from-MNI_to-T1w and from-T1w_to-MNI
# !!! .h5 files; step05c writes *_brainstemRefine_*Warp and *InverseWarp. Confirm
# !!! which combination lands the atlas correctly before trusting ROI values.
#
# Flag + log + continue: per-subject failure is logged and skipped, never fatal.
# No hardcoded paths.
#
# Usage:
#   bash step10b_atlas_native_roi_v2.sh <subject_list> <fmriprep_deriv> <coreg_dir> \
#        <atlas_mni> <native_con_root> <con_glob> <output_dir> <python_exe> \
#        [labels] [names] [ants_bin_dir]
#
#   coreg_dir        step05c output root (<subj>/<subj>_brainstemRefine_*)
#   native_con_root  root holding per-subject native (T1w-space) contrasts
#   con_glob         glob for the native contrast under <native_con_root>/<subj>/
#                    (e.g. '*/con_0001.nii' — T1w-space, NOT wcon/MNI)
#   labels/names     atlas label values / names (space or comma list; optional)
# ============================================================================

set -uo pipefail   # flag + log + continue (no -e)

if [ $# -lt 8 ]; then
    echo "Usage: $0 <subject_list> <fmriprep_deriv> <coreg_dir> <atlas_mni> <native_con_root> <con_glob> <output_dir> <python_exe> [labels] [names] [ants_bin_dir]"
    exit 1
fi
SUBJLIST="$1"; FP_DER="$2"; COREG_DIR="$3"; ATLAS_MNI="$4"
CON_ROOT="$5"; CON_GLOB="$6"; OUT_DIR="$7"; PYTHON="$8"
LABELS="${9:-}"; NAMES="${10:-}"; ANTS_BIN="${11:-}"
# Which composed transform chain to use for the canonical atlas-in-native + extraction.
# The chain is genuinely ambiguous until validated by a NUCLEUS-scale overlay (review M1),
# so we always EMIT all candidates and CHAIN selects which one is used:
#   C1 = fMRIPrep MNI->T1w only (no step05c refine; baseline)
#   C2 = refine INVERSE then MNI->T1w   (default)
#   C3 = refine FORWARD then MNI->T1w
CHAIN="${12:-C2}"
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

for chk in "subject_list:${SUBJLIST}:f" "fmriprep_deriv:${FP_DER}:d" \
           "coreg_dir:${COREG_DIR}:d" "atlas_mni:${ATLAS_MNI}:f" \
           "native_con_root:${CON_ROOT}:d"; do
    name="${chk%%:*}"; rest="${chk#*:}"; val="${rest%:*}"; typ="${rest##*:}"
    if { [ "$typ" = f ] && [ ! -f "$val" ]; } || { [ "$typ" = d ] && [ ! -d "$val" ]; }; then
        echo "ERROR: ${name} not found: ${val}"; exit 1
    fi
done
[ -n "${ANTS_BIN}" ] && export PATH="${ANTS_BIN}:${PATH}"
mkdir -p "${OUT_DIR}"
LOG="${OUT_DIR}/step10b_atlas_native_$(date +%Y%m%d_%H%M%S).log"
COHORT="${OUT_DIR}/group_brainstem_nuclei_native.csv"; : > "${COHORT}"

echo "============================================"
echo " STEP 10b — native-space brainstem-nuclei ROIs (atlas via composed warp)"
echo " atlas(MNI): ${ATLAS_MNI}"
echo " fMRIPrep:   ${FP_DER}   coreg(step05c): ${COREG_DIR}"
echo " native con: ${CON_ROOT}/<subj>/${CON_GLOB}"
echo " out: ${OUT_DIR}"
echo " (SCAFFOLD — verify the transform chain with an overlay on the cluster)"
echo "============================================"

command -v antsApplyTransforms >/dev/null 2>&1 || echo "WARNING: antsApplyTransforms not on PATH — subjects will flag-skip."

n=0; ok=0; fail=0
while IFS= read -r subj; do
    [ -z "${subj}" ] && continue
    n=$((n+1))
    so="${OUT_DIR}/${subj}"; mkdir -p "${so}"

    # native T1w reference (T1w space, NOT MNI)
    native_t1w=$(find "${FP_DER}/${subj}" -name "*desc-preproc_T1w.nii.gz" 2>/dev/null \
                 | grep -v "space-MNI" | head -1)
    # fMRIPrep MNI->T1w transform
    mni2t1w=$(find "${FP_DER}/${subj}" -name "*from-MNI152NLin2009cAsym*to-T1w*xfm.h5" 2>/dev/null | head -1)
    # step05c refining warp — BOTH directions (which one is correct is decided by overlay)
    refine_inv=$(find "${COREG_DIR}/${subj}" -name "*brainstemRefine_*InverseWarp.nii.gz" 2>/dev/null | head -1)
    refine_fwd=$(find "${COREG_DIR}/${subj}" -name "*brainstemRefine_*[0-9]Warp.nii.gz" 2>/dev/null | grep -v InverseWarp | head -1)
    # subject native contrast
    con=$(find "${CON_ROOT}/${subj}" -path "*/${CON_GLOB#*/}" 2>/dev/null | head -1)
    [ -z "${con}" ] && con=$(ls ${CON_ROOT}/${subj}/${CON_GLOB} 2>/dev/null | head -1)

    if [ -z "${native_t1w}" ] || [ -z "${mni2t1w}" ]; then
        echo "[FLAG] ${subj}: missing native T1w or fMRIPrep MNI->T1w xfm — skipped"; fail=$((fail+1)); continue
    fi
    if [ -z "${con}" ]; then
        echo "[FLAG] ${subj}: native contrast (${CON_GLOB}) not found — skipped (run step07 Space=T1w)"; fail=$((fail+1)); continue
    fi
    if ! command -v antsApplyTransforms >/dev/null 2>&1; then
        echo "[FLAG] ${subj}: antsApplyTransforms missing — skipped"; fail=$((fail+1)); continue
    fi

    # ── Emit ALL candidate chains so a nucleus-scale overlay can pick the right one ──
    # (M1: the step05c refine warp is estimated in MNI space; its correct direction/order
    #  is ambiguous on paper. ANTs applies -t in REVERSE — last -t hits the point first.)
    declare -A CAND
    CAND[C1]="-t ${mni2t1w}"
    [ -n "${refine_inv}" ] && CAND[C2]="-t ${refine_inv} -t ${mni2t1w}"
    [ -n "${refine_fwd}" ] && CAND[C3]="-t ${refine_fwd} -t ${mni2t1w}"
    echo "[${subj}] emitting candidate atlas-in-native chains: ${!CAND[*]}"
    for c in "${!CAND[@]}"; do
        outc="${so}/${subj}_atlas-in-native_${c}.nii.gz"
        # shellcheck disable=SC2086
        if antsApplyTransforms -d 3 -i "${ATLAS_MNI}" -r "${native_t1w}" \
                ${CAND[$c]} -n NearestNeighbor -o "${outc}" >>"${LOG}" 2>&1; then
            echo "   -> ${c}: ${outc}"
        else
            echo "[FLAG] ${subj}: candidate ${c} failed (see ${LOG})"
        fi
    done

    # Canonical atlas-in-native = the selected CHAIN (default C2); fall back if absent.
    native_atlas="${so}/${subj}_atlas-in-native_${CHAIN}.nii.gz"
    if [ ! -f "${native_atlas}" ]; then
        native_atlas="${so}/${subj}_atlas-in-native_C1.nii.gz"
        echo "[FLAG] ${subj}: chain ${CHAIN} unavailable — using C1 (no refine). Verify overlay."
    fi
    if [ ! -f "${native_atlas}" ]; then
        echo "[FLAG] ${subj}: no candidate atlas produced — skipped"; fail=$((fail+1)); continue
    fi
    cp -f "${native_atlas}" "${so}/${subj}_atlas-in-native.nii.gz"
    native_atlas="${so}/${subj}_atlas-in-native.nii.gz"
    echo "   -> canonical (${CHAIN}): ${native_atlas}  !!! overlay-check before trusting"

    # Reuse roi_extract.py for the per-nucleus extraction in native space:
    # put the subject's native con in a temp dir as <subj>.nii so roi_extract treats it
    # as one "subject", and pass the native atlas.
    tmpd="${so}/_con"; mkdir -p "${tmpd}"; cp -f "${con}" "${tmpd}/${subj}.nii"
    lbl_arg=""; [ -n "${LABELS}" ] && lbl_arg="--roi-labels ${LABELS//,/ }"
    nm_arg="";  [ -n "${NAMES}" ]  && nm_arg="--roi-label-names ${NAMES//,/ }"
    if "${PYTHON}" "${SCRIPT_DIR}/utility/roi_extract.py" \
            --wcon-dir "${tmpd}" --wcon-glob "*.nii" \
            --roi-atlas "${native_atlas}" ${lbl_arg} ${nm_arg} \
            --output-dir "${so}" >>"${LOG}" 2>&1; then
        # append this subject's row(s) to the cohort csv
        if [ -f "${so}/roi_values.csv" ]; then
            if [ ! -s "${COHORT}" ]; then head -1 "${so}/roi_values.csv" > "${COHORT}"; fi
            tail -n +2 "${so}/roi_values.csv" >> "${COHORT}"
        fi
        ok=$((ok+1)); echo "   -> per-nucleus means appended"
    else
        echo "[FLAG] ${subj}: roi_extract failed (see ${LOG})"; fail=$((fail+1))
    fi
done < "${SUBJLIST}"

echo ""
echo "============================================"
echo " Done (${n} subject(s)).  ok=${ok}  flagged/skipped=${fail}"
echo " Cohort: ${COHORT}"
echo " Per-subject native atlases: ${OUT_DIR}/<subj>/<subj>_atlas-in-native.nii.gz"
echo " !!! Verify atlas-in-native overlay before trusting ROI values (SCAFFOLD)."
echo " log: ${LOG}   (flag + log + continue)"
echo "============================================"
exit 0
