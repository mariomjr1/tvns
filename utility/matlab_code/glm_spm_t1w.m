% ============================================================
% tvns_glm_pipeline_spm.m
%
% Goal: mimic the FEAT model from liz (Roberta postdoc in 2025), but in SPM:
%  - Run a FIRST-LEVEL per subject per task (like FEAT per task folder)
%  - One main EV: Stim timing from *_bold_stim.txt (3-col supported)
%  - Flexible HRF shape: HRF + time + dispersion derivatives (3 basis cols)
%    -> analogous to FEAT/FLOBS 3 bases producing Stim(1..3)
%  - Nuisance: motion/outliers (from *_motion_regressors.txt)
%  - Optional: respiration regressor (TR-resolution) if you have it
%
% Mario Murakami - Jan 2026 (adapted)
% ============================================================

function tvns_glm_pipeline_spm()

addpath('/autofs/cluster/vagabond/USERS/MARIO/Packages/matlab/spm12');
spm('defaults','FMRI');
spm_jobman('initcfg');
spm_get_defaults('cmdline',true);

% -------------------------
% Inputs / naming conventions 
% -------------------------
sublist_file = '/autofs/cluster/vagabond/USERS/MARIO/Pipelines/9_tvns/SubjectListfmriprep.txt';

base_firstlvl = '/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/8_first_level';
stim_dir      = fullfile(base_firstlvl, 'stim_onsets');
bold_dir      = fullfile(base_firstlvl, 'bolds');
mask_dir      = fullfile(base_firstlvl, 'masks');
motion_dir    = fullfile(base_firstlvl, 'motion_regressors');

% Output (FIX typo "deviratives" -> "derivatives")
outputDir     = '/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/9_first_level_t1w';

ses  = '01';
run  = '01';
tasks = {'ContinuousStim','BlockStim','rest'};

% SPM timing
TR = 1.19;  % seconds

% Smoothing (FEAT brainstem example used 3mm; change if you want)
smooth_fwhm = [3 3 3];
smooth_prefix = 's3';

% -------------------------
% Read subject list (expects IDs)
% -------------------------
sublist = read_subject_list(sublist_file);

