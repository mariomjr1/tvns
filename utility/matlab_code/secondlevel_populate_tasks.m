function secondlevel_populate_tasks(firstlevel_root, output_dir, varargin)
% SECONDLEVEL_POPULATE_TASKS  (Step 08, Part 1)
% Gather first-level contrast images into per-task group folders.
%
% Step07 writes contrasts as:
%     <firstlevel_root>/<subject>/<task>/<con_name>
% This copies each into a flat per-task folder, renamed by subject so the
% group membership (cases / controls) can be resolved in Part 2:
%     <output_dir>/<task>/<subject>.nii
%
% Usage:
%   secondlevel_populate_tasks(firstlevel_root, output_dir)
%   secondlevel_populate_tasks(..., 'Tasks',{'BlockStim','ContinuousStim','rest'}, ...
%                                   'ConName','wcon_0001.nii')
%
% Required:
%   firstlevel_root  step07 output root (<subject>/<task>/<con_name>)
%   output_dir       where per-task folders are created
%
% Optional name-value:
%   Tasks    cellstr of task names (default {'BlockStim','ContinuousStim','rest'})
%   ConName  contrast image filename (default 'wcon_0001.nii' — MNI-warped)
%   ExpectedConName  expected SPM contrast name for ConName's index
%                    (default 'Stim > baseline'). Verified from each subject's
%                    SPM.mat. A mismatch is FLAGGED + LOGGED but the subject is
%                    NEVER skipped (Task 27) — review the log before group stats.
%
% Output:
%   <output_dir>/<task>/<subject>.nii   (one per subject per task)
%   plus a manifest <output_dir>/<task>/_subjects.txt listing the subjects,
%   and a contrast-verification log <output_dir>/_contrast_check.csv.
%
% Created by Mario Murakami

    p = inputParser();
    addRequired(p, 'firstlevel_root');
    addRequired(p, 'output_dir');
    % 'rest' excluded by default — resting baseline, no Stim contrast (Task 10).
    addParameter(p, 'Tasks',   {'BlockStim','ContinuousStim'}, @iscell);
    addParameter(p, 'ConName', 'wcon_0001.nii', @(x) ischar(x)||isstring(x));
    addParameter(p, 'ExpectedConName', 'Stim > baseline', @(x) ischar(x)||isstring(x));
    parse(p, firstlevel_root, output_dir, varargin{:});

    firstlevel_root = char(p.Results.firstlevel_root);
    output_dir      = char(p.Results.output_dir);
    tasks           = p.Results.Tasks;
    con_name        = char(p.Results.ConName);
    expected_con    = char(p.Results.ExpectedConName);
    % Contrast index implied by ConName (e.g. wcon_0001.nii -> 1), used to look up
    % SPM.xCon(idx).name for verification. NaN if ConName has no number.
    tok = regexp(con_name, '\d+', 'match', 'once');
    if isempty(tok), con_idx = NaN; else, con_idx = str2double(tok); end

    if ~exist(firstlevel_root, 'dir')
        error('First-level root not found: %s', firstlevel_root);
    end
    if ~exist(output_dir, 'dir'), mkdir(output_dir); end

    fprintf('\n========================================\n');
    fprintf(' secondlevel_populate_tasks (Step 08 Part 1)\n');
    fprintf('========================================\n');
    fprintf(' First-level root: %s\n', firstlevel_root);
    fprintf(' Output:           %s\n', output_dir);
    fprintf(' Contrast image:   %s\n', con_name);
    fprintf(' Tasks:            %s\n\n', strjoin(tasks, ', '));

    % Discover subject folders (any dir directly under firstlevel_root)
    d = dir(firstlevel_root);
    subjects = {d([d.isdir] & ~startsWith({d.name}, '.')).name};

    total = 0;
    n_flag = 0;
    check_rows = {};   % {subj, task, con_image, expected, actual, status}
    for t = 1:numel(tasks)
        task = tasks{t};
        task_out = fullfile(output_dir, task);
        if ~exist(task_out, 'dir'), mkdir(task_out); end

        n = 0;
        manifest = {};
        for s = 1:numel(subjects)
            subj = subjects{s};
            src = fullfile(firstlevel_root, subj, task, con_name);
            if exist(src, 'file')
                dst = fullfile(task_out, [subj '.nii']);
                copyfile(src, dst);              % never skip — copy regardless
                manifest{end+1, 1} = subj; %#ok<AGROW>
                n = n + 1;

                % Verify contrast identity (Task 27): flag + log, do NOT skip.
                spm_mat = fullfile(firstlevel_root, subj, task, 'SPM.mat');
                [vstatus, actual] = verify_contrast(spm_mat, con_idx, expected_con);
                check_rows(end+1, :) = {subj, task, con_name, expected_con, ...
                                        actual, vstatus}; %#ok<AGROW>
                if ~strcmp(vstatus, 'OK')
                    fprintf(['  [FLAG] %s/%s: %s = "%s" (expected "%s") [%s] ' ...
                             '- copied anyway\n'], subj, task, con_name, actual, ...
                            expected_con, vstatus);
                    n_flag = n_flag + 1;
                end
            end
        end

        % Write a manifest of subjects present for this task
        fid = fopen(fullfile(task_out, '_subjects.txt'), 'w');
        if fid > 0
            for k = 1:numel(manifest)
                fprintf(fid, '%s\n', manifest{k});
            end
            fclose(fid);
        end

        fprintf('[%s] copied %d subject contrast(s) -> %s\n', task, n, task_out);
        total = total + n;
    end

    % ── Contrast-verification log (Task 27) — advisory; nobody is skipped ──────
    log_path = fullfile(output_dir, '_contrast_check.csv');
    fid = fopen(log_path, 'w');
    if fid > 0
        fprintf(fid, 'subject,task,con_image,expected,actual,status\n');
        for k = 1:size(check_rows, 1)
            fprintf(fid, '%s,%s,%s,%s,%s,%s\n', check_rows{k,1}, check_rows{k,2}, ...
                    check_rows{k,3}, check_rows{k,4}, ...
                    strrep(check_rows{k,5}, ',', ' '), check_rows{k,6});
        end
        fclose(fid);
    end

    fprintf('\nDone. %d contrast image(s) populated under %s\n', total, output_dir);
    if n_flag > 0
        fprintf('*** %d contrast-verification FLAG(s) — review %s (subjects copied anyway) ***\n', ...
                n_flag, log_path);
    else
        fprintf('Contrast verification: all match "%s"  (log: %s)\n', expected_con, log_path);
    end
    fprintf('========================================\n\n');
end


% ── Verify a subject's contrast identity from SPM.mat (Task 27) ────────────────
function [status, actual] = verify_contrast(spm_mat, con_idx, expected)
% Returns status: OK | MISMATCH | NO_SPM | NO_XCON | LOAD_ERROR | UNVERIFIED
% and the actual contrast name found (empty if none). Never throws.
    status = 'UNVERIFIED';
    actual = '';
    if isempty(con_idx) || isnan(con_idx)
        return;   % ConName has no index to look up
    end
    if exist(spm_mat, 'file') ~= 2
        status = 'NO_SPM';
        return;
    end
    try
        S = load(spm_mat);
        if isfield(S, 'SPM') && isfield(S.SPM, 'xCon') && numel(S.SPM.xCon) >= con_idx
            actual = S.SPM.xCon(con_idx).name;
            if strcmpi(strtrim(actual), strtrim(expected))
                status = 'OK';
            else
                status = 'MISMATCH';
            end
        else
            status = 'NO_XCON';
        end
    catch
        status = 'LOAD_ERROR';
    end
end
