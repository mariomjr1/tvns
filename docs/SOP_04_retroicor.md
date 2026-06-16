# SOP 04 — RETROICOR regressors

**Script:** `step04_retroicor_v2.sh`
**Scope:** per subject
**Runs on:** MATLAB (sources `utility/fmriprep_env.sh`)

---

## 1. Purpose
Build slice-timed RETROICOR respiratory (and, where R-DECO peaks exist, cardiac)
nuisance regressors and run RETROICOR. The regressors are used as GLM covariates
in Step 07 — see the design note in the [README](README.md).

## 2. Prerequisites
- Step 04 complete: `derivatives/physio/<subj>/preprocessed/*_filtered.mat`
  (and ideally `*_rdeco.mat`).
- BIDS BOLD + JSON in `sourcedata/<subj>/ses-01/func/` (JSON must contain
  `SliceTiming` and `RepetitionTime`).
- MATLAB with `preproc_generate_1D_v2.m` (in `matlab_code/`) and
  `retroicor_main_modi.m` (in `retroicor/`).

## 3. Inputs / parameters
```
step04_retroicor_v2.sh <bids_subject_id> \
    [sourcedata_dir] [preproc_dir] [retro_input_dir] [retro_output_dir] \
    [matlab_exe] [matlab_code_dir] [retro_code_dir] \
    [session] [sms_flag] [fs_out] [tr_fallback] [cardiac] [decision_file]
```
| Param | Meaning | Default |
|-------|---------|---------|
| `bids_subject_id` | BIDS label | — (required) |
| `session` | BIDS session | `01` |
| `sms_flag` | SMS / multiband (1/0) | `1` |
| `fs_out` | output physio rate (Hz) | `40` |
| `tr_fallback` | TR if JSON missing (s) | `1.19` |
| `cardiac` | global cardiac flag: 1 = cardiac+resp, 0 = respiration-only | `1` |
| `decision_file` | per-run piezo-QC manifest CSV (overrides `cardiac` per run) | auto: `<preproc>/<subj>_cardiac_decision.csv` if present |

## 4. Run
```bash
bash step04_retroicor_v2.sh sub-7T1019HC042726
```

## 5. What it does
- **Part 1 — generate 1D files** (`preproc_generate_1D_v2.m`): reads RESP + MRTRIG
  from the physio struct, incorporates R-DECO R-peaks if present, reads TR from
  the BIDS JSON, and writes `RETRO-resp_*.1D` (+ `RETRO-qrs_*.1D` if R-DECO) into
  the input folder.
- **Part 2 — assemble** BIDS BOLD `.nii.gz` + `.json` into the input folder
  alongside the 1D files (`retroicor_batch.m` needs them co-located).
- **Part 3 — run RETROICOR** (`retroicor_batch.m`): produces corrected BOLDs,
  regressors, and phase files.

## 6. Outputs
```
derivatives/physio/<subj>/retroicor/
  input/   *_bold.nii.gz, *.json, RETRO-resp_*.1D, RETRO-qrs_*.1D
  output/  *_retro-corrected.nii.gz
           *_retro-regressors.mat   (→ Step 06 first-level inputs)
           *_retro-pctvar.mat       (% variance explained)
```

## 7. Piezo cardiac quality — per-sequence review
A messy piezo trace yields unreliable R-peaks; using its cardiac RETROICOR
regressors injects structured noise rather than removing it. The pipeline policy
is to **skip cardiac and run respiration-only** for those runs.

In the GUI's **Step 04 → "Piezo QC Review"** tab:
1. **Run / Refresh Cardiac QC** — generates a per-run QC image + verdict
   (`cardiac_qc.m`; GOOD / SUSPECT / BAD).
2. **Load review** — shows each sequence's piezo trace + R-peaks with a per-run
   choice (**Use cardiac** / **Respiration only**), pre-selected from the verdict.
3. **Save decisions** — writes `<preproc>/<subj>_cardiac_decision.csv`. Part 1
   (and Run All) auto-apply it per run; runs not listed fall back to the global
   cardiac choice.
4. **Cohort report** — `qc_snapshots.py --piezo-report` rolls every subject's
   verdicts + decisions into `codes/qc/group_piezo_qc.csv` and `.md`, flagging all
   BAD/SUSPECT or respiration-only runs.

A run routed to respiration-only logs `[PIEZO-SKIP]` and simply omits its
`RETRO-qrs_*.1D`, so `retroicor_batch.m` corrects it with respiration only.

## 8. QC / verification
- Confirm `n_filtered` and (if used) `n_rdeco` counts match the number of runs.
- Inspect `*_retro-pctvar.mat` for the % variance the regressors remove.
- **Provenance (Task 29):** the RETROICOR source (`generate_1D_fun_1.m`,
  `retroicor_main_modi.m`) and R-DECO (`R_DECO.m`) versions/hashes are recorded by
  `utility/collect_provenance.py` (QC panel → "Capture provenance"). Run it once per
  batch so the exact RETROICOR/R-DECO code is tied to the outputs.
- Optionally re-run the Step 02 pre/post GIF against `*_retro-corrected.nii.gz`
  to visualise physiological-noise removal.
- **Flag + log + continue:** `preproc_generate_1D_v2.m` skips any sequence that
  fails (e.g. flat physio, no MR triggers), flags it in a closing warning that
  lists the skipped runs, and proceeds with the rest — it does **not** abort. Review
  the skipped list before trusting the corrected output. (Part 1 only aborts on a
  genuine MATLAB crash.)

> The `*_retro-regressors.mat` are the GLM covariates; the corrected NIfTIs are
> for visualisation. Step 07 models the **fMRIPrep T1w BOLD** with these
> regressors (it does **not** use the RETROICOR-corrected image as input).

## 9. Troubleshooting
| Symptom | Cause / fix |
|---------|-------------|
| `No *_filtered.mat files found` | Run Step 03 first. |
| `0 R-DECO file(s)` | R-DECO not run — proceeds respiration-only (no cardiac regressors). Run R-DECO in Step 03 to add them. |
| `N of M sequence(s) SKIPPED` (generate_1D) | Those runs failed (flat physio / no MR triggers) and were skipped; the rest continue. Fix and re-run if needed. |
| `generate_1D_v2 crashed` | Genuine MATLAB error (license/paths/syntax), not a per-sequence skip — check the MATLAB log. |
| TR fallback warning | JSON missing `RepetitionTime` — verify sidecars or pass correct `tr_fallback`. |

## 10. Next step
[SOP 05 — fMRIPrep](SOP_05_fmriprep.md)
