# TVNS fMRI Pipeline

A complete, GUI-driven pipeline for preprocessing and analysing **transcutaneous
Vagus Nerve Stimulation (tVNS) task-fMRI** data acquired at 7 T. It takes a study
from raw DICOMs on the scanner archive all the way to second-level group
statistical maps in MNI space, with explicit handling of the physiological
(respiration + cardiac) recordings that are essential for denoising brainstem
signal.

---

## What this pipeline does

This pipeline converts raw 7 T MRI sessions into group-level activation maps for a
tVNS study that contains three functional conditions — **BlockStim**,
**ContinuousStim**, and **rest**. Starting from DICOMs pulled off the cluster
archive, it organises the data into BIDS, runs fMRIPrep for anatomical/functional
preprocessing and spatial normalisation, then parses the simultaneously-recorded
physiological signals (LabChart `.mat` exports) into per-sequence segments using a
trigger-anchored "pseudotime" mapping. From those segments it builds RETROICOR
respiratory and cardiac regressors (the cardiac component optionally refined with
R-DECO R-peak annotation) and applies physiological noise correction to the BOLD
runs — a step that matters disproportionately for the deep brainstem nuclei (NTS,
DMV) targeted by vagal stimulation. It extracts stimulus onset/offset timing from
the stimulus-trigger channel, runs a first-level SPM GLM per subject and task in
native T1w space, warps the resulting contrasts to MNI, and finally runs
second-level one-sample t-tests for each condition plus a combined Block+Continuous
analysis. Every stage is exposed through a single tkinter dashboard with a
persistent per-subject pipeline-state tracker, a live console, optional QC at each
step, and editable configuration so that nothing is hard-coded to one machine.

---

## The GUI

Launch the dashboard with:

```bash
bash gui/run.sh
# or directly with the conda env that has tkinter:
~/anaconda3/envs/Neuroimaging/bin/python3 gui/app.py
```

The window is a **left navigation column** (click a step to open it) plus a
**resizable content area** and a **dockable console**. A **Pipeline State sidebar**
on the far left shows, per subject, which steps have completed/failed (backed by
`pipeline_state.json`). All three regions — sidebar, step panel, and console — are
on draggable splitters, so you can size them to your screen.

| Nav entry | Backed by | Purpose |
|-----------|-----------|---------|
| **Setup** | — | Configure shared paths (raw data, sourcedata, heuristic, env) and edit the subject list |
| **00 Download DICOMs** | `step00_unpack_V2.sh` | `findsession` + `rsync` raw DICOMs from the archive |
| **01 BIDS Conversion** | `step01_create_bids_v2.sh` | heudiconv two-pass conversion; sequence viewer; **BIDS validator** |
| **02 fMRIPrep** | `step02_fmriprep_v2.sh` | Generate BIDS subject list; run fMRIPrep locally; optional pre/post QC GIF |
| **03 Physioparse** | `step03_physioparse_v2.sh` | Pseudotime mapping → quality viz → parse segments → optional signal QC (Classic / Block1 formats) |
| **04 Preprocess + RDECO** | `step04_preprocess_for_retroicor_v2.sh` | Filter cardiac signal per sequence; launch R-DECO for R-peak annotation |
| **05 RETROICOR** | `step05_retroicor_v2.sh` | Generate 1D regressors (with R-DECO peaks) and apply RETROICOR correction |
| **06 Stim Triggers** | `step06_stim_v2.sh` | Extract stimulus onsets from STIMTRIG; copy to fMRIPrep; first-level prep; optional QC |
| **07 First-level + MNI** | `step07_firstlevel_mni_v2.sh` | SPM GLM (masks located in place, not copied) + warp contrasts to MNI |
| **08 Second-level** | `step08_secondlevel_v2.sh` | Group one-sample t-tests per task + combined Block+Continuous |
| **Heuristic** | — | View/edit/create the heudiconv `heuristic.py` |

Every step panel runs its shell/MATLAB backend through a thread-safe runner that
streams output to the console, so the GUI never blocks. The same scripts can be run
headless on the cluster without the GUI (see below).

---

## Pipeline stages (command line)

All scripts live in the project root and read subject lists from `utility/`. Each
script is self-documenting (`bash stepNN_*.sh` with no args prints usage). Paths
default to the lab layout but every value is a positional argument.

