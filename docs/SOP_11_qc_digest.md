# SOP 11 — Unified QC digest (final check)

**Script:** `utility/qc_digest.py`
**Scope:** per study (all subjects)
**Runs on:** Python (stdlib only — no nibabel/MATLAB)

---

## 1. Purpose
Roll up **every step's flags** plus **fMRIPrep motion** (mean framewise displacement and the
% of high-motion volumes) into **one per-subject table** so you can see, at a glance, *which
subjects and which steps are good and which need a look*. It is a **read-only** roll-up: it
changes nothing and never fails — a check whose source hasn't been produced yet just shows
`NA`. It is the recommended **final check before trusting any result**, and it regenerates
**in real time** whenever you run it.

> **Policy:** a `FLAG` (or `REVIEW`) means **review**, not **exclude**. This pipeline never
> auto-drops a subject ([flag-and-log](_start_here.md#i-the-flag-and-log-rule)); the digest
> just surfaces what to look at.

## GUI (in the app)
**QC panel → "Unified QC digest" → ↻ Generate / Refresh QC digest.** It runs the
aggregator and loads the table live, with `REVIEW` rows highlighted. Re-click any time
(e.g. after re-running a step) to refresh. The label shows `N subject(s) · M to review`.

## 2. Prerequisites
None hard — it reads whatever exists. Each check is populated by the step that produces it
(otherwise the cell is `NA`):

| Check column | Populated by | Source file |
|---|---|---|
| `mean_fd`, `pct_fd_gt_*`, `motion` | fMRIPrep (step 05) | `derivatives/fmriprep/<subj>/**/*_desc-confounds_timeseries.tsv` |
| `mni_bold` | step 05 FD summary | `derivatives/fmriprep/qc_fd_summary.json` |
| `sdc` | **SDC audit** (QC panel) | `codes/qc/group_sdc_audit.csv` |
| `cardinality` | **Cardinality audit** (QC panel) | `codes/qc/group_cardinality_audit.csv` |
| `piezo` | **Cohort report (piezo)** (step 04 / QC) | `codes/qc/group_piezo_qc.csv` |
| `contrast` | step 08 Part 1 | `derivatives/spm/**/_contrast_check.csv` |
| `roi_geom` | step 10 | `derivatives/spm/**/_roi_geometry_check.csv` |

Run those audits first to fill the table; until then those cells read `NA`.

## 3. Inputs / parameters
```
python utility/qc_digest.py --sourcedata <BIDS> \
    [--derivatives D] [--qc-dir Q] [--output CSV] [--session 01] \
    [--fd-thresh 0.5] [--fd-mean-thresh 0.9]
```
| Param | Meaning | Default |
|-------|---------|---------|
| `--sourcedata` | BIDS root | — (required) |
| `--derivatives` | derivatives dir | `<sourcedata>/derivatives` |
| `--qc-dir` | where the digest is written + where the audit CSVs live | `<project>/codes/qc` |
| `--fd-thresh` | per-volume FD (mm) above which a volume is "high-motion" (→ `pct_fd_gt_*`) | `0.5` |
| `--fd-mean-thresh` | mean FD (mm) above which a subject is flagged `HIGH-FD` | `0.9` |

## 4. Run
```bash
python utility/qc_digest.py --sourcedata /path/to/sourcedata
# custom motion thresholds:
python utility/qc_digest.py --sourcedata /path/to/sourcedata --fd-thresh 0.3 --fd-mean-thresh 0.5
```

## 5. Outputs
```
codes/qc/qc_digest.csv   one row per subject + per-check status + overall verdict
codes/qc/qc_digest.md    legend + the list of subjects to review
```
Columns: `subject, n_fd_vols, mean_fd, pct_fd_gt_<thr>, motion, mni_bold, sdc,
cardinality, piezo, contrast, roi_geom, n_flags, status`.

## 6. What each flag means (good vs bad)
Every check cell is **`OK`** / **`FLAG…`** / **`NA`** (source not produced yet).

| Check | **OK** (good) means | **FLAG** (review) means |
|---|---|---|
| `motion` | mean FD ≤ `--fd-mean-thresh` | `HIGH-FD` — lots of head motion. **Still kept**; high-motion volumes are censored by FD-spike regressors (step 06). |
| `mni_bold` | fMRIPrep produced an MNI BOLD | `no-MNI-BOLD` — registration/normalisation likely failed; inspect the fMRIPrep HTML report. |
| `sdc` | distortion correction was applied | fieldmap missing or SDC not applied (`FMAP_BUT_NO_SDC` / `NO_FIELDMAP`); check `fmap/` + `IntendedFor`. |
| `cardinality` | volume counts match across all stages | a stage lost/added volumes — open the named step's output. |
| `piezo` | cardiac RETROICOR was used | piezo too messy → `respiration-only` (or `BAD`/`SUSPECT`). Expected for messy traces; documented, not an error. |
| `contrast` | the contrast is `Stim > baseline` | `MISMATCH` — a different contrast was entered at step 08 (e.g. a reordered pmod GLM). |
| `roi_geom` | the ROI grid/affine matched | image was `RESAMPLED` onto the reference grid, or unreadable (`ERROR`). |

**Reading "good subjects / good steps":**
- **`status = OK`** → all checks green (or `NA`): a clean subject — nothing to look at.
- **`status = REVIEW`** with `n_flags = k` → **k** checks flagged. Open `qc_digest.md` (or the
  named source file) and decide per subject. A single `HIGH-FD` or `respiration-only` flag is
  usually fine to keep (by design); `no-MNI-BOLD`, a cardinality mismatch, or a `contrast`
  `MISMATCH` are the ones that more often warrant fixing/re-running before group stats.
- A **column** that is mostly `FLAG` across subjects points at a *step* problem (e.g. SDC
  never applied → fieldmaps/`IntendedFor` misconfigured), not a per-subject one.

## 7. Troubleshooting
| Symptom | Cause / fix |
|---|---|
| A check is `NA` for everyone | That audit hasn't been run — run it (QC panel) and refresh. |
| `mean_fd` empty | No fMRIPrep confounds found for that subject — run step 05. |
| Everything `REVIEW` | Check `--fd-mean-thresh` isn't too low; review the dominant flagged column. |
| GUI table empty | Click **↻ Generate / Refresh QC digest** (or set `sourcedata` in Setup). |

## 8. Next step
This is the final QC gate. Review every `REVIEW` subject, then proceed to your statistics /
figures. See [`_start_here.md`](_start_here.md) §H for where this fits in the workflow.
