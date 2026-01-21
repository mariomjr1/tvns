#!/bin/bash

# Set directories and task
derivativesDir="/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/fmriprep"
task="ContinuousStim"
subject_list="SubjectListfmriprep.txt"

# Initialize outlier summary file
outlier_summary="${derivativesDir}/tvns_ses-01_task-${task}_outliers.csv"
echo "SubID,Run,Number_of_outliers,Percent_outliers" > "$outlier_summary"

# Loop through subjects and runs
while read -r subj; do
  for run in 1 2; do
    subPath="${derivativesDir}/${subj}/ses-01/func"
    subFile="${subPath}/${subj}_ses-01_task-${task}_run-0${run}_desc-confounds_timeseries.tsv"

    if [ -f "$subFile" ]; then

      # Extract nuisance regressors
      awk -F'\t' 'NR==1{for(i=1;i<=NF;i++)h[$i]=i} NR>1{print $(h["trans_x"]) "," $(h["trans_y"]) "," $(h["trans_z"]) "," $(h["rot_x"]) "," $(h["rot_y"]) "," $(h["rot_z"])}' "$subFile" > "${subPath}/${subj}_ses-01_task-${task}_run-0${run}_nuissance_regressors_for_GLM.csv"
      awk -F'\t' 'NR==1{for(i=1;i<=NF;i++)h[$i]=i} NR>1{print $(h["trans_x"]) " " $(h["trans_y"]) " " $(h["trans_z"]) " " $(h["rot_x"]) " " $(h["rot_y"]) " " $(h["rot_z"])}' "$subFile" > "${subPath}/${subj}_ses-01_task-${task}_run-0${run}_nuissance_regressors_for_GLM_no_header.txt"

      # Extract outlier columns
      outlier_cols=$(head -1 "$subFile" | tr '\t' '\n' | grep -n "^motion_outlier" | cut -d":" -f1)

      if [ ! -z "$outlier_cols" ]; then
        cut -f$(echo "$outlier_cols" | paste -sd",") "$subFile" > "${subPath}/${subj}_ses-01_task-${task}_run-0${run}_outliers.csv"
        num_outliers=$(echo "$outlier_cols" | wc -l)
      else
        num_outliers=0
        touch "${subPath}/${subj}_ses-01_task-${task}_run-0${run}_outliers.csv"
      fi

      percent_outliers=$(echo "scale=2; ($num_outliers/326)*100" | bc)

      # Append to summary
      echo "${subj},${run},${num_outliers},${percent_outliers}" >> "$outlier_summary"

    else
      echo "Missing file: $subFile"
    fi

  done
done < "$subject_list"

