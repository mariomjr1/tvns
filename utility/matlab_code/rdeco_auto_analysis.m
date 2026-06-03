function rdeco_auto_analysis(rpiezo_file, output_file, qc_file, rdeco_dir, varargin)
% RDECO_AUTO_ANALYSIS
% Headless (no-GUI) R-DECO R-peak detection on a *_rpiezo.mat file.
%
% Reproduces the manual R-DECO workflow automatically:
%   1. Resample the signal to 1000 Hz (if needed)
%   2. Detect Peaks with R-DECO's peak_detection at the MIN and MAX envelope
%      sizes, with ectopic removal — then merge both peak sets
%   3. Maximum search: snap each peak to the local signal maximum
%      (peak_detection already snaps ±65 ms; we add a final snap + merge)
%   4. Delete side-by-side peaks that push the instantaneous heart rate above
%      a threshold (> 150 bpm): the doubled beat is removed by deleting the
%      lower-amplitude peak of the offending pair
%   5. Save a QC image (ECG with peaks on top, HR in bpm below)
%   6. Save results as <output_file> in R-DECO's native format
%      (struct 'data' with data.R_loc{1} as a datetime array), so step05's
%      loader reads it identically to a manually-saved R-DECO file.
%
% Usage:
%   rdeco_auto_analysis(rpiezo_file, output_file, qc_file, rdeco_dir)
%   rdeco_auto_analysis(..., 'Fs',1000, 'EnvMin',300, 'EnvMax',500, ...
%                            'Ectopic',true, 'HrMaxBpm',150, 'Inverted',false, ...
%                            'AvgHR',100)
%
% Required:
%   rpiezo_file  *_rpiezo.mat from step04 (variable RPIEZO_signal, 1000 Hz)
%   output_file  destination *_rdeco.mat
%   qc_file      destination QC png
%   rdeco_dir    path to utility/r-deco-master (peak_detection lives under it)
%
% Optional name-value:
%   Fs        target sampling rate Hz                (default 1000)
%   EnvMin    minimum envelope size ms               (default 300)
%   EnvMax    maximum envelope size ms               (default 500)
%   Ectopic   ectopic removal on/off                 (default true)
%   HrMaxBpm  delete doubled peaks above this HR     (default 150)
%   Inverted  treat the signal as inverted           (default false)
%   AvgHR     expected average HR for thresholding   (default 100)
%
% Created by Mario Murakami

    % ── Parse inputs ──────────────────────────────────────────────────────────
    p = inputParser();
    addRequired(p, 'rpiezo_file');
    addRequired(p, 'output_file');
    addRequired(p, 'qc_file');
    addRequired(p, 'rdeco_dir');
    addParameter(p, 'Fs',       1000,  @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'EnvMin',   300,   @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'EnvMax',   500,   @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'Ectopic',  true,  @(x) islogical(x)||isnumeric(x));
    addParameter(p, 'HrMaxBpm', 150,   @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'Inverted', false, @(x) islogical(x)||isnumeric(x));
    addParameter(p, 'AvgHR',    100,   @(x) isnumeric(x)&&isscalar(x));
    parse(p, rpiezo_file, output_file, qc_file, rdeco_dir, varargin{:});

    fs        = p.Results.Fs;
    env_min   = p.Results.EnvMin;
    env_max   = p.Results.EnvMax;
    ectopic   = double(logical(p.Results.Ectopic));
    hr_max    = p.Results.HrMaxBpm;
    inverted  = double(logical(p.Results.Inverted));
    avgHR     = p.Results.AvgHR;
    rdeco_dir = char(p.Results.rdeco_dir);

    set(0, 'DefaultFigureVisible', 'off');

    % ── Add R-DECO algorithm folder to path ───────────────────────────────────
    alg_dir = fullfile(rdeco_dir, 'R_peak_detection', 'Algorithm');
    if ~exist(fullfile(alg_dir, 'peak_detection.m'), 'file')
        % fall back to a recursive search under rdeco_dir
        f = dir(fullfile(rdeco_dir, '**', 'peak_detection.m'));
        if isempty(f)
            error('peak_detection.m not found under %s', rdeco_dir);
        end
        alg_dir = f(1).folder;
    end
    addpath(alg_dir);

    fprintf('\n========================================\n');
    fprintf(' rdeco_auto_analysis\n');
    fprintf('========================================\n');
    fprintf(' Input:   %s\n', rpiezo_file);
    fprintf(' Output:  %s\n', output_file);
    fprintf(' QC:      %s\n', qc_file);
    fprintf(' Fs=%d  Env=[%d %d]ms  Ectopic=%d  HRmax=%d bpm  Inverted=%d\n\n', ...
            fs, env_min, env_max, ectopic, hr_max, inverted);

    % ── Load signal ───────────────────────────────────────────────────────────
    raw = load(rpiezo_file);
    if isfield(raw, 'RPIEZO_signal')
        sig = double(raw.RPIEZO_signal(:));
        in_fs = 1000;   % physioparse sampling rate
    else
        % Fall back to the first numeric vector in the file
        fns = fieldnames(raw);
        sig = [];
        for k = 1:numel(fns)
            v = raw.(fns{k});
            if isnumeric(v) && isvector(v) && numel(v) > 100
                sig = double(v(:)); break;
            end
        end
        if isempty(sig)
            error('No signal vector found in %s', rpiezo_file);
        end
        in_fs = 1000;
    end

    % ── 1. Resample to target fs ──────────────────────────────────────────────
    if in_fs ~= fs
        sig = resample(sig, fs, in_fs);
        fprintf('Resampled %d Hz -> %d Hz\n', in_fs, fs);
    end

    % ── 2. Detect peaks at min and max envelope, merge ────────────────────────
    peaks_min = run_peak_detection(sig, fs, env_min, avgHR, ectopic, inverted);
    peaks_max = run_peak_detection(sig, fs, env_max, avgHR, ectopic, inverted);
    fprintf('Peaks: env%d -> %d,  env%d -> %d\n', ...
            env_min, numel(peaks_min), env_max, numel(peaks_max));

    peaks = union(peaks_min(:), peaks_max(:));
    peaks = peaks(:)';

    if isempty(peaks)
        warning('No peaks detected — writing empty R-DECO result.');
        save_rdeco_empty(output_file);
        return;
    end

    % ── 3. Maximum search: snap to local max + merge near-duplicates ──────────
    peaks = snap_to_local_max(sig, peaks, round(0.050 * fs));   % ±50 ms
    peaks = merge_close_peaks(sig, peaks, round(0.100 * fs));   % merge < 100 ms apart

    % ── 4. Delete doubled peaks pushing HR above hr_max ───────────────────────
    [peaks, n_removed] = remove_doubled_peaks(sig, peaks, fs, hr_max);
    fprintf('Removed %d doubled peak(s) (HR > %d bpm)\n', n_removed, hr_max);

    % ── Heart rate timeseries ─────────────────────────────────────────────────
    peak_sec = peaks / fs;
    RR_sec   = diff(peak_sec);
    HR_bpm   = 60 ./ RR_sec;              % one value per interval
    HR_time  = peak_sec(2:end);           % attach HR to the 2nd peak of each pair

    fprintf('Final: %d peaks,  mean HR = %.1f bpm\n', numel(peaks), ...
            (numel(HR_bpm) > 0) * mean(HR_bpm));

    % ── 5. QC image ───────────────────────────────────────────────────────────
    make_qc_plot(sig, fs, peaks, HR_time, HR_bpm, qc_file, rpiezo_file);

    % ── 6. Save in R-DECO native format ───────────────────────────────────────
    % R-DECO stores data.R_loc{ii} as a datetime array (time-of-day = peak time).
    % Build it so step05's rloc_to_seconds() returns peak seconds.
    dt = datetime(2000,1,1,0,0,0) + seconds(peak_sec);
    dt.Format = 'hh:mm:ss.SSSS';

    data = struct();
    data.R_loc  = {dt};
    data.RR_int = {1000 * RR_sec};   % ms
    data.fs     = fs;
    data.source = rpiezo_file;
    save(output_file, 'data');

    fprintf('\nSaved R-DECO result: %s\n', output_file);
    fprintf('Saved QC image:      %s\n', qc_file);
    fprintf('========================================\n\n');
