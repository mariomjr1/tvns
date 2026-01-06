#!/bin/bash

##################################################################################
################## DON'T RUN THIS SCRIPT ON LAUNCHPAD #################

# This script:
#    a) creates MR, PET, MR_PET folders inside the patientID directory
#    b) copies and unpacks dicom files inside the MR folders
#    c) copies the PET listmode data and the Dose_info.xks
#    d) prints a 'findpetsession.txt' inside the PET folder, which contains info on the actual session that has been copied. Please check that it's the correct one

# It opens automatically a local screen sessions for every patients.

# NOTE 1: THIS SCRIPT RUNS LOCALLY. The number of patients that can be run in parallel (without overloading the workstation) depends on the GPU/CPU of the linux machine that is running the script.
# NOTE 2: The name of the folder needs to be UNIQUE and <= the full patient ID (ex: ppg_mig015_post can be used instead of ppg_mig015_post_bay6_20210205)
# NOTE 3: When the script is done, you will find "step1_DONE.txt" in ~/patientID/PET/LOG
##################################################################################

echo -e " by Ludovica Brusaferri\n"
echo -e " modified by Mario Murakami (August 2024)"
echo -e " STEP 0 - Pulling the data from Scanner, type screen -ls to check progress"
echo -e " Date:\t `date`"


out_path=/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/rawdata
subjlistpath=/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/codes


#We first run a bash shell, and we add the current path to PATH
PATH=$PATH:/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/codes/utility

cd /autofs/cluster/vagabond/USERS/MARIO/Projects/7T/codes/utility

# here you can declare the list of patients you want to run
declare -a StringArray=(`cat "${subjlistpath}/SubjectList.txt"`)
for subjID in ${StringArray[@]}; do
	#this ONLY runs locally
        screen -S ${subjID}-DownloadAndUnpack -dm bash -c "sleep 5; download_and_unpack.sh ${subjID}"
done
