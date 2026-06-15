function preproc_generate_1D_v2(input_dir, output_dir, bids_subject_id, ...
                                 sourcedata_dir, varargin)
% PREPROC_GENERATE_1D_V2
% Generate RETROICOR .1D files from per-sequence preprocessed physio mats.
%
% Reads *_filtered.mat files produced by preproc_filter_per_sequence (step 04),
% optionally incorporates R-DECO R-peak files (*_rdeco.mat), reads TR from the
% BIDS JSON sidecar, and calls generate_1D_fun_1 to write .1D files.
%
% Usage:
%   preproc_generate_1D_v2(input_dir, output_dir, bids_subject_id, sourcedata_dir)
%   preproc_generate_1D_v2(..., 'SMS', 1, 'FS_OUT', 40, 'TR_FALLBACK', 1.19)
%
% Required:
%   input_dir        folder with *_filtered.mat and optional *_rdeco.mat
%                    (output of step04_preprocess_for_retroicor.sh)
%   output_dir       where to write .1D files (created if absent)
%   bids_subject_id  full BIDS subject ID (e.g. sub-7T1019HC042726)
%   sourcedata_dir   BIDS sourcedata root — used to find *_bold.json for TR
%
% Optional:
%   SMS           Multiband flag: 1 = SMS (default), 0 = non-SMS
%   FS_OUT        Output physio sampling rate Hz (default: 40)
%   TR_FALLBACK   TR value to use if JSON sidecar not found (default: 1.19)
%   SESSION       BIDS session label without 'ses-' (default: '01')
%   Cardiac       Global cardiac flag: 1 = cardiac+respiration (default),
%                 0 = respiration-only. Overridden per-run by DecisionFile.
%   DecisionFile  Optional path to a per-run piezo-QC decision manifest CSV
%                 (columns: subject,task,run,use_cardiac,verdict,decided_by,
%                 timestamp) written by the GUI piezo review. When present, the
%                 per-run use_cardiac value overrides the global Cardiac flag.
%
% Output files in output_dir:
%   RETRO-resp_<bids_subject_id>_ses-<session>_task-*_run-*_bold.1D
%   RETRO-qrs_<bids_subject_id>_ses-<session>_task-*_run-*_bold.1D  (if R-DECO available)
%
% Created by Mario Murakami

    % ── Parse inputs ──────────────────────────────────────────────────────────
    p = inputParser();
    addRequired(p, 'input_dir',       @(x) ischar(x)||isstring(x));
    addRequired(p, 'output_dir',      @(x) ischar(x)||isstring(x));
    addRequired(p, 'bids_subject_id', @(x) ischar(x)||isstring(x));
    addRequired(p, 'sourcedata_dir',  @(x) ischar(x)||isstring(x));
    addParameter(p, 'SMS',         1,    @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'FS_OUT',      40,   @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'TR_FALLBACK', 1.19, @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'SESSION',     '01', @(x) ischar(x)||isstring(x));
    % Cardiac=1 uses R-DECO peaks for cardiac regressors; Cardiac=0 forces
    % RESPIRATION-ONLY (skip R-DECO entirely) — use when piezo QC is BAD.
    addParameter(p, 'Cardiac',     1,    @(x) isnumeric(x)&&isscalar(x));
    % Optional per-run piezo-QC decision manifest (overrides Cardiac per run).
    addParameter(p, 'DecisionFile', '',  @(x) ischar(x)||isstring(x));
    parse(p, input_dir, output_dir, bids_subject_id, sourcedata_dir, varargin{:});

    input_dir       = char(p.Results.input_dir);
    output_dir      = char(p.Results.output_dir);
    bids_subject_id = char(p.Results.bids_subject_id);
    sourcedata_dir  = char(p.Results.sourcedata_dir);
    SMS             = p.Results.SMS;
    fs_out          = p.Results.FS_OUT;
    tr_fallback     = p.Results.TR_FALLBACK;
    session         = char(p.Results.SESSION);
    use_cardiac     = logical(p.Results.Cardiac);
    decision_file   = char(p.Results.DecisionFile);
    sr              = 1000;   % physio sampling rate (always 1000 Hz from physioparse)

    % Per-run cardiac decisions from the GUI piezo review (empty map if none).
    decision_map = load_decision_map(decision_file);

    % ── Suppress figure windows (headless cluster) ────────────────────────────
    set(0, 'DefaultFigureVisible', 'off');

    % ── Validate dirs ─────────────────────────────────────────────────────────
    if ~exist(input_dir, 'dir')
        error('Input directory not found: %s', input_dir);
    end
    if ~exist(output_dir, 'dir')
        mkdir(output_dir);
    end

    % Add generate_1D_fun_1 to path (must be in the same folder as this file)
    this_dir = fileparts(mfilename('fullpath'));
    addpath(this_dir);

    % ── Find filtered mats ────────────────────────────────────────────────────
    % Pattern: <bids_subject_id>_task-*_run-*_filtered.mat
    pattern = fullfile(input_dir, [bids_subject_id '_task-*_run-*_filtered.mat']);
    mats    = dir(pattern);

    if isempty(mats)
        error('No filtered mats found matching: %s', pattern);
    end

    fprintf('\n========================================\n');
    fprintf(' preproc_generate_1D_v2\n');
    fprintf('========================================\n');
    fprintf(' Subject:      %s\n', bids_subject_id);
    fprintf(' Input:        %s\n', input_dir);
    fprintf(' Output:       %s\n', output_dir);
    fprintf(' SMS=%d  FS_OUT=%d  TR_fallback=%.3f\n', SMS, fs_out, tr_fallback);
    if use_cardiac
        fprintf(' Cardiac:      ON (cardiac + respiration regressors)\n');
    else
        fprintf(' Cardiac:      OFF — RESPIRATION-ONLY (R-DECO skipped; bad piezo)\n');
    end
    if decision_map.Count > 0
        fprintf(' Decisions:    %d per-run override(s) from %s\n', ...
                decision_map.Count, decision_file);
    end
    fprintf(' Found:        %d filtered mat(s)\n\n', numel(mats));

    n_ok   = 0;
    n_fail = 0;

    % cd to output_dir so generate_1D_fun_1 writes .1D files there
    orig_dir = pwd;
    cd(output_dir);

    for k = 1:numel(mats)
        in_path = fullfile(mats(k).folder, mats(k).name);
        [~, stem, ~] = fileparts(mats(k).name);
        % stem = sub-<ID>_task-BlockStim_run-01_filtered

        % Extract task and run from the stem
        tok = regexp(stem, '_task-(\w+)_run-(\d+)_filtered', 'tokens');
        if isempty(tok)
            fprintf('[%d/%d] SKIP (cannot parse task/run): %s\n', k, numel(mats), mats(k).name);
            n_fail = n_fail + 1;
            continue;
        end
        task_name = tok{1}{1};   % e.g. BlockStim
        run_num   = tok{1}{2};   % e.g. 01

        % Per-run cardiac decision: a manifest entry overrides the global flag.
        use_cardiac_run = use_cardiac;
        dkey = sprintf('%s|%s', task_name, run_num);
        if isKey(decision_map, dkey)
            use_cardiac_run = decision_map(dkey);
        end

        % BIDS filename base for this run
        fname_base = sprintf('%s_ses-%s_task-%s_run-%s_bold', ...
                             bids_subject_id, session, task_name, run_num);

        fprintf('[%d/%d] %s  (fnameBase: %s)\n', k, numel(mats), mats(k).name, fname_base);

        try
            raw = load(in_path);
            if ~isfield(raw, 'physio')
                error('physio struct not found — re-run step04.');
            end
            physio = raw.physio;

            % ── Load R-DECO peaks if available (and cardiac enabled) ──────────
            % stem(1:end-9) strips '_filtered'
            if use_cardiac_run
                rdeco_path = fullfile(input_dir, [stem(1:end-9) '_rdeco.mat']);
                [piezoout, has_rdeco] = load_rdeco_optional(rdeco_path);
                if has_rdeco
                    physio.piezoout = piezoout;
                    fprintf('  R-DECO: loaded %d R-peaks\n', numel(piezoout));
                else
                    piezoout = [];
                    fprintf('  R-DECO: not found — cardiac regressors will be skipped\n');
                end
            else
                piezoout = [];
                has_rdeco = false;
                if isKey(decision_map, dkey)
                    fprintf('  [PIEZO-SKIP] manifest decision: respiration-only (R-DECO skipped)\n');
                else
                    fprintf('  Cardiac OFF — respiration-only (R-DECO skipped)\n');
                end
            end

            % ── Read TR from BIDS JSON sidecar ────────────────────────────────
            json_path = fullfile(sourcedata_dir, bids_subject_id, ...
                sprintf('ses-%s', session), 'func', [fname_base '.json']);
            TR = read_tr_from_json(json_path, tr_fallback);
            fprintf('  TR = %.4f s\n', TR);

            % ── Detect volume triggers from MRTRIG ────────────────────────────
            MRTRIG_diff = diff(physio.MRTRIG(:));
            [~, trig_idx] = findpeaks(MRTRIG_diff, 'MinPeakHeight', 2);
            vols = numel(trig_idx);
            if vols == 0
                error('No MR triggers detected in MRTRIG — check physio mat.');
            end
            fprintf('  Volumes: %d\n', vols);

            % ── Build cardiac inputs ──────────────────────────────────────────
            % HBsignal = filtered/envelope cardiac signal
            % R        = R-peak times in seconds (from R-DECO) or []
            if has_rdeco && ~isempty(physio.PIEZOF)
                HBsignal = physio.PIEZOF(:);
                R        = piezoout;
            else
                HBsignal = [];
                R        = [];
            end

            % ── Call generate_1D_fun_1 ────────────────────────────────────────
            % (cd to output_dir already done above)
            generate_1D_fun_1( ...
                HBsignal, ...
                R, ...
                physio.RESP(:), ...
                vols, ...
                TR, ...
                sr, ...
                fs_out, ...
                SMS, ...
                physio.MRTRIG(:), ...
                fname_base);

            fprintf('  -> RETRO-resp_%s.1D  written\n', fname_base);
            if has_rdeco
                fprintf('  -> RETRO-qrs_%s.1D  written\n', fname_base);
            end
            n_ok = n_ok + 1;

        catch ME
            fprintf('  FAILED: %s\n', ME.message);
            n_fail = n_fail + 1;
        end
    end

    cd(orig_dir);

    fprintf('\n========================================\n');
    fprintf(' Done.  OK: %d  |  Failed: %d\n', n_ok, n_fail);
    fprintf(' 1D files in: %s\n', output_dir);
    fprintf('========================================\n\n');

    % ── Fail-fast (Task 31) ───────────────────────────────────────────────────
    % A failed run leaves a BOLD without its 1D regressors, so RETROICOR would
    % silently run uncorrected on it. Throw so MATLAB -batch returns non-zero and
    % step04 aborts rather than proceeding on incomplete physio.
    if n_fail > 0
        error('preproc_generate_1D_v2:failures', ...
              '%d of %d sequence(s) FAILED 1D generation (see log above). Aborting.', ...
              n_fail, numel(mats));
    end
