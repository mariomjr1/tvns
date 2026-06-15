function glm_spm_secondlevel_groups(task_root, cases_list_file, ...
        controls_list_file, output_dir, spm_dir, varargin)
% GLM_SPM_SECONDLEVEL_GROUPS  (Step 08, Part 2)
% Two-sample (cases vs controls) second-level analysis on the per-task folders
% produced by secondlevel_populate_tasks (Part 1).
%
% For each task folder under <task_root> it builds a two-sample t-test:
%   group 1 = cases, group 2 = controls
% and the contrasts:
%   1. Cases > Controls
%   2. Controls > Cases
%   3. Cases mean (> 0)
%   4. Controls mean (> 0)
%
% A combined BlockStim + ContinuousStim analysis is also run (con images pooled
% within each group across the two tasks).
%
% Usage:
%   glm_spm_secondlevel_groups(task_root, cases_list, controls_list, output_dir, spm_dir)
%   glm_spm_secondlevel_groups(..., 'Tasks',{'BlockStim','ContinuousStim'}, ...
%                                   'DoCombined',true)
%
% Required:
%   task_root           folder with per-task subfolders of <subject>.nii (Part 1 output)
%   cases_list_file     text file, one BIDS subject per line (the cases group)
%   controls_list_file  text file, one BIDS subject per line (the controls group)
%   output_dir          where group results are written
%   spm_dir             SPM12 path
%
% Optional name-value:
%   Tasks       cellstr of task folder names (default {'BlockStim','ContinuousStim'};
%               'rest' is a resting baseline, not a Stim contrast)
%   DoCombined  also run pooled Block+Continuous (default true)
%
% Output per analysis:
%   <output_dir>/<name>/SPM.mat + con_000*.nii + spmT_000*.nii
%
% Created by Mario Murakami

    p = inputParser();
    addRequired(p, 'task_root');
    addRequired(p, 'cases_list_file');
    addRequired(p, 'controls_list_file');
    addRequired(p, 'output_dir');
    addRequired(p, 'spm_dir');
    % 'rest' excluded by default — it is a resting baseline with no Stim contrast (Task 10).
    addParameter(p, 'Tasks',      {'BlockStim','ContinuousStim'}, @iscell);
    addParameter(p, 'DoCombined', true, @(x) islogical(x)||isnumeric(x));
    % Combined Block+Continuous analysis mode (optional analysis; default method):
    %   'average' = average each subject's Block+Continuous into ONE image, then
    %               two-sample test (one observation/subject — preserves independence). DEFAULT.
    %   'pool'    = legacy: enter both conditions per subject (double-counts subjects,
    %               inflates df/false positives). Kept for backward comparison only.
    addParameter(p, 'CombinedMode', 'average', @(x) ischar(x)||isstring(x));
    % Optional nuisance covariates (Task 08): subset of the column names in the
    % CovariatesFile (a TSV from build_group_covariates.py — e.g. age, sex, mean_fd).
    % Empty = no covariates (backward compatible). A covariate that is incomplete
    % for the analysed subjects is dropped (with a warning).
    addParameter(p, 'Covariates',     {}, @iscell);
    addParameter(p, 'CovariatesFile', '', @(x) ischar(x)||isstring(x));
    % Optional brainstem restriction (Task 05 C3): explicit mask for the factorial
    % design. Empty = whole-brain (default). Must be in the wcon (MNI) space.
    addParameter(p, 'BrainstemMask',  '', @(x) ischar(x)||isstring(x));
    parse(p, task_root, cases_list_file, controls_list_file, output_dir, spm_dir, varargin{:});

    task_root   = char(p.Results.task_root);
    output_dir  = char(p.Results.output_dir);
    spm_dir     = char(p.Results.spm_dir);
    tasks       = p.Results.Tasks;
    do_combined = logical(p.Results.DoCombined);
    combined_mode = lower(char(p.Results.CombinedMode));

    set(0, 'DefaultFigureVisible', 'off');
    addpath(spm_dir);
    spm('defaults', 'FMRI');
    spm_jobman('initcfg');
    spm_get_defaults('cmdline', true);

    if ~exist(output_dir, 'dir'), mkdir(output_dir); end

    cases    = read_list(char(p.Results.cases_list_file));
    controls = read_list(char(p.Results.controls_list_file));

    % ── Build covariate lookup (Task 08) ──────────────────────────────────────
    cov_names = p.Results.Covariates(:)';
    cov_file  = char(p.Results.CovariatesFile);
    cov_map   = build_cov_map(cov_file, cov_names);
    if isempty(cov_map.keys), cov_names = {}; end

    em_mask = char(p.Results.BrainstemMask);   % explicit mask (brainstem) or ''
    if ~isempty(em_mask) && exist(em_mask, 'file') ~= 2
        warning('BrainstemMask not found (%s) — group analysis stays whole-brain.', em_mask);
        em_mask = '';
    end

    fprintf('\n========================================\n');
    fprintf(' glm_spm_secondlevel_groups (Step 08 Part 2)\n');
    fprintf('========================================\n');
    fprintf(' Task root:  %s\n', task_root);
    fprintf(' Output:     %s\n', output_dir);
    fprintf(' Cases:      %d subjects\n', numel(cases));
    fprintf(' Controls:   %d subjects\n', numel(controls));
    fprintf(' Tasks:      %s\n', strjoin(tasks, ', '));
    if ~isempty(cov_names)
        fprintf(' Covariates: %s  (from %s)\n', strjoin(cov_names, ', '), cov_file);
    end
    fprintf('\n');

    % ── Per-task two-sample tests ─────────────────────────────────────────────
    block_idx = find(strcmpi(tasks, 'BlockStim'),      1);
    cont_idx  = find(strcmpi(tasks, 'ContinuousStim'), 1);

    for t = 1:numel(tasks)
        task = tasks{t};
        task_dir = fullfile(task_root, task);
        [case_files, ctrl_files] = collect_group_files(task_dir, cases, controls, task);
        run_two_sample(case_files, ctrl_files, ...
            fullfile(output_dir, task), task, cov_names, cov_map, em_mask);
    end

    % ── Combined Block + Continuous (optional) ────────────────────────────────
    if do_combined && ~isempty(block_idx) && ~isempty(cont_idx)
        bdir = fullfile(task_root, tasks{block_idx});
        cdir = fullfile(task_root, tasks{cont_idx});
        outc = fullfile(output_dir, 'Combined_Block_Continuous');
        if strcmpi(combined_mode, 'pool')
            % LEGACY (optional): pool both conditions — double-counts subjects.
            [cb, ctb] = collect_group_files(bdir, cases,    controls, 'BlockStim');
            [cc, ctc] = collect_group_files(cdir, cases,    controls, 'ContinuousStim');
            run_two_sample([cb; cc], [ctb; ctc], outc, ...
                'Combined (pooled — legacy, double-counts)', cov_names, cov_map, em_mask);
        else
            % DEFAULT: average Block+Continuous within subject → one obs/subject.
            fprintf('\n[Combined] within-subject average (one image per subject)\n');
            avgdir   = fullfile(outc, '_subject_avg');
            case_avg = make_combined_avg(bdir, cdir, cases,    avgdir);
            ctrl_avg = make_combined_avg(bdir, cdir, controls, avgdir);
            run_two_sample(case_avg, ctrl_avg, outc, ...
                'Combined (within-subject average)', cov_names, cov_map, em_mask);
        end
    end

    fprintf('\n========================================\n');
    fprintf(' ALL DONE. Group results in: %s\n', output_dir);
    fprintf('========================================\n\n');
