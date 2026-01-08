% retroicorBatch.m
% Batch (no GUI) version of retroicorGUI
%
% Changes requested:
% - No GUI
% - Do NOT use RPIEZO or piezoout (since no R-DECO)
% - Input folder:  /autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/2_retroicor
% - Output folder: /autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/3_generated1D
% - fs = 40 (fixed)
%
% IMPORTANT NOTE:
% The original function call was:
%   generate_1D_fun_1(HBsignal, RpeaksSeconds, RESPsig, vols, TR, sr, fs, SMS, MRTRIG, fnameBase)
% If you are not using heartbeat/R-peaks, you must ensure generate_1D_fun_1 can handle:
%   - HBsignal = [] and RpeaksSeconds = []
% OR adjust generate_1D_fun_1 to skip cardiac regressors when empty.
%
% Run headless:
%   matlab -batch "retroicorBatch"

function retroicorBatch()

    % --- Ensure this script's folder (and generate_1D_fun1.m) is on the path ---
    thisFileDir = fileparts(mfilename('fullpath'));
    addpath(thisFileDir);

    physioFolder = '/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/2_retroicor';
    outputFolder = '/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/3_generated1D';

    % Set these here (no GUI inputs)
    TR  = 1.19;   % <-- adjust if needed
    sr  = 1000;   % <-- adjust if needed
    fs  = 40;     % fixed as requested
    SMS = 1;      % keep same default unless you need otherwise

    if ~exist(physioFolder, 'dir')
        error('Input folder does not exist: %s', physioFolder);
    end
    if ~exist(outputFolder, 'dir')
        mkdir(outputFolder);
    end

    matFiles = dir(fullfile(physioFolder, '*.mat'));
    if isempty(matFiles)
        error('No .mat files found in: %s', physioFolder);
    end

    fprintf('Found %d MAT files in %s\n', numel(matFiles), physioFolder);
    fprintf('Output folder: %s\n', outputFolder);

    originalDir = pwd;

    for i = 1:numel(matFiles)
        physioPath = fullfile(matFiles(i).folder, matFiles(i).name);

        try
            % Load physio struct from the .mat file
            loadedData = load(physioPath);
            if ~isfield(loadedData, 'physio')
                warning('File %s does not contain a "physio" struct. Skipping...', matFiles(i).name);
                continue;
            end
            physio = loadedData.physio;

            % Required fields (NO RPIEZO / piezoout)
            requiredFields = {'RESP', 'MRTRIG'};
            missing = false;
            for f = 1:numel(requiredFields)
                if ~isfield(physio, requiredFields{f})
                    warning('File %s missing required field %s. Skipping...', matFiles(i).name, requiredFields{f});
                    missing = true;
                end
            end
            if missing
                continue;
            end

            % Type checks
            if ~isnumeric(physio.RESP) || ~isnumeric(physio.MRTRIG)
                warning('File %s has non-numeric RESP or MRTRIG. Skipping...', matFiles(i).name);
                continue;
            end

            % Determine vols from MRTRIG (same logic)
            MRtrig_diff = diff(physio.MRTRIG);
            [~, trig_idx] = findpeaks(MRtrig_diff, 'MinPeakHeight', 2);
            vols = numel(trig_idx);

            if vols == 0
                warning('File %s: No valid triggers detected in MRTRIG. Skipping...', matFiles(i).name);
                continue;
            end

            % SID from filename (remove extension)
            [~, sidRaw, ~] = fileparts(matFiles(i).name);
            sid = regexprep(sidRaw, '[^a-zA-Z0-9]', '_');

            % (Optional) keep the "prepend if starts with digit" behavior
            if ~isempty(sid) && isstrprop(sid(1), 'digit')
                sid = ['sub_' sid];
            end

            % Base filename
            fnameBase = strcat('sub-', sid, '_run-001_bold');

            % Call generator with cardiac inputs empty
            HBsignal = [];         % no heartbeat signal
            Rpeaks   = [];         % no R-peak annotations (seconds)

            cd(outputFolder);
            try
                [~, ~] = generate_1D_fun_1( ...
                    HBsignal, ...          % HB: empty (no R-DECO)
                    Rpeaks, ...            % R: empty (no R-DECO)
                    physio.RESP, ...       % resp signal
                    vols, ...              % number of volumes
                    TR, ...                % TR
                    sr, ...                % sampling rate
                    fs, ...                % desired sampling freq (40)
                    SMS, ...               % SMS flag
                    physio.MRTRIG, ...     % MR trigger
                    fnameBase);            % file identifier

                fprintf('[%d/%d] OK  %s  (vols=%d)\n', i, numel(matFiles), matFiles(i).name, vols);
            catch ME2
                warning('Error processing file %s: %s. Skipping...', matFiles(i).name, ME2.message);
            end

            cd(originalDir);

        catch ME
            cd(originalDir);
            warning('FAIL %s: %s', matFiles(i).name, ME.message);
        end
    end

    fprintf('Done. 1D files should be in: %s\n', outputFolder);
end
