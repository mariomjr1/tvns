#!/usr/bin/env python3
"""
build_group_covariates.py   (Task 08)
Build a tidy group-covariates table for the second-level GLM, from data fMRIPrep /
BIDS already produced — so age/sex come from the BIDS participants.tsv and mean
framewise displacement (mean_fd) is computed from the fMRIPrep confounds TSVs.

Output: a tab-separated file with columns
    participant_id   age   sex   mean_fd
(sex is numeric: male/m/1 -> 1, female/f/0/2 -> 0; blank if unknown). Missing
values are left blank; glm_spm_secondlevel_groups.m drops any covariate that is
not complete for the analysed subjects.

Usage:
  build_group_covariates.py --participants participants.tsv --fmriprep-dir DIR \
      --output group_covariates.tsv [--session 01] [--subjects sub-a sub-b ...]

Created by Mario Murakami  (group covariates — Task 08)
"""

import argparse
import csv
import glob
import os
import sys

CONFOUNDS_SUFFIX = "_desc-confounds_timeseries.tsv"


def _read_participants(path):
    """Return {participant_id: {'age':str, 'sex':str}} from a BIDS participants.tsv."""
    out = {}
    if not path or not os.path.isfile(path):
        return out
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        cols = {c.lower(): c for c in (reader.fieldnames or [])}
        pid = cols.get("participant_id") or cols.get("participant") or "participant_id"
        agec = cols.get("age")
        sexc = cols.get("sex") or cols.get("gender")
        for row in reader:
            sid = (row.get(pid) or "").strip()
            if not sid:
                continue
            if not sid.startswith("sub-"):
                sid = "sub-" + sid
            out[sid] = {"age": (row.get(agec, "").strip() if agec else ""),
                        "sex": (row.get(sexc, "").strip() if sexc else "")}
    return out


def _sex_to_numeric(s):
    s = (s or "").strip().lower()
    if s in ("m", "male", "1"):
        return "1"
    if s in ("f", "female", "0", "2"):
        return "0"
    return ""   # unknown


def _mean_fd(fmriprep_dir, subject, session):
    """Mean framewise_displacement across all of a subject's confounds TSVs."""
    func = os.path.join(fmriprep_dir, subject, f"ses-{session}", "func")
    tsvs = sorted(glob.glob(os.path.join(func, f"*{CONFOUNDS_SUFFIX}")))
    if not tsvs:  # tolerate no-session layout
        tsvs = sorted(glob.glob(os.path.join(fmriprep_dir, subject, "func",
                                             f"*{CONFOUNDS_SUFFIX}")))
    vals = []
    for tsv in tsvs:
        with open(tsv, newline="") as fh:
            for row in csv.DictReader(fh, delimiter="\t"):
                v = row.get("framewise_displacement", "")
                if v and v.strip().lower() not in ("n/a", "na", ""):
                    try:
                        vals.append(float(v))
                    except ValueError:
                        pass
    return (sum(vals) / len(vals)) if vals else None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--participants", default="", help="BIDS participants.tsv (age/sex)")
    ap.add_argument("--fmriprep-dir", default="", help="fMRIPrep derivatives dir (for mean_fd)")
    ap.add_argument("--output", required=True, help="output group_covariates.tsv")
    ap.add_argument("--session", default="01")
    ap.add_argument("--subjects", nargs="*", default=None,
                    help="subjects to include (default: all in participants.tsv)")
    args = ap.parse_args()

    parts = _read_participants(args.participants)
    subjects = args.subjects or sorted(parts.keys())
    subjects = [s if s.startswith("sub-") else "sub-" + s for s in subjects]
    if not subjects:
        print("[ERROR] no subjects (give --participants and/or --subjects)", file=sys.stderr)
        sys.exit(1)

    os.makedirs(os.path.dirname(os.path.abspath(args.output)) or ".", exist_ok=True)
    n_fd = n_age = n_sex = 0
    with open(args.output, "w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["participant_id", "age", "sex", "mean_fd"])
        for s in subjects:
            p = parts.get(s, {})
            age = p.get("age", "")
            sex = _sex_to_numeric(p.get("sex", ""))
            fd = _mean_fd(args.fmriprep_dir, s, args.session) if args.fmriprep_dir else None
            fd_s = f"{fd:.6f}" if fd is not None else ""
            n_age += bool(age); n_sex += bool(sex); n_fd += fd is not None
            w.writerow([s, age, sex, fd_s])

    print(f"[OK] {len(subjects)} subjects -> {args.output}")
    print(f"     present: age={n_age}, sex={n_sex}, mean_fd={n_fd}")
    if n_fd == 0 and args.fmriprep_dir:
        print("[WARN] no mean_fd computed — check the fMRIPrep confounds path/session.",
              file=sys.stderr)


if __name__ == "__main__":
    main()
