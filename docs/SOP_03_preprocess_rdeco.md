# SOP 03 — Preprocess physio + R-DECO cardiac annotation

**Script:** `step03_preprocess_for_retroicor_v2.sh`
**Scope:** per subject
**Runs on:** MATLAB (filtering); R-DECO GUI or automated (peak detection)

---

## 1. Purpose
Filter the cardiac (RPIEZO) channel of each per-run physio segment to prepare it
for R-peak detection, then **detect R-peaks with R-DECO**. The script does the
filtering and **stops before R-DECO** — R-peak detection is a separate manual or
automated action that you perform afterward.

## GUI (in the app)
Open the **Step 03 — Preprocess + R-DECO** panel.
1. **Filter** tab: pick the subject → **Run filtering** (writes `*_filtered.mat` +
   `*_rpiezo.mat`). A flat/garbage RESP errors *for that run only*; a flat RPIEZO warns
   (route it respiration-only in Step 04); both are flagged and the remaining runs continue.
2. **R-DECO** tab: **Run automated** R-peak detection (or open the R-DECO GUI to correct
   beats by hand) → saves `*_rdeco.mat` per run.
Confirm one `*_rdeco.mat` per run (Step 04 can still run respiration-only without it).

## 2. Prerequisites
- Step 03 complete: `derivatives/physio/<subj>/parsed/task-*_run-*.mat` exist.
- MATLAB on PATH with `utility/matlab_code/preproc_filter_per_sequence.m`.
- R-DECO available at `utility/r-deco-master/` (GUI `R_DECO.m`), or the automated
  analysis script for headless peak detection.

## 3. Inputs / parameters
```
step03_preprocess_for_retroicor_v2.sh <bids_subject_id> \
    [parsed_dir] [output_dir] [sourcedata_dir] [matlab_exe] \
    [matlab_code_dir] [sr] [hp_cutoff] [bp_low] [bp_high]
```
| Param | Meaning | Default |
|-------|---------|---------|
| `bids_subject_id` | BIDS label | — (required) |
| `parsed_dir` | Step 03 `parsed/` | `derivatives/physio/<subj>/parsed` |
| `output_dir` | preprocessed mats | `derivatives/physio/<subj>/preprocessed` |
| `sr` | physio sampling rate (Hz) | `1000` |
| `hp_cutoff` | highpass cutoff (Hz) | `0.05` |
| `bp_low` / `bp_high` | bandpass edges (Hz) | `0.5` / `2.0` |

## 4. Run (filtering)
```bash
bash step03_preprocess_for_retroicor_v2.sh sub-7T1019HC042726
```
Per `task-*_run-*.mat` it writes:
- `<subj>_task-*_run-*_filtered.mat` — physio struct for Step 05
- `<subj>_task-*_run-*_rpiezo.mat` — plain RPIEZO array for R-DECO

## 5. R-DECO R-peak detection (do this next, per `*_rpiezo.mat`)
**Manual (GUI):**
```matlab
addpath('utility/r-deco-master'); R_DECO
% Load <subj>_task-*_run-*_rpiezo.mat → auto-detect → correct peaks manually
% Save output as <subj>_task-*_run-*_rdeco.mat in the SAME output_dir
```
**Automated:** run the R-DECO auto-analysis (`rdeco_auto_analysis.m`) — peak
detection at 300/500 ms envelopes, ectopic removal, doubled-beat removal
>150 bpm, plus a QC image → `*_rdeco.mat`.

## 6. Outputs
```
derivatives/physio/<subj>/preprocessed/
  <subj>_task-*_run-*_filtered.mat   (→ Step 05)
  <subj>_task-*_run-*_rpiezo.mat     (→ R-DECO input)
  <subj>_task-*_run-*_rdeco.mat      (← R-DECO output; cardiac regressors)
```

## 7. QC / verification
- Inspect the R-DECO QC image / GUI overlay — every true R-peak marked once, no
  doubled beats, no ectopics misfired.
- Confirm one `*_rdeco.mat` exists per run before Step 04 (Step 04 can run
  respiration-only without it, but cardiac regressors will be omitted).
- **Channel validation (flag + log + continue):** filtering validates channel
  *content*, not just presence — a flat (zero-variance) `RESP` errors **for that
  sequence**, a flat `RPIEZO` warns (use respiration-only), and `MRTRIG` with no
  rising edges warns. `RESP` is mean-centered (DC offset removed) before regressor
  generation. A failed sequence is **skipped and flagged** (its bad physio is not
  written); the step keeps going with the runs that succeeded and lists the skipped
  ones in a closing warning — review that list before trusting the output.

## 8. Troubleshooting
| Symptom | Cause / fix |
|---------|-------------|
| `No task-*_run-*.mat files found` | Run Step 02 (physioparse) first. |
| `RESP channel is empty or flat` | The respiratory channel was not recorded / saturated — re-check the LabChart export; this run cannot produce respiratory regressors. |
| `WARNING: RPIEZO is flat` | Piezo not recorded — route this run to respiration-only in the Step 04 Piezo QC Review. |
| `N of M sequence(s) SKIPPED` | Those segments failed and were skipped (not fatal); fix them and re-run if you need them, otherwise the rest proceed. |
| MATLAB hangs on cluster | Figures are suppressed (`DefaultFigureVisible off`); use the automated R-DECO path for headless runs. |
| Poor peak detection | Adjust bandpass (`bp_low`/`bp_high`) for the piezo signal, re-filter, re-run R-DECO. |

## 9. Next step
[SOP 04 — RETROICOR](SOP_04_retroicor.md)
