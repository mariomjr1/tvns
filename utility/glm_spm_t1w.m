%generates GLM for MSIT task fMRI using SPM
%created by Jourdan Parent
%modified by Mario Murakami - July 2025

function MSIT_interference_glm_pipeline_spm(sublist)

addpath('/autofs/cluster/vagabond/USERS/MARIO/Packages/matlab/spm12')

% read subjects from a text file
sublist = {};
fid = fopen('SubjectList.txt', 'r');
if fid == -1
    error('Cannot open SubjectList.txt');
end
tline = fgetl(fid);
while ischar(tline)
    sublist{end+1} = strtrim(tline);  % remove any trailing whitespace/newline
    tline = fgetl(fid);
end
fclose(fid);


eventsDir='/autofs/cluster/vagabond/USERS/MARIO/Projects/msit/sourcedata/';
derivativesDir='/autofs/cluster/vagabond/USERS/MARIO/Projects/msit/sourcedata/derivatives/';
outputDir='/autofs/cluster/vagabond/USERS/MARIO/Projects/msit/results/spm12/MSIT_GLM_t1w/';
fileDir=[derivativesDir,'spm/'];

for i = 1:length(sublist)

    sub=sublist{i};
    %%directory of subject bids func
    subeventsDir=[eventsDir,'sub-',sub,'/ses-01/func/'];
    subfuncDir=[derivativesDir,'sub-',sub,'/ses-01/func/'];

    %make sub output directory
    Suboutdir=[outputDir,'sub-',sub];
    if exist(Suboutdir)==0
        mkdir(Suboutdir)
    else
    end

    %make sub file directory
    Subfiledir=[fileDir,'sub-',sub,'/ses-01/func/'];
    if exist(Subfiledir)==0
        mkdir(Subfiledir)
    else
    end



