# SOP 08 — Second-level (group) analysis

**Scripts:** `step08a_populate_v2.sh` (Part 1), `step08b_groups_v2.sh` (Part 2),
`step08_secondlevel_v2.sh` (alternative one-sample route)
**Scope:** per study
**Runs on:** MATLAB + SPM12

---

## 1. Purpose
Aggregate first-level contrast images into group statistics. The standard route is
**08a → 08b**: 08a gathers per-subject contrasts into per-task folders; 08b runs
two-sample **cases-vs-controls** t-tests. `step08_secondlevel_v2.sh` is an
alternative that runs **one-sample** group t-tests per task.

> Group tests require MNI space — use the `wcon_*.nii` from Step 07's MNI warp,
> **not** the native-space `con_*.nii`.

---

## GUI (in the app)
Open the **Step 08 — Second-level** panel.
1. **Part 1 — Populate:** set the Step 07 first-level root + task root + contrast name
   (`wcon_0001.nii`) → **▶ Run Part 1** (`step08a_populate_v2.sh`; verifies each subject's
   contrast by name and flags mismatches to `_contrast_check.csv` — review it).
2. **Part 2 — Group analysis:** set the **Cases** and **Controls** lists, optional
   **covariates** (`age,sex,mean_fd`), the combined mode, and the group output dir → **▶ Run
   Part 2** (`step08b_groups_v2.sh`). Produces per-task + combined cases-vs-controls maps.

---

## Part 1 — `step08a_populate_v2.sh` (populate task folders)

### Inputs
```
step08a_populate_v2.sh <firstlevel_root> <output_dir> \
    [spm_dir] [matlab_exe] [matlab_code_dir] [con_name] [env_script]
```
| Param | Meaning | Default |
|-------|---------|---------|
| `firstlevel_root` | Step 07 output (`<subj>/<task>/<con>`) | — (required) |
| `output_dir` | per-task group folders | — (required) |
| `con_name` | contrast image to gather | `wcon_0001.nii` (MNI) |

### Run
```bash
bash step08a_populate_v2.sh derivatives/spm/first_level derivatives/spm/second_level/tasks
```
### Output
```
<output_dir>/<task>/<subject>.nii
<output_dir>/<task>/_subjects.txt
```

---

## Part 2 — `step08b_groups_v2.sh` (cases vs controls)

### Inputs
```
step08b_groups_v2.sh <task_root> <cases_list> <controls_list> <output_dir> \
    [spm_dir] [matlab_exe] [matlab_code_dir] [do_combined] [env_script] [combined_mode]
```
- `task_root` — the 08a `<output_dir>` (`<task>/<subject>.nii`)
- `cases_list` / `controls_list` — text files, one BIDS subject per line
- `do_combined` (default `1`) — also run a combined Block+Continuous analysis
- `combined_mode` (default **`average`**) — how the combined analysis treats the two
  conditions:
  - **`average`** (default, recommended): average each subject's BlockStim + ContinuousStim
    into **one** image, then the two-sample test → **one observation per subject**
    (preserves independence).
  - `pool` (legacy, optional): enters **both** conditions per subject — this **double-counts**
    subjects, inflates the df and false positives. Kept only for backward comparison.

> The per-task analyses (BlockStim, ContinuousStim) are unaffected — this option only
> changes the **Combined_Block_Continuous** folder. **`rest` is a resting baseline** and is
> excluded from the default `Tasks` (no Stim contrast); group folders are produced only for
> BlockStim/ContinuousStim/Combined.

- `covariates` (default `none`) — **optional** nuisance covariates, a comma list of
  `age`, `sex`, `mean_fd` (e.g. `"age,sex,mean_fd"`). `age`/`sex` come from the BIDS
  `participants.tsv`; `mean_fd` is computed from the fMRIPrep confounds — both assembled by
  `utility/build_group_covariates.py` into `<output_dir>/group_covariates.tsv`. Covariates are
  mean-centered and applied to **all** two-sample tests (per-task + combined). A covariate that
  is incomplete for the analysed subjects is dropped (with a warning). Provide `participants_tsv`
  + `fmriprep_dir`, or `sourcedata` (arg 14) to auto-derive both.

### Run
```bash
bash step08b_groups_v2.sh derivatives/spm/second_level/tasks \
    cases.txt controls.txt derivatives/spm/second_level/groups
```
### Output
```
<output_dir>/<task>/SPM.mat + spmT_000*.nii
  Contrasts: Cases>Controls, Controls>Cases, Cases mean, Controls mean
```

---

## Alternative — `step08_secondlevel_v2.sh` (one-sample per task)

Point at the three task folders directly; each is searched recursively for
`con_name` (one per subject) and entered into a one-sample t-test. Runs
BlockStim, ContinuousStim, rest, and (optional) a pooled Combined analysis.
```
step08_secondlevel_v2.sh <block_dir> <continuous_dir> <rest_dir> <output_dir> \
    [spm_dir] [matlab_exe] [matlab_code_dir] [con_name] [do_combined]
# con_name default con_0001.nii — use wcon_0001.nii for MNI; "" skips a task
```

## QC / verification
- Confirm each task folder has the expected subject count (`_subjects.txt`).
- Verify cases/controls lists match enrolled subjects; no subject in both.
- Inspect `spmT_000*.nii` in SPM for each contrast.
- **Contrast identity (Task 27):** Part 1 copies `con_name` (default `wcon_0001.nii`,
  index 1) and verifies it against each subject's `SPM.mat` `xCon(1).name`
  (expected `Stim > baseline`, set via `ExpectedConName`). Mismatches are written to
  `<group_out>/_contrast_check.csv` with a `[FLAG]` line — the subject is **copied
  anyway, never skipped** (flag + log). **Review `_contrast_check.csv` for any status
  ≠ OK before trusting group stats** (a `MISMATCH` means a different contrast, e.g.
  if a pmod GLM reordered contrasts).

## Troubleshooting
| Symptom | Cause / fix |
|---------|-------------|
| `.m not found` | Check `matlab_code_dir` (`secondlevel_populate_tasks.m`, `glm_spm_secondlevel_groups.m`, `glm_spm_secondlevel_v2.m`). |
| Empty task folder | Step 07 didn't produce `wcon_*.nii` — re-run Step 07 with `do_mni=1`. |
| Subject in list but no image | Name mismatch — check `_subjects.txt` vs list. |

## Next step
[SOP 09 — Threshold p<0.05](SOP_09_threshold.md)
