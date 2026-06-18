#!/usr/bin/env python3
"""
audit_platform.py  —  acquisition platform / console audit (M9, Task 33)

The 7T scanner console was upgraded mid-study (pre/post-2026: same magnet, different
console + pulse-sequence implementation). This audit reads each subject's BIDS BOLD
JSON sidecars (and, when present, participants.tsv for group and the fMRIPrep
confounds for mean FD / tSNR), infers the acquisition **platform** per BOLD run, and
writes a cohort report with the **group × platform contingency table** and per-platform
summaries. It flags if a group is platform-imbalanced.

This is an AUDIT ONLY: it never pauses, never skips a subject, never fails — it flags
and logs and always exits 0. It settles the platform-confound question *before* group
inference (a platform covariate may be unnecessary if balanced — Task 33).

Platform inference (first hit wins; each rule is a substring match, case-insensitive):
  E12   pre-upgrade console — ManufacturersModelName ~ Investigational_Device_7T /
        Magnetom7T, SoftwareVersions ~ syngo MR E12 / VE12, PulseSequenceDetails or
        SeriesDescription ~ ep2d_bold_sms_mgh
  XA60  post-upgrade console — SoftwareVersions ~ syngo XA60 / XA60, SeriesDescription
        or PulseSequenceDetails ~ cmrr_mbep2d_bold
  Tokens are configurable via --e12-tokens / --xa60-tokens (comma lists). A run that
  matches neither (or both) is recorded as UNKNOWN and flagged.

Outputs (in --out, default <project>/codes/qc):
  group_platform_audit.csv   one row per BOLD run (subject, group, platform, evidence,
                             mean_fd, tsnr)
  group_platform_audit.md    contingency table + per-platform summary + imbalance flag

Usage:
  audit_platform.py <bids_root> [subject ...] [--all] \
      [--participants <participants.tsv>] [--fmriprep <derivatives/fmriprep>] \
      [--out <dir>] [--e12-tokens a,b] [--xa60-tokens c,d] [--imbalance 0.30]

Created by Mario Murakami
"""

import argparse
import csv
import datetime
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Default substring tokens (lower-case) that identify each console generation.
# Matched against the concatenation of the sidecar fields below.
DEFAULT_E12_TOKENS = ["e12", "ve12", "ep2d_bold_sms_mgh", "investigational_device_7t"]
DEFAULT_XA60_TOKENS = ["xa60", "cmrr_mbep2d_bold", "mbep2d"]

# Sidecar fields consulted for platform inference (order is the reported "evidence").
_PLATFORM_FIELDS = ["ManufacturersModelName", "SoftwareVersions",
                    "PulseSequenceDetails", "SeriesDescription"]


# ── BIDS discovery (stdlib only) ────────────────────────────────────────────────

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


def _load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except Exception as exc:  # noqa: BLE001
        print(f"  [platform] WARN json {path}: {exc}", file=sys.stderr)
        return None


def _infer_platform(meta, e12_tokens, xa60_tokens):
    """Return (platform, evidence_str). platform ∈ {E12, XA60, UNKNOWN}."""
    if not meta:
        return "UNKNOWN", "no sidecar"
    blob_parts = []
    for f in _PLATFORM_FIELDS:
        v = meta.get(f)
        if isinstance(v, (list, tuple)):
            v = " ".join(str(x) for x in v)
        if v:
            blob_parts.append(f"{f}={v}")
    blob = " | ".join(blob_parts)
    low = blob.lower()
    is_e12 = any(tok in low for tok in e12_tokens)
    is_xa60 = any(tok in low for tok in xa60_tokens)
    if is_e12 and not is_xa60:
        plat = "E12"
    elif is_xa60 and not is_e12:
        plat = "XA60"
    elif is_e12 and is_xa60:
        plat = "UNKNOWN"   # ambiguous — both generations matched
    else:
        plat = "UNKNOWN"   # neither matched
    return plat, (blob if blob else "no platform fields")


# ── fMRIPrep confounds (optional mean FD / tSNR) ────────────────────────────────

def _confounds_for_run(fmriprep_root, subj, task, run):
    """Locate the fMRIPrep confounds TSV for this run; None if absent."""
    if fmriprep_root is None:
        return None
    sd = fmriprep_root / subj
    if not sd.is_dir():
        return None
    hits = sorted(sd.rglob(f"*task-{task}_run-{run}*desc-confounds*timeseries.tsv"))
    return hits[0] if hits else None


