#!/bin/bash
set -euo pipefail

# =========================
# Paths
# =========================
input_folder="/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/fmriprep"
output_folder="/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/8_first_level"

sublist="SubjectListfmriprep.txt"
ses="01"
tasks=("ContinuousStim" "BlockStim" "rest")
runs=("01") 

# =========================
# Output folders
# =========================
mkdir -p "${output_folder}/masks" \


# =========================
# Main loop
# =========================
while read -r subject; do
  [[ -z "${subject}" ]] && continue
  echo "Processing subject: ${subject}"

  for task in "${tasks[@]}"; do
    for run in "${runs[@]}"; do

      func_dir="${input_folder}/${subject}/ses-${ses}/func"

      # ---------
      # 1) masks: expected pattern like:
      #    sub-XXX_ses-01_task-<TASK>_run-<RUN>_desc-brain_mask.nii.gz
      # ---------
      mask_src="${func_dir}/${subject}_ses-${ses}_task-${task}_run-${run}_desc-brain_mask.nii.gz"
      mask_dst="${output_folder}/masks/${subject}_ses-${ses}_task-${task}_run-${run}_desc-brain_mask.nii.gz"

      if [[ -f "${mask_src}" ]]; then
        cp -f "${mask_src}" "${mask_dst}"
      else
        echo "  [WARN] Missing mask file: ${mask_src}"
      fi

    done
  done

done < "${sublist}"

echo "Done. Outputs in: ${output_folder}"