```text
00  step00_unpack_V2.sh            Download raw DICOMs (findsession + rsync)
01  step01_create_bids_v2.sh       heudiconv → BIDS  (utility/SubjectList.txt)
02  step02_fmriprep_v2.sh          Raw→BIDS IDs (utility/SubjectListBIDS.txt) + fMRIPrep
03  step03_physioparse_v2.sh       Parse LabChart .mat into per-sequence physio segments
04  step04_preprocess_for_retroicor_v2.sh
                                    Filter RPIEZO per sequence → ready for R-DECO
        ── manual ──               Open *_rpiezo.mat in R-DECO, save *_rdeco.mat
05  step05_retroicor_v2.sh         1D regressors + RETROICOR correction
06  step06_stim_v2.sh              Stimulus onset extraction + first-level assembly
07  step07_firstlevel_mni_v2.sh    First-level SPM GLM + MNI normalisation
08  step08_secondlevel_v2.sh       Second-level group t-tests
```

### Physiological branch (steps 03–05)

The physio recordings are the crux of this study. `physioparse` (sibling repo)
anchors the recording to the first MR trigger and cuts it into one `.mat` per BOLD
run. Step 04 band-pass filters the cardiac (piezo) channel and writes a plain
`*_rpiezo.mat` you load into **R-DECO** to detect/correct R-peaks. Step 05 reads the
respiration channel and the R-DECO peaks, builds slice-timed RETROICOR regressors,
and applies the correction to the BIDS BOLD runs. Both Classic
(`data`/`datastart`/`dataend`) and Block1 (`data_block1`) LabChart export formats are
supported and selectable in the GUI.

---

## Directory layout

```
tvns/
├── gui/
│   ├── app.py              Dashboard (all step panels)
│   ├── runner.py           Thread-safe subprocess runner
│   └── run.sh              Launcher (selects conda env with tkinter)
├── step00_unpack_V2.sh … step08_secondlevel_v2.sh
├── utility/
│   ├── SubjectList.txt         Raw scanner IDs (one per line)        ← input
│   ├── SubjectListBIDS.txt     BIDS IDs (generated by step02 part 1)
│   ├── heuristic.py            heudiconv DICOM→BIDS mapping
│   ├── fmriprep_env.sh         Cluster environment (FreeSurfer/FSL/ANTs/conda)
│   ├── extract_stim_onsets.py  STIMTRIG onset/offset extractor
│   ├── matlab_code/            SPM GLM, RETROICOR 1D, filtering (.m)
│   ├── retroicor/              RETROICOR core (retroicor_main_modi.m, …)
│   └── r-deco-master/          R-DECO cardiac R-peak GUI
├── pipeline_state.json     Per-subject step completion (written by the GUI)
└── README.md
```

Derivatives are written under `<sourcedata>/derivatives/`:
`fmriprep/`, `physio/<subj>/{parsed,preprocessed,retroicor,first_level,stimtrigger}`,
and `spm/{first_level,second_level}`.

---

## Subject lists

- **`utility/SubjectList.txt`** — raw scanner IDs, one per line (e.g. `7T1019HC_042726`).
- **`utility/SubjectListBIDS.txt`** — BIDS IDs (`sub-7T1019HC042726`), generated from
  the raw list by step 02 (rule: strip underscores, prepend `sub-`).

---

## Requirements

- **Python** with tkinter (the lab uses a conda `Neuroimaging` env; macOS Homebrew
  Python lacks tkinter) plus `nibabel`, `numpy`, `scipy`, `matplotlib`, `imageio`.
- **heudiconv** + **dcm2niix** (BIDS conversion).
- **fMRIPrep** Singularity image (run on the cluster).
- **MATLAB** with **SPM12** (first/second-level GLM, RETROICOR, MNI warp).
- **bids-validator** (optional, via `npm` / `npx`).
- Cluster tooling: `findsession`, `rsync`, Singularity, FreeSurfer license.

---

## Notes

- **Masks are located, not copied.** Step 07 reads the fMRIPrep brain masks and
  BOLDs directly from `derivatives/fmriprep/` rather than duplicating them.
- **RETROICOR before group stats.** Physiological correction is applied to the BIDS
  BOLDs; regressors and corrected images are kept for use in the GLM.
- **Group analysis needs MNI space.** Second-level tests use the `wcon_*.nii`
  produced by the step 07 MNI warp — not the native-space `con_*.nii`.
- Scripts default to the lab's cluster paths but accept all paths as arguments, so
  the pipeline is portable to other projects by editing the Setup tab / passing args.

---

*Created by Mario Murakami.*
