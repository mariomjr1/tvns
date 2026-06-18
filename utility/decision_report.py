#!/usr/bin/env python3
"""
decision_report.py — shared "methodology decision" report convention.

Every validation step (template residual, physio order, brainstem refinement, transform
chain, platform, …) records ONE decision via `write_decision(...)`: the question, the
numbers it compared, a **verdict** (KEEP / DROP / REVIEW / PENDING) and a one-line
recommendation. `aggregate(...)` rolls every recorded decision into a single human report
(`codes/qc/methodology_decisions.md` + `.csv`) so you read one file and decide which parts
of the methodology to keep / whether they make sense.

Pure standard library; importable + testable. Flag + log (never raises on a bad sidecar).

Verdicts:
  KEEP    — evidence supports keeping this methodology choice
  DROP    — evidence says drop / not worth it
  REVIEW  — needs a human look (e.g. pick the transform chain by overlay)
  PENDING — not run yet (no evidence on disk)
"""
import csv
import json
import os
import sys
from pathlib import Path

VERDICTS = ("KEEP", "DROP", "REVIEW", "PENDING")

# Canonical methodology decisions (order = report order). name -> human title.
# A decision with no sidecar shows as PENDING with its "run" hint.
DECISIONS = [
    ("retroicor_before",  "RETROICOR applied before fMRIPrep (vs physio-in-GLM)",
                          "run utility/brainstem_physio_metrics.py --compare"),
    ("brainstem_refine",  "step05c brainstem co-registration refinement (vs fMRIPrep-only)",
                          "run utility/coreg_dice_report.py"),
    ("transform_chain",   "step10b atlas->native transform chain (C1/C2/C3)",
                          "run utility/chain_report.py + judge the overlay"),
    ("template_residual", "ICBM 2009b->2009c brainstem residual (resample vs warp)",
                          "run utility/measure_template_residual.py"),
    ("platform_covariate", "Scanner console (E12/XA60) as a group covariate",
                          "run utility/audit_platform.py --all"),
    ("whitening",         "Serial-correlation model FAST vs AR(1)",
                          "compare residual-ACF on pilot (Task 17)"),
]
_TITLES = {n: t for n, t, _ in DECISIONS}
_HINTS = {n: h for n, _, h in DECISIONS}


def _dec_dir(qc_dir):
    d = Path(qc_dir) / "decisions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_decision(qc_dir, name, verdict, recommendation, metrics=None, title=None):
    """Record one decision as codes/qc/decisions/<name>.json (+ returns the dict)."""
    verdict = (verdict or "PENDING").upper()
    if verdict not in VERDICTS:
        verdict = "REVIEW"
    rec = {
        "name": name,
        "title": title or _TITLES.get(name, name),
        "verdict": verdict,
        "recommendation": recommendation or "",
        "metrics": metrics or {},
    }
    try:
        path = _dec_dir(qc_dir) / f"{name}.json"
        with open(path, "w") as f:
            json.dump(rec, f, indent=2)
        print(f"[decision] {name}: {verdict} -> {path}")
    except Exception as exc:  # noqa: BLE001
        print(f"[decision] WARN could not write {name}: {exc}", file=sys.stderr)
    return rec


def load_decisions(qc_dir):
    """Return {name: rec} for every recorded decision (missing dir -> {})."""
    out = {}
    d = Path(qc_dir) / "decisions"
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.json")):
        try:
            with open(p) as f:
                r = json.load(f)
            out[r.get("name", p.stem)] = r
        except Exception as exc:  # noqa: BLE001
            print(f"[decision] WARN bad sidecar {p}: {exc}", file=sys.stderr)
    return out


def _metrics_str(m):
    if not m:
        return ""
    return "; ".join(f"{k}={v}" for k, v in m.items())


def render_md(found):
    """Pure function: build the methodology-decisions markdown from {name: rec}."""
    rows = []
    for name, title, hint in DECISIONS:                       # canonical order first
        if name in found:
            rows.append(found[name])
        else:
            rows.append({"name": name, "title": title, "verdict": "PENDING",
                         "recommendation": hint, "metrics": {}})
    for name, rec in found.items():                            # any extra decisions
        if name not in _TITLES:
            rows.append(rec)

    n_keep = sum(1 for r in rows if r["verdict"] == "KEEP")
    n_pend = sum(1 for r in rows if r["verdict"] == "PENDING")
    n_rev = sum(1 for r in rows if r["verdict"] == "REVIEW")
    n_drop = sum(1 for r in rows if r["verdict"] == "DROP")

    L = ["# Methodology decisions", "",
         f"- KEEP **{n_keep}** · DROP **{n_drop}** · REVIEW **{n_rev}** · PENDING **{n_pend}** "
         f"(of {len(rows)} decisions)",
         "- Read the verdict + recommendation; PENDING = run the listed step to get the evidence.",
         "",
         "| decision | verdict | numbers | recommendation |",
         "|---|---|---|---|"]
    icon = {"KEEP": "✅ KEEP", "DROP": "❌ DROP", "REVIEW": "🔶 REVIEW", "PENDING": "⏳ PENDING"}
    for r in rows:
        L.append(f"| {r['title']} | {icon.get(r['verdict'], r['verdict'])} | "
                 f"{_metrics_str(r.get('metrics'))} | {r.get('recommendation','')} |")
    L += ["", "*Generated by utility/methodology_report.py from codes/qc/decisions/*.json.*"]
    return "\n".join(L)


def aggregate(qc_dir, out_md=None):
    """Write methodology_decisions.md + .csv from the recorded decisions. Returns summary."""
    found = load_decisions(qc_dir)
    md = render_md(found)
    out_md = out_md or str(Path(qc_dir) / "methodology_decisions.md")
    Path(out_md).parent.mkdir(parents=True, exist_ok=True)
    with open(out_md, "w") as f:
        f.write(md + "\n")
    out_csv = str(Path(out_md).with_suffix(".csv"))
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["decision", "verdict", "metrics", "recommendation"])
        # mirror render order
        names = [n for n, _, _ in DECISIONS] + [n for n in found if n not in _TITLES]
        for n in names:
            r = found.get(n, {"title": _TITLES.get(n, n), "verdict": "PENDING",
                              "recommendation": _HINTS.get(n, ""), "metrics": {}})
            w.writerow([r.get("title", n), r["verdict"], _metrics_str(r.get("metrics")),
                        r.get("recommendation", "")])
    print(f"[decision] wrote {out_md}\n[decision] wrote {out_csv}")
    return {"md": out_md, "csv": out_csv, "n": len(found)}


if __name__ == "__main__":
    sys.exit(0 if aggregate(sys.argv[1] if len(sys.argv) > 1 else "codes/qc") else 1)
