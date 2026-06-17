# SOP X — The Whole Pipeline, Start to Finish (read me first)

**Who this is for:** a brand-new intern who has never used this pipeline. No prior
fMRI experience assumed. This explains, in plain language, *what the GUI does, button
by button, from raw scanner files to final results.* The other SOPs (00–10) are the
detailed reference for each step; this one is the map.

> **One-line summary:** this software turns the raw 7‑Tesla MRI files from a tVNS
> (vagus‑nerve‑stimulation) study into brain activation maps and brainstem
> measurements — comparing patients ("cases") to "controls."

---

## 0. The 60‑second picture

Think of it as an assembly line. Each **step** takes the output of the previous one
and adds something:

```
DICOMs ─▶ BIDS ─▶ physiology ─▶ RETROICOR (clean) ─▶ fMRIPrep (preprocess)
   00       01       02–04            04                    05
                                                             │
        (optional brainstem extras: 05b segment, 05c refine)│
                                                             ▼
        stim timing + motion ─▶ first‑level ─▶ group ─▶ threshold ─▶ ROIs
              06                    07           08         09        10 / 10b
```

You run the steps **in order, top to bottom.** Each one writes files to disk; the
next one reads them. The GUI is just a friendly set of buttons that runs the
underlying scripts with the paths you set once at the start.

**Two golden rules to remember the whole time:**
1. **Physiology is cleaned BEFORE fMRIPrep** (steps 02–04 happen before 05). This is
   intentional and fixed.
2. **The pipeline never silently throws away a subject.** If something looks wrong it
   **flags it and writes a log, then keeps going.** *Your job is to read those logs.*
   Nothing is "skipped" behind your back — but nothing is auto-rejected either.

---

## 1. Plain‑English glossary (skim this once)

- **DICOM** — the raw file format the MRI scanner spits out. Messy; one folder of
  thousands of little files per scan.
- **BIDS** — a tidy, standard way to organize brain‑imaging files and name them so
  software knows what each scan is. Step 01 converts DICOM → BIDS.
- **BOLD** — the functional MRI signal (blood‑oxygen level); how we infer brain
  activity over time. One "BOLD run" = one task scan.
- **T1w** — a high‑resolution anatomical (structural) image of the brain. Used as the
  map onto which functional data is aligned.
- **Physiology (physio)** — heartbeat and breathing recordings (here: a **piezo**
  pulse sensor for the heartbeat and a belt for breathing). Heart/breathing add noise
  to the BOLD signal.
- **RETROICOR** — a method that uses the physio recordings to **remove** heartbeat/
  breathing noise from the BOLD images (steps 02–04).
- **fMRIPrep** — a widely‑used program that does standard preprocessing: motion
  correction, distortion correction, aligning the BOLD to the T1w and to a common
  template, etc. (step 05). It also runs **FreeSurfer** (brain reconstruction).
- **MNI** — a standard "average brain" coordinate space. Putting everyone in MNI lets
  us compare subjects and report coordinates.
- **GLM / first‑level** — the statistics that estimate, per subject, how much each
  brain voxel responded to the stimulation (step 07). Produces a **contrast** image.
- **Contrast** — the specific comparison, here **"Stim > baseline."**
- **Second‑level / group** — compares cases vs controls across subjects (step 08).
- **Threshold** — keeps only the statistically significant voxels (step 09).
- **ROI** — "region of interest"; a specific area (e.g. a brainstem nucleus) where we
  measure the signal (step 10 / 10b).
- **Brainstem nuclei (NTS / LC / raphe)** — tiny structures deep in the brainstem
  that this study cares about. They are small and hard to image — hence the extra
  brainstem steps (05b/05c/10b).

---

## 2. Before you start

You need:
- Access to the **cluster** (the heavy steps need it — FreeSurfer, fMRIPrep, MATLAB,
  ANTs are installed there, not on a laptop).
- The **subject list** (which people to process).
- Patience: some steps (fMRIPrep especially) take hours per subject.

**Launch the GUI:** run the app (e.g. `python gui/app.py`). A window opens with a list
of steps down the left side and a big **console** at the bottom that shows what's
happening. Click a step on the left to open its panel on the right.

When a step runs, **watch the console.** Lines containing **`WARNING`** or **`[FLAG]`**
are the things to read. A step turning "complete ✓" means it finished — *not* that
everything inside was perfect, so still skim the flags.

---

## 3. Setup tab — do this ONCE

Open **Setup** first and fill in the paths. You set these once and every step reuses
them. The important ones:

- **Raw data path** — where downloaded DICOMs go.
- **BIDS sourcedata** — the tidy dataset folder.
- **SubjectList.txt** — one subject ID per line.
- **fMRIPrep / FreeSurfer dir / SPM12 / MATLAB / Python / environment script** — the
  tool locations on the cluster.
- **FreeSurfer 8.1+ home** — needed for the pituitary segmentation (05b). Point it at
  your FreeSurfer 8.1 install.
