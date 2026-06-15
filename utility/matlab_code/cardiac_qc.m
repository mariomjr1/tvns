function cardiac_qc(preproc_dir, bids_subject_id, qc_dir, varargin)
% CARDIAC_QC  Quality check of the piezo R-peak annotation (Task: piezo QC).
%
% For each run of one subject it loads the filtered piezo signal (*_rpiezo.mat,
% variable RPIEZO_signal) and the R-DECO R-peaks (*_rdeco.mat, data.R_loc),
% computes cardiac-quality metrics, renders a visual QC PNG, and writes a
% per-run verdict (GOOD / SUSPECT / BAD) plus a recommendation of whether to run
% RETROICOR with cardiac+respiration ("both") or respiration-only ("resp").
%
% A bad piezo trace produces noisy / implausible R-peaks; using its cardiac
% RETROICOR regressors then injects structured noise rather than removing it, so
% for those runs you should proceed respiration-only (Step 05 'Cardiac', 0).
%
% Usage:
%   cardiac_qc(preproc_dir, bids_subject_id, qc_dir)
%   cardiac_qc(..., 'Fs',1000, 'HrMin',40, 'HrMax',150, ...
%                   'ImplausiblePctBad',20, 'CvBad',0.5, 'GapBadSec',3)
%
% Required:
%   preproc_dir       step04 output with <subj>_task-*_run-*_rpiezo.mat and *_rdeco.mat
%   bids_subject_id   BIDS subject (e.g. sub-7T1019HC042726)
%   qc_dir            output dir for PNGs + cardiac_qc.csv
%
% Optional name-value:
%   Fs                 piezo sampling rate Hz                (default 1000)
%   HrMin / HrMax      plausible HR band bpm                 (default 40 / 150)
%   ImplausiblePctBad  % implausible RR -> BAD               (default 20)
%   CvBad              RR coefficient-of-variation -> BAD    (default 0.5)
%   GapBadSec          max RR gap (s) -> BAD (missed beats)  (default 3)
%
% Output:
%   qc_dir/<subj>_task-*_run-*_cardiacqc.png   one per run
%   qc_dir/<subj>_cardiac_qc.csv               one row per run (verdict + metrics)
%
% Created by Mario Murakami  (piezo QC)

    p = inputParser();
    addRequired(p, 'preproc_dir');
    addRequired(p, 'bids_subject_id');
    addRequired(p, 'qc_dir');
    addParameter(p, 'Fs',                1000, @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'HrMin',             40,   @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'HrMax',             150,  @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'ImplausiblePctBad', 20,   @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'CvBad',             0.5,  @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'GapBadSec',         3,    @(x) isnumeric(x)&&isscalar(x));
    parse(p, preproc_dir, bids_subject_id, qc_dir, varargin{:});

    preproc_dir = char(p.Results.preproc_dir);
    subj        = char(p.Results.bids_subject_id);
    qc_dir      = char(p.Results.qc_dir);
    fs          = p.Results.Fs;
    hr_min      = p.Results.HrMin;
    hr_max      = p.Results.HrMax;
    imp_bad     = p.Results.ImplausiblePctBad;
    cv_bad      = p.Results.CvBad;
    gap_bad     = p.Results.GapBadSec;

    set(0, 'DefaultFigureVisible', 'off');
    if ~exist(qc_dir, 'dir'), mkdir(qc_dir); end

    % Find the per-run piezo signals
    rpiezos = dir(fullfile(preproc_dir, [subj '_task-*_run-*_rpiezo.mat']));
    if isempty(rpiezos)
        error('No %s_task-*_run-*_rpiezo.mat in %s', subj, preproc_dir);
    end

    fprintf('\n========================================\n');
    fprintf(' cardiac_qc — %s\n', subj);
    fprintf(' Preproc: %s\n', preproc_dir);
    fprintf(' HR band: [%g %g] bpm   BAD if impl>%g%% | CV>%g | gap>%gs\n', ...
            hr_min, hr_max, imp_bad, cv_bad, gap_bad);
    fprintf('========================================\n\n');

    csv_path = fullfile(qc_dir, [subj '_cardiac_qc.csv']);
    fid = fopen(csv_path, 'w');
    fprintf(fid, ['subject,task,run,n_beats,duration_s,mean_hr_bpm,sd_hr_bpm,' ...
                  'cv_rr,sdnn_ms,pct_implausible,max_gap_s,amp_cv,verdict,recommendation\n']);

    for k = 1:numel(rpiezos)
        rp_path = fullfile(rpiezos(k).folder, rpiezos(k).name);
        stem = erase(rpiezos(k).name, '_rpiezo.mat');   % subj_task-..._run-..
        tok = regexp(stem, '_task-(\w+)_run-(\d+)', 'tokens');
        if isempty(tok), task = 'NA'; run = 'NA'; else, task = tok{1}{1}; run = tok{1}{2}; end

        % Load signal
        sig = load_signal(rp_path);

        % Load R-DECO peaks (seconds)
        rdeco_path = fullfile(rpiezos(k).folder, [stem '_rdeco.mat']);
        peak_sec = load_rdeco_peaks(rdeco_path);

        m = compute_metrics(sig, fs, peak_sec, hr_min, hr_max);
        [verdict, recommendation, reasons] = decide(m, imp_bad, cv_bad, gap_bad);

        png_path = fullfile(qc_dir, [stem '_cardiacqc.png']);
        make_plot(sig, fs, peak_sec, m, hr_min, hr_max, verdict, recommendation, ...
                  reasons, png_path, stem);

        fprintf('[%s run-%s] %s -> %s  (beats=%d, HR=%.0f, CV=%.2f, impl=%.0f%%)\n', ...
                task, run, verdict, recommendation, m.n_beats, m.mean_hr, m.cv_rr, m.pct_impl);

        fprintf(fid, '%s,%s,%s,%d,%.1f,%.1f,%.1f,%.3f,%.1f,%.1f,%.2f,%.3f,%s,%s\n', ...
                subj, task, run, m.n_beats, m.dur, m.mean_hr, m.sd_hr, m.cv_rr, ...
                m.sdnn_ms, m.pct_impl, m.max_gap, m.amp_cv, verdict, recommendation);
    end

    fclose(fid);
    fprintf('\nWrote verdicts: %s\n', csv_path);
    fprintf('QC images:      %s\n========================================\n\n', qc_dir);
