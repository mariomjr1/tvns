#!/usr/bin/env python3
"""
roi_extract.py   (Step 10)

Given a coordinate (an ROI peak, e.g. from the step09 significance map):

  1. Extract the single-voxel value of each subject's wcon image -> CSV column.
  2. Build a 5 mm sphere around the coordinate; extract each subject's mean
     wcon value inside it -> CSV column.
  3. Write 5 mm and 10 mm sphere masks as NIfTI.
  4. Apply the 10 mm sphere mask to a manually-selected con_0001.nii
     (keeps the values inside the sphere, zeros elsewhere).

The wcon images are assumed to share a common space (MNI), one file per
subject (e.g. the per-task folder from step08 Part 1: <task>/<subject>.nii).

Coordinates default to MNI mm; pass --voxel to give voxel indices instead.

Usage:
  python roi_extract.py --coord X Y Z --wcon-dir DIR --output-dir OUT \
         [--con CON_FILE] [--radii 5 10] [--voxel]

Outputs (in OUT):
  roi_values.csv                 subject, voxel_value, sphere<r0>mm_mean
  sphere_<r>mm_x_y_z.nii         one mask per radius
  <conbase>_masked_<r1>mm.nii    con masked by the largest sphere (if --con)

Created by Mario Murakami
"""

import argparse
import csv
import glob
import os
import sys
import numpy as np

try:
    import nibabel as nib
except ImportError:
    print("ERROR: nibabel not installed (pip install nibabel)", file=sys.stderr)
    sys.exit(1)


def mm_to_vox(affine, xyz_mm):
    inv = np.linalg.inv(affine)
    v = inv @ np.array([xyz_mm[0], xyz_mm[1], xyz_mm[2], 1.0])
    return v[:3]


def vox_to_mm(affine, ijk):
    v = affine @ np.array([ijk[0], ijk[1], ijk[2], 1.0])
    return v[:3]


