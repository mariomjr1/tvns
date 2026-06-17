# SOP 10 — ROI value extraction + spheres

**Script:** `step10_ROI.sh`
**Scope:** per peak coordinate
**Runs on:** Python (`utility/roi_extract.py`)

---

## 1. Purpose
At a chosen coordinate (e.g. a peak from the Step 09 significance map):
1. extract each subject's single-voxel `wcon` value → CSV
2. build a small (5 mm) sphere and extract each subject's mean `wcon` → CSV
3. write 5 mm and 10 mm spherical masks (NIfTI)
4. optionally mask a chosen `con`/group contrast with the large sphere

## GUI (in the app)
Open the **Step 10 — ROI** panel.
- **Coordinate (sphere):** enter **X/Y/Z** + **mode** (mm/voxel), the **wcon dir** (a Step
  08a task folder), **output dir**, and the sphere radii.
- **Mask / atlas ROIs** (coordinate-free): set a **ROI mask** and/or the **Labeled atlas**
  (bound to Setup's `brainstem_atlas`) + **label values/names** → one mean column per
  nucleus. Leave X/Y/Z empty to run these alone.
- **▶ Run Step 10 — Extract** (`step10_ROI.sh`) → `roi_values.csv`.
- **Native-space nuclei ROIs (step10b):** set the **native con root** + glob + output →
  **▶ Atlas→native ROIs** (`step10b_atlas_native_roi_v2.sh`; warps the atlas into native
  space via the step05c refinement — needs Step 07 run with Space=T1w). See §6b.

## 2. Prerequisites
- A peak coordinate (MNI mm, or voxel indices).
- A folder of per-subject MNI contrast images (`<subject>.nii`) — typically a
  Step 08a task folder (`tasks/<task>/`).
- `utility/roi_extract.py`.

## 3. Inputs / parameters
```
step10_ROI.sh <X> <Y> <Z> <wcon_dir> <output_dir> \
    [con_file] [r_small] [r_large] [coord_mode] [python_exe] [group_con] [group_mask] [sig_mask]
```
| Param | Meaning | Default |
|-------|---------|---------|
| `X Y Z` | coordinate | — (required) |
| `wcon_dir` | per-subject `wcon` images | — (required) |
| `output_dir` | results | — (required) |
| `con_file` | con image to mask with large sphere | (optional) |
| `r_small` / `r_large` | sphere radii (mm) | `5` / `10` |
| `coord_mode` | `mm` (MNI) or `voxel` | `mm` |
| `group_con` / `group_mask` | group contrast + mask to apply | (optional) |
| `sig_mask` | **optional** significance mask (e.g. a Step 09 corrected `*_mask.nii`) — adds a per-subject sphere mean restricted to significant voxels | (optional) |
| `roi_mask` (arg 14) | **optional** whole-mask ROI (e.g. the brainstem mask) — per-subject MEAN inside it → CSV column. **Coordinate-free** (X/Y/Z may be empty) | (optional) |
| `roi_atlas` (arg 15) | **optional** labeled atlas — per-subject mean within each label → one column per nucleus | (optional) |
| `roi_labels` (arg 16) | label values to extract from `roi_atlas` (space/comma list, e.g. `"7,3"`) | (all nonzero) |
| `roi_label_names` (arg 17) | optional names for the labels (same order, e.g. `"NTS,LC"`) | (label#) |

> **Mask / atlas ROIs (Task 05 C4)** are coordinate-free — give a `roi_mask` (whole-mask mean,
> e.g. the brainstem mask from the Brainstem Mask tool) and/or a `roi_atlas` + `roi_labels` for
> per-nucleus means (NTS/LC/raphe…). Resampled nearest-neighbour to the wcon grid. The GUI has
> a "Mask / atlas ROIs" section; leave X/Y/Z empty to run these alone.

## 4. Run
```bash
bash step10_ROI.sh -6 -40 -20 \
    derivatives/spm/second_level/tasks/BlockStim \
    derivatives/spm/second_level/roi/BlockStim_peak1 \
    derivatives/spm/first_level/sub-XXXX/BlockStim/con_0001.nii 5 10 mm
```

## 5. Outputs
```
<output_dir>/
  roi_values.csv               (voxel value + 5mm-sphere mean, per subject)
  _roi_geometry_check.csv      (per-subject geometry/affine status — Task 24)
  sphere_5mm_*.nii  sphere_10mm_*.nii   (masks)
  *_masked_10mm.nii            (con masked by 10 mm sphere; if con_file given)
  *_groupmasked.nii            (group contrast masked; if group_con/mask given)
```
> With `sig_mask`, `roi_values.csv` gains a `sphere<r>mm_sig_mean` column — the per-subject
> sphere mean restricted to voxels significant in the Step 09 corrected map.

## 6. QC / verification
- **Geometry/counts (Task 24):** a subject whose image geometry/affine differs from
  the reference is **resampled onto the reference grid and `[FLAG]`-ged, never
  skipped**; an unreadable image gets a NaN row. Every input image becomes one row
  (expected == analyzed — no silent omission). Check `_roi_geometry_check.csv` for
  any status ≠ OK (RESAMPLED / ERROR) before trusting the ROI stats.
- Open `roi_values.csv` — one row per subject, no NaNs from out-of-brain coords.
- Overlay a sphere mask on the template to confirm it sits at the intended peak.
- Confirm `coord_mode` matches how you read the peak (mm vs voxel) — a mismatch
  silently places the ROI in the wrong location.

## 6b. Brainstem-nuclei ROIs (atlas + native-space step10b)
For named brainstem nuclei (NTS/LC/raphe), use a labeled atlas instead of spheres:
- **Direct (MNI) — works now:** set **`brainstem_atlas`** in Setup (a labeled NIfTI,
  e.g. Brainstem Navigator, in MNI). The step10 ROI panel's "Labeled atlas" field is
  bound to it; add the label values/names → one mean column per nucleus. With the
  default MNI first level (Task 22) the atlas aligns to the `wcon` images by grid
  resampling.
- **Native space (uses step05c) — `step10b_atlas_native_roi_v2.sh`:** warps the atlas
  into each subject's native T1w space via the composed transform (fMRIPrep MNI→T1w ∘
  step05c refine), then extracts per-nucleus means from the native (T1w-space)
  contrast → `group_brainstem_nuclei_native.csv`. **Requires step07 run with Space=`T1w`
  or `both`.** GUI: RoiPanel "Native-space nuclei ROIs (step10b)" sub-frame/button — its
  **native con root defaults to `derivatives/spm/first_level_t1w`** (the native route) and
  output to `derivatives/spm/roi/brainstem_native`.
  **SCAFFOLD:** verify the atlas-in-native overlay on the cluster before trusting the
  numbers (the transform direction/order must be confirmed there).

## 7. Troubleshooting
| Symptom | Cause / fix |
|---------|-------------|
| `wcon directory not found` | Point `wcon_dir` at a Step 08a task folder. |
| All-NaN / zero values | Coordinate outside the image or wrong `coord_mode`. |
| `roi_extract.py not found` | Check `utility/roi_extract.py` exists. |
| `atlas-in-native` looks misregistered | step10b transform chain — confirm fMRIPrep xfm direction + step05c warp order on cluster. |

## 8. End of pipeline
ROI CSVs feed statistical analysis / figures. See the
[README](README.md) for the full step map.
