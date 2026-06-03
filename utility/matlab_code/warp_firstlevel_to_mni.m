function warp_firstlevel_to_mni(con_dir, t1_file, output_dir, spm_dir, varargin)
% WARP_FIRSTLEVEL_TO_MNI  (Step 07 — optional, warp-only)
% Take an already-computed first-level folder (contrast images in native T1w
% space) and warp the contrasts to MNI by segmenting the subject T1.
% No GLM is re-run.
%
%   1. Segment t1_file  ->  forward deformation y_<t1>.nii
%   2. Apply the deformation to each <ConPattern> in con_dir  ->  w<con>.nii
%
% Usage:
%   warp_firstlevel_to_mni(con_dir, t1_file, output_dir, spm_dir)
%   warp_firstlevel_to_mni(..., 'ConPattern','con_*.nii', 'MNIRef','')
%
% Required:
%   con_dir     folder with the first-level contrast images (con_*.nii)
%   t1_file     subject T1 in the SAME native space as the contrasts
%               (.nii or .nii.gz — e.g. fMRIPrep <subj>_desc-preproc_T1w.nii.gz)
%   output_dir  where w*.nii are written ('' or same as con_dir = in place)
%   spm_dir     SPM12 path
%
% Optional name-value:
%   ConPattern  glob for contrast images (default 'con_*.nii')
%   MNIRef      MNI reference image ('' uses spm canonical avg152T1)
%
% Output:
%   <output_dir>/w<con>.nii   for every matched contrast
%
% Created by Mario Murakami

    p = inputParser();
    addRequired(p, 'con_dir');
    addRequired(p, 't1_file');
    addRequired(p, 'output_dir');
    addRequired(p, 'spm_dir');
    addParameter(p, 'ConPattern', 'con_*.nii', @(x) ischar(x)||isstring(x));
    addParameter(p, 'MNIRef',     '',          @(x) ischar(x)||isstring(x));
    parse(p, con_dir, t1_file, output_dir, spm_dir, varargin{:});

    con_dir     = char(p.Results.con_dir);
    t1_file     = char(p.Results.t1_file);
    output_dir  = char(p.Results.output_dir);
    spm_dir     = char(p.Results.spm_dir);
    con_pattern = char(p.Results.ConPattern);
    mni_ref     = char(p.Results.MNIRef);

    if isempty(output_dir), output_dir = con_dir; end

    set(0, 'DefaultFigureVisible', 'off');
    addpath(spm_dir);
    spm('defaults', 'FMRI');
    spm_jobman('initcfg');
    spm_get_defaults('cmdline', true);

    if isempty(mni_ref)
        mni_ref = fullfile(spm_dir, 'canonical', 'avg152T1.nii');
    end

    if ~exist(con_dir, 'dir'), error('con_dir not found: %s', con_dir); end
    if ~exist(t1_file, 'file'), error('t1_file not found: %s', t1_file); end
    if ~exist(output_dir, 'dir'), mkdir(output_dir); end

    % Work folder for the gunzipped T1 + deformation field
    workdir = fullfile(output_dir, 'warp_work');
    if ~exist(workdir, 'dir'), mkdir(workdir); end

    fprintf('\n========================================\n');
    fprintf(' warp_firstlevel_to_mni\n');
    fprintf('========================================\n');
    fprintf(' con_dir:  %s\n', con_dir);
    fprintf(' T1:       %s\n', t1_file);
    fprintf(' output:   %s\n', output_dir);
    fprintf(' pattern:  %s\n\n', con_pattern);

    % ── Find contrasts ────────────────────────────────────────────────────────
    cons = dir(fullfile(con_dir, con_pattern));
    if isempty(cons)
        error('No contrast images matching %s in %s', con_pattern, con_dir);
    end
    fprintf('Found %d contrast image(s).\n', numel(cons));

    % ── Prepare T1 (gunzip into workdir) ──────────────────────────────────────
    t1_nii = gunzip_to(t1_file, workdir);
    mni_ref = gunzip_if_needed(mni_ref);

    [t1_path, t1_name, ~] = fileparts(t1_nii);
    y_file = fullfile(t1_path, ['y_' t1_name '.nii']);

    % ── Segment T1 → forward deformation y_*.nii ──────────────────────────────
    if ~exist(y_file, 'file')
        fprintf('Segmenting T1 ...\n');
        mb = [];
        mb{1}.spm.spatial.preproc.channel.vols     = {[t1_nii ',1']};
        mb{1}.spm.spatial.preproc.channel.biasreg  = 0.001;
        mb{1}.spm.spatial.preproc.channel.biasfwhm = 60;
        mb{1}.spm.spatial.preproc.channel.write    = [0 1];
        for k = 1:6
            mb{1}.spm.spatial.preproc.tissue(k).tpm = ...
                {fullfile(spm_dir, 'tpm', sprintf('TPM.nii,%d', k))};
        end
        ng  = [1 1 2 3 4 2];
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
        error('Deformation field not created: %s', y_file);
    end

    % ── MNI geometry from reference ───────────────────────────────────────────
    Vref = spm_vol(mni_ref);
    bb   = spm_get_bbox(Vref, 'fv');
    vox  = sqrt(sum(Vref.mat(1:3,1:3).^2));

    % ── Warp each contrast ────────────────────────────────────────────────────
    resample = cell(numel(cons), 1);
    for c = 1:numel(cons)
        resample{c} = [fullfile(con_dir, cons(c).name) ',1'];
    end

    mb = [];
    mb{1}.spm.spatial.normalise.write.subj.def      = {y_file};
    mb{1}.spm.spatial.normalise.write.subj.resample = resample;
    mb{1}.spm.spatial.normalise.write.woptions.bb     = bb;
    mb{1}.spm.spatial.normalise.write.woptions.vox    = vox;
    mb{1}.spm.spatial.normalise.write.woptions.interp = 4;
    mb{1}.spm.spatial.normalise.write.woptions.prefix = 'w';
    spm_jobman('run', mb);

    % ── Move w*.nii to output_dir if different from con_dir ───────────────────
    if ~strcmp(output_dir, con_dir)
        for c = 1:numel(cons)
            wname = ['w' cons(c).name];
            src = fullfile(con_dir, wname);
            if exist(src, 'file')
                movefile(src, fullfile(output_dir, wname));
            end
        end
    end

    fprintf('\nWarped %d contrast(s) to MNI -> %s\n', numel(cons), output_dir);
    fprintf('========================================\n\n');
end


% ── Helpers ───────────────────────────────────────────────────────────────────

function out = gunzip_to(gz_file, dest_dir)
    if endsWith(gz_file, '.gz')
        [~, base, ~] = fileparts(gz_file);   % strips .gz -> base.nii
        out = fullfile(dest_dir, base);
        if ~exist(out, 'file'), gunzip(gz_file, dest_dir); end
    else
        % copy plain .nii into dest_dir so the y_ field is written there
        [~, nm, ex] = fileparts(gz_file);
        out = fullfile(dest_dir, [nm ex]);
        if ~exist(out, 'file'), copyfile(gz_file, out); end
    end
end

function out = gunzip_if_needed(f)
    if endsWith(f, '.nii.gz')
        d = fileparts(f);
        gunzip(f, d);
        out = erase(f, '.gz');
    else
        out = f;
    end
end
