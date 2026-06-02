function [qrs_1D_array, resp_1D_array] = generate_1D_fun_1(HB, R, resp, vols, ...
        TR, sr, fs, SMS, MRtrig, fname)
%GENERATE_1D create .1D files to denoise fMRI data.
%   Uses respiration and (optionally) heart beat info to generate RETROICOR 1D files.
%   If HB and/or R are empty, cardiac regressors are skipped (no R-DECO / no PIEZO case).
%
% INPUTS:
%   HB      (array)  : heartbeat signal (optional; can be [])
%   R       (array)  : R-peak locations (seconds or indices) (optional; can be [])
%   resp    (array)  : respiration signal
%   vols    (double) : number of volumes
%   TR      (double) : acquisition time (s)
%   sr      (double) : physio sampling rate (Hz)
%   fs      (double) : desired output sampling rate (Hz) (e.g., 40)
%   SMS     (logical): 1 for SMS, 0 for non-SMS
%   MRtrig  (array)  : scanner trigger
%   fname   (string) : file identifier
%
% OUTPUTS:
%   qrs_1D_array  : cardiac retroicor array (empty if skipped)
%   resp_1D_array : respiration retroicor array

%% ---------------------------
% Validate minimal inputs
%% ---------------------------
if isempty(resp) || isempty(MRtrig)
    error('resp and MRtrig must be provided and non-empty.');
end

%% ---------------------------
% Remove extra MR trigger if needed
%% ---------------------------
MRtrig_diff = diff(MRtrig);
[~, trig_idx] = findpeaks(MRtrig_diff, 'MinPeakHeight', 2);

if numel(trig_idx) ~= vols
    error('Number of MR scanner triggers ~= number of volumes.');
end

dist = diff(trig_idx);
dmax = max(dist); t_dmax = dmax/sr;
dmin = min(dist); t_dmin = dmin/sr;

if t_dmax > TR + 1/sr
    error('Detected trigger peaks are too far apart.');
elseif t_dmin < TR - 1/sr
    error('Detected trigger peaks are too far apart.');
end

%% Assign sampling window
first_sample_idx = trig_idx(1);
last_sample_idx  = trig_idx(end) + round(TR*sr);

%% (Optional) Plot trigger + detected trigger peaks
% Comment out these figures if you are running many subjects headless
figure; hold on;
p(1) = plot(MRtrig);
p(2) = plot(trig_idx, MRtrig(trig_idx), 'ro');
p(3) = xline(first_sample_idx, 'g', 'LineWidth', 2);
p(4) = xline(last_sample_idx,  'k', 'LineWidth', 2);
legend(p, {'Scanner Trigger','Scanner Trigger Annotated', ...
    'Retroicor Sampling Start', 'Retroicor Sampling End'});
title({'Scanner Trigger with Annotated Peaks', ...
    'Visually verify that the first/last trigger are correctly annotated.'});
hold off;

%% ---------------------------
% Generate respiration retroicor array
%% ---------------------------
resp_cut = resp(first_sample_idx:last_sample_idx);
temp = resp_cut(1:round(sr/fs):end);
RESPretro = medfilt1(temp, 10);
RESPretro(1) = temp(1);

% Save resp 1D
dest = pwd;  % current output folder
respFile = fullfile(dest, ['RETRO-resp_' char(fname) '.1D']);
fid = fopen(respFile, 'w');
fprintf(fid, '%.4f\n', RESPretro);
fclose(fid);
resp_1D_array = RESPretro;

%% ---------------------------
% Cardiac/QRS retroicor array (OPTIONAL)
%% ---------------------------
% Skip cardiac if no R-DECO (R empty) or no HB (HB empty)
if isempty(R) || isempty(HB)
    qrs_1D_array = [];
    return;
end

%% Convert R to indices if given in seconds
% Safer check: if any element is non-integer, treat as seconds
if ~isempty(R)
    if any(mod(double(R(:)), 1) ~= 0)
        R = uint64(double(R) .* sr);
    else
        R = uint64(R);
    end
end

%% Generate QRS array
if SMS == 1
    % Prepare cardiac trigger times in seconds from start of scan for SMS retroicor
    cut_RR = R((first_sample_idx <= R) & (R <= last_sample_idx));
    cut_RR = double(cut_RR);
    RRretro = (cut_RR - first_sample_idx) / sr;  % seconds from scan start
else
    % Prepare binary signal (AFNI 3dretroicor style)
    QRS = zeros(1, numel(HB));
    R = R(R >= 1 & R <= numel(HB));  % clamp to HB bounds
    QRS(R) = 1;

    QRS_cut = QRS(first_sample_idx:last_sample_idx);

    % Smooth so downsampling doesn't miss peaks
    windowSize = 30;
    b = (1/windowSize) * ones(1, windowSize);
    a = 1;
    QRS_sm = filter(b, a, QRS_cut);

    QRS_sm_rs = QRS_sm(1:round(sr/fs):end);
    [~, qrs] = findpeaks(QRS_sm_rs);

    RRretro = zeros(1, numel(QRS_sm_rs));
    RRretro(qrs) = 1;
    RRretro = RRretro';

    if sum(QRS_cut) ~= sum(RRretro)
        disp('Warning: choose a different window size for smoothing.');
    end
end

%% Optional plots (comment out for headless batch)
figure; hold on;
p(1) = plot(HB);
cut_RR_plot = R((first_sample_idx <= R) & (R <= last_sample_idx));
p(2) = plot(double(cut_RR_plot), HB(double(cut_RR_plot)), 'ro');
p(3) = xline(first_sample_idx, 'g', 'LineWidth', 2);
p(4) = xline(last_sample_idx,  'k', 'LineWidth', 2);
legend(p, {'Heart Beat','Annotated Sampled Heart Beat Peaks', ...
    'Retroicor Sampling Start', 'Retroicor Sampling End'});
title({'Heart Beat, Annotated Beat, Retroicor Sampling Start/End', ...
    'Visually verify that the heartbeat is correctly annotated.'});
hold off;

figure;
subplot(2,1,1), plot((1:length(RESPretro))/fs, RESPretro);
xlabel('Time (s)'); ylabel('Respiration');

if SMS == 1
    subplot(2,1,2), plot(RRretro(2:end), diff(RRretro));
    xlabel('Time (s)'); ylabel('R interval');
else
    subplot(2,1,2), plot(1:length(RRretro), RRretro);
    xlabel('Time (s)'); ylabel('Binary QRS signal');
end
sgtitle('Respiration and R Interval');

%% Save QRS 1D file
qrsFile = fullfile(dest, ['RETRO-qrs_' char(fname) '.1D']);
fid = fopen(qrsFile, 'w');
fprintf(fid, '%g\n', RRretro);
fclose(fid);
qrs_1D_array = RRretro;

end