def _mean_fd_and_tsnr(conf_tsv):
    """mean framewise_displacement; tSNR from std_dvars if a column is present.
    Returns (mean_fd|'', tsnr|''). Pure stdlib (csv); never raises."""
    if conf_tsv is None or not Path(conf_tsv).is_file():
        return "", ""
    try:
        with open(conf_tsv) as f:
            reader = csv.DictReader(f, delimiter="\t")
            fd_vals = []
            # tSNR is not stored directly; if the file carries a per-run scalar we
            # surface it, otherwise leave blank (mean FD is the robust common metric).
            tsnr_col = None
            for cand in ("tsnr", "tSNR", "temporal_snr"):
                if reader.fieldnames and cand in reader.fieldnames:
                    tsnr_col = cand
                    break
            tsnr_vals = []
            for row in reader:
                v = row.get("framewise_displacement", "n/a")
                if v not in ("", "n/a", "NaN"):
                    try:
                        fd_vals.append(float(v))
                    except ValueError:
                        pass
                if tsnr_col:
                    tv = row.get(tsnr_col, "")
                    try:
                        tsnr_vals.append(float(tv))
                    except (ValueError, TypeError):
                        pass
        mean_fd = round(sum(fd_vals) / len(fd_vals), 4) if fd_vals else ""
        tsnr = round(sum(tsnr_vals) / len(tsnr_vals), 2) if tsnr_vals else ""
        return mean_fd, tsnr
    except Exception as exc:  # noqa: BLE001
        print(f"  [platform] WARN confounds {conf_tsv}: {exc}", file=sys.stderr)
        return "", ""


# ── participants.tsv (optional group) ───────────────────────────────────────────

def _load_groups(participants_tsv):
    """Map subject -> group from a participants.tsv. Returns {} if absent/unreadable.
    Looks for a 'group' column (case-insensitive); falls back to 'dx'/'diagnosis'."""
    out = {}
    if participants_tsv is None or not Path(participants_tsv).is_file():
        return out
    try:
        with open(participants_tsv) as f:
            reader = csv.DictReader(f, delimiter="\t")
            if not reader.fieldnames:
                return out
            id_col = next((c for c in reader.fieldnames
                           if c.lower() in ("participant_id", "subject", "subject_id")), None)
            grp_col = next((c for c in reader.fieldnames
                            if c.lower() in ("group", "dx", "diagnosis", "cohort")), None)
            if id_col is None or grp_col is None:
                return out
            for row in reader:
                sid = (row.get(id_col) or "").strip()
                if not sid:
                    continue
                if not sid.startswith("sub-"):
                    sid = "sub-" + sid.replace("_", "")
                out[sid] = (row.get(grp_col) or "").strip() or "NA"
    except Exception as exc:  # noqa: BLE001
        print(f"  [platform] WARN participants {participants_tsv}: {exc}", file=sys.stderr)
    return out


# ── Per-subject audit ────────────────────────────────────────────────────────────

def audit_subject(bids_root, subj, groups, fmriprep_root, e12_tokens, xa60_tokens):
    """One record per BOLD run for *subj*."""
    subj_dir = Path(bids_root) / subj
    group = groups.get(subj, "NA")
    seen, records = set(), []
    for func in _ses_func_dirs(subj_dir):
        for bold in sorted(func.glob("*_bold.nii.gz")):
            m = re.search(r"task-(\w+?)_run-(\d+)", bold.name)
            if not m:
                continue
            task, run = m.group(1), m.group(2)
            if (task, run) in seen:
                continue
            seen.add((task, run))
            sidecar = bold.with_name(bold.name[:-len(".nii.gz")] + ".json")
            meta = _load_json(sidecar) if sidecar.is_file() else None
            plat, evidence = _infer_platform(meta, e12_tokens, xa60_tokens)
            conf = _confounds_for_run(fmriprep_root, subj, task, run)
            mean_fd, tsnr = _mean_fd_and_tsnr(conf)
            records.append({
                "subject": subj, "group": group, "task": task, "run": run,
                "platform": plat, "mean_fd": mean_fd, "tsnr": tsnr,
                "evidence": evidence[:200],
                "flag": "FLAG" if plat == "UNKNOWN" else "",
            })
    return records


# ── Contingency / imbalance ──────────────────────────────────────────────────────

