function glm_spm_secondlevel_v2(block_dir, continuous_dir, rest_dir, ...
        output_dir, spm_dir, varargin)
% GLM_SPM_SECONDLEVEL_V2
% Second-level (group) one-sample t-tests on first-level contrast images.
%
% Runs FOUR group analyses:
%   1. BlockStim        — one-sample t-test over all BlockStim con images
%   2. ContinuousStim   — one-sample t-test over all ContinuousStim con images
%   3. rest             — one-sample t-test over all rest con images
%   4. Combined         — BlockStim + ContinuousStim con images pooled
%
% Each task folder is searched RECURSIVELY for the contrast image
% (default con_0001.nii). One image found = one subject in the group test.
%
% Usage:
%   glm_spm_secondlevel_v2(block_dir, continuous_dir, rest_dir, output_dir, spm_dir)
%   glm_spm_secondlevel_v2(..., 'ConName','wcon_0001.nii', 'DoCombined',true)
%
% Required:
%   block_dir       folder holding BlockStim first-level outputs
%   continuous_dir  folder holding ContinuousStim first-level outputs
%   rest_dir        folder holding rest first-level outputs
%                   (pass '' to skip any of these three)
%   output_dir      where group results are written (created if absent)
%   spm_dir         SPM12 installation path
%
% Optional name-value:
%   ConName     contrast image filename to collect (default 'con_0001.nii')
%               NOTE: group stats require a COMMON space — use the MNI-warped
%               'wcon_0001.nii' from step07 for valid group inference.
%   DoCombined  logical — also run the pooled Block+Continuous test (default true)
%
% Output per analysis:
%   output_dir/<name>/SPM.mat
%   output_dir/<name>/con_0001.nii  (group mean > 0)
%   output_dir/<name>/spmT_0001.nii (positive t)
%   output_dir/<name>/spmT_0002.nii (negative t)
%
% Created by Mario Murakami

    % ── Parse inputs ──────────────────────────────────────────────────────────
    p = inputParser();
    addRequired(p, 'block_dir');
    addRequired(p, 'continuous_dir');
    addRequired(p, 'rest_dir');
    addRequired(p, 'output_dir');
    addRequired(p, 'spm_dir');
    addParameter(p, 'ConName',    'con_0001.nii', @(x) ischar(x)||isstring(x));
    addParameter(p, 'DoCombined', true, @(x) islogical(x)||isnumeric(x));
    parse(p, block_dir, continuous_dir, rest_dir, output_dir, spm_dir, varargin{:});

    con_name    = char(p.Results.ConName);
    do_combined = logical(p.Results.DoCombined);
    spm_dir     = char(p.Results.spm_dir);
    output_dir  = char(p.Results.output_dir);

    % ── Setup SPM ─────────────────────────────────────────────────────────────
    set(0, 'DefaultFigureVisible', 'off');
    addpath(spm_dir);
    spm('defaults', 'FMRI');
    spm_jobman('initcfg');
    spm_get_defaults('cmdline', true);

    if ~exist(output_dir, 'dir'), mkdir(output_dir); end

    fprintf('\n========================================\n');
    fprintf(' glm_spm_secondlevel_v2\n');
    fprintf('========================================\n');
    fprintf(' Contrast image: %s\n', con_name);
    fprintf(' Output:         %s\n', output_dir);
    fprintf(' DoCombined:     %d\n\n', do_combined);

    % ── Collect contrast images per task ──────────────────────────────────────
    block_cons      = collect_cons(char(block_dir),      con_name, 'BlockStim');
    continuous_cons = collect_cons(char(continuous_dir), con_name, 'ContinuousStim');
    rest_cons       = collect_cons(char(rest_dir),       con_name, 'rest');

    % ── Run each one-sample t-test ────────────────────────────────────────────
    if ~isempty(block_cons)
        run_one_sample(block_cons, fullfile(output_dir, 'BlockStim'), 'BlockStim');
    end
    if ~isempty(continuous_cons)
        run_one_sample(continuous_cons, fullfile(output_dir, 'ContinuousStim'), 'ContinuousStim');
    end
    if ~isempty(rest_cons)
        run_one_sample(rest_cons, fullfile(output_dir, 'rest'), 'rest');
    end

    % ── Combined Block + Continuous ───────────────────────────────────────────
    if do_combined
        combined = [block_cons; continuous_cons];
        if ~isempty(combined)
            run_one_sample(combined, ...
                fullfile(output_dir, 'Combined_Block_Continuous'), ...
                'Combined (Block + Continuous)');
        else
            fprintf('[SKIP] Combined: no Block/Continuous con images found.\n');
        end
    end

    fprintf('\n========================================\n');
    fprintf(' ALL DONE. Group results in: %s\n', output_dir);
    fprintf('========================================\n\n');
