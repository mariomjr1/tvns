import os


def create_key(template, outtype=('nii.gz',), annotation_classes=None):
    if template is None or not template:
        raise ValueError('Template must be a valid format string')
    return template, outtype, annotation_classes


def infotodict(seqinfo):
    """Heuristic evaluator for determining which runs belong where

    allowed template fields - follow python string module:

    item: index within category
    subject: participant id
    seqitem: run number during scanning
    subindex: sub index within group
    """

    # Section 1: These key definitions should be revised by the user
    ###################################################################
    # For each sequence, define a key variables (e.g., t1w, dwi etc) and template using the create_key function:
    # key = create_key(output_directory_path_and_name).
    # TIPS
    # If there are sessions, then session must be subfolder name. 
    # Do not prepend the ses key to the session! It will be prepended automatically for the subfolder and the filename.
    # The final value in the filename should be the modality.  It does not have a key, just a value.
    # Otherwise, there is a key for every value. 
    # Filenames always start with subject, optionally followed by session, and end with modality.
    
    # The "data" key creates sequential numbers which can be for naming sequences.
    # This is especially valuable if you run the same sequence multiple times at the scanner.
    data = create_key('run-{item:02d}')
    
    t1w = create_key('sub-{subject}/{session}/anat/sub-{subject}_{session}_T1w')

    t2w = create_key('sub-{subject}/{session}/anat/sub-{subject}_{session}_T2w')
    

    # Even if this is resting state, you still need a task key

    func_rest = create_key('sub-{subject}/{session}/func/sub-{subject}_{session}_task-rest_run-{item:02d}_bold')

    func_rest_AP = create_key('sub-{subject}/{session}/func/sub-{subject}_{session}_task-AP_run-{item:02d}_bold')
    
    func_rest_PA = create_key('sub-{subject}/{session}/func/sub-{subject}_{session}_task-PA_run-{item:02d}_bold')
    
    func_task_block_stim = create_key('sub-{subject}/{session}/func/sub-{subject}_{session}_task-BlockStim_run-{item:02d}_bold')

    func_task_continuous_stim = create_key('sub-{subject}/{session}/func/sub-{subject}_{session}_task-ContinuousStim_run-{item:02d}_bold')

    func_task_BEAT = create_key('sub-{subject}/{session}/func/sub-{subject}_{session}_task-BEAT_run-{item:02d}_bold')

    func_task_free_breath = create_key('sub-{subject}/{session}/func/sub-{subject}_{session}_task-FreeBreath_run-{item:02d}_bold')

    func_task_pace_breath = create_key('sub-{subject}/{session}/func/sub-{subject}_{session}_task-PaceBreath_run-{item:02d}_bold')

    # Section 1b: This data dictionary (below) should be revised by the user.
    ########################################################################### 
    # info is a Python dictionary containing the following keys from the infotodict defined above.
    # This list should contain all and only the sequences you want to export from the dicom directory.
    info = {t1w: [], t2w: [], func_rest: [], func_rest_AP: [], func_rest_PA: [], func_task_block_stim: [], func_task_continuous_stim: [], func_task_BEAT: [], func_task_free_breath: [], func_task_pace_breath: []}

    # The following line does no harm, but it is not part of the dictionary.
    last_run = len(seqinfo)

    # Section 2: These criteria should be revised by the user.
    ##########################################################
    # Define test criteria to check that each DICOM sequence is correct
    # seqinfo (s) refers to information in dicominfo.tsv. Consult that file for
    # available criteria.
    # Each sequence to export must have been defined in Section 1 and included in Section 1b.
    # The following illustrates the use of multiple criteria:
    for idx, s in enumerate(seqinfo):

        # Dimension 3 must equal x and the string 'y' must appear somewhere in the protocol_name
        if (s.dim3 == 176) and ('MEMP_4e_p3_hiBW_TR3500_TI1300 RMS' == s.series_description):
            info[t1w].append(s.series_id)

	#t2w
        if (s.dim3 == 114) and ('t2_tse_tra_0p7x1_p3_TE204' in s.series_description):
            info[t2w].append(s.series_id)
        
        
        # fMRI
        if (s.dim3 == 92) and ('REST_ep2d_bold' in s.series_description):
            info[func_rest].append(s.series_id)

        if (s.dim3 == 92) and ('TOPUP_AP' in s.series_description):
            info[func_rest_AP].append(s.series_id)

        if (s.dim3 == 92) and ('TOPUP_PA' in s.series_description):
            info[func_rest_PA].append(s.series_id) 

        if (s.dim3 == 92) and ('BlockStim' in s.series_description):
            info[func_task_block_stim].append(s.series_id) 

        if (s.dim3 == 92) and ('ContinuousStim' in s.series_description):
            info[func_task_continuous_stim].append(s.series_id)

        if (s.dim3 == 20) and ('BEAT' in s.series_description):
            info[func_task_BEAT].append(s.series_id)

        if (s.dim3 == 250) and ('FreeBreathe' in s.series_description):
            info[func_task_free_breath].append(s.series_id)

        if (s.dim3 == 250) and ('PaceBreathe' in s.series_description):
            info[func_task_pace_breath].append(s.series_id)

    for s in seqinfo:
        """
        The namedtuple `s` contains the following fields:

        * total_files_till_now
        * example_dcm_file
        * series_id
        * dcm_dir_name
        * unspecified2
        * unspecified3
        * dim1
        * dim2
        * dim3
        * dim4
        * TR
        * TE
        * protocol_name
        * is_motion_corrected
        * is_derived
        * patient_id
        * study_description
        * referring_physician_name
        * series_description
        * image_type
        """
    # Section 3: Optional Report
    ###################################
    # Populate the msg list IF the wrong number of files is created (!= means not equal) 
    # and exit the program with an error (No BIDS files are generated)
    msg = []

    if len(info[t1w]) != 1: msg.append('WARNING: Missing correct number of T1w runs')
    if len(info[t2w]) != 1: msg.append('WARNING: Missing correct number of T2w runs')
    if len(info[func_rest]) != 1: msg.append('WARNING: Missing correct number of func_rest runs')
    if len(info[func_rest_AP]) != 1: msg.append('WARNING: Missing correct number of func_rest_AP runs')
    if len(info[func_rest_PA]) != 1: msg.append('WARNING: Missing correct number of func_rest_PA runs')
    if len(info[func_task_block_stim]) != 1: msg.append('WARNING: Missing correct number of func_task_block_stim runs')
    if len(info[func_task_continuous_stim]) != 1: msg.append('WARNING: Missing correct number of func_task_continuous_stim runs')
    if len(info[func_task_BEAT]) != 1: msg.append('WARNING: Missing correct number of func_task_BEAT runs')
    if len(info[func_task_free_breath]) != 1: msg.append('WARNING: Missing correct number of func_task_free_breath runs')
    if len(info[func_task_pace_breath]) != 1: msg.append('WARNING: Missing correct number of func_task_pace_breath runs')

    # If there is an error, a message will be generated and no NIfTI files will be generated for the subject.
    if msg:
       print('\n'.join(msg))


    return info
