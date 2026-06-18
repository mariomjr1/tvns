#!/usr/bin/env python3
"""
roi_group_stats.py — cases-vs-controls group test PER brainstem nucleus, with OPTIONAL
two-tailed and OPTIONAL FDR (the reviewer's primary brainstem inference).

Reads a per-subject ROI table (the `roi_values.csv` from step10, or
`group_brainstem_nuclei_native.csv` from step10b — one row per subject, columns = nuclei),
splits it into cases vs controls, and for each nucleus runs a two-sample (Welch) t-test.

Defaults (match the study design):
  - **one-tailed, cases > controls** (the directional hypothesis). `--two-tailed` switches.
  - **uncorrected** p. `--fdr` adds Benjamini-Hochberg across the nuclei (× tasks if you
    pass several CSVs) and is the recommended PRIMARY report.

Pure standard library (no numpy/scipy) so it is fully testable; flag + log + continue
(missing/short data → that nucleus gets NaN, never aborts; exits 0).

Usage:
  python roi_group_stats.py --roi-csv <roi_values.csv> [<more.csv> …] \
      --cases cases.txt --controls controls.txt [--two-tailed] [--fdr] [--fdr-q 0.05] \
      [--value-cols nuc_VSM_L nuc_LC_L …] [--out group_roi_stats.csv]
"""
import argparse
import csv
import math
import os
import sys


# ── Student-t tail probability via the regularized incomplete beta (stdlib) ────
def _betacf(a, b, x):
    MAXIT, EPS, FPMIN = 200, 3e-12, 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < FPMIN:
        d = FPMIN
    d = 1.0 / d
    h = d
    for m in range(1, MAXIT + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < FPMIN:
            d = FPMIN
        c = 1.0 + aa / c
        if abs(c) < FPMIN:
            c = FPMIN
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < EPS:
            break
    return h


def _betai(a, b, x):
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(lbeta + a * math.log(x) + b * math.log(1.0 - x))
    if x < (a + 1.0) / (a + b + 2.0):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1.0 - x) / b


def _t_sf(t, df):
    """P(T >= t) for Student-t with df."""
    if df <= 0:
        return float("nan")
    p2 = _betai(df / 2.0, 0.5, df / (df + t * t))   # = P(|T| >= |t|)
    return 0.5 * p2 if t >= 0 else 1.0 - 0.5 * p2


def welch(cases, controls, two_tailed=False):
    """Welch two-sample t; returns (t, df, p, mean_c, mean_k). p is one-tailed cases>controls
    unless two_tailed."""
    nc, nk = len(cases), len(controls)
    if nc < 2 or nk < 2:
        return (float("nan"),) * 5
    mc = sum(cases) / nc
    mk = sum(controls) / nk
    vc = sum((x - mc) ** 2 for x in cases) / (nc - 1)
    vk = sum((x - mk) ** 2 for x in controls) / (nk - 1)
    se2 = vc / nc + vk / nk
    if se2 <= 0:
        return (float("nan"), float("nan"), float("nan"), mc, mk)
    t = (mc - mk) / math.sqrt(se2)
    df = se2 ** 2 / ((vc / nc) ** 2 / (nc - 1) + (vk / nk) ** 2 / (nk - 1))
    if two_tailed:
        p = _betai(df / 2.0, 0.5, df / (df + t * t))     # P(|T| >= |t|)
    else:
        p = _t_sf(t, df)                                  # P(T >= t): cases > controls
    return t, df, p, mc, mk


def bh_fdr(pvals):
    """Benjamini-Hochberg adjusted p-values (same order as input). NaNs pass through."""
    idx = [i for i, p in enumerate(pvals) if p == p]      # non-NaN
    m = len(idx)
    out = [float("nan")] * len(pvals)
    if m == 0:
        return out
    order = sorted(idx, key=lambda i: pvals[i])
    prev = 1.0
    for rank in range(m, 0, -1):
        i = order[rank - 1]
        q = pvals[i] * m / rank
        prev = min(prev, q)
        out[i] = min(prev, 1.0)
    return out


def _norm(s):
    return "".join(ch for ch in str(s).lower() if ch.isalnum())


