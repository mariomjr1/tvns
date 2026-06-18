function glm_spm_firstlevel_mni_v2(subject_list_file, fmriprep_dir, ...
        firstlevel_dir, output_dir, spm_dir, varargin)
% GLM_SPM_FIRSTLEVEL_MNI_V2
% First-level SPM GLM. DEFAULT (Space='MNI'): model the fMRIPrep
% MNI152NLin2009cAsym BOLD directly (con already in MNI; no SPM renormalisation).
% OPTIONAL LEGACY (Space='T1w'): model the native T1w BOLD, then SPM unified-seg
% warp contrasts to MNI (DoMNI).
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
%   glm_spm_firstlevel_mni_v2(..., 'TR',1.19, 'Tasks',{'BlockStim','ContinuousStim'}, ...
%       'Session','01', 'Run','01', 'SmoothFWHM',[3 3 3], 'Space','MNI', ...
%       'DoMNI',true, 'MNIRef','', 'SmoothPrefix','s3', 'WarpOnly',false, ...
%       'SourceData','')
%
% Required:
%   subject_list_file  text file, one BIDS subject per line (sub-XXXX...)
%   fmriprep_dir       derivatives/fmriprep root
%                      BOLD: <subj>/ses-<ses>/func/<subj>_ses-<ses>_task-<task>_run-<run>_space-T1w_desc-preproc_bold.nii.gz
%                      MASK: <subj>/ses-<ses>/func/<subj>_ses-<ses>_task-<task>_run-<run>_space-T1w_desc-brain_mask.nii.gz
%                      T1w : <subj>/ses-<ses>/anat/ OR <subj>/anat/ *_desc-preproc_T1w.nii.gz (MNI warp)
%   firstlevel_dir     folder from step06 with stim_onsets/ and motion_regressors/ OR sourcedata root
%   output_dir         where to write first-level GLM outputs
%   spm_dir            SPM12 installation path
%
% Optional name-value:
%   TR            scalar seconds (default 1.19)
%   Tasks         cellstr of task names (default {'ContinuousStim','BlockStim'};
%                 'rest' is a baseline and is skipped — not a Stim contrast)
%   Session       BIDS session, no 'ses-' (default '01')
%   Run           BIDS run, no 'run-' (default '01')
%   SmoothFWHM    [x y z] mm (default [3 3 3])
%   SmoothPrefix  string (default 's3')
%   Space         'MNI' | 'T1w' | 'both' (default 'MNI' — direct fMRIPrep MNI BOLD;
%                 'T1w' is the optional legacy T1w + SPM-warp path)
%   DoMNI         logical — for Space='T1w' only: SPM unified-seg warp con→MNI
%                 (legacy double-normalisation toggle; default true)
%   MNIRef        MNI reference image; '' uses spm canonical avg152T1 (default '')
%   WarpOnly      logical — skip GLM, only warp existing con_*.nii to MNI (default false)
%   SourceData    sourcedata root dir; if provided, look for stim in sourcedata/derivatives/physio/<subj>/stimtrigger/
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
    % 'rest' is a resting baseline (no stimulus) — NOT a Stim-contrast task, so it
    % is excluded by default. It is skipped with a note even if passed in. (Task 10.)
    addParameter(p, 'Tasks',        {'ContinuousStim','BlockStim'}, @iscell);
    addParameter(p, 'Session',      '01',  @(x) ischar(x)||isstring(x));
    addParameter(p, 'Run',          '01',  @(x) ischar(x)||isstring(x));
    addParameter(p, 'SmoothFWHM',   [3 3 3], @(x) isnumeric(x)&&numel(x)==3);
    addParameter(p, 'SmoothPrefix', 's3',  @(x) ischar(x)||isstring(x));
    addParameter(p, 'DoMNI',        true,  @(x) islogical(x)||isnumeric(x));
    addParameter(p, 'MNIRef',       '',    @(x) ischar(x)||isstring(x));
    addParameter(p, 'WarpOnly',     false, @(x) islogical(x)||isnumeric(x));
    addParameter(p, 'SourceData',   '',    @(x) ischar(x)||isstring(x));
    % Space to model the first level in (Task 06):
    %   'MNI'  — fMRIPrep MNI152NLin2009cAsym BOLD directly (con already in MNI → copied
    %            to wcon_*; NO SPM segment-normalisation). Default (preferred path).
    %   'T1w'  — fMRIPrep T1w BOLD (con in T1w; optional SPM warp→MNI via DoMNI).
    %            Optional legacy double-normalisation path.
    %   'both' — run T1w (in <subj>/<task>) AND MNI (in <subj>/<task>/mni).
    addParameter(p, 'Space',        'MNI', @(x) ischar(x)||isstring(x));
    % Brainstem restriction (Task 05 C2): explicit GLM mask = brainstem ∩ fMRIPrep
    % brain mask. Optional (default off). The brainstem mask must be in the SAME
    % space as the modeling (use Space='MNI' with an MNI brainstem mask).
    % BrainstemSmoothFWHM (optional) overrides SmoothFWHM in brainstem mode.
    addParameter(p, 'BrainstemMask',       '',    @(x) ischar(x)||isstring(x));
    addParameter(p, 'RestrictBrainstem',   false, @(x) islogical(x)||isnumeric(x));
    % Brainstem smoothing FWHM (mm) used in restrict-to-brainstem mode. Default 0 = NO
    % smoothing (ROI averaging gives the SNR; smoothing only mixes mm-scale nuclei).
    % Cortex/whole-brain keeps SmoothFWHM (3 mm).
    addParameter(p, 'BrainstemSmoothFWHM', [0 0 0], @(x) isnumeric(x));
    % HPF cutoff (s) and serial-correlation model. Default cvi = FAST (better than AR(1)
    % for short-TR 7T data; Corbin 2018, Olszowy 2019). Hpf must be >= 2x the longest
    % period of interest — checked at runtime against the onsets (Task 15/17).
    addParameter(p, 'Hpf',          128,    @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'Cvi',          'FAST', @(x) ischar(x)||isstring(x));
    parse(p, subject_list_file, fmriprep_dir, firstlevel_dir, output_dir, spm_dir, varargin{:});

    R   = p.Results;
    TR  = R.TR;
    tasks = R.Tasks;
    ses = char(R.Session);
    run = char(R.Run);
    smooth_fwhm   = R.SmoothFWHM;
    smooth_prefix = char(R.SmoothPrefix);
    do_mni = logical(R.DoMNI);
    warp_only = logical(R.WarpOnly);
    source_data = char(R.SourceData);
    spm_dir = char(R.spm_dir);
    switch lower(char(R.Space))
        case 't1w',  space_list = {'T1w'};
        case 'mni',  space_list = {'MNI152NLin2009cAsym'};
        case 'both', space_list = {'T1w', 'MNI152NLin2009cAsym'};
        otherwise,   error('Space must be ''T1w'', ''MNI'', or ''both'' (got %s)', char(R.Space));
    end
    brainstem_mask = char(R.BrainstemMask);
    restrict_bs    = logical(R.RestrictBrainstem);
    bs_fwhm        = R.BrainstemSmoothFWHM;
    hpf_s          = R.Hpf;
    cvi            = char(R.Cvi);
    if restrict_bs && (isempty(brainstem_mask) || exist(brainstem_mask, 'file') ~= 2)
        warning('RestrictBrainstem on but BrainstemMask not found (%s) — ignoring.', brainstem_mask);
        restrict_bs = false;
    end

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

    % ── Detect stim/motion directories ────────────────────────────────────────
    % If SourceData is provided, look in sourcedata/derivatives/physio/<subj>/stimtrigger/
    % Otherwise, look in firstlevel_dir/stim_onsets/ (backward compatible)
    % Sub-folders are numbered (01_stim_onsets, ...) by step06; fall back to the
    % unnumbered names so older datasets still work.
    use_physio_subj_dir = ~isempty(source_data);
    if ~use_physio_subj_dir
        stim_dir   = pick_dir(firstlevel_dir, {'01_stim_onsets', 'stim_onsets'});
        motion_dir = pick_dir(firstlevel_dir, {'02_motion_regressors', 'motion_regressors'});
    end

    % ── Read subjects ─────────────────────────────────────────────────────────
    subs = read_subject_list(subject_list_file);
    fprintf('\n========================================\n');
    fprintf(' glm_spm_firstlevel_mni_v2\n');
    fprintf('========================================\n');
    fprintf(' Subjects:   %d\n', numel(subs));
    fprintf(' fMRIPrep:   %s\n', fmriprep_dir);
    if ~warp_only
        fprintf(' First-lvl:  %s\n', firstlevel_dir);
    end
    fprintf(' Output:     %s\n', output_dir);
    fprintf(' Tasks:      %s\n', strjoin(tasks, ', '));
    if warp_only
        fprintf(' MODE:       Warp-only (skip GLM, only warp existing con_*.nii)\n');
    else
        fprintf(' TR=%.3f  Smooth=%s  DoMNI=%d\n', TR, mat2str(smooth_fwhm), do_mni);
    end
    fprintf('\n');

    % ── Main loop ─────────────────────────────────────────────────────────────
    for i = 1:numel(subs)
        subj = normalize_subj(subs{i});
        fprintf('\n=== Subject: %s ===\n', subj);

        % Set per-subject stim/motion directories if using SourceData
        if use_physio_subj_dir
            stim_dir   = fullfile(source_data, 'derivatives', 'physio', subj, 'stimtrigger');
            motion_dir = fullfile(source_data, 'derivatives', 'physio', subj, 'motion_regressors');
        end

        func_dir = fullfile(fmriprep_dir, subj, ['ses-' ses], 'func');
        % T1w is located via find_t1w() (handles <subj>/anat and <subj>/ses-XX/anat)

        for t = 1:numel(tasks)
            task = tasks{t};
            if strcmpi(task, 'rest')
                fprintf('--- Task: rest --- (resting baseline — no Stim>baseline contrast; skipping)\n');
                continue;
            end
            fprintf('--- Task: %s ---\n', task);

            task_out = fullfile(output_dir, subj, task);
            if ~exist(task_out, 'dir'), mkdir(task_out); end

            % ── WARP-ONLY MODE: Skip GLM, only warp existing T1w con_*.nii ────
            if warp_only
                if do_mni
                    workdir = fullfile(task_out, 'work');
                    if ~exist(workdir, 'dir'), mkdir(workdir); end
                    t1_gz = find_t1w(fmriprep_dir, subj, ses);
                    if isempty(t1_gz)
                        warning('No T1w for MNI warp (skip): %s', subj);
                    else
                        con_files = dir(fullfile(task_out, 'con_*.nii'));
                        if ~isempty(con_files)
                            fprintf('Warping %d contrast(s)...\n', numel(con_files));
                            warp_cons_to_mni(t1_gz, {con_files.name}, task_out, ...
                                           mni_ref, spm_dir, workdir);
                        else
                            warning('No con_*.nii found in %s (skip warp)', task_out);
                        end
                    end
                end
                continue;  % Skip GLM
            end

            % ── First-level GLM in the requested space(s) (Task 06) ──────────
            % 'T1w' models the fMRIPrep T1w BOLD (con in T1w; optional SPM warp to
            % MNI via DoMNI). 'MNI152NLin2009cAsym' models the fMRIPrep MNI BOLD
            % directly — con is already in MNI and is copied to wcon_* (no warp).
            for sp = 1:numel(space_list)
                se = space_list{sp};
                if strcmpi(se, 'T1w')
                    out_sp = task_out;  warp_sp = do_mni;
                else
                    % MNI: own subfolder only when T1w is also run (avoid clobber)
                    if numel(space_list) > 1
                        out_sp = fullfile(task_out, 'mni');
                    else
                        out_sp = task_out;
                    end
                    warp_sp = false;
                end
                % Effective smoothing — brainstem override folds in Part A
                if restrict_bs && ~isempty(bs_fwhm)
                    if isscalar(bs_fwhm), eff_fwhm = [bs_fwhm bs_fwhm bs_fwhm];
                    else,                 eff_fwhm = bs_fwhm; end
                    eff_prefix = sprintf('s%gbs', round(eff_fwhm(1)));
                else
                    eff_fwhm = smooth_fwhm;  eff_prefix = smooth_prefix;
                end
                run_one_glm(se, out_sp, warp_sp, subj, ses, task, run, func_dir, ...
                    fmriprep_dir, eff_fwhm, eff_prefix, TR, stim_dir, ...
                    motion_dir, mni_ref, spm_dir, brainstem_mask, restrict_bs, hpf_s, cvi);
            end
        end
        fprintf('DONE subject: %s\n', subj);
    end

    fprintf('\n========================================\n');
    fprintf(' ALL DONE. Outputs in: %s\n', output_dir);
    fprintf('========================================\n\n');
