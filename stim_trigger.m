%% Clear workspace
clearvars

%% Paths, filenames, etc.
source     = '/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/fsl-regressed/stimtrigger';
dest       = '/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/fsl-regressed/stimtrigger';
descriptor = '7T1813CI_20240716'; 

% The specific .mat file to process
physioFile = fullfile(source, [descriptor '_Rest.mat']);

%% Parameters
sr     = 1000;   % (sample rate). In new data, all channels have samplerate=1000.
maxvol = 326;    % number of volumes in sequence (example)
TR     = 1.19;   % repetition time in seconds

%% Process the specified file
%----------------------------------------------------------------------
% 1) Load the .mat file
%----------------------------------------------------------------------
[~, baseName, ~] = fileparts(physioFile); % Extract base filename without extension
Data   = load(physioFile);  % Load new data structure

% Channel roles in 'titles' (this can vary, so well dynamically assign based on names)
titles = Data.titles;

% Ensure titles is a cell array of strings
if ~iscell(titles)
    titles = cellstr(titles);  % Convert to cell array of strings if not already
end

% Identify channels based on their names (e.g., Respiration, Piezo, Stimulus, MRI)
respIdx = find(contains(titles, 'Respiration', 'IgnoreCase', true));
piezoIdx = find(contains(titles, 'Piezo', 'IgnoreCase', true));
stimIdx = find(contains(titles, {'Stim Trigger', 'Stimulus'}, 'IgnoreCase', true));
mriIdx = find(contains(titles, 'MRI Trigger', 'IgnoreCase', true));

% Extract relevant channels using dynamic indices
resp = Data.data(Data.datastart(respIdx) : Data.dataend(respIdx));
piezo = Data.data(Data.datastart(piezoIdx) : Data.dataend(piezoIdx));
stimSig = Data.data(Data.datastart(stimIdx) : Data.dataend(stimIdx));
MRItrig = Data.data(Data.datastart(mriIdx) : Data.dataend(mriIdx));

% Construct time vector for the MRI trigger channel
timeFull = (0 : length(MRItrig)-1) / sr;

%----------------------------------------------------------------------
% 2) Find the first MRI trigger to define start of scanning
%----------------------------------------------------------------------
% Using the same approach as old code:
% findpeaks with 'MINPEAKHEIGHT', 1.5 or whatever threshold is relevant
[pks, locs] = findpeaks(MRItrig, 'MINPEAKHEIGHT', 1.5);
start_ix    = locs(1);  % index of the first MRI trigger

% Infer the stop index based on the data length from datastart and dataend
stop_ix = min(Data.dataend(stimIdx), length(MRItrig));  % Use the end of stimSig or length of MRI data

% Now extract the portion of the stim signal we care about
stim = stimSig(start_ix : stop_ix);

% Similarly, define the time vector for this slice
time = timeFull(start_ix : stop_ix);

% Shift the time so that 0 = first MRI trigger
time = time - time(1);

%----------------------------------------------------------------------
% 3) Clean up the stim channel (like your old code)
%----------------------------------------------------------------------
% E.g., if Stim < 0, set to 0; if Stim > 3, set to 3
stim(stim < 0) = 0;
stim(stim > 3) = 3;

%----------------------------------------------------------------------
% 4) Find onsets and offsets based on threshold changes
%----------------------------------------------------------------------
startInds = find(diff(stim) > 1.5);
stopInds  = find(diff(stim) < -1.5);

% Remove spurious starts/stops that are too close (less than 1.5 * sr)
temp = diff(startInds);
startInds(temp < 1.5*sr) = [];

temp = diff(stopInds);
stopInds(temp < 1.5*sr) = [];

% Convert these indices to time
onTimes  = time(startInds);
offTimes = time(stopInds);

% Pair onsets and offsets to ensure matching lengths
validPairs = [];
for i = 1:length(onTimes)
    % Find the first offset that occurs after this onset
    validOffset = find(offTimes > onTimes(i), 1, 'first');
    if ~isempty(validOffset) && validOffset <= length(offTimes)
        validPairs = [validPairs; i, validOffset];
    end
end

% Keep only paired onsets and offsets
if ~isempty(validPairs)
    onTimes  = onTimes(validPairs(:,1));
    offTimes = offTimes(validPairs(:,2));
else
    warning('No valid onset-offset pairs found. STIMS will be empty.');
    onTimes = [];
    offTimes = [];
end

%----------------------------------------------------------------------
% 5) Build STIMS array: (onset, duration, 1)
%----------------------------------------------------------------------
if ~isempty(onTimes)
    STIMS = ones(numel(onTimes), 3);
    STIMS(:,1) = onTimes;
    STIMS(:,2) = offTimes - onTimes;  % duration (now safe because lengths match)
else
    STIMS = []; % Empty array if no valid pairs
end

%----------------------------------------------------------------------
% 6) Save to text file --
outName = strcat(baseName, '_Stim.txt'); % Add '_Stim.txt' suffix
outPath = fullfile(dest, outName);       % Create full file path

% Ensure the destination directory exists
if ~exist(dest, 'dir')
    mkdir(dest); % Create the directory if it doesn't exist
end

% Save the STIMS matrix to the text file
save(outPath, 'STIMS', '-ascii');

fprintf('Created STIM file: %s\n', outPath);
