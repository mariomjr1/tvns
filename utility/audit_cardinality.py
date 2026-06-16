#!/usr/bin/env python3
"""
audit_cardinality.py  —  cross-stage volume/trigger cardinality audit (Task 25)

For each subject/run, collect the number of volumes / triggers / rows reported by
every pipeline stage that has run, write a per-subject CSV, roll the cohort up into
a flag log, and flag any run whose populated counts disagree.

This is an AUDIT ONLY. It never fails and never changes the pipeline — it acquires
everything and surfaces oddities for you to review. Each count is labelled with the
pipeline step that produced it, so a flag tells you exactly which step is off.

Stages audited (column = step that produces the count):
  step01_raw_bold    raw BIDS BOLD volumes            (NIfTI 4th dim)
  step02_mrtrig      MR-trigger rising edges          (physioparse parsed mat)
  step04_retro       RETROICOR-corrected BOLD volumes (NIfTI 4th dim)
  step05_fmriprep    fMRIPrep preprocessed BOLD vols  (NIfTI 4th dim)
  step05_confounds   fMRIPrep confounds rows          (TSV data rows)
  step06_motion      motion-regressor rows            (txt rows)

Flagging is advisory: the volume-based counts (step01/04/05/06) should match; the
trigger count (step02) is compared only when > 0, so intentionally triggerless
sequences (and fieldmaps, which never reach this audit) are not flagged for it.

Only BOLD runs are audited (sourcedata/<subj>/**/func/*_bold.nii.gz). Fieldmap /
topup acquisitions have no cross-stage counterpart, so they are not included.

Usage:
  python3 audit_cardinality.py <sourcedata> <subject> [subject2 ...] [--out <dir>]
  python3 audit_cardinality.py <sourcedata> --all [--subjlist_bids <path>] [--out <dir>]

Created by Mario Murakami
"""

import argparse
import csv
import datetime
import re
import sys
from pathlib import Path

import numpy as np

try:
    import scipy.io as sio
except ImportError:
    sio = None
try:
    import nibabel as nib
except ImportError:
    nib = None

# Column order: each count is tagged with the pipeline step that produces it.
STAGE_COLS = [
    "step01_raw_bold",
    "step02_mrtrig",
    "step04_retro",
    "step05_fmriprep",
    "step05_confounds",
    "step06_motion",
]
# Volume-based counts that must agree (trigger count is compared separately).
VOLUME_COLS = ["step01_raw_bold", "step04_retro", "step05_fmriprep",
               "step05_confounds", "step06_motion"]


# ── Count helpers ──────────────────────────────────────────────────────────────

def nifti_nvols(path):
    """Number of volumes (4th dim) from a NIfTI header; 1 if 3-D; None on error."""
    if path is None or nib is None:
        return None
    try:
        shape = nib.load(str(path)).shape
        return int(shape[3]) if len(shape) >= 4 else 1
    except Exception as exc:
        print(f"  [audit] WARN nifti {path}: {exc}", file=sys.stderr)
        return None


def count_mr_triggers(mrtrig, fs=1000, min_gap_s=0.3):
    """Discrete MR-trigger rising edges (debounced). Mirrors extract_stim_onsets."""
    d = np.diff(np.asarray(mrtrig, dtype=float))
    if d.size == 0 or d.max() <= 0:
        return 0
    cand = np.where(d > d.max() * 0.30)[0]
    if len(cand) == 0:
        return 0
    gap = int(min_gap_s * fs)
    trig = [int(cand[0])]
    for c in cand[1:]:
        if c - trig[-1] >= gap:
            trig.append(int(c))
    return len(trig)