end


% ── One first-level GLM for one space (T1w or MNI) ────────────────────────────

function run_one_glm(space_entity, out_dir, do_warp, subj, ses, task, run, func_dir, ...
        fmriprep_dir, smooth_fwhm, smooth_prefix, TR, stim_dir, motion_dir, mni_ref, spm_dir, ...
        brainstem_mask, restrict_bs, hpf_s, cvi)
    if nargin < 17, brainstem_mask = ''; end
    if nargin < 18, restrict_bs = false; end
    if nargin < 19 || isempty(hpf_s), hpf_s = 128; end
    if nargin < 20 || isempty(cvi),   cvi = 'FAST'; end
% Model one acquisition space. If space is MNI the con_*.nii are ALREADY in MNI and
% are copied to wcon_*.nii (so the step08 group analysis finds them) — no SPM warp.
% If T1w and do_warp, con_*.nii are warped to MNI via T1 segmentation.
    is_mni = contains(space_entity, 'MNI');
    if ~exist(out_dir, 'dir'), mkdir(out_dir); end
    workdir = fullfile(out_dir, 'work');
    if ~exist(workdir, 'dir'), mkdir(workdir); end

    % ── Locate (not copy) BOLD + mask for this space ─────────────────────────
    bold_gz = fullfile(func_dir, sprintf( ...
        '%s_ses-%s_task-%s_run-%s_space-%s_desc-preproc_bold.nii.gz', ...
        subj, ses, task, run, space_entity));
    mask_gz = fullfile(func_dir, sprintf( ...
        '%s_ses-%s_task-%s_run-%s_space-%s_desc-brain_mask.nii.gz', ...
        subj, ses, task, run, space_entity));
    if ~exist(bold_gz, 'file'), warning('Missing BOLD (skip): %s', bold_gz); return; end
    if ~exist(mask_gz, 'file'), warning('Missing MASK (skip): %s', mask_gz); return; end

    bold_nii = gunzip_to(bold_gz, workdir);
    mask_nii = gunzip_to(mask_gz, workdir);

    % Reslice mask into BOLD grid (NN, binary)
    mask_in_bold = fullfile(workdir, ['mb_' basename(mask_nii)]);
    if ~exist(mask_in_bold, 'file')
        mask_in_bold = coreg_reslice_mask_to_bold(mask_nii, bold_nii, mask_in_bold);
    end

    % ── Restrict to brainstem (Task 05 C2): explicit mask = brainstem ∩ brain ──
    % The brainstem mask must be in the SAME space as this BOLD (use Space='MNI'
    % with an MNI brainstem mask). spm_imcalc resamples it onto the brain-mask grid.
    if restrict_bs && ~isempty(brainstem_mask) && exist(brainstem_mask, 'file') == 2
        bs_in_bold = fullfile(workdir, 'bs_in_bold.nii');
        spm_imcalc(char(mask_in_bold, brainstem_mask), bs_in_bold, '(i2>0.5)');
        final_mask = fullfile(workdir, 'mask_brainstem.nii');
        spm_imcalc(char(mask_in_bold, bs_in_bold), final_mask, '((i1>0.5).*(i2>0.5))');
        nvox = nnz(spm_read_vols(spm_vol(final_mask)) > 0.5);
        if nvox < 1
            warning(['Brainstem ∩ brain mask is EMPTY — likely a space mismatch ' ...
                     '(brainstem mask must match the modeling space; use Space=MNI). ' ...
                     'Falling back to the fMRIPrep brain mask.']);
        else
            mask_in_bold = final_mask;
            fprintf('  Restricted to brainstem: %d voxels (brainstem ∩ brain)\n', nvox);
        end
    end

    % Smooth (skip if already smoothed)
    [~, bn, be] = fileparts(bold_nii);
    smoothed = fullfile(workdir, [smooth_prefix bn be]);
    if startsWith(basename(bold_nii), smooth_prefix)
        scans_sm = cellstr(spm_select('expand', bold_nii));
    elseif all(smooth_fwhm == 0)
        % FWHM 0 (brainstem ROI default) → model the UNSMOOTHED BOLD (no smooth step)
        fprintf('  No smoothing (FWHM=0) — modeling the unsmoothed BOLD\n');
        scans_sm = cellstr(spm_select('expand', bold_nii));
    else
        scans_bold = cellstr(spm_select('expand', bold_nii));
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
    end

    % ── Stim condition ────────────────────────────────────────────────────────
    stim_file = fullfile(stim_dir, sprintf( ...
        '%s_ses-%s_task-%s_run-%s_bold_stim.txt', subj, ses, task, run));
    if ~exist(stim_file, 'file'), warning('Missing stim (skip): %s', stim_file); return; end
    S = read_stim_file(stim_file);
    if isempty(S.onset), warning('No stim onsets in %s (skip)', stim_file); return; end

    cond = struct([]);
    cond(1).name     = 'Stim';
    cond(1).onset    = S.onset;
    cond(1).duration = S.duration;
    cond(1).tmod     = 0;
    cond(1).pmod     = struct('name', {}, 'param', {}, 'poly', {});
    cond(1).orth     = 1;

    % HPF sanity check vs the paradigm (Task 15): SPM's high-pass cutoff must be >= 2x
    % the longest period of interest, else the task regressor is attenuated. Estimate the
    % longest period from the onsets and warn (does not abort — flag + log).
    if numel(cond(1).onset) >= 2
        max_isi = max(diff(sort(cond(1).onset(:))));
        if isfinite(max_isi) && max_isi > 0 && hpf_s < 2*max_isi
            warning(['[HPF] %s: HPF=%g s may be too short — longest onset interval ~%.1f s ' ...
                     '(use HPF >= %.0f s) or the task regressor is attenuated.'], ...
                     task, hpf_s, max_isi, ceil(2*max_isi));
        end
    end
    fprintf(' [%s] HPF=%g s, cvi=%s\n', task, hpf_s, cvi);

    % ── Motion nuisance (RETROICOR already removed from the image upstream) ───
    motion_file = fullfile(motion_dir, sprintf( ...
        '%s_ses-%s_task-%s_run-%s_motion_regressors.txt', subj, ses, task, run));
    if ~exist(motion_file, 'file')
        warning('Missing motion regressors (continuing without): %s', motion_file);
        motion_file = '';
    end

    % ── Specify + estimate ────────────────────────────────────────────────────
    mb = [];
    mb{1}.spm.stats.fmri_spec.dir = {out_dir};
    mb{1}.spm.stats.fmri_spec.timing.units  = 'secs';
    mb{1}.spm.stats.fmri_spec.timing.RT     = TR;
    mb{1}.spm.stats.fmri_spec.timing.fmri_t = 16;
    mb{1}.spm.stats.fmri_spec.timing.fmri_t0 = 8;
    mb{1}.spm.stats.fmri_spec.sess(1).scans = scans_sm;
    mb{1}.spm.stats.fmri_spec.sess(1).cond  = cond;
    mb{1}.spm.stats.fmri_spec.sess(1).multi = {''};
    mb{1}.spm.stats.fmri_spec.sess(1).regress = struct('name', {}, 'val', {});
    mb{1}.spm.stats.fmri_spec.sess(1).multi_reg = { iff(~isempty(motion_file), motion_file, '') };
    mb{1}.spm.stats.fmri_spec.sess(1).hpf = hpf_s;
    mb{1}.spm.stats.fmri_spec.bases.hrf.derivs = [0 0];
    mb{1}.spm.stats.fmri_spec.volt    = 1;
    mb{1}.spm.stats.fmri_spec.global  = 'None';
    mb{1}.spm.stats.fmri_spec.mask    = {mask_in_bold};
    mb{1}.spm.stats.fmri_spec.mthresh = 0;
    mb{1}.spm.stats.fmri_spec.cvi     = cvi;
    spm_jobman('run', mb);

    spm_mat = fullfile(out_dir, 'SPM.mat');
    mb = [];
    mb{1}.spm.stats.fmri_est.spmmat = {spm_mat};
    mb{1}.spm.stats.fmri_est.write_residuals = 0;
    mb{1}.spm.stats.fmri_est.method.Classical = 1;
    spm_jobman('run', mb);

    % ── Contrasts ──────────────────────────────────────────────────────────────
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
    fprintf('First-level done: %s | %s | space-%s\n', subj, task, space_entity);

    % ── Percent signal change (PSC) image(s) — primary, platform-robust ──────────
    write_psc(out_dir, spm_mat);

    % ── MNI handling ────────────────────────────────────────────────────────────
    if is_mni
        % con already in MNI → copy to wcon_*.nii for the group step (no warp).
        % Include pscon_* so wpscon_* (PSC in MNI) is available too.
        cons = [dir(fullfile(out_dir, 'con_*.nii')); dir(fullfile(out_dir, 'pscon_*.nii'))];
        for c = 1:numel(cons)
            copyfile(fullfile(out_dir, cons(c).name), ...
                     fullfile(out_dir, ['w' cons(c).name]));
        end
        fprintf('  MNI-space con copied to wcon_*.nii (%d) — no warp needed\n', numel(cons));
    elseif do_warp
        t1_gz = find_t1w(fmriprep_dir, subj, ses);
        if isempty(t1_gz)
            warning('No T1w for MNI warp (skip warp): %s', subj);
        else
            con_files = [dir(fullfile(out_dir, 'con_*.nii')); ...
                         dir(fullfile(out_dir, 'pscon_*.nii'))];
            warp_cons_to_mni(t1_gz, {con_files.name}, out_dir, mni_ref, spm_dir, workdir);
        end
    end