end


% ── Collect per-group contrast files for one task ─────────────────────────────

function [case_files, ctrl_files] = collect_group_files(task_dir, cases, controls, label)
    case_files = {};
    ctrl_files = {};
    if ~exist(task_dir, 'dir')
        fprintf('[%s] task folder not found: %s — skipping.\n', label, task_dir);
        return
    end
    for k = 1:numel(cases)
        f = fullfile(task_dir, [cases{k} '.nii']);
        if exist(f, 'file'), case_files{end+1,1} = f; end %#ok<AGROW>
    end
    for k = 1:numel(controls)
        f = fullfile(task_dir, [controls{k} '.nii']);
        if exist(f, 'file'), ctrl_files{end+1,1} = f; end %#ok<AGROW>
    end
    fprintf('[%s] cases: %d/%d found,  controls: %d/%d found\n', ...
            label, numel(case_files), numel(cases), numel(ctrl_files), numel(controls));
end


% ── Build subject -> covariate-struct map from a covariates TSV (Task 08) ─────

function m = build_cov_map(cov_file, cov_names)
% Reads the TSV from build_group_covariates.py and returns a containers.Map
% (participant_id -> struct with one numeric field per requested covariate;
% missing/blank values become NaN and cause that covariate to be dropped later).
    m = containers.Map('KeyType', 'char', 'ValueType', 'any');
    if isempty(cov_names) || isempty(cov_file) || exist(cov_file, 'file') ~= 2
        return
    end
    T = readtable(cov_file, 'FileType', 'text', 'Delimiter', '\t');
    vars = T.Properties.VariableNames;
    if ~ismember('participant_id', vars)
        warning('Covariates file has no participant_id column: %s', cov_file);
        return
    end
    for r = 1:height(T)
        sid = char(string(T.participant_id(r)));
        s = struct();
        for cn = cov_names
            nm = cn{1};
            if ismember(nm, vars)
                val = T.(nm)(r);
                if iscell(val), val = val{1}; end
                s.(nm) = double(str2double(string(val)));   % blank/non-numeric -> NaN
            else
                s.(nm) = NaN;
            end
        end
        m(sid) = s;
    end