%% copy files
    %copy event file
    %eventsfile=[subeventsDir,'sub-',sub,'_ses-01_task-msit_run-001_events.xls'];
    %copyfile(eventsfile, Suboutdir)
    
    %copy func file
    funcfile=[Subfiledir,'s8sub-',sub,'_ses-01_task-msit_run-001_space-T1w_desc-preproc_bold.nii'];
    if exist(funcfile)==0
        funcfile=[subfuncDir,'sub-',sub,'_ses-01_task-msit_run-001_space-T1w_desc-preproc_bold.nii.gz'];
        copyfile(funcfile, Subfiledir)
        cd(Subfiledir)
        %unzip func file
        gunzip(['sub-',sub,'_ses-01_task-msit_run-001_space-T1w_desc-preproc_bold.nii.gz'])
        funcfile=['sub-',sub,'_ses-01_task-msit_run-001_space-T1w_desc-preproc_bold.nii'];

        spm_input=cellstr(spm_select('expand',fullfile(Subfiledir,funcfile)));



        %% smooth functional files

        matlabbatch{1}.spm.spatial.smooth.data = spm_input;
        matlabbatch{1}.spm.spatial.smooth.fwhm = [8 8 8];
        matlabbatch{1}.spm.spatial.smooth.dtype = 0;
        matlabbatch{1}.spm.spatial.smooth.im = 0;
        matlabbatch{1}.spm.spatial.smooth.prefix = 's8';
        spm_jobman('run',matlabbatch);
        clear matlabbatch

    else
    end

    
    %nuissance regressor file
    regressorfile=[derivativesDir,'sub-',sub,'/ses-01/func/sub-',sub,'_ses-01_task-msit_run-001_nuissance_regressors_for_GLM_no_header.txt'];


    
    
  
    %% specify model
    cd(Subfiledir)
    smoothfile=['s8sub-',sub,'_ses-01_task-msit_run-001_space-T1w_desc-preproc_bold.nii'];
    spm_input=cellstr(spm_select('expand',fullfile(Subfiledir,smoothfile)));

    %get onsets from each condition from events file
    %eventsfile=['sub-',sub,'_ses-01_task-msit_run-001_events.xls'];
    events=readtable('/autofs/cluster/vagabond/USERS/MARIO/Projects/msit/sourcedata/task-msit_run-001_events.xls');
    disp(events.Properties.VariableNames);

    disp('NaNs in onset:'), disp(any(isnan(events.onset)));
    disp('NaNs in duration:'), disp(any(isnan(events.duration)));
    disp('NaNs in trial_type:'), disp(any(isnan(events.trial_type)));

    interference_onsets=events.onset(events.trial_type==2);
    no_interference_onsets=events.onset(events.trial_type==1);
    rest_onsets=events.onset(events.trial_type==0);

    %get durations
    interference_duration=events.duration(events.trial_type==2);
    no_interference_duration=events.duration(events.trial_type==1);
    rest_duration=events.duration(events.trial_type==0);

    %set up batch
    matlabbatch{1}.spm.stats.fmri_spec.dir = {Suboutdir};
    matlabbatch{1}.spm.stats.fmri_spec.timing.units = 'secs';
    matlabbatch{1}.spm.stats.fmri_spec.timing.RT = 1.5;
    matlabbatch{1}.spm.stats.fmri_spec.timing.fmri_t = 22;
    matlabbatch{1}.spm.stats.fmri_spec.timing.fmri_t0 = 11;
    %
    matlabbatch{1}.spm.stats.fmri_spec.sess.scans = spm_input;
    %
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(1).name = 'interference';
    %
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(1).onset = interference_onsets;
    %
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(1).duration = interference_duration;
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(1).tmod = 0;
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(1).pmod = struct('name', {}, 'param', {}, 'poly', {});
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(1).orth = 1;
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(2).name = 'no interference';
    %
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(2).onset = no_interference_onsets;
    %
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(2).duration = no_interference_duration;
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(2).tmod = 0;
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(2).pmod = struct('name', {}, 'param', {}, 'poly', {});
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(2).orth = 1;
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(3).name = 'rest';
    %
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(3).onset = rest_onsets;
    %
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(3).duration = rest_duration;
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(3).tmod = 0;
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(3).pmod = struct('name', {}, 'param', {}, 'poly', {});
    matlabbatch{1}.spm.stats.fmri_spec.sess.cond(3).orth = 1;
    matlabbatch{1}.spm.stats.fmri_spec.sess.multi = {''};
    matlabbatch{1}.spm.stats.fmri_spec.sess.regress = struct('name', {}, 'val', {});
    matlabbatch{1}.spm.stats.fmri_spec.sess.multi_reg = {regressorfile};
    matlabbatch{1}.spm.stats.fmri_spec.sess.hpf = 128;
    matlabbatch{1}.spm.stats.fmri_spec.fact = struct('name', {}, 'levels', {});
    matlabbatch{1}.spm.stats.fmri_spec.bases.hrf.derivs = [0 0];
    matlabbatch{1}.spm.stats.fmri_spec.volt = 1;
    matlabbatch{1}.spm.stats.fmri_spec.global = 'None';
    matlabbatch{1}.spm.stats.fmri_spec.mthresh = 0.8;
    matlabbatch{1}.spm.stats.fmri_spec.mask = {''};
    matlabbatch{1}.spm.stats.fmri_spec.cvi = 'AR(1)';
    spm('defaults','FMRI');
    spm_get_defaults();
    defaults.delete=true;
    spm_jobman('initcfg');
    spm_jobman('run',matlabbatch);
    clear matlabbatch


    %% estimate model
     spm_mat_file=[Suboutdir,'/SPM.mat'];
    matlabbatch{1}.spm.stats.fmri_est.spmmat = {spm_mat_file};
    matlabbatch{1}.spm.stats.fmri_est.write_residuals = 0;
    matlabbatch{1}.spm.stats.fmri_est.method.Classical = 1;
    spm_jobman('run',matlabbatch);
    clear matlabbatch

    %% set up contrasts

    %interference > no interference
    matlabbatch{1}.spm.stats.con.spmmat = {spm_mat_file};
    matlabbatch{1}.spm.stats.con.consess{1}.tcon.name = 'interference > no interference';
    matlabbatch{1}.spm.stats.con.consess{1}.tcon.weights = [1 -1];
    matlabbatch{1}.spm.stats.con.consess{1}.tcon.sessrep = 'none';
    %ino interference > interference
    matlabbatch{1}.spm.stats.con.consess{2}.tcon.name = 'no interference > interference';
    matlabbatch{1}.spm.stats.con.consess{2}.tcon.weights = [-1 1];
    matlabbatch{1}.spm.stats.con.consess{2}.tcon.sessrep = 'none';
    matlabbatch{1}.spm.stats.con.delete = 1;
    spm('defaults','FMRI');
    spm_get_defaults();
    defaults.delete=true;
    spm_jobman('initcfg');
    spm_jobman('run',matlabbatch);
    clear matlabbatch




    %% compute model results

    %inialize spm
    spm('defaults','FMRI');
    %suppress graphics
    spm_get_defaults('cmdline',true);

    %contrast 1
    matlabbatch{1}.spm.stats.results.spmmat = {spm_mat_file};
    matlabbatch{1}.spm.stats.results.conspec.titlestr = '';
    matlabbatch{1}.spm.stats.results.conspec.contrasts = 1;
    matlabbatch{1}.spm.stats.results.conspec.threshdesc = 'none';
    matlabbatch{1}.spm.stats.results.conspec.thresh = 0.005;
    matlabbatch{1}.spm.stats.results.conspec.extent = 20;
    matlabbatch{1}.spm.stats.results.conspec.conjunction = 1;
    matlabbatch{1}.spm.stats.results.conspec.mask.none = 1;
    matlabbatch{1}.spm.stats.results.units = 1;
    matlabbatch{1}.spm.stats.results.export{1}.ps = true;
    matlabbatch{1}.spm.stats.results.export{2}.tspm.basename = 'interference-control_thresholded';
    spm_jobman('run',matlabbatch);
    clear matlabbatch

    %contrast 2
    matlabbatch{1}.spm.stats.results.spmmat = {spm_mat_file};
    matlabbatch{1}.spm.stats.results.conspec.titlestr = '';
    matlabbatch{1}.spm.stats.results.conspec.contrasts = 2;
    matlabbatch{1}.spm.stats.results.conspec.threshdesc = 'none';
    matlabbatch{1}.spm.stats.results.conspec.thresh = 0.005;
    matlabbatch{1}.spm.stats.results.conspec.extent = 20;
    matlabbatch{1}.spm.stats.results.conspec.conjunction = 1;
    matlabbatch{1}.spm.stats.results.conspec.mask.none = 1;
    matlabbatch{1}.spm.stats.results.units = 1;
    matlabbatch{1}.spm.stats.results.export{1}.ps = true;
    matlabbatch{1}.spm.stats.results.export{2}.tspm.basename = 'control-interference_thresholded';
    spm_jobman('run',matlabbatch);
    clear matlabbatch

    disp(['Finished: ' sub]);
end
end