% -------------------------
% Main loop: subject x task (each task gets its OWN SPM.mat like FEAT)
% -------------------------
for i = 1:numel(sublist)

    subj_in  = sublist{i};
    subj_tag = normalize_subj(subj_in);  % ensures "sub-XXX"
    fprintf('\n=== Subject: %s ===\n', subj_tag);

    for t = 1:numel(tasks)

        task = tasks{t};
        fprintf('--- Task: %s ---\n', task);

        TaskOutdir = fullfile(outputDir, subj_tag, task);
        if ~exist(TaskOutdir,'dir'), mkdir(TaskOutdir); end

        Workdir = fullfile(TaskOutdir, 'work');
        if ~exist(Workdir,'dir'), mkdir(Workdir); end

        % ------------------------------------------------------------
        % 1) Prepare BOLD + MASK: copy -> gunzip -> skullstrip -> smooth
        % ------------------------------------------------------------
        bold_gz = fullfile(bold_dir, sprintf('%s_ses-%s_task-%s_run-%s_space-T1w_desc-preproc_bold.nii.gz', subj_tag, ses, task, run));
        mask_gz = fullfile(mask_dir, sprintf('%s_ses-%s_task-%s_run-%s_desc-brain_mask.nii.gz', subj_tag, ses, task, run));

        if ~exist(bold_gz,'file')
            warning('Missing BOLD: %s (skipping task)', bold_gz);
            continue
        end
        if ~exist(mask_gz,'file')
            warning('Missing MASK: %s (skipping task)', mask_gz);
            continue
        end

        % Copy gz to Workdir
        bold_gz_local = fullfile(Workdir, basename(bold_gz));
        if ~exist(bold_gz_local,'file'), copyfile(bold_gz, bold_gz_local); end

        mask_gz_local = fullfile(Workdir, basename(mask_gz));
        if ~exist(mask_gz_local,'file'), copyfile(mask_gz, mask_gz_local); end

        % gunzip to .nii
        bold_nii_local = erase(bold_gz_local, '.gz');
        if ~exist(bold_nii_local,'file'), gunzip(bold_gz_local); end

        mask_nii_local = erase(mask_gz_local, '.gz');
        if ~exist(mask_nii_local,'file'), gunzip(mask_gz_local); end


        % Reslice mask into BOLD space (for analysis mask later)
        mask_in_bold = fullfile(Workdir, ['mb_' basename(mask_nii_local)]);
        if ~exist(mask_in_bold,'file')
            mask_in_bold = coreg_reslice_mask_to_bold(mask_nii_local, bold_nii_local, mask_in_bold);
        end

        % IMPORTANT: smooth the ORIGINAL bold (not masked)
        scans_bold = cellstr(spm_select('expand', bold_nii_local));
        [~,n,e] = fileparts(bold_nii_local);
        smoothed_nii = fullfile(Workdir, [smooth_prefix n e]);  % e.g., s3sub-...bold.nii

        if ~exist(smoothed_nii,'file')
            matlabbatch = [];
            matlabbatch{1}.spm.spatial.smooth.data = scans_bold;
            matlabbatch{1}.spm.spatial.smooth.fwhm = smooth_fwhm;
            matlabbatch{1}.spm.spatial.smooth.dtype = 0;
            matlabbatch{1}.spm.spatial.smooth.im = 0;
            matlabbatch{1}.spm.spatial.smooth.prefix = smooth_prefix;
            spm_jobman('run', matlabbatch);
        end

        scans_sm = cellstr(spm_select('expand', smoothed_nii));

        % ------------------------------------------------------------
        % 2) Condition: Stim (for ALL tasks, including sham rest)
        %    Uses your *_bold_stim.txt convention.
        % ------------------------------------------------------------
        stim_file = fullfile(stim_dir, sprintf('%s_ses-%s_task-%s_run-%s_bold_stim.txt', subj_tag, ses, task, run));
        if ~exist(stim_file,'file')
            warning('Missing stim file: %s (skipping task)', stim_file);
            continue
        end

        S = read_stim_file(stim_file);

        cond = struct([]);
        cond(1).name     = 'Stim';
        cond(1).onset    = S.onset;
        cond(1).duration = S.duration;
        cond(1).tmod     = 0;
        cond(1).pmod     = struct('name', {}, 'param', {}, 'poly', {});
        cond(1).orth     = 1;

        % Optional amplitude column as pmod if present and non-constant
        if isfield(S,'amp') && ~isempty(S.amp) && any(abs(S.amp - 1) > 1e-6)
            cond(1).pmod(1).name  = 'amp';
            cond(1).pmod(1).param = S.amp;
            cond(1).pmod(1).poly  = 1;
        end

        % ------------------------------------------------------------
        % 3) Nuisance regressors (motion + outliers ONLY)
        %    (No respiration: already RetroICORed)
        % ------------------------------------------------------------
        motion_file = fullfile(motion_dir, sprintf('%s_ses-%s_task-%s_run-%s_motion_regressors.txt', subj_tag, ses, task, run));
        if ~exist(motion_file,'file')
            warning('Missing motion regressor file: %s (continuing without it)', motion_file);
            motion_file = '';  % allow model w/out multi_reg if needed
        end

        % ------------------------------------------------------------
        % 4) Specify + estimate first-level
        % ------------------------------------------------------------
        matlabbatch = [];
        matlabbatch{1}.spm.stats.fmri_spec.dir = {TaskOutdir};
        matlabbatch{1}.spm.stats.fmri_spec.timing.units = 'secs';
        matlabbatch{1}.spm.stats.fmri_spec.timing.RT = TR;

        % keep defaults unless you need microtime tuning
        matlabbatch{1}.spm.stats.fmri_spec.timing.fmri_t  = 16;
        matlabbatch{1}.spm.stats.fmri_spec.timing.fmri_t0 = 8;

        matlabbatch{1}.spm.stats.fmri_spec.sess(1).scans = scans_sm;
        matlabbatch{1}.spm.stats.fmri_spec.sess(1).cond  = cond;

        matlabbatch{1}.spm.stats.fmri_spec.sess(1).multi   = {''};
        matlabbatch{1}.spm.stats.fmri_spec.sess(1).regress = struct('name', {}, 'val', {});
        matlabbatch{1}.spm.stats.fmri_spec.sess(1).multi_reg = { iff(~isempty(motion_file), motion_file, '') };
        matlabbatch{1}.spm.stats.fmri_spec.sess(1).hpf = 128;

        % Mimic FEAT "3 basis functions": HRF + time + dispersion derivatives
        matlabbatch{1}.spm.stats.fmri_spec.bases.hrf.derivs = [1 1];

        matlabbatch{1}.spm.stats.fmri_spec.volt   = 1;
        matlabbatch{1}.spm.stats.fmri_spec.global = 'None';
        matlabbatch{1}.spm.stats.fmri_spec.mask = {mask_in_bold};  % explicit mask
        matlabbatch{1}.spm.stats.fmri_spec.mthresh = 0;            % reduce implicit masking impact
        matlabbatch{1}.spm.stats.fmri_spec.cvi  = 'AR(1)';

        spm_jobman('run', matlabbatch);

        spm_mat_file = fullfile(TaskOutdir, 'SPM.mat');
        matlabbatch = [];
        matlabbatch{1}.spm.stats.fmri_est.spmmat = {spm_mat_file};
        matlabbatch{1}.spm.stats.fmri_est.write_residuals = 0;
        matlabbatch{1}.spm.stats.fmri_est.method.Classical = 1;
        spm_jobman('run', matlabbatch);

        % ------------------------------------------------------------
        % 5) Contrasts: Stim+(1..3), Stim-(1..3), and F across all 3
        % ------------------------------------------------------------
        matlabbatch = [];
        matlabbatch{1}.spm.stats.con.spmmat = {spm_mat_file};
        matlabbatch{1}.spm.stats.con.delete = 1;

        matlabbatch{1}.spm.stats.con.consess{1}.tcon.name    = 'Stim + (canonical)';
        matlabbatch{1}.spm.stats.con.consess{1}.tcon.weights = [1 0 0];
        matlabbatch{1}.spm.stats.con.consess{1}.tcon.sessrep = 'none';

        matlabbatch{1}.spm.stats.con.consess{2}.tcon.name    = 'Stim + (time-deriv)';
        matlabbatch{1}.spm.stats.con.consess{2}.tcon.weights = [0 1 0];
        matlabbatch{1}.spm.stats.con.consess{2}.tcon.sessrep = 'none';

        matlabbatch{1}.spm.stats.con.consess{3}.tcon.name    = 'Stim + (disp-deriv)';
        matlabbatch{1}.spm.stats.con.consess{3}.tcon.weights = [0 0 1];
        matlabbatch{1}.spm.stats.con.consess{3}.tcon.sessrep = 'none';

        matlabbatch{1}.spm.stats.con.consess{4}.tcon.name    = 'Stim - (canonical)';
        matlabbatch{1}.spm.stats.con.consess{4}.tcon.weights = [-1 0 0];
        matlabbatch{1}.spm.stats.con.consess{4}.tcon.sessrep = 'none';

        matlabbatch{1}.spm.stats.con.consess{5}.tcon.name    = 'Stim - (time-deriv)';
        matlabbatch{1}.spm.stats.con.consess{5}.tcon.weights = [0 -1 0];
        matlabbatch{1}.spm.stats.con.consess{5}.tcon.sessrep = 'none';

        matlabbatch{1}.spm.stats.con.consess{6}.tcon.name    = 'Stim - (disp-deriv)';
        matlabbatch{1}.spm.stats.con.consess{6}.tcon.weights = [0 0 -1];
        matlabbatch{1}.spm.stats.con.consess{6}.tcon.sessrep = 'none';

        matlabbatch{1}.spm.stats.con.consess{7}.fcon.name    = 'Stim (any basis)';
        matlabbatch{1}.spm.stats.con.consess{7}.fcon.weights = eye(3);
        matlabbatch{1}.spm.stats.con.consess{7}.fcon.sessrep = 'none';

        spm_jobman('run', matlabbatch);

        fprintf('Finished task: %s | %s\n', subj_tag, task);

    end

    fprintf('DONE subject: %s\n', subj_tag);

