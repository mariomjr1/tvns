function glm_spm_firstlevel_mni_v2(subject_list_file, fmriprep_dir, ...
        firstlevel_dir, output_dir, spm_dir, varargin)
% GLM_SPM_FIRSTLEVEL_MNI_V2
% First-level SPM GLM in native T1w space, then warp contrasts to MNI.
%
% Differences from the old step21+22+23 chain:
%   - Masks and BOLDs are LOCATED in place inside derivatives/fmriprep
%     (no copy step — masks are read directly from the fMRIPrep func dir).
%   - First-level GLM and MNI normalisation run in one batch function.
%   - All paths are arguments (configurable from the GUI / shell wrapper).
%
% Usage:
%   glm_spm_firstlevel_mni_v2(subject_list_file, fmriprep_dir, ...
%       firstlevel_dir, output_dir, spm_dir)
%   glm_spm_firstlevel_mni_v2(..., 'TR',1.19, 'Tasks',{'BlockStim','ContinuousStim','rest'}, ...
%       'Session','01', 'Run','01', 'SmoothFWHM',[3 3 3], 'DoMNI',true, ...
%       'MNIRef','', 'SmoothPrefix','s3')
%
% Required:
%   subject_list_file  text file, one BIDS subject per line (sub-XXXX...)
%   fmriprep_dir       derivatives/fmriprep root
%                      BOLD: <subj>/ses-<ses>/func/<subj>_ses-<ses>_task-<task>_run-<run>_space-T1w_desc-preproc_bold.nii.gz
%                      MASK: <subj>/ses-<ses>/func/<subj>_ses-<ses>_task-<task>_run-<run>_space-T1w_desc-brain_mask.nii.gz
%                      T1w : <subj>/anat/<subj>_desc-preproc_T1w.nii.gz  (for MNI warp)
%   firstlevel_dir     folder from step06 with stim_onsets/ and motion_regressors/
%   output_dir         where to write first-level GLM outputs
%   spm_dir            SPM12 installation path
%
% Optional name-value:
%   TR            scalar seconds (default 1.19)
%   Tasks         cellstr of task names (default {'ContinuousStim','BlockStim','rest'})
%   Session       BIDS session, no 'ses-' (default '01')
%   Run           BIDS run, no 'run-' (default '01')
%   SmoothFWHM    [x y z] mm (default [3 3 3])
%   SmoothPrefix  string (default 's3')
%   DoMNI         logical — also warp contrasts to MNI (default true)
%   MNIRef        MNI reference image; '' uses spm canonical avg152T1 (default '')
%
% Output per subject/task:
%   output_dir/<subj>/<task>/SPM.mat, con_0001.nii, con_0002.nii, ...
%   output_dir/<subj>/<task>/wcon_0001.nii  (MNI-warped, if DoMNI)
%
% Created by Mario Murakami

    % ── Parse inputs ──────────────────────────────────────────────────────────
    p = inputParser();
    addRequired(p, 'subject_list_file');
    addRequired(p, 'fmriprep_dir');
    addRequired(p, 'firstlevel_dir');
    addRequired(p, 'output_dir');
    addRequired(p, 'spm_dir');
    addParameter(p, 'TR',           1.19,  @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'Tasks',        {'ContinuousStim','BlockStim','rest'}, @iscell);
    addParameter(p, 'Session',      '01',  @(x) ischar(x)||isstring(x));
    addParameter(p, 'Run',          '01',  @(x) ischar(x)||isstring(x));
    addParameter(p, 'SmoothFWHM',   [3 3 3], @(x) isnumeric(x)&&numel(x)==3);
    addParameter(p, 'SmoothPrefix', 's3',  @(x) ischar(x)||isstring(x));
    addParameter(p, 'DoMNI',        true,  @(x) islogical(x)||isnumeric(x));
    addParameter(p, 'MNIRef',       '',    @(x) ischar(x)||isstring(x));
    parse(p, subject_list_file, fmriprep_dir, firstlevel_dir, output_dir, spm_dir, varargin{:});

    R   = p.Results;
    TR  = R.TR;
    tasks = R.Tasks;
    ses = char(R.Session);
    run = char(R.Run);
    smooth_fwhm   = R.SmoothFWHM;
    smooth_prefix = char(R.SmoothPrefix);
    do_mni = logical(R.DoMNI);
    spm_dir = char(R.spm_dir);

    % ── Setup SPM ─────────────────────────────────────────────────────────────
    set(0, 'DefaultFigureVisible', 'off');
    addpath(spm_dir);
    spm('defaults', 'FMRI');
    spm_jobman('initcfg');
    spm_get_defaults('cmdline', true);

    % MNI reference
    if isempty(char(R.MNIRef))
        mni_ref = fullfile(spm_dir, 'canonical', 'avg152T1.nii');
    else
        mni_ref = char(R.MNIRef);
    end

    stim_dir   = fullfile(firstlevel_dir, 'stim_onsets');
    motion_dir = fullfile(firstlevel_dir, 'motion_regressors');

    % ── Read subjects ─────────────────────────────────────────────────────────
    subs = read_subject_list(subject_list_file);
    fprintf('\n========================================\n');
    fprintf(' glm_spm_firstlevel_mni_v2\n');
    fprintf('========================================\n');
    fprintf(' Subjects:   %d\n', numel(subs));
    fprintf(' fMRIPrep:   %s\n', fmriprep_dir);
    fprintf(' First-lvl:  %s\n', firstlevel_dir);
    fprintf(' Output:     %s\n', output_dir);
    fprintf(' Tasks:      %s\n', strjoin(tasks, ', '));
    fprintf(' TR=%.3f  Smooth=%s  DoMNI=%d\n\n', TR, mat2str(smooth_fwhm), do_mni);

    % ── Main loop ─────────────────────────────────────────────────────────────
    for i = 1:numel(subs)
        subj = normalize_subj(subs{i});
        fprintf('\n=== Subject: %s ===\n', subj);

        func_dir = fullfile(fmriprep_dir, subj, ['ses-' ses], 'func');
        anat_dir = fullfile(fmriprep_dir, subj, 'anat');

        for t = 1:numel(tasks)
            task = tasks{t};
            fprintf('--- Task: %s ---\n', task);

            task_out = fullfile(output_dir, subj, task);
            if ~exist(task_out, 'dir'), mkdir(task_out); end
            workdir = fullfile(task_out, 'work');
            if ~exist(workdir, 'dir'), mkdir(workdir); end

            % ── LOCATE (not copy) BOLD + mask in fMRIPrep func dir ────────────
            bold_gz = fullfile(func_dir, sprintf( ...
                '%s_ses-%s_task-%s_run-%s_space-T1w_desc-preproc_bold.nii.gz', ...
                subj, ses, task, run));
            mask_gz = fullfile(func_dir, sprintf( ...
                '%s_ses-%s_task-%s_run-%s_space-T1w_desc-brain_mask.nii.gz', ...
                subj, ses, task, run));

            if ~exist(bold_gz, 'file')
                warning('Missing BOLD (skip): %s', bold_gz); continue
            end
            if ~exist(mask_gz, 'file')
                warning('Missing MASK (skip): %s', mask_gz); continue
            end

            % gunzip into workdir (originals stay untouched in fmriprep)
            bold_nii = gunzip_to(bold_gz, workdir);
            mask_nii = gunzip_to(mask_gz, workdir);

            % Reslice mask into BOLD grid (NN, binary)
            mask_in_bold = fullfile(workdir, ['mb_' basename(mask_nii)]);
            if ~exist(mask_in_bold, 'file')
                mask_in_bold = coreg_reslice_mask_to_bold(mask_nii, bold_nii, mask_in_bold);
            end

            % Smooth the BOLD
            scans_bold = cellstr(spm_select('expand', bold_nii));
            [~, bn, be] = fileparts(bold_nii);
            smoothed = fullfile(workdir, [smooth_prefix bn be]);
            if ~exist(smoothed, 'file')
                mb = [];
                mb{1}.spm.spatial.smooth.data   = scans_bold;
                mb{1}.spm.spatial.smooth.fwhm   = smooth_fwhm;
                mb{1}.spm.spatial.smooth.dtype  = 0;
                mb{1}.spm.spatial.smooth.im     = 0;
                mb{1}.spm.spatial.smooth.prefix = smooth_prefix;
                spm_jobman('run', mb);
            end
            scans_sm = cellstr(spm_select('expand', smoothed));

            % ── Stim condition ────────────────────────────────────────────────
            stim_file = fullfile(stim_dir, sprintf( ...
                '%s_ses-%s_task-%s_run-%s_bold_stim.txt', subj, ses, task, run));
            if ~exist(stim_file, 'file')
                warning('Missing stim (skip): %s', stim_file); continue
            end
            S = read_stim_file(stim_file);
            if isempty(S.onset)
                warning('No stim onsets in %s (skip task)', stim_file); continue
            end

            cond = struct([]);
            cond(1).name     = 'Stim';
            cond(1).onset    = S.onset;
            cond(1).duration = S.duration;
            cond(1).tmod     = 0;
            cond(1).pmod     = struct('name', {}, 'param', {}, 'poly', {});
            cond(1).orth     = 1;

            % ── Motion nuisance ───────────────────────────────────────────────
            motion_file = fullfile(motion_dir, sprintf( ...
                '%s_ses-%s_task-%s_run-%s_motion_regressors.txt', subj, ses, task, run));
            if ~exist(motion_file, 'file')
                warning('Missing motion regressors (continuing without): %s', motion_file);
                motion_file = '';
            end

            % ── Specify + estimate ────────────────────────────────────────────
            mb = [];
            mb{1}.spm.stats.fmri_spec.dir = {task_out};
            mb{1}.spm.stats.fmri_spec.timing.units  = 'secs';
            mb{1}.spm.stats.fmri_spec.timing.RT     = TR;
            mb{1}.spm.stats.fmri_spec.timing.fmri_t = 16;
            mb{1}.spm.stats.fmri_spec.timing.fmri_t0 = 8;
            mb{1}.spm.stats.fmri_spec.sess(1).scans = scans_sm;
            mb{1}.spm.stats.fmri_spec.sess(1).cond  = cond;
            mb{1}.spm.stats.fmri_spec.sess(1).multi = {''};
            mb{1}.spm.stats.fmri_spec.sess(1).regress = struct('name', {}, 'val', {});
            mb{1}.spm.stats.fmri_spec.sess(1).multi_reg = { iff(~isempty(motion_file), motion_file, '') };
            mb{1}.spm.stats.fmri_spec.sess(1).hpf = 128;
            mb{1}.spm.stats.fmri_spec.bases.hrf.derivs = [0 0];
            mb{1}.spm.stats.fmri_spec.volt    = 1;
            mb{1}.spm.stats.fmri_spec.global  = 'None';
            mb{1}.spm.stats.fmri_spec.mask    = {mask_in_bold};
            mb{1}.spm.stats.fmri_spec.mthresh = 0;
            mb{1}.spm.stats.fmri_spec.cvi     = 'AR(1)';
            spm_jobman('run', mb);

            spm_mat = fullfile(task_out, 'SPM.mat');
            mb = [];
            mb{1}.spm.stats.fmri_est.spmmat = {spm_mat};
            mb{1}.spm.stats.fmri_est.write_residuals = 0;
            mb{1}.spm.stats.fmri_est.method.Classical = 1;
            spm_jobman('run', mb);

            % ── Contrasts ─────────────────────────────────────────────────────
            mb = [];
            mb{1}.spm.stats.con.spmmat = {spm_mat};
            mb{1}.spm.stats.con.delete = 1;
            mb{1}.spm.stats.con.consess{1}.tcon.name    = 'Stim > baseline';
            mb{1}.spm.stats.con.consess{1}.tcon.weights = 1;
            mb{1}.spm.stats.con.consess{1}.tcon.sessrep = 'none';
            mb{1}.spm.stats.con.consess{2}.tcon.name    = 'Stim < baseline';
            mb{1}.spm.stats.con.consess{2}.tcon.weights = -1;
            mb{1}.spm.stats.con.consess{2}.tcon.sessrep = 'none';
            spm_jobman('run', mb);

            fprintf('First-level done: %s | %s\n', subj, task);

            % ── MNI warp ──────────────────────────────────────────────────────
            if do_mni
                t1_gz  = fullfile(anat_dir, sprintf('%s_desc-preproc_T1w.nii.gz', subj));
                if ~exist(t1_gz, 'file')
                    % fallback: any *_desc-preproc_T1w.nii.gz in anat
                    cand = dir(fullfile(anat_dir, '*_desc-preproc_T1w.nii.gz'));
                    if ~isempty(cand)
                        t1_gz = fullfile(cand(1).folder, cand(1).name);
                    end
                end
                if ~exist(t1_gz, 'file')
                    warning('No T1w for MNI warp (skip warp): %s', t1_gz);
                else
                    con_files = dir(fullfile(task_out, 'con_*.nii'));
                    warp_cons_to_mni(t1_gz, {con_files.name}, task_out, ...
                                     mni_ref, spm_dir, workdir);
                end
            end
        end
        fprintf('DONE subject: %s\n', subj);
    end

    fprintf('\n========================================\n');
    fprintf(' ALL DONE. Outputs in: %s\n', output_dir);
    fprintf('========================================\n\n');