- **Brainstem atlas (NIfTI)** — the labeled brainstem‑nuclei map (e.g. Brainstem
  Navigator) used to define NTS/LC/raphe ROIs. Set the path if you have it.

If a path is wrong, the step that needs it will tell you (it won't guess).

---

## 4. The steps, in order

For each step: **what it does → what you click → what "good" looks like.**

### Step 00 — Download DICOMs
**What:** copies the raw scanner files for each subject onto the cluster.
**Click:** Step 00 → choose "All subjects" (or a specific one) → **Run Step 00.**
**Good:** the console shows each subject copied; a `step0_DONE.txt` appears per subject.

### Step 01 — BIDS conversion
**What:** converts the messy DICOMs into the tidy BIDS layout and names every scan
(T1w, rest, BlockStim, ContinuousStim, and the AP/PA "TOPUP" fieldmaps used to fix
image distortion). Uses a "heuristic" (a rules file) to decide which scan is which.
**Click:** Step 01 → Run. (The **Heuristic Builder** panel helps you make/edit the
rules if scans aren't matching.)
**Good:** a `sub-XXXX/` folder appears in sourcedata with `anat/`, `func/`, `fmap/`.
**Watch:** `WARNING: Missing correct number of … runs` means a scan wasn't found —
check the heuristic.

### Step 02 — Physioparse
**What:** takes the raw heartbeat/breathing recording (one big LabChart file) and cuts
it into one piece per scan, lined up in time with the MRI.
**Click:** the Physioparse panel → run its sub‑steps.
**Good:** `parsed/task-*_run-*.mat` files appear; the console reports the expected vs.
actual number of pieces (a mismatch is flagged, not fatal).

### Step 03 — Preprocess + R‑DECO
**What:** filters the heartbeat (piezo) signal and detects heartbeats ("R‑peaks") with
a tool called **R‑DECO** (semi‑automatic — you may eyeball/correct the beats).
**Click:** filter, then run R‑DECO per scan.
**Good:** `*_rdeco.mat` (heartbeat times) exists per scan. A flat/garbage channel is
flagged and that run is skipped (logged), the rest continue.

### Step 04 — RETROICOR (+ Piezo QC review)
**What:** uses the physio to **remove heart/breathing noise from the BOLD**, producing
a "corrected" BOLD that the next step will use.
**Important sub‑tab — "Piezo QC Review":** because we use a piezo pulse (not an ECG),
some recordings are too messy to trust. This tab shows you **a picture of each scan's
heartbeat trace** with a GOOD / SUSPECT / BAD verdict. You decide per scan: **use
cardiac** or **respiration‑only** (skip the unreliable heartbeat). Click **Save
decisions**, and step 04 applies them per run.
**Click:** Piezo QC Review → Run Cardiac QC → Load review → set each scan → Save
decisions → then **Run All** (Parts 1–3).
**Good:** `*_retro-corrected.nii.gz` files appear. Bad‑piezo runs are processed
respiration‑only and logged — never dropped.

### Step 05 — fMRIPrep
**What:** the big standard preprocessing, run on the **corrected** BOLD: fixes
distortion (using the AP/PA fieldmaps), corrects motion and slice timing, aligns BOLD
→ T1w → MNI, and runs FreeSurfer. Takes hours per subject.
**Click:** Step 05 (fMRIPrep panel) → pick subjects → **Run fMRIPrep.**
**Good:** outputs under `derivatives/fmriprep/sub-XXXX/`; open each subject's **HTML
report** to eyeball alignment and distortion correction.
**Also check:** the **corrected‑BIDS audit log** and, with the **SDC audit** button
(in the QC panel), confirm distortion correction was actually applied per run.

### Step 05b — Brainstem & pituitary segmentation (optional extra)
**What:** after fMRIPrep's FreeSurfer finishes, this labels brainstem sub‑parts and
(optionally) the pituitary/pineal glands. The brainstem label becomes the **mask** the
next step uses. *(Segmentation alone does not improve alignment — it sets up 05c.)*
**Click:** in the fMRIPrep panel → "Brainstem segmentation" and/or "Pituitary/pineal."
**Needs:** FreeSurfer 8.1+ (set in Setup) for the pituitary tool; if it's missing,
that button flags‑and‑skips instead of failing.

### Step 05c — Brainstem co‑registration refine (optional extra)
**What:** **this is the step that actually improves brainstem alignment.** It uses the
05b brainstem mask to do a focused re‑alignment of the brainstem to the template, so
small nuclei line up better.
**Click:** fMRIPrep panel → set the MNI template → "Brainstem co‑reg refine."
**Note:** this is a **scaffold** — on the cluster, confirm with an overlay that the
brainstem actually lines up before trusting brainstem ROI numbers.

### Step 06 — Stim triggers + motion
**What:** reads when the stimulation happened (onsets) and builds the **motion
nuisance** (6 head‑motion parameters + spikes for high‑motion volumes). Assembles
everything the GLM needs.
**Click:** Step 06 → Run.
**Good:** `*_bold_stim.txt` (onsets) and `*_motion_regressors.txt` appear. The nuisance
model is intentionally **minimal** (motion only — physio was already removed in 04).

### Step 07 — First‑level GLM (+ 07b)
**What:** per subject, estimates the brain response to stimulation → a **contrast**
image ("Stim > baseline"). By default it works in **MNI space** (directly from
fMRIPrep). A "T1w/native" mode exists too (needed if you want native‑space brainstem
ROIs in 10b).
**Click:** Step 07 → Run.
**Good:** `con_0001.nii` / `wcon_0001.nii` per subject.

### Step 08 — Second‑level (groups)
**What:** combines subjects and compares **cases vs controls.** Optional covariates
(age/sex/motion).
**Click:** Step 08 → Part 1 (gather contrasts) → Part 2 (group test). It checks that
each subject's contrast is really "Stim > baseline" and flags any mismatch (still
copies it — you review).
**Good:** a group `SPM.mat` + statistical maps per task.

### Step 09 — Threshold
**What:** keeps only statistically significant voxels. The study uses a **one‑tailed
(cases > controls)** test by design.
**Click:** Step 09 → set p / correction → Run.
**Good:** a thresholded map + a binary "significant voxels" mask.

### Step 10 — ROI extraction
**What:** measures each subject's signal in a region — either a **sphere** around a
peak coordinate, or **named brainstem nuclei** from the atlas you set in Setup.
**Click:** Step 10 → enter a coordinate (sphere) and/or set the atlas + label
values/names → Run.
**Good:** `roi_values.csv` (one row per subject). A `_roi_geometry_check.csv` flags any
subject whose image geometry differed (it's resampled + flagged, never dropped).

### Step 10b — Native‑space nuclei ROIs (uses 05c)
**What:** the precise brainstem version — warps the atlas into each subject's own space
through the 05c‑refined alignment, then measures NTS/LC/raphe there.
**Click:** Step 10 panel → "Native‑space nuclei ROIs" → set native‑contrast root +
output → Run. (Requires step 07 run in T1w/native mode.)
**Good:** `group_brainstem_nuclei_native.csv`. **First verify** the atlas‑in‑native
overlay looks right (scaffold caveat).

---

## 5. The QC / housekeeping tools (use throughout)

In the **QC panel** (and elsewhere) there are buttons that don't change your data —
they check it and write logs to `codes/qc/`:

- **QC snapshots** — quick brain‑image thumbnails for each step (catch obvious errors).
- **FD QC** — flags subjects with a lot of head motion (flag only; nobody is excluded).
- **Cardinality audit** — checks that the number of volumes matches across every stage
  (catches dropped/duplicated scans).
- **SDC audit** — confirms distortion correction was applied per run.
- **Capture provenance** — records exact software versions for reproducibility (run
  once per batch before generating final results).
- **Cohort report** (piezo) — lists every scan that went respiration‑only.

**Habit:** after a big step, click the relevant audit and skim `codes/qc/` for any
`FLAG`/`MISMATCH`. That's the safety net that replaces hard stops.

---

## 6. Where things land (so you can find outputs)

```
<project>/rawdata/                         downloaded DICOMs (step 00)
<project>/sourcedata/                      BIDS dataset (step 01)
  derivatives/physio/<subj>/               physio, RETROICOR-corrected BOLD (02–04)
  derivatives/fmriprep/<subj>/             fMRIPrep outputs (05); FreeSurfer dir (05b)
  derivatives/brainstem_coreg/<subj>/      step05c refine warps
  derivatives/spm/first_level, second_level/  GLM + group (07–08)
<project>/codes/qc/                        all QC logs, audits, provenance, ROI reports
```

---

## 7. Common problems & what to do

| You see… | Likely meaning | Do |
|---|---|---|
| `WARNING: … not found` | a path in Setup is wrong/empty | fix it in Setup |
| `Missing correct number of … runs` (step 01) | a scan didn't match the heuristic | check/edit the heuristic |
| `[FLAG] … SKIPPED` (steps 02–04) | a physio channel was bad | review that run; the rest continue |
| `[PIEZO-SKIP]` | a scan went respiration‑only | expected for messy heartbeat traces |
| `FMAP_BUT_NO_SDC` (SDC audit) | distortion correction didn't apply | check fieldmaps / `IntendedFor` |
| counts disagree (cardinality audit) | a stage lost/added volumes | open the named step's output |
| brainstem ROI numbers look odd | 05c/10b transform not yet validated | check the atlas‑in‑native overlay |

**Remember:** the pipeline is built to **flag and continue, never silently skip.** If
you don't read the logs, you can miss a flagged problem — so always skim `codes/qc/`
before trusting the final numbers.

---

*This is the orientation map. For the exact commands, parameters, and outputs of each
step, see the numbered SOPs (00–10) in this folder. Pipeline created by Mario
Murakami.*
