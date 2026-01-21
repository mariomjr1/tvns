#!/bin/bash

# Directories
dir_output=/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/fsl-regressed/confound_list
dir_input=/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/fmriprep

# Parameters
subjects="sub-7T0111CI110124 sub-7T0111LC031523 sub-7T0407CI110824 sub-7T1011HC020524 sub-7T1813CI071624"
ses="01"
tasks="restCorrected"

# Loop through subjects and tasks
for subj in $subjects; do
    for task in $tasks; do

        echo "Processing subject: ${subj}, task: ${task}"

        # 1. Import 6D head motion
        awk -F'\t' -vcols=trans_x,trans_y,trans_z,rot_x,rot_y,rot_z '
        (NR==1) {
            n = split(cols, cs, ",");
            for (c = 1; c <= n; c++) {
                for (i = 1; i <= NF; i++) {
                    if ($(i) == cs[c]) ci[c] = i;
                }
            }
        }
        {
            for (i = 1; i <= n; i++) printf "%s\t", $(ci[i]);
            printf "\n";
        }' "$dir_input/${subj}/ses-${ses}/func/${subj}_ses-${ses}_task-${task}_run-01_desc-confounds_timeseries.tsv" \
        > "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_headmotion.txt"
        wait

        # 2. Import FD and std_dvars values
        awk -F'\t' -vcols=framewise_displacement,std_dvars '
        (NR==1) {
            n = split(cols, cs, ",");
            for (c = 1; c <= n; c++) {
                for (i = 1; i <= NF; i++) {
                    if ($(i) == cs[c]) ci[c] = i;
                }
            }
        }
        {
            for (i = 1; i <= n; i++) printf "%s\t", $(ci[i]);
            printf "\n";
        }' "$dir_input/${subj}/ses-${ses}/func/${subj}_ses-${ses}_task-${task}_run-01_desc-confounds_timeseries.tsv" \
        > "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_outlier.txt"
        wait

        # 3. Select outlier scans
        awk '
        NR == FNR {
            n += ($1 + 0 > 1.5 || $2 + 0 > 1.5);
            next;
        }
        n {
            for (i = (n + 2); i > 2; i--) $i = (FNR == 1 ? "EV" (i - 2) : (($1 + 0 > 1.5 || $2 + 0 > 1.5) && i == (s + 3) && ++s));
        }1' "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_outlier.txt" "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_outlier.txt" \
        > "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_outlier_1.txt"
        wait

        # 4. Remove first 2 columns
        awk '{for (i = 3; i <= NF; i++) printf "%s\t", $i; printf "\n";}' "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_outlier_1.txt" \
        > "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_motion_outlier_final.txt"
        wait

        echo "Creating confound list for ${subj}, task: ${task}"

        # Combine head motion and outlier data
        paste -d "" "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_headmotion.txt" "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_motion_outlier_final.txt" \
        > "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_confounds.txt"

        # Clean up intermediate files
        rm "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_outlier_1.txt" \
           "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_outlier.txt" \
           "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_headmotion.txt" \
           "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_motion_outlier_final.txt"

        # 5. Remove headers
        tail -n +2 "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_confounds.txt" \
        > "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_confounds_feat.txt"

        # Remove temporary confounds file
        rm -r "$dir_output/${subj}_ses-${ses}_task-${task}_run-01_bold_confounds.txt"

    done
done

