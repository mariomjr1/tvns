# SOP 07 — First-level SPM GLM + MNI warp

**Scripts:** `step07_firstlevel_mni_v2.sh` (main), `step07b_warp_folder_v2.sh` (single-folder warp)
**Scope:** per study (all subjects in `SubjectListBIDS.txt`)
**Runs on:** MATLAB + SPM12

---

## 1. Purpose
Run a first-level GLM with the Stim condition plus motion (and RETROICOR) nuisance
regressors and build Stim contrasts. **By default** the GLM models the **direct
fMRIPrep MNI152NLin2009cAsym BOLD** (`space=MNI`): contrasts are already in MNI
(`con_*` copied to `wcon_*`) with **no SPM renormalisation**. The legacy **native
T1w + SPM unified-segmentation warp** path (`space=T1w`, `do_mni=1`) remains
available as an optional sensitivity/comparison route. Masks and BOLDs are
**located in place** in `derivatives/fmriprep/` — there is no copy step.

## 2. Prerequisites
- Step 02 (fMRIPrep T1w BOLD + brain mask) and Step 06 (`first_level/` inputs).
- `utility/SubjectListBIDS.txt` (from Step 02 Part 1).
- SPM12 + `utility/matlab_code/glm_spm_firstlevel_mni_v2.m`.

## 3. Inputs / parameters
```
step07_firstlevel_mni_v2.sh \
    [subject_list] [sourcedata_dir] [firstlevel_dir] [output_dir] [spm_dir] \
    [matlab_exe] [matlab_code_dir] [session] [run] [tr] [smooth_fwhm] \
    [do_mni] [env_script] [warp_only] [use_sourcedata]
```
| Param | Meaning | Default |
|-------|---------|---------|
| `subject_list` | BIDS subject list | `utility/SubjectListBIDS.txt` |
| `firstlevel_dir` | Step 06 inputs | `derivatives/physio/first_level` |
| `output_dir` | GLM output root | `derivatives/spm/first_level` |
| `tr` | TR (s) | `1.19` |
| `smooth_fwhm` | smoothing (mm, isotropic) | `3` |
| `do_mni` | 1 = SPM-warp the **T1w** con to MNI (`wcon_*.nii`); legacy `space=T1w` only | `1` |
| `warp_only` | 1 = warp existing con, skip GLM | `0` |
| `use_sourcedata` | 1 = read stim from `…/stimtrigger/` | `0` |
| `space` (arg 16) | first-level space: `MNI` / `T1w` / `both` | `MNI` |

> **First-level space (Task 06).** Choose where the GLM is modeled:
> - **`MNI`** (default, preferred): model the fMRIPrep **MNI152NLin2009cAsym** BOLD directly
>   (physio-clean, since fMRIPrep ran on the RETROICOR-corrected data). The `con_*.nii` are
>   **already in MNI** and are copied to `wcon_*.nii` for the group step — **no SPM
>   segment-normalisation** (avoids the weaker/inconsistent double-normalisation). `do_mni` is
>   ignored for this space.
> - **`T1w`** (optional legacy): model the fMRIPrep T1w BOLD → `con_*.nii` (native); if `do_mni=1`,
>   SPM segments the T1 and warps them to `wcon_*.nii` (MNI). This is the original
>   double-normalisation route, kept for sensitivity/comparison.
> - **`both`**: T1w in `<subj>/<task>/` (con + optional warped wcon) **and** MNI in
>   `<subj>/<task>/mni/` (con + wcon). Lets you compare the two normalisation routes.
>
> The SPM T1w→MNI warp is **optional** (`do_mni`, legacy `space=T1w`); the per-task
> `wcon_0001.nii` that Step 08 consumes is produced by whichever route you pick.

## 4. Run
```bash
# Default: GLM on the direct fMRIPrep MNI BOLD for all subjects:
bash step07_firstlevel_mni_v2.sh

# Legacy T1w + SPM-warp route (arg 16 = T1w, do_mni stays 1):
bash step07_firstlevel_mni_v2.sh "" "" "" "" "" matlab "" 01 01 1.19 3 1 "" 0 0 T1w

# Warp-only over the whole step07 tree (skip GLM; legacy T1w route):
bash step07_firstlevel_mni_v2.sh "" "" "" "" "" matlab "" 01 01 1.19 3 1 "" 1
```
Per subject × task the MATLAB function locates the BOLD + brain mask → smooths
BOLD, reslices mask → specifies + estimates GLM → builds contrasts. In the default
**MNI** route the `con_*.nii` are already in MNI and are copied to `wcon_*.nii`
(no warp). In the legacy **T1w** route it additionally segments the T1 and warps
`con_*.nii` to `wcon_*.nii`.

## 5. Outputs
```
derivatives/spm/first_level/<subj>/<task>/
  SPM.mat
  con_0001.nii   (Stim > baseline)
  con_0002.nii   (Stim < baseline)
  wcon_0001.nii  wcon_0002.nii   (MNI-warped; needed for group analysis)
```

## 6. step07b — warp one already-done folder
Use when you have a single first-level folder of native-space `con_*.nii` and a
matching T1 and just want MNI warps (no GLM re-run):
```bash
bash step07b_warp_folder_v2.sh <con_dir> <t1_file> <output_dir> \
    [spm_dir] [matlab_exe] [matlab_code_dir] [con_pattern] [env_script]
# con_pattern default: con_*.nii ; output_dir = con_dir → warp in place
```
For warping the **whole** step07 `<subj>/<task>` tree, prefer
`step07_firstlevel_mni_v2.sh` with `warp_only=1` instead.

## 7. QC / verification
- Open `SPM.mat` design in SPM — Stim regressor + motion/RETROICOR nuisance look
  correct; estimation converged.
- View `con_0001.nii` (native) and `wcon_0001.nii` (MNI) — sensible activation,
  correct alignment to the MNI template.

## 8. Troubleshooting
| Symptom | Cause / fix |
|---------|-------------|
| `.m not found` | Wrong `matlab_code_dir` / SPM not on PATH. |
| Missing BOLD or mask | fMRIPrep incomplete for subject — re-run Step 02. |
| Stim file not found | Check `firstlevel_dir/01_stim_onsets/` (Step 06) or set `use_sourcedata=1`. |
| Env-script abort | Pass `env_script="none"` if `matlab` is already on PATH. |

## 10. `rest` is a baseline (not a Stim contrast)
`rest` carries no stimulus, so there is no meaningful *Stim > baseline* contrast for it. The
default `Tasks` are `{BlockStim, ContinuousStim}`; the GLM **skips** any task named `rest`
(with a note) and produces no `rest` first-level/group folder. If you want a resting analysis
(e.g. connectivity or fluctuation amplitude), that is a **separate model**, not this Stim GLM.

## 9. Next step
[SOP 08 — Second-level (groups)](SOP_08_secondlevel.md)
