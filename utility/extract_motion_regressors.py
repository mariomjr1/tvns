#!/usr/bin/env python3
"""
extract_motion_regressors.py  (Task 01)
Build first-level motion nuisance regressors from fMRIPrep confounds.

Reads fMRIPrep's *_desc-confounds_timeseries.tsv and writes a plain-text
*_motion_regressors.txt containing:
    - the 6 rigid-body parameters  (trans_x trans_y trans_z rot_x rot_y rot_z)
    - one FD "spike" regressor per volume whose framewise_displacement exceeds
      a threshold (a unit impulse: 1 at that volume, 0 elsewhere). This censors
      high-motion volumes in the GLM.

NO temporal derivatives and NO squared terms are added (by design — Task 01).

The output is a headerless, space-delimited matrix (n_volumes x [6 + n_spikes])
that SPM reads directly as a `multi_reg` file. A .log is written per run.

Usage:
  # one confounds file:
  extract_motion_regressors.py <confounds.tsv> <output_dir> [options]
  # or a whole fMRIPrep func dir (globs *_desc-confounds_timeseries.tsv):
  extract_motion_regressors.py <func_dir> <output_dir> --subject sub-XXX --session 01

Options:
  --subject S     only process TSVs for this BIDS subject (when input is a dir)
  --session 01    only process TSVs for this session       (when input is a dir)
  --fd-thresh F   FD spike threshold in mm (default 0.5)
  --log-dir DIR   where to write the .log (default: <output_dir>/logs)
  --no-spikes     write only the 6 rigid params (skip FD spike regressors)

Created by Mario Murakami  (pipeline Task 01)
"""

import argparse
import csv
import os
import sys
from datetime import datetime

import numpy as np

CONFOUNDS_SUFFIX = "_desc-confounds_timeseries.tsv"
RIGID_COLS = ["trans_x", "trans_y", "trans_z", "rot_x", "rot_y", "rot_z"]
FD_COL = "framewise_displacement"


def _read_confounds(tsv_path):
    """Return (columns dict of name->float list, n_rows). 'n/a'/'' -> NaN."""
    with open(tsv_path, newline="") as fh:
        reader = csv.DictReader(fh, delimiter="\t")
        fieldnames = reader.fieldnames or []
        data = {name: [] for name in fieldnames}
        n = 0
        for row in reader:
            n += 1
            for name in fieldnames:
                val = row.get(name, "")
                if val is None or val.strip() == "" or val.strip().lower() == "n/a":
                    data[name].append(np.nan)
                else:
                    try:
                        data[name].append(float(val))
                    except ValueError:
                        data[name].append(np.nan)
    return data, n, fieldnames


def _out_name(tsv_path):
    base = os.path.basename(tsv_path)
    if base.endswith(CONFOUNDS_SUFFIX):
        stem = base[: -len(CONFOUNDS_SUFFIX)]
    else:
        stem = os.path.splitext(base)[0]
    return stem + "_motion_regressors.txt"


def process_one(tsv_path, output_dir, fd_thresh=0.5, add_spikes=True):
    """Build the regressor matrix for one confounds TSV. Returns a summary dict."""
    data, n_rows, fieldnames = _read_confounds(tsv_path)

    missing = [c for c in RIGID_COLS if c not in fieldnames]
    if missing:
        raise ValueError(
            f"Confounds file missing rigid columns {missing}: {os.path.basename(tsv_path)}"
        )

    motion = np.column_stack([np.asarray(data[c], dtype=float) for c in RIGID_COLS])
    # fMRIPrep leaves the first rigid-param row finite; guard NaNs just in case.
    motion = np.nan_to_num(motion, nan=0.0)

    # ── FD spike regressors ────────────────────────────────────────────────────
    spike_idx = []
    fd = None
    if FD_COL in fieldnames:
        fd = np.asarray(data[FD_COL], dtype=float)  # first volume is NaN in fMRIPrep
        if add_spikes:
            # NaN never exceeds threshold -> first volume is not censored
            spike_idx = list(np.where(fd > fd_thresh)[0])

    if spike_idx:
        spikes = np.zeros((n_rows, len(spike_idx)), dtype=float)
        for j, vi in enumerate(spike_idx):
            spikes[vi, j] = 1.0
        out = np.column_stack([motion, spikes])
    else:
        out = motion

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, _out_name(tsv_path))
    np.savetxt(out_path, out, fmt="%.6f", delimiter=" ")

    fd_finite = fd[np.isfinite(fd)] if fd is not None else np.array([])
    summary = {
        "tsv": os.path.basename(tsv_path),
        "out": out_path,
        "n_volumes": n_rows,
        "n_motion_cols": motion.shape[1],
        "n_spikes": len(spike_idx),
        "spike_volumes": [int(v) for v in spike_idx],
        "fd_thresh": fd_thresh,
        "fd_mean": float(np.mean(fd_finite)) if fd_finite.size else float("nan"),
        "fd_max": float(np.max(fd_finite)) if fd_finite.size else float("nan"),
        "fd_available": fd is not None,
        "total_cols": out.shape[1],
        "pct_censored": (100.0 * len(spike_idx) / n_rows) if n_rows else 0.0,
    }
    return summary


