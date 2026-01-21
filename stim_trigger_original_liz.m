clearvars
source = '/autofs/space/ponyo_001/users/liz/GUTBRAIN_FreqMod/physio/channels/';
dest = '/autofs/space/ponyo_001/users/liz/GUTBRAIN_FreqMod/stim_trigger/';
descriptor = 'sub-FM007'; 
ses='ses-brain';
list = strcat(source, descriptor);
tasks = {...
        {'rest'}; ...      
    %   {'stim2Hz'}, ...
        {'stim10Hz'}; ...
    %   {'stim25Hz'}, ...
        {'stim100Hz'}};

sr = 400; %change accordingly
maxvol = 326;
TR = 1.19;

for j=1:length(tasks)
    task = string(tasks{j}(1));
    Name = strcat('annotated_',descriptor,'_',ses,'_task-',task,'_run-001_bold.mat'); %filename of physio .MAT file
    Data = load(strcat(list,'/',Name));  %loads .MAT file

    % imaging start/stop
    MRtrig = Data.Data.Trigger; ticktimes = Data.Data.Time;
    [pks,locs] = findpeaks(MRtrig,'MINPEAKHEIGHT',1.5);
    start = locs(1);
    stop = start + maxvol * sr * TR;
    stim = Data.Data.Stim(start : stop);
    
    time = Data.Data.Time(start : stop); % design time
    time = time - repmat(time(1),1,numel(time));
    
    stim(stim < 0) = 0;
    stim(stim > 3) = 3;
    start = find(diff(stim) > 1.5);
    stop = find(diff(stim) < -1.5);
    temp = diff(start);
    start(temp < 1.5*sr) = [];
    temp = diff(stop);
    stop(temp < 1.5*sr) = [];
    on = time(start);
    %on(end)=[]; %only activate this function (on if greater value) if the original script without this function gives u an array error between on and off at STIMS(:,2) = off - on
    off = time(stop);
    if on(end) > off(end)
        on(end) = [];
    end
    if off(1) < on(1)
        off(1) = [];
    end
    %off(end)=[]; %only activate this function (off if greater value) if the original script without this function gives u an array error between on and off at STIMS(:,2) = off - on

    STIMS = ones(numel(on),3);
    STIMS(:,1) = on;
    STIMS(:,2) = off - on;
    
    save(strcat(dest,Name,'_Stim.txt'),'STIMS','-ascii');
end
