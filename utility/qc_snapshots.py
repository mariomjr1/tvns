#!/usr/bin/env python3
"""
QC snapshot generator for the BIDS fMRI Pipeline.

Produces per-step mid-brain montages (axial / coronal / sagittal) for every
pipeline stage that creates or transforms a subject's images.  Results land in:

    <project_root>/codes/qc/<subject>/
        NN_<step_name>_<subject>.png    -- per-step snapshots
        00_pipeline_montage_<subject>.png  -- combined overview strip

A JSON manifest is written to:
    <project_root>/codes/qc/qc_manifest.json

Usage (command-line):
    python3 qc_snapshots.py <project_root> <sourcedata> <subject> [subject2 ...]

    or with --all to read SubjectList.txt / SubjectListBIDS.txt:
    python3 qc_snapshots.py <project_root> <sourcedata> --all \
        --subjlist <path> --subjlist_bids <path>

Dependencies: nibabel, numpy, matplotlib  (all present in the Neuroimaging env).
nilearn is used when available for smarter display ranges; falls back gracefully.
"""

import argparse
import datetime
import json
import os
import sys
import traceback
from pathlib import Path

import matplotlib
matplotlib.use("Agg")          # headless — no display required
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import nibabel as nib
import numpy as np

# ---------------------------------------------------------------------------
# Step catalogue
# Each entry: (zero-padded number, short_name, description)
# These are the pipeline stages that produce or modify subject images.
# ---------------------------------------------------------------------------
# NEW ORDER (RETROICOR → fMRIPrep): physio is removed from the BOLD in native
# space before fMRIPrep, so the fMRIPrep images below are physio-cleaned.
STEPS = [
    ("01", "raw_bold",        "Raw BOLD (BIDS)"),
    ("02", "raw_t1w",         "Raw T1w (BIDS)"),
    ("03", "retro_corrected", "RETROICOR-corrected BOLD (native)"),
    ("04", "fmriprep_t1w",    "fMRIPrep T1w-space BOLD (physio-clean)"),
    ("05", "fmriprep_mni",    "fMRIPrep MNI-space BOLD (physio-clean)"),
    ("06", "brain_mask",      "fMRIPrep brain mask"),
    ("07", "t1_preproc",      "fMRIPrep T1w preprocessed"),
    ("08", "first_level_con", "First-level contrast (con_0001)"),
    ("09", "mni_wcon",        "MNI-warped contrast (wcon_0001)"),
]

STEP_MAP = {s[1]: s for s in STEPS}  # name -> (num, name, desc)


# ---------------------------------------------------------------------------
# Path resolution: given cfg-level variables, return candidate NIfTI paths
# for each step and one subject.
# ---------------------------------------------------------------------------

def _glob_first(pattern_dir: Path, glob_pattern: str):
    """Return the first match or None."""
    if not pattern_dir.is_dir():
        return None
    hits = sorted(pattern_dir.glob(glob_pattern))
    return hits[0] if hits else None


def locate_step_files(project_root: str, sourcedata: str, bids_subj: str) -> dict:
    """
    Return {step_name: Path_or_None} for every QC step for *bids_subj*.

    bids_subj: BIDS subject label, e.g. 'sub-7T1019HC042726'
    Assumes standard BIDS layout under sourcedata.
    """
    sd = Path(sourcedata)
    fp_der = sd / "derivatives" / "fmriprep"
    spm_fl = sd / "derivatives" / "spm" / "first_level"

    # Session label — check for ses-01 first, then any session
    def _ses_dir(base: Path, sub: str) -> Path | None:
        p01 = base / sub / "ses-01"
        if p01.is_dir():
            return p01
        hits = sorted((base / sub).glob("ses-*")) if (base / sub).is_dir() else []
        return hits[0] if hits else None

    # BIDS raw dirs
    bids_anat = _ses_dir(sd, bids_subj)
    bids_func_dir = (bids_anat / "func") if bids_anat else None
    bids_anat_dir = (bids_anat / "anat") if bids_anat else None

    # fMRIPrep derivative dirs
    fp_ses = _ses_dir(fp_der, bids_subj)
    fp_func = (fp_ses / "func") if fp_ses else None
    fp_anat = (fp_ses / "anat") if fp_ses else None

    def _try(directory, pattern):
        if directory is None or not directory.is_dir():
            return None
        return _glob_first(directory, pattern)

    # RETROICOR-corrected native BOLD (step04 output)
    retro_out = sd / "derivatives" / "physio" / bids_subj / "retroicor" / "output"

    locs = {
        "raw_bold":        _try(bids_func_dir, "*_bold.nii.gz")
                           or _try(bids_func_dir, "*_bold.nii"),
        "raw_t1w":         _try(bids_anat_dir, "*_T1w.nii.gz")
                           or _try(bids_anat_dir, "*_T1w.nii"),
        "retro_corrected": _try(retro_out, "*_retro-corrected.nii.gz"),
        "fmriprep_t1w":    _try(fp_func, "*_space-T1w_desc-preproc_bold.nii.gz"),
        "fmriprep_mni":    _try(fp_func, "*_space-MNI152*_desc-preproc_bold.nii.gz"),
        "brain_mask":      _try(fp_func, "*_space-T1w_desc-brain_mask.nii.gz")
                           or _try(fp_anat, "*_desc-brain_mask.nii.gz"),
        "t1_preproc":      _try(fp_anat, "*_desc-preproc_T1w.nii.gz"),
        "first_level_con": _glob_first(spm_fl / bids_subj, "*/con_0001.nii")
                           if (spm_fl / bids_subj).is_dir() else None,
        "mni_wcon":        _glob_first(spm_fl / bids_subj, "*/wcon_0001.nii")
                           if (spm_fl / bids_subj).is_dir() else None,
    }
    return locs