end


% ── Collect contrast images recursively ───────────────────────────────────────

function cons = collect_cons(task_dir, con_name, label)
    cons = {};
    if isempty(task_dir)
        fprintf('[%s] folder not provided — skipping.\n', label);
        return
    end
    if ~exist(task_dir, 'dir')
        fprintf('[%s] folder not found: %s — skipping.\n', label, task_dir);
        return
    end

    % Recursive search for con_name under task_dir
    hits = dir(fullfile(task_dir, '**', con_name));
    all_paths = cell(numel(hits), 1);
    for k = 1:numel(hits)
        all_paths{k} = fullfile(hits(k).folder, hits(k).name);
    end

    if isempty(all_paths)
        fprintf('[%s] found 0 con image(s) in %s\n', label, task_dir);
        return
    end

    % Step07 layout is <root>/<subj>/<task>/<con>. If you point this at a
    % step07 root, the recursive search would mix tasks — so prefer paths
    % whose folder name matches the task label. If none match (the folder is
    % already task-specific), keep all hits.
    %
    % Use a path-separator-bounded match so 'rest' doesn't match 'BlockStim'
    % or partial words: look for the task as a full path component.
    sep = filesep;
    pat = [sep label sep];
    keep = false(numel(all_paths), 1);
    for k = 1:numel(all_paths)
        keep(k) = contains(all_paths{k}, pat, 'IgnoreCase', true);
    end

    if any(keep)
        cons = all_paths(keep);
    else
        cons = all_paths;   % folder is already task-specific
    end

    fprintf('[%s] found %d con image(s) in %s\n', label, numel(cons), task_dir);
end


% ── One-sample t-test ─────────────────────────────────────────────────────────

function run_one_sample(con_files, out_dir, label)
    if numel(con_files) < 2
        fprintf('[%s] only %d image(s) — need >= 2 for a group test. Skipping.\n', ...
                label, numel(con_files));
        return
    end

    if ~exist(out_dir, 'dir'), mkdir(out_dir); end

    % Remove any stale SPM.mat so the design can be re-specified
    spm_mat = fullfile(out_dir, 'SPM.mat');
    if exist(spm_mat, 'file'), delete(spm_mat); end

    % SPM expects single-volume scans as 'file.nii,1'
    scans = cell(numel(con_files), 1);
    for k = 1:numel(con_files)
        scans{k} = [con_files{k} ',1'];
    end

    fprintf('\n[%s] one-sample t-test  (n = %d)\n', label, numel(con_files));

    % ── 1) Factorial design: one-sample t-test ────────────────────────────────
    mb = [];
    mb{1}.spm.stats.factorial_design.dir = {out_dir};
    mb{1}.spm.stats.factorial_design.des.t1.scans = scans;
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

    % ── 3) Contrasts: group mean > 0 and < 0 ──────────────────────────────────
    mb = [];
    mb{1}.spm.stats.con.spmmat = {spm_mat};
    mb{1}.spm.stats.con.delete = 1;
    mb{1}.spm.stats.con.consess{1}.tcon.name    = [label ' > 0'];
    mb{1}.spm.stats.con.consess{1}.tcon.weights = 1;
    mb{1}.spm.stats.con.consess{1}.tcon.sessrep = 'none';
    mb{1}.spm.stats.con.consess{2}.tcon.name    = [label ' < 0'];
    mb{1}.spm.stats.con.consess{2}.tcon.weights = -1;
    mb{1}.spm.stats.con.consess{2}.tcon.sessrep = 'none';
    spm_jobman('run', mb);

    fprintf('[%s] done -> %s\n', label, out_dir);
end
