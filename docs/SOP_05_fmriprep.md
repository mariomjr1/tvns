# SOP 05 — fMRIPrep (name correction + preprocessing + QC)

**Script:** `step05_fmriprep_v2.sh`
**Scope:** per study
**Runs on:** the cluster (needs Singularity/Apptainer and the project filesystem mounted)

---

## 1. Purpose
A four-part step:
1. **Part 1** — convert raw IDs → BIDS IDs, writing `utility/SubjectListBIDS.txt`.
2. **Part 1.5** — run the BIDS validator on `sourcedata`.
3. **Part 2** — run fMRIPrep (anatomical + functional preprocessing, native T1w +
   MNI152NLin2009cAsym normalisation) sequentially per subject, then build a
   pre/post QC GIF per BOLD run.
4. **Part 3** — QC: mean framewise displacement per subject + MNI-BOLD existence
   check, written to `qc_fd_summary.json`.

## GUI (in the app)
Open the **Step 05 — fMRIPrep** panel (tabs: **Generate BIDS List | fMRIPrep | Pre/Post QC**).
1. **Generate BIDS List:** build `SubjectListBIDS.txt` (every later step consumes it).
2. **fMRIPrep:** confirm the paths (raw + corrected BIDS, derivatives, FreeSurfer dir, work
   dir, Singularity image, FS license), set options (output spaces, memory, slice-timing,
   skip-bids, CIFTI, "Assemble corrected BIDS first"), pick subjects → **▶ Run fMRIPrep**.
   Below the run button: **FreeSurfer segmentation extras (step05b)** — **▶ Brainstem
   segmentation**, **▶ Pituitary/pineal (PGlandsSeg)**, and (after setting an MNI template)
   **▶ Brainstem co-reg refine (step05c)** — see §8b.
3. **Pre/Post QC:** optional before/after GIF.
Then open each subject's fMRIPrep HTML report and run the **SDC audit** (QC panel).

## 2. Prerequisites
- Step 01 complete (BIDS `sourcedata`).
- fMRIPrep Singularity image present (`fmriprep-25.2.3.simg`).
- FreeSurfer license file.
- Adequate scratch space for `work_dir` (intermediate files are large).

## 3. Inputs / paths (edit in script for a new project)
| Variable | Default |
|----------|---------|
| Raw subject list (arg 1) | `utility/SubjectList.txt` |
| `bids_dir` | `<project>/sourcedata` |
| `derivatives_dir` | `<bids_dir>/derivatives/fmriprep` |
| `fs_dir` | `<bids_dir>/derivatives/freesurfer` |
| `work_dir` | `<project>/codes/working-fmriprep` |
| `fmriprep_simg` | `…/my_images/fmriprep-25.2.3.simg` |
| `fs_license` | `…/Pipelines/license.txt` |

## 4. Run
```bash
bash step05_fmriprep_v2.sh
# or:
bash step05_fmriprep_v2.sh /path/to/CustomSubjectList.txt
```
Generates `utility/SubjectListBIDS.txt`, which **all later steps consume.**

## 5. Key fMRIPrep flags
- `--output-spaces T1w MNI152NLin2009cAsym` — native + group space.
- Slice-timing correction is enabled. RETROICOR uses native slice timing for
  physiological phase correction upstream; fMRIPrep still performs neural STC.
- `--cifti-output` — HCP grayordinate output.
- `--skip-bids-validation` (validator already run in Part 1.5).
- `--mem_mb 50000` — workflow memory ceiling.

## 6. Outputs
```
derivatives/fmriprep/sub-<ID>/
  ses-01/func/*_space-T1w_desc-preproc_bold.nii.gz
                *_space-MNI152*_desc-preproc_bold.nii.gz
                *_desc-confounds_timeseries.tsv
  figures/*_prepost_fmriprep.gif        (pre/post QC GIFs)
derivatives/freesurfer/sub-<ID>/         (recon-all)
derivatives/fmriprep/qc_fd_summary.json  (FD + registration QC)
```