def mrtrig_from_mat(path):
    """MR-trigger count from a physioparse parsed mat; None if unreadable."""
    if path is None or sio is None or not path.is_file():
        return None
    try:
        raw = sio.loadmat(str(path))
        if "MRTRIG" not in raw:
            return None
        fs_arr = raw.get("sampling_rate", np.array([[1000]]))
        fs = int(np.asarray(fs_arr).flatten()[0])
        return count_mr_triggers(raw["MRTRIG"].flatten(), fs=fs)
    except Exception as exc:
        print(f"  [audit] WARN mat {path}: {exc}", file=sys.stderr)
        return None


def tsv_rows(path):
    """Data rows in a TSV (excludes the header line); None if missing."""
    if path is None or not path.is_file():
        return None
    try:
        with open(path) as f:
            n = sum(1 for _ in f)
        return max(0, n - 1)
    except Exception:
        return None


def txt_rows(path):
    """Non-empty, non-comment rows in a text file; None if missing."""
    if path is None or not path.is_file():
        return None
    try:
        with open(path) as f:
            return sum(1 for ln in f if ln.strip() and not ln.lstrip().startswith("#"))
    except Exception:
        return None


# ── Run discovery / matching ───────────────────────────────────────────────────

def _first(base, pattern):
    if base is None or not base.is_dir():
        return None
    hits = sorted(base.rglob(pattern))
    return hits[0] if hits else None


def audit_subject(sourcedata, subj):
    """Return one record dict per BOLD run for *subj*."""
    sd = Path(sourcedata)
    raw_subj = sd / subj
    physio = sd / "derivatives" / "physio" / subj
    parsed = physio / "parsed"
    retro = physio / "retroicor" / "output"
    fmriprep = sd / "derivatives" / "fmriprep" / subj
    # first_level is shared (named by subject); also allow a per-subject variant
    motion_dirs = [sd / "derivatives" / "physio" / "first_level" / "02_motion_regressors",
                   physio / "first_level" / "02_motion_regressors"]

    records = []
    raw_bolds = sorted(raw_subj.rglob("*_bold.nii.gz")) if raw_subj.is_dir() else []
    for raw in raw_bolds:
        m = re.search(r"task-(\w+?)_run-(\d+)", raw.name)
        if not m:
            continue
        task, run = m.group(1), m.group(2)
        tr = f"task-{task}_run-{run}"

        mat = parsed / f"{tr}.mat"
        motion = None
        for md in motion_dirs:
            motion = _first(md, f"{subj}*{tr}*motion_regressors.txt")
            if motion:
                break

        rec = {
            "subject": subj, "task": task, "run": run,
            "step01_raw_bold":  nifti_nvols(raw),
            "step02_mrtrig":    mrtrig_from_mat(mat if mat.is_file() else None),
            "step04_retro":     nifti_nvols(_first(retro, f"*{tr}*retro-corrected.nii.gz")),
            "step05_fmriprep":  nifti_nvols(_first(fmriprep, f"*{tr}*desc-preproc_bold.nii.gz")),
            "step05_confounds": tsv_rows(_first(fmriprep, f"*{tr}*confounds*.tsv")),
            "step06_motion":    txt_rows(motion),
        }
        rec["flag"], rec["note"] = _flag(rec)
        records.append(rec)
    return records


def _flag(rec):
    """Advisory flag: volume counts must agree; trigger count compared only if >0."""
    vols = [rec[c] for c in VOLUME_COLS if rec[c] is not None]
    compared = list(vols)
    mt = rec["step02_mrtrig"]
    if mt is not None and mt > 0:
        compared.append(mt)
    if len(set(compared)) > 1:
        parts = [f"{c}={rec[c]}" for c in STAGE_COLS if rec[c] is not None]
        return "FLAG", "counts differ: " + ", ".join(parts)
    return "", ""


# ── Output ─────────────────────────────────────────────────────────────────────

_FIELDS = ["subject", "task", "run"] + STAGE_COLS + ["flag", "note"]


def write_subject_csv(physio_subj_dir, records):
    out = Path(physio_subj_dir) / "cardinality_audit.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        w.writeheader()
        for r in records:
            w.writerow(r)
    return out


