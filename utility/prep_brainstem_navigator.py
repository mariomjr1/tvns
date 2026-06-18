#!/usr/bin/env python3
"""
prep_brainstem_navigator.py — turn a Brainstem Navigator v1.0 download into ONE labeled
brainstem-nuclei atlas, on the pipeline's MNI grid, ready for step 10 / 10b.

You point it at the **root folder** of the unzipped Brainstem Navigator v1.0 and at a
**reference** image in the pipeline's MNI space (an fMRIPrep MNI BOLD or a wcon_*.nii).
It:
  1. discovers the per-nucleus probabilistic label files in the toolkit's **MNI** folder,
  2. merges left/right of each nucleus (default), thresholds the probability,
  3. resamples each onto the reference grid and writes ONE integer labeled atlas
     (overlaps resolved by highest probability), plus a labels CSV (value,name).

MNI note (important): the Brainstem Navigator MNI labels are ICBM152 **2009b nonlinear
asymmetric, 0.5 mm**; fMRIPrep here uses **MNI152NLin2009cAsym** (2009c asymmetric, 1 mm)
— the same ICBM-2009 nonlinear-ASYMMETRIC family at a different resolution. So resampling
the labels onto the fMRIPrep MNI reference grid is the correct operation (no nonlinear
template-to-template warp). ALWAYS verify the atlas-on-wcon / atlas-in-native overlay
before trusting ROI values (the residual is sub-millimetre but should be eyeballed).

Outputs feed step 10 directly:
  step10_ROI.sh ... --roi-atlas <labeled atlas> --roi-labels <values> --roi-label-names <names>
which extracts each nucleus's mean contrast (β) value per subject.

Discovery (file finding + naming) needs only the standard library, so it can be listed/
checked anywhere; the resample/build step needs nibabel (+ scipy).

Usage:
  # 1) verify what it finds on YOUR download (no nibabel needed):
  python prep_brainstem_navigator.py --atlas-root <BrainstemNavigatorv1.0> --list
  # 2) build the atlas on your MNI grid:
  python prep_brainstem_navigator.py --atlas-root <root> --reference <wcon_or_MNI_BOLD.nii> \
        --output <out>/brainstem_navigator_atlas.nii.gz --threshold 0.35
"""
import argparse
import csv
import os
import re
import sys
from collections import OrderedDict
from pathlib import Path

NIFTI_EXTS = (".nii", ".nii.gz")
# filenames that are templates/backgrounds, not nuclei — skipped by default
_SKIP_TOKENS = ("template", "t1w", "t2w", "_t1", "_t2", "fa_", "_fa", "b0", "brainmask",
                "brain_mask", "background", "wholebrainstem", "whole_brainstem")


# ── discovery (stdlib only) ───────────────────────────────────────────────────
def _niftis(d: Path, recursive=False):
    if not d.is_dir():
        return []
    it = d.rglob("*") if recursive else d.iterdir()
    return sorted(p for p in it
                  if p.is_file() and p.name.lower().endswith(NIFTI_EXTS))


def discover_label_dir(root: Path, space_subdir: str = "") -> Path:
    """Find the directory holding the MNI per-nucleus label NIfTIs.
    Prefers a dir whose path mentions MNI (+ label/atlas/prob/nuclei/roi) and has the
    most NIfTIs. Falls back to the NIfTI-richest dir under root (with a warning)."""
    if space_subdir:
        p = root / space_subdir
        if p.is_dir():
            return p
        print(f"WARNING: --space-subdir '{space_subdir}' not under {root}; auto-detecting.")
    cands = []
    for d in [root] + [x for x in root.rglob("*") if x.is_dir()]:
        n = len(_niftis(d))
        if n == 0:
            continue
        path_l = str(d).lower()
        score = n
        if "mni" in path_l:
            score += 1000
        if "iit" in path_l:           # IIT version is for diffusion — de-prioritise
            score -= 2000
        if any(t in path_l for t in ("label", "atlas", "prob", "nuclei", "roi")):
            score += 200
        # Brainstem Navigator layout: prefer the BRAINSTEM nuclei + the RAW probabilistic
        # labels (so our own threshold + highest-probability overlap resolution apply).
        if "brainstemnucleiatlas" in path_l:
            score += 400
        if path_l.rstrip("/").endswith("labels_probabilistic"):
            score += 500
        elif "thresholded_probabilistic" in path_l:
            score += 100
        elif "binary" in path_l:
            score += 50
        cands.append((score, n, d))
    if not cands:
        raise SystemExit(f"ERROR: no NIfTI files found anywhere under {root}")
    cands.sort(reverse=True)
    best = cands[0][2]
    if "mni" not in str(best).lower():
        print(f"WARNING: no 'MNI' folder found; using NIfTI-richest dir {best}. "
              f"Pass --space-subdir to override.")
    return best


