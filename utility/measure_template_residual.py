#!/usr/bin/env python3
"""
measure_template_residual.py  —  ICBM152 2009b ↔ 2009c residual displacement (M7)

The Brainstem Navigator atlas labels live in ICBM152 **2009b**-nonlinear-asymmetric
space (0.5 mm); fMRIPrep here normalises to **MNI152NLin2009cAsym** (2009c, 1 mm).
`prep_brainstem_navigator.py` resamples the labels onto the fMRIPrep grid assuming the
2009b→2009c residual is sub-millimetre (same ICBM-2009 nonlinear-asymmetric family).
This utility *measures* that residual so the assumption is verified, not asserted:

  1. nonlinearly register the 2009b template to the 2009c reference (ANTs SyN),
  2. compute the warp's displacement magnitude (mm) **inside the brainstem mask**,
  3. report median / 95th-percentile residual and print a recommendation:
       median <~0.5 mm  ->  "resample-only OK"
       otherwise        ->  "add a 2009b->2009c warp to the atlas build"

The heavy registration is an ANTs call shelled out and **guarded** (warns + skips if
ANTs is missing); the per-voxel statistic needs nibabel and is likewise guarded. The
input **discovery** and the **recommendation printing** are pure stdlib, so they are
testable anywhere. Flag + log + continue — never fatal; always exits 0.

Usage:
  # discovery only (no ANTs/nibabel needed) — confirm the three inputs were found:
  measure_template_residual.py --template-2009b <2009b.nii[.gz]> \
        --reference-2009c <fmriprep MNI ref.nii[.gz]> --brainstem-mask <mask> \
        --out <dir> --discover-only

  # full run (needs ANTs + nibabel):
  measure_template_residual.py --template-2009b <…> --reference-2009c <…> \
        --brainstem-mask <…> --out <dir> [--ants-bin <dir>] [--ok-threshold 0.5]

Created by Mario Murakami
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

# median residual (mm) at or below which resample-only is judged adequate
DEFAULT_OK_THRESHOLD_MM = 0.5


def discover_inputs(template_2009b, reference_2009c, brainstem_mask):
    """Resolve + validate the three inputs. Returns (ok, info_dict).
    Pure stdlib — does not touch ANTs/nibabel."""
    info = {}
    ok = True
    for key, val in (("template_2009b", template_2009b),
                     ("reference_2009c", reference_2009c),
                     ("brainstem_mask", brainstem_mask)):
        p = Path(val) if val else None
        exists = bool(p and p.is_file())
        info[key] = {"path": str(p) if p else "", "exists": exists}
        if not exists:
            ok = False
    return ok, info


def recommend(median_mm, p95_mm, threshold=DEFAULT_OK_THRESHOLD_MM):
    """Return (verdict, message) from the residual statistics. Pure stdlib."""
    if median_mm is None:
        return "UNKNOWN", ("Could not compute the residual (ANTs/nibabel missing or "
                           "registration failed) — re-run on the cluster.")
    if median_mm <= threshold:
        return "RESAMPLE_OK", (
            f"Median residual {median_mm:.3f} mm (95th pct {p95_mm:.3f} mm) inside the "
            f"brainstem is <= {threshold:.2f} mm — RESAMPLE-ONLY OK: resampling the 2009b "
            f"atlas labels onto the 2009c grid (as prep_brainstem_navigator does) is "
            f"adequate; no 2009b->2009c warp needed.")
    return "ADD_WARP", (
        f"Median residual {median_mm:.3f} mm (95th pct {p95_mm:.3f} mm) inside the "
        f"brainstem EXCEEDS {threshold:.2f} mm — ADD a 2009b->2009c nonlinear warp to the "
        f"atlas build before resampling, or sub-mm brainstem nuclei will be mislocalised.")


def _have(tool):
    return shutil.which(tool) is not None


def run_registration(template_2009b, reference_2009c, out_dir, ants_bin=""):
    """ANTs SyN 2009b->2009c; returns the path to the warp field, or None (guarded)."""
    if ants_bin:
        import os
        os.environ["PATH"] = f"{ants_bin}:{os.environ.get('PATH', '')}"
    if not _have("antsRegistration"):
        print("[residual] WARNING: antsRegistration not on PATH — skipping registration "
              "(run on the cluster).", file=sys.stderr)
        return None
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    prefix = str(out / "tpl2009b_to_2009c_")
    cmd = [
        "antsRegistration", "-d", "3", "-o", prefix,
        "--transform", "SyN[0.1,3,0]",
        "--metric", f"CC[{reference_2009c},{template_2009b},1,4]",
        "--convergence", "[100x70x50x20,1e-6,10]",
        "--shrink-factors", "8x4x2x1",
        "--smoothing-sigmas", "3x2x1x0vox",
        "--interpolation", "Linear",
        "--winsorize-image-intensities", "[0.005,0.995]",
    ]
    try:
        log = out / "measure_template_residual_ants.log"
        with open(log, "w") as lf:
            subprocess.run(cmd, check=True, stdout=lf, stderr=subprocess.STDOUT)
    except (subprocess.CalledProcessError, OSError) as exc:
        print(f"[residual] WARNING: antsRegistration failed ({exc}) — see log; "
              "skipping.", file=sys.stderr)
        return None
    warp = Path(prefix + "1Warp.nii.gz")
    if not warp.is_file():
        print(f"[residual] WARNING: expected warp not produced: {warp}", file=sys.stderr)
        return None
    return warp


def residual_stats_in_mask(warp_path, brainstem_mask, reference_2009c):
    """median / 95th-pct displacement magnitude (mm) inside the brainstem mask.
    Needs nibabel + numpy (guarded). Returns (median, p95) or (None, None)."""
    try:
        import numpy as np
        import nibabel as nib
        from nibabel.processing import resample_from_to
    except Exception as exc:  # noqa: BLE001
        print(f"[residual] WARNING: nibabel/numpy missing ({exc}) — cannot compute "
              "statistics; run on the cluster.", file=sys.stderr)
        return None, None
    try:
        w = nib.load(str(warp_path))
        disp = np.asanyarray(w.dataobj, dtype=np.float64)
        # ANTs warp: shape (X,Y,Z,1,3) with mm displacement in the last axis.
        disp = np.squeeze(disp)
        if disp.ndim != 4 or disp.shape[-1] != 3:
            print(f"[residual] WARNING: unexpected warp shape {disp.shape}; "
                  "expected (...,3).", file=sys.stderr)
            return None, None
        mag = np.sqrt(np.sum(disp ** 2, axis=-1))  # mm magnitude per voxel

        ref = nib.load(str(reference_2009c))
        m = nib.load(str(brainstem_mask))
        if m.shape[:3] != mag.shape[:3]:
            m = resample_from_to(m, (mag.shape[:3], ref.affine), order=0)
        mask = np.asanyarray(m.dataobj) > 0.5
        if not mask.any():
            print("[residual] WARNING: brainstem mask is empty on the warp grid.",
                  file=sys.stderr)
            return None, None
        vals = mag[mask]
        return float(np.median(vals)), float(np.percentile(vals, 95))
    except Exception as exc:  # noqa: BLE001
        print(f"[residual] WARNING: stats failed ({exc}).", file=sys.stderr)
        return None, None


def write_report(out_dir, info, median_mm, p95_mm, verdict, message):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rep = out / "template_residual_report.md"
    with open(rep, "w") as f:
        f.write("# ICBM152 2009b -> 2009c residual displacement (brainstem)\n\n")
        f.write("Inputs:\n")
        for k, v in info.items():
            f.write(f"- {k}: {v['path']}  (exists={v['exists']})\n")
        f.write("\n")
        f.write(f"- Median residual inside brainstem: "
                f"{median_mm:.3f} mm\n" if median_mm is not None
                else "- Median residual inside brainstem: not computed\n")
        f.write(f"- 95th-pct residual inside brainstem: "
                f"{p95_mm:.3f} mm\n" if p95_mm is not None
                else "- 95th-pct residual inside brainstem: not computed\n")
        f.write(f"\n## Verdict: {verdict}\n\n{message}\n")
    return rep


def main():
    ap = argparse.ArgumentParser(
        description="Measure ICBM152 2009b->2009c residual in the brainstem (M7).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--template-2009b", required=True,
                    help="ICBM152 2009b template (the atlas's native MNI template)")
    ap.add_argument("--reference-2009c", required=True,
                    help="fMRIPrep MNI152NLin2009cAsym reference image")
    ap.add_argument("--brainstem-mask", required=True,
                    help="brainstem mask (on either grid; resampled if needed)")
    ap.add_argument("--out", required=True, help="output dir for the warp + report")
    ap.add_argument("--ants-bin", default="", help="dir to prepend to PATH for ANTs")
    ap.add_argument("--ok-threshold", type=float, default=DEFAULT_OK_THRESHOLD_MM,
                    help="median residual (mm) at/below which resample-only is OK "
                         f"(default {DEFAULT_OK_THRESHOLD_MM})")
    ap.add_argument("--discover-only", action="store_true",
                    help="just resolve + report the three inputs and exit (no ANTs/nibabel)")
    args = ap.parse_args()

    ok, info = discover_inputs(args.template_2009b, args.reference_2009c,
                               args.brainstem_mask)
    print("[residual] inputs:")
    for k, v in info.items():
        print(f"  {k}: {v['path']}  (exists={v['exists']})")
    if not ok:
        print("[residual] one or more inputs missing — cannot proceed. "
              "(Discovery done; nothing computed.)", file=sys.stderr)
        # flag + log + continue: still exit 0
        return
    if args.discover_only:
        print("[residual] --discover-only: all three inputs present.")
        return

    warp = run_registration(args.template_2009b, args.reference_2009c, args.out,
                            ants_bin=args.ants_bin)
    if warp is None:
        median_mm = p95_mm = None
    else:
        median_mm, p95_mm = residual_stats_in_mask(
            warp, args.brainstem_mask, args.reference_2009c)

    verdict, message = recommend(median_mm, p95_mm, threshold=args.ok_threshold)
    rep = write_report(args.out, info, median_mm, p95_mm, verdict, message)
    print(f"\n[residual] VERDICT: {verdict}")
    print(f"[residual] {message}")
    print(f"[residual] Wrote {rep}")


if __name__ == "__main__":
    main()
