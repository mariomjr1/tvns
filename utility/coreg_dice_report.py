#!/usr/bin/env python3
"""
coreg_dice_report.py — does step05c brainstem refinement actually help? (decision: brainstem_refine)

Computes the Dice overlap of a subject's brainstem against a reference brainstem template
BEFORE (fMRIPrep-only warp) vs AFTER (fMRIPrep + step05c refine), across pilots, and writes
a decision report: KEEP if mean Dice improves by >= --improve, DROP if it doesn't improve,
REVIEW if marginal. Read it to decide whether the 05c step belongs in the methodology.

You provide the masks (produced on the cluster): the reference brainstem and, per subject,
the before- and after-refinement brainstem masks in the same (template) space. Or pass a
precomputed CSV (subject,dice_before,dice_after).

Dice needs nibabel (guarded); the verdict logic is pure stdlib (testable). Flag+log, exits 0.

Usage:
  python coreg_dice_report.py --reference tmpl_brainstem.nii.gz \
      --before s1_before.nii.gz s2_before.nii.gz --after s1_after.nii.gz s2_after.nii.gz \
      [--improve 0.02] [--qc-dir <project>/codes/qc]
  # or from a precomputed table:
  python coreg_dice_report.py --dice-csv dice.csv [--improve 0.02] [--qc-dir …]
"""
import argparse
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decision_report as D   # noqa: E402

DEFAULT_IMPROVE = 0.02


def verdict_from_dice(before, after, improve=DEFAULT_IMPROVE):
    """Pure stdlib verdict from paired Dice lists. Returns (verdict, message, metrics)."""
    pairs = [(b, a) for b, a in zip(before, after) if b == b and a == a]
    if not pairs:
        return "PENDING", "no Dice values — run on pilot masks", {}
    mb = sum(b for b, _ in pairs) / len(pairs)
    ma = sum(a for _, a in pairs) / len(pairs)
    d = ma - mb
    metrics = {"n": len(pairs), "dice_before": round(mb, 3), "dice_after": round(ma, 3),
               "delta": round(d, 3)}
    if d >= improve:
        return "KEEP", (f"05c improves brainstem Dice {mb:.3f}->{ma:.3f} (+{d:.3f} >= {improve}) "
                        f"across {len(pairs)} subj — keep the refinement."), metrics
    if d <= 0:
        return "DROP", (f"05c does NOT improve Dice ({mb:.3f}->{ma:.3f}, {d:+.3f}) — "
                        f"not worth it; use fMRIPrep-only."), metrics
    return "REVIEW", (f"05c improves Dice only marginally ({mb:.3f}->{ma:.3f}, +{d:.3f} < {improve}) "
                      f"— inspect per-subject + overlays before deciding."), metrics


def _dice(a_path, b_path):
    import numpy as np, nibabel as nib  # noqa: E401
    A = np.asanyarray(nib.load(a_path).dataobj) > 0.5
    B = np.asanyarray(nib.load(b_path).dataobj) > 0.5
    if A.shape != B.shape:
        return float("nan")
    inter = int((A & B).sum()); denom = int(A.sum() + B.sum())
    return (2.0 * inter / denom) if denom else float("nan")


def main():
    ap = argparse.ArgumentParser(description="step05c brainstem refinement Dice report")
    ap.add_argument("--reference", default="")
    ap.add_argument("--before", nargs="*", default=[])
    ap.add_argument("--after", nargs="*", default=[])
    ap.add_argument("--dice-csv", default="", help="precomputed subject,dice_before,dice_after")
    ap.add_argument("--improve", type=float, default=DEFAULT_IMPROVE)
    ap.add_argument("--qc-dir", default="codes/qc")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    before, after, subjects = [], [], []
    if a.dice_csv and os.path.isfile(a.dice_csv):
        for r in csv.DictReader(open(a.dice_csv)):
            subjects.append(r.get("subject", "?"))
            before.append(float(r.get("dice_before", "nan") or "nan"))
            after.append(float(r.get("dice_after", "nan") or "nan"))
    elif a.before and a.after and a.reference:
        try:
            import nibabel  # noqa: F401
        except Exception as exc:  # noqa: BLE001
            print(f"[coreg-dice] WARN needs nibabel ({exc}) — skipped", file=sys.stderr)
            D.write_decision(a.qc_dir, "brainstem_refine", "PENDING",
                             "nibabel missing — run on the cluster", title=None)
            return 0
        for b, c in zip(a.before, a.after):
            subjects.append(os.path.basename(b))
            before.append(_dice(b, a.reference))
            after.append(_dice(c, a.reference))
    else:
        print("[coreg-dice] WARN give --dice-csv OR --reference + --before + --after — skipped",
              file=sys.stderr)
        D.write_decision(a.qc_dir, "brainstem_refine", "PENDING",
                         "run coreg_dice_report.py on pilot masks")
        return 0

    verdict, msg, metrics = verdict_from_dice(before, after, a.improve)
    out = a.out or os.path.join(a.qc_dir, "brainstem_coreg_dice_report.md")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        f.write("# step05c brainstem refinement — Dice before vs after\n\n")
        f.write(f"**Verdict: {verdict}** — {msg}\n\n")
        f.write("| subject | dice_before | dice_after | delta |\n|---|---|---|---|\n")
        for s, b, c in zip(subjects, before, after):
            dd = (c - b) if (b == b and c == c) else float("nan")
            f.write(f"| {s} | {b:.3f} | {c:.3f} | {dd:+.3f} |\n")
    D.write_decision(a.qc_dir, "brainstem_refine", verdict, msg, metrics=metrics)
    print(f"[coreg-dice] {verdict}: {msg}\n[coreg-dice] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
