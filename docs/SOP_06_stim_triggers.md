# SOP 06 — Stimulus triggers + first-level assembly

**Script:** `step06_stim_v2.sh`
**Scope:** per subject
**Runs on:** the cluster (Python; sources `utility/fmriprep_env.sh`)

---

## 1. Purpose
Extract stimulus onset/offset times from the STIMTRIG channel of each parsed
physio segment, and assemble the first-level inputs (stim onsets + motion
regressors; plus legacy T1w-BOLD and RETROICOR-regressor folders) into one shared
`first_level/` folder for Step 07. **The default Step-07 GLM models the fMRIPrep
MNI BOLD with motion regressors only** (RETROICOR was already applied to the image
upstream); the T1w-BOLD / RETROICOR-regressor folders feed only the optional legacy
T1w route.

## GUI (in the app)
Open the **Step 06 — Stim Triggers** panel. Pick subject(s), set **threshold** /
**debounce** and the **QC** option → **Run** (`step06_stim_v2.sh`). Produces
`*_bold_stim.txt` (onsets) and `*_motion_regressors.txt` (6 rigid params + one FD spike per
high-motion volume — the minimal model), assembled into the shared `first_level/` folder.
Review the QC plots: detected onsets must land on the STIMTRIG edges.

## 2. Prerequisites
- Step 03 complete: `derivatives/physio/<subj>/parsed/task-*_run-*.mat`.
- Step 02 complete: fMRIPrep confounds / motion regressors and
  `*_space-T1w_desc-preproc_bold.nii.gz`.
- Step 05 complete (for `*_retro-regressors.mat`).
- `utility/extract_stim_onsets.py`.

## 3. Inputs / parameters
```
step06_stim_v2.sh <bids_subject_id> \
    [sourcedata_dir] [parsed_dir] [stim_dir] [fmriprep_dir] [firstlevel_dir] \
    [session] [threshold] [debounce] [python_exe] [qc] [no_firstlevel] [retroicor_output]
```
| Param | Meaning | Default |
|-------|---------|---------|
| `bids_subject_id` | BIDS label | — (required) |
| `threshold` | STIMTRIG step threshold | `1.5` |
| `debounce` | min seconds between events | `1.5` |
| `qc` | 1 = save QC plots | `0` |
| `no_firstlevel` | 1 = skip Part 3 assembly | `0` |

> **Onset robustness + sanity checks (Task 11).** `extract_stim_onsets.py` now pairs each onset
> with the offset that occurs **before the next onset** (so a missed/late offset can't create a
> spurious duration spanning to the next block) and prints diagnostics per run:
> `MR triggers, onsets, offsets, paired, dropped, recording_s`. The **MR-trigger count** ≈ the
> number of acquired volumes — compare it to the fMRIPrep BOLD `#volumes`; a mismatch means the
> onsets (timed from the first MR trigger) are shifted vs the modeled BOLD (e.g. dropped dummy
> scans). Run the extractor directly with `--expected-events N` (paradigm count) and/or
> `--n-volumes N` to turn those into explicit warnings. Always review the `--qc` plots:
> onsets must land on the STIMTRIG edges.

## 4. Run
```bash
bash step06_stim_v2.sh sub-7T1019HC042726
# with QC plots:
bash step06_stim_v2.sh sub-7T1019HC042726 "" "" "" "" "" 01 1.5 1.5 python3 1
```

## 5. What it does
- **Part 1** — `extract_stim_onsets.py` on each parsed mat → `*_bold_stim.txt`
  (rest runs get a header-only file).
- **Part 2** — copies stim `.txt` into `derivatives/fmriprep/<subj>/ses-01/func/`.
- **Part 3** (optional) — assembles the **shared** first-level folder
  (named per-subject so all subjects coexist):
  - `01_stim_onsets/` ← stim `.txt`
  - `02_motion_regressors/` ← `*_motion_regressors.txt` generated from the fMRIPrep
    confounds. **Minimal model by design (Task 19): 6 rigid-body params + one FD
    spike regressor per high-motion volume — no derivatives, no aCompCor, no
    non-steady-state regressors** (deliberately not overspecified for this pilot;
    one row per BOLD volume). `--no-spikes` writes rigid-only.
  - `03_retroicor_regressors/` ← `*_retro-regressors.mat`
  - `04_bolds/` ← `*_space-T1w_desc-preproc_bold.nii.gz`
- **Part 4** — optional QC plots (STIMTRIG + detected onsets) with `--qc`.

## 6. Outputs
```
derivatives/physio/<subj>/stimtrigger/*_bold_stim.txt  (+ qc/ if --qc)
derivatives/physio/first_level/
  01_stim_onsets/  02_motion_regressors/  03_retroicor_regressors/  04_bolds/
```

## 7. QC / verification
- Open the Part 4 QC plots: detected onsets should land on STIMTRIG edges.
- Confirm Part 3 counts: stim, motion, retroicor, and BOLD file counts are
  consistent across subjects (BOLD count = 0 means fMRIPrep wasn't run).

## 8. Troubleshooting
| Symptom | Cause / fix |
|---------|-------------|
| Missed / extra onsets | Tune `threshold` and `debounce` and re-run. |
| `BOLD files: 0` | Run Step 02 (fMRIPrep) first. |
| Motion regressors: 0 | fMRIPrep `*_motion_regressors.txt` not generated/located — check subject root and func dir. |
| fMRIPrep func dir not found (Part 2) | fMRIPrep not run for this subject — non-fatal warning. |

## 9. Next step
[SOP 07 — First-level + MNI](SOP_07_firstlevel_mni.md)