end


% ── Helpers ───────────────────────────────────────────────────────────────────

function m = load_decision_map(decision_file)
% Load per-run cardiac decisions from a GUI-written manifest CSV.
% Columns (header required): subject,task,run,use_cardiac,verdict,decided_by,timestamp
% Returns a containers.Map keyed 'task|run' -> logical(use_cardiac); empty if no file.

    m = containers.Map('KeyType', 'char', 'ValueType', 'any');
    if isempty(decision_file) || ~exist(decision_file, 'file')
        return;
    end
    try
        T = readtable(decision_file, 'Delimiter', ',', 'TextType', 'char');
    catch ME
        warning('Could not read decision manifest %s: %s', decision_file, ME.message);
        return;
    end
    vn = T.Properties.VariableNames;
    if ~all(ismember({'task', 'run', 'use_cardiac'}, vn))
        warning('Decision manifest missing task/run/use_cardiac columns: %s', decision_file);
        return;
    end
    for i = 1:height(T)
        task = char(string(T.task(i)));
        run  = char(string(T.run(i)));
        rnum = str2double(run);             % normalise run to 2-digit ('01')
        if ~isnan(rnum), run = sprintf('%02d', rnum); end
        uc = T.use_cardiac(i);
        if iscell(uc), uc = uc{1}; end
        m(sprintf('%s|%s', task, run)) = logical(str2double(string(uc)));
    end
    fprintf(' Loaded %d cardiac decision(s) from %s\n', m.Count, decision_file);
