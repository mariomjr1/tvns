# SOP X — The Whole Pipeline, Start to Finish (read me first)

**Who this is for:** a brand-new intern who has never used this pipeline and may have
little or no fMRI background. It explains, in plain language, **what the GUI does — panel
by panel, button by button — from the raw scanner files all the way to the final numbers.**
The numbered SOPs (00–10) are the detailed per-step reference; **this document is the
complete operator's manual and the map that ties them together.**

> **One-line summary:** this software turns the raw 7-Tesla MRI files from a **tVNS**
> (transcutaneous vagus-nerve-stimulation) study into brain-activation maps and brainstem
> measurements, and compares patients ("**cases**") with "**controls**."

---

## Table of contents
- [A. Orientation — the big idea](#a-orientation--the-big-idea)
- [B. Vocabulary (concepts, files, IDs)](#b-vocabulary)
- [C. Getting started (launch + window tour)](#c-getting-started)
- [D. Setup tab — fill the paths once](#d-setup-tab--fill-the-paths-once)
- [E. The steps, in order (the core)](#e-the-steps-in-order)
- [F. The two analysis routes (MNI vs native)](#f-the-two-analysis-routes)
- [G. The brainstem sub-pipeline (05b → 05c → 10b)](#g-the-brainstem-sub-pipeline)
- [H. QC & housekeeping tools](#h-qc--housekeeping-tools)
- [I. The flag-and-log rule + how to read logs](#i-the-flag-and-log-rule)
- [J. Where everything lands (output map)](#j-where-everything-lands)
- [K. End-to-end run checklist](#k-end-to-end-run-checklist)
- [L. Troubleshooting](#l-troubleshooting)
- [M. FAQ](#m-faq)
- [N. Data handling & safety (important)](#n-data-handling--safety)

---

## A. Orientation — the big idea

### What the study is
The vagus nerve carries signals between the body and the brainstem. **tVNS** stimulates a
branch of it non-invasively (through the skin, e.g. at the ear). We record **fMRI** while
stimulation is on/off and ask: *which brain regions — especially small brainstem nuclei —
respond, and do patients (cases) respond differently from controls?* Because the key
targets are tiny brainstem nuclei imaged at 7T, a lot of the pipeline is about getting the
**physiological noise out** and the **brainstem alignment right**.

### The assembly line
Each **step** consumes the previous step's output and adds something. Run them **in order,
top to bottom.** The GUI is just buttons that run the underlying scripts with the paths you
set once in Setup.

```
  raw scanner files
        │  00  download DICOMs
        ▼
   tidy dataset (BIDS) ──01──┐
        │                    │ (uses AP/PA fieldmaps for distortion correction later)
        │  02  parse physiology (heart/breathing) into per-scan pieces
        │  03  filter + detect heartbeats (R-DECO)
        │  04  RETROICOR: REMOVE heart/breathing noise from the BOLD images
        ▼
   physio-cleaned BOLD
        │  05  fMRIPrep: motion/distortion correction, align to anatomy + template
        │      05b (optional) segment brainstem + pituitary
        │      05c (optional) refine brainstem alignment   ← improves brainstem
        ▼
   preprocessed BOLD
        │  06  stimulus timing + head-motion nuisance
        │  07  first-level GLM  → per-subject "Stim > baseline" map
        │  08  group analysis   → cases vs controls
        │  09  threshold        → keep significant voxels
        │  10  ROIs (spheres or atlas nuclei) ; 10b native-space brainstem nuclei
        ▼
   tables/maps for the paper
```

### Three golden rules (true for the whole pipeline)
1. **Physiology is cleaned BEFORE fMRIPrep** (steps 02→03→04 run *before* 05). fMRIPrep
   then runs on the corrected image. The first-level GLM (07) adds **motion regressors
   only** — it does **not** re-remove physiology (that already happened to the image).
2. **Nothing is silently dropped.** When a check fails, the pipeline **flags it, writes a
   log, and keeps going** — it never stops the batch or quietly excludes a subject/run.
   **Reading the logs is part of your job** (see [Section I](#i-the-flag-and-log-rule)).
3. **Group test is one-tailed, cases > controls, by design** (preliminary/directional
   hypothesis). Don't "fix" it to two-tailed.

---

## B. Vocabulary

### Concepts (skim once; refer back as needed)
| Term | Plain meaning |
|---|---|
| **DICOM** | Raw scanner output: a folder of thousands of little files per scan. Messy. |
| **BIDS** | A tidy, standardized folder layout + file names so software knows what each scan is. Step 01 makes it. |
| **BOLD** | The functional MRI signal (blood-oxygenation) over time — our proxy for brain activity. One "run" = one task scan. |
| **T1w** | A sharp anatomical/structural brain image. The map onto which functional data is aligned. |
| **Fieldmap / PEPOLAR / TOPUP / SDC** | 7T images are geometrically distorted. We acquire two short scans with opposite phase encoding (**AP** and **PA**); **TOPUP** uses them to undo the distortion — this is **SDC** (susceptibility-distortion correction). |
| **Physiology (physio)** | Heartbeat + breathing recordings. Here the heartbeat is a **piezo** pulse sensor (not ECG) and breathing is a belt. They add noise to BOLD. |
| **RETROICOR** | Uses the physio to **remove** heart/breathing noise from the BOLD images (steps 02–04). |
| **R-peak / R-DECO** | The "R-peak" is the heartbeat spike. **R-DECO** is the tool that detects them from the piezo trace (step 03). |
| **fMRIPrep** | A standard preprocessing program: motion + distortion correction, alignment of BOLD→T1w→template, confounds, and it runs **FreeSurfer**. |
| **FreeSurfer** | Reconstructs the brain's anatomy/surfaces from the T1w; also segments structures (we use it for the brainstem and pituitary in 05b). |
| **ANTs** | The registration (image-alignment/warping) toolkit used for the brainstem refinement (05c) and atlas warping (10b). |
| **MNI** | A standard "average brain" coordinate space. Putting everyone in MNI lets us compare subjects and report coordinates. |
| **Registration / warp / transform** | The math that maps one image's space to another's (e.g. subject T1w ↔ MNI). A "warp" can be applied to images or to atlases. |
| **GLM / first-level** | Per-subject statistics estimating how strongly each voxel responded to stimulation (step 07). |
| **Contrast** | The specific comparison being estimated. Here: **"Stim > baseline."** |
| **con / wcon** | `con_*.nii` = a subject's contrast image in its first-level space; `wcon_*.nii` = that contrast in MNI (what the group step needs). |
| **Second-level / group** | Combines subjects; compares **cases vs controls** (step 08). |
| **Threshold** | Keeps only statistically significant voxels (step 09). |
| **ROI** | "Region of interest" — a defined area where we measure the signal (step 10/10b). |
| **Atlas** | A pre-made labeled map (in MNI) where each voxel is tagged as a specific structure. We use a brainstem-nuclei atlas (e.g. **Brainstem Navigator**) to define NTS/LC/raphe. |
| **Brainstem nuclei (NTS / LC / raphe)** | Tiny deep structures this study targets. Small + low-contrast ⇒ hard to image ⇒ the extra brainstem steps. |
| **FD (framewise displacement)** | A per-volume head-motion measure. High-motion volumes are "censored" by spike regressors; no subject is excluded for motion. |

### File types you'll see
| Extension | What it is |
|---|---|
| `.nii.gz` / `.nii` | A brain image (NIfTI). Compressed or not. |
| `.json` | A "sidecar" with scan metadata (timing, TR, etc.). |
| `.mat` | MATLAB data (physio segments, regressors, SPM models). |
| `.1D` | A plain text regressor column (RETROICOR output). |
| `.tsv` / `.csv` | Tables (confounds, QC summaries, ROI values). |
| `SPM.mat` | An SPM statistical model (first- or second-level). |
| `.h5` | A composite transform/warp file from fMRIPrep. |

### Two kinds of subject ID (don't mix them up)
- **Raw scanner ID** — e.g. `7T1019HC_042726` (has an underscore). Goes in
  `SubjectList.txt`. Used by steps 00–01.
- **BIDS ID** — e.g. `sub-7T1019HC042726` (underscore stripped, `sub-` prefix). Generated
  by step 05 Part 1 into `SubjectListBIDS.txt`. Used by every step from 02/05 onward.

---

## C. Getting started

### What you need
- Access to the **cluster** — the heavy tools (fMRIPrep, FreeSurfer, MATLAB/SPM, ANTs,
  the heudiconv environment) live there, not on a laptop.
- A populated **`SubjectList.txt`** (raw scanner IDs).
- The session **LabChart `.mat`** physio file(s).
- Patience: **fMRIPrep takes hours per subject**; FreeSurfer-based steps are also slow.

### Launch + the window
Run the app (e.g. `python gui/app.py`). The window has:
- **Left nav** — the list of steps/tools. Click one to open its **panel** on the right.
- **Right panel** — the controls (paths, options, buttons) for the selected step.
- **Console (bottom)** — live output of whatever is running. **This is where you watch for
  problems.**
- **Status line** — short "running…/complete ✓/failed" messages.

### How to read the console (important habit)
- **`WARNING`** and **`[FLAG]`** lines are the ones to read — they mark something that was
  flagged-and-continued.
- **`[PIEZO-SKIP]`** = a run was processed respiration-only (expected for messy heartbeat).
- **`✓ complete`** means the step *finished*, **not** that everything inside was perfect —
  still skim the flags.
- A red **failed (exit N)** means the step actually errored (a real stop, e.g. bad path,
  out-of-memory) — read the last lines of the console / the step's log.

---

## D. Setup tab — fill the paths once

### First: point at a project (left "Project" sidebar)
The **Project** bar on the left chooses *which study you're working on* — and can build a
fresh one for you:
- **Folder + `…`** — pick an **existing** project folder. The GUI auto-fills `rawdata/`,
  `sourcedata/`, and the subject lists under it.
- **New: `<name>` + `+ Create`** — **scaffolds a brand-new project**: it creates the whole
  folder tree (rawdata, sourcedata, every `derivatives/` subfolder **including the two
  separated first-level routes `spm/first_level_mni` and `spm/first_level_t1w`**,
  `second_level/{tasks,groups,thresholded}`, `roi`, `brainstem_coreg`, `codes/{qc,logs}`)
  and switches to it. Use **Create** for a new study; use **Folder** to *continue an
  existing one*.
- **✓ Check** — *inventories* the project (it does **not** change anything): counts the
  subjects in rawdata/sourcedata, lists every `derivatives/` subfolder + the subject lists,
  reports what changed since last time, and saves a snapshot to `project_inventory.json`
  (plus a dated log in `codes/logs/`). It runs automatically on launch and whenever the
  project folder changes — it's your "what's in this project right now?" view.

### Save / Load config (top header buttons)
- **💾 Save config** writes **all** the Setup paths to a JSON file.
- **📂 Load config** reads them back in. So you configure a project once, save it, and
  reload it next session (or hand it to a colleague) — nothing is hardcoded in the code.

### Then: fill the path fields (once)
Set these **once**; every step reuses them. If a path is wrong, the step that needs it
tells you (it won't guess or invent a path). **What to put in each field:**

| Setup field | What it is | Typical value / note |
|---|---|---|
| **Raw data path** | Where downloaded DICOMs go | `<project>/rawdata` |
| **BIDS sourcedata** | The tidy dataset root | `<project>/sourcedata` |
| **Heuristic file** | Rules that map scans → BIDS names | `utility/heuristic.py` (or one you build) |
| **Env activate script** | The heudiconv Python env | lab `env/heudiconv/bin/activate` |
| **SubjectList.txt** | Raw scanner IDs (one per line) | step 00/01 input |
| **fMRIPrep derivatives** | Where fMRIPrep writes | `sourcedata/derivatives/fmriprep` |
| **SPM12 dir** | SPM install | for steps 07–10 |
| **MATLAB exe** | MATLAB binary | `matlab` (or full path) |
| **MATLAB code dir** | This repo's `.m` files | `utility/matlab_code` |
| **Environment script** | The cluster env for fMRIPrep/MATLAB steps | `utility/fmriprep_env.sh` |
| **Python exe** | Python for the Python steps | usually auto-filled |
| **RETROICOR / R-DECO code dirs** | Those toolboxes | `utility/retroicor`, `utility/r-deco-master` |
| **FreeSurfer 8.1+ home** | FreeSurfer ≥ 8.1 install | needed for pituitary seg (05b) + brainstem steps; the seg/coreg scripts source `<home>/SetUpFreeSurfer.sh` |
| **Brainstem atlas (NIfTI)** | Labeled brainstem-nuclei map (MNI) | e.g. Brainstem Navigator; used by step 10 / 10b |

> The two **first-level route folders** (`first_level_mni`, `first_level_t1w`) are created
> by **+ Create** and filled by step 07 — you don't set them in Setup (see Section F).

---

## E. The steps, in order

For each step below: **What it does → GUI (panel + what to click) → Inputs → Outputs →
Roughly how long → How to verify ("good" looks like) → Move on when.** Full detail is in
the matching numbered SOP (linked).

### Step 00 — Download DICOMs · [SOP 00](SOP_00_download_dicoms.md)
- **What:** finds each subject's scan archive and copies it to `rawdata/`.
- **GUI:** *Step 00* panel → subject selection (**All** / **Specific**) → **▶ Run Step 00**
  (foreground; streams to console). **⏹ Stop** cancels.
- **In:** `SubjectList.txt` (raw IDs). **Out:** `rawdata/<id>/DICOM/raw[/_NN]/` +
  `step0_DONE.txt`.
- **Time:** minutes–tens of minutes per subject (network copy).
- **Verify:** every subject has `step0_DONE.txt` and a non-trivial `raw/`. Investigate any
  `step0_ERROR.txt`.
- **Move on when:** all subjects show `step0_DONE.txt`.

### Step 01 — BIDS conversion · [SOP 01](SOP_01_bids_conversion.md)
- **What:** converts DICOM → tidy BIDS, naming every scan (T1w, rest, BlockStim,
  ContinuousStim, and the **AP/PA fieldmaps** for distortion correction). A **heuristic**
  (rules file) decides which scan is which.
- **GUI (tabs):** **Pass 1 — Generate codes** (detect sequences) → **Sequences (heuristic)**
  — the *embedded Heuristic Builder*: **↻ Scan**, pick subject; it **auto-assigns each
  sequence to its BIDS target from the default rules** (T1w/tasks/fmaps). Change/exclude any
  row (or **↺ Auto-fill** to reset), then **⚙ Generate → 💾 Save → Use in Pass 2** → **Pass 2
  — Convert to BIDS** (**Run**) → **BIDS Validator**.
- **In:** `rawdata/`. **Out:** `sourcedata/sub-XXXX/ses-01/{anat,func,fmap}/` + `.json`.
- **Time:** minutes per subject.
- **Verify:** each task run has `*_bold.nii.gz` + `*_bold.json`; fieldmaps in `fmap/`. Run
  the BIDS validator tab.
- **Watch:** `Missing correct number of … runs` ⇒ a scan didn't match — fix the heuristic.
- **Move on when:** BIDS folders look complete and the validator is clean enough.

### Step 02 — Physioparse · [SOP 02](SOP_02_physioparse.md)
- **What:** anchors the continuous LabChart recording to the first MR trigger, cuts it into
  one piece per BOLD run, and computes physio QC.
- **GUI:** *Step 02* panel → pick subject + the session `.mat` (Classic or Block1) → run
  **pseudotime → quality viz → parse → signal QC** (buttons, in order).
- **In:** the LabChart `.mat` + BIDS sidecars. **Out:** `derivatives/physio/<subj>/parsed/
  task-*_run-*.mat`, `pseudotime_plot.png`, `qc/physio_qc_metrics.csv`.
- **Time:** a few minutes (file can be multi-GB to read).
- **Verify (most important):** open **`pseudotime_plot.png`** — every BOLD sequence must
  align to the correct recording segment. Console flags any expected-vs-parsed count
  mismatch.
- **Move on when:** segments are correctly aligned and parsed.

### Step 03 — Preprocess + R-DECO · [SOP 03](SOP_03_preprocess_rdeco.md)
- **What:** filters the piezo (cardiac) channel and detects heartbeats (R-peaks) with
  **R-DECO**.
- **GUI:** *Step 03* panel → **Filter** tab → **Run filtering**; then **R-DECO** tab → **Run
  automated** (or open the R-DECO GUI to correct beats by hand).
- **In:** parsed `.mat`. **Out:** `*_filtered.mat`, `*_rpiezo.mat`, `*_rdeco.mat`.
- **Time:** minutes per subject (manual R-DECO correction adds time).
- **Verify:** one `*_rdeco.mat` per run; check the R-DECO QC image (each true beat marked
  once). A flat RESP errors *for that run only*; a flat RPIEZO warns (route it
  respiration-only in step 04). Both are flagged; the rest continue.
- **Move on when:** R-peaks look right (or you've decided which runs go respiration-only).

### Step 04 — RETROICOR (+ Piezo QC review) · [SOP 04](SOP_04_retroicor.md)
- **What:** builds slice-timed RETROICOR regressors and **removes heart/breathing noise
  from the BOLD images** → a corrected BOLD that fMRIPrep will ingest.
- **GUI (three tabs):**
  - *Configuration* — subject + parameters (session, SMS, output rate, TR fallback).
  - *Piezo QC Review* (**do this first**) — **▶ Run/Refresh Cardiac QC** → **⟳ Load review**
    → for each scan view the piezo trace + a **GOOD/SUSPECT/BAD** verdict and pick **Use
    cardiac** or **Respiration only** → **💾 Save decisions**. **📊 Cohort report**
    summarizes all respiration-only runs.
  - *Run Pipeline* — **Part 1 — Generate 1D**, **Part 2 — Assemble BOLD**, **Part 3 —
    RETROICOR**, or **▶▶ Run All** (your saved per-run decisions are applied automatically).
- **Why the piezo review exists:** we record a **piezo pulse, not ECG** (a documented
  limitation). A messy piezo gives unreliable beats; using its cardiac regressors would add
  noise. Those runs are processed **respiration-only** — flagged, never dropped.
- **In:** `*_filtered.mat` (+ `*_rdeco.mat`). **Out:** `output/*_retro-corrected.nii.gz`
  (the pipeline input for fMRIPrep) + diagnostics (`*_retro-pctvar.mat`).
- **Time:** minutes–tens of minutes per subject.
- **Verify:** corrected BOLDs exist; review the respiration-only list; check `% variance`
  removed.
- **Move on when:** every run has a corrected BOLD and you've reviewed the piezo decisions.

### Step 05 — fMRIPrep · [SOP 05](SOP_05_fmriprep.md)
- **What:** the heavy standard preprocessing, **run on the corrected BOLD**: distortion
  correction (AP/PA→TOPUP), motion + slice-timing correction, BOLD→T1w→MNI alignment,
  FreeSurfer recon, confounds. Outputs native-T1w and MNI BOLD (+ CIFTI).
- **GUI (tabs Generate BIDS List | fMRIPrep | Pre/Post QC):** build **SubjectListBIDS.txt**
  first; then on the fMRIPrep tab confirm paths/options, pick subjects → **▶ Run fMRIPrep**.
- **In:** corrected BIDS. **Out:** `derivatives/fmriprep/sub-XXXX/…` + FreeSurfer dir +
  `qc_fd_summary.json`.
- **Time:** **hours per subject.** Run sequentially.
- **Verify:** open each subject's **HTML report** (registration, SDC, surfaces); review
  pre/post GIFs; run the **SDC audit** (confirms distortion correction was *applied*, not
  just available); check the corrected-BIDS audit log.
- **Motion policy:** mean FD > 0.9 mm is **flagged only** — no subject is excluded;
  high-motion volumes are censored later by FD spike regressors.
- **Move on when:** reports look acceptable and SDC is confirmed.

### Step 05b — Brainstem & pituitary segmentation (optional) · [SOP 05 §8b](SOP_05_fmriprep.md#8b-optional-brainstem--pituitary-extras-step05b--step05c)
- **What:** after recon-all, labels brainstem sub-parts (→ a **brainstem mask** for 05c)
  and the **pituitary/pineal** glands (volumetry). *Segmentation alone does not improve
  alignment* — it sets up 05c.
- **GUI:** fMRIPrep panel → **▶ Brainstem segmentation** and/or **▶ Pituitary/pineal
  (PGlandsSeg)**.
- **Needs:** FreeSurfer ≥ 8.1 (Setup) for the pituitary tool; if absent it **flags and
  skips** that part rather than failing.
- **Move on when:** brainstem labels exist (needed before 05c).

### Step 05c — Brainstem co-registration refine (optional) · [SOP 05 §8b](SOP_05_fmriprep.md#8b-optional-brainstem--pituitary-extras-step05b--step05c)
- **What:** **the step that actually improves brainstem alignment** — a brainstem-only
  (cost-function-masked) ANTs refinement driven by the 05b mask.
- **GUI:** fMRIPrep panel → set the **MNI template** → **▶ Brainstem co-reg refine**.
- **Caveat:** this is a **scaffold** — on the cluster, confirm with an overlay that the
  brainstem lines up before trusting brainstem ROI numbers (see [Section G](#g-the-brainstem-sub-pipeline)).

### Step 06 — Stim triggers + motion · [SOP 06](SOP_06_stim_triggers.md)
- **What:** reads stimulation onsets from the STIMTRIG channel and builds the **head-motion
  nuisance** (6 rigid params + one FD-spike regressor per high-motion volume — a deliberately
  **minimal** model; no aCompCor/derivatives). Assembles the shared `first_level/` inputs.
- **GUI:** *Step 06* panel → pick subject(s), set threshold/debounce + QC → **Run**.
- **Out:** `*_bold_stim.txt` (onsets), `*_motion_regressors.txt`.
- **Verify:** QC plots — onsets land on STIMTRIG edges; the MR-trigger count ≈ the BOLD
  volume count (a mismatch hints at dropped dummy scans).
- **Move on when:** onsets + motion regressors look right.

### Step 07 — First-level GLM (+ 07b) · [SOP 07](SOP_07_firstlevel_mni.md)
- **What:** per subject, estimates the response to stimulation → a **"Stim > baseline"**
  contrast. The GLM adds **motion regressors only** (physio was removed upstream).
- **Space (the key choice):** **`both` (default)** runs *both* routes into **separate
  folders** (`first_level_mni` + `first_level_t1w`); pick `MNI` or `T1w` to run just one.
  (See [Section F](#f-the-two-analysis-routes).)
- **GUI (tabs):** **First-level GLM** + **Brainstem mask**. On *First-level GLM*: set
  **Space** (defaults to `both`), smoothing/TR → **Run** (with `both` it runs the MNI route
  then the T1w route, each into its own auto-created folder). The **Brainstem mask** tab is
  the embedded mask builder — from an atlas + reference grid it writes the MNI brainstem
  mask used by step 07's "restrict to brainstem" option and by steps 09/10. The *07b* panel
  warps a single existing con folder to MNI.
- **Out:** `first_level_mni/<subj>/<task>/{con,wcon}_0001.nii` (MNI) **and**
  `first_level_t1w/<subj>/<task>/{con}_0001.nii` (native). Same names, separate folders.
- **Verify:** open `SPM.mat` (design looks right) and `wcon_0001.nii` (sensible activation,
  correct MNI alignment).

### Step 08 — Second-level (groups) · [SOP 08](SOP_08_secondlevel.md)
- **What:** combines subjects and runs **cases vs controls**.
- **GUI:** *Step 08* panel → **Part 1 — Populate** (gathers each subject's `wcon_0001.nii`;
  verifies the contrast name, flags mismatches to `_contrast_check.csv`) → **Part 2 — Group
  analysis** (set **Cases**/**Controls** lists, optional covariates `age,sex,mean_fd`,
  combined mode → run).
- **Out:** per-task + combined `SPM.mat` + `spmT_*.nii` (Cases>Controls etc.).
- **Verify:** subject counts per task; nobody in both lists; review `_contrast_check.csv`.

### Step 09 — Threshold · [SOP 09](SOP_09_threshold.md)
- **What:** keeps significant voxels. Default is **one-tailed, Cases > Controls**
  (`tail=pos`); correction defaults to **none** (pilot) with FWE/FDR opt-in.
- **GUI:** *Step 09* panel → set analysis dir (a Part 2 group folder), p, extent, contrast
  index, tail, correction → **Run**.
- **Out:** binary significance mask + thresholded t-map (filenames tagged `unc`/`FWE`/`FDR`).
- **Verify:** overlay the t-map on the MNI template; record p/extent/tail/correction (these
  are reporting-critical).

### Step 10 — ROI extraction · [SOP 10](SOP_10_roi.md)
- **What:** measures each subject's signal in a region — a **sphere** at a peak coordinate
  and/or **named brainstem nuclei** from the atlas.
- **GUI:** *Step 10* panel → enter X/Y/Z + mode (sphere) **and/or** set the **Labeled atlas**
  (from Setup) + label values/names → **▶ Run Step 10 — Extract**.
- **Out:** `roi_values.csv` (one row per subject) + sphere masks. `_roi_geometry_check.csv`
  flags any subject whose geometry differed (resampled + flagged, never dropped).
- **Verify:** one row per subject, no out-of-brain NaNs; `coord_mode` matches how you read
  the peak.

### Step 10b — Native-space nuclei ROIs (uses 05c) · [SOP 10 §6b](SOP_10_roi.md#6b-brainstem-nuclei-rois-atlas--native-space-step10b)
- **What:** the precise brainstem version — warps the atlas into each subject's **native**
  space through the **composed** `fMRIPrep MNI→T1w ∘ step05c refine` transform, then measures
  the nuclei there.
- **GUI:** *Step 10* panel → **Native-space nuclei ROIs** sub-frame → set native-con root +
  glob + output → **▶ Atlas→native ROIs**. **Requires step 07 run with Space=T1w.**
- **Out:** `group_brainstem_nuclei_native.csv` + per-subject `*_atlas-in-native.nii.gz`.
- **Caveat:** **scaffold** — first verify the atlas-in-native overlay looks right on the
  cluster before trusting the numbers.

---

## F. The two analysis routes

This is the part people find confusing, so here it is slowly. You choose the route at
**step 07 (the "Space" setting).**

### What "space" means (the core idea)
Every brain is a slightly different shape. To compare subjects, or to drop an atlas of
nuclei onto someone, you must pick **whose coordinate system you work in**:
- **MNI space** = a shared "standard template brain." You **warp each subject onto the
  template.** Great for group stats and reporting coordinates — but the warp is computed
  for the **whole brain**, so the tiny, low-contrast **brainstem is aligned only loosely.**
- **Native (T1w) space** = each subject **stays in their own brain** (nothing is squished
  onto a template). The BOLD is aligned to that subject's own anatomy (a tight, local
  alignment called **BBR**), so the brainstem stays faithfully where it really is.

**Analogy:** the MNI route *moves every brain to a shared map and measures there*; the
native route *leaves each brain where it is and instead brings the map (atlas) to them*,
using a brainstem-tuned alignment.

### Route 1 — MNI (default)
- The GLM (07) models the **MNI** BOLD (fMRIPrep already warped it with its whole-brain
  warp); ROIs (10) put the MNI atlas directly on the MNI maps.
- **Does NOT use the brainstem registration (step05c).** The functional data reached MNI
  through the loose whole-brain warp, and step05c never touches this path — so the
  brainstem is only roughly placed.

### Route 2 — Native / T1w (this one uses the brainstem registration)
- The GLM (07, Space=`T1w`/`both`) models the **native T1w** BOLD — the brainstem stays
  exactly where it is in that subject.
- ROIs (**step10b**): instead of pushing the subject to MNI, it **brings the atlas to the
  subject**, warping it into native space through the **composed transform
  `fMRIPrep MNI→T1w ∘ step05c refine`**.
- **This is the only route that uses step05c.** Both the functional data *and* the atlas
  end up best-aligned in the brainstem.

> **So: which one co-registers with the brainstem registration?** Only the **native /
> T1w route (via step10b)**. The MNI route ignores step05c.

### Side by side
| | **MNI route (default)** | **Native / T1w route** |
|---|---|---|
| First-level GLM (07) | models the fMRIPrep **MNI** BOLD | models the **native T1w** BOLD |
| Group/threshold (08–09) | uses `wcon` (already MNI) | also warps con→MNI for the group test |
| Uses step05c brainstem refinement? | **No** | **Yes** (in step10b) |
| Brainstem-nucleus ROIs (10b) | atlas grid-resampled in MNI (rougher) | **atlas warped to native via step05c** (precise) |
| Best for | whole-brain / cortex, group maps, MNI coordinates | the small **brainstem nuclei** (NTS/LC/raphe) |

### Where each route's files go (separate folders — no collision)
Step 07 **defaults to `both`** and writes each route to its **own folder**, created
automatically (and pre-built by **+ Create**):
- **MNI route →** `…/derivatives/spm/first_level_mni/<subj>/<task>/` (`con`/`wcon` in MNI)
- **T1w route →** `…/derivatives/spm/first_level_t1w/<subj>/<task>/` (`con` in native)

The file **names are identical** in both routes (`con_0001.nii`, `wcon_0001.nii`,
`SPM.mat`) — the **folder** is what separates them, so neither run can overwrite the other.
Downstream defaults follow automatically: **step 08** reads `first_level_mni`; **step 10b**
reads `first_level_t1w`. (In the GUI, `both` runs the two routes back-to-back; choosing
`MNI` or `T1w` runs just that one into its own folder.)

### Which is better?
**It depends on what you're measuring — and they're not mutually exclusive.** The
brainstem nuclei are *exactly* where the whole-brain MNI warp is weakest, so that's where
the native + step05c route pays off; everywhere else the MNI route is perfectly good.

**The default (`both`) gives you both in one click** — each into its own folder:
1. **MNI route** for the main analysis (cortex, whole-brain group maps, coordinates).
2. **Native route** for the **brainstem ROI values** — then run **step10b** to extract
   NTS/LC/raphe with the step05c-refined atlas.

Note: even the native route still produces **MNI group maps** (step 07 also warps the
contrast to MNI for step 08) — native space is used **only** at the ROI-extraction stage
(step10b), which is the one place brainstem precision matters.

> **Caveat:** step05c + step10b are **scaffolds** — validate the transform chain on the
> cluster with an **atlas-in-native overlay** (does the atlas land on the actual brainstem
> nuclei?) before trusting the native brainstem ROI numbers. Until then, the MNI atlas
> ROIs are a rougher fallback.

---

## G. The brainstem sub-pipeline

**Why it's special:** whole-brain normalization aligns the brainstem poorly (it's small,
low-contrast, and the nuclei are millimeter-scale). Three optional steps target it:

1. **05b — segmentation:** FreeSurfer brainstem substructures → a subject-space **brainstem
   mask**; PGlandsSeg → pituitary/pineal volumetry. *These label things; they don't move
   anything.*
2. **05c — refinement:** a **cost-function-masked ANTs SyN** driven by the 05b mask refines
   the brainstem alignment to the template. **This is the actual co-registration
   improvement.**
3. **10b — ROIs in native space:** compose `(fMRIPrep MNI→T1w) ∘ (05c refine)` to bring the
   **Brainstem Navigator** atlas into each subject's native space, then read NTS/LC/raphe
   there.

**Important distinctions:**
- FreeSurfer's brainstem segmentation is **coarse** (midbrain/pons/medulla/SCP) — it does
  **not** label NTS/LC/raphe. The **atlas** provides the named nuclei.
- **Segmentation ≠ registration.** Only 05c improves alignment.
- **Scaffold caveat:** 05c's ANTs parameters and 10b's transform direction/order are
  reasoned but **must be validated on the cluster** with an **atlas-in-native overlay**
  (does the atlas land on the actual brainstem?) before the brainstem ROI numbers are
  trustworthy.

---

## H. QC & housekeeping tools

These **don't change your data** — they check it and write logs (mostly under
`codes/qc/`). Use them throughout.

| Tool (button) | What it checks | Output |
|---|---|---|
| **QC snapshots** | Quick brain-image thumbnails per step (catch gross errors) | image grids in `codes/qc/` |
| **FD QC** | Per-subject head motion (flag only — never exclude) | `qc_fd_summary.json` |
| **Cardinality audit** | Volume/file counts match across **every** stage (dropped/duplicated scans) | `codes/qc/` audit |
| **SDC audit** | Distortion correction was **applied** per run (not just available) | `group_sdc_audit.{csv,md}` |
| **Capture provenance** | Records exact software versions (pipeline commit, fMRIPrep/SPM/MATLAB/RETROICOR/R-DECO, Python) | `codes/qc/provenance/…json` |
| **Cohort report (piezo)** | Lists every run that went respiration-only + BAD/SUSPECT | `group_piezo_qc.{csv,md}` |

**Habit:** after a big step, run the relevant audit and skim `codes/qc/` for `FLAG` /
`MISMATCH`. Capture **provenance once per batch** before generating final results.

---

## I. The flag-and-log rule

This pipeline **never silently stops or excludes**. Instead it **flags + logs + continues**.
That makes *your review* the safety net. Concretely:

- **Where logs live:** mostly under **`codes/qc/`** (audits, piezo reports, provenance, ROI
  geometry checks), plus per-step logs printed to the console and written next to outputs
  (e.g. step05b/05c/10b write a timestamped `.log` in their output dir).
- **What to look for:** lines with **`[FLAG]`**, **`WARNING`**, **`MISMATCH`**,
  **`SKIPPED`** (a *run/sequence* flagged, never a whole subject silently), **`[PIEZO-SKIP]`**
  (respiration-only), **`FMAP_BUT_NO_SDC`** (distortion correction didn't apply).
- **How to skim quickly:** open the relevant `codes/qc/*.csv`/`.md` and look for any status
  ≠ `OK`. For console logs, scan for the words above.
- **The mindset:** "complete ✓" ≠ "perfect." Always check the flags before trusting numbers.
  If you skip the logs, you can miss a flagged problem — that's the one way this design can
  bite you.

---

## J. Where everything lands

```
<project>/
  rawdata/                                   downloaded DICOMs (00)
  sourcedata/                                BIDS dataset (01)
    sub-XXXX/ses-01/{anat,func,fmap}/        per-subject scans + .json
    .heudiconv/                              conversion metadata
    derivatives/
      physio/<subj>/                         physio parse/preproc/RETROICOR (02–04)
        parsed/  preprocessed/  retroicor/output/*_retro-corrected.nii.gz
      physio/first_level/                    shared first-level inputs (06)
      fmriprep/<subj>/                       fMRIPrep outputs (05) + HTML reports
      freesurfer/<subj>/                     recon-all + brainstem/pituitary labels (05b)
      brainstem_coreg/<subj>/                step05c refine warps
      spm/first_level_mni/<subj>/<task>/     GLM — MNI route (07) → step08
      spm/first_level_t1w/<subj>/<task>/     GLM — native route (07) → step10b
      spm/second_level/tasks|groups/         group analysis (08, from first_level_mni)
      spm/second_level/thresholded/          step09 masks/t-maps
      spm/roi/                               step10 roi_values.csv, spheres; step10b native
  codes/qc/                                  ALL QC logs, audits, provenance, reports
  codes/SubjectList.txt, SubjectListBIDS.txt working subject lists
```

---

## K. End-to-end run checklist

A first full run, in order (✅ each before moving on):

1. **Setup** — fill every path (Section D). Set FreeSurfer 8.1 home + brainstem atlas if
   doing the brainstem path.
2. **00** Download → every subject `step0_DONE.txt`.
3. **01** BIDS (build heuristic if needed) → `{anat,func,fmap}` populated; validator clean.
4. **02** Physioparse → `pseudotime_plot.png` aligned; segments parsed.
5. **03** Filter + R-DECO → `*_rdeco.mat` per run; beats look right.
6. **04** Piezo QC Review → save decisions; **Run All** → `*_retro-corrected.nii.gz`.
7. **05** Generate BIDS list → **Run fMRIPrep** (hours/subj) → check HTML + **SDC audit**.
8. **05b/05c** (optional brainstem) → brainstem labels, then refine (verify overlay later).
9. **06** Stim triggers → onsets on edges; motion regressors present.
10. **07** First-level → choose **Space** (MNI default; T1w/both for brainstem 10b) → con/wcon.
11. **08** Part 1 populate → Part 2 cases-vs-controls → review `_contrast_check.csv`.
12. **09** Threshold (pos, one-tailed) → significance mask/t-map.
13. **10** ROI (spheres and/or atlas nuclei) → `roi_values.csv`.
14. **10b** (optional) native-space nuclei → verify atlas-in-native overlay → cohort CSV.
15. **QC pass:** Cardinality audit, FD QC, **Capture provenance**; skim `codes/qc/` flags.

---

## L. Troubleshooting

| You see… | Likely meaning | Do |
|---|---|---|
| `WARNING: … not found` | a Setup path is wrong/empty | fix it in Setup |
| `Missing correct number of … runs` (01) | a scan didn't match the heuristic | rebuild/edit the heuristic, re-run Pass 2 |
| Misaligned segments (02 plot) | wrong/corrupt trigger channel or `.mat` format | re-check the recording; use the GUI for Block1 |
| `RESP is empty/flat` (03) | respiratory channel not recorded | that run can't make respiratory regressors — flagged |
| `WARNING: RPIEZO is flat` (03) | piezo not recorded | route that run respiration-only in step 04 |
| `[PIEZO-SKIP]` (04) | run went respiration-only | expected for messy heartbeat traces |
| fMRIPrep `✗ FAILED` (05) | bad fieldmaps / OOM / missing FS license | check `work_dir` crash logs; raise memory |
| `FMAP_BUT_NO_SDC` (SDC audit) | distortion correction didn't apply | check fieldmaps + `IntendedFor` |
| counts disagree (cardinality audit) | a stage lost/added volumes | open the named step's output |
| empty group task folder (08) | step 07 didn't make `wcon` | re-run 07 producing MNI `wcon` |
| empty mask (09) | no voxels survive | relax p/extent; check tail/contrast index |
| all-NaN ROI values (10) | coord outside brain or wrong `coord_mode` | fix coordinate/mode |
| brainstem ROIs look wrong (10b) | 05c/10b transform not validated | check the atlas-in-native overlay; confirm transform direction/order |

---

## M. FAQ

- **Do I have to run every step?** Run 00–10 in order for the main analysis. **05b/05c/10b
  are optional** (the brainstem-nucleus path). 09/10 are repeated per contrast/peak.
- **Can a subject be dropped automatically?** No. The pipeline flags + logs and keeps going.
  Exclusions, if any, are a human decision after reviewing the flags.
- **Why is the GLM "motion only"?** Because RETROICOR already removed physiology *from the
  image* (steps 02–04) before fMRIPrep. Re-adding physio regressors would double-remove.
- **Why one-tailed?** Preliminary, directional hypothesis (cases > controls). It's the
  intended default — don't switch it to two-tailed.
- **Why a piezo review?** We record a piezo pulse, not ECG; messy traces give bad heartbeats,
  so those runs go respiration-only (flagged).
- **MNI or native for the brainstem?** Native (step 07 T1w → step 10b) is more precise for
  nuclei because it uses the 05c refinement; MNI is fine for the main/cortical analysis.
- **It says "complete ✓" — am I done?** It finished. Now skim the flags/logs before trusting
  the output.
- **Something needs the cluster.** fMRIPrep, FreeSurfer, MATLAB/SPM, ANTs, heudiconv all run
  on the cluster, not a laptop.

---

## N. Data handling & safety

- **PHI / protected files:** the **`docs/protocols/`** folder (acquisition protocol PDFs,
  subject sidecars, the task-reevaluation note) contains identifiers and is **gitignored —
  never commit it.** If you see it staged for commit, unstage it.
- **Commits/pushes:** push only to the intended personal remote; don't push protocol/PHI
  files. Use targeted `git add <files>` (not `git add -A`) so nothing sensitive sneaks in.
- **Don't change the fixed design** without discussion: RETROICOR-before-fMRIPrep, motion-only
  GLM, one-tailed cases>controls, flag-and-log (no auto-exclusion). These are deliberate.
- **When unsure, ask** — and read the matching numbered SOP + `methodology.md` (the
  paper-style Methods) before changing parameters.

---

*This is the complete operator's manual. For exact commands, parameters, and outputs of each
step, see the numbered SOPs (00–10) in this folder; for the scientific Methods, see
`methodology.md`. Pipeline created by Mario Murakami.*
