# SOP 09 — Threshold a group contrast (p < 0.05)

**Script:** `step09_p_value.sh`
**Scope:** per contrast
**Runs on:** MATLAB + SPM12

---

## 1. Purpose
Threshold a second-level group contrast (default **Cases > Controls**) at
p < 0.05 and write a binary significance map plus a thresholded t-map.

## GUI (in the app)
Open the **Step 09 — Threshold** panel. Set the **analysis dir** (a Step 08b group folder),
**output dir**, **p**, **extent**, **contrast index** (1 = Cases>Controls), **tail**
(default `pos` = cases>controls, the study's one-tailed design), and optional **correction**
(none / FWE / FDR) → **Run** (`step09_p_value.sh`). Outputs a binary significance mask +
thresholded t-map (filenames tagged by the correction used).

## 2. Prerequisites
- A Step 08b group folder containing `SPM.mat` + `spmT_000*.nii`
  (e.g. `…/groups/BlockStim` or `…/groups/Combined_Block_Continuous`).
- SPM12 + `utility/matlab_code/threshold_group_map.m`.

## 3. Inputs / parameters
```
step09_p_value.sh <analysis_dir> <output_dir> \
    [spm_dir] [matlab_exe] [matlab_code_dir] [p] [extent] [contrast_idx] [tail] [env_script] [correction]
```
| Param | Meaning | Default |
|-------|---------|---------|
| `analysis_dir` | Step 08b folder (SPM.mat + spmT) | — (required) |
| `output_dir` | where thresholded maps go | — (required) |
| `p` | p-threshold | `0.05` |
| `extent` | min cluster size (voxels) | `0` |
| `contrast_idx` | spmT index | `1` (Cases>Controls) |
| `tail` | `pos` / `neg` / `two` | `pos` |
| `correction` | `none` / `FWE` / `FDR` — **optional** multiple-comparison correction | `none` |

> **Multiple-comparison correction (optional).** This is a **pilot study**, so the default
> is **`none`** (uncorrected voxel threshold) — acceptable for exploratory pilot maps.
> Opt in to `FWE` (voxel-wise family-wise error, SPM random-field theory via `spm_uc`) or
> `FDR` (false-discovery rate via `spm_uc_FDR`) when you need formal control. Both fall back
> to uncorrected (with a warning) if SPM's RFT fields / FDR helper are unavailable. The
> output filenames are tagged with the method (`unc` / `FWE` / `FDR`) so runs don't overwrite.
>
> **Reporting note (peer review):** `none` is the engineering default, **not** the reporting
> default. For the **brainstem ROI** analysis the *primary* inferential statement should be
> **FDR across the pre-specified nuclei × tasks** (step 10); uncorrected voxelwise maps are
> for **visualization/localization only** (42–44 ROIs × 2 tasks × one-tailed is a large,
> undeclared multiplicity otherwise — Eklund 2016).

## 4. Run
```bash
bash step09_p_value.sh derivatives/spm/second_level/groups/BlockStim \
    derivatives/spm/second_level/thresholded/BlockStim
# custom threshold + cluster extent + two-tailed:
bash step09_p_value.sh <analysis_dir> <output_dir> "" matlab "" 0.05 10 1 two
# opt-in FWE voxel correction:
bash step09_p_value.sh <analysis_dir> <output_dir> "" matlab "" 0.05 0 1 pos "" FWE
```

## 5. Outputs
```
<output_dir>/
  *_<unc|FWE|FDR>_p0.05_mask.nii   (binary significance map; tagged by correction)
  *_<unc|FWE|FDR>_p0.05_tmap.nii   (thresholded t-values)
```

## 6. QC / verification
- Overlay `*_tmap.nii` on the MNI template in SPM/your viewer — clusters are
  anatomically plausible.
- Note the `contrast_idx` you thresholded (1 = Cases>Controls, 2 = Controls>Cases,
  etc. per the Step 08b contrast order).
- Record p, extent, and tail used — these are reporting-critical.

## 7. Troubleshooting
| Symptom | Cause / fix |
|---------|-------------|
| `SPM.mat not found` | Point `analysis_dir` at a Step 08b group folder, not its parent. |
| Empty mask | No voxels survive — relax `p`/`extent`, or check `tail`/`contrast_idx`. |
| `threshold_group_map.m not found` | Check `matlab_code_dir`. |

## 8. Next step
[SOP 10 — ROI extraction](SOP_10_roi.md) — extract values / build spheres at a
peak voxel from this map.