end

fprintf('\nALL DONE. Outputs in: %s\n', outputDir);

end

% =========================
% Helper functions
% =========================

function subs = read_subject_list(fname)
    subs = {};
    fid = fopen(fname,'r');
    if fid == -1, error('Cannot open %s', fname); end
    tline = fgetl(fid);
    while ischar(tline)
        s = strtrim(tline);
        if ~isempty(s)
            subs{end+1} = s; %#ok<AGROW>
        end
        tline = fgetl(fid);
    end
    fclose(fid);
end

function subj_tag = normalize_subj(s)
    s = strtrim(s);
    if startsWith(s,'sub-')
        subj_tag = s;
    else
        subj_tag = ['sub-' s];
    end
end

function S = read_stim_file(stim_file)
    % Supports:
    %  - 1 col: onset (sec), duration=0
    %  - 2 col: onset, duration
    %  - 3 col: onset, duration, amplitude
    M = readmatrix(stim_file);
    if isempty(M)
        error('Stim file is empty: %s', stim_file);
    end
    if size(M,2) == 1
        S.onset = M(:,1);
        S.duration = zeros(size(M,1),1);
    elseif size(M,2) == 2
        S.onset = M(:,1);
        S.duration = M(:,2);
    else
        S.onset = M(:,1);
        S.duration = M(:,2);
        S.amp = M(:,3);
    end
    S.onset = S.onset(:);
    S.duration = S.duration(:);
    if isfield(S,'amp'), S.amp = S.amp(:); end
