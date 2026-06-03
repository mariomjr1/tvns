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
%
% Output:
%   <output_dir>/<task>/<subject>.nii   (one per subject per task)
%   plus a manifest <output_dir>/<task>/_subjects.txt listing the subjects.
%
% Created by Mario Murakami

    p = inputParser();
    addRequired(p, 'firstlevel_root');
    addRequired(p, 'output_dir');
    addParameter(p, 'Tasks',   {'BlockStim','ContinuousStim','rest'}, @iscell);
    addParameter(p, 'ConName', 'wcon_0001.nii', @(x) ischar(x)||isstring(x));
    parse(p, firstlevel_root, output_dir, varargin{:});

    firstlevel_root = char(p.Results.firstlevel_root);
    output_dir      = char(p.Results.output_dir);
    tasks           = p.Results.Tasks;
    con_name        = char(p.Results.ConName);

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
                copyfile(src, dst);
                manifest{end+1, 1} = subj; %#ok<AGROW>
                n = n + 1;
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

    fprintf('\nDone. %d contrast image(s) populated under %s\n', total, output_dir);
    fprintf('========================================\n\n');
end