def _load_ids(path):
    if not path or not os.path.isfile(path):
        return set()
    with open(path) as f:
        return {_norm(ln) for ln in f if ln.strip()}


def main():
    ap = argparse.ArgumentParser(description="Per-nucleus cases-vs-controls group test")
    ap.add_argument("--roi-csv", nargs="+", required=True, help="per-subject ROI CSV(s)")
    ap.add_argument("--cases", required=True)
    ap.add_argument("--controls", required=True)
    ap.add_argument("--two-tailed", action="store_true",
                    help="two-tailed test (default: one-tailed cases > controls)")
    ap.add_argument("--fdr", action="store_true",
                    help="Benjamini-Hochberg FDR across nuclei×CSVs (recommended primary report)")
    ap.add_argument("--fdr-q", type=float, default=0.05)
    ap.add_argument("--value-cols", nargs="+", default=None,
                    help="columns to test (default: nuc_* if present, else all numeric non-subject)")
    ap.add_argument("--out", default="group_roi_stats.csv")
    a = ap.parse_args()

    cases, controls = _load_ids(a.cases), _load_ids(a.controls)
    if not cases or not controls:
        print("ERROR: empty cases/controls list", file=sys.stderr); return 1

    rows = []   # (source, nucleus, n_c, n_k, mean_cases, mean_controls, diff, t, df, p)
    for path in a.roi_csv:
        if not os.path.isfile(path):
            print(f"[roi-stats] WARN missing {path} — skipped", file=sys.stderr); continue
        with open(path, newline="") as f:
            data = list(csv.DictReader(f))
        if not data:
            continue
        cols = list(data[0].keys())
        subj_col = cols[0]
        cand = a.value_cols or [c for c in cols if c.startswith("nuc_")] \
            or [c for c in cols[1:]]
        for col in cand:
            cvals, kvals = [], []
            for r in data:
                key = _norm(r.get(subj_col, ""))
                grp = "c" if key in cases else ("k" if key in controls else None)
                if grp is None:
                    # also try contains-match (filename vs id)
                    if any(cid in key or key in cid for cid in cases):
                        grp = "c"
                    elif any(kid in key or key in kid for kid in controls):
                        grp = "k"
                if grp is None:
                    continue
                try:
                    v = float(r.get(col, ""))
                except (TypeError, ValueError):
                    continue
                if v == v:
                    (cvals if grp == "c" else kvals).append(v)
            t, df, p, mc, mk = welch(cvals, kvals, two_tailed=a.two_tailed)
            rows.append([os.path.basename(path), col, len(cvals), len(kvals),
                         mc, mk, (mc - mk) if mc == mc else float("nan"), t, df, p])

    pvals = [r[9] for r in rows]
    qvals = bh_fdr(pvals) if a.fdr else [float("nan")] * len(rows)
    tail = "two-tailed" if a.two_tailed else "one-tailed(cases>controls)"

    Path_out = a.out
    os.makedirs(os.path.dirname(os.path.abspath(Path_out)), exist_ok=True)
    with open(Path_out, "w", newline="") as f:
        w = csv.writer(f)
        head = ["source", "nucleus", "n_cases", "n_controls", "mean_cases", "mean_controls",
                "mean_diff", "t", "df", "p_" + ("two" if a.two_tailed else "pos")]
        if a.fdr:
            head += ["p_fdr", f"sig_fdr_q{a.fdr_q}"]
        else:
            head += ["sig_unc_p0.05"]
        w.writerow(head)
        nsig = 0
        for r, q in zip(rows, qvals):
            out = r[:]
            for i in (4, 5, 6, 7, 8, 9):
                out[i] = "" if out[i] != out[i] else round(out[i], 5)
            if a.fdr:
                sig = (q == q and q < a.fdr_q)
                out += ["" if q != q else round(q, 5), int(sig)]
            else:
                p = r[9]
                sig = (p == p and p < 0.05)
                out += [int(sig)]
            nsig += int(sig)
            w.writerow(out)

    corr = f"FDR q<{a.fdr_q}" if a.fdr else "uncorrected p<0.05"
    print(f"[roi-stats] {len(rows)} nucleus test(s), {tail}, {corr}: {nsig} significant.")
    print(f"[roi-stats] wrote {Path_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
