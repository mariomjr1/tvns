# MARIO's environment (Mario Murakami - Vagabond Lab)

# This stable environment also source other stuff from cluster packages!!!!!! You can change the version of freesurfer checking the original folder ang changing the number 741 to the correspondent version that you want
#source /usr/local/freesurfer/nmr-stable741-env-bash
#export FSLDIR=/usr/pubsw/packages/fsl/6.0.7.4

# Set environment variables
export FREESURFER_HOME=/usr/local/freesurfer/7.4.1
export SUBJECTS_DIR=$FREESURFER_HOME/subjects
export MNI_DIR=$FREESURFER_HOME/mni
#if you want to export a specific version of fsl: 
export FSL_DIR=/usr/pubsw/packages/fsl/6.0.4
#export FSLDIR=/usr/pubsw/packages/fsl/current
source $FREESURFER_HOME/SetUpFreeSurfer.sh



export PATH=/usr/pubsw/common/matlab:${PATH}
export PATH=/usr/pubsw/packages/mrpet/bin:${PATH}
#export ANTSPATH=/usr/pubsw/packages/ANTS/2.3.1/bin/
#export PATH=$PATH\:${ANTSPATH}



if [ -n "${PATH##*pubsw*}" ] ; then
  PATH=~/bin:$PATH:/usr/pubsw/bin
fi

if [ "$TERM" = "xterm" -a -n "$PS1" ] ; then
  stty erase '^?'
fi

LS_COLORS=$LS_COLORS:'di=0;35:' ; export LS_COLORS


#environments 14 august
source /autofs/cluster/vagabond/USERS/MARIO/Packages/miniforge3/etc/profile.d/conda.sh
conda activate /autofs/cluster/vagabond/USERS/MARIO/Packages/miniforge3

#this env bellow is for bids conversion
#source /autofs/cluster/vagabond/USERS/MARIO/Packages/marioenv/bin/activate

export SINGULARITY_CACHEDIR=/autofs/cluster/vagabond/USERS/MARIO/Packages/Singularity
export APPTAINER_CACHEDIR=/autofs/cluster/vagabond/USERS/MARIO/Packages/Singularity
export SINGULARITY_TMPDIR=/autofs/cluster/vagabond/USERS/MARIO/Packages/Singularity
export APPTAINER_TMPDIR=/autofs/cluster/vagabond/USERS/MARIO/Packages/Singularity

source /autofs/cluster/vagabond/USERS/MARIO/Packages/env/fmriprep/bin/activate

export PATH=$PATH:/autofs/cluster/vagabond/USERS/MARIO/Packages/node-v20.16.0-linux-x64/bin
export PATH=/autofs/cluster/vagabond/USERS/MARIO/Packages/msm:$PATH
export PATH=$PATH:/autofs/cluster/vagabond/USERS/MARIO/Packages/parallel-20240722/src

#set ants and afni again because of bug, 9 september 2024

export ANTSPATH=/autofs/cluster/pubsw/2/pubsw/Linux2-2.3-x86_64/packages/ANTS/2.4.4/bin
export PATH=${ANTSPATH}:$PATH
export PATH=$PATH:/autofs/cluster/pubsw/2/pubsw/Linux2-2.3-x86_64/packages/AFNI/current/bin




