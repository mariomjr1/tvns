#!/usr/bin/env python3
"""
assemble_corrected_bids.py
Build a BIDS dataset whose func BOLDs are the RETROICOR-corrected images, so
fMRIPrep can run on physiologically-cleaned data (new pipeline: RETROICOR → fMRIPrep).

For one subject it mirrors the raw BIDS layout into <corrected_bids> by:
  - copying the top-level dataset files (dataset_description.json, participants.*,
    README, .bidsignore) once,
  - symlinking the subject's anat/ and fmap/ (untouched by RETROICOR),
  - placing each RETROICOR output  <base>_bold_retro-corrected.nii.gz  as the BIDS
    raw name  <base>_bold.nii.gz  in func/, and copying the original <base>_bold.json
    (keeps SliceTiming/RepetitionTime so fMRIPrep can do slice-timing correction).

Same filenames are kept so fieldmap `IntendedFor` entries stay valid, and RETROICOR
preserves the NIfTI header/affine (retroicor_batch uses niftiinfo), so geometry matches.

Usage:
  assemble_corrected_bids.py <sourcedata> <subject> <corrected_bids> \
      [--session 01] [--retro-output DIR] [--link-func]

Args:
  sourcedata        raw BIDS root (e.g. <project>/sourcedata)
  subject           BIDS subject (sub-XXXX)
  corrected_bids    output BIDS root (e.g. <project>/sourcedata_retrocorr)
  --session         session label, no 'ses-'           (default 01)
  --retro-output    RETROICOR output dir with *_retro-corrected.nii.gz
                    (default <sourcedata>/derivatives/physio/<subject>/retroicor/output)
  --link-func       symlink corrected BOLDs instead of copying (default copy)

Created by Mario Murakami  (RETROICOR → fMRIPrep reorder)
"""

import argparse
import os
import shutil
import sys
import json
from pathlib import Path

try:                       # optional — geometry verification (Task 13)
    import nibabel as nib
    import numpy as np
    _HAVE_NIB = True
except Exception:          # noqa: BLE001
    _HAVE_NIB = False

TOPLEVEL = ["dataset_description.json", "participants.tsv", "participants.json",
            "README", "README.md", ".bidsignore", "dataset_description.JSON"]
RETRO_SUFFIX = "_retro-corrected.nii.gz"


def _ensure_dataset_description(corrected_root: Path, sourcedata: Path):
    """fMRIPrep requires dataset_description.json. Copy from source; synthesize if absent."""
    dd = corrected_root / "dataset_description.json"
    if dd.exists():
        return
    src_dd = sourcedata / "dataset_description.json"
    if src_dd.exists():
        shutil.copy2(src_dd, dd)
    else:
        dd.write_text('{\n  "Name": "RETROICOR-corrected",\n  "BIDSVersion": "1.8.0"\n}\n')


def _copy_toplevel(corrected_root: Path, sourcedata: Path):
    corrected_root.mkdir(parents=True, exist_ok=True)
    for name in TOPLEVEL:
        src = sourcedata / name
        dst = corrected_root / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
    _ensure_dataset_description(corrected_root, sourcedata)


def _symlink_dir(src: Path, dst: Path):
    """Mirror a directory by symlinking each file (keeps the corrected tree light)."""
    if not src.is_dir():
        return 0
    dst.mkdir(parents=True, exist_ok=True)
    n = 0
    for f in sorted(src.iterdir()):
        if not f.is_file():
            continue
        link = dst / f.name
        if link.exists() or link.is_symlink():
            link.unlink()
        os.symlink(os.path.realpath(f), link)
        n += 1
    return n


def assemble_subject(sourcedata: Path, subject: str, corrected_root: Path,
                     session: str, retro_output: Path, link_func: bool) -> dict:
    ses = f"ses-{session}"
    src_subj = sourcedata / subject / ses
    if not src_subj.is_dir():
        # tolerate no-session layout
        src_subj = sourcedata / subject
        ses_out = ""
    else:
        ses_out = ses

    dst_subj = corrected_root / subject / ses_out if ses_out else corrected_root / subject

    _copy_toplevel(corrected_root, sourcedata)

    # anat + fmap: symlink (untouched by RETROICOR)
    n_anat = _symlink_dir(src_subj / "anat", dst_subj / "anat")
    n_fmap = _symlink_dir(src_subj / "fmap", dst_subj / "fmap")

    # func: corrected BOLD (renamed to BIDS) + original json
    src_func = src_subj / "func"
    dst_func = dst_subj / "func"
    dst_func.mkdir(parents=True, exist_ok=True)

    if not retro_output.is_dir():
        raise SystemExit(f"[ERROR] RETROICOR output dir not found: {retro_output}")

    corrected = sorted(retro_output.glob(f"{subject}_*{RETRO_SUFFIX}"))
    if not corrected:
        corrected = sorted(retro_output.glob(f"*{RETRO_SUFFIX}"))
    if not corrected:
        raise SystemExit(f"[ERROR] No *{RETRO_SUFFIX} in {retro_output}")

    n_bold = 0
    missing_json = []
    for cf in corrected:
        # RETROICOR names are "<...>_bold_retro-corrected.nii.gz" (base already ends
        # in _bold); strip the suffix to recover the BIDS "<...>_bold.nii.gz".
        bids_name = cf.name[: -len(RETRO_SUFFIX)] + ".nii.gz"
        dst_bold = dst_func / bids_name
        if dst_bold.exists() or dst_bold.is_symlink():
            dst_bold.unlink()
        if link_func:
            os.symlink(os.path.realpath(cf), dst_bold)
        else:
            shutil.copy2(cf, dst_bold)
        # original json sidecar (has SliceTiming + RepetitionTime)
        json_name = bids_name[:-len(".nii.gz")] + ".json"
        src_json = src_func / json_name
        if src_json.exists():
            shutil.copy2(src_json, dst_func / json_name)
        else:
            missing_json.append(json_name)
        n_bold += 1

    return {"subject": subject, "anat": n_anat, "fmap": n_fmap,
            "bold": n_bold, "missing_json": missing_json, "dst": str(dst_subj)}