def _write_log(log_dir, subject_tag, summaries, fd_thresh, add_spikes):
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{subject_tag}_motion_regressors.log")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(log_path, "a") as fh:
        fh.write("=" * 70 + "\n")
        fh.write(f"extract_motion_regressors.py  (Task 01)   {ts}\n")
        fh.write(f"Subject: {subject_tag}\n")
        fh.write(f"Regressors: 6 rigid (trans_x/y/z, rot_x/y/z)"
                 f"{' + FD spikes' if add_spikes else ' (spikes disabled)'}\n")
        fh.write(f"FD spike threshold: {fd_thresh} mm\n")
        fh.write("-" * 70 + "\n")
        for s in summaries:
            fh.write(f"[{s['tsv']}]\n")
            if not s["fd_available"]:
                fh.write("  WARNING: no framewise_displacement column — no spikes added\n")
            fh.write(
                f"  volumes={s['n_volumes']}  motion_cols={s['n_motion_cols']}  "
                f"FD_spikes={s['n_spikes']} ({s['pct_censored']:.1f}% censored)  "
                f"total_cols={s['total_cols']}\n"
            )
            fh.write(
                f"  FD mean={s['fd_mean']:.4f}  FD max={s['fd_max']:.4f}\n"
            )
            if s["n_spikes"]:
                fh.write(f"  censored volumes: {s['spike_volumes']}\n")
            fh.write(f"  -> {s['out']}\n")
        fh.write("=" * 70 + "\n\n")
    return log_path


def _collect_inputs(input_path, subject, session):
    if os.path.isfile(input_path):
        return [input_path]
    if os.path.isdir(input_path):
        files = sorted(
            os.path.join(input_path, f)
            for f in os.listdir(input_path)
            if f.endswith(CONFOUNDS_SUFFIX)
        )
        if subject:
            files = [f for f in files if os.path.basename(f).startswith(subject)]
        if session:
            files = [f for f in files if f"ses-{session}" in os.path.basename(f)]
        return files
    return []


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("input", help="confounds TSV file OR fMRIPrep func directory")
    ap.add_argument("output_dir", help="where to write *_motion_regressors.txt")
    ap.add_argument("--subject", default="", help="BIDS subject filter (dir input)")
    ap.add_argument("--session", default="", help="session filter, no 'ses-' (dir input)")
    ap.add_argument("--fd-thresh", type=float, default=0.5,
                    help="FD spike threshold in mm (default 0.5)")
    ap.add_argument("--log-dir", default="", help="log directory (default <output_dir>/logs)")
    ap.add_argument("--no-spikes", action="store_true",
                    help="write only the 6 rigid params (no FD spike regressors)")
    args = ap.parse_args()

    add_spikes = not args.no_spikes
    inputs = _collect_inputs(args.input, args.subject, args.session)
    if not inputs:
        print(f"[ERROR] No confounds TSV found for input: {args.input}", file=sys.stderr)
        sys.exit(1)

    log_dir = args.log_dir or os.path.join(args.output_dir, "logs")
    subject_tag = args.subject or "motion"

    summaries = []
    n_fail = 0
    for tsv in inputs:
        try:
            s = process_one(tsv, args.output_dir, fd_thresh=args.fd_thresh,
                            add_spikes=add_spikes)
            summaries.append(s)
            print(f"[OK] {s['tsv']}: {s['n_motion_cols']} motion + {s['n_spikes']} "
                  f"FD spikes ({s['pct_censored']:.1f}% censored) -> {os.path.basename(s['out'])}")
        except Exception as e:  # noqa: BLE001 — report and continue over runs
            n_fail += 1
            print(f"[ERROR] {os.path.basename(tsv)}: {e}", file=sys.stderr)

    if summaries:
        log_path = _write_log(log_dir, subject_tag, summaries, args.fd_thresh, add_spikes)
        print(f"[LOG] {log_path}")

    print(f"Done. {len(summaries)} written, {n_fail} failed.")
    sys.exit(1 if (n_fail and not summaries) else 0)


if __name__ == "__main__":
    main()