## 7. QC / verification
- Open each subject's fMRIPrep **HTML report** (registration, SDC, surfaces).
- Review the pre/post GIFs in `figures/`.
- Check `qc_fd_summary.json`: subjects with **mean FD > 0.9 mm** are flagged;
  any subject "missing MNI BOLD" indicates a registration/normalisation failure.
  **Flag only — no automated motion exclusion** (decision, preliminary data): the
  flag is advisory; high-motion volumes are censored within the GLM by FD spike
  regressors (Step 06), and no subject/run is dropped for motion.
- **SDC verification (Task 16):** QC panel → **"SDC audit"** (or
  `utility/audit_sdc.py <derivatives/fmriprep> --all --bids <sourcedata>`). Per BOLD
  run it confirms SDC was *applied* (not just that an AP/PA fieldmap exists) via the
  `*desc-sdc*.svg` figure / report / sidecar, and flags `FMAP_BUT_NO_SDC`,
  `NO_FIELDMAP`, or `UNKNOWN` → `codes/qc/group_sdc_audit.{csv,md}`. Flag + log only —
  never blocks. The AP/PA TOPUP pair is pulled into `fmap/` as `dir-AP/dir-PA_epi`
  by the heuristic (PEPOLAR), with `IntendedFor` auto-populated for SDC.

## 8. Provenance (Task 29)
Before generating pilot outputs, capture the analysis environment so a scanner/
platform effect is not confused with a software change:

- GUI: **QC panel → "Capture provenance"** (or run `utility/collect_provenance.py`).
- Records: pipeline git commit/branch/dirty, fMRIPrep `.simg` version-from-filename
  (+ optional sha256 with `--hash-simg`), SPM12 + MATLAB versions (via `spm('Ver')`
  and `version` when MATLAB is launched), RETROICOR (`generate_1D_fun_1.m`,
  `retroicor_main_modi.m`) and R-DECO source hashes, and the Python environment.
- Output: `codes/qc/provenance/provenance_<timestamp>.json` (+ `provenance_latest.json`
  and `requirements_frozen_<timestamp>.txt` — the authoritative Python pin).
- Direct Python deps are listed in `utility/requirements.txt`.

Capture once per batch **on each acquisition platform** so the two-platform pilot
(Task 12) can be tied to an identical analysis environment.

> Step 05 Part 1 also writes a corrected-BIDS audit log
> (`derivatives/fmriprep/qc/corrected_bids_audit.csv`, Task 13) — review flagged
> subjects there before trusting fMRIPrep outputs.

## 8b. Optional brainstem & pituitary extras (step05b / step05c)
After fMRIPrep's recon-all, the fMRIPrep panel exposes optional segmentation/refinement
buttons (require **FreeSurfer ≥ 8.1** in Setup; ANTs for 05c). Flag + log + continue.

- **step05b** (`step05b_freesurfer_segment_v2.sh`): **Brainstem segmentation**
  (`segment_subregions brainstem` → subject-space brainstem label/mask, the input to
  05c) and **Pituitary/pineal** (`mri_pglands_seg`, FS ≥ 8.1 → `T1.pglands.mgz` +
  volumes; flag-skips if FS < 8.1). These are segmentation/volumetry — they do NOT
  change co-registration by themselves.
- **step05c** (`step05c_brainstem_coreg_v2.sh`): **Brainstem co-reg refine** — a
  cost-function-masked ANTs SyN driven by the 05b mask that refines brainstem
  alignment (this *is* the co-registration improvement). Consumed by step10b.
  **SCAFFOLD:** confirm ANTs params + fMRIPrep transform filenames on the cluster and
  verify an atlas-in-native overlay before trusting brainstem ROIs.

## 9. Troubleshooting
| Symptom | Cause / fix |
|---------|-------------|
| `✗ FAILED: sub-…` | See `work_dir` crash logs; common causes: bad fieldmaps, OOM (raise `--mem_mb`), missing FS license. |
| GIF step skipped | fMRIPrep output for that run not found / Python env missing — non-fatal. |
| No MNI BOLD in QC | Normalisation failed — inspect HTML report, re-run subject. |
| Validator reports issues | Fix in BIDS (Step 01) before continuing. |

## 10. Next step
[SOP 06 — Stim Triggers](SOP_06_stim_triggers.md) (physiological branch).