def contingency(records):
    """{group: {platform: n}} counting distinct SUBJECTS (not runs)."""
    seen = set()
    table = defaultdict(Counter)
    for r in records:
        # Count a subject once per (group, platform) — a subject's runs are normally
        # all the same platform; if a subject mixes platforms, each is counted once.
        key = (r["subject"], r["group"], r["platform"])
        if key in seen:
            continue
        seen.add(key)
        table[r["group"]][r["platform"]] += 1
    return table


def imbalance_flags(table, threshold):
    """Flag groups whose platform split deviates from the cohort split by > threshold.
    Returns list of human-readable flag strings."""
    flags = []
    platforms = sorted({p for c in table.values() for p in c})
    cohort = Counter()
    for c in table.values():
        cohort.update(c)
    cohort_total = sum(cohort.values()) or 1
    cohort_frac = {p: cohort[p] / cohort_total for p in platforms}
    for grp, c in table.items():
        total = sum(c.values()) or 1
        for p in platforms:
            grp_frac = c.get(p, 0) / total
            if abs(grp_frac - cohort_frac.get(p, 0)) > threshold:
                flags.append(
                    f"group '{grp}' platform '{p}' fraction {grp_frac:.2f} deviates "
                    f"from cohort {cohort_frac.get(p, 0):.2f} (> {threshold:.2f})")
    return flags


# ── Output ───────────────────────────────────────────────────────────────────────

_FIELDS = ["subject", "group", "task", "run", "platform", "mean_fd", "tsnr",
           "evidence", "flag"]


def write_cohort(out_dir, records, threshold, print_fn=print):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / "group_platform_audit.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=_FIELDS)
        w.writeheader()
        for r in records:
            w.writerow(r)

    table = contingency(records)
    platforms = sorted({p for c in table.values() for p in c})
    imbal = imbalance_flags(table, threshold)
    n_unknown = sum(1 for r in records if r["platform"] == "UNKNOWN")
    n_subj = len({r["subject"] for r in records})
    ts = datetime.datetime.now().isoformat(timespec="seconds")

    # per-platform summary (mean FD over runs that have it)
    by_plat_fd = defaultdict(list)
    by_plat_runs = Counter()
    for r in records:
        by_plat_runs[r["platform"]] += 1
        if r["mean_fd"] != "":
            by_plat_fd[r["platform"]].append(float(r["mean_fd"]))

    md_path = out / "group_platform_audit.md"
    with open(md_path, "w") as f:
        f.write("# Acquisition platform / console audit (Task 33)\n\n")
        f.write(f"Generated: {ts}\n\n")
        f.write(f"- Subjects: {n_subj}\n- BOLD runs: {len(records)}\n")
        f.write(f"- Platform UNKNOWN (flagged): {n_unknown}\n\n")
        f.write("Platforms: E12 = pre-upgrade console, XA60 = post-upgrade console. "
                "Same 7T magnet — the difference is the console + pulse-sequence "
                "implementation.\n\n")

        f.write("## Group × platform contingency (distinct subjects)\n\n")
        header = "| group | " + " | ".join(platforms) + " | total |\n"
        f.write(header)
        f.write("|" + "---|" * (len(platforms) + 2) + "\n")
        for grp in sorted(table):
            c = table[grp]
            cells = [str(c.get(p, 0)) for p in platforms]
            f.write(f"| {grp} | " + " | ".join(cells) + f" | {sum(c.values())} |\n")
        f.write("\n")

        f.write("## Per-platform summary\n\n")
        f.write("| platform | runs | mean FD (avg over runs) |\n|---|---|---|\n")
        for p in platforms:
            fds = by_plat_fd.get(p, [])
            mfd = f"{sum(fds) / len(fds):.4f}" if fds else "—"
            f.write(f"| {p} | {by_plat_runs[p]} | {mfd} |\n")
        f.write("\n")

        f.write("## Imbalance check\n\n")
        if imbal:
            f.write(f"**FLAGGED** — group platform split is uneven "
                    f"(threshold {threshold:.2f}); consider a platform covariate "
                    f"at the group level:\n\n")
            for fl in imbal:
                f.write(f"- {fl}\n")
        else:
            f.write(f"No imbalance beyond the {threshold:.2f} threshold — platform is "
                    f"reasonably balanced across groups (a platform covariate is likely "
                    f"unnecessary).\n")

    print_fn(f"[platform] {len(records)} run(s) across {n_subj} subject(s); "
             f"{n_unknown} UNKNOWN; imbalance flags: {len(imbal)}.")
    print_fn(f"[platform] Wrote {csv_path}")
    print_fn(f"[platform] Wrote {md_path}")
    return {"subjects": n_subj, "runs": len(records), "unknown": n_unknown,
            "imbalance_flags": len(imbal), "platforms": platforms}


