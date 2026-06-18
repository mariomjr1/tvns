#!/usr/bin/env python3
"""
overlay_atlas_native.py — montage of step10b atlas-in-native candidate(s) over the
subject's native T1w, so you can PICK the correct transform chain (C1/C2/C3) by
NUCLEUS-scale anatomy (review M1): e.g. VSM on the dorsomedial medulla at the obex,
LC on the floor of the 4th ventricle — not just "is it in the brainstem".

Needs nibabel + matplotlib (cluster). Flag + log + continue: warns and exits 0 if a
dependency or input is missing, never aborts.

Usage:
  python overlay_atlas_native.py --t1w <native_T1w.nii.gz> \
      --atlas <…_atlas-in-native_C1.nii.gz> [<…_C2> <…_C3> …] --out overlay.png [--alpha 0.45]
"""
import argparse
import os
import sys


def main():
    ap = argparse.ArgumentParser(description="Overlay atlas-in-native candidates on the T1w")
    ap.add_argument("--t1w", required=True, help="subject native T1w NIfTI")
    ap.add_argument("--atlas", nargs="+", required=True, help="one or more atlas-in-native label NIfTIs")
    ap.add_argument("--out", default="atlas_native_overlay.png")
    ap.add_argument("--alpha", type=float, default=0.45)
    a = ap.parse_args()

    try:
        import numpy as np
        import nibabel as nib
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:  # noqa: BLE001
        print(f"[overlay] WARN needs nibabel + matplotlib ({exc}) — skipped", file=sys.stderr)
        return 0
    if not os.path.isfile(a.t1w):
        print(f"[overlay] WARN T1w not found: {a.t1w} — skipped", file=sys.stderr); return 0
    atlases = [p for p in a.atlas if os.path.isfile(p)]
    if not atlases:
        print("[overlay] WARN no atlas files found — skipped", file=sys.stderr); return 0

    t1 = nib.load(a.t1w)
    t1d = np.nan_to_num(np.asanyarray(t1.dataobj, dtype=np.float32))

    def _planes(vol, cx, cy, cz):
        return (np.rot90(vol[:, :, cz]), np.rot90(vol[:, cy, :]), np.rot90(vol[cx, :, :]))

    rows = len(atlases)
    fig, axes = plt.subplots(rows, 3, figsize=(9, 3.0 * rows), squeeze=False)
    vmax = np.percentile(t1d[t1d > 0], 98) if (t1d > 0).any() else 1.0
    for r, ap_ in enumerate(atlases):
        lab = nib.load(ap_)
        ld = np.rint(np.asanyarray(lab.dataobj)).astype(np.int32)
        if ld.shape != t1d.shape:
            print(f"[overlay] WARN {os.path.basename(ap_)} grid != T1w — skipping row", file=sys.stderr)
            continue
        ys, xs, zs = np.where(ld > 0)            # centroid of labels = brainstem focus
        if len(xs) == 0:
            cx, cy, cz = (s // 2 for s in ld.shape)
        else:
            cx, cy, cz = int(np.median(ys)), int(np.median(xs)), int(np.median(zs))
        t_pl = _planes(t1d, cx, cy, cz)
        l_pl = _planes(ld, cx, cy, cz)
        for c in range(3):
            ax = axes[r][c]
            ax.imshow(t_pl[c], cmap="gray", vmin=0, vmax=vmax)
            ov = np.ma.masked_where(l_pl[c] == 0, l_pl[c])
            ax.imshow(ov, cmap="nipy_spectral", alpha=a.alpha, interpolation="nearest")
            ax.set_xticks([]); ax.set_yticks([])
            if c == 0:
                ax.set_ylabel(os.path.basename(ap_).replace("_atlas-in-native", "").replace(".nii.gz", ""),
                              fontsize=8)
    fig.suptitle("Atlas-in-native candidates over T1w — pick the chain by nucleus anatomy", fontsize=11)
    fig.tight_layout()
    fig.savefig(a.out, dpi=130)
    print(f"[overlay] wrote {a.out}  ({rows} candidate row(s)) — choose the anatomically correct chain")
    return 0


if __name__ == "__main__":
    sys.exit(main())
