#!/bin/bash

# ============================================================================
# step05_fmriprep_v2.sh   (NEW ORDER: RETROICOR → fMRIPrep)
# Created by Mario Murakami
#
# STEP 5 — fMRIPrep on the RETROICOR-corrected BOLD.
#
# In the reordered pipeline, physiological noise is removed in NATIVE space by
# RETROICOR (step04) BEFORE fMRIPrep. This script:
#
#   PART 1    Assemble a corrected BIDS dataset (utility/assemble_corrected_bids.py)
#             Mirrors sourcedata into <corrected_bids_dir>, replacing each func BOLD
#             with its *_retro-corrected.nii.gz (anat/fmap symlinked, JSONs copied).
#             Skip with --no-assemble if corrected BIDS is already built.
#
#   PART 1.5  BIDS Validator (non-fatal warning if not installed).
#
#   PART 2    fMRIPrep (local, sequential) on the CORRECTED BIDS dataset.
#             Slice-timing correction is done here by fMRIPrep; RETROICOR upstream
#             used slice timing for physio phase on un-shifted data.
#
#   PART 3    QC — mean FD + MNI-BOLD registration check; writes JSON log.
#
# Usage:
#   bash step05_fmriprep_v2.sh \
#       <bids_subj_list> <raw_bids_dir> <corrected_bids_dir> <derivatives_dir> \
#       <fs_dir> <work_dir> <fmriprep_simg> <fs_license> \
#       [python_exe] [output_spaces] [mem_mb] [env_script] \
#       [extra_fmriprep_flags...]
#
# Required arguments ($1–$8):
#   $1  bids_subj_list       BIDS subject list (.txt, one sub-XX per line)
#   $2  raw_bids_dir         Raw BIDS sourcedata root (step01 output)
#   $3  corrected_bids_dir   Corrected BIDS root (RETROICOR-corrected; fMRIPrep input)
#   $4  derivatives_dir      fMRIPrep derivatives output dir
#   $5  fs_dir               FreeSurfer subjects dir
#   $6  work_dir             fMRIPrep working dir (intermediate files; safe to delete post-QC)
#   $7  fmriprep_simg        Singularity image (.simg or .sif)
#   $8  fs_license           FreeSurfer license file
#
# Optional arguments ($9–$12):
#   $9  python_exe           Python interpreter (default: python3)
#   $10 output_spaces        Space-separated output spaces (default: "T1w MNI152NLin2009cAsym")
#   $11 mem_mb               Memory ceiling in MB (default: 50000)
#   $12 env_script           Shell script to source before running (sets up Singularity, FSL, etc.)
#
# Remaining arguments ($13+):
#   Passed verbatim to fMRIPrep inside Singularity (e.g. --skip-bids-validation --cifti-output).
#   The special flag --no-assemble skips PART 1 (corrected BIDS already built).
#
# NOTE: Must run on the cluster — requires Singularity and /autofs mounts.
#       SubjectListBIDS.txt is produced by step01 (BIDS conversion).
# ============================================================================

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"

