function threshold_group_map(analysis_dir, output_dir, spm_dir, varargin)
% THRESHOLD_GROUP_MAP  (Step 09)
% Threshold a second-level SPM contrast (e.g. Cases > Controls) at p < 0.05
% and write a binary significance map + a thresholded t-map.
%
% Reads the SPM.mat and spmT image produced by step08b (group analysis),
% converts the p-threshold to a t-threshold using the exact error df from
% SPM, and keeps the positive tail (cases > controls is the positive
% direction of contrast 1).
%
% Usage:
%   threshold_group_map(analysis_dir, output_dir, spm_dir)
%   threshold_group_map(..., 'P',0.05, 'Extent',0, 'ContrastIndex',1, 'Tail','pos')
%
% Required:
%   analysis_dir  a step08b group folder (contains SPM.mat + spmT_000*.nii),
%                 e.g. <group out>/BlockStim or <group out>/Combined_Block_Continuous
%   output_dir    where the thresholded maps are written
%   spm_dir       SPM12 path
%
% Optional name-value:
%   P              p-threshold, uncorrected (default 0.05)
%   Extent         minimum cluster size in voxels (default 0 = none)
%   ContrastIndex  which contrast/spmT to threshold (default 1 = Cases>Controls)
%   Tail           'pos' (cases>controls), 'neg', or 'two' (default 'pos')
%
% Output:
%   <output_dir>/<conname>_p<P>_mask.nii   binary significance map (1/0)
%   <output_dir>/<conname>_p<P>_tmap.nii   t-values where significant, else 0
%   prints the t-threshold, df, and number of surviving voxels/clusters.
%
% Created by Mario Murakami

    p = inputParser();
    addRequired(p, 'analysis_dir');
    addRequired(p, 'output_dir');
    addRequired(p, 'spm_dir');
    addParameter(p, 'P',             0.05, @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'Extent',        0,    @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'ContrastIndex', 1,    @(x) isnumeric(x)&&isscalar(x));
    addParameter(p, 'Tail',          'pos',@(x) ischar(x)||isstring(x));
    parse(p, analysis_dir, output_dir, spm_dir, varargin{:});

    analysis_dir = char(p.Results.analysis_dir);
    output_dir   = char(p.Results.output_dir);
    spm_dir      = char(p.Results.spm_dir);
    p_thr        = p.Results.P;
    extent       = p.Results.Extent;
    cidx         = p.Results.ContrastIndex;
    tail         = lower(char(p.Results.Tail));

    set(0, 'DefaultFigureVisible', 'off');
    addpath(spm_dir);
    spm('defaults', 'FMRI');

    if ~exist(output_dir, 'dir'), mkdir(output_dir); end

    spm_mat = fullfile(analysis_dir, 'SPM.mat');
    if ~exist(spm_mat, 'file')
        error('SPM.mat not found in %s', analysis_dir);
    end

    % ── Error df + contrast name from SPM.mat ─────────────────────────────────
    S = load(spm_mat);
    SPM = S.SPM;
    erdf = SPM.xX.erdf;

    con_name = sprintf('con%d', cidx);
    if isfield(SPM, 'xCon') && numel(SPM.xCon) >= cidx
        con_name = SPM.xCon(cidx).name;
    end
    con_name_safe = regexprep(con_name, '[^a-zA-Z0-9]', '_');

    % ── Load the t-map ────────────────────────────────────────────────────────
    spmT = fullfile(analysis_dir, sprintf('spmT_%04d.nii', cidx));
    if ~exist(spmT, 'file')
        error('t-map not found: %s', spmT);
    end
    V = spm_vol(spmT);
    T = spm_read_vols(V);

    % ── p -> t threshold (exact df) ───────────────────────────────────────────
    t_thr = spm_invTcdf(1 - p_thr, erdf);
    fprintf('\n========================================\n');
    fprintf(' threshold_group_map (Step 09)\n');
    fprintf('========================================\n');
    fprintf(' Analysis:  %s\n', analysis_dir);
    fprintf(' Contrast:  %s (spmT_%04d)\n', con_name, cidx);
    fprintf(' df (erdf): %.2f\n', erdf);
    fprintf(' p<%.3f  ->  t>%.3f  (tail=%s, extent=%d)\n\n', p_thr, t_thr, tail, extent);

    % ── Apply threshold (positive tail = cases > controls) ────────────────────
    switch tail
        case 'pos', mask = T >  t_thr;
        case 'neg', mask = T < -t_thr;
        otherwise,  mask = abs(T) > t_thr;   % two-tailed (use t at p/2 for exact)
    end
    mask(isnan(T)) = false;

    % ── Cluster-extent threshold ──────────────────────────────────────────────
    if extent > 0
        [L, n] = spm_bwlabel(double(mask), 18);
        for c = 1:n
            if nnz(L == c) < extent
                mask(L == c) = false;
            end
        end
    end

    n_vox = nnz(mask);
    fprintf(' Surviving voxels: %d\n', n_vox);

    % ── Write outputs ─────────────────────────────────────────────────────────
    Vout = V;
    Vout.dt = [spm_type('uint8') 0];
    Vout.fname = fullfile(output_dir, sprintf('%s_p%.3g_mask.nii', con_name_safe, p_thr));
    spm_write_vol(Vout, double(mask));

    Vt = V;
    Vt.fname = fullfile(output_dir, sprintf('%s_p%.3g_tmap.nii', con_name_safe, p_thr));
    Tthr = T; Tthr(~mask) = 0;
    spm_write_vol(Vt, Tthr);

    fprintf('\n Wrote:\n   %s\n   %s\n', ...
        Vout.fname, Vt.fname);
    fprintf('========================================\n\n');
end
