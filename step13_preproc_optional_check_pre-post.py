#!/usr/bin/env python3
import argparse
import os
import sys
import numpy as np

# Force non-interactive backend (safe on clusters)
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

import os
os.chdir("/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/test")
print(os.getcwd())

try:
    import nibabel as nib
except ImportError:
    print("ERROR: nibabel is not installed. Try: pip install nibabel imageio matplotlib", file=sys.stderr)
    sys.exit(1)

import imageio.v2 as imageio


def pick_middle_slice(shape3d, plane: str) -> int:
    x, y, z = shape3d
    plane = plane.lower()
    if plane == "axial":
        return z // 2
    if plane == "coronal":
        return y // 2
    if plane == "sagittal":
        return x // 2
    raise ValueError("plane must be one of: axial, coronal, sagittal")


def extract_slice(vol3d: np.ndarray, plane: str, sl: int) -> np.ndarray:
    """
    Return 2D slice as float32, oriented for display (transpose so it looks upright-ish).
    """
    plane = plane.lower()
    if plane == "axial":
        img2d = vol3d[:, :, sl]
    elif plane == "coronal":
        img2d = vol3d[:, sl, :]
    elif plane == "sagittal":
        img2d = vol3d[sl, :, :]
    else:
        raise ValueError("plane must be one of: axial, coronal, sagittal")

    # transpose for nicer display; flip vertically so it isn't upside down
    img2d = np.flipud(img2d.T)
    return img2d.astype(np.float32, copy=False)


def robust_range_from_samples(get_frame_fn, nt: int, nsamp: int, p_lo: float, p_hi: float):
    """
    Estimate display range using percentiles from a subset of timepoints.
    get_frame_fn(t) -> 2D ndarray
    """
    if nt <= 0:
        raise ValueError("nt must be > 0")

    if nsamp >= nt:
        sample_ts = np.arange(nt)
    else:
        # Evenly spaced samples across time
        sample_ts = np.linspace(0, nt - 1, nsamp).astype(int)

    vals = []
    for t in sample_ts:
        fr = get_frame_fn(t)
        vals.append(fr.ravel())
    vals = np.concatenate(vals)

    vmin = np.percentile(vals, p_lo)
    vmax = np.percentile(vals, p_hi)
    if not np.isfinite(vmin) or not np.isfinite(vmax) or vmin == vmax:
        # fallback
        vmin, vmax = float(np.min(vals)), float(np.max(vals))
        if vmin == vmax:
            vmax = vmin + 1.0
    return float(vmin), float(vmax)


def robust_symmetric_range_from_samples(get_diff_fn, nt: int, nsamp: int, p_hi: float):
    """
    Symmetric range for diff using percentile of abs(diff)
    """
    if nsamp >= nt:
        sample_ts = np.arange(nt)
    else:
        sample_ts = np.linspace(0, nt - 1, nsamp).astype(int)

    vals = []
    for t in sample_ts:
        d = get_diff_fn(t)
        vals.append(np.abs(d).ravel())
    vals = np.concatenate(vals)

    vmax = np.percentile(vals, p_hi)
    if not np.isfinite(vmax) or vmax == 0:
        vmax = float(np.max(vals))
        if vmax == 0:
            vmax = 1.0
    return float(vmax)