# ---------------------------------------------------------------------------
# Visualisation helpers
# ---------------------------------------------------------------------------

def _robust_range(data: np.ndarray, pct_lo=2, pct_hi=98):
    """Return (vmin, vmax) clipped to percentile range, ignoring NaN/zero."""
    flat = data.ravel()
    flat = flat[np.isfinite(flat)]
    if len(flat) == 0:
        return 0.0, 1.0
    vmin = float(np.percentile(flat, pct_lo))
    vmax = float(np.percentile(flat, pct_hi))
    if vmax <= vmin:
        vmax = vmin + 1e-6
    return vmin, vmax


def _mid_slice(vol: np.ndarray) -> tuple:
    """
    Return (axial_slice, coronal_slice, sagittal_slice) at the midpoint of
    each axis.  vol may be 3-D or 4-D (takes middle TR).
    """
    if vol.ndim == 4:
        mid_tr = vol.shape[3] // 2
        vol = vol[..., mid_tr]
    # vol is now guaranteed 3-D (X, Y, Z)
    cx = vol.shape[0] // 2
    cy = vol.shape[1] // 2
    cz = vol.shape[2] // 2
    # Standard radiological display convention: flip L-R for axial/coronal
    axial    = np.rot90(vol[:, :, cz])   # XY
    coronal  = np.rot90(vol[:, cy, :])   # XZ
    sagittal = np.rot90(vol[cx, :, :])   # YZ
    return axial, coronal, sagittal


