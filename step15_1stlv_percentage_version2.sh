#!/bin/bash
set -uo pipefail

# =========================
# Config
# =========================
derivativesDir="/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/fmriprep"
subject_list="SubjectListfmriprep.txt"

ses="01"
tasks="ContinuousStim BlockStim rest"
runs="1"

# Custom censoring thresholds (FD + std_dvars)
FD_THRESH="1.5"
DVARS_THRESH="1.5"

# Global summary CSV
outlier_summary="${derivativesDir}/tvns_ses-${ses}_outliers_summary.csv"
echo "SubID,Task,Run,Num_TRs,Num_motion_outlier_cols,Percent_motion_outlier_cols,Num_custom_censor_TRs,Percent_custom_censor_TRs" > "$outlier_summary"

# =========================
# Main loop
# =========================
while read -r subj; do
  [[ -z "${subj}" ]] && continue

  for task in $tasks; do
    for run in $runs; do

      subPath="${derivativesDir}/${subj}/ses-${ses}/func"
      subFile="${subPath}/${subj}_ses-${ses}_task-${task}_run-0${run}_desc-confounds_timeseries.tsv"

      if [ ! -f "$subFile" ]; then
        echo "Missing file (skipping): $subFile"
        continue
      fi

      echo "Processing ${subj} ses-${ses} task-${task} run-0${run}"

      # Count TRs (rows minus header) for percent calculations
      nTR=$(awk 'END{print NR-1}' "$subFile")
      if [[ "$nTR" -le 0 ]]; then
        echo "Empty/invalid TSV (skipping): $subFile"
        continue
      fi

      # -------------------------
      # (A) Extract 6 motion params (CSV + space TXT)
      # -------------------------
      motion_csv="${subPath}/${subj}_ses-${ses}_task-${task}_run-0${run}_nuisance_regressors_for_GLM.csv"
      motion_txt="${subPath}/${subj}_ses-${ses}_task-${task}_run-0${run}_nuisance_regressors_for_GLM_no_header.txt"

      awk -F'\t' '
        NR==1{for(i=1;i<=NF;i++)h[$i]=i; next}
        {print $(h["trans_x"]) "," $(h["trans_y"]) "," $(h["trans_z"]) "," $(h["rot_x"]) "," $(h["rot_y"]) "," $(h["rot_z"])}
      ' "$subFile" > "$motion_csv"

      awk -F'\t' '
        NR==1{for(i=1;i<=NF;i++)h[$i]=i; next}
        {print $(h["trans_x"]) " " $(h["trans_y"]) " " $(h["trans_z"]) " " $(h["rot_x"]) " " $(h["rot_y"]) " " $(h["rot_z"])}
      ' "$subFile" > "$motion_txt"

      # Also create motion TSV no header for FEAT paste (tab-delimited)
      motion_tsv_nohdr="${subPath}/${subj}_ses-${ses}_task-${task}_run-0${run}_headmotion_no_header.tsv"
      awk -F'\t' '
        NR==1{for(i=1;i<=NF;i++)h[$i]=i; next}
        {print $(h["trans_x"]) "\t" $(h["trans_y"]) "\t" $(h["trans_z"]) "\t" $(h["rot_x"]) "\t" $(h["rot_y"]) "\t" $(h["rot_z"])}
      ' "$subFile" > "$motion_tsv_nohdr"

      # -------------------------
      # (B) Extract fMRIPrep motion_outlier* columns (if present)
      # -------------------------
      outliers_fmriprep="${subPath}/${subj}_ses-${ses}_task-${task}_run-0${run}_outliers_fmriprep.tsv"
      outlier_cols=$(head -1 "$subFile" | tr '\t' '\n' | grep -n "^motion_outlier" | cut -d":" -f1 || true)

      if [ -n "$outlier_cols" ]; then
        cut -f"$(echo "$outlier_cols" | paste -sd",")" "$subFile" > "$outliers_fmriprep"
        num_outlier_cols=$(echo "$outlier_cols" | wc -l | tr -d ' ')
      else
        : > "$outliers_fmriprep"
        num_outlier_cols=0
      fi

      percent_outlier_cols=$(echo "scale=2; ($num_outlier_cols/$nTR)*100" | bc)

      # -------------------------
      # (C) Custom censor EV spikes from FD + std_dvars thresholds
      #     Output: k columns, one per censored TR (1 at that TR, else 0), no header
      # -------------------------
      custom_ev="${subPath}/${subj}_ses-${ses}_task-${task}_run-0${run}_custom_censor_EVs_no_header.tsv"

      awk -F'\t' -v fd_th="$FD_THRESH" -v dv_th="$DVARS_THRESH" '
        NR==1{
          for(i=1;i<=NF;i++){
            if($i=="framewise_displacement") fd=i
            if($i=="std_dvars") dv=i
          }
          next
        }
        {
          row=NR-1
          is_censor = (($(fd)+0 > fd_th) || ($(dv)+0 > dv_th)) ? 1 : 0
          if(is_censor){ k++; idx[k]=row }
          nrows=row
        }
        END{
          if(k==0){ exit 0 }
          for(r=1;r<=nrows;r++){
            for(c=1;c<=k;c++){
              val = (r==idx[c]) ? 1 : 0
              printf "%d", val
              if(c<k) printf "\t"
            }
            printf "\n"
          }
        }
      ' "$subFile" > "$custom_ev"

      # Count censored TRs
      num_custom_censor=$(awk -F'\t' -v fd_th="$FD_THRESH" -v dv_th="$DVARS_THRESH" '
        NR==1{for(i=1;i<=NF;i++){if($i=="framewise_displacement") fd=i; if($i=="std_dvars") dv=i} next}
        { if(($(fd)+0>fd_th)||($(dv)+0>dv_th)) n++ }
        END{ print n+0 }
      ' "$subFile")

      percent_custom_censor=$(echo "scale=2; ($num_custom_censor/$nTR)*100" | bc)

      # -------------------------
      # (D) FEAT-ready combined confounds: motion(6) + custom EVs (if any)
      # -------------------------
      feat_confounds="${subPath}/${subj}_ses-${ses}_task-${task}_run-0${run}_bold_confounds_feat.tsv"

      if [ -s "$custom_ev" ]; then
        paste -d $'\t' "$motion_tsv_nohdr" "$custom_ev" > "$feat_confounds"
      else
        cp "$motion_tsv_nohdr" "$feat_confounds"
      fi

      # -------------------------
      # (E) Append summary row
      # -------------------------
      echo "${subj},${task},${run},${nTR},${num_outlier_cols},${percent_outlier_cols},${num_custom_censor},${percent_custom_censor}" >> "$outlier_summary"

      # Optional: clean small intermediate
      rm -f "$motion_tsv_nohdr"

    done
  done
done < "$subject_list"
