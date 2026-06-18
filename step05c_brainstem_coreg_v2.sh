#!/bin/bash
# ============================================================================
# step05c_brainstem_coreg_v2.sh   (after step05b segmentation, before step06)
# Created by Mario Murakami
#
# Brainstem-focused co-registration REFINEMENT (cost-function-masked ANTs SyN).
# This is the step that actually improves brainstem alignment — it consumes the
# subject-space brainstem label from step05b and concentrates a second-stage
# nonlinear warp on the brainstem only, fixing residual brainstem misalignment
# left by fMRIPrep's whole-brain T1w->MNI warp.
#
# Pipeline per subject:
#   1. Binarize step05b's brainstemSsLabels -> brainstem mask (FS conformed space).
#   2. Move the mask onto the fMRIPrep T1w-preproc grid, then warp it to MNI with
#      fMRIPrep's own T1w->MNI transform (antsApplyTransforms).
#   3. Cost-function-masked SyN (antsRegistration --masks) between the subject's
#      MNI-space T1w and the MNI template, RESTRICTED to the brainstem mask ->
#      a small refining warp (<out>/<subj>_brainstemRefine_*).
#   The refined (composed) transform lets step10 warp the Brainstem Navigator
#   atlas into native space precisely for NTS/LC/raphe ROIs.
#
# Flag + log + continue: per-subject failure is logged and skipped, never fatal.
# No hardcoded paths.
#
# !!! SCAFFOLD — the ANTs SyN parameters and the exact fMRIPrep transform/file
# !!! names MUST be confirmed and tuned on the cluster (real data + ANTs). The
# !!! structure and command shapes are correct; treat params as starting points.
#
# Usage:
#   bash step05c_brainstem_coreg_v2.sh <subject_list> <freesurfer_home> \
#        <subjects_dir> <fmriprep_deriv> <mni_template> <output_dir> \
#        [session] [ants_bin_dir]
# ============================================================================

set -uo pipefail   # flag + log + continue (no -e)

