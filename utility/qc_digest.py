#!/usr/bin/env python3
"""
qc_digest.py — one unified QC table for the whole pipeline.

Aggregates every step's flags + fMRIPrep motion (mean framewise displacement and the
% of high-motion volumes) into a SINGLE per-subject CSV that the GUI shows live. It is
a read-only roll-up: it never changes data and never fails (flag + log + continue) — a
missing source just shows as "NA".

Sources it reads (whatever exists):
  fMRIPrep confounds  <deriv>/fmriprep/<subj>/**/func/*_desc-confounds_timeseries.tsv
                      -> mean FD and % of volumes with FD > --fd-thresh  (motion)
  FD summary          <deriv>/fmriprep/qc_fd_summary.json        -> MNI-BOLD present?
  SDC audit           <qc>/group_sdc_audit.csv                   -> distortion correction
  Cardinality audit   <qc>/group_cardinality_audit.csv           -> volume-count match
  Piezo cardiac QC    <qc>/group_piezo_qc.csv                    -> cardiac/resp-only
  Contrast identity   <deriv>/spm/**/_contrast_check.csv         -> "Stim > baseline"?
  ROI geometry        <deriv>/spm/**/_roi_geometry_check.csv     -> grid/affine match

Writes:
  <qc>/qc_digest.csv   one row per subject + per-check status + overall verdict
  <qc>/qc_digest.md    legend + the flagged-subject summary

Per-check cell values: OK | FLAG (+ specific tokens) | NA (source not found / no row).
Overall `status`: OK (everything green) or REVIEW (>=1 flag — read it, don't auto-drop).

Usage:
  python qc_digest.py --sourcedata <BIDS> [--derivatives D] [--qc-dir Q]
                      [--output CSV] [--session 01] [--fd-thresh 0.5] [--fd-mean-thresh 0.9]
"""
import argparse
import csv
import glob
import json
import os
import statistics
import sys
from pathlib import Path


# ── small helpers ────────────────────────────────────────────────────────────
def _read_csv(path):
    """Return list-of-dicts for a CSV, or [] if missing/unreadable.
    A missing source is normal (that check just shows NA) — only a real read error warns."""
    if not os.path.isfile(path):
        return []
    try:
        with open(path, newline="") as f:
            return list(csv.DictReader(f))
    except Exception as exc:  # noqa: BLE001 — flag + continue
        print(f"[qc_digest] WARN cannot read {path}: {exc}", file=sys.stderr)
        return []


def _col(row, *candidates):
    """First matching column value (case-insensitive contains), else ''."""
    low = {k.lower(): v for k, v in row.items() if k}
    for cand in candidates:
        for k, v in low.items():
            if cand in k:
                return (v or "").strip()
    return ""


def _subj_of(row):
    s = _col(row, "subject", "subj", "participant")
    if s and not s.startswith("sub-"):
        s = "sub-" + s.lstrip("sub-")
    return s


# ── fMRIPrep motion (mean FD + % high-FD volumes) ────────────────────────────
def _motion(deriv, subj, fd_thresh):
    """Pool framewise_displacement across the subject's confounds TSVs."""
    fmriprep = Path(deriv) / "fmriprep" / subj
    vals = []
    for tsv in glob.glob(str(fmriprep / "**" / "func" / "*desc-confounds_timeseries.tsv"),
                         recursive=True):
        try:
            with open(tsv, newline="") as f:
                for r in csv.DictReader(f, delimiter="\t"):
                    v = r.get("framewise_displacement", "")
                    if v not in ("", "n/a", "NaN"):
                        try:
                            vals.append(float(v))
                        except ValueError:
                            pass
        except Exception:  # noqa: BLE001
            continue
    if not vals:
        return None, None, 0
    mean_fd = round(statistics.mean(vals), 4)
    pct_hi = round(100.0 * sum(1 for v in vals if v > fd_thresh) / len(vals), 1)
    return mean_fd, pct_hi, len(vals)


