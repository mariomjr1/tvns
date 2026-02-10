#!/bin/bash
set -euo pipefail

# =========================
# Paths
# =========================
input_folder="/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/fmriprep"
output_folder="/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/8_first_level_vanilla"

sublist="SubjectListfmriprep.txt"
ses="01"
tasks=("ContinuousStim" "BlockStim" "rest")
runs=("01") 

# =========================
# Output folders
# =========================
mkdir -p "${output_folder}/stim_onsets" \
         "${output_folder}/motion_regressors" \
         "${output_folder}/bolds"

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
      # 1) Stim onsets (step16/17): expected pattern like:
      #    sub-XXX_ses-01_task-<TASK>_run-<RUN>_stim.txt
      # ---------
      stim_src="${func_dir}/${subject}_ses-${ses}_task-${task}_run-${run}_bold_stim.txt"
      stim_dst="${output_folder}/stim_onsets/${subject}_ses-${ses}_task-${task}_run-${run}_bold_stim.txt"

      if [[ -f "${stim_src}" ]]; then
        cp -f "${stim_src}" "${stim_dst}"
      else
        echo "  [WARN] Missing stim file: ${stim_src}"
      fi

      # ---------
      # 2) Motion regressors from step15
      #    Use the file you generated earlier (SPM-friendly no header):
      #    *_nuissance_regressors_for_GLM_no_header.txt
      #    (keeping your original spelling "nuissance")
      # ---------
      motion_src="${func_dir}/${subject}_ses-${ses}_task-${task}_run-${run}_nuisance_regressors_for_GLM_no_header.txt"
      motion_dst="${output_folder}/motion_regressors/${subject}_ses-${ses}_task-${task}_run-${run}_motion_regressors.txt"

      if [[ -f "${motion_src}" ]]; then
        cp -f "${motion_src}" "${motion_dst}"
      else
        echo "  [WARN] Missing motion regressors: ${motion_src}"
      fi

      # ---------
      # 3) BOLD T1w-space preproc bold
      #    Expected fMRIPrep name:
      #    *_space-T1w_desc-preproc_bold.nii.gz
      # ---------
      bold_src="${func_dir}/${subject}_ses-${ses}_task-${task}_run-${run}_space-T1w_desc-preproc_bold.nii.gz"
      bold_dst="${output_folder}/bolds/${subject}_ses-${ses}_task-${task}_run-${run}_space-T1w_desc-preproc_bold.nii.gz"

      if [[ -f "${bold_src}" ]]; then
        cp -f "${bold_src}" "${bold_dst}"
        # If you truly need .nii (uncompressed) for SPM, uncomment:
        # gunzip -f "${bold_dst}"
      else
        echo "  [WARN] Missing BOLD: ${bold_src}"
      fi

    done
  done

done < "${sublist}"

echo "Done. Outputs in: ${output_folder}"
