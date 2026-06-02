#!/usr/bin/env python3
"""
Step 4 — Quality Control

Loads the full-session .mat recording and pseudotime_mapping.json,
computes signal-quality metrics for all four physiological channels,
and produces a multi-panel QC figure with a shared time axis.

Handles both LabChart .mat formats automatically:
  Classic  — data / datastart / dataend
  Block1   — data_block1  (4 × N array)

Panels (all share the same x-axis in seconds):
  1. Respiratory signal  (raw + filtered, peaks ▲ and troughs ▼)
  2. Respiratory inter-peak intervals  (beat-by-beat regularity trace)
  3. RPIEZO cardiac signal  (raw + filtered, cardiac peaks ▲)
  4. MRTRIG  (full signal + detected trigger events)
  5. Sequence coverage timeline

QC metrics with pass/warn/fail thresholds:
  resp_snr            — RESP signal quality  (ok >5, warn >2)
  resp_rate_bpm       — breathing rate (bpm)  (ok 8–20, warn 4–30)
  resp_regularity_cv  — CV% of inter-breath intervals  (ok <20%, warn <40%)
  cardiac_snr         — RPIEZO signal quality  (ok >5, warn >2)
  cardiac_rate_bpm    — heart rate (bpm)  (ok 50–100, warn 40–120)
  trigger_cv_pct      — CV% of inter-MR-trigger intervals  (ok <5%, warn <15%)
  sequence_coverage   — % of recording spanned by mapped sequences  (ok >80%, warn >50%)

Usage:
    python step04_qc.py <data_dir> [output_dir]

    data_dir    Folder containing the .mat file and pseudotime_mapping.json
    output_dir  Where to write outputs (default: data_dir/qc/)

Outputs:
    physio_qc_plot.png      — multi-panel QC figure
    physio_qc_metrics.csv   — per-metric table with values and status
"""

import csv
import glob
import json
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import scipy.io as sio
from scipy.signal import butter, sosfiltfilt, find_peaks


FS            = 1000     # Hz — LabChart default sampling rate
CHANNEL_NAMES = ["RESP", "RPIEZO", "STIMTRIG", "MRTRIG"]


# ── QC thresholds ─────────────────────────────────────────────────────────────

THRESHOLDS = {
    "resp_snr":            {"ok": 5.0,        "warn": 2.0},
    "resp_rate_bpm":       {"ok": (8, 20),    "warn": (4, 30)},
    "resp_regularity_cv":  {"ok": 20.0,       "warn": 40.0},   # lower is better
    "cardiac_snr":         {"ok": 5.0,        "warn": 2.0},
    "cardiac_rate_bpm":    {"ok": (50, 100),  "warn": (40, 120)},
    "trigger_cv_pct":      {"ok": 5.0,        "warn": 15.0},   # lower is better
    "sequence_coverage":   {"ok": 80.0,       "warn": 50.0},
}

_LOWER_IS_BETTER  = {"resp_regularity_cv", "trigger_cv_pct"}
STATUS_COLORS     = {"ok": "#4ec97b", "warn": "#dcdcaa", "fail": "#f44747"}


def status(value, key):
    t = THRESHOLDS[key]
    if isinstance(t["ok"], tuple):
        lo_ok, hi_ok = t["ok"]
        lo_w,  hi_w  = t["warn"]
        if lo_ok <= value <= hi_ok:  return "ok"
        if lo_w  <= value <= hi_w:   return "warn"
        return "fail"
    elif key in _LOWER_IS_BETTER:
        if value <= t["ok"]:   return "ok"
        if value <= t["warn"]: return "warn"
        return "fail"
    else:
        if value >= t["ok"]:   return "ok"
        if value >= t["warn"]: return "warn"
        return "fail"


# ── File discovery / loaders ─────────────────────────────────────────────────

def _find_source_mat(data_dir, mapping):
    ref = mapping.get("reference_mat_file", "")
    if ref:
        c = os.path.join(data_dir, ref)
        if os.path.exists(c):
            return c
    mats = [p for p in glob.glob(os.path.join(data_dir, "*.mat"))
            if "bold" not in os.path.basename(p).lower()]
    return mats[0] if mats else None


