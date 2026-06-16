function preproc_filter_per_sequence(input_dir, output_dir, bids_subject_id, varargin)
% PREPROC_FILTER_PER_SEQUENCE
% Filter per-sequence physio .mat files produced by physioparse step 03.
%
% Applies highpass + bandpass filtering and Hilbert envelope to the RPIEZO
% (cardiac piezo) channel.  Saves two output files per sequence:
%
%   *_filtered.mat   physio struct ready for preproc_generate_1D (step 05)
%   *_rpiezo.mat     plain RPIEZO_signal array — load this directly in R-DECO
%
% Usage:
%   preproc_filter_per_sequence(input_dir, output_dir, bids_subject_id)
%   preproc_filter_per_sequence(..., 'SR',1000, 'HP',0.05, 'BPL',0.5, 'BPH',2.0)
%
% Required inputs:
%   input_dir        physioparse parsed/ folder
%                    Expected mats: task-*_run-*.mat
%                    Top-level keys: RESP, RPIEZO, MRTRIG, STIMTRIG
%   output_dir       Destination folder (will be created if absent)
%   bids_subject_id  Full BIDS subject ID (e.g. sub-7T1019HC042726)
%
% Optional name-value parameters:
%   SR      Physio sampling rate (Hz)      default: 1000
%   HP      Highpass cutoff (Hz)           default: 0.05
%   BPL     Bandpass lower edge (Hz)       default: 0.5
%   BPH     Bandpass upper edge (Hz)       default: 2.0
%
% Output *_filtered.mat contains a 'physio' struct with fields:
%   RESP, RPIEZO, MRTRIG, STIMTRIG  — original signals
%   PIEZOF                          — bandpass-filtered RPIEZO
%   PIEZOD                          — Hilbert envelope of PIEZOF
%   name                            — output filename stem
%   sr                              — sampling rate
%   piezoout                        — [] placeholder; filled after R-DECO in step 05
%
% Run this script, then open each *_rpiezo.mat in R-DECO to detect R-peaks.
% Save R-DECO output as *_rdeco.mat in the same output_dir.
%
% Created by Mario Murakami

    % ── Parse name-value inputs ───────────────────────────────────────────────
    p = inputParser();
    addRequired( p, 'input_dir',       @(x) ischar(x) || isstring(x));
    addRequired( p, 'output_dir',      @(x) ischar(x) || isstring(x));
    addRequired( p, 'bids_subject_id', @(x) ischar(x) || isstring(x));
    addParameter(p, 'SR',  1000, @(x) isnumeric(x) && isscalar(x));
    addParameter(p, 'HP',  0.05, @(x) isnumeric(x) && isscalar(x));
    addParameter(p, 'BPL', 0.5,  @(x) isnumeric(x) && isscalar(x));
    addParameter(p, 'BPH', 2.0,  @(x) isnumeric(x) && isscalar(x));
    parse(p, input_dir, output_dir, bids_subject_id, varargin{:});

    input_dir       = char(p.Results.input_dir);
    output_dir      = char(p.Results.output_dir);
    bids_subject_id = char(p.Results.bids_subject_id);
    sr              = p.Results.SR;
    hp_cutoff       = p.Results.HP;
    bp_low          = p.Results.BPL;
    bp_high         = p.Results.BPH;

    % ── Validate and create directories ───────────────────────────────────────
    if ~exist(input_dir, 'dir')
        error('Input directory not found: %s', input_dir);
    end
    if ~exist(output_dir, 'dir')
        mkdir(output_dir);
    end

    % ── Find per-sequence mats ────────────────────────────────────────────────
    mats = dir(fullfile(input_dir, 'task-*_run-*.mat'));
    if isempty(mats)
        error('No task-*_run-*.mat files found in: %s\n  Run physioparse step 03 first.', input_dir);
    end

    fprintf('\n========================================\n');
    fprintf(' preproc_filter_per_sequence\n');
    fprintf('========================================\n');
    fprintf(' Subject:    %s\n', bids_subject_id);
    fprintf(' Input:      %s\n', input_dir);
    fprintf(' Output:     %s\n', output_dir);
    fprintf(' Filter:     HP=%.3f Hz  BP=[%.2f %.2f] Hz  SR=%d Hz\n', hp_cutoff, bp_low, bp_high, sr);
    fprintf(' Found mats: %d\n\n', numel(mats));

    n_ok        = 0;
    n_fail      = 0;
    failed_names = {};

    for k = 1:numel(mats)
        in_path = fullfile(mats(k).folder, mats(k).name);
        [~, stem, ~] = fileparts(mats(k).name);   % e.g. task-BlockStim_run-01

        % Output filenames include the BIDS subject ID
        out_stem   = sprintf('%s_%s', bids_subject_id, stem);
        out_filt   = fullfile(output_dir, [out_stem '_filtered.mat']);
        out_rpiezo = fullfile(output_dir, [out_stem '_rpiezo.mat']);

        fprintf('[%d/%d] %s\n', k, numel(mats), mats(k).name);

        try
            raw = load(in_path);

            % Validate required channels exist
            required = {'RESP', 'RPIEZO', 'MRTRIG', 'STIMTRIG'};
            for c = 1:numel(required)
                if ~isfield(raw, required{c})
                    error('Missing channel ''%s'' in %s', required{c}, mats(k).name);
                end
            end

            RESP     = raw.RESP(:);
            RPIEZO   = raw.RPIEZO(:);
            MRTRIG   = raw.MRTRIG(:);
            STIMTRIG = raw.STIMTRIG(:);

            % ── Validate channel CONTENT, not just existence (Task 31) ────────
            % A present-but-flat channel passes the field check above but then
            % produces garbage/NaN RETROICOR regressors with no error. Reject it.
            if isempty(RESP) || numel(RESP) < 2 || std(RESP) == 0
                error('RESP channel is empty or flat (zero variance) in %s', mats(k).name);
            end
            if isempty(RPIEZO) || numel(RPIEZO) < 2 || std(RPIEZO) == 0
                % Cardiac is optional — respiration-only is a valid fallback for
                % bad piezo — so a flat RPIEZO is a WARNING, not fatal. Cardiac QC
                % / the per-run decision routes this run to respiration-only.
                fprintf('  WARNING: RPIEZO is flat (zero variance) — cardiac unusable; use respiration-only\n');
            end
            % MRTRIG drives volume timing for trigger-bearing BOLD. Report zero
            % rising edges now; preproc_generate_1D_v2 makes the final (fatal)
            % determination once the sequence class is known.
            if sum(diff(MRTRIG) > 2) == 0
                fprintf('  WARNING: MRTRIG has no rising edges — trigger-bearing runs will fail in step04 Part 1\n');
            end

            % Remove DC offset from RESP (LabChart respiratory exports often carry
            % a large baseline offset); mirrors the mean removal in filter_cardiac.
            RESP = RESP - mean(RESP);

            % ── Filter RPIEZO (cardiac) ───────────────────────────────────────
            [PIEZOF, PIEZOD] = filter_cardiac(RPIEZO, sr, hp_cutoff, bp_low, bp_high);

            % ── Build physio struct (format expected by preproc_generate_1D) ──
            physio          = struct();
            physio.RESP     = RESP;
            physio.RPIEZO   = RPIEZO;
            physio.MRTRIG   = MRTRIG;
            physio.STIMTRIG = STIMTRIG;
            physio.PIEZOF   = PIEZOF;
            physio.PIEZOD   = PIEZOD;
            physio.name     = out_stem;
            physio.sr       = sr;
            physio.piezoout = [];   % R-DECO R-peaks go here after step 05

            save(out_filt, 'physio');
            fprintf('  -> %s\n', [out_stem '_filtered.mat']);

            % ── Save plain RPIEZO for R-DECO ──────────────────────────────────
            % R-DECO expects a plain numeric array, not a nested struct.
            RPIEZO_signal = RPIEZO; %#ok<NASGU>
            save(out_rpiezo, 'RPIEZO_signal');
            fprintf('  -> %s  (load in R-DECO)\n', [out_stem '_rpiezo.mat']);

            n_ok = n_ok + 1;

        catch ME
            fprintf('  FAILED: %s\n', ME.message);
            n_fail = n_fail + 1;
            failed_names{end+1} = mats(k).name; %#ok<AGROW>
        end
    end

    fprintf('\n========================================\n');
    fprintf(' Done.  OK: %d  |  Failed: %d\n', n_ok, n_fail);
    fprintf(' Output: %s\n', output_dir);

    % ── Flag + log + continue (Task 31) ───────────────────────────────────────
    % A failed sequence is skipped (its bad/garbage physio is not written), but the
    % step does NOT abort — the runs that succeeded are kept and the pipeline
    % continues. Failures are flagged here for review.
    if n_fail > 0
        warning('preproc_filter_per_sequence:failures', ...
                '%d of %d sequence(s) SKIPPED (see FAILED lines above): %s. Continuing with the %d that succeeded.', ...
                n_fail, numel(mats), strjoin(failed_names, ', '), n_ok);
    end

    fprintf('\nNext steps:\n');
    fprintf('  1. Open each *_rpiezo.mat in R-DECO (utility/r-deco-master/R_DECO.m)\n');
    fprintf('  2. Run automatic R-peak detection, correct manually if needed\n');
    fprintf('  3. Save R-DECO output as *_rdeco.mat in the same output folder\n');
    fprintf('  4. Run step04_retroicor_v2.sh to generate 1D files and run RETROICOR\n');
    fprintf('========================================\n\n');
end


% ── Signal filter ─────────────────────────────────────────────────────────────

function [hbf, hbd] = filter_cardiac(hb, sr, hp_cutoff, bp_low, bp_high)
% Filter cardiac (RPIEZO) signal for R-peak detection and RETROICOR.
%
% Steps:
%   1. Remove mean, highpass to eliminate baseline drift
%   2. Bandpass to isolate cardiac frequency band
%   3. Z-score normalise
%   4. Hilbert envelope (smooth, normalised) for threshold-based R-peak detection

    hb    = hb(:);
    hb    = hb - mean(hb);
    hb_hp = highpass(hb, hp_cutoff, sr);

    [b, a] = butter(3, [bp_low bp_high] / (sr / 2), 'bandpass');
    hbf    = filtfilt(b, a, hb_hp);
    hbf    = hbf ./ (std(hbf) + eps);

    hbd = abs(hilbert(hbf));
    hbd = movmean(hbd, round(0.03 * sr));
    hbd = hbd ./ (std(hbd) + eps);
end
