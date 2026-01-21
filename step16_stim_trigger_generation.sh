source /autofs/cluster/vagabond/USERS/MARIO/env.sh

#!/usr/bin/env bash
set -euo pipefail

# If your cluster uses environment modules, uncomment:
# module load matlab

# Folder where stim_trigger.m lives (edit to wherever you saved it)
MATLAB_CODE_DIR="/autofs/cluster/vagabond/USERS/MARIO/Pipelines/9_tvns/utility/matlab_code"

# Run MATLAB in batch and call the function
matlab -batch "addpath('${MATLAB_CODE_DIR}'); run(fullfile('${MATLAB_CODE_DIR}','stim_trigger.m'));"