def load_channels(mat_path):
    """Return dict {channel_name: np.ndarray}.  Handles Classic and Block1 formats."""
    mat      = sio.loadmat(mat_path)
    channels = {}
    if "data_block1" in mat:
        block = mat["data_block1"]
        for i, name in enumerate(CHANNEL_NAMES):
            if i < block.shape[0]:
                channels[name] = block[i].flatten().astype(float)
    elif "data" in mat and "datastart" in mat:
        data      = mat["data"].flatten()
        datastart = mat["datastart"].flatten().astype(int)
        dataend   = mat["dataend"].flatten().astype(int)
        for i, name in enumerate(CHANNEL_NAMES):
            if i < len(datastart):
                channels[name] = data[datastart[i] - 1 : dataend[i]]
    return channels


# ── Signal processing ─────────────────────────────────────────────────────────

def _bandpass(sig, fs, lo, hi):
    sos = butter(4, [lo, hi], btype="bandpass", fs=fs, output="sos")
    return sosfiltfilt(sos, sig)


def _snr(raw, filtered):
    noise = raw - filtered
    return float(np.std(filtered) / np.std(noise)) if np.std(noise) > 0 else 0.0


def _detect_peaks(sig_filt, fs, min_s, prominence_scale=0.1):
    prom = prominence_scale * np.std(sig_filt)
    pk,  _ = find_peaks( sig_filt, distance=int(min_s * fs), prominence=prom)
    tr_, _ = find_peaks(-sig_filt, distance=int(min_s * fs), prominence=prom)
    return pk, tr_


def _detect_triggers(mrtrig, fs):
    if len(mrtrig) == 0:
        return np.array([], dtype=int)
    thr   = 0.5 * np.max(np.abs(mrtrig))
    above = (mrtrig > thr).astype(int)
    edges = np.where(np.diff(above) > 0)[0] + 1
    if len(edges) > 1:
        keep = [int(edges[0])]
        for t in edges[1:]:
            if t - keep[-1] > 0.1 * fs:   # 100 ms debounce
                keep.append(int(t))
        edges = np.array(keep, dtype=int)
    return edges


# ── QC metrics ────────────────────────────────────────────────────────────────

def compute_metrics(channels, mapping, fs):
    """Return dict of scalar metrics plus internal arrays prefixed with '_'."""
    m = {}

    resp = channels.get("RESP")
    if resp is not None and len(resp) > fs * 10:
        resp_filt          = _bandpass(resp, fs, 0.1, 0.8)
        peaks, troughs     = _detect_peaks(resp_filt, fs, min_s=1.5)
        m["resp_snr"]      = _snr(resp, resp_filt)
        m["_resp_filt"]    = resp_filt
        m["_resp_peaks"]   = peaks
        m["_resp_troughs"] = troughs
        if len(peaks) > 1:
            ipi                     = np.diff(peaks) / fs
            m["resp_rate_bpm"]      = float(60.0 / np.mean(ipi))
            m["resp_regularity_cv"] = float(100.0 * np.std(ipi) / np.mean(ipi))

    rp = channels.get("RPIEZO")
    if rp is not None and len(rp) > fs * 5:
        rp_filt             = _bandpass(rp, fs, 0.5, 5.0)
        card_peaks, _       = _detect_peaks(rp_filt, fs, min_s=0.3)
        m["cardiac_snr"]    = _snr(rp, rp_filt)
        m["_rp_filt"]       = rp_filt
        m["_card_peaks"]    = card_peaks
        if len(card_peaks) > 1:
            ibi                    = np.diff(card_peaks) / fs
            m["cardiac_rate_bpm"]  = float(60.0 / np.mean(ibi))

    mr = channels.get("MRTRIG")
    if mr is not None and len(mr) > 0:
        triggers          = _detect_triggers(mr, fs)
        m["_triggers"]    = triggers
        if len(triggers) > 1:
            iti                  = np.diff(triggers) / fs
            m["trigger_cv_pct"]  = float(100.0 * np.std(iti) / np.mean(iti))
            m["_trigger_iti"]    = iti

    pseud = mapping.get("pseudotime_mapping", {})
    if pseud and mr is not None:
        dur_s   = len(mr) / fs
        times   = sorted(info["pseudotime_sec"] for info in pseud.values())
        covered = 0.0
        for i, t_start in enumerate(times):
            t_end    = times[i + 1] if i + 1 < len(times) else t_start + 120.0
            covered += max(0.0, min(t_end, dur_s) - t_start)
        m["sequence_coverage"] = float(100.0 * covered / dur_s) if dur_s > 0 else 0.0
        m["n_sequences"]       = len(pseud)

    return m