def make_step_snapshot(nifti_path: Path, out_png: Path,
                       step_num: str, step_desc: str, subject: str):
    """
    Load *nifti_path*, extract mid-slices, save a 3-panel PNG montage to *out_png*.
    Returns True on success, False on failure (with exception printed to stderr).
    """
    try:
        img = nib.load(str(nifti_path))
        data = np.asanyarray(img.dataobj, dtype=np.float32)
        # Replace NaN/Inf with 0
        data = np.nan_to_num(data, nan=0.0, posinf=0.0, neginf=0.0)

        axial, coronal, sagittal = _mid_slice(data)
        vmin, vmax = _robust_range(data)

        cmap = "gray"
        # For mask images: binary colormap
        unique = np.unique(data[np.isfinite(data)])
        if len(unique) <= 3:
            cmap = "gray"
            vmin, vmax = 0, 1

        fig, axes = plt.subplots(1, 3, figsize=(10, 3.5),
                                 facecolor="#1a1a1a",
                                 gridspec_kw={"wspace": 0.04})
        fig.patch.set_facecolor("#1a1a1a")

        planes = [axial, coronal, sagittal]
        plane_labels = ["Axial", "Coronal", "Sagittal"]
        for ax, plane, lbl in zip(axes, planes, plane_labels):
            ax.imshow(plane, cmap=cmap, origin="lower",
                      vmin=vmin, vmax=vmax, interpolation="bilinear")
            ax.set_title(lbl, color="#aaaaaa", fontsize=9, pad=3)
            ax.axis("off")
            ax.set_facecolor("#1a1a1a")

        # Suptitle: step number, description, subject
        title = f"Step {step_num} — {step_desc}    [{subject}]"
        fig.suptitle(title, color="#e0e0e0", fontsize=10, y=0.97,
                     fontweight="bold")

        # File info strip at the bottom
        fname = Path(nifti_path).name
        fig.text(0.5, 0.01, fname, ha="center", va="bottom",
                 color="#666666", fontsize=7)

        out_png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out_png), dpi=120, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        plt.close(fig)
        return True

    except Exception as exc:
        print(f"  [QC] ERROR {nifti_path}: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        plt.close("all")
        return False


def make_pipeline_montage(step_pngs: list, out_png: Path, subject: str):
    """
    Assemble all per-step PNGs (list of Path objects, any of which may not
    exist) into one tall combined strip.  Missing steps show a placeholder.
    Returns True on success.
    """
    from PIL import Image  # PIL is in the env — used only here
    try:
        imgs = []
        for p in step_pngs:
            if p is not None and Path(p).is_file():
                imgs.append((str(p), True))
            else:
                imgs.append((str(p) if p else "MISSING", False))

        if not any(ok for _, ok in imgs):
            return False

        rows = []
        thumb_w = 960
        thumb_h = 280
        from PIL import ImageDraw, ImageFont
        for src, ok in imgs:
            if ok:
                im = Image.open(src).convert("RGB")
                im = im.resize((thumb_w, thumb_h), Image.LANCZOS)
            else:
                im = Image.new("RGB", (thumb_w, thumb_h), color=(28, 28, 28))
                draw = ImageDraw.Draw(im)
                label = f"MISSING: {Path(src).name}" if src and src != "MISSING" else "MISSING"
                draw.text((20, thumb_h // 2 - 10), label, fill=(120, 120, 120))
            rows.append(im)

        gap = 4
        total_h = sum(r.height for r in rows) + gap * (len(rows) - 1)
        canvas = Image.new("RGB", (thumb_w, total_h), color=(14, 14, 14))
        y = 0
        for r in rows:
            canvas.paste(r, (0, y))
            y += r.height + gap

        out_png.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(str(out_png))
        return True

    except Exception as exc:
        print(f"  [QC] Montage error: {exc}", file=sys.stderr)
        traceback.print_exc(file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Manifest
# ---------------------------------------------------------------------------

def load_manifest(manifest_path: Path) -> dict:
    if manifest_path.is_file():
        try:
            with open(manifest_path) as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_manifest(manifest_path: Path, manifest: dict):
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=2)


# ---------------------------------------------------------------------------
# Core per-subject runner
# ---------------------------------------------------------------------------

def run_subject_qc(project_root: str, sourcedata: str, bids_subj: str,
                   manifest: dict, print_fn=print) -> dict:
    """
    Generate all QC snapshots for *bids_subj*.

    Updates and returns the *manifest* dict (caller is responsible for saving).
    """
    qc_dir = Path(project_root) / "codes" / "qc" / bids_subj
    qc_dir.mkdir(parents=True, exist_ok=True)

    print_fn(f"[QC] Subject: {bids_subj}  ->  {qc_dir}")

    locs = locate_step_files(project_root, sourcedata, bids_subj)

    # Build per-step results
    subject_record = {
        "subject":   bids_subj,
        "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "steps":     {},
    }

    step_pngs_ordered = []   # for the pipeline montage, in step order

    for num, name, desc in STEPS:
        nifti_path = locs.get(name)
        png_name = f"{num}_{name}_{bids_subj}.png"
        png_path = qc_dir / png_name

        if nifti_path is None or not Path(nifti_path).is_file():
            status = "missing"
            note   = "input NIfTI not found"
            print_fn(f"  [QC] {num} {name}: SKIP ({note})")
            step_pngs_ordered.append(None)
        else:
            print_fn(f"  [QC] {num} {name}: {Path(nifti_path).name}")
            ok = make_step_snapshot(Path(nifti_path), png_path,
                                    num, desc, bids_subj)
            if ok:
                status = "done"
                note   = str(nifti_path)
                step_pngs_ordered.append(png_path)
                print_fn(f"    -> {png_path.name}")
            else:
                status = "error"
                note   = "snapshot generation failed"
                step_pngs_ordered.append(None)
                print_fn(f"    -> ERROR generating snapshot")

        subject_record["steps"][name] = {
            "num":    num,
            "desc":   desc,
            "status": status,
            "note":   note,
            "png":    str(png_path) if status == "done" else None,
        }

    # Pipeline montage
    montage_path = qc_dir / f"00_pipeline_montage_{bids_subj}.png"
    print_fn(f"  [QC] Building pipeline montage…")
    # Try PIL first; fall back to matplotlib if PIL not available
    montage_ok = _make_montage_fallback(step_pngs_ordered, montage_path,
                                        bids_subj, print_fn)
    subject_record["pipeline_montage"] = {
        "path":   str(montage_path) if montage_ok else None,
        "status": "done" if montage_ok else "error",
    }
    if montage_ok:
        print_fn(f"    -> {montage_path.name}")
    else:
        print_fn(f"    -> Montage failed (see stderr)")

    manifest[bids_subj] = subject_record
    return manifest


def _make_montage_fallback(step_pngs: list, out_png: Path,
                           subject: str, print_fn=print) -> bool:
    """Try PIL montage first, then fall back to a matplotlib tile grid."""
    try:
        from PIL import Image
        return make_pipeline_montage(step_pngs, out_png, subject)
    except ImportError:
        pass

    # matplotlib fallback: tight subplot grid
    try:
        existing = [(i, p) for i, p in enumerate(step_pngs) if p and Path(p).is_file()]
        if not existing:
            return False
        n = len(existing)
        ncols = 2
        nrows = (n + ncols - 1) // ncols
        fig, axes = plt.subplots(nrows, ncols,
                                 figsize=(14, 4 * nrows),
                                 facecolor="#1a1a1a")
        axes = np.array(axes).flatten()
        for ax in axes:
            ax.axis("off")
            ax.set_facecolor("#1a1a1a")
        for plot_idx, (step_i, png_path) in enumerate(existing):
            if plot_idx >= len(axes):
                break
            try:
                img = plt.imread(str(png_path))
                axes[plot_idx].imshow(img)
                step_info = STEPS[step_i]
                axes[plot_idx].set_title(
                    f"{step_info[0]} {step_info[2]}",
                    color="#aaaaaa", fontsize=8)
            except Exception:
                pass
        fig.suptitle(f"Pipeline QC — {subject}", color="#e0e0e0",
                     fontsize=11, fontweight="bold")
        out_png.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(str(out_png), dpi=100, bbox_inches="tight",
                    facecolor=fig.get_facecolor())
        plt.close(fig)
        return True
    except Exception as exc:
        print_fn(f"    [QC] Montage fallback failed: {exc}")
        plt.close("all")
        return False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Generate QC snapshots for the BIDS fMRI pipeline.")
    parser.add_argument("project_root",
                        help="Project root directory (contains codes/, rawdata/, sourcedata/)")
    parser.add_argument("sourcedata",
                        help="BIDS sourcedata directory")
    parser.add_argument("subjects", nargs="*",
                        help="BIDS subject IDs (e.g. sub-7T1019HC042726)")
    parser.add_argument("--all", dest="all_subjects", action="store_true",
                        help="Process all subjects from subject lists")
    parser.add_argument("--subjlist", default=None,
                        help="Path to SubjectList.txt (raw IDs)")
    parser.add_argument("--subjlist_bids", default=None,
                        help="Path to SubjectListBIDS.txt (BIDS IDs)")
    args = parser.parse_args()

    project_root = args.project_root
    sourcedata   = args.sourcedata

    # Resolve BIDS subject list
    subjects = list(args.subjects)
    if args.all_subjects:
        # Try SubjectListBIDS first, then SubjectList with sub- prepend
        bids_list = args.subjlist_bids or str(
            Path(project_root) / "codes" / "SubjectListBIDS.txt")
        raw_list  = args.subjlist or str(
            Path(project_root) / "codes" / "SubjectList.txt")
        if os.path.isfile(bids_list):
            with open(bids_list) as f:
                subjects = [ln.strip() for ln in f if ln.strip()]
        elif os.path.isfile(raw_list):
            with open(raw_list) as f:
                raw = [ln.strip() for ln in f if ln.strip()]
            subjects = ["sub-" + r.replace("_", "") for r in raw]

    if not subjects:
        print("No subjects specified and none found in subject lists.", file=sys.stderr)
        sys.exit(1)

    manifest_path = Path(project_root) / "codes" / "qc" / "qc_manifest.json"
    manifest = load_manifest(manifest_path)

    for subj in subjects:
        if not subj.startswith("sub-"):
            subj_bids = "sub-" + subj.replace("_", "")
            print(f"[QC] Auto-converting raw ID {subj} -> {subj_bids}")
            subj = subj_bids
        try:
            manifest = run_subject_qc(project_root, sourcedata, subj,
                                      manifest, print_fn=print)
        except Exception as exc:
            print(f"[QC] FAILED for {subj}: {exc}", file=sys.stderr)
            traceback.print_exc(file=sys.stderr)
        finally:
            save_manifest(manifest_path, manifest)

    print(f"\n[QC] Manifest saved: {manifest_path}")
    print(f"[QC] Done. Processed {len(subjects)} subject(s).")


if __name__ == "__main__":
    main()
