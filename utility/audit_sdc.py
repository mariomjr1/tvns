#!/usr/bin/env python3
"""
audit_sdc.py  —  susceptibility-distortion-correction (SDC) verification (Task 16)

For each subject/BOLD run in the fMRIPrep derivatives, check whether SDC was
actually applied (not just whether a fieldmap exists), and flag every run that
can't be confirmed. AUDIT ONLY: it never pauses, never skips, never fails — it
flags and logs, and always exits 0.

Per run it records:
  fmap_linked   a BIDS fmap *_epi has this BOLD in its IntendedFor (needs --bids)
  sdc_figure    fMRIPrep wrote a *desc-sdc*.svg report figure for the run
  html_sdc      the subject's fMRIPrep .html report mentions SDC/fieldmap (subject-level)
  sidecar_sdc   the preproc BOLD JSON sidecar hints at distortion correction

Verdict:
  SDC_APPLIED       figure or sidecar confirms SDC ran
  FMAP_BUT_NO_SDC   a fieldmap is linked but no SDC evidence  (flag — investigate)
  NO_FIELDMAP       no fieldmap linked and no SDC evidence     (flag — AP/PA expected)
  UNKNOWN           cannot determine                           (flag)

Outputs (in --out, default <project>/codes/qc):
  <fmriprep>/sub-XXXX/sdc_audit.csv          per subject (if writable)
  group_sdc_audit.csv / group_sdc_audit.md   cohort (flagged rows in the .md)

Usage:
  audit_sdc.py <fmriprep_derivatives> [subject ...] [--all] [--bids <root>] [--out <dir>]

Created by Mario Murakami
"""

import argparse
import csv
import datetime
import json
import re
import sys
from pathlib import Path

_SDC_HTML_TERMS = ("susceptibility distortion", "distortion correction",
                   "fieldmap", "field map", "pepolar", "topup", "sdcflows")


def _ses_func_dirs(subj_dir):
    """func dirs under a subject (handles ses-* and no-session layouts)."""
    out = []
    if not subj_dir.is_dir():
        return out
    for ses in sorted(subj_dir.glob("ses-*")):
        if (ses / "func").is_dir():
            out.append(ses / "func")
    if (subj_dir / "func").is_dir():
        out.append(subj_dir / "func")
    return out


def _intended_for_targets(bids_subj_dir):
    """Collect every path mentioned in any fmap *_epi.json IntendedFor (basenames)."""
    targets = set()
    if bids_subj_dir is None:
        return None  # signal "unknown — no --bids"
    for ses_fmap in list(bids_subj_dir.glob("ses-*/fmap")) + [bids_subj_dir / "fmap"]:
        if not ses_fmap.is_dir():
            continue
        for jf in sorted(ses_fmap.glob("*_epi.json")):
            try:
                meta = json.loads(jf.read_text())
            except Exception:  # noqa: BLE001
                continue
            tgt = meta.get("IntendedFor")
            if not tgt:
                continue
            for rel in ([tgt] if isinstance(tgt, str) else tgt):
                targets.add(Path(str(rel)).name)  # basename, BIDS-URI safe
    return targets


def _has_sdc_figure(subj_dir, task, run):
    figdir = subj_dir / "figures"
    if not figdir.is_dir():
        return False
    pats = [f"*task-{task}*run-{run}*sdc*.svg", f"*task-{task}*sdc*.svg"]
    for p in pats:
        if any(figdir.glob(p)):
            return True
    return False


def _html_mentions_sdc(fmriprep_root, subj):
    html = fmriprep_root / f"{subj}.html"
    if not html.is_file():
        return None  # unknown
    try:
        txt = html.read_text(errors="ignore").lower()
    except Exception:  # noqa: BLE001
        return None
    return any(term in txt for term in _SDC_HTML_TERMS)


def _sidecar_sdc(preproc_json):
    if not preproc_json.is_file():
        return None
    try:
        meta = json.loads(preproc_json.read_text())
    except Exception:  # noqa: BLE001
        return None
    blob = json.dumps(meta).lower()
    return any(k in blob for k in ("sdc", "distortion", "fieldmap", "b0field"))


