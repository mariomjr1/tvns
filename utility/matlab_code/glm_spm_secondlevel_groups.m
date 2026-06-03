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
%   glm_spm_secondlevel_groups(..., 'Tasks',{'BlockStim','ContinuousStim','rest'}, ...
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
%   Tasks       cellstr of task folder names (default {'BlockStim','ContinuousStim','rest'})
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
    addParameter(p, 'Tasks',      {'BlockStim','ContinuousStim','rest'}, @iscell);
    addParameter(p, 'DoCombined', true, @(x) islogical(x)||isnumeric(x));
    parse(p, task_root, cases_list_file, controls_list_file, output_dir, spm_dir, varargin{:});

    task_root   = char(p.Results.task_root);
    output_dir  = char(p.Results.output_dir);
    spm_dir     = char(p.Results.spm_dir);
    tasks       = p.Results.Tasks;
    do_combined = logical(p.Results.DoCombined);

    set(0, 'DefaultFigureVisible', 'off');
    addpath(spm_dir);
    spm('defaults', 'FMRI');
    spm_jobman('initcfg');
    spm_get_defaults('cmdline', true);

    if ~exist(output_dir, 'dir'), mkdir(output_dir); end

    cases    = read_list(char(p.Results.cases_list_file));
    controls = read_list(char(p.Results.controls_list_file));

    fprintf('\n========================================\n');
    fprintf(' glm_spm_secondlevel_groups (Step 08 Part 2)\n');
    fprintf('========================================\n');
    fprintf(' Task root:  %s\n', task_root);
    fprintf(' Output:     %s\n', output_dir);
    fprintf(' Cases:      %d subjects\n', numel(cases));
    fprintf(' Controls:   %d subjects\n', numel(controls));
    fprintf(' Tasks:      %s\n\n', strjoin(tasks, ', '));

    % ── Per-task two-sample tests ─────────────────────────────────────────────
    block_idx = find(strcmpi(tasks, 'BlockStim'),      1);
    cont_idx  = find(strcmpi(tasks, 'ContinuousStim'), 1);

    for t = 1:numel(tasks)
        task = tasks{t};
        task_dir = fullfile(task_root, task);
        [case_files, ctrl_files] = collect_group_files(task_dir, cases, controls, task);
        run_two_sample(case_files, ctrl_files, ...
            fullfile(output_dir, task), task);
    end

    % ── Combined Block + Continuous ───────────────────────────────────────────
    if do_combined && ~isempty(block_idx) && ~isempty(cont_idx)
        [cb, ctb] = collect_group_files(fullfile(task_root, tasks{block_idx}), cases, controls, 'BlockStim');
        [cc, ctc] = collect_group_files(fullfile(task_root, tasks{cont_idx}),  cases, controls, 'ContinuousStim');
        run_two_sample([cb; cc], [ctb; ctc], ...
            fullfile(output_dir, 'Combined_Block_Continuous'), 'Combined (Block + Continuous)');
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


% ── Two-sample t-test ─────────────────────────────────────────────────────────

function run_two_sample(case_files, ctrl_files, out_dir, label)
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
    mb{1}.spm.stats.factorial_design.cov = struct('c', {}, 'cname', {}, 'iCFI', {}, 'iCC', {});
    mb{1}.spm.stats.factorial_design.multi_cov = struct('files', {}, 'iCFI', {}, 'iCC', {});
    mb{1}.spm.stats.factorial_design.masking.tm.tm_none = 1;
    mb{1}.spm.stats.factorial_design.masking.im = 1;
    mb{1}.spm.stats.factorial_design.masking.em = {''};
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
