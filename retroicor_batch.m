function retroicor_batch(input_folder)
% retroicor_batch(input_folder)
%
% Batch RETROICOR runner (no GUI) that works inside a single folder containing:
%   - sub-*_bold.nii.gz
%   - matching .json (same basename)
%   - RETRO-resp_*.1D  (must exist to run)
%   - RETRO-qrs_*.1D   (optional; if missing, cardiac is skipped)
%
% Outputs are written into the same input_folder (next to inputs).

    if nargin < 1 || isempty(input_folder)
        input_folder = "/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/4_names_corrected";
    end

    input_folder = char(input_folder);

    % --- IMPORTANT: put your retroicor code folder here (no hard-coded paths elsewhere) ---
    % Update this to wherever retroicor_main_modi.m and friends live:
    retroicor_code_dir = "/autofs/cluster/vagabond/USERS/MARIO/Pipelines/9_tvns/retroicor";
    addpath(retroicor_code_dir);

    % Optional: avoid /tmp space issues if needed
    % tempdir('/autofs/.../tmp');

    fs = 40;                 % sampling freq for 1D physio
    OPTIONS = struct();
    OPTIONS.doCorr = 1;       % apply correction & save corrected image

    save_hist_plot = 0;
    save_phase_plot = 0;
    save_var_nii   = 0;

    % NEW: folder to save corrected BOLD files
    output_corrected_folder = '/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/5_corrected_bold';
    if ~exist(output_corrected_folder, 'dir')
        mkdir(output_corrected_folder);
    end

    % Find bolds
    bolds = dir(fullfile(input_folder, "*_bold.nii.gz"));
    if isempty(bolds)
        fprintf("No *_bold.nii.gz found in: %s\n", input_folder);
        return;
    end

    for k = 1:numel(bolds)

        funcname = bolds(k).name;                        % e.g. sub-..._bold.nii.gz
        [~, boldbase, ~] = fileparts(funcname);          % strips .gz -> leaves *.nii
        [~, boldbase, ~] = fileparts(boldbase);          % strips .nii -> leaves base without ext

        boldPath = fullfile(input_folder, funcname);
        jsonPath = fullfile(input_folder, [boldbase ".json"]);


        outbase  = fullfile(input_folder, boldbase);
        % save corrected bold into dedicated output folder
        outfunc  = fullfile(output_corrected_folder, [boldbase "_retro-corrected.nii.gz"]);

        if exist(outfunc, "file") == 2
            fprintf("[SKIP] already corrected: %s\n", funcname);
            continue
        end

        if exist(jsonPath, "file") ~= 2
            fprintf("[SKIP] missing JSON for: %s\n", funcname);
            continue
        end

        % --- Locate physio files (flexible matching) ---
        % Prefer "annotated_" if present, otherwise any match
        resp_candidates = dir(fullfile(input_folder, ["RETRO-resp_*" boldbase "*.1D"]));
        if isempty(resp_candidates)
            resp_candidates = dir(fullfile(input_folder, ["*resp*" boldbase "*.1D"]));
        end

        qrs_candidates = dir(fullfile(input_folder, ["RETRO-qrs_*" boldbase "*.1D"]));
        if isempty(qrs_candidates)
            qrs_candidates = dir(fullfile(input_folder, ["*qrs*" boldbase "*.1D"]));
        end

        if isempty(resp_candidates)
            fprintf("[SKIP] no RESP .1D for: %s\n", boldbase);
            continue
        end

        respfile = fullfile(resp_candidates(1).folder, resp_candidates(1).name);
        hasQRS = ~isempty(qrs_candidates);
        if hasQRS
            qrsfile = fullfile(qrs_candidates(1).folder, qrs_candidates(1).name);
        else
            qrsfile = "";
        end

        % --- Load physio ---
        % RESP (required)
        RESPretro = readtable(respfile, "FileType", "text", "ReadVariableNames", false);
        resp_struct = struct();
        resp_struct.wave = RESPretro.Var1;
        resp_struct.dt   = 1/fs;

        % QRS (optional)
        QRSretro_trig = [];
        if hasQRS && exist(qrsfile, "file") == 2
            QRStmp = readtable(qrsfile, "FileType", "text", "ReadVariableNames", false);
            QRSretro = QRStmp.Var1;

            if max(QRSretro) == 1
                QRSretro_trig = find(QRSretro == 1) / fs;
            elseif (1 / mean(diff(QRSretro))) > 20
                QRSretro_trig = QRSretro / fs;
            else
                QRSretro_trig = QRSretro;
            end
        end

        % --- Read JSON for slice timing + TR ---
        js = jsondecode(fileread(jsonPath));
        if ~isfield(js, "SliceTiming")
            fprintf("[SKIP] JSON missing SliceTiming: %s\n", jsonPath);
            continue
        end
        ST = js.SliceTiming;

        if isfield(js, "RepetitionTime")
            TR = js.RepetitionTime;
        else
            % fallback if missing
            TR = 1.19;
        end

        % Update physio flags + write JSON copy (same basename)
        js.CardiacPhysio = ~isempty(QRSretro_trig);
        js.RespPhysio    = ~isempty(resp_struct.wave);
        fid = fopen([outbase ".json"], "w");
        fprintf(fid, "%s", jsonencode(js));
        fclose(fid);

        % --- Load BOLD ---
        try
            image_matrix = niftiread(boldPath);
            info = niftiinfo(boldPath);
        catch ME
            fprintf("[SKIP] cannot read NIfTI: %s (%s)\n", boldPath, ME.message);
            continue
        end

        [~, ~, nslices, maxvol] = size(image_matrix);

        fprintf("Running RETROICOR: %s (RESP=%d, QRS=%d)\n", boldbase, 1, ~isempty(QRSretro_trig));

        % --- Run RETROICOR ---
        [image_matrix_corrected, PHASES, REGRESSORS, OTHER] = retroicor_main_modi( ...
            image_matrix, ST, TR, QRSretro_trig, resp_struct, OPTIONS);

        % --- Save corrected image ---
        if OPTIONS.doCorr == 1
            image_matrix_corrected = int16(image_matrix_corrected);
            niftiwrite(image_matrix_corrected, outfunc, info);
        end

        % --- Save regressors + pctvar ---
        save([outbase "_retro-regressors.mat"], "REGRESSORS");
        save([outbase "_retro-pctvar.mat"], "-struct", "OTHER", "PCT_VAR_REDUCED");

        % --- Phases output ---
        if ~isempty(resp_struct.wave) && ~isempty(QRSretro_trig)
            card_phases = zeros(maxvol, nslices);
            resp_phases = zeros(maxvol, nslices);
            for sl = 1:nslices
                phases = PHASES{sl};
                card_phases(:, sl) = phases(:, 1);
                resp_phases(:, sl) = phases(:, 2);
            end
            writetable(array2table(card_phases), [outbase "_retro-cardphases.txt"]);
            writetable(array2table(resp_phases), [outbase "_retro-respphases.txt"]);

        elseif ~isempty(resp_struct.wave)
            resp_phases = zeros(maxvol, nslices);
            for sl = 1:nslices
                phases = PHASES{sl};
                resp_phases(:, sl) = phases(:, 1);
            end
            writetable(array2table(resp_phases), [outbase "_retro-respphases.txt"]);

        elseif ~isempty(QRSretro_trig)
            card_phases = zeros(maxvol, nslices);
            for sl = 1:nslices
                phases = PHASES{sl};
                card_phases(:, sl) = phases(:, 1);
            end
            writetable(array2table(card_phases), [outbase "_retro-cardphases.txt"]);
        end

        % --- Optional extras (kept, but default off) ---
        if save_var_nii == 1
            var_info = info;
            var_info.PixelDimensions = info.PixelDimensions(1:3);
            var_info.ImageSize       = info.ImageSize(1:3);
            pctvar = int16(round(OTHER.PCT_VAR_REDUCED * 10000));
            niftiwrite(pctvar, [outbase "_retro-PctVarReduced.nii.gz"], var_info);
        end

        if save_hist_plot == 1
            [~, EDGES] = histcounts(squeeze(OTHER.PCT_VAR_REDUCED(:,:,:)));
            adj_edges = EDGES(1):((EDGES(end)-EDGES(1))/50):EDGES(end);
            hist_mat = zeros(numel(adj_edges)-1, nslices);
            for sl = 1:nslices
                Hb = histcounts(squeeze(OTHER.PCT_VAR_REDUCED(:,:,sl)), adj_edges);
                hist_mat(:, sl) = Hb;
            end
            xx = adj_edges(1:end-1) + diff(adj_edges);
            S = figure("visible", "off");
            surf(xx * 100, 1:nslices, hist_mat', "FaceAlpha", 0.7);
            xlabel("% Variance Change"); ylabel("Slices"); zlabel("# voxels");
            colorbar;
            saveas(S, [outbase "_retro-varchgsurf.png"]);
            close(S);
        end

        if save_phase_plot == 1
            if exist("resp_phases", "var")
                ph = figure("visible", "off");
                heatmap(resp_phases' + pi, "Colormap", parula);
                xlabel("Time (TRs)"); ylabel("Slices"); title("Respiratory Phase");
                ax = gca;
                ax.XDisplayLabels = repmat(' ', maxvol, 1);
                ax.YDisplayLabels = repmat(' ', nslices, 1);
                saveas(ph, [outbase "_retro-phases.png"]);
                close(ph);
            end
        end

        fprintf("[OK] finished: %s\n", boldbase);
    end
end