def discover_nuclei(label_dir: Path, glob: str = "", keep_skips=False):
    """Return the per-nucleus NIfTI files in label_dir (skips template/background files)."""
    if glob:
        files = sorted(label_dir.glob(glob))
    else:
        files = _niftis(label_dir)
        if not files:                  # try one level down
            files = _niftis(label_dir, recursive=True)
    if not keep_skips:
        files = [f for f in files
                 if not any(t in f.name.lower() for t in _SKIP_TOKENS)]
    return files


def nucleus_name(path: Path, keep_lr=False) -> str:
    """Nucleus name from a filename: drop extension, optional L/R side, tidy."""
    name = path.name
    for e in (".nii.gz", ".nii"):
        if name.lower().endswith(e):
            name = name[: -len(e)]
            break
    if not keep_lr:
        # strip a trailing/embedded side token: _l _r _left _right _lh _rh (case-insens)
        name = re.sub(r"[._-](l|r|lh|rh|left|right)$", "", name, flags=re.I)
        name = re.sub(r"[._-](left|right)[._-]", "_", name, flags=re.I)
    return name.strip("_-. ") or path.stem


def _side(path: Path) -> str:
    """'L' / 'R' / '' from a filename side token."""
    name = path.name
    for e in (".nii.gz", ".nii"):
        if name.lower().endswith(e):
            name = name[: -len(e)]
            break
    if re.search(r"[._-](l|lh|left)$", name, re.I):
        return "L"
    if re.search(r"[._-](r|rh|right)$", name, re.I):
        return "R"
    return ""


def plan_labels(files, keep_lr=False, lateralizable=None, select=None):
    """OrderedDict {nucleus_name: [files...]} preserving first-seen order.
    L/R are merged into one ROI EXCEPT for `lateralizable` nuclei (kept as <name>_L/_R) —
    or all kept when keep_lr=True. `select` restricts to matching nuclei (case-insensitive).
    Laterality matters for tVNS (ipsilateral-afferent hypothesis), so NTS/VSM + LC default
    to kept-separate."""
    lateralizable = lateralizable or []
    plan = OrderedDict()
    for f in files:
        base = nucleus_name(f, keep_lr=False)
        side = _side(f)
        keep = bool(side) and (keep_lr or
                               any(s.lower() in base.lower() for s in lateralizable))
        nm = f"{base}_{side}" if keep else base
        if select and not any(s.lower() in nm.lower() for s in select):
            continue
        plan.setdefault(nm, []).append(f)
    return plan


# ── build (needs nibabel + scipy) ─────────────────────────────────────────────
def build_atlas(plan, reference, out_path, labels_out, threshold=0.35):
    try:
        import numpy as np
        import nibabel as nib
        from nibabel.processing import resample_from_to
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"ERROR: building needs nibabel + scipy ({exc}).")

    ref = nib.load(str(reference))
    ref_shape, ref_affine = ref.shape[:3], ref.affine

    names = list(plan.keys())
    stack = np.zeros((len(names),) + tuple(ref_shape), dtype=np.float32)
    for i, nm in enumerate(names):
        acc = np.zeros(tuple(ref_shape), dtype=np.float32)
        for f in plan[nm]:
            img = nib.load(str(f))
            r = resample_from_to(img, (ref_shape, ref_affine), order=1)  # trilinear (prob)
            d = np.nan_to_num(np.asanyarray(r.dataobj, dtype=np.float32), nan=0.0)
            acc = np.maximum(acc, d)   # combine L/R by max probability
        stack[i] = acc
        print(f"  [{i+1:>2}] {nm:<28} from {len(plan[nm])} file(s); "
              f"max p = {acc.max():.3f}")

    winner = np.argmax(stack, axis=0)
    maxp = np.max(stack, axis=0)
    labels = np.where(maxp >= threshold, winner.astype(np.int16) + 1, 0).astype(np.int16)

    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    nib.save(nib.Nifti1Image(labels, ref_affine, ref.header), str(out_path))
    # Prob-max companion (winning nucleus probability per voxel) — for probability-weighted
    # ROI means in step10 (roi_extract --roi-weight), which reduce partial-volume bias.
    probmax_path = str(Path(out_path).with_suffix("").with_suffix("")) + "_probmax.nii.gz"
    nib.save(nib.Nifti1Image(maxp.astype(np.float32), ref_affine, ref.header), probmax_path)
    with open(labels_out, "w", newline="") as fcsv:
        w = csv.writer(fcsv)
        w.writerow(["value", "name", "n_voxels"])
        for i, nm in enumerate(names):
            w.writerow([i + 1, nm, int((labels == i + 1).sum())])

    n_lab = int((labels > 0).sum())
    print(f"\nWrote labeled atlas: {out_path}  ({len(names)} nuclei, {n_lab} labeled voxels)")
    print(f"Wrote prob-max map:  {probmax_path}  (use as roi_extract --roi-weight)")
    print(f"Wrote labels CSV:    {labels_out}")
    # space-match reminder (the residual to eyeball)
    print("NOTE: atlas = ICBM152 2009b-asym 0.5mm resampled onto your MNI reference "
          "(2009cAsym). Verify the atlas-on-wcon overlay before trusting ROI values.")
    return names