# ── Required arguments ────────────────────────────────────────────────────────
if [ $# -lt 8 ]; then
    echo "Usage: $0 bids_subj_list raw_bids_dir corrected_bids_dir derivatives_dir"
    echo "            fs_dir work_dir fmriprep_simg fs_license"
    echo "            [python_exe] [output_spaces] [mem_mb] [env_script]"
    echo "            [extra_fmriprep_flags...]"
    exit 1
fi

bids_subj_list="${1}"
raw_bids_dir="${2}"
corrected_bids_dir="${3}"
derivatives_dir="${4}"
fs_dir="${5}"
work_dir="${6}"
fmriprep_simg="${7}"
fs_license="${8}"

# ── Optional arguments ────────────────────────────────────────────────────────
python_exe="${9:-python3}"
output_spaces="${10:-T1w MNI152NLin2009cAsym}"
mem_mb="${11:-50000}"
env_script="${12:-}"

# ── Source environment script if provided ─────────────────────────────────────
if [ -n "${env_script}" ] && [ -f "${env_script}" ]; then
    # shellcheck disable=SC1090
    source "${env_script}"
fi

# ── Parse remaining flags ($13+) ─────────────────────────────────────────────
# --no-assemble is consumed here; all other flags are forwarded to fMRIPrep.
NO_ASSEMBLE=0
extra_opts=()
for arg in "${@:13}"; do
    if [[ "${arg}" == "--no-assemble" ]]; then
        NO_ASSEMBLE=1
    else
        extra_opts+=("${arg}")
    fi
done

# ── Derived paths ─────────────────────────────────────────────────────────────
assembler="${SCRIPT_DIR}/utility/assemble_corrected_bids.py"
step13_script="${SCRIPT_DIR}/step13_preproc_optional_check_pre-post.py"

echo "============================================"
echo " Created by Mario Murakami"
echo " STEP 5 — fMRIPrep on RETROICOR-corrected BOLD"
echo " Date: $(date)"
echo "============================================"
echo ""
echo " BIDS subject list: ${bids_subj_list}"
echo " Raw BIDS:          ${raw_bids_dir}"
echo " Corrected BIDS:    ${corrected_bids_dir}   (fMRIPrep input)"
echo " Derivatives:       ${derivatives_dir}      (unchanged location)"
echo " FreeSurfer:        ${fs_dir}"
echo " Work dir:          ${work_dir}"
echo " Singularity:       ${fmriprep_simg}"
echo " FS license:        ${fs_license}"
echo " Python:            ${python_exe}"
echo " Output spaces:     ${output_spaces}"
echo " Memory:            ${mem_mb} MB"
[ -n "${env_script}" ] && echo " Env script:        ${env_script}"
[ ${NO_ASSEMBLE} -eq 1 ] && echo " --no-assemble:     skipping PART 1"
[ ${#extra_opts[@]} -gt 0 ] && echo " Extra fMRIPrep flags: ${extra_opts[*]}"
echo ""

# ── Validate ─────────────────────────────────────────────────────────────────
if [ ! -f "${bids_subj_list}" ]; then
    echo "ERROR: BIDS subject list not found: ${bids_subj_list}"
    echo "  Generate it in step01 (BIDS conversion)."
    exit 1
fi

if [ ! -f "${fmriprep_simg}" ]; then
    echo "ERROR: Singularity image not found: ${fmriprep_simg}"
    exit 1
fi

if [ ! -f "${fs_license}" ]; then
    echo "ERROR: FreeSurfer license not found: ${fs_license}"
    exit 1
fi

# ── PART 1: Assemble the RETROICOR-corrected BIDS dataset ────────────────────
echo "============================================"
echo " PART 1: Assemble corrected BIDS"
echo "============================================"

if [ ${NO_ASSEMBLE} -eq 1 ]; then
    echo " Skipped (--no-assemble). Assuming corrected BIDS is already built."
else
    if [ ! -f "${assembler}" ]; then
        echo "ERROR: assembler not found: ${assembler}"
        exit 1
    fi
    # Corrected-BIDS audit log (Task 13, flag + log + continue). Fresh each run.
    audit_log="${derivatives_dir}/qc/corrected_bids_audit.csv"
    mkdir -p "$(dirname "${audit_log}")"
    rm -f "${audit_log}"
    while IFS= read -r subject; do
        [ -z "${subject}" ] && continue
        echo "  ${subject}: assembling corrected BIDS..."
        # Assembler is non-fatal: it logs issues/skips to the audit CSV and exits 0.
        # The || guard only catches a genuine crash; we still continue either way.
        "${python_exe}" "${assembler}" "${raw_bids_dir}" "${subject}" "${corrected_bids_dir}" \
            --audit-log "${audit_log}" \
            || echo "  WARNING: assembler crashed for ${subject} (logged; continuing)"
    done < "${bids_subj_list}"
    echo " Corrected BIDS ready: ${corrected_bids_dir}"
    if [ -f "${audit_log}" ]; then
        echo " Corrected-BIDS audit log: ${audit_log}"
        flagged=$(awk -F, 'NR>1 && $3+0>0 {print "   - "$1" ("$3" issue(s))"}' "${audit_log}")
        if [ -n "${flagged}" ]; then
            echo " Flagged subjects (advisory — review before trusting fMRIPrep):"
            echo "${flagged}"
        else
            echo " No corrected-BIDS issues flagged."
        fi
    fi
fi

# ── PART 1.5: BIDS Validator ──────────────────────────────────────────────────
echo ""
echo "============================================"
echo " PART 1.5: BIDS Validator"
echo "============================================"

if command -v bids-validator &>/dev/null; then
    echo " Running: bids-validator ${corrected_bids_dir}"
    bids-validator "${corrected_bids_dir}"
    bids_exit=$?
    if [ ${bids_exit} -eq 0 ]; then
        echo " BIDS validation passed ✓"
    else
        echo " BIDS validation reported issues — review output above before continuing."
        echo " (Proceeding anyway; fix critical errors before submitting to a journal.)"
    fi
elif command -v npx &>/dev/null; then
    echo " Running: npx bids-validator ${corrected_bids_dir}"
    npx bids-validator "${corrected_bids_dir}"
else
    echo " WARNING: bids-validator not found in PATH."
    echo "   Install: npm install -g bids-validator"
    echo "   Skipping validation — continuing to fMRIPrep."
fi

# ── PART 2: Run fMRIPrep locally via Singularity ─────────────────────────────
echo ""
echo "============================================"
echo " PART 2: Running fMRIPrep"
echo "============================================"

mkdir -p "${derivatives_dir}"
mkdir -p "${fs_dir}"
mkdir -p "${work_dir}"

while IFS= read -r subject; do
    [ -z "${subject}" ] && continue

    echo ""
    echo "--------------------------------------------"
    echo " Subject : ${subject}"
    echo " Started : $(date)"
    echo "--------------------------------------------"

    singularity run --cleanenv \
        -B /autofs \
        -B /usr/pubsw \
        -B /cluster \
        -B /homes \
        -B /space \
        -B /vast \
        -B /run/user \
        "${fmriprep_simg}" \
        "${corrected_bids_dir}" "${derivatives_dir}" participant \
        --participant-label "${subject}" \
        --output-spaces ${output_spaces} \
        --fs-subjects-dir "${fs_dir}" \
        --work-dir "${work_dir}" \
        --fs-license-file "${fs_license}" \
        --mem_mb "${mem_mb}" \
        "${extra_opts[@]}"

    fp_exit=$?
    if [ ${fp_exit} -eq 0 ]; then
        echo ""
        echo " ✓ Done: ${subject}  ($(date))"
    else
        echo ""
        echo " ✗ FAILED: ${subject}  ($(date))"
        echo "   Check logs in: ${work_dir}"
        continue
    fi

    # ── Step 13: pre vs post fMRIPrep visual QC (optional, non-fatal) ────────
    if [ -f "${step13_script}" ]; then
        echo ""
        echo "  [Step 13] Generating pre/post QC GIFs for ${subject} ..."
        raw_func_dir="${corrected_bids_dir}/${subject}/ses-01/func"
        fp_func_dir="${derivatives_dir}/${subject}/ses-01/func"
        qc_dir="${derivatives_dir}/${subject}/figures"
        mkdir -p "${qc_dir}"

        for raw_bold in "${raw_func_dir}"/*_bold.nii.gz; do
            [ -f "${raw_bold}" ] || continue
            boldbase=$(basename "${raw_bold}" _bold.nii.gz)
            fp_bold="${fp_func_dir}/${boldbase}_space-T1w_desc-preproc_bold.nii.gz"
            if [ ! -f "${fp_bold}" ]; then
                echo "  [Step 13] fMRIPrep output not found, skipping: $(basename "${fp_bold}")"
                continue
            fi
            gif_out="${qc_dir}/${boldbase}_prepost_fmriprep.gif"
            echo "  [Step 13]  $(basename "${raw_bold}")  →  $(basename "${gif_out}")"
            "${python_exe}" "${step13_script}" \
                "${raw_bold}" "${fp_bold}" \
                -o "${gif_out}" --plane axial --fps 10 --step 2
            if [ $? -eq 0 ]; then
                echo "  [Step 13] ✓ GIF written: ${gif_out}"
            else
                echo "  [Step 13] ✗ GIF generation failed for ${boldbase}"
            fi
        done
    fi

done < "${bids_subj_list}"

echo ""
echo "============================================"
echo " All subjects processed.  $(date)"
echo "============================================"

# ── PART 3: QC — Mean Framewise Displacement + registration check ─────────────
echo ""
echo "============================================"
echo " PART 3: QC — FD + registration check"
echo "============================================"

"${python_exe}" - "${derivatives_dir}" "${bids_subj_list}" <<'PYEOF'
import sys, csv, json, pathlib, datetime

deriv      = pathlib.Path(sys.argv[1])
subj_file  = pathlib.Path(sys.argv[2])
FD_THRESH  = 0.9   # mm — flag subjects above this mean FD
SESSION    = "ses-01"

subjects = [l.strip() for l in subj_file.read_text().splitlines() if l.strip()]
results  = {}

for subj in subjects:
    func_dir = deriv / subj / SESSION / "func"
    entry = {"has_output": func_dir.is_dir(), "mean_fd": None,
             "flagged_fd": False, "has_mni_bold": False}

    if not entry["has_output"]:
        print(f"  {subj}: no fMRIPrep output directory ({func_dir})")
        results[subj] = entry
        continue

    mni_bolds = list(func_dir.glob("*MNI152*desc-preproc_bold.nii.gz"))
    entry["has_mni_bold"] = len(mni_bolds) > 0
    if not entry["has_mni_bold"]:
        print(f"  {subj}: ⚠  MNI-space BOLD not found — check registration")

    fds = []
    for tsv in func_dir.glob("*_desc-confounds_timeseries.tsv"):
        with open(tsv) as f:
            for row in csv.DictReader(f, delimiter="\t"):
                v = row.get("framewise_displacement", "n/a")
                if v not in ("n/a", "", "NA"):
                    try:
                        fds.append(float(v))
                    except ValueError:
                        pass

    if fds:
        mfd = sum(fds) / len(fds)
        entry["mean_fd"]     = round(mfd, 4)
        entry["flagged_fd"]  = mfd > FD_THRESH
        flag_str = f"  ⚠ FLAGGED (>{FD_THRESH} mm)" if mfd > FD_THRESH else "  ✓"
        print(f"  {subj}: mean FD = {mfd:.4f} mm{flag_str}")
    else:
        print(f"  {subj}: confounds TSV not found — FD unavailable")

    results[subj] = entry

log_path = deriv / "qc_fd_summary.json"
payload = {
    "generated":       datetime.datetime.now().isoformat(timespec="seconds"),
    "fd_threshold_mm": FD_THRESH,
    "session":         SESSION,
    "results":         results,
}
log_path.write_text(json.dumps(payload, indent=2))
print(f"\n QC log written: {log_path}")
n_flagged = sum(1 for v in results.values() if v.get("flagged_fd"))
n_noreg   = sum(1 for v in results.values() if not v.get("has_mni_bold"))
print(f" Subjects flagged (FD > {FD_THRESH} mm): {n_flagged}/{len(results)}")
if n_noreg:
    print(f" Subjects missing MNI BOLD: {n_noreg}/{len(results)}  ← check registration")
PYEOF