# ── Integrity verification (Task 13) ──────────────────────────────────────────
def verify_subject(sourcedata: Path, subject: str, corrected_root: Path, session: str):
    """Check the assembled corrected BIDS against the raw BIDS. Returns (issues, checks).
    issues = list of problem strings (empty = clean); checks = count performed."""
    issues, checks = [], 0
    ses = f"ses-{session}"
    src_subj = sourcedata / subject / ses
    dst_subj = corrected_root / subject / ses
    if not src_subj.is_dir():
        src_subj = sourcedata / subject
        dst_subj = corrected_root / subject

    # multi-session guard: warn if the subject has sessions we did not process
    all_ses = sorted(p.name for p in (sourcedata / subject).glob("ses-*")) \
        if (sourcedata / subject).is_dir() else []
    if len(all_ses) > 1:
        issues.append(f"subject has {len(all_ses)} sessions {all_ses} — only '{ses}' assembled "
                      f"(re-run per session)")

    src_func, dst_func = src_subj / "func", dst_subj / "func"

    # 1) geometry: corrected BOLD affine/shape must match the raw BIDS BOLD
    if _HAVE_NIB:
        for cbold in sorted(dst_func.glob("*_bold.nii.gz")) if dst_func.is_dir() else []:
            raw = src_func / cbold.name
            if not raw.exists():
                issues.append(f"no raw BOLD matching corrected {cbold.name}")
                continue
            checks += 1
            try:
                a, b = nib.load(str(raw)), nib.load(str(cbold))
                if not np.allclose(a.affine, b.affine, atol=1e-3):
                    issues.append(f"AFFINE mismatch: {cbold.name}")
                if a.shape[:3] != b.shape[:3]:
                    issues.append(f"SHAPE mismatch {a.shape[:3]} vs {b.shape[:3]}: {cbold.name}")
                if a.ndim == 4 and b.ndim == 4 and a.shape[3] != b.shape[3]:
                    issues.append(f"#VOLUMES mismatch {a.shape[3]} vs {b.shape[3]}: {cbold.name}")
            except Exception as e:  # noqa: BLE001
                issues.append(f"could not read NIfTI ({cbold.name}): {e}")
    else:
        issues.append("nibabel not available — skipped geometry checks (affine/shape/#volumes)")

    # 2) fmap IntendedFor must resolve to a file in the corrected tree
    dst_fmap = dst_subj / "fmap"
    if dst_fmap.is_dir():
        for jf in sorted(dst_fmap.glob("*.json")):
            try:
                meta = json.loads(jf.read_text())
            except Exception:  # noqa: BLE001
                continue
            tgt = meta.get("IntendedFor")
            if not tgt:
                continue
            for rel in ([tgt] if isinstance(tgt, str) else tgt):
                checks += 1
                # IntendedFor is relative to the subject directory
                if not (corrected_root / subject / rel).exists():
                    issues.append(f"fmap IntendedFor unresolved: {jf.name} -> {rel}")

    # 3) every corrected BOLD has its JSON sidecar (SliceTiming for fMRIPrep STC)
    if dst_func.is_dir():
        for cbold in sorted(dst_func.glob("*_bold.nii.gz")):
            checks += 1
            if not cbold.with_name(cbold.name[:-len(".nii.gz")] + ".json").exists():
                issues.append(f"missing JSON sidecar for {cbold.name}")
    return issues, checks


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sourcedata")
    ap.add_argument("subject")
    ap.add_argument("corrected_bids")
    ap.add_argument("--session", default="01")
    ap.add_argument("--retro-output", default="")
    ap.add_argument("--link-func", action="store_true")
    ap.add_argument("--no-verify", action="store_true",
                    help="skip post-assembly integrity checks (Task 13)")
    args = ap.parse_args()

    sourcedata = Path(args.sourcedata)
    subject = args.subject if args.subject.startswith("sub-") else f"sub-{args.subject}"
    corrected_root = Path(args.corrected_bids)
    retro_output = Path(args.retro_output) if args.retro_output else \
        sourcedata / "derivatives" / "physio" / subject / "retroicor" / "output"

    s = assemble_subject(sourcedata, subject, corrected_root, args.session,
                         retro_output, args.link_func)
    print(f"[OK] {s['subject']}: {s['bold']} corrected BOLD, "
          f"{s['anat']} anat + {s['fmap']} fmap linked -> {s['dst']}")
    if s["missing_json"]:
        print(f"[WARN] missing JSON sidecars (fMRIPrep STC needs SliceTiming): "
              f"{s['missing_json']}", file=sys.stderr)

    # ── Integrity verification (Task 13) ──────────────────────────────────────
    if not args.no_verify:
        issues, checks = verify_subject(sourcedata, subject, corrected_root, args.session)
        if issues:
            print(f"[VERIFY] {checks} checks, {len(issues)} issue(s):", file=sys.stderr)
            for i in issues:
                print(f"   ✗ {i}", file=sys.stderr)
        else:
            print(f"[VERIFY] {checks} checks passed ✓ (geometry, fmap IntendedFor, JSON sidecars)")
        if not _HAVE_NIB:
            print("[VERIFY] note: install nibabel for affine/shape/#volume checks "
                  "(plus run bids-validator on the corrected root).")

    print(f"[DONE] corrected BIDS root: {corrected_root}")


if __name__ == "__main__":
    main()