def write_cohort(out_dir, all_records, print_fn=print):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "group_cardinality_audit.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        w.writeheader()
        for r in all_records:
            w.writerow(r)

    flagged = [r for r in all_records if r["flag"] == "FLAG"]
    n_subj = len({r["subject"] for r in all_records})
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    md_path = out / "group_cardinality_audit.md"
    with open(md_path, "w") as f:
        f.write("# Cross-stage cardinality audit\n\n")
        f.write(f"Generated: {ts}\n\n")
        f.write(f"- Subjects: {n_subj}\n")
        f.write(f"- Runs: {len(all_records)}\n")
        f.write(f"- Flagged (counts disagree): {len(flagged)}\n\n")
        f.write("Columns are labelled by the pipeline step that produces each count "
                "(step01 raw BOLD, step02 MR triggers, step04 RETROICOR, step05 "
                "fMRIPrep BOLD/confounds, step06 motion).\n\n")
        if flagged:
            f.write("## Flagged runs\n\n")
            f.write("| subject | task | run | " + " | ".join(STAGE_COLS) + " |\n")
            f.write("|" + "---|" * (3 + len(STAGE_COLS)) + "\n")
            for r in flagged:
                cells = [str(r[c]) if r[c] is not None else "—" for c in STAGE_COLS]
                f.write(f"| {r['subject']} | {r['task']} | {r['run']} | "
                        + " | ".join(cells) + " |\n")
        else:
            f.write("No flagged runs — all populated counts agree.\n")

    print_fn(f"[audit] {len(all_records)} run(s) across {n_subj} subject(s); "
             f"{len(flagged)} flagged.")
    print_fn(f"[audit] Wrote {csv_path}")
    print_fn(f"[audit] Wrote {md_path}")
    return {"subjects": n_subj, "runs": len(all_records), "flagged": len(flagged)}


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Cross-stage cardinality audit (Task 25). Audit only — never fails.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("sourcedata", help="BIDS sourcedata directory")
    ap.add_argument("subjects", nargs="*", help="BIDS subject IDs (sub-...)")
    ap.add_argument("--all", dest="all_subjects", action="store_true",
                    help="Audit every sub-* under sourcedata")
    ap.add_argument("--subjlist_bids", default=None,
                    help="Path to a file with one BIDS subject ID per line")
    ap.add_argument("--out", default=None,
                    help="Cohort report dir (default: <sourcedata>/../codes/qc, "
                         "else <sourcedata>/derivatives/qc)")
    args = ap.parse_args()

    sd = Path(args.sourcedata)
    subjects = list(args.subjects)
    if args.all_subjects:
        subjects = sorted(d.name for d in sd.iterdir()
                          if d.is_dir() and d.name.startswith("sub-"))
    elif args.subjlist_bids:
        with open(args.subjlist_bids) as f:
            subjects = [ln.strip() for ln in f if ln.strip()]
    if not subjects:
        print("No subjects specified.", file=sys.stderr)
        sys.exit(1)

    if args.out:
        out_dir = args.out
    else:
        codes = sd.parent / "codes" / "qc"
        out_dir = str(codes if (sd.parent / "codes").is_dir()
                      else sd / "derivatives" / "qc")

    all_records = []
    for subj in subjects:
        if not subj.startswith("sub-"):
            subj = "sub-" + subj.replace("_", "")
        recs = audit_subject(args.sourcedata, subj)
        if not recs:
            print(f"[audit] {subj}: no BOLD runs found", file=sys.stderr)
            continue
        sub_csv = write_subject_csv(sd / "derivatives" / "physio" / subj, recs)
        n_flag = sum(1 for r in recs if r["flag"] == "FLAG")
        print(f"[audit] {subj}: {len(recs)} run(s), {n_flag} flagged -> {sub_csv}")
        all_records.extend(recs)

    if all_records:
        write_cohort(out_dir, all_records)
    print("[audit] Done.")  # always exit 0


if __name__ == "__main__":
    main()