end


% ── Within-subject average of Block + Continuous (combined, default) ──────────

function out = make_combined_avg(block_dir, cont_dir, subjects, out_dir)
% For each subject, average its <subj>.nii from block_dir and cont_dir into ONE
% image (so the combined two-sample test gets one observation per subject, not
% two — preserving independence). If a subject has only one condition, that
% image is used as-is. Returns a column cell of averaged-image paths.
    out = {};
    if ~exist(out_dir, 'dir'), mkdir(out_dir); end
    for k = 1:numel(subjects)
        s = subjects{k};
        fb = fullfile(block_dir, [s '.nii']);
        fc = fullfile(cont_dir,  [s '.nii']);
        have = {};
        if exist(fb, 'file'), have{end+1} = fb; end %#ok<AGROW>
        if exist(fc, 'file'), have{end+1} = fc; end %#ok<AGROW>
        if isempty(have), continue; end
        avg = fullfile(out_dir, [s '.nii']);
        if numel(have) == 1
            copyfile(have{1}, avg);
        else
            spm_imcalc(char(have{1}, have{2}), avg, '(i1+i2)/2');
        end
        out{end+1, 1} = avg; %#ok<AGROW>
    end
    fprintf('[Combined] averaged %d subject image(s) -> %s\n', numel(out), out_dir);
end


% ── Two-sample t-test ─────────────────────────────────────────────────────────