def metrics_table(m):
    """Return list of (label, value_str, status_str) for thresholded metrics."""
    checks = [
        ("resp_snr",           "RESP signal quality (SNR)",     ".1f"),
        ("resp_rate_bpm",      "Breathing rate (bpm)",          ".1f"),
        ("resp_regularity_cv", "Breathing regularity (CV %)",   ".1f"),
        ("cardiac_snr",        "Cardiac signal quality (SNR)",  ".1f"),
        ("cardiac_rate_bpm",   "Heart rate (bpm)",              ".1f"),
        ("trigger_cv_pct",     "MR trigger regularity (CV %)",  ".2f"),
        ("sequence_coverage",  "Sequence coverage (%)",         ".1f"),
    ]
    rows = []
    for key, label, fmt in checks:
        if key in m:
            val = m[key]
            st  = status(val, key)
            rows.append((label, f"{val:{fmt}}", st))
    return rows


# ── Figure ────────────────────────────────────────────────────────────────────

_TASK_COLORS = {
    "rest":          "#1f77b4",
    "FreeBreath":    "#8c564b",
    "PaceBreath":    "#e377c2",
    "BEAT":          "#bcbd22",
    "ContinuousStim":"#2ca02c",
    "BlockStim":     "#ff7f0e",
    "AP":            "#d62728",
    "PA":            "#9467bd",
}