end


% ── Percent signal change (PSC) images ────────────────────────────────────────
% PSC = 100 * con * peak(Stim regressor) / session-mean beta  (Mazaika/marsbar event-
% height scaling). PSC is interpretable and robust to between-platform scaling
% (E12<->XA60), so it is the preferred ROI currency; beta stays available too.
% Flag + log: warns and returns on any problem, never aborts the GLM.
function write_psc(out_dir, spm_mat)
    try
        L = load(spm_mat); SPM = L.SPM;
        if ~isfield(SPM, 'Sess') || isempty(SPM.Sess) || isempty(SPM.Sess(1).col)
            warning('[PSC] no session columns in SPM — skipped'); return;
        end
        stim_col = SPM.Sess(1).col(1);                 % first condition (Stim)
        peak = max(SPM.xX.X(:, stim_col));             % HRF-convolved regressor peak
        if ~isfinite(peak) || peak <= 0
            warning('[PSC] non-positive regressor peak — skipped'); return;
        end
        if ~isfield(SPM.xX, 'iB') || isempty(SPM.xX.iB)
            warning('[PSC] no constant/session-mean term — skipped'); return;
        end
        bfile = fullfile(out_dir, sprintf('beta_%04d.nii', SPM.xX.iB(1)));
        if exist(bfile, 'file') ~= 2
            warning('[PSC] constant beta not found (%s) — skipped', bfile); return;
        end
        mean_img = spm_read_vols(spm_vol(bfile));
        cons = dir(fullfile(out_dir, 'con_*.nii'));
        for c = 1:numel(cons)
            Vc  = spm_vol(fullfile(out_dir, cons(c).name));
            con = spm_read_vols(Vc);
            psc = 100 .* con .* peak ./ mean_img;
            psc(~isfinite(psc)) = 0;
            Vo = Vc; Vo.fname = fullfile(out_dir, ['ps' cons(c).name]);  % pscon_*.nii
            Vo.descrip = 'percent signal change';
            spm_write_vol(Vo, psc);
        end
        fprintf('  PSC written: pscon_*.nii (regressor peak = %.4f)\n', peak);
    catch ME
        warning('[PSC] could not compute (%s) — skipped', ME.message);
    end
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