def main():
    ap = argparse.ArgumentParser(description="Make a GIF comparing pre vs post RETROICOR BOLD (Pre|Post|Diff).")
    ap.add_argument("pre", nargs="?", default=None, help="Pre-correction BOLD *_bold.nii.gz")
    ap.add_argument("post", nargs="?", default=None, help="Post-correction BOLD *_retro-corrected.nii.gz")
    ap.add_argument("-o", "--out", default="retroicor_compare.gif", help="Output GIF filename")
    ap.add_argument("--plane", default="axial", choices=["axial", "coronal", "sagittal"], help="Slice plane")
    ap.add_argument("--slice", dest="slice_idx", default="auto", help="Slice index (int) or 'auto' for middle")
    ap.add_argument("--fps", type=float, default=10.0, help="Frames per second in GIF")
    ap.add_argument("--step", type=int, default=1, help="Use every Nth TR (e.g., 2 for half frames)")
    ap.add_argument("--nsamp", type=int, default=25, help="How many TRs to sample to set intensity ranges")
    ap.add_argument("--plo", type=float, default=2.0, help="Lower percentile for display scaling")
    ap.add_argument("--phi", type=float, default=98.0, help="Upper percentile for display scaling")
    ap.add_argument("--diffp", type=float, default=98.0, help="Percentile of abs(diff) for symmetric diff scaling")
    args = ap.parse_args()

    # Auto-detect inputs if not provided
    if args.pre is None or args.post is None:
        files = os.listdir(".")
        pre_cands = sorted([f for f in files if f.endswith("_bold.nii.gz") and "retro-corrected" not in f])
        post_cands = sorted([f for f in files if f.endswith("_retro-corrected.nii.gz")])
        if args.pre is None:
            args.pre = pre_cands[0] if pre_cands else None
        if args.post is None:
            args.post = post_cands[0] if post_cands else None

    if not args.pre or not os.path.isfile(args.pre):
        raise SystemExit(f"ERROR: pre file not found: {args.pre}")
    if not args.post or not os.path.isfile(args.post):
        raise SystemExit(f"ERROR: post file not found: {args.post}")

    pre_img = nib.load(args.pre)
    post_img = nib.load(args.post)

    pre_shape = pre_img.shape
    post_shape = post_img.shape

    if len(pre_shape) != 4 or len(post_shape) != 4:
        raise SystemExit("ERROR: both inputs must be 4D fMRI NIfTI (x,y,z,t).")
    if pre_shape != post_shape:
        raise SystemExit(f"ERROR: shapes differ: pre={pre_shape} post={post_shape}")

    x, y, z, nt = pre_shape

    # slice index
    if args.slice_idx == "auto":
        sl = pick_middle_slice((x, y, z), args.plane)
    else:
        sl = int(args.slice_idx)

    if args.plane == "axial" and not (0 <= sl < z):
        raise SystemExit(f"ERROR: axial slice must be 0..{z-1}")
    if args.plane == "coronal" and not (0 <= sl < y):
        raise SystemExit(f"ERROR: coronal slice must be 0..{y-1}")
    if args.plane == "sagittal" and not (0 <= sl < x):
        raise SystemExit(f"ERROR: sagittal slice must be 0..{x-1}")

    pre_data = pre_img.dataobj   # lazy proxy
    post_data = post_img.dataobj

    def get_pre2d(t):
        vol = np.asanyarray(pre_data[..., t], dtype=np.float32)
        return extract_slice(vol, args.plane, sl)

    def get_post2d(t):
        vol = np.asanyarray(post_data[..., t], dtype=np.float32)
        return extract_slice(vol, args.plane, sl)

    def get_diff2d(t):
        return get_post2d(t) - get_pre2d(t)

    # Compute robust display ranges from sampled TRs
    vmin_pre, vmax_pre = robust_range_from_samples(get_pre2d, nt, args.nsamp, args.plo, args.phi)
    vmin_post, vmax_post = robust_range_from_samples(get_post2d, nt, args.nsamp, args.plo, args.phi)
    vmax_diff = robust_symmetric_range_from_samples(get_diff2d, nt, args.nsamp, args.diffp)

    # Build frames
    frames = []
    duration = 1.0 / args.fps

    # Prepare figure once
    fig = plt.figure(figsize=(12, 4), dpi=120)
    ax1 = fig.add_subplot(1, 3, 1)
    ax2 = fig.add_subplot(1, 3, 2)
    ax3 = fig.add_subplot(1, 3, 3)

    for ax in (ax1, ax2, ax3):
        ax.set_axis_off()

    im1 = ax1.imshow(get_pre2d(0), cmap="gray", vmin=vmin_pre, vmax=vmax_pre)
    ax1.set_title("Pre", fontsize=10)

    im2 = ax2.imshow(get_post2d(0), cmap="gray", vmin=vmin_post, vmax=vmax_post)
    ax2.set_title("Post", fontsize=10)

    im3 = ax3.imshow(get_diff2d(0), cmap="gray", vmin=-vmax_diff, vmax=vmax_diff)
    ax3.set_title("Diff (Post - Pre)", fontsize=10)

    fig.tight_layout(pad=0.5)

    for t in range(0, nt, max(1, args.step)):
        im1.set_data(get_pre2d(t))
        im2.set_data(get_post2d(t))
        im3.set_data(get_diff2d(t))
        fig.canvas.draw()

        # Convert canvas to RGB array
        w, h = fig.canvas.get_width_height()
        buf = np.frombuffer(fig.canvas.tostring_rgb(), dtype=np.uint8).reshape(h, w, 3)
        frames.append(buf.copy())

        if (t % (10 * max(1, args.step))) == 0:
            print(f"Rendered TR {t}/{nt-1} ...")

    plt.close(fig)

    # Save GIF
    imageio.mimsave(args.out, frames, duration=duration)
    print(f"\nWrote GIF: {args.out}")
    print(f"Plane={args.plane} Slice={sl} step={args.step} fps={args.fps} frames={len(frames)}")


if __name__ == "__main__":
    main()
