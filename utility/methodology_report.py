#!/usr/bin/env python3
"""
methodology_report.py — roll every recorded methodology decision into ONE report.

Reads `codes/qc/decisions/*.json` (written by the validation steps) and writes
`codes/qc/methodology_decisions.md` (+ .csv): one table of every decision with its
verdict (KEEP / DROP / REVIEW / PENDING), the numbers, and a recommendation — so you read
one file and decide which parts of the methodology to keep / whether they make sense.

Run it any time (after some/all validation steps); decisions not yet run show as PENDING
with the command to produce them. Stdlib only; never fails.

Usage:
  python methodology_report.py [--qc-dir <project>/codes/qc]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decision_report as D   # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Aggregate methodology decisions into one report")
    ap.add_argument("--qc-dir", default="codes/qc",
                    help="QC dir holding decisions/ (default codes/qc)")
    a = ap.parse_args()
    res = D.aggregate(a.qc_dir)
    print(f"[methodology] {res['n']} decision(s) recorded; read {res['md']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