def _has_mni(deriv, subj):
    """has_mni_bold from qc_fd_summary.json; None if the summary is absent."""
    p = Path(deriv) / "fmriprep" / "qc_fd_summary.json"
    if not p.is_file():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
    except Exception:  # noqa: BLE001
        return None
    res = data.get("results", data)
    entry = res.get(subj) if isinstance(res, dict) else None
    if not isinstance(entry, dict):
        return None
    return bool(entry.get("has_mni_bold"))


# ── per-source flag maps (subject -> "FLAG: detail" or "OK") ─────────────────
def _flagmap(rows, is_flagged, detail):
    """Collapse per-(subject,run) rows to one verdict per subject."""
    out = {}
    for r in rows:
        s = _subj_of(r)
        if not s:
            continue
        if is_flagged(r):
            out[s] = "FLAG:" + detail(r) if s not in out or out[s] == "OK" else out[s]
        else:
            out.setdefault(s, "OK")
    return out


def _glob_first(deriv, name):
    hits = glob.glob(str(Path(deriv) / "spm" / "**" / name), recursive=True)
    rows = []
    for h in hits:
        rows += _read_csv(h)
    return rows


def main():
    ap = argparse.ArgumentParser(description="Unified pipeline QC digest")
    ap.add_argument("--sourcedata", required=True)
    ap.add_argument("--derivatives", default="")
    ap.add_argument("--qc-dir", default="")
    ap.add_argument("--output", default="")
    ap.add_argument("--session", default="01")
    ap.add_argument("--fd-thresh", type=float, default=0.5,
                    help="per-volume FD (mm) above which a volume is 'high-motion'")
    ap.add_argument("--fd-mean-thresh", type=float, default=0.9,
                    help="mean FD (mm) above which a subject is flagged HIGH-FD")
    a = ap.parse_args()

    sourcedata = a.sourcedata
    deriv = a.derivatives or str(Path(sourcedata) / "derivatives")
    project = Path(sourcedata).parent
    qc = a.qc_dir or str(project / "codes" / "qc")
    out = a.output or str(Path(qc) / "qc_digest.csv")
    Path(qc).mkdir(parents=True, exist_ok=True)
    Path(out).parent.mkdir(parents=True, exist_ok=True)

    # ── per-source flag maps ─────────────────────────────────────────────────
    sdc = _flagmap(_read_csv(Path(qc) / "group_sdc_audit.csv"),
                   lambda r: _col(r, "flag") == "FLAG" or
                             (_col(r, "verdict") not in ("", "SDC_APPLIED")),
                   lambda r: _col(r, "verdict") or "no-SDC")
    card = _flagmap(_read_csv(Path(qc) / "group_cardinality_audit.csv"),
                    lambda r: _col(r, "flag") == "FLAG",
                    lambda r: (_col(r, "note") or "count-mismatch")[:30])
    piezo = _flagmap(_read_csv(Path(qc) / "group_piezo_qc.csv"),
                     lambda r: _col(r, "decision", "recommendation").lower().startswith("resp")
                               or _col(r, "verdict").upper() in ("BAD", "SUSPECT"),
                     lambda r: (_col(r, "verdict") or "resp-only"))
    con = _flagmap(_glob_first(deriv, "_contrast_check.csv"),
                   lambda r: _col(r, "status") not in ("", "OK"),
                   lambda r: _col(r, "status") or "mismatch")
    roi = _flagmap(_glob_first(deriv, "_roi_geometry_check.csv"),
                   lambda r: _col(r, "status") not in ("", "OK"),
                   lambda r: _col(r, "status") or "geom")

    # ── subject universe (union of fMRIPrep subjects + every source) ─────────
    subs = set()
    fp = Path(deriv) / "fmriprep"
    if fp.is_dir():
        subs |= {d.name for d in fp.iterdir() if d.is_dir() and d.name.startswith("sub-")}
    if os.path.isdir(sourcedata):
        subs |= {d.name for d in Path(sourcedata).iterdir()
                 if d.is_dir() and d.name.startswith("sub-")}
    for m in (sdc, card, piezo, con, roi):
        subs |= set(m)
    subs = sorted(subs)

    fields = ["subject", "n_fd_vols", "mean_fd", f"pct_fd_gt_{a.fd_thresh}", "motion",
              "mni_bold", "sdc", "cardinality", "piezo", "contrast", "roi_geom",
              "n_flags", "status"]
    rows = []
    for s in subs:
        mean_fd, pct_hi, n = _motion(deriv, s, a.fd_thresh)
        has_mni = _has_mni(deriv, s)
        motion = "NA"
        if mean_fd is not None:
            motion = "HIGH-FD" if mean_fd > a.fd_mean_thresh else "OK"
        mni = "NA" if has_mni is None else ("OK" if has_mni else "FLAG:no-MNI-BOLD")

        cells = {
            "sdc": sdc.get(s, "NA"),
            "cardinality": card.get(s, "NA"),
            "piezo": piezo.get(s, "NA"),
            "contrast": con.get(s, "NA"),
            "roi_geom": roi.get(s, "NA"),
        }
        n_flags = sum(1 for v in list(cells.values()) + [motion, mni]
                      if isinstance(v, str) and (v.startswith("FLAG") or v in ("HIGH-FD",)))
        rows.append({
            "subject": s,
            "n_fd_vols": n or "",
            "mean_fd": "" if mean_fd is None else mean_fd,
            f"pct_fd_gt_{a.fd_thresh}": "" if pct_hi is None else pct_hi,
            "motion": motion,
            "mni_bold": mni,
            **cells,
            "n_flags": n_flags,
            "status": "OK" if n_flags == 0 else "REVIEW",
        })

    with open(out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    # ── human-readable summary + legend ──────────────────────────────────────
    md = Path(out).with_suffix(".md")
    n_review = sum(1 for r in rows if r["status"] == "REVIEW")
    with open(md, "w") as f:
        f.write("# QC digest\n\n")
        f.write(f"- Subjects: **{len(rows)}**  ·  OK: **{len(rows) - n_review}**  ·  "
                f"REVIEW (>=1 flag): **{n_review}**\n")
        f.write(f"- FD thresholds: per-volume > {a.fd_thresh} mm = high-motion; "
                f"mean FD > {a.fd_mean_thresh} mm = HIGH-FD subject.\n\n")
        f.write("**Legend** — each cell is OK / FLAG / NA (source not run).\n"
                "A FLAG means *review*, not *exclude* (flag + log policy).\n\n")
        f.write("| check | OK means | FLAG means |\n|---|---|---|\n")
        f.write("| motion | mean FD <= threshold | HIGH-FD: lots of head motion (still kept; FD spikes censor it) |\n")
        f.write("| mni_bold | fMRIPrep made an MNI BOLD | registration/normalisation likely failed |\n")
        f.write("| sdc | distortion correction applied | fieldmap missing or SDC not applied |\n")
        f.write("| cardinality | volume counts match across stages | a stage lost/added volumes |\n")
        f.write("| piezo | cardiac RETROICOR used | piezo too messy -> respiration-only |\n")
        f.write("| contrast | contrast == 'Stim > baseline' | a different contrast was entered |\n")
        f.write("| roi_geom | ROI grid matched | image resampled / unreadable |\n\n")
        if n_review:
            f.write("## Subjects to review\n\n")
            for r in rows:
                if r["status"] == "REVIEW":
                    flagged = [f"{k}={r[k]}" for k in
                               ("motion", "mni_bold", "sdc", "cardinality", "piezo",
                                "contrast", "roi_geom")
                               if str(r[k]).startswith("FLAG") or r[k] == "HIGH-FD"]
                    f.write(f"- **{r['subject']}** — {', '.join(flagged)}\n")
        else:
            f.write("All subjects OK (no flags).\n")

    print(f"[qc_digest] {len(rows)} subject(s); {n_review} to review.")
    print(f"[qc_digest] wrote {out}")
    print(f"[qc_digest] wrote {md}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