function run_two_sample(case_files, ctrl_files, out_dir, label, cov_names, cov_map, em_mask)
    if nargin < 5, cov_names = {}; end
    if nargin < 6, cov_map = containers.Map('KeyType','char','ValueType','any'); end
    if nargin < 7, em_mask = ''; end
    if numel(case_files) < 2 || numel(ctrl_files) < 2
        fprintf('[%s] need >= 2 subjects per group (cases=%d, controls=%d). Skipping.\n', ...
                label, numel(case_files), numel(ctrl_files));
        return
    end
    if ~exist(out_dir, 'dir'), mkdir(out_dir); end
    spm_mat = fullfile(out_dir, 'SPM.mat');
    if exist(spm_mat, 'file'), delete(spm_mat); end

    scans1 = cellfun(@(f) [f ',1'], case_files, 'UniformOutput', false);
    scans2 = cellfun(@(f) [f ',1'], ctrl_files, 'UniformOutput', false);

    % ── Covariates (Task 08): one value per scan, order = [cases; controls] ───
    cov = struct('c', {}, 'cname', {}, 'iCFI', {}, 'iCC', {});
    if ~isempty(cov_names)
        ordered = [case_files(:); ctrl_files(:)];
        sids = cell(numel(ordered), 1);
        for ii = 1:numel(ordered)
            [~, sids{ii}] = fileparts(ordered{ii});   % <subj> from <subj>.nii
        end
        for cn = cov_names
            nm  = cn{1};
            vec = nan(numel(sids), 1);
            for ii = 1:numel(sids)
                if isKey(cov_map, sids{ii})
                    sv = cov_map(sids{ii});
                    if isfield(sv, nm), vec(ii) = sv.(nm); end
                end
            end
            if any(~isfinite(vec))
                warning('[%s] covariate "%s" missing for %d subject(s) — omitting it.', ...
                        label, nm, sum(~isfinite(vec)));
                continue
            end
            cov(end+1) = struct('c', vec, 'cname', nm, 'iCFI', 1, 'iCC', 1); %#ok<AGROW>
        end
        if ~isempty(cov)
            fprintf('[%s] covariates: %s\n', label, strjoin({cov.cname}, ', '));
        end
    end

    fprintf('\n[%s] two-sample t-test  (cases=%d, controls=%d)\n', ...
            label, numel(scans1), numel(scans2));

    % ── 1) Two-sample design ──────────────────────────────────────────────────
    mb = [];
    mb{1}.spm.stats.factorial_design.dir = {out_dir};
    mb{1}.spm.stats.factorial_design.des.t2.scans1 = scans1;
    mb{1}.spm.stats.factorial_design.des.t2.scans2 = scans2;
    mb{1}.spm.stats.factorial_design.des.t2.dept = 0;
    mb{1}.spm.stats.factorial_design.des.t2.variance = 1;   % unequal variance
    mb{1}.spm.stats.factorial_design.des.t2.gmsca = 0;
    mb{1}.spm.stats.factorial_design.des.t2.ancova = 0;
    mb{1}.spm.stats.factorial_design.cov = cov;
    mb{1}.spm.stats.factorial_design.multi_cov = struct('files', {}, 'iCFI', {}, 'iCC', {});
    mb{1}.spm.stats.factorial_design.masking.tm.tm_none = 1;
    mb{1}.spm.stats.factorial_design.masking.im = 1;
    if isempty(em_mask)
        mb{1}.spm.stats.factorial_design.masking.em = {''};
    else
        mb{1}.spm.stats.factorial_design.masking.em = {[em_mask ',1']};
        fprintf('[%s] explicit brainstem mask: %s\n', label, em_mask);
    end
    mb{1}.spm.stats.factorial_design.globalc.g_omit = 1;
    mb{1}.spm.stats.factorial_design.globalm.gmsca.gmsca_no = 1;
    mb{1}.spm.stats.factorial_design.globalm.glonorm = 1;
    spm_jobman('run', mb);

    % ── 2) Estimate ───────────────────────────────────────────────────────────
    mb = [];
    mb{1}.spm.stats.fmri_est.spmmat = {spm_mat};
    mb{1}.spm.stats.fmri_est.write_residuals = 0;
    mb{1}.spm.stats.fmri_est.method.Classical = 1;
    spm_jobman('run', mb);

    % ── 3) Contrasts (design columns: [cases controls]) ───────────────────────
    mb = [];
    mb{1}.spm.stats.con.spmmat = {spm_mat};
    mb{1}.spm.stats.con.delete = 1;
    mb{1}.spm.stats.con.consess{1}.tcon.name    = 'Cases > Controls';
    mb{1}.spm.stats.con.consess{1}.tcon.weights = [1 -1];
    mb{1}.spm.stats.con.consess{1}.tcon.sessrep = 'none';
    mb{1}.spm.stats.con.consess{2}.tcon.name    = 'Controls > Cases';
    mb{1}.spm.stats.con.consess{2}.tcon.weights = [-1 1];
    mb{1}.spm.stats.con.consess{2}.tcon.sessrep = 'none';
    mb{1}.spm.stats.con.consess{3}.tcon.name    = 'Cases mean';
    mb{1}.spm.stats.con.consess{3}.tcon.weights = [1 0];
    mb{1}.spm.stats.con.consess{3}.tcon.sessrep = 'none';
    mb{1}.spm.stats.con.consess{4}.tcon.name    = 'Controls mean';
    mb{1}.spm.stats.con.consess{4}.tcon.weights = [0 1];
    mb{1}.spm.stats.con.consess{4}.tcon.sessrep = 'none';
    spm_jobman('run', mb);

    fprintf('[%s] done -> %s\n', label, out_dir);
end


% ── Helper ────────────────────────────────────────────────────────────────────

function names = read_list(fname)
    names = {};
    if isempty(fname) || ~exist(fname, 'file')
        error('Subject list not found: %s', fname);
    end
    fid = fopen(fname, 'r');
    tline = fgetl(fid);
    while ischar(tline)
        s = strtrim(tline);
        if ~isempty(s)
            if ~startsWith(s, 'sub-'), s = ['sub-' s]; end
            names{end+1} = s; %#ok<AGROW>
        end
        tline = fgetl(fid);
    end
    fclose(fid);
end
