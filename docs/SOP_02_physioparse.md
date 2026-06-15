# SOP 02 — Physioparse (parse LabChart physio into per-run segments)

**Script:** `step02_physioparse_v2.sh`
**Scope:** per subject
**Runs on:** the cluster (Python; sources `utility/fmriprep_env.sh`)

---

## 1. Purpose
Anchor the continuous LabChart `.mat` recording to the first MR trigger
("pseudotime"), then cut it into one `.mat` per BOLD run, and compute physio QC
metrics. This is the entry point of the physiological branch (03 → 04 → 05).

## 2. Prerequisites
- Step 01 complete: BIDS `func/*_bold.json` sidecars exist (provide
  `AcquisitionTime`) and `.heudiconv/.../dicominfo_ses-01.tsv` exists (provides
  `dim4 × TR` → segment durations).
- The session's LabChart export `.mat` file (the **only** input you supply
  manually). Classic (`data`/`datastart`/`dataend`) and Block1 (`data_block1`)
  formats are supported — this CLI wrapper assumes Classic; use the GUI for Block1.

## 3. Inputs
```
step02_physioparse_v2.sh <mat_file> <bids_subject_id> \
    [sourcedata_dir] [physioparse_dir] [python_exe]
```
| Arg | Meaning | Default |
|-----|---------|---------|
| `mat_file` | full path to the session `.mat` | — (required) |
| `bids_subject_id` | BIDS label, with `sub-` | — (required) |
| `sourcedata_dir` | BIDS root | `<project>/sourcedata` |
| `physioparse_dir` | physioparse code | `utility/physioparse` |
| `python_exe` | Python | `python3` |

## 4. Run
```bash
bash step02_physioparse_v2.sh /path/to/7T1019HC042726.mat sub-7T1019HC042726
```

## 5. What it does
Creates a working dir `derivatives/physio/<subj>/`, symlinks the `.mat` (it can be
multi-GB), copies the JSON sidecars + `dicominfo_ses-01.tsv`, then runs:
- **Step 1 — pseudotime mapping** → `pseudotime_mapping.json`
- **Step 2 — quality viz** → `pseudotime_plot.png`, `pseudotime_plot_stats.png`
- **Step 3 — parse segments** → `parsed/task-*_run-*.mat` + `parsed/plots/`
- **Step 4 — signal QC** → `qc/physio_qc_plot.png`, `qc/physio_qc_metrics.csv`

## 6. Outputs
```
derivatives/physio/<subj>/
  pseudotime_mapping.json
  pseudotime_plot.png  pseudotime_plot_stats.png
  parsed/task-*_run-*.mat   parsed/plots/
  qc/physio_qc_plot.png     qc/physio_qc_metrics.csv
```

## 7. QC / verification
- **Open `pseudotime_plot.png`** and confirm every BOLD sequence aligns to the
  correct recording segment (the most important manual check of this step).
- Review `qc/physio_qc_metrics.csv` for plausible heart-rate, respiration-rate,
  SNR, and trigger jitter per run.

## 8. Troubleshooting
| Symptom | Cause / fix |
|---------|-------------|
| `BIDS func directory not found` | Run Step 01 for this subject first. |
| `dicominfo.tsv not found` warning | Step 3 falls back to 120 s per sequence — re-run Step 01 Pass 1 (`-c none`) to regenerate it for correct durations. |
| Misaligned segments in the plot | Wrong/ corrupt trigger channel or wrong `.mat` format — verify the recording; for Block1 exports use the GUI. |

## 9. Next step
[SOP 03 — Preprocess + R-DECO](SOP_03_preprocess_rdeco.md)
