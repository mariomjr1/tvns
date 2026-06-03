# TVNS fMRI Pipeline

A complete, GUI-driven pipeline for preprocessing and analysing **transcutaneous
Vagus Nerve Stimulation (tVNS) task-fMRI** data acquired at 7 T. It takes a study
from raw DICOMs on the scanner archive all the way to second-level group
statistical maps and ROI summaries in MNI space, with explicit handling of the
physiological (respiration + cardiac) recordings that are essential for denoising
brainstem signal.

---

## What this pipeline does

This pipeline converts raw 7 T MRI sessions into group-level activation maps for a
tVNS study with three functional conditions — **BlockStim**, **ContinuousStim**,
and **rest**. Starting from DICOMs pulled off the cluster archive, it organises the
data into BIDS (HeuDiConv + dcm2niix), runs **fMRIPrep** for anatomical/functional
preprocessing and spatial normalisation, then parses the simultaneously-recorded
physiological signals (ADInstruments LabChart `.mat` exports) into per-sequence
segments using a trigger-anchored "pseudotime" alignment. From those segments it
builds **RETROICOR** respiratory and cardiac nuisance regressors — the cardiac
component refined with **R-DECO** R-peak detection (manual GUI or automated) — and
enters them, together with fMRIPrep motion parameters, as covariates in the
first-level model. It extracts stimulus onset/offset timing from the stimulus
trigger channel, runs a first-level **SPM12** GLM per subject and task on the
fMRIPrep T1w-space BOLD, warps the contrasts to MNI, runs second-level cases-vs-
controls analyses, thresholds the group maps, and exports ROI values and spherical
masks. Every stage is exposed through a single tkinter dashboard with a project
explorer, a live console, optional QC at each step, centralised configuration, and
save/load of the full path configuration.

> **Physiological-noise design (important).** RETROICOR is used as **nuisance
> regressors in the GLM on the fMRIPrep-preprocessed BOLD** — *not* as an image
> correction applied before fMRIPrep. fMRIPrep runs on the pristine raw BOLD; the
> RETROICOR regressors (derived from physio + slice timing) and motion parameters
> enter only at the modeling stage. This is the TAPAS/PhysIO-style standard and the
> only arrangement that preserves fMRIPrep's motion correction.

---

## The GUI

Launch the dashboard with:

```bash
bash gui/run.sh
# or directly with the conda env that has tkinter:
~/anaconda3/envs/Neuroimaging/bin/python3 gui/app.py
```

The window has a **left project explorer**, a **scrollable step-navigation column**,
a **resizable content area**, and a **dockable console** — all on draggable
splitters. Header buttons let you **💾 Save / 📂 Load** the entire path
configuration to/from a JSON file.

### Project explorer (left panel)
A **✓ Check** button inventories the project folder — `rawdata`, `sourcedata`
(BIDS subjects + `.heudiconv`), every `derivatives/` subfolder, and the (temporary)
`SubjectList.txt` / `SubjectListBIDS.txt`. The check **runs automatically on launch
and whenever the project folder changes**, writes a rolling
`project_inventory.json` to the project root, reports what was added/removed since
the last check, and appends a dated snapshot to `<project>/codes/logs/`.

### Setup
All **common tool paths** (fMRIPrep dir, SPM12, MATLAB, MATLAB code dir,
environment script, Python, RETROICOR code, R-DECO code) are set **once** in Setup
and shared by every step. fMRIPrep auto-derives from the BIDS sourcedata path.

### Steps

