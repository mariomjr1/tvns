# Copy bold (vanilla) from sourcedata and paste in output_folder
sourcedata="/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata"
output_folder="/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/4_names_corrected"

# Inside sourcedata there are folders of subjects 
# In SubjectList.txt there is the list of subjects (same name of folders inside sourcedata)
# Inside those folders it follows BIDS structure (ses-01/func)
# Inside func there are bold files (ends with .nii.gz)
# copy all them and paste in output_folder

for subject in $(cat SubjectListfmriprep.txt); do
    find "$sourcedata/$subject/ses-01/func" -name "*.nii.gz" -exec cp {} "$output_folder" \;
done

for subject in $(cat SubjectListfmriprep.txt); do
    find "$sourcedata/$subject/ses-01/func" -name "*.json" -exec cp {} "$output_folder" \;
done
