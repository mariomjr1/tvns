#!/usr/bin/env python3
"""
extract_stim_onsets.py
Extract stimulus onset/offset times from a physioparse per-sequence .mat file.

Algorithm (ported from stim_trigger.m):
  1. Load STIMTRIG and MRTRIG from the parsed per-sequence .mat
  2. Find the first MR trigger in MRTRIG → defines time zero (scan start)
  3. Clip STIMTRIG from that trigger onwards
  4. Clamp signal to [0, 3], detect rising edges (onset) and falling edges (offset)
  5. Debounce events < debounce_sec apart
  6. Pair each onset with the next offset → duration
  7. Build STIMS matrix: [onset_sec  duration_sec  1]

Output:
  <output_dir>/<bids_subject_id>_ses-<session>_task-<task>_run-<run>_bold_stim.txt
  ASCII, one row per stimulus: onset_sec   duration_sec   1

Optional QC plot:
  <qc_dir>/<bids_subject_id>_ses-<session>_task-<task>_run-<run>_stim_qc.png
  Shows STIMTRIG + MRTRIG signals with detected onsets overlaid.

Usage:
  python3 extract_stim_onsets.py <parsed_mat> <bids_subject_id> <output_dir> \
          [--session 01] [--threshold 1.5] [--debounce 1.5] \
          [--qc] [--qc-dir <dir>]

Created by Mario Murakami
"""

import argparse
import os
import re
import sys
import numpy as np
import scipy.io as sio

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ── Core extraction ───────────────────────────────────────────────────────────

def count_mr_triggers(mrtrig: np.ndarray, fs: int = 1000, min_gap_s: float = 0.3) -> int:
    """Count discrete MR-trigger rising edges (one per acquired volume).
    Debounced so a multi-sample edge counts once."""
    d = np.diff(mrtrig.astype(float))
    if d.size == 0 or d.max() <= 0:
        return 0
    cand = np.where(d > d.max() * 0.30)[0]
    if len(cand) == 0:
        return 0
    gap = int(min_gap_s * fs)
    trig = [int(cand[0])]
    for c in cand[1:]:
        if c - trig[-1] >= gap:
            trig.append(int(c))
    return len(trig)


def extract_onsets(stimtrig: np.ndarray, mrtrig: np.ndarray,
                   fs: int = 1000,
                   threshold: float = 1.5,
                   debounce_sec: float = 1.5):
    """
    Detect stimulus onset/offset pairs from STIMTRIG aligned to first MR trigger.

    Each onset is paired with the first offset that falls BEFORE the next onset
    (so a missing/late offset can't create a spurious duration spanning the gap to
    the next block). Onsets without such an offset are reported as dropped.

    Returns
    -------
    stims : np.ndarray  shape (N, 3) — [onset_sec, duration_sec, 1]
    t_rel : np.ndarray  time axis of clipped STIMTRIG (seconds from first MR trigger)
    stim_clip : np.ndarray  clipped + clamped STIMTRIG signal
    info : dict  diagnostics (n_mr_triggers, n_onsets, n_offsets, n_paired,
                 n_dropped, recording_s)
    """
    # Discrete MR triggers (≈ number of acquired volumes — for the volume-count check)
    n_mr = count_mr_triggers(mrtrig, fs)

    # Find first MR trigger (rising edge in MRTRIG)
    mrtrig_diff = np.diff(mrtrig.astype(float))
    peaks = np.where(mrtrig_diff > mrtrig_diff.max() * 0.30)[0]
    if len(peaks) == 0:
        raise ValueError("No MR triggers detected in MRTRIG channel")

    start_ix = int(peaks[0])
    stop_ix  = min(len(stimtrig), len(mrtrig))

    if start_ix >= stop_ix:
        raise ValueError(f"start_ix ({start_ix}) >= stop_ix ({stop_ix}) — check alignment")

    # Clip and clamp
    stim_clip = stimtrig[start_ix:stop_ix].astype(float)
    stim_clip = np.clip(stim_clip, 0.0, 3.0)

    n = len(stim_clip)
    t_rel = np.arange(n) / fs   # seconds from first MR trigger

    # Detect edges
    d = np.diff(stim_clip)
    start_inds = np.where(d >  threshold)[0]
    stop_inds  = np.where(d < -threshold)[0]

    # Debounce: remove events within debounce_sec of the previous one
    def debounce(inds, min_gap_samples):
        if len(inds) == 0:
            return inds
        keep = [inds[0]]
        for idx in inds[1:]:
            if idx - keep[-1] >= min_gap_samples:
                keep.append(idx)
        return np.array(keep)

    min_gap = int(debounce_sec * fs)
    start_inds = debounce(start_inds, min_gap)
    stop_inds  = debounce(stop_inds,  min_gap)

    on_times  = t_rel[start_inds] if len(start_inds) else np.array([])
    off_times = t_rel[stop_inds]  if len(stop_inds)  else np.array([])

    # Pair onset[i] with the first offset in (onset[i], onset[i+1]); else drop it
    paired_on, paired_off = [], []
    n_dropped = 0
    oj = 0
    for i, ot in enumerate(on_times):
        next_on = on_times[i + 1] if (i + 1) < len(on_times) else np.inf
        while oj < len(off_times) and off_times[oj] <= ot:
            oj += 1
        if oj < len(off_times) and off_times[oj] < next_on:
            paired_on.append(ot)
            paired_off.append(off_times[oj])
            oj += 1
        else:
            n_dropped += 1   # no offset before the next onset (overlap / missed edge)

    info = {"n_mr_triggers": n_mr,
            "n_onsets": int(len(on_times)),
            "n_offsets": int(len(off_times)),
            "n_paired": int(len(paired_on)),
            "n_dropped": int(n_dropped),
            "recording_s": round(n / fs, 1)}

    if not paired_on:
        return np.zeros((0, 3)), t_rel, stim_clip, info

    stims = np.ones((len(paired_on), 3))
    stims[:, 0] = paired_on
    stims[:, 1] = np.array(paired_off) - np.array(paired_on)
    return stims, t_rel, stim_clip, info