| Nav entry | Backed by | Purpose |
|-----------|-----------|---------|
| **Setup** | — | Common paths, subject-list editor |
| **00 Download DICOMs** | `step00_unpack_V2.sh` | `findsession` + `rsync` raw DICOMs from the archive |
| **01 BIDS Conversion** | `step01_create_bids_v2.sh` | HeuDiConv two-pass conversion; sequence viewer; **BIDS validator** |
| **02 fMRIPrep** | `step02_fmriprep_v2.sh` | Raw→BIDS subject list; run fMRIPrep locally; optional pre/post QC GIF |
| **03 Physioparse** | `step03_physioparse_v2.sh` | Pseudotime mapping → quality viz → parse segments → optional signal QC (Classic / Block1) |
| **04 Preprocess + RDECO** | `step04_preprocess_for_retroicor_v2.sh` | Filter cardiac signal per sequence; **manual or automated R-DECO** R-peak detection |
| **05 RETROICOR** | `step05_retroicor_v2.sh` | Generate 1D regressors (with R-DECO peaks) + RETROICOR |
| **06 Stim Triggers** | `step06_stim_v2.sh` | Stimulus onsets from STIMTRIG; assemble first-level inputs (stim + motion + RETROICOR + BOLD) |
| **07 First-level + MNI** | `step07_firstlevel_mni_v2.sh`, `step07b_warp_folder_v2.sh` | SPM GLM (masks located in place) + MNI warp; warp-only & single-folder-warp modes |
| **08 Second-level** | `step08a_populate_v2.sh`, `step08b_groups_v2.sh` | Part 1 populate per-task folders; Part 2 cases-vs-controls two-sample t-tests |
| **09 Threshold p<0.05** | `step09_p_value.sh` | Threshold a group contrast (e.g. Cases>Controls) → significance map |
| **10 ROI extraction** | `step10_ROI.sh` | Per-subject values at a peak + 5/10 mm spheres; mask con & group contrasts |
| **Heuristic** | — | **Heuristic builder** — assign step01 sequences to BIDS targets and auto-generate `heuristic.py` |

Every step panel streams its shell/MATLAB backend through a thread-safe runner, so
the GUI never blocks. All scripts also run headless on the cluster.

### Heuristic builder
Instead of a plain text editor, the **Heuristic** tool loads the sequences detected
by Step 01 Pass 1 for a chosen subject, lets you **assign each sequence to a BIDS
target** (T1w / T2w / `task-*`) matched on `series_description` and/or `dim3`, and
**auto-generates a valid `heuristic.py`**. Heuristics are saved to
`utility/heuristic/<name>.py` with a sibling `<name>.log` listing the **added** and
**excluded** sequences. **Templates** (shared starting points for projects with the
same sequence pattern) live in `utility/heuristic/template/` — load one, adapt it,
and save a project-specific heuristic. **Step 01 Pass 2** has a heuristic dropdown
so you can pick which one to convert with.

---

## Pipeline stages (command line)

Each script is self-documenting (`bash stepNN_*.sh` with no args prints usage).
Paths default to the lab layout but every value is a positional argument.

```text
00  step00_unpack_V2.sh             Download raw DICOMs (findsession + rsync)
01  step01_create_bids_v2.sh        HeuDiConv → BIDS  (utility/SubjectList.txt)
02  step02_fmriprep_v2.sh           Raw→BIDS IDs (utility/SubjectListBIDS.txt) + fMRIPrep
03  step03_physioparse_v2.sh        Parse LabChart .mat into per-sequence physio segments
04  step04_preprocess_for_retroicor_v2.sh
                                     Filter RPIEZO per sequence → ready for R-DECO
        ── R-DECO ──                Manual GUI or automated (rdeco_auto_analysis.m) → *_rdeco.mat
05  step05_retroicor_v2.sh          1D regressors + RETROICOR
06  step06_stim_v2.sh               Stim onsets + first-level assembly (numbered subfolders)
07  step07_firstlevel_mni_v2.sh     First-level SPM GLM + MNI normalisation
    step07b_warp_folder_v2.sh       Optional: warp one already-done first-level folder to MNI
08  step08a_populate_v2.sh          Part 1 — gather wcon images into per-task folders
    step08b_groups_v2.sh            Part 2 — cases-vs-controls two-sample t-tests + contrasts
09  step09_p_value.sh               Threshold a group contrast at p<0.05 → significance map
10  step10_ROI.sh                   ROI value extraction + 5/10 mm spheres + masking
```

### Physiological branch (steps 03–05)
`physioparse` (vendored in `utility/physioparse/`) anchors the recording to the
first MR trigger and cuts it into one `.mat` per BOLD run, reading the BIDS JSON
sidecars directly from `sourcedata/<subj>/ses-01/func/` and `dicominfo_ses-01.tsv`
from `.heudiconv/`. Step 04 band-pass filters the cardiac (piezo) channel per
sequence and writes a plain `*_rpiezo.mat`; you then either open it in **R-DECO**
manually or run the **automated R-DECO** analysis (peak detection at 300/500 ms
envelopes, ectopic removal, doubled-beat removal > 150 bpm, QC image) to produce
`*_rdeco.mat`. Step 05 builds slice-timed RETROICOR regressors from the respiration
channel and the R-DECO peaks. Both Classic (`data`/`datastart`/`dataend`) and Block1
(`data_block1`) LabChart export formats are supported and selectable in the GUI.

