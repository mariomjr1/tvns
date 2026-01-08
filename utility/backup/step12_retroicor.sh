source /autofs/cluster/vagabond/USERS/MARIO/env.sh

matlab -batch "addpath('/autofs/cluster/vagabond/USERS/MARIO/Pipelines/9_tvns/retroicor'); retroicor_batch('/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/4_names_corrected');"