end

function [piezoout, found] = load_rdeco_optional(rdeco_path)
% Load R-DECO output and return R-peak times in seconds.
% Returns empty [] and found=false if file absent or unusable.

    piezoout = [];
    found    = false;

    if ~exist(rdeco_path, 'file')
        return;
    end

    try
        d = load(rdeco_path);

        % R-DECO typically saves: d.data.R_loc  (cell of datenums or datetimes)
        if isfield(d, 'data') && isstruct(d.data) && isfield(d.data, 'R_loc')
            piezoout = rloc_to_seconds(d.data.R_loc);
        elseif isfield(d, 'R_loc')
            piezoout = rloc_to_seconds(d.R_loc);
        else
            return;
        end
        found = ~isempty(piezoout);
    catch
        % Silently ignore corrupt R-DECO files
    end
end


function r = rloc_to_seconds(R_loc)
% Convert R-DECO R_loc (cell of datenums or datetimes) to a seconds array.

    t = R_loc;
    if iscell(t)
        t = t{1, 1};
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
            r(j) = H * 3600 + MN * 60 + S;
        end
        return;
    end

    error('Unsupported R_loc type: %s', class(t));
end


function TR = read_tr_from_json(json_path, fallback)
% Read RepetitionTime from a BIDS JSON sidecar.  Returns fallback on any error.

    TR = fallback;
    if ~exist(json_path, 'file')
        fprintf('  WARNING: JSON sidecar not found — using TR=%.4f\n  %s\n', fallback, json_path);
        return;
    end
    try
        js = jsondecode(fileread(json_path));
        if isfield(js, 'RepetitionTime')
            TR = js.RepetitionTime;
        end
    catch
    end
end