def build_sphere_mask(shape, affine, center_mm, radius_mm):
    """Boolean mask of voxels whose mm-center is within radius_mm of center_mm."""
    i = np.arange(shape[0])
    j = np.arange(shape[1])
    k = np.arange(shape[2])
    I, J, K = np.meshgrid(i, j, k, indexing="ij")
    # mm coordinates of every voxel
    X = affine[0, 0] * I + affine[0, 1] * J + affine[0, 2] * K + affine[0, 3]
    Y = affine[1, 0] * I + affine[1, 1] * J + affine[1, 2] * K + affine[1, 3]
    Z = affine[2, 0] * I + affine[2, 1] * J + affine[2, 2] * K + affine[2, 3]
    d2 = (X - center_mm[0]) ** 2 + (Y - center_mm[1]) ** 2 + (Z - center_mm[2]) ** 2
    return d2 <= (radius_mm ** 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--coord", nargs=3, type=float, required=True,
                    metavar=("X", "Y", "Z"),
                    help="Coordinate (MNI mm by default; voxel indices with --voxel)")
    ap.add_argument("--wcon-dir", required=True,
                    help="Folder of per-subject wcon images (<subject>.nii)")
    ap.add_argument("--wcon-glob", default="*.nii",
                    help="Glob for the wcon images in --wcon-dir (default *.nii)")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--con", default=None,
                    help="A con image to mask with the largest sphere (manually selected)")
    ap.add_argument("--radii", nargs="+", type=float, default=[5.0, 10.0],
                    help="Sphere radii in mm (default 5 10). "
                         "Smallest is used for the per-subject mean; largest masks the con.")
    ap.add_argument("--voxel", action="store_true",
                    help="Interpret --coord as voxel indices instead of MNI mm")
    ap.add_argument("--group-con", default=None,
                    help="A group-comparison contrast image to mask (manually selected, "
                         "e.g. spmT_0001.nii / con_0001.nii from step08b)")
    ap.add_argument("--group-mask", default=None,
                    help="Mask to apply to --group-con (manually selected). If omitted, "
                         "the auto-generated large sphere is used.")
    args = ap.parse_args()

    os.makedirs(args.output_dir, exist_ok=True)

    wcons = sorted(glob.glob(os.path.join(args.wcon_dir, args.wcon_glob)))
    # exclude the masks we may have written before
    wcons = [w for w in wcons if "sphere_" not in os.path.basename(w)
             and "_masked_" not in os.path.basename(w)]
    if not wcons:
        print(f"ERROR: no wcon images matching {args.wcon_glob} in {args.wcon_dir}",
              file=sys.stderr)
        sys.exit(1)

    # Reference geometry from the first wcon
    ref = nib.load(wcons[0])
    affine = ref.affine
    shape = ref.shape[:3]

    # Resolve the coordinate to both voxel and mm
    if args.voxel:
        ijk = np.round(np.array(args.coord)).astype(int)
        center_mm = vox_to_mm(affine, ijk)
    else:
        center_mm = np.array(args.coord, dtype=float)
        ijk = np.round(mm_to_vox(affine, center_mm)).astype(int)

    print(f"Coordinate: MNI mm = {center_mm.tolist()}  |  voxel = {ijk.tolist()}")
    if not (0 <= ijk[0] < shape[0] and 0 <= ijk[1] < shape[1] and 0 <= ijk[2] < shape[2]):
        print("ERROR: voxel is outside the image bounds.", file=sys.stderr)
        sys.exit(1)

    radii = sorted(args.radii)
    r_small = radii[0]
    r_large = radii[-1]

    # Build sphere masks (one per radius) and save them
    masks = {}
    for r in radii:
        m = build_sphere_mask(shape, affine, center_mm, r)
        masks[r] = m
        out = os.path.join(
            args.output_dir,
            f"sphere_{int(r)}mm_{int(round(center_mm[0]))}_"
            f"{int(round(center_mm[1]))}_{int(round(center_mm[2]))}.nii")
        nib.save(nib.Nifti1Image(m.astype(np.uint8), affine, ref.header), out)
        print(f"  sphere {int(r)}mm: {int(m.sum())} voxels -> {out}")

    small_mask = masks[r_small]

    # ── Extract per-subject values ────────────────────────────────────────────
    rows = []
    for w in wcons:
        subj = os.path.splitext(os.path.basename(w))[0]
        img = nib.load(w)
        data = np.asanyarray(img.dataobj, dtype=np.float64)
        if data.shape[:3] != shape:
            print(f"  WARNING: {subj} shape {data.shape[:3]} != ref {shape}; skipping")
            continue
        vox_val = float(data[ijk[0], ijk[1], ijk[2]])
        sphere_vals = data[small_mask]
        sphere_mean = float(np.nanmean(sphere_vals)) if sphere_vals.size else float("nan")
        rows.append((subj, vox_val, sphere_mean))
        print(f"  {subj}: voxel={vox_val:.4f}  sphere{int(r_small)}mm_mean={sphere_mean:.4f}")

    csv_path = os.path.join(args.output_dir, "roi_values.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["subject", "voxel_value", f"sphere{int(r_small)}mm_mean"])
        writer.writerows(rows)
    print(f"\nWrote {len(rows)} rows -> {csv_path}")

    # ── Mask a manually-selected con with the largest sphere ──────────────────
    if args.con:
        if not os.path.isfile(args.con):
            print(f"ERROR: con file not found: {args.con}", file=sys.stderr)
            sys.exit(1)
        con_img = nib.load(args.con)
        con_data = np.asanyarray(con_img.dataobj, dtype=np.float64)
        large_mask = masks[r_large]
        if con_data.shape[:3] != shape:
            # rebuild the mask in the con's own geometry
            large_mask = build_sphere_mask(con_data.shape[:3], con_img.affine,
                                           center_mm, r_large)
        masked = np.zeros_like(con_data)
        masked[large_mask] = con_data[large_mask]
        base = os.path.splitext(os.path.basename(args.con))[0]
        out = os.path.join(args.output_dir, f"{base}_masked_{int(r_large)}mm.nii")
        nib.save(nib.Nifti1Image(masked, con_img.affine, con_img.header), out)
        print(f"Masked con ({int(r_large)}mm sphere) -> {out}")

    # ── Mask a manually-selected GROUP-comparison contrast ────────────────────
    if args.group_con:
        if not os.path.isfile(args.group_con):
            print(f"ERROR: group-con not found: {args.group_con}", file=sys.stderr)
            sys.exit(1)
        gimg = nib.load(args.group_con)
        gdata = np.asanyarray(gimg.dataobj, dtype=np.float64)

        # Mask: manually selected file, else the auto-generated large sphere
        if args.group_mask:
            if not os.path.isfile(args.group_mask):
                print(f"ERROR: group-mask not found: {args.group_mask}", file=sys.stderr)
                sys.exit(1)
            mimg = nib.load(args.group_mask)
            gmask = np.asanyarray(mimg.dataobj) > 0
            mask_label = os.path.basename(args.group_mask)
        else:
            gmask = masks[r_large]
            mask_label = f"auto {int(r_large)}mm sphere"

        if gmask.shape[:3] != gdata.shape[:3]:
            print(f"ERROR: group-con shape {gdata.shape[:3]} != mask shape "
                  f"{gmask.shape[:3]} — they must be in the same space.", file=sys.stderr)
            sys.exit(1)

        gmasked = np.zeros_like(gdata)
        gmasked[gmask] = gdata[gmask]
        gbase = os.path.splitext(os.path.basename(args.group_con))[0]
        gout = os.path.join(args.output_dir, f"{gbase}_groupmasked.nii")
        nib.save(nib.Nifti1Image(gmasked, gimg.affine, gimg.header), gout)
        n_in = int(np.count_nonzero(gmask))
        print(f"Masked group contrast with {mask_label} ({n_in} voxels) -> {gout}")

    print("Done.")


if __name__ == "__main__":
    main()