end


% ── Load piezo signal vector ──────────────────────────────────────────────────
function sig = load_signal(rp_path)
    raw = load(rp_path);
    if isfield(raw, 'RPIEZO_signal')
        sig = double(raw.RPIEZO_signal(:));
        return;
    end
    fns = fieldnames(raw);
    sig = [];
    for i = 1:numel(fns)
        v = raw.(fns{i});
        if isnumeric(v) && isvector(v) && numel(v) > 100
            sig = double(v(:)); return;
        end
    end
    error('No signal vector in %s', rp_path);
end


% ── Load R-DECO peaks as seconds (mirror of preproc_generate_1D_v2) ───────────
function r = load_rdeco_peaks(rdeco_path)
    r = [];
    if ~exist(rdeco_path, 'file'), return; end
    try
        d = load(rdeco_path);
        if isfield(d, 'data') && isstruct(d.data) && isfield(d.data, 'R_loc')
            t = d.data.R_loc;
        elseif isfield(d, 'R_loc')
            t = d.R_loc;
        else
            return;
        end
        if iscell(t), t = t{1, 1}; end
        if isempty(t), return; end
        if isdatetime(t)
            r = seconds(timeofday(t)); r = r(:)';
        elseif isnumeric(t)
            r = t(:)';
        end
    catch
    end
end


% ── Metrics ───────────────────────────────────────────────────────────────────
function m = compute_metrics(sig, fs, peak_sec, hr_min, hr_max)
    m.dur = numel(sig) / fs;
    peak_sec = peak_sec(peak_sec >= 0 & peak_sec <= m.dur);
    m.n_beats = numel(peak_sec);

    if m.n_beats < 3
        m.mean_hr = NaN; m.sd_hr = NaN; m.cv_rr = NaN; m.sdnn_ms = NaN;
        m.pct_impl = 100; m.max_gap = m.dur; m.amp_cv = NaN; m.peak_sec = peak_sec;
        m.rr = []; m.hr = []; return;
    end

    rr = diff(peak_sec);                 % seconds
    hr = 60 ./ rr;                       % bpm per interval
    m.rr = rr; m.hr = hr; m.peak_sec = peak_sec;
    m.mean_hr = mean(hr);
    m.sd_hr   = std(hr);
    m.cv_rr   = std(rr) / mean(rr);
    m.sdnn_ms = std(rr) * 1000;
    m.pct_impl = 100 * mean(hr < hr_min | hr > hr_max);
    m.max_gap  = max(rr);

    % beat amplitude consistency (CV of signal value at peaks)
    idx = round(peak_sec * fs);
    idx = min(max(idx, 1), numel(sig));
    amps = sig(idx);
    if mean(abs(amps)) > 0
        m.amp_cv = std(amps) / mean(abs(amps));
    else
        m.amp_cv = NaN;
    end
end