def audit_subject(fmriprep_root, subj, bids_root):
    subj_dir = fmriprep_root / subj
    bids_subj = (bids_root / subj) if bids_root else None
    intended = _intended_for_targets(bids_subj)        # set | None(unknown)
    html_sdc = _html_mentions_sdc(fmriprep_root, subj)  # bool | None

    seen, records = set(), []
    for func in _ses_func_dirs(subj_dir):
        for bold in sorted(func.glob("*_desc-preproc_bold.nii.gz")):
            m = re.search(r"task-(\w+?)_run-(\d+)", bold.name)
            if not m:
                continue
            task, run = m.group(1), m.group(2)
            if (task, run) in seen:
                continue
            seen.add((task, run))

            raw_name = re.sub(r"_space-[A-Za-z0-9]+", "", bold.name)  # raw BOLD name
            if intended is None:
                fmap_linked = None
            else:
                fmap_linked = any(task in t and f"run-{run}" in t for t in intended) \
                    or raw_name in intended
            sdc_fig = _has_sdc_figure(subj_dir, task, run)
            side = _sidecar_sdc(bold.with_name(
                bold.name[:-len(".nii.gz")] + ".json"))

            applied = bool(sdc_fig) or bool(side)
            if applied:
                verdict = "SDC_APPLIED"
            elif fmap_linked:
                verdict = "FMAP_BUT_NO_SDC"
            elif fmap_linked is False:
                verdict = "NO_FIELDMAP"
            else:
                verdict = "UNKNOWN"

            records.append({
                "subject": subj, "task": task, "run": run,
                "fmap_linked": "" if fmap_linked is None else int(fmap_linked),
                "sdc_figure": int(bool(sdc_fig)),
                "html_sdc": "" if html_sdc is None else int(html_sdc),
                "sidecar_sdc": "" if side is None else int(bool(side)),
                "verdict": verdict,
                "flag": "" if verdict == "SDC_APPLIED" else "FLAG",
            })
    return records


_FIELDS = ["subject", "task", "run", "fmap_linked", "sdc_figure", "html_sdc",
           "sidecar_sdc", "verdict", "flag"]


def _write_csv(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        w.writeheader()
        for r in records:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser(
        description="SDC verification audit (Task 16). Audit only — never fails.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("fmriprep", help="fMRIPrep derivatives root")
    ap.add_argument("subjects", nargs="*", help="BIDS subject IDs (sub-...)")
    ap.add_argument("--all", dest="all_subjects", action="store_true",
                    help="audit every sub-* under the derivatives root")
    ap.add_argument("--bids", default="",
                    help="BIDS root fMRIPrep ran on (for IntendedFor check)")
    ap.add_argument("--out", default="", help="cohort report dir (default <root>/qc)")
    args = ap.parse_args()

    fmriprep_root = Path(args.fmriprep)
    if not fmriprep_root.is_dir():
        print(f"[sdc] fMRIPrep derivatives not found: {fmriprep_root}", file=sys.stderr)
        # still exit 0 — audit only
        return
    bids_root = Path(args.bids) if args.bids else None

    subjects = list(args.subjects)
    if args.all_subjects:
        subjects = sorted(d.name for d in fmriprep_root.iterdir()
                          if d.is_dir() and d.name.startswith("sub-"))
    if not subjects:
        print("[sdc] no subjects given (use --all or list IDs)", file=sys.stderr)
        return

    out_dir = Path(args.out) if args.out else fmriprep_root / "qc"
    all_records = []
    for subj in subjects:
        if not subj.startswith("sub-"):
            subj = "sub-" + subj.replace("_", "")
        recs = audit_subject(fmriprep_root, subj, bids_root)
        if not recs:
            print(f"[sdc] {subj}: no preproc BOLD found", file=sys.stderr)
            continue
        try:
            _write_csv(fmriprep_root / subj / "sdc_audit.csv", recs)
        except Exception as e:  # noqa: BLE001
            print(f"[sdc] {subj}: could not write per-subject csv: {e}", file=sys.stderr)
        n_flag = sum(1 for r in recs if r["flag"])
        print(f"[sdc] {subj}: {len(recs)} run(s), {n_flag} flagged")
        all_records.extend(recs)

    if not all_records:
        print("[sdc] nothing to report."); return

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_csv(out_dir / "group_sdc_audit.csv", all_records)

    flagged = [r for r in all_records if r["flag"]]
    n_subj = len({r["subject"] for r in all_records})
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    with open(out_dir / "group_sdc_audit.md", "w") as f:
        f.write("# Susceptibility-distortion-correction (SDC) audit\n\n")
        f.write(f"Generated: {ts}\n\n")
        f.write(f"- Subjects: {n_subj}\n- Runs: {len(all_records)}\n")
        f.write(f"- Flagged (SDC not confirmed): {len(flagged)}\n\n")
        f.write("Verdicts: SDC_APPLIED ok · FMAP_BUT_NO_SDC (fieldmap present but no "
                "SDC evidence) · NO_FIELDMAP · UNKNOWN.\n\n")
        if flagged:
            f.write("## Flagged runs\n\n| subject | task | run | verdict | "
                    "fmap_linked | sdc_figure |\n|---|---|---|---|---|---|\n")
            for r in flagged:
                f.write(f"| {r['subject']} | {r['task']} | {r['run']} | {r['verdict']} | "
                        f"{r['fmap_linked']} | {r['sdc_figure']} |\n")
        else:
            f.write("No flagged runs — SDC confirmed for every BOLD run.\n")

    print(f"[sdc] {len(all_records)} run(s), {len(flagged)} flagged across "
          f"{n_subj} subject(s).")
    print(f"[sdc] Wrote {out_dir / 'group_sdc_audit.csv'}")
    print(f"[sdc] Wrote {out_dir / 'group_sdc_audit.md'}")


if __name__ == "__main__":
    main()