def _fmt_values_names(plan):
    vals = " ".join(str(i + 1) for i in range(len(plan)))
    names = " ".join(re.sub(r"\s+", "", n) for n in plan)
    return vals, names


def main():
    ap = argparse.ArgumentParser(description="Build a labeled brainstem atlas from Brainstem Navigator v1.0")
    ap.add_argument("--atlas-root", required=True, help="unzipped BrainstemNavigatorv1.0 root")
    ap.add_argument("--reference", default="", help="NIfTI on the pipeline MNI grid (fMRIPrep MNI BOLD / wcon)")
    ap.add_argument("--output", default="", help="output labeled atlas .nii.gz")
    ap.add_argument("--labels-out", default="", help="output labels CSV (value,name)")
    ap.add_argument("--threshold", type=float, default=0.35, help="probability threshold per nucleus")
    ap.add_argument("--space-subdir", default="", help="force the MNI labels subfolder (relative to root)")
    ap.add_argument("--glob", default="", help="override the nucleus-file glob within the labels dir")
    ap.add_argument("--select", default="", help="comma list of nucleus name substrings to include (default all)")
    ap.add_argument("--keep-lr", action="store_true", help="keep ALL nuclei left/right as separate ROIs")
    ap.add_argument("--lateralizable", default="VSM,LC,NTS",
                    help="comma list of nuclei kept L/R-separate (laterality matters for tVNS); "
                         "others merged. Default VSM,LC,NTS. Empty string = merge all.")
    ap.add_argument("--keep-skips", action="store_true", help="don't skip template/background files")
    ap.add_argument("--list", action="store_true", help="just list discovered nuclei and exit")
    a = ap.parse_args()

    root = Path(a.atlas_root)
    if not root.is_dir():
        raise SystemExit(f"ERROR: --atlas-root not found: {root}")
    label_dir = discover_label_dir(root, a.space_subdir)
    files = discover_nuclei(label_dir, a.glob, keep_skips=a.keep_skips)
    if not files:
        raise SystemExit(f"ERROR: no nucleus NIfTIs in {label_dir} "
                         f"(try --space-subdir / --glob / --keep-skips).")
    select = [s.strip() for s in a.select.split(",") if s.strip()] or None
    lateralizable = [s.strip() for s in a.lateralizable.split(",") if s.strip()]
    plan = plan_labels(files, keep_lr=a.keep_lr, lateralizable=lateralizable, select=select)

    print(f"Labels dir: {label_dir}")
    print(f"Discovered {len(files)} file(s) -> {len(plan)} nucleus ROI(s):")
    for i, (nm, fs) in enumerate(plan.items()):
        print(f"  {i+1:>2}. {nm:<28} ({len(fs)} file(s))")

    if a.list or not a.reference:
        if not a.reference and not a.list:
            print("\n(no --reference given — listing only. Re-run with --reference to build.)")
        vals, names = _fmt_values_names(plan)
        print(f"\nFor step10:  --roi-labels {vals}\n             --roi-label-names {names}")
        return 0

    out = a.output or str(Path(label_dir).parent / "brainstem_navigator_atlas.nii.gz")
    labels_out = a.labels_out or str(Path(out).with_suffix("").with_suffix("")) + "_labels.csv"
    print(f"\nBuilding on reference grid: {a.reference}")
    build_atlas(plan, a.reference, out, labels_out, threshold=a.threshold)
    vals, names = _fmt_values_names(plan)
    print(f"\nstep10 args:\n  --roi-atlas {out}\n  --roi-labels {vals}\n  --roi-label-names {names}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
