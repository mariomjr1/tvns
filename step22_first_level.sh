#!/usr/bin/env bash
set -euo pipefail

# If your cluster uses environment modules, uncomment:
# module load matlab

MATLAB_CODE_DIR="/autofs/cluster/vagabond/USERS/MARIO/Pipelines/9_tvns/utility/matlab_code"

# Run MATLAB in batch and call the function
matlab -batch "addpath('${MATLAB_CODE_DIR}'); run(fullfile('${MATLAB_CODE_DIR}','glm_spm_t1w.m'));"
