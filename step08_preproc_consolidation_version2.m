% Batch version of consolidation_8 (no GUI)
% R-DECO optional: if <base>_rdeco.mat exists, use it; otherwise piezoout=[]
%
% Input :  .../1_physio_preprocessed/.mat   (expects *_filtered.mat naming)
% R-DECO :  .../1_physio_preprocessed/<base>_rdeco.mat   (optional)
% Output:  .../2_retroicor/<base>_retroicor_ready.mat
%
% Modified - Mario Murakami (batch refactor) - January 2026

function physioRDecoBatch()

    inDir  = '/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/1_physio_preprocessed';
    outDir = '/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/2_retroicor';

    if ~exist(inDir, 'dir')
        error('Input folder does not exist: %s', inDir);
    end
    if ~exist(outDir, 'dir')
        mkdir(outDir);
    end

    files = dir(fullfile(inDir, '*.mat'));
    if isempty(files)
        error('No .mat files found in: %s', inDir);
    end

    fprintf('Found %d MAT files in %s\n', numel(files), inDir);

    requiredVars = {'RESP', 'RPIEZO', 'MRTRIG', 'STIMTRIG', 'name', 'PIEZOF', 'PIEZOD'};

    for k = 1:numel(files)
        inPath = fullfile(files(k).folder, files(k).name);

        try
            physioData = load(inPath);

            % Check required physio variables
            for v = 1:numel(requiredVars)
                if ~isfield(physioData, requiredVars{v})
                    error('Missing required variable in physio file (%s): %s', files(k).name, requiredVars{v});
                end
            end

            % Assign variables locally
            RESP     = physioData.RESP;
            RPIEZO   = physioData.RPIEZO;
            MRTRIG   = physioData.MRTRIG;
            STIMTRIG = physioData.STIMTRIG;
            name     = physioData.name;
            PIEZOF   = physioData.PIEZOF;
            PIEZOD   = physioData.PIEZOD;

            % Determine base name and output name
            % Preferred rule: <base>_filtered.mat -> <base>_retroicor_ready.mat
            base = strip_suffix(files(k).name, '_filtered.mat');
            if isempty(base)
                % If file doesn't match *_filtered.mat, fall back to file stem
                [~, base, ~] = fileparts(files(k).name);
            end

            outName = [base '_retroicor_ready.mat'];
            outPath = fullfile(outDir, outName);

            % Optional R-DECO file: <base>_rdeco.mat
            rdecoPath = fullfile(inDir, [base '_rdeco.mat']);
            [piezoout, usedRdeco] = piezoout_from_rdeco_optional(rdecoPath);

            % Build output struct
            physio = struct();
            physio.RESP     = RESP;
            physio.RPIEZO   = RPIEZO;
            physio.MRTRIG   = MRTRIG;
            physio.STIMTRIG = STIMTRIG;
            physio.PIEZOF   = PIEZOF;
            physio.PIEZOD   = PIEZOD;
            physio.name     = name;
            physio.piezoout = piezoout;  % [] if no R-DECO

            save(outPath, 'physio');

            if usedRdeco
                fprintf('[%d/%d] OK  %s  (R-DECO: YES | R-peaks: %d)\n', ...
                    k, numel(files), outName, numel(piezoout));
            else
                fprintf('[%d/%d] OK  %s  (R-DECO: NO | piezoout empty)\n', ...
                    k, numel(files), outName);
            end

        catch ME
            warning('[%d/%d] FAIL %s\n  %s', k, numel(files), files(k).name, ME.message);
        end
    end

    fprintf('Done. Outputs in: %s\n', outDir);
end


% ---------------- helpers ----------------

function base = strip_suffix(fname, suffix)
    % Returns filename without suffix if it ends with suffix, else ''.
    base = '';
    if endsWith(fname, suffix)
        base = extractBefore(fname, strlength(fname) - strlength(suffix) + 1);
    end
end


function [piezoout, usedRdeco] = piezoout_from_rdeco_optional(rdecoPath)
    % If rdecoPath exists and contains R_loc, compute piezoout; else [].

    piezoout = [];
    usedRdeco = false;

    if ~exist(rdecoPath, 'file')
        return;
    end

    try
        rDecoData = load(rdecoPath);

        % Most common: rDecoData.data.R_loc (often cell)
        if isfield(rDecoData, 'data') && isstruct(rDecoData.data) && isfield(rDecoData.data, 'R_loc')
            piezoout = rloc_to_seconds(rDecoData.data.R_loc);
            usedRdeco = ~isempty(piezoout);
            return;
        end

        % Alternate: top-level R_loc
        if isfield(rDecoData, 'R_loc')
            piezoout = rloc_to_seconds(rDecoData.R_loc);
            usedRdeco = ~isempty(piezoout);
            return;
        end

        % Exists but no usable R_loc -> treat as "no R-DECO"
        piezoout = [];
        usedRdeco = false;

    catch
        piezoout = [];
        usedRdeco = false;
    end
end


function r = rloc_to_seconds(R_loc)
    % Accepts:
    % - numeric datenums
    % - datetime array
    % - cell containing either of the above (common in R-DECO)

    t = R_loc;
    if iscell(t)
        t = t{1,1};
    end

    if isempty(t)
        r = [];
        return;
    end

    if isdatetime(t)
        r = seconds(timeofday(t))';
        return;
    end

    if isnumeric(t)
        r = zeros(1, numel(t));
        for j = 1:numel(t)
            [~, ~, ~, H, MN, S] = datevec(t(j));
            r(j) = H*3600 + MN*60 + S;
        end
        return;
    end

    error('Unsupported R_loc type: %s', class(t));
end