end


% ── Warp contrasts to MNI via T1 segmentation ─────────────────────────────────

function warp_cons_to_mni(t1_gz, con_names, con_dir, mni_ref, spm_dir, workdir)
    if isempty(con_names), return; end

    % Prepare T1 (gunzip into workdir so we don't litter fmriprep)
    t1_nii  = gunzip_to(t1_gz, workdir);
    mni_ref = gunzip_if_needed_local(mni_ref);

    [t1_path, t1_name, ~] = fileparts(t1_nii);
    y_file = fullfile(t1_path, ['y_' t1_name '.nii']);

    % Segment T1 once → forward deformation y_*.nii
    if ~exist(y_file, 'file')
        mb = [];
        mb{1}.spm.spatial.preproc.channel.vols    = {[t1_nii ',1']};
        mb{1}.spm.spatial.preproc.channel.biasreg = 0.001;
        mb{1}.spm.spatial.preproc.channel.biasfwhm = 60;
        mb{1}.spm.spatial.preproc.channel.write   = [0 1];
        for k = 1:6
            mb{1}.spm.spatial.preproc.tissue(k).tpm = ...
                {fullfile(spm_dir, 'tpm', sprintf('TPM.nii,%d', k))};
        end
        ng = [1 1 2 3 4 2];
        nat = {[1 0],[1 0],[1 0],[0 0],[0 0],[0 0]};
        for k = 1:6
            mb{1}.spm.spatial.preproc.tissue(k).ngaus  = ng(k);
            mb{1}.spm.spatial.preproc.tissue(k).native = nat{k};
            mb{1}.spm.spatial.preproc.tissue(k).warped = [0 0];
        end
        mb{1}.spm.spatial.preproc.warp.mrf     = 1;
        mb{1}.spm.spatial.preproc.warp.cleanup = 1;
        mb{1}.spm.spatial.preproc.warp.reg     = [0 0.001 0.5 0.05 0.2];
        mb{1}.spm.spatial.preproc.warp.affreg  = 'mni';
        mb{1}.spm.spatial.preproc.warp.fwhm    = 0;
        mb{1}.spm.spatial.preproc.warp.samp    = 3;
        mb{1}.spm.spatial.preproc.warp.write   = [1 1];
        spm_jobman('run', mb);
    end

    if ~exist(y_file, 'file')
        warning('Deformation field not created — skipping MNI warp for %s', con_dir);
        return
    end

    % MNI geometry
    Vref = spm_vol(mni_ref);
    bb   = spm_get_bbox(Vref, 'fv');
    vox  = sqrt(sum(Vref.mat(1:3,1:3).^2));

    % Warp each con image
    resample = cell(numel(con_names), 1);
    for c = 1:numel(con_names)
        resample{c} = [fullfile(con_dir, con_names{c}) ',1'];
    end

    mb = [];
    mb{1}.spm.spatial.normalise.write.subj.def      = {y_file};
    mb{1}.spm.spatial.normalise.write.subj.resample = resample;
    mb{1}.spm.spatial.normalise.write.woptions.bb     = bb;
    mb{1}.spm.spatial.normalise.write.woptions.vox    = vox;
    mb{1}.spm.spatial.normalise.write.woptions.interp = 4;
    mb{1}.spm.spatial.normalise.write.woptions.prefix = 'w';
    spm_jobman('run', mb);

    fprintf('  MNI-warped %d contrast(s) in %s\n', numel(con_names), con_dir);
end


% ── Helpers ───────────────────────────────────────────────────────────────────

function subs = read_subject_list(fname)
    subs = {};
    fid = fopen(fname, 'r');
    if fid == -1, error('Cannot open %s', fname); end
    tline = fgetl(fid);
    while ischar(tline)
        s = strtrim(tline);
        if ~isempty(s), subs{end+1} = s; end %#ok<AGROW>
        tline = fgetl(fid);
    end
    fclose(fid);
end

function subj = normalize_subj(s)
    s = strtrim(s);
    if startsWith(s, 'sub-'), subj = s; else, subj = ['sub-' s]; end
end

function out = gunzip_to(gz_file, dest_dir)
    % gunzip a .nii.gz into dest_dir, return path to the .nii
    [~, base, ~] = fileparts(gz_file);   % strips .gz -> base.nii
    out = fullfile(dest_dir, base);
    if ~exist(out, 'file')
        gunzip(gz_file, dest_dir);
    end
end

function out = gunzip_if_needed_local(f)
    if endsWith(f, '.nii.gz')
        d = fileparts(f);
        gunzip(f, d);
        out = erase(f, '.gz');
    else
        out = f;
    end
end

function S = read_stim_file(stim_file)
    M = readmatrix(stim_file);
    if isempty(M)
        S.onset = []; S.duration = []; return
    end
    if size(M,2) == 1
        S.onset = M(:,1); S.duration = zeros(size(M,1),1);
    elseif size(M,2) == 2
        S.onset = M(:,1); S.duration = M(:,2);
    else
        S.onset = M(:,1); S.duration = M(:,2); S.amp = M(:,3);
    end
    S.onset = S.onset(:); S.duration = S.duration(:);
end

function out = basename(p)
    [~, n, e] = fileparts(p); out = [n e];
end

function out = iff(c, a, b)
    if c, out = a; else, out = b; end
end

function out_mask = coreg_reslice_mask_to_bold(mask_nii, bold_nii, out_mask)
    Vb = spm_vol(bold_nii);
    ref_vol = [Vb(1).fname ',' num2str(Vb(1).n(1))];

    mb = [];
    mb{1}.spm.spatial.coreg.estwrite.ref    = {ref_vol};
    mb{1}.spm.spatial.coreg.estwrite.source = {mask_nii};
    mb{1}.spm.spatial.coreg.estwrite.other  = {''};
    mb{1}.spm.spatial.coreg.estwrite.eoptions.cost_fun = 'nmi';
    mb{1}.spm.spatial.coreg.estwrite.eoptions.sep      = [4 2];
    mb{1}.spm.spatial.coreg.estwrite.eoptions.tol      = ...
        [0.02 0.02 0.02 0.001 0.001 0.001 0.01 0.01 0.01 0.001 0.001 0.001];
    mb{1}.spm.spatial.coreg.estwrite.eoptions.fwhm     = [7 7];
    mb{1}.spm.spatial.coreg.estwrite.roptions.interp   = 0;
    mb{1}.spm.spatial.coreg.estwrite.roptions.wrap     = [0 0 0];
    mb{1}.spm.spatial.coreg.estwrite.roptions.mask     = 0;
    mb{1}.spm.spatial.coreg.estwrite.roptions.prefix   = 'r';
    spm_jobman('run', mb);

    [mpath, mname, mext] = fileparts(mask_nii);
    produced = fullfile(mpath, ['r' mname mext]);
    if ~exist(produced, 'file')
        error('Resliced mask not found: %s', produced);
    end
    if ~strcmp(produced, out_mask)
        copyfile(produced, out_mask);
    end
    Vm = spm_vol(out_mask);
    Y  = spm_read_vols(Vm);
    Y  = double(Y > 0.5);
    spm_write_vol(Vm, Y);
end