# ── CLI ──────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Acquisition platform / console audit (Task 33). Audit only — never fails.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("bids", help="BIDS root (sourcedata) holding sub-*/**/func/*_bold.json")
    ap.add_argument("subjects", nargs="*", help="BIDS subject IDs (sub-...)")
    ap.add_argument("--all", dest="all_subjects", action="store_true",
                    help="audit every sub-* under the BIDS root")
    ap.add_argument("--participants", default="",
                    help="participants.tsv for the group column (default: <bids>/participants.tsv)")
    ap.add_argument("--fmriprep", default="",
                    help="fMRIPrep derivatives root (for per-run mean FD / tSNR; optional)")
    ap.add_argument("--out", default="",
                    help="cohort report dir (default <project>/codes/qc, else <bids>/derivatives/qc)")
    ap.add_argument("--e12-tokens", default="",
                    help="comma list of substrings identifying the pre-upgrade (E12) console")
    ap.add_argument("--xa60-tokens", default="",
                    help="comma list of substrings identifying the post-upgrade (XA60) console")
    ap.add_argument("--imbalance", type=float, default=0.30,
                    help="flag a group whose platform fraction deviates from the cohort by "
                         "more than this (default 0.30)")
    args = ap.parse_args()

    bids_root = Path(args.bids)
    if not bids_root.is_dir():
        print(f"[platform] BIDS root not found: {bids_root}", file=sys.stderr)
        return  # exit 0 — audit only

    e12_tokens = ([t.strip().lower() for t in args.e12_tokens.split(",") if t.strip()]
                  or DEFAULT_E12_TOKENS)
    xa60_tokens = ([t.strip().lower() for t in args.xa60_tokens.split(",") if t.strip()]
                   or DEFAULT_XA60_TOKENS)

    participants = (Path(args.participants) if args.participants
                    else bids_root / "participants.tsv")
    groups = _load_groups(participants)
    fmriprep_root = Path(args.fmriprep) if args.fmriprep else None

    subjects = list(args.subjects)
    if args.all_subjects:
        subjects = sorted(d.name for d in bids_root.iterdir()
                          if d.is_dir() and d.name.startswith("sub-"))
    if not subjects:
        print("[platform] no subjects given (use --all or list IDs)", file=sys.stderr)
        return

    if args.out:
        out_dir = args.out
    else:
        codes = bids_root.parent / "codes" / "qc"
        out_dir = str(codes if (bids_root.parent / "codes").is_dir()
                      else bids_root / "derivatives" / "qc")

    all_records = []
    for subj in subjects:
        if not subj.startswith("sub-"):
            subj = "sub-" + subj.replace("_", "")
        recs = audit_subject(bids_root, subj, groups, fmriprep_root,
                             e12_tokens, xa60_tokens)
        if not recs:
            print(f"[platform] {subj}: no BOLD runs found", file=sys.stderr)
            continue
        n_flag = sum(1 for r in recs if r["flag"])
        plats = ",".join(sorted({r["platform"] for r in recs}))
        print(f"[platform] {subj}: {len(recs)} run(s), platform={plats}, {n_flag} flagged")
        all_records.extend(recs)

    if not all_records:
        print("[platform] nothing to report.")
        return
    summ = write_cohort(out_dir, all_records, args.imbalance)
    # Record the platform-covariate methodology decision (balanced -> DROP the covariate).
    try:
        import sys as _sys, os as _os
        _sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
        import decision_report as _D
        nflags = summ.get("imbalance_flags", 0)
        if nflags:
            _D.write_decision(out_dir, "platform_covariate", "KEEP",
                              f"group×platform imbalance flagged ({nflags}) — add a platform "
                              f"covariate or stratify.",
                              metrics={"imbalance_flags": nflags, "subjects": summ.get("subjects")})
        else:
            _D.write_decision(out_dir, "platform_covariate", "DROP",
                              "platform balanced across groups — covariate unnecessary (Task 33).",
                              metrics={"imbalance_flags": 0, "subjects": summ.get("subjects")})
    except Exception as _exc:  # noqa: BLE001
        print(f"[platform] WARN decision not recorded: {_exc}")
    print("[platform] Done.")  # always exit 0


if __name__ == "__main__":
    main()
