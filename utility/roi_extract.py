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
    from nibabel.processing import resample_from_to
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
    ap.add_argument("--coord", nargs=3, type=float, default=None,
                    metavar=("X", "Y", "Z"),
                    help="Coordinate (MNI mm by default; voxel indices with --voxel). "
                         "Optional if --roi-mask / --roi-atlas is given.")
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
    ap.add_argument("--sig-mask", default=None,
                    help="Optional significance mask (e.g. a step09 corrected *_mask.nii): "
                         "adds a per-subject sphere mean restricted to significant voxels.")
    # ── Mask-based ROIs (Task 05 C4) — coordinate-free ────────────────────────
    ap.add_argument("--roi-mask", nargs="+", default=None,
                    help="One or more binary mask NIfTIs (e.g. the brainstem mask, or a "
                         "single-nucleus mask): per-subject MEAN within each → CSV column.")
    ap.add_argument("--roi-atlas", default=None,
                    help="A labeled atlas NIfTI: per-subject mean within each label (see "
                         "--roi-labels) → one CSV column per nucleus.")
    ap.add_argument("--roi-labels", nargs="+", type=int, default=None,
                    help="Integer label values to extract from --roi-atlas "
                         "(default: all nonzero labels).")
    ap.add_argument("--roi-label-names", nargs="+", default=None,
                    help="Optional names for --roi-labels (same count/order).")
    args = ap.parse_args()

    have_coord = args.coord is not None
    if not (have_coord or args.roi_mask or args.roi_atlas):
        print("ERROR: give --coord and/or --roi-mask / --roi-atlas.", file=sys.stderr)
        sys.exit(1)

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

    # ── Resample helper: bring any mask/atlas onto the reference grid (NN) ─────
    def _to_ref(path):
        im = nib.load(path)
        d = np.asanyarray(im.dataobj)
        if d.ndim == 4:
            d = d[..., 0]
        if im.shape[:3] == shape and np.allclose(im.affine, affine, atol=1e-3):
            return d
        res = resample_from_to(
            nib.Nifti1Image(d.astype(np.float32), im.affine, im.header),
            (shape, affine), order=0)
        return np.asanyarray(res.dataobj)

    masks = {}
    r_small = r_large = None
    center_mm = ijk = None
    if have_coord:
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
        r_small, r_large = radii[0], radii[-1]
        for r in radii:
            m = build_sphere_mask(shape, affine, center_mm, r)
            masks[r] = m
            out = os.path.join(
                args.output_dir,
                f"sphere_{int(r)}mm_{int(round(center_mm[0]))}_"
                f"{int(round(center_mm[1]))}_{int(round(center_mm[2]))}.nii")
            nib.save(nib.Nifti1Image(m.astype(np.uint8), affine, ref.header), out)
            print(f"  sphere {int(r)}mm: {int(m.sum())} voxels -> {out}")
    small_mask = masks[r_small] if have_coord else None

    # ── Optional significance mask (restricts the sphere mean) ─────────────────
    sig_mask = None
    if args.sig_mask and have_coord:
        if not os.path.isfile(args.sig_mask):
            print(f"ERROR: sig-mask not found: {args.sig_mask}", file=sys.stderr)
            sys.exit(1)
        sig_mask = _to_ref(args.sig_mask) > 0.5
        n_sig_in = int(np.count_nonzero(small_mask & sig_mask))
        print(f"  significance mask: {int(sig_mask.sum())} sig voxels; "
              f"{n_sig_in} inside the {int(r_small)}mm sphere")

    # ── Mask-based ROIs (Task 05 C4): whole-mask + per-nucleus atlas labels ───
    roi_specs = []   # (column_name, bool_array_on_ref_grid)
    for mpath in (args.roi_mask or []):
        if not os.path.isfile(mpath):
            print(f"ERROR: roi-mask not found: {mpath}", file=sys.stderr); sys.exit(1)
        arr = _to_ref(mpath) > 0.5
        name = "roi_" + os.path.basename(mpath).split(".nii")[0]
        roi_specs.append((name, arr))
        print(f"  roi-mask {name}: {int(arr.sum())} voxels")
    if args.roi_atlas:
        if not os.path.isfile(args.roi_atlas):
            print(f"ERROR: roi-atlas not found: {args.roi_atlas}", file=sys.stderr); sys.exit(1)
        adata = np.rint(_to_ref(args.roi_atlas)).astype(np.int64)
        labels = args.roi_labels or [int(v) for v in np.unique(adata) if v != 0]
        names = args.roi_label_names or [f"label{v}" for v in labels]
        if len(names) != len(labels):
            print("ERROR: --roi-label-names count must match --roi-labels.", file=sys.stderr)
            sys.exit(1)
        for v, nm in zip(labels, names):
            arr = adata == v
            roi_specs.append((f"nuc_{nm}", arr))
            print(f"  atlas label {v} ({nm}): {int(arr.sum())} voxels")

    # ── Build the CSV header dynamically ──────────────────────────────────────
    header = ["subject"]
    if have_coord:
        header += ["voxel_value", f"sphere{int(r_small)}mm_mean"]
        if sig_mask is not None:
            header.append(f"sphere{int(r_small)}mm_sig_mean")
    header += [name for name, _ in roi_specs]

    def _nan_row(subj):
        """A row of NaNs for a subject we couldn't read (kept, never dropped)."""
        r = [subj]
        if have_coord:
            r += [float("nan"), float("nan")]
            if sig_mask is not None:
                r.append(float("nan"))
        r += [float("nan") for _ in roi_specs]
        return tuple(r)

    # ── Extract per-subject values (Task 24: flag + log, NEVER skip) ───────────
    # A geometry/affine mismatch is resampled onto the reference grid and flagged,
    # not silently dropped; an unreadable image yields a NaN row + flag. Every wcon
    # found becomes one CSV row, so expected == analyzed (no silent omission).
    rows = []
    geom_rows = []          # (subject, shape, affine_match, status)
    n_resampled = n_error = n_ok = 0
    for w in wcons:
        subj = os.path.splitext(os.path.basename(w))[0]
        try:
            img = nib.load(w)
            same_shape  = tuple(img.shape[:3]) == tuple(shape)
            same_affine = np.allclose(img.affine, affine, atol=1e-3)
            if same_shape and same_affine:
                data = np.asanyarray(img.dataobj, dtype=np.float64)
                gstatus = "OK"; n_ok += 1
            else:
                # never skip — resample onto the reference grid (linear), flag it
                d = np.asanyarray(img.dataobj, dtype=np.float32)
                if d.ndim == 4:
                    d = d[..., 0]
                res = resample_from_to(
                    nib.Nifti1Image(d, img.affine, img.header), (shape, affine), order=1)
                data = np.asanyarray(res.dataobj, dtype=np.float64)
                gstatus = "RESAMPLED"; n_resampled += 1
                print(f"  [FLAG] {subj}: geometry differs from ref "
                      f"(shape {tuple(img.shape[:3])} vs {tuple(shape)}, "
                      f"affine_match={same_affine}) — resampled onto ref grid, NOT skipped")
            geom_rows.append((subj, str(tuple(int(x) for x in img.shape[:3])),
                              int(same_affine), gstatus))
        except Exception as e:  # noqa: BLE001
            n_error += 1
            geom_rows.append((subj, "NA", 0, f"ERROR:{e}"))
            rows.append(_nan_row(subj))   # keep the subject — flagged, not dropped
            print(f"  [FLAG] {subj}: could not read/resample ({e}) — "
                  f"NaN row written, NOT skipped")
            continue

        row = [subj]
        msg = f"  {subj}:"
        if have_coord:
            vox_val = float(data[ijk[0], ijk[1], ijk[2]])
            sphere_vals = data[small_mask]
            sphere_mean = float(np.nanmean(sphere_vals)) if sphere_vals.size else float("nan")
            row += [vox_val, sphere_mean]
            msg += f" voxel={vox_val:.4f} sphere{int(r_small)}mm={sphere_mean:.4f}"
            if sig_mask is not None:
                sv = data[small_mask & sig_mask]
                row.append(float(np.nanmean(sv)) if sv.size else float("nan"))
                msg += f" sig={row[-1]:.4f}"
        for name, arr in roi_specs:
            vals = data[arr]
            mval = float(np.nanmean(vals)) if vals.size else float("nan")
            row.append(mval)
            msg += f" {name}={mval:.4f}"
        rows.append(tuple(row))
        print(msg)

    csv_path = os.path.join(args.output_dir, "roi_values.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    print(f"\nWrote {len(rows)} rows × {len(header)} cols -> {csv_path}")

    # ── Geometry / subject-count audit (Task 24) — flag + log, never omit ──────
    geom_path = os.path.join(args.output_dir, "_roi_geometry_check.csv")
    with open(geom_path, "w", newline="") as f:
        wtr = csv.writer(f)
        wtr.writerow(["subject", "shape", "affine_match", "status"])
        wtr.writerows(geom_rows)
    n_expected, n_analyzed = len(wcons), len(rows)
    print(f"Geometry/counts: expected {n_expected}, analyzed {n_analyzed} "
          f"(OK {n_ok}, resampled {n_resampled}, read-error {n_error}) -> {geom_path}")
    if n_analyzed != n_expected:
        print(f"*** WARNING: analyzed ({n_analyzed}) != expected ({n_expected}) — "
              f"investigate {geom_path} ***")
    if n_resampled or n_error:
        print(f"*** {n_resampled + n_error} subject(s) FLAGGED (resampled/read-error) "
              f"— review {geom_path} before trusting ROI stats ***")

    # ── Mask a manually-selected con with the largest sphere (needs --coord) ──
    if args.con and not have_coord:
        print("NOTE: --con masking needs --coord (a sphere) — skipped.", file=sys.stderr)
    if args.con and have_coord:
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
        elif have_coord:
            gmask = masks[r_large]
            mask_label = f"auto {int(r_large)}mm sphere"
        else:
            print("ERROR: --group-con needs --group-mask (or --coord for a sphere).",
                  file=sys.stderr)
            sys.exit(1)

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