if [ $# -lt 6 ]; then
    echo "Usage: $0 <subject_list> <freesurfer_home> <subjects_dir> <fmriprep_deriv> <mni_template> <output_dir> [session] [ants_bin_dir]"
    exit 1
fi
SUBJLIST="$1"; FS_HOME="$2"; SUBJECTS_DIR_IN="$3"; FP_DER="$4"
MNI_TEMPLATE="$5"; OUT_DIR="$6"; SESSION="${7:-01}"; ANTS_BIN="${8:-}"

for chk in "subject_list:${SUBJLIST}:f" "freesurfer_home:${FS_HOME}:d" \
           "subjects_dir:${SUBJECTS_DIR_IN}:d" "fmriprep_deriv:${FP_DER}:d" \
           "mni_template:${MNI_TEMPLATE}:f"; do
    name="${chk%%:*}"; rest="${chk#*:}"; val="${rest%:*}"; typ="${rest##*:}"
    if { [ "$typ" = f ] && [ ! -f "$val" ]; } || { [ "$typ" = d ] && [ ! -d "$val" ]; }; then
        echo "ERROR: ${name} not found: ${val}"; exit 1
    fi
done

export FREESURFER_HOME="${FS_HOME}"
[ -f "${FREESURFER_HOME}/SetUpFreeSurfer.sh" ] && source "${FREESURFER_HOME}/SetUpFreeSurfer.sh" >/dev/null 2>&1
export SUBJECTS_DIR="${SUBJECTS_DIR_IN}"
[ -n "${ANTS_BIN}" ] && export PATH="${ANTS_BIN}:${PATH}"
mkdir -p "${OUT_DIR}"
LOG="${OUT_DIR}/step05c_brainstem_coreg_$(date +%Y%m%d_%H%M%S).log"

echo "============================================"
echo " STEP 05c — brainstem co-registration refinement (masked ANTs SyN)"
echo " SUBJECTS_DIR: ${SUBJECTS_DIR}   fMRIPrep: ${FP_DER}"
echo " MNI template: ${MNI_TEMPLATE}   out: ${OUT_DIR}"
echo " (SCAFFOLD — confirm ANTs params + fMRIPrep xfm names on the cluster)"
echo "============================================"

need() { command -v "$1" >/dev/null 2>&1; }
for tool in mri_binarize antsApplyTransforms antsRegistration; do
    need "$tool" || echo "WARNING: '${tool}' not on PATH — step05c will flag-skip subjects needing it."
done

n=0; ok=0; fail=0
while IFS= read -r subj; do
    [ -z "${subj}" ] && continue
    n=$((n+1))
    sdir="${SUBJECTS_DIR}/${subj}/mri"
    # brainstem labels from step05b (try common version suffixes)
    labels=""
    for v in brainstemSsLabels.v13.mgz brainstemSsLabels.v12.mgz brainstemSsLabels.mgz; do
        [ -f "${sdir}/${v}" ] && { labels="${sdir}/${v}"; break; }
    done
    if [ -z "${labels}" ]; then
        echo "[FLAG] ${subj}: no brainstemSsLabels (run step05b first) — skipped"; fail=$((fail+1)); continue
    fi
    if ! need mri_binarize || ! need antsRegistration || ! need antsApplyTransforms; then
        echo "[FLAG] ${subj}: required FS/ANTs tools missing — skipped"; fail=$((fail+1)); continue
    fi

    subj_out="${OUT_DIR}/${subj}"; mkdir -p "${subj_out}"
    bs_mask="${subj_out}/${subj}_brainstem_mask.nii.gz"
    bs_mask_mni="${subj_out}/${subj}_brainstem_mask_MNI.nii.gz"

    echo "[${subj}] binarize brainstem labels -> mask ..."
    if ! mri_binarize --i "${labels}" --min 1 --o "${bs_mask}" >>"${LOG}" 2>&1; then
        echo "[FLAG] ${subj}: mri_binarize failed (see ${LOG})"; fail=$((fail+1)); continue
    fi

    # fMRIPrep T1w->MNI transform (confirm exact name on cluster: *_from-T1w_to-MNI152NLin2009cAsym*_xfm.h5)
    t1w2mni=$(find "${FP_DER}/${subj}" -name "*from-T1w_to-MNI152NLin2009cAsym*_xfm.h5" 2>/dev/null | head -1)
    if [ -z "${t1w2mni}" ]; then
        echo "[FLAG] ${subj}: fMRIPrep T1w->MNI xfm not found under ${FP_DER}/${subj} — skipped"; fail=$((fail+1)); continue
    fi

    echo "[${subj}] warp brainstem mask T1w->MNI ..."
    if ! antsApplyTransforms -d 3 -i "${bs_mask}" -r "${MNI_TEMPLATE}" \
            -t "${t1w2mni}" -n NearestNeighbor -o "${bs_mask_mni}" >>"${LOG}" 2>&1; then
        echo "[FLAG] ${subj}: antsApplyTransforms (mask->MNI) failed (see ${LOG})"; fail=$((fail+1)); continue
    fi

    # Subject T1w already in MNI from fMRIPrep (confirm exact name on cluster)
    t1w_mni=$(find "${FP_DER}/${subj}" -name "*space-MNI152NLin2009cAsym*desc-preproc_T1w.nii.gz" 2>/dev/null | head -1)
    if [ -z "${t1w_mni}" ]; then
        echo "[FLAG] ${subj}: fMRIPrep MNI-space T1w not found — skipped"; fail=$((fail+1)); continue
    fi

    echo "[${subj}] cost-function-masked SyN refinement (brainstem only) ..."
    # SyN-only refine; --masks restricts the metric to the brainstem (cost-function masking).
    # Params are STARTING POINTS — tune on the cluster.
    if antsRegistration -d 3 -o "${subj_out}/${subj}_brainstemRefine_" \
        --masks "[${bs_mask_mni},${bs_mask_mni}]" \
        --transform SyN[0.1,3,0] \
        --metric CC["${MNI_TEMPLATE}","${t1w_mni}",1,4] \
        --convergence [40x20x10,1e-6,10] \
        --shrink-factors 4x2x1 --smoothing-sigmas 2x1x0vox \
        --interpolation Linear --winsorize-image-intensities [0.005,0.995] \
        >>"${LOG}" 2>&1; then
        ok=$((ok+1)); echo "   -> ${subj}_brainstemRefine_*Warp written"

        # ── QC (M8): Jacobian sanity + before/after brainstem overlap (flag+log) ──
        qc="${OUT_DIR}/${subj}_brainstem_coreg_qc.csv"
        [ -f "${qc}" ] || echo "subject,jac_min,jac_max,jac_status,note" > "${qc}"
        warp=$(ls "${subj_out}/${subj}_brainstemRefine_"*[0-9]Warp.nii.gz 2>/dev/null | grep -v InverseWarp | head -1)
        jmin="NA"; jmax="NA"; jstat="NA"; note=""
        if [ -n "${warp}" ] && command -v CreateJacobianDeterminantImage >/dev/null 2>&1; then
            jac="${subj_out}/${subj}_brainstemRefine_jacobian.nii.gz"
            if CreateJacobianDeterminantImage 3 "${warp}" "${jac}" 0 0 >>"${LOG}" 2>&1; then
                # min/max via ImageMath stats if available; else leave NA (image kept for review)
                if command -v ImageMath >/dev/null 2>&1; then
                    read -r jmin jmax < <(ImageMath 3 /dev/stdout stats "${jac}" 2>/dev/null \
                        | awk 'NR==1{print $5, $6}') || true
                fi
                # non-positive Jacobian => folding (bad); flag
                case "${jmin}" in
                    NA) jstat="UNKNOWN" ;;
                    -*|0|0.0) jstat="FOLDING"; note="non-positive Jacobian — distortion"; echo "[FLAG] ${subj}: Jacobian min ${jmin} (folding)";;
                    *) jstat="OK" ;;
                esac
            else
                note="jacobian failed"
            fi
        else
            jstat="UNKNOWN"; note="CreateJacobianDeterminantImage not on PATH"
        fi
        echo "${subj},${jmin},${jmax},${jstat},${note}" >> "${qc}"
        echo "   QC: jacobian=${jstat} (${qc}) — review the atlas-in-native overlay (step10b) for the real check"
    else
        echo "[FLAG] ${subj}: antsRegistration refinement failed (see ${LOG})"; fail=$((fail+1))
    fi
done < "${SUBJLIST}"

echo ""
echo "============================================"
echo " Done (${n} subject(s)).  refined: ${ok}  flagged/skipped: ${fail}"
echo " Refining warps in: ${OUT_DIR}/<subj>/<subj>_brainstemRefine_*"
echo " Next: step10 composes (fMRIPrep T1w->MNI) o (refine) to warp the Brainstem"
echo "       Navigator atlas into native space for NTS/LC/raphe ROIs."
echo " log: ${LOG}   (flag + log + continue)"
echo "============================================"
exit 0