# ── QC plot ───────────────────────────────────────────────────────────────────

def make_qc_plot(t_rel: np.ndarray, stim_clip: np.ndarray,
                 mrtrig_clip: np.ndarray, stims: np.ndarray,
                 title: str, out_path: str):
    """Save a two-panel QC figure: STIMTRIG (top) + MRTRIG (bottom)."""
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(16, 6), sharex=True)
    fig.suptitle(title, fontsize=12, fontweight="bold")

    # STIMTRIG panel
    ax1.plot(t_rel, stim_clip, color="#4ec9b0", linewidth=0.7, label="STIMTRIG")
    for i, row in enumerate(stims):
        onset, dur = row[0], row[1]
        ax1.axvline(onset, color="#f44747", linewidth=1.2,
                    label="Onset" if i == 0 else "")
        ax1.axvspan(onset, onset + dur, alpha=0.15, color="#f44747")
        ax1.text(onset + dur / 2, ax1.get_ylim()[1] * 0.85,
                 f"{onset:.1f}s\n{dur:.1f}s",
                 ha="center", va="top", fontsize=7, color="#f44747")
    ax1.set_ylabel("STIMTRIG", fontsize=9)
    ax1.legend(fontsize=8, loc="upper right")
    ax1.grid(True, alpha=0.2)
    n = len(stims)
    ax1.set_title(f"{n} event(s) detected", fontsize=9)

    # MRTRIG panel
    ax2.plot(t_rel[:len(mrtrig_clip)], mrtrig_clip, color="#9cdcfe",
             linewidth=0.5, label="MRTRIG")
    ax2.set_ylabel("MRTRIG", fontsize=9)
    ax2.set_xlabel("Time from first MR trigger (s)", fontsize=10)
    ax2.legend(fontsize=8, loc="upper right")
    ax2.grid(True, alpha=0.2)

    plt.tight_layout()
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    plt.savefig(out_path, dpi=120, bbox_inches="tight")
    plt.close(fig)


# ── Main ──────────────────────────────────────────────────────────────────────