def make_figure(channels, mapping, metrics, fs, plot_path):
    ds   = max(1, int(fs / 200))   # downsample to ~200 Hz for display
    resp = channels.get("RESP")
    rp   = channels.get("RPIEZO")
    mr   = channels.get("MRTRIG")
    pseud = mapping.get("pseudotime_mapping", {})

    resp_filt    = metrics.get("_resp_filt")
    resp_peaks   = metrics.get("_resp_peaks",   np.array([], dtype=int))
    resp_troughs = metrics.get("_resp_troughs", np.array([], dtype=int))
    rp_filt      = metrics.get("_rp_filt")
    card_peaks   = metrics.get("_card_peaks",   np.array([], dtype=int))
    triggers     = metrics.get("_triggers",     np.array([], dtype=int))

    has_resp  = resp is not None
    has_ipi   = has_resp and len(resp_peaks) > 1
    has_rp    = rp is not None
    has_mr    = mr is not None
    has_pseud = bool(pseud)

    n_panels = sum([has_resp, has_ipi, has_rp, has_mr, has_pseud])
    if n_panels == 0:
        print("  No data to plot.")
        return

    dur_s = max(
        len(resp) / fs if has_resp else 0,
        len(rp)   / fs if has_rp   else 0,
        len(mr)   / fs if has_mr   else 0,
    )

    fig, axes = plt.subplots(
        n_panels, 1,
        figsize=(max(20, int(dur_s / 60)), 3.5 * n_panels),
        sharex=True,
    )
    if n_panels == 1:
        axes = [axes]
    ax_i = 0

    # ── RESP signal ───────────────────────────────────────────────────────────
    if has_resp:
        ax = axes[ax_i]; ax_i += 1
        t  = np.arange(len(resp)) / fs
        ax.plot(t[::ds], resp[::ds], color="#888888", lw=0.3, alpha=0.5, label="Raw")
        if resp_filt is not None:
            ax.plot(t[::ds], resp_filt[::ds], color="#4e9cd0", lw=0.8,
                    label="Filtered (0.1–0.8 Hz)")
        if len(resp_peaks):
            ax.plot(resp_peaks / fs,
                    (resp_filt if resp_filt is not None else resp)[resp_peaks],
                    "r^", ms=5, zorder=5, label=f"Peaks n={len(resp_peaks)}")
        if len(resp_troughs):
            ax.plot(resp_troughs / fs,
                    (resp_filt if resp_filt is not None else resp)[resp_troughs],
                    "gv", ms=5, zorder=5, label=f"Troughs n={len(resp_troughs)}")
        bpm_s = f"  {metrics['resp_rate_bpm']:.1f} bpm" if "resp_rate_bpm" in metrics else ""
        snr_s = f"  SNR={metrics['resp_snr']:.1f}"      if "resp_snr"       in metrics else ""
        ax.set_ylabel("RESP\nSignal", fontsize=9)
        ax.set_title(f"① Respiratory Signal{bpm_s}{snr_s}",
                     loc="left", fontsize=10, fontweight="bold")
        ax.legend(fontsize=8, loc="upper right", ncol=4)
        ax.grid(True, alpha=0.25)

    # ── RESP inter-peak intervals ─────────────────────────────────────────────
    if has_ipi:
        ax = axes[ax_i]; ax_i += 1
        ipi    = np.diff(resp_peaks) / fs
        t_ipi  = resp_peaks[:-1] / fs
        cv_s   = f"  CV={metrics['resp_regularity_cv']:.1f}%" \
                 if "resp_regularity_cv" in metrics else ""
        ax.step(t_ipi, ipi, where="post", color="#4ec97b", lw=1.0)
        ax.axhline(np.mean(ipi), color="#dcdcaa", lw=1.0, ls="--",
                   label=f"Mean {np.mean(ipi):.2f} s  ({60/np.mean(ipi):.1f} bpm)")
        ax.set_ylabel("Inter-breath\nInterval (s)", fontsize=9)
        ax.set_title(f"② Breathing Regularity{cv_s}",
                     loc="left", fontsize=10, fontweight="bold")
        ax.legend(fontsize=8, loc="upper right")
        ax.grid(True, alpha=0.25)

    # ── RPIEZO ───────────────────────────────────────────────────────────────
    if has_rp:
        ax = axes[ax_i]; ax_i += 1
        t  = np.arange(len(rp)) / fs
        ax.plot(t[::ds], rp[::ds], color="#888888", lw=0.3, alpha=0.5, label="Raw")
        if rp_filt is not None:
            ax.plot(t[::ds], rp_filt[::ds], color="#f08080", lw=0.8,
                    label="Filtered (0.5–5 Hz)")
        if len(card_peaks):
            ax.plot(card_peaks / fs,
                    (rp_filt if rp_filt is not None else rp)[card_peaks],
                    "r^", ms=3, zorder=5, label=f"Peaks n={len(card_peaks)}")
        bpm_c = f"  {metrics['cardiac_rate_bpm']:.1f} bpm" if "cardiac_rate_bpm" in metrics else ""
        snr_c = f"  SNR={metrics['cardiac_snr']:.1f}"       if "cardiac_snr"       in metrics else ""
        ax.set_ylabel("RPIEZO\n(Cardiac)", fontsize=9)
        ax.set_title(f"③ Cardiac Signal{bpm_c}{snr_c}",
                     loc="left", fontsize=10, fontweight="bold")
        ax.legend(fontsize=8, loc="upper right", ncol=3)
        ax.grid(True, alpha=0.25)

    # ── MRTRIG ────────────────────────────────────────────────────────────────
    if has_mr:
        ax = axes[ax_i]; ax_i += 1
        t  = np.arange(len(mr)) / fs
        ax.plot(t[::ds], mr[::ds], color="#a0c878", lw=0.5)
        if len(triggers):
            ax.plot(triggers / fs, mr[triggers], "r|", ms=8, lw=1.5,
                    label=f"Triggers n={len(triggers)}")
        cv_s = f"  CV={metrics['trigger_cv_pct']:.2f}%" if "trigger_cv_pct" in metrics else ""
        ax.set_ylabel("MRTRIG", fontsize=9)
        ax.set_title(f"④ MR Trigger Channel{cv_s}",
                     loc="left", fontsize=10, fontweight="bold")
        ax.legend(fontsize=8, loc="upper right")
        ax.grid(True, alpha=0.25)

    # ── Sequence coverage timeline ────────────────────────────────────────────
    if has_pseud:
        ax = axes[ax_i]; ax_i += 1
        task_idx = {}
        for fname in sorted(pseud, key=lambda f: pseud[f]["pseudotime_sec"]):
            task = fname.split("task-")[1].split("_run")[0] if "task-" in fname else fname
            if task not in task_idx:
                task_idx[task] = len(task_idx)

        for fname, info in pseud.items():
            task    = fname.split("task-")[1].split("_run")[0] if "task-" in fname else fname
            t_start = info["pseudotime_sec"]
            t_end   = t_start + 120.0   # approximate width
            y       = task_idx.get(task, 0)
            c       = _TASK_COLORS.get(task, "#999999")
            ax.add_patch(mpatches.FancyBboxPatch(
                (t_start, y - 0.38), t_end - t_start, 0.76,
                boxstyle="round,pad=0.01",
                facecolor=c, edgecolor="black", lw=0.7, alpha=0.75))
            ax.text(t_start + (t_end - t_start) / 2, y, task,
                    ha="center", va="center", fontsize=6,
                    fontweight="bold", clip_on=True)

        cov_s = f"  coverage≈{metrics['sequence_coverage']:.1f}%" \
                if "sequence_coverage" in metrics else ""
        ax.set_ylim(-0.7, max(task_idx.values()) + 0.7 if task_idx else 1)
        ax.set_yticks(list(task_idx.values()))
        ax.set_yticklabels(list(task_idx.keys()), fontsize=8)
        ax.set_title(f"⑤ Sequence Coverage Timeline{cov_s}",
                     loc="left", fontsize=10, fontweight="bold")
        ax.grid(True, alpha=0.2, axis="x")

    axes[-1].set_xlabel("Time (s)", fontsize=10)
    axes[0].set_xlim(0, dur_s)

    # QC summary footer
    rows = metrics_table(metrics)
    if rows:
        icon    = {"ok": "✓", "warn": "⚠", "fail": "✗"}
        summary = "   ".join(f"{icon[s]} {lbl}: {val}" for lbl, val, s in rows)
        fig.text(0.01, 0.002, summary, fontsize=8.5, color="#cccccc", va="bottom",
                 bbox=dict(boxstyle="round,pad=0.3", fc="#222222", alpha=0.85))

    plt.suptitle("Physioparse QC — Signal Quality & Sequence Coverage",
                 fontsize=13, fontweight="bold", y=1.002)
    plt.tight_layout()
    plt.savefig(plot_path, dpi=130, bbox_inches="tight")
    plt.close()
    print(f"  QC plot  → {plot_path}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: step04_qc.py <data_dir> [output_dir]")
        sys.exit(1)

    data_dir   = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(data_dir, "qc")
    os.makedirs(output_dir, exist_ok=True)

    # ── Pseudotime mapping ────────────────────────────────────────────────────
    json_path = os.path.join(data_dir, "pseudotime_mapping.json")
    if not os.path.isfile(json_path):
        print(f"WARNING: pseudotime_mapping.json not found in {data_dir}")
        mapping = {"pseudotime_mapping": {}}
    else:
        with open(json_path) as f:
            mapping = json.load(f)
        n_seq = len(mapping.get("pseudotime_mapping", {}))
        print(f"  Pseudotime mapping: {n_seq} sequences")

    # ── Load channels ─────────────────────────────────────────────────────────
    mat_path = _find_source_mat(data_dir, mapping)
    if mat_path is None:
        print(f"ERROR: no .mat file found in {data_dir}")
        sys.exit(1)

    print(f"  Loading {os.path.basename(mat_path)} …")
    channels = load_channels(mat_path)
    for name, ch in channels.items():
        print(f"    {name}: {len(ch)} samples  ({len(ch) / FS:.1f} s)")

    # ── Compute metrics ───────────────────────────────────────────────────────
    print("\nComputing QC metrics…")
    metrics = compute_metrics(channels, mapping, FS)

    print("\nQC Results:")
    rows = metrics_table(metrics)
    _icon = {"ok": "✓", "warn": "⚠", "fail": "✗"}
    for lbl, val, st in rows:
        print(f"  {_icon[st]} {lbl}: {val}  [{st.upper()}]")

    # ── Figure ────────────────────────────────────────────────────────────────
    print("\nGenerating QC figure…")
    plot_path = os.path.join(output_dir, "physio_qc_plot.png")
    make_figure(channels, mapping, metrics, FS, plot_path)

    # ── CSV ───────────────────────────────────────────────────────────────────
    csv_path = os.path.join(output_dir, "physio_qc_metrics.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["metric", "value", "status"])
        for lbl, val, st in rows:
            w.writerow([lbl, val, st])
    print(f"  QC CSV   → {csv_path}")

    # ── KEY=VALUE for GUI parsing ─────────────────────────────────────────────
    _gui_keys = [
        ("resp_snr",           "QC_RESP_SNR"),
        ("resp_rate_bpm",      "QC_RESP_RATE_BPM"),
        ("resp_regularity_cv", "QC_RESP_REG_CV"),
        ("cardiac_snr",        "QC_CARDIAC_SNR"),
        ("cardiac_rate_bpm",   "QC_CARDIAC_RATE_BPM"),
        ("trigger_cv_pct",     "QC_TRIGGER_CV_PCT"),
        ("sequence_coverage",  "QC_SEQ_COVERAGE"),
    ]
    for mkey, qc_key in _gui_keys:
        if mkey in metrics:
            val = metrics[mkey]
            st  = status(val, mkey)
            print(f"{qc_key}={val:.4f}")
            print(f"{qc_key}_STATUS={st}")
    print(f"PLOT_PATH={plot_path}")
    print(f"CSV_PATH={csv_path}")
    print("DONE")


if __name__ == "__main__":
    main()
