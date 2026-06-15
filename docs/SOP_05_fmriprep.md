# SOP 05 — fMRIPrep (name correction + preprocessing + QC)

**Script:** `step05_fmriprep_v2.sh`
**Scope:** per study
**Runs on:** the cluster (needs Singularity/Apptainer and `/autofs` mounts)

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

## 8. Troubleshooting
| Symptom | Cause / fix |
|---------|-------------|
| `✗ FAILED: sub-…` | See `work_dir` crash logs; common causes: bad fieldmaps, OOM (raise `--mem_mb`), missing FS license. |
| GIF step skipped | fMRIPrep output for that run not found / Python env missing — non-fatal. |
| No MNI BOLD in QC | Normalisation failed — inspect HTML report, re-run subject. |
| Validator reports issues | Fix in BIDS (Step 01) before continuing. |

## 9. Next step
[SOP 06 — Stim Triggers](SOP_06_stim_triggers.md) (physiological branch).
