#!/usr/bin/env python3
"""
chain_report.py — which step10b transform chain (C1/C2/C3) is correct? (decision: transform_chain)

For one pilot, computes how much of each candidate atlas-in-native lands inside the subject's
brainstem mask (overlap fraction) + the atlas centroid, ranks the chains, and writes a
decision report. The verdict is **REVIEW**: overlap narrows it down, but the chain MUST be
confirmed by nucleus-scale anatomy in `overlay_atlas_native.py` (a wrong chain can still have
high gross brainstem overlap).

Overlap needs nibabel (guarded); ranking is pure stdlib (testable). Flag+log, exits 0.

Usage:
  python chain_report.py --brainstem-mask <native_brainstem.nii.gz> \
      --c1 <…_atlas-in-native_C1.nii.gz> --c2 <…_C2…> --c3 <…_C3…> [--qc-dir …]
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import decision_report as D   # noqa: E402


def rank_chains(overlaps):
    """Pure stdlib: {chain: overlap_fraction} -> (best, message). Verdict stays REVIEW."""
    valid = {k: v for k, v in overlaps.items() if v == v}
    if not valid:
        return None, "no candidate overlaps — run step10b (emits C1/C2/C3) first"
    best = max(valid, key=valid.get)
    ranked = ", ".join(f"{k}={valid[k]:.2f}" for k in sorted(valid, key=valid.get, reverse=True))
    return best, (f"{best} has the highest brainstem overlap ({ranked}). "
                  f"CONFIRM by nucleus anatomy in overlay_atlas_native.py before freezing "
                  f"(gross overlap can't tell a 1-2 mm misplacement).")


def _overlap(atlas_path, mask_path):
    import numpy as np, nibabel as nib  # noqa: E401
    lab = np.asanyarray(nib.load(atlas_path).dataobj)
    m = np.asanyarray(nib.load(mask_path).dataobj) > 0.5
    a = lab > 0
    if a.shape != m.shape:
        return float("nan")
    na = int(a.sum())
    return (int((a & m).sum()) / na) if na else float("nan")


def main():
    ap = argparse.ArgumentParser(description="step10b transform-chain selection report")
    ap.add_argument("--brainstem-mask", required=True)
    ap.add_argument("--c1", default=""); ap.add_argument("--c2", default=""); ap.add_argument("--c3", default="")
    ap.add_argument("--qc-dir", default="codes/qc")
    ap.add_argument("--out", default="")
    a = ap.parse_args()

    cands = {k: v for k, v in (("C1", a.c1), ("C2", a.c2), ("C3", a.c3)) if v}
    if not cands or not os.path.isfile(a.brainstem_mask):
        print("[chain] WARN need --brainstem-mask + >=1 candidate — skipped", file=sys.stderr)
        D.write_decision(a.qc_dir, "transform_chain", "PENDING",
                         "run step10b (C1/C2/C3) + chain_report.py + judge the overlay")
        return 0
    try:
        import nibabel  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        print(f"[chain] WARN needs nibabel ({exc}) — skipped", file=sys.stderr)
        D.write_decision(a.qc_dir, "transform_chain", "PENDING", "nibabel missing — run on cluster")
        return 0

    overlaps = {}
    for k, p in cands.items():
        overlaps[k] = _overlap(p, a.brainstem_mask) if os.path.isfile(p) else float("nan")
    best, msg = rank_chains(overlaps)
    out = a.out or os.path.join(a.qc_dir, "transform_chain_report.md")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, "w") as f:
        f.write("# step10b transform chain — candidate brainstem overlap\n\n")
        f.write(f"**Verdict: REVIEW** — {msg}\n\n")
        f.write("| chain | atlas-in-brainstem overlap |\n|---|---|\n")
        for k in ("C1", "C2", "C3"):
            if k in overlaps:
                f.write(f"| {k} | {overlaps[k]:.3f} |\n")
        f.write("\nC1 = no refine · C2 = refine-inverse · C3 = refine-forward. "
                "Set the chosen chain in the step10b selector once confirmed by overlay.\n")
    metrics = {k: round(v, 3) for k, v in overlaps.items() if v == v}
    metrics["best"] = best or ""
    D.write_decision(a.qc_dir, "transform_chain", "REVIEW", msg, metrics=metrics)
    print(f"[chain] best overlap={best}; {msg}\n[chain] wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