% ── Verdict ─────────────────────────────────────────────────────────────────--
function [verdict, recommendation, reasons] = decide(m, imp_bad, cv_bad, gap_bad)
    reasons = {};
    bad = false; suspect = false;

    if m.n_beats < 3
        verdict = 'BAD'; recommendation = 'resp';
        reasons = {'too few beats detected'}; return;
    end
    if m.pct_impl > imp_bad
        bad = true; reasons{end+1} = sprintf('%.0f%% implausible RR', m.pct_impl);
    elseif m.pct_impl > imp_bad/2.5
        suspect = true; reasons{end+1} = sprintf('%.0f%% implausible RR', m.pct_impl);
    end
    if m.cv_rr > cv_bad
        bad = true; reasons{end+1} = sprintf('RR CV %.2f', m.cv_rr);
    elseif m.cv_rr > cv_bad*0.6
        suspect = true; reasons{end+1} = sprintf('RR CV %.2f', m.cv_rr);
    end
    if m.max_gap > gap_bad
        bad = true; reasons{end+1} = sprintf('%.1fs gap (missed beats)', m.max_gap);
    end
    if ~isnan(m.amp_cv) && m.amp_cv > 0.6
        suspect = true; reasons{end+1} = sprintf('amp CV %.2f', m.amp_cv);
    end

    if bad
        verdict = 'BAD'; recommendation = 'resp';
    elseif suspect
        verdict = 'SUSPECT'; recommendation = 'both';
    else
        verdict = 'GOOD'; recommendation = 'both';
        reasons = {'clean R-peaks, physiological HR'};
    end
end


% ── QC plot ─────────────────────────────────────────────────────────────────--
function make_plot(sig, fs, peak_sec, m, hr_min, hr_max, verdict, rec, reasons, png_path, ttl)
    t = (0:numel(sig)-1) / fs;
    col = struct('GOOD', [0.20 0.60 0.30], 'SUSPECT', [0.85 0.65 0.10], 'BAD', [0.80 0.20 0.15]);
    vc = col.(verdict);

    fig = figure('visible', 'off', 'Position', [100 100 1500 850]);

    % (1) piezo + peaks
    ax1 = subplot(3, 1, 1);
    plot(ax1, t, sig, 'Color', [0.2 0.5 0.8], 'LineWidth', 0.5); hold(ax1, 'on');
    if ~isempty(m.peak_sec)
        idx = min(max(round(m.peak_sec*fs),1), numel(sig));
        scatter(ax1, m.peak_sec, sig(idx), 22, 'r', 'filled');
    end
    grid(ax1, 'on'); ylabel(ax1, 'piezo (a.u.)');
    title(ax1, sprintf('%s    [%s → run %s]', ttl, verdict, upper(rec)), ...
          'Interpreter', 'none', 'Color', vc, 'FontWeight', 'bold');

    % (2) RR tachogram, implausible highlighted
    ax2 = subplot(3, 1, 2);
    if ~isempty(m.rr)
        tt = m.peak_sec(2:end);
        plot(ax2, tt, m.hr, '-o', 'Color', [0.85 0.33 0.1], 'MarkerSize', 3); hold(ax2, 'on');
        impl = m.hr < hr_min | m.hr > hr_max;
        if any(impl), scatter(ax2, tt(impl), m.hr(impl), 36, 'r', 'x', 'LineWidth', 1.2); end
        yline(ax2, hr_min, '--', 'HR min'); yline(ax2, hr_max, '--', 'HR max');
        if ~isnan(m.mean_hr), yline(ax2, m.mean_hr, '-', sprintf('mean %.0f', m.mean_hr)); end
    end
    grid(ax2, 'on'); ylabel(ax2, 'HR (bpm)'); xlabel(ax2, 'Time (s)');
    linkaxes([ax1, ax2], 'x'); xlim(ax1, [t(1) t(end)]);

    % (3) RR histogram + metrics text
    ax3 = subplot(3, 1, 3);
    if ~isempty(m.rr)
        histogram(ax3, m.rr*1000, 30, 'FaceColor', [0.3 0.5 0.7]);
        xlabel(ax3, 'RR interval (ms)'); ylabel(ax3, 'count'); grid(ax3, 'on');
    end
    txt = sprintf(['VERDICT: %s   →  proceed: %s\n' ...
                   'beats=%d   dur=%.0fs   HR=%.0f±%.0f bpm\n' ...
                   'RR CV=%.2f   SDNN=%.0f ms   implausible=%.0f%%\n' ...
                   'max gap=%.1fs   amp CV=%.2f\n%s'], ...
                  verdict, upper(rec), m.n_beats, m.dur, m.mean_hr, m.sd_hr, ...
                  m.cv_rr, m.sdnn_ms, m.pct_impl, m.max_gap, m.amp_cv, ...
                  strjoin(reasons, '; '));
    annotation(fig, 'textbox', [0.55 0.06 0.42 0.24], 'String', txt, ...
               'Interpreter', 'none', 'EdgeColor', vc, 'LineWidth', 1.5, ...
               'BackgroundColor', [1 1 1], 'Color', vc*0.7, 'FontSize', 10, ...
               'FitBoxToText', 'off', 'VerticalAlignment', 'top');

    outdir = fileparts(png_path);
    if ~isempty(outdir) && ~exist(outdir, 'dir'), mkdir(outdir); end
    try
        exportgraphics(fig, png_path, 'Resolution', 110);
    catch
        saveas(fig, png_path);
    end
    close(fig);
end
