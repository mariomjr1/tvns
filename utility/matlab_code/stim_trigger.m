%% Clear workspace
clearvars

%% Paths
source = '/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata/derivatives/physio/0_raw_from_roberta';
dest   = '/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata/derivatives/physio/6_stimtrigger';

%% Parameters
sr = 1000;   % sampling rate (Hz)

%% Make sure destination exists
if ~exist(dest, 'dir')
    mkdir(dest);
end

%% Find all .mat files in source
matFiles = dir(fullfile(source, '*.mat'));

if isempty(matFiles)
    error('No .mat files found in source: %s', source);
end

%% Loop over all files
for f = 1:numel(matFiles)

    physioFile = fullfile(source, matFiles(f).name);
    [~, baseName, ~] = fileparts(physioFile);

    fprintf('\n[%d/%d] Processing: %s\n', f, numel(matFiles), physioFile);

    try
        %--------------------------------------------------------------
        % 1) Load .mat
        %--------------------------------------------------------------
        Data = load(physioFile);

        % Basic field checks (skip file if unexpected format)
        requiredFields = {'titles','data','datastart','dataend'};
        missing = requiredFields(~isfield(Data, requiredFields));
        if ~isempty(missing)
            warning('Skipping %s (missing fields: %s)', matFiles(f).name, strjoin(missing, ', '));
            continue
        end

        titles = Data.titles;
        if ~iscell(titles)
            titles = cellstr(titles);
        end

        % Identify channels by name
        respIdx  = find(contains(titles, 'Respiration', 'IgnoreCase', true), 1, 'first');
        piezoIdx = find(contains(titles, 'Piezo',       'IgnoreCase', true), 1, 'first');
        stimIdx  = find(contains(titles, {'Stim Trigger','Stimulus'}, 'IgnoreCase', true), 1, 'first');
        mriIdx   = find(contains(titles, 'MRI Trigger',  'IgnoreCase', true), 1, 'first');

        if isempty(stimIdx) || isempty(mriIdx)
            warning('Skipping %s (could not find Stim and/or MRI Trigger channel in titles)', matFiles(f).name);
            continue
        end

        % Extract channels (resp/piezo optional)
        stimSig = Data.data(Data.datastart(stimIdx) : Data.dataend(stimIdx));
        MRItrig = Data.data(Data.datastart(mriIdx)  : Data.dataend(mriIdx));

        %--------------------------------------------------------------
        % 2) Time vector + first MRI trigger (scan start)
        %--------------------------------------------------------------
        timeFull = (0 : length(MRItrig)-1) / sr;

        [pks, locs] = findpeaks(MRItrig, 'MINPEAKHEIGHT', 1.5);
        if isempty(locs)
            warning('Skipping %s (no MRI trigger peaks found above threshold)', matFiles(f).name);
            continue
        end
        start_ix = locs(1);

        % Define stop index safely in the LOCAL vector index space
        stop_ix = min([length(stimSig), length(MRItrig)]);

        if start_ix >= stop_ix
            warning('Skipping %s (start_ix >= stop_ix; check trigger alignment / lengths)', matFiles(f).name);
            continue
        end

        stim = stimSig(start_ix : stop_ix);
        time = timeFull(start_ix : stop_ix);
        time = time - time(1);

        %--------------------------------------------------------------
        % 3) Clean stim channel
        %--------------------------------------------------------------
        stim(stim < 0) = 0;
        stim(stim > 3) = 3;

        %--------------------------------------------------------------
        % 4) Detect onsets/offsets by thresholded step changes
        %--------------------------------------------------------------
        startInds = find(diff(stim) > 1.5);
        stopInds  = find(diff(stim) < -1.5);

        % Debounce events too close (< 1.5 s)
        if numel(startInds) > 1
            temp = diff(startInds);
            startInds(temp < 1.5*sr) = [];
        end
        if numel(stopInds) > 1
            temp = diff(stopInds);
            stopInds(temp < 1.5*sr) = [];
        end

        onTimes  = time(startInds);
        offTimes = time(stopInds);

        % Pair on/off (consume offsets so one offset can't match multiple onsets)
        on2  = [];
        off2 = [];
        j = 1;
        for i = 1:numel(onTimes)
            while j <= numel(offTimes) && offTimes(j) <= onTimes(i)
                j = j + 1;
            end
            if j <= numel(offTimes)
                on2(end+1,1)  = onTimes(i);     %#ok<SAGROW>
                off2(end+1,1) = offTimes(j);    %#ok<SAGROW>
                j = j + 1; % consume this offset
            end
        end

        onTimes  = on2;
        offTimes = off2;

        %--------------------------------------------------------------
        % 5) Build STIMS: (onset, duration, 1)
        %--------------------------------------------------------------
        if ~isempty(onTimes)
            STIMS = ones(numel(onTimes), 3);
            STIMS(:,1) = onTimes;
            STIMS(:,2) = offTimes - onTimes;
        else
            STIMS = [];
            warning('No valid onset-offset pairs found in %s. Writing empty STIMS.', matFiles(f).name);
        end

        %--------------------------------------------------------------
        % 6) Save
        %--------------------------------------------------------------
        outName = strcat(baseName, '_Stim.txt');
        outPath = fullfile(dest, outName);
        save(outPath, 'STIMS', '-ascii');

        fprintf('Created STIM file: %s\n', outPath);

    catch ME
        warning('Error processing %s: %s', matFiles(f).name, ME.message);
        continue
    end
end

fprintf('\nDone. Processed %d .mat files from:\n%s\nOutput in:\n%s\n', numel(matFiles), source, dest);