end

function out = basename(p)
    [~,n,e] = fileparts(p);
    out = [n e];
end

function out = iff(cond, a, b)
    if cond, out = a; else, out = b; end
end

function apply_mask_4d(bold_nii, mask_nii, out_nii)
    Vb = spm_vol(bold_nii);
    Vm = spm_vol(mask_nii);

    % mask should be 3D; if 4D, take first vol
    if numel(Vm) > 1
        Vm = Vm(1);
    end

    % sanity: dimensions must match
    if any(Vb(1).dim ~= Vm.dim)
        error('Mask dims (%s) do not match BOLD dims (%s). Check that mask and bold are in same space.', ...
              mat2str(Vm.dim), mat2str(Vb(1).dim));
    end

    M = spm_read_vols(Vm);
    M = double(M > 0); % binarize

    % output header for multi-vol nifti
    Vo = Vb;
    for k = 1:numel(Vo)
        Vo(k).fname = out_nii;
    end
    Vo = spm_create_vol(Vo);

    for k = 1:numel(Vb)
        Y = spm_read_vols(Vb(k));
        Y = Y .* M;
        spm_write_vol(Vo(k), Y);
    end
end

function out_mask = coreg_reslice_mask_to_bold(mask_nii, bold_nii, out_mask)
    % Coregister+reslice mask into BOLD space (grid + header geometry).
    % Uses nearest-neighbor interpolation to preserve binary nature.
    %
    % mask_nii: path to mask (3D)
    % bold_nii: path to BOLD (4D or 3D); we use first volume as reference
    % out_mask: desired output filename (in same folder typically)

    % Reference should be a single volume; use first volume if 4D
    Vb = spm_vol(bold_nii);
    ref_vol = [Vb(1).fname ',' num2str(Vb(1).n(1))];  % "file.nii,1"

    % Run coregister (estimate) + reslice (write) using batch
    matlabbatch = [];
    matlabbatch{1}.spm.spatial.coreg.estwrite.ref    = {ref_vol};
    matlabbatch{1}.spm.spatial.coreg.estwrite.source = {mask_nii};
    matlabbatch{1}.spm.spatial.coreg.estwrite.other  = {''};

    % Estimation options (reasonable defaults)
    matlabbatch{1}.spm.spatial.coreg.estwrite.eoptions.cost_fun = 'nmi';
    matlabbatch{1}.spm.spatial.coreg.estwrite.eoptions.sep      = [4 2];
    matlabbatch{1}.spm.spatial.coreg.estwrite.eoptions.tol      = ...
        [0.02 0.02 0.02 0.001 0.001 0.001 0.01 0.01 0.01 0.001 0.001 0.001];
    matlabbatch{1}.spm.spatial.coreg.estwrite.eoptions.fwhm     = [7 7];

    % Reslice options: NN interp for masks
    matlabbatch{1}.spm.spatial.coreg.estwrite.roptions.interp   = 0; % nearest neighbor
    matlabbatch{1}.spm.spatial.coreg.estwrite.roptions.wrap     = [0 0 0];
    matlabbatch{1}.spm.spatial.coreg.estwrite.roptions.mask     = 0;
    matlabbatch{1}.spm.spatial.coreg.estwrite.roptions.prefix   = 'r';

    spm_jobman('run', matlabbatch);

    % SPM writes resliced source with prefix 'r' in same folder as mask_nii
    [mpath,mname,mext] = fileparts(mask_nii);
    produced = fullfile(mpath, ['r' mname mext]);

    if ~exist(produced,'file')
        error('Expected resliced mask not found: %s', produced);
    end

    % Move/copy to requested output name (keeps workdir tidy)
    if ~strcmp(produced, out_mask)
        copyfile(produced, out_mask);
    end

    % Re-binarize after reslicing (safety)
    Vm = spm_vol(out_mask);
    Y  = spm_read_vols(Vm);
    Y  = double(Y > 0.5);
    spm_write_vol(Vm, Y);

    out_mask = out_mask;
end