---

## Directory layout

```
tvns/
├── gui/
│   ├── app.py               Dashboard (all step panels, project explorer)
│   ├── runner.py            Thread-safe subprocess runner
│   └── run.sh               Launcher (selects conda env with tkinter)
├── step00_unpack_V2.sh … step10_ROI.sh
├── utility/
│   ├── SubjectList.txt          Raw scanner IDs (temporary list)        ← input
│   ├── SubjectListBIDS.txt      BIDS IDs (temporary, from step02)
│   ├── heuristic.py             HeuDiConv DICOM→BIDS mapping (default)
│   ├── heuristic/               Built heuristics (<name>.py + <name>.log)
│   │   └── template/            Reusable heuristic templates (e.g. tvns_default.py)
│   ├── fmriprep_env.sh          Cluster environment (FreeSurfer/FSL/ANTs/conda)
│   ├── extract_stim_onsets.py   STIMTRIG onset/offset extractor
│   ├── roi_extract.py           ROI value extraction + spheres + masking
│   ├── physioparse/             Vendored physioparse (pseudotime + parse + QC)
│   ├── matlab_code/             SPM GLM, group stats, RETROICOR 1D, R-DECO auto (.m)
│   ├── retroicor/               RETROICOR core (retroicor_main_modi.m, …)
│   └── r-deco-master/           R-DECO cardiac R-peak GUI
├── pipeline_state.json      Per-subject step completion (written by the GUI)
└── README.md
```

Derivatives are written under `<sourcedata>/derivatives/`:
`fmriprep/`, `physio/<subj>/{parsed,preprocessed,retroicor,stimtrigger}`,
`physio/first_level/{01_stim_onsets,02_motion_regressors,03_retroicor_regressors,04_bolds}`,
and `spm/{first_level,second_level/{tasks,groups,thresholded}}`.
A `project_inventory.json` (project root) and dated logs (`<project>/codes/logs/`)
track the data state.

---

## Subject lists (temporary working lists)

- **`utility/SubjectList.txt`** — raw scanner IDs, one per line (e.g. `7T1019HC_042726`).
- **`utility/SubjectListBIDS.txt`** — BIDS IDs (`sub-7T1019HC042726`), generated from
  the raw list by step 02 (strip underscores, prepend `sub-`).

---

## Requirements

- **Python** with tkinter (the lab uses a conda `Neuroimaging` env; macOS Homebrew
  Python lacks tkinter) plus `nibabel`, `numpy`, `scipy`, `matplotlib`, `imageio`.
- **HeuDiConv** + **dcm2niix** (BIDS conversion).
- **fMRIPrep** Singularity/Apptainer image (run on the cluster).
- **MATLAB** with **SPM12** (first/second-level GLM, RETROICOR, MNI warp, R-DECO auto).
- **bids-validator** (optional, via `npm` / `npx`).
- Cluster tooling: `findsession`, `rsync`, Singularity, FreeSurfer license.

---

## Method notes

- **fMRIPrep first, RETROICOR as regressors.** See the design box above — the
  fMRIPrep T1w BOLD (motion-corrected, SDC, registered) is modeled with RETROICOR
  regressors + motion parameters, rather than feeding a RETROICOR-corrected image
  into fMRIPrep. Slice-wise RETROICOR regressors are averaged across slices to yield
  volume-level covariates.
- **Masks are located, not copied.** Step 07 reads the fMRIPrep brain masks and
  BOLDs directly from `derivatives/fmriprep/`.
- **Group analysis needs MNI space.** Second-level tests use the `wcon_*.nii`
  produced by the step 07 MNI warp — not the native-space `con_*.nii`.
- **First-level inputs use numbered subfolders** (`01_stim_onsets`, …); the GLM also
  accepts the legacy unnumbered names for backward compatibility.
- Scripts default to the lab's cluster paths but accept all paths as arguments, so
  the pipeline is portable by editing the Setup tab / passing args.

---

*Created by Mario Murakami.*