function d = pick_dir(base, names)
% Return base/<name> for the first name that exists; else base/<first name>.
% Lets the GLM accept both numbered (01_stim_onsets) and legacy (stim_onsets)
% sub-folder schemes.
    d = fullfile(base, names{1});
    for k = 1:numel(names)
        cand = fullfile(base, names{k});
        if exist(cand, 'dir'), d = cand; return; end
    end
end

function t1 = find_t1w(fmriprep_dir, subj, ses)
% Locate the native-space preprocessed T1w, handling both fMRIPrep layouts:
%   <subj>/ses-<ses>/anat/   (session-anat — most common with sessions)
%   <subj>/anat/             (no-session anat)
% Prefers the native T1w (no 'space-' entity). Returns '' if not found.
    t1 = '';
    anat_dirs = { fullfile(fmriprep_dir, subj, ['ses-' ses], 'anat'), ...
                  fullfile(fmriprep_dir, subj, 'anat') };
    for d = 1:numel(anat_dirs)
        ad = anat_dirs{d};
        if ~exist(ad, 'dir'), continue; end
        cand = dir(fullfile(ad, '*_desc-preproc_T1w.nii.gz'));
        if isempty(cand), continue; end
        % Prefer a filename WITHOUT a 'space-' entity (i.e. native T1w)
        names = {cand.name};
        native = find(~contains(names, 'space-'), 1);
        if isempty(native), native = 1; end
        t1 = fullfile(cand(native).folder, cand(native).name);
        return;
    end
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