end


% ── Run R-DECO peak_detection for one envelope ────────────────────────────────

function peaks = run_peak_detection(sig, fs, env_ms, avgHR, ectopic, inverted)
    % parameters cell: {envelope_ms, avgHR, postproc, ectopic, inverted}
    parameters = {env_ms, avgHR, 1, ectopic, inverted};
    try
        [R_peak, ~, ~, check] = peak_detection(parameters, sig(:), fs, 0);
        if check && ~isempty(R_peak) && ~isempty(R_peak{1})
            peaks = double(R_peak{1}(:))';
        else
            peaks = [];
        end
    catch ME
        warning('peak_detection failed at env=%d: %s', env_ms, ME.message);
        peaks = [];
    end
end


% ── Snap each peak to the nearest local maximum ───────────────────────────────

function peaks = snap_to_local_max(sig, peaks, win)
    n = numel(sig);
    for k = 1:numel(peaks)
        a = max(1, peaks(k) - win);
        b = min(n, peaks(k) + win);
        [~, rel] = max(sig(a:b));
        peaks(k) = a + rel - 1;
    end
    peaks = unique(peaks);
end


% ── Merge peaks closer than min_gap, keeping the larger-amplitude one ─────────

function peaks = merge_close_peaks(sig, peaks, min_gap)
    peaks = sort(peaks);
    out = [];
    k = 1;
    while k <= numel(peaks)
        j = k;
        % grow a cluster of peaks within min_gap of each other
        while j < numel(peaks) && (peaks(j+1) - peaks(j)) < min_gap
            j = j + 1;
        end
        cluster = peaks(k:j);
        [~, idx] = max(sig(cluster));   % keep highest-amplitude peak
        out(end+1) = cluster(idx); %#ok<AGROW>
        k = j + 1;
    end
    peaks = unique(out);