def process_one(mat_path: str, bids_subject_id: str, output_dir: str,
                session: str = "01", threshold: float = 1.5,
                debounce_sec: float = 1.5, generate_qc: bool = False,
                qc_dir: str | None = None,
                expected_events: int | None = None,
                n_volumes: int | None = None) -> dict:
    """
    Process a single parsed mat file.  Returns a summary dict.
    """
    mat_name = os.path.basename(mat_path)
    # Extract task and run from filename: task-BlockStim_run-01.mat
    m = re.search(r"task-(\w+)_run-(\d+)", mat_name)
    if not m:
        raise ValueError(f"Cannot parse task/run from: {mat_name}")
    task = m.group(1)
    run  = m.group(2)

    bids_base = f"{bids_subject_id}_ses-{session}_task-{task}_run-{run}_bold"
    out_txt   = os.path.join(output_dir, f"{bids_base}_stim.txt")

    # Load
    raw = sio.loadmat(mat_path)
    for ch in ("STIMTRIG", "MRTRIG"):
        if ch not in raw:
            raise ValueError(f"Channel '{ch}' not found in {mat_name}")

    stimtrig = raw["STIMTRIG"].flatten().astype(float)
    mrtrig   = raw["MRTRIG"].flatten().astype(float)
    fs_arr   = raw.get("sampling_rate", np.array([[1000]]))
    fs       = int(np.asarray(fs_arr).flatten()[0])

    stims, t_rel, stim_clip, info = extract_onsets(
        stimtrig, mrtrig, fs=fs, threshold=threshold, debounce_sec=debounce_sec)

    # Save STIMS table (ASCII, space-separated: onset dur 1)
    os.makedirs(output_dir, exist_ok=True)
    if len(stims) > 0:
        np.savetxt(out_txt, stims, fmt="%.6f", delimiter="\t")
    else:
        # Write header comment and empty file so downstream tools don't crash
        with open(out_txt, "w") as fh:
            fh.write("# No stimulus events detected\n")

    # ── Robustness checks (Task 11) ───────────────────────────────────────────
    warnings = []
    if info["n_dropped"] > 0:
        warnings.append(f"{info['n_dropped']} onset(s) had no offset before the next onset "
                        f"(overlap / missed edge / debounce too large)")
    if expected_events is not None and info["n_paired"] != expected_events:
        warnings.append(f"detected {info['n_paired']} events but expected {expected_events} "
                        f"(tune --threshold / --debounce)")
    # MR-trigger vs BOLD volume-count: onsets are timed from the first MR trigger, so a
    # mismatch with the modeled BOLD #volumes shifts every onset (dummy-scan drop, etc.)
    if n_volumes is not None and info["n_mr_triggers"] != n_volumes:
        warnings.append(f"MR triggers ({info['n_mr_triggers']}) != BOLD volumes ({n_volumes}) "
                        f"— onsets may be shifted vs the fMRIPrep BOLD timeline")

    summary = {
        "mat":      mat_name,
        "task":     task,
        "run":      run,
        "n_events": len(stims),
        "output":   out_txt,
        "stims":    stims,
        "info":     info,
        "warnings": warnings,
    }

    # Optional QC plot
    if generate_qc:
        qd = qc_dir or os.path.join(output_dir, "qc")
        qc_path = os.path.join(qd, f"{bids_base}_stim_qc.png")
        start_ix = np.where(np.diff(mrtrig.astype(float)) >
                            np.diff(mrtrig.astype(float)).max() * 0.30)[0]
        start_ix = int(start_ix[0]) if len(start_ix) else 0
        mrtrig_clip = mrtrig[start_ix:start_ix + len(stim_clip)]
        title = f"{bids_base}  |  {len(stims)} event(s)"
        make_qc_plot(t_rel, stim_clip, mrtrig_clip, stims, title, qc_path)
        summary["qc_plot"] = qc_path

    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("parsed_mat",      help="Path to physioparse parsed .mat file")
    ap.add_argument("bids_subject_id", help="BIDS subject ID (e.g. sub-7T1019HC042726)")
    ap.add_argument("output_dir",      help="Directory to write *_stim.txt")
    ap.add_argument("--session",   default="01",  help="BIDS session label (default: 01)")
    ap.add_argument("--threshold", type=float, default=1.5,
                    help="Step-change threshold for onset/offset detection (default: 1.5)")
    ap.add_argument("--debounce",  type=float, default=1.5,
                    help="Minimum seconds between events (default: 1.5)")
    ap.add_argument("--qc",        action="store_true",
                    help="Generate QC plot")
    ap.add_argument("--qc-dir",    default=None,
                    help="QC plot directory (default: output_dir/qc/)")
    ap.add_argument("--expected-events", type=int, default=None,
                    help="Warn if the detected event count differs from this (paradigm check)")
    ap.add_argument("--n-volumes", type=int, default=None,
                    help="Warn if the MR-trigger count differs from this (BOLD volume-count check)")
    args = ap.parse_args()

    try:
        result = process_one(
            mat_path=args.parsed_mat,
            bids_subject_id=args.bids_subject_id,
            output_dir=args.output_dir,
            session=args.session,
            threshold=args.threshold,
            debounce_sec=args.debounce,
            generate_qc=args.qc,
            qc_dir=args.qc_dir,
            expected_events=args.expected_events,
            n_volumes=args.n_volumes,
        )
        n = result["n_events"]
        info = result["info"]
        qc_str = f"  QC: {result.get('qc_plot', '')}" if args.qc else ""
        print(f"[OK] {result['task']} run-{result['run']}: "
              f"{n} event(s) -> {result['output']}{qc_str}")
        print(f"     MR triggers={info['n_mr_triggers']}  onsets={info['n_onsets']}  "
              f"offsets={info['n_offsets']}  paired={info['n_paired']}  "
              f"dropped={info['n_dropped']}  rec={info['recording_s']}s")
        for w in result["warnings"]:
            print(f"     [WARN] {w}", file=sys.stderr)
        sys.exit(0)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
