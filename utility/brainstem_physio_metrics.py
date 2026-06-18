#!/usr/bin/env python3
"""
brainstem_physio_metrics.py — brainstem-mask tSNR + cardiac-band spectral power of a BOLD
run, so the Task-12 physio-order pilot (RETROICOR-before vs conventional) is one command
(review M2). Run it on the corrected and the conventionally-processed BOLD and compare:
RETROICOR should LOWER the cardiac-band power and RAISE tSNR in the brainstem.

  tSNR            = median over brainstem voxels of (mean_t / std_t)
  cardiac_frac    = fraction of the brainstem mean-signal spectral power in the cardiac
                    band (default 0.8–1.5 Hz), after linear detrend

Needs nibabel + numpy (cluster). Flag + log + continue: warns and exits 0 on missing deps
or inputs, never aborts.

Usage:
  python brainstem_physio_metrics.py --bold <bold.nii.gz> --mask <brainstem_mask.nii.gz> \
      --tr 1.19 [--card-band 0.8 1.5] [--label corrected] [--out metrics.csv]
"""
import argparse
import csv
import os
import sys


def main():
    ap = argparse.ArgumentParser(description="Brainstem tSNR + cardiac-band power of a BOLD run")
    ap.add_argument("--bold", required=True, help="4D BOLD NIfTI")
    ap.add_argument("--mask", required=True, help="brainstem mask NIfTI (same grid as BOLD)")
    ap.add_argument("--tr", type=float, required=True, help="repetition time (s)")
    ap.add_argument("--card-band", nargs=2, type=float, default=[0.8, 1.5], help="cardiac band (Hz)")
    ap.add_argument("--label", default="", help="label for the CSV row (e.g. corrected/conventional)")
    ap.add_argument("--out", default="", help="append a row to this CSV (optional)")
    a = ap.parse_args()

    try:
        import numpy as np
        import nibabel as nib
    except Exception as exc:  # noqa: BLE001
        print(f"[physio-metrics] WARN needs nibabel + numpy ({exc}) — skipped", file=sys.stderr)
        return 0
    for p in (a.bold, a.mask):
        if not os.path.isfile(p):
            print(f"[physio-metrics] WARN not found: {p} — skipped", file=sys.stderr); return 0

    bold = nib.load(a.bold)
    data = np.asanyarray(bold.dataobj, dtype=np.float32)
    if data.ndim != 4 or data.shape[3] < 8:
        print(f"[physio-metrics] WARN {a.bold} is not a usable 4D series — skipped", file=sys.stderr); return 0
    m = np.asanyarray(nib.load(a.mask).dataobj) > 0.5
    if m.shape != data.shape[:3] or not m.any():
        print("[physio-metrics] WARN mask grid mismatch or empty — skipped", file=sys.stderr); return 0

    ts = data[m]                                   # (n_vox, n_t)
    mean_t = ts.mean(axis=1); std_t = ts.std(axis=1)
    good = std_t > 0
    tsnr = float(np.median(mean_t[good] / std_t[good])) if good.any() else float("nan")

    # cardiac-band fraction of the mask-mean signal power (linear detrend first)
    sig = ts.mean(axis=0).astype(np.float64)
    n = sig.size
    t = np.arange(n)
    sig = sig - np.polyval(np.polyfit(t, sig, 1), t)   # remove mean + linear trend
    freqs = np.fft.rfftfreq(n, d=a.tr)
    p = np.abs(np.fft.rfft(sig)) ** 2
    p[0] = 0.0
    lo, hi = a.card_band
    band = (freqs >= lo) & (freqs <= hi)
    total = p.sum()
    card_frac = float(p[band].sum() / total) if total > 0 else float("nan")
    nyq = 0.5 / a.tr
    aliased = "" if hi <= nyq else f"(WARNING: {hi} Hz > Nyquist {nyq:.2f} Hz — cardiac is aliased)"

    print(f"[physio-metrics] {a.label or os.path.basename(a.bold)}: "
          f"tSNR(median)={tsnr:.2f}  cardiac-band[{lo}-{hi}Hz] power frac={card_frac:.4f} {aliased}")
    if a.out:
        new = not os.path.isfile(a.out)
        with open(a.out, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["label", "bold", "tsnr_median", f"cardiac_frac_{lo}-{hi}Hz", "tr", "nyquist"])
            w.writerow([a.label, os.path.basename(a.bold), f"{tsnr:.3f}", f"{card_frac:.5f}",
                        a.tr, f"{nyq:.3f}"])
        print(f"[physio-metrics] appended -> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