end


% ── Remove doubled peaks that push HR above hr_max ────────────────────────────

function [peaks, n_removed] = remove_doubled_peaks(sig, peaks, fs, hr_max)
    n_removed = 0;
    min_rr = 60 / hr_max;   % seconds; HR>hr_max  <=>  RR < min_rr
    changed = true;
    while changed && numel(peaks) > 2
        changed = false;
        rr = diff(peaks) / fs;
        bad = find(rr < min_rr, 1, 'first');
        if ~isempty(bad)
            % The pair (bad, bad+1) is too close → delete the lower-amplitude one
            i1 = peaks(bad);
            i2 = peaks(bad + 1);
            if sig(i1) >= sig(i2)
                peaks(bad + 1) = [];   % delete the shorter (smaller-amplitude) one
            else
                peaks(bad) = [];
            end
            n_removed = n_removed + 1;
            changed = true;
        end
    end
end


% ── QC plot: ECG + peaks (top), HR in bpm (bottom) ────────────────────────────

function make_qc_plot(sig, fs, peaks, HR_time, HR_bpm, qc_file, src)
    t = (0:numel(sig)-1) / fs;

    fig = figure('visible', 'off', 'Position', [100 100 1400 700]);

    % Top: ECG/piezo with detected peaks
    ax1 = subplot(2, 1, 1);
    plot(ax1, t, sig, 'Color', [0.2 0.5 0.8], 'LineWidth', 0.5); hold(ax1, 'on');
    if ~isempty(peaks)
        scatter(ax1, peaks/fs, sig(peaks), 28, 'r', 'filled');
    end
    ylabel(ax1, 'ECG / piezo (a.u.)');
    [~, nm, ex] = fileparts(src);
    title(ax1, sprintf('R-DECO auto — %s%s   (%d peaks)', nm, ex, numel(peaks)), ...
          'Interpreter', 'none');
    grid(ax1, 'on'); legend(ax1, {'signal', 'R-peaks'}, 'Location', 'northeast');

    % Bottom: heart rate (bpm)
    ax2 = subplot(2, 1, 2);
    if ~isempty(HR_bpm)
        plot(ax2, HR_time, HR_bpm, '-o', 'Color', [0.85 0.33 0.1], ...
             'MarkerSize', 3, 'LineWidth', 0.8);
        yline(ax2, mean(HR_bpm), '--', sprintf('mean %.0f bpm', mean(HR_bpm)));
    end
    ylabel(ax2, 'Heart rate (bpm)');
    xlabel(ax2, 'Time (s)');
    grid(ax2, 'on');

    linkaxes([ax1, ax2], 'x');
    xlim(ax1, [t(1) t(end)]);

    % Ensure output dir exists
    outdir = fileparts(qc_file);
    if ~isempty(outdir) && ~exist(outdir, 'dir'), mkdir(outdir); end
    try
        exportgraphics(fig, qc_file, 'Resolution', 120);
    catch
        saveas(fig, qc_file);
    end
    close(fig);
end


% ── Save an empty R-DECO result ───────────────────────────────────────────────

function save_rdeco_empty(output_file)
    data = struct();
    data.R_loc  = {[]};
    data.RR_int = {[]};
    save(output_file, 'data');
end
