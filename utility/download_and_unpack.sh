#!/bin/bash

#=====================================================================================================
# Download PET data and unpack MR

#======================================================================================================


patient_ID=$1
#rename_dicom=$2

# folder where the PET linux scripts are stored
main_script_path=/autofs/cluster/petcore/PET_computer_backup/brainPET_code/Aether-Mirror

# this assumes 'standard' data organisation
working_directory_path=/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/rawdata

mkdir -p $working_directory_path/$1

perl $main_script_path/brainPET_mrpet_dwnld_unpack.pl -d $working_directory_path -s $1 -id $1  -mr 1 -pet 1



