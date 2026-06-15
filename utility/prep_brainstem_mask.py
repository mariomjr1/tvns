#!/usr/bin/env python3
"""
prep_brainstem_mask.py   (Task 05, Part C1)
Turn a user-supplied brainstem atlas/mask into a clean binary mask on the grid of
the images you will analyse — so it can be used as the explicit mask for the
first-level GLM (step07), the group design + threshold (step08/09), and ROI
extraction (step10), restricting the whole analysis to the brainstem.

You provide the atlas (BYO — this script makes no assumptions about which one):
  * binary mask, OR
  * probabilistic atlas (use --threshold), OR
  * labeled atlas (use --labels to pick nuclei by integer value).
It is resampled (nearest-neighbour) to the --reference grid (e.g. a wcon_*.nii or
the fMRIPrep MNI BOLD), optionally dilated, and written as a uint8 binary mask.

  RECOMMENDED BRAINSTEM ATLASES (download/generate, MNI space):
    - Brainstem Navigator (Bianciardi lab): probabilistic brainstem nuclei
      (NTS/NST, LC, raphe, PAG, PBN, ...) — https://www.nitrc.org/projects/brainstemnavig
    - Harvard Ascending Arousal Network (AAN) atlas (LC, raphe, PBN, PAG, ...)
    - SUIT brainstem/cerebellum, or a hand-drawn brainstem ROI in MNI152NLin2009cAsym.
  Make sure the atlas is in the SAME MNI variant as your data (fMRIPrep =
  MNI152NLin2009cAsym); --reference handles grid/resolution differences.

Usage:
  prep_brainstem_mask.py --atlas ATLAS.nii --reference REF.nii --output brainstem_mask.nii \
      [--threshold 0.0] [--labels 7 8 9] [--dilate 0]

Created by Mario Murakami  (brainstem mask — Task 05 Part C1)
"""

import argparse
import sys
import numpy as np

try:
    import nibabel as nib
    from nibabel.processing import resample_from_to
except ImportError:
    print("ERROR: nibabel (with nibabel.processing/scipy) required.", file=sys.stderr)
    sys.exit(1)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--atlas", required=True, help="brainstem atlas/mask NIfTI (MNI)")
    ap.add_argument("--reference", required=True,
                    help="NIfTI defining the target grid (e.g. a wcon_*.nii / fMRIPrep MNI BOLD)")
    ap.add_argument("--output", required=True, help="output binary mask NIfTI")
    ap.add_argument("--threshold", type=float, default=0.0,
                    help="probabilistic atlas: keep voxels > threshold (default 0 = any nonzero)")
    ap.add_argument("--labels", nargs="*", type=int, default=None,
                    help="labeled atlas: keep these integer label values (union)")
    ap.add_argument("--dilate", type=int, default=0,
                    help="optional binary dilation in voxels (default 0)")
    args = ap.parse_args()

    atlas = nib.load(args.atlas)
    ref = nib.load(args.reference)
    adata = np.asanyarray(atlas.dataobj)
    if adata.ndim == 4:        # take first volume of a 4D atlas
        adata = adata[..., 0]

    # Build the boolean mask in the ATLAS grid first
    if args.labels:
        bool_mask = np.isin(np.rint(adata).astype(np.int64), args.labels)
        how = f"labels {args.labels}"
    else:
        bool_mask = adata > args.threshold
        how = f"value > {args.threshold}"
    n_atlas = int(bool_mask.sum())
    if n_atlas == 0:
        print(f"ERROR: selection ({how}) produced an empty mask from {args.atlas}",
              file=sys.stderr)
        sys.exit(1)

    # Resample to the reference grid (nearest-neighbour — it's a label/mask)
    mask_img = nib.Nifti1Image(bool_mask.astype(np.uint8), atlas.affine, atlas.header)
    res = resample_from_to(mask_img, (ref.shape[:3], ref.affine), order=0)
    out = np.asanyarray(res.dataobj) > 0

    if args.dilate and args.dilate > 0:
        try:
            from scipy.ndimage import binary_dilation
            out = binary_dilation(out, iterations=int(args.dilate))
        except ImportError:
            print("WARN: scipy not available — skipping dilation.", file=sys.stderr)

    n_out = int(out.sum())
    if n_out == 0:
        print("ERROR: mask is empty after resampling to the reference grid "
              "(check the atlas is in the same space as --reference).", file=sys.stderr)
        sys.exit(1)

    nib.save(nib.Nifti1Image(out.astype(np.uint8), ref.affine, ref.header), args.output)
    print(f"[OK] brainstem mask -> {args.output}")
    print(f"     selection: {how}  | atlas voxels: {n_atlas}  | on reference grid: {n_out}"
          + (f"  | dilated {args.dilate}vox" if args.dilate else ""))
    print(f"     grid: shape {tuple(ref.shape[:3])} matches reference ✓")


if __name__ == "__main__":
    main()
