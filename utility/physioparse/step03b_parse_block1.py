#!/usr/bin/env python3
"""
Script: 3b_parse_block1.py
Purpose: Parse the .mat file (block1 format) into per-sequence segments using pseudotime_mapping.json.

Block1 format: data_block1 is a (4, N) array.
  Row 0: RESP  |  Row 1: RPIEZO  |  Row 2: STIMTRIG  |  Row 3: MRTRIG

For each sequence:
  - Extracts the 4 channels (RESP, RPIEZO, STIMTRIG, MRTRIG) for that time window
  - Saves the segment as a .mat file
  - Saves a 4-panel plot of the segment

Source file discovery (DATA_DIR):
  1. Uses the filename recorded in pseudotime_mapping.json ("reference_mat_file").
  2. If not found, falls back to any *.mat in DATA_DIR (excluding per-sequence bold.mat files).
  3. If still not found but a *.adicht exists, exits with an informative message —
     adi-reader only ships Windows DLLs and cannot be used on macOS/Linux.

Sequences with no matching dicominfo row are skipped and written to
parsed/unmatched_sequences.log instead of receiving a 120 s fallback.
"""

import glob
import json
import os
import re
import sys

import matplotlib.pyplot as plt
import numpy as np
import scipy.io as sio

# ── Paths ──────────────────────────────────────────────────────────────────────
DATA_DIR   = '/Users/mariomjr/Desktop/pseudotime/data'
OUTPUT_DIR = '/Users/mariomjr/Desktop/pseudotime/parsed'
JSON_FILE  = os.path.join(DATA_DIR, 'pseudotime_mapping.json')
TSV_FILE   = os.path.join(DATA_DIR, 'dicominfo_ses-01.tsv')
LOG_FILE   = os.path.join(OUTPUT_DIR, 'unmatched_sequences.log')

CHANNEL_NAMES  = ['RESP', 'RPIEZO', 'STIMTRIG', 'MRTRIG']
CHANNEL_COLORS = {'RESP': 'blue', 'RPIEZO': 'red', 'STIMTRIG': 'orange', 'MRTRIG': 'green'}
FS = 1000  # Hz


# ── Source file discovery ──────────────────────────────────────────────────────

def find_source_file(data_dir, mapping):
    """
    Return the path to the full-session .mat file, or exit with an error.

    Search order:
      1. The filename stored in mapping['reference_mat_file'].
      2. Any *.mat in data_dir that is NOT a per-sequence bold.mat.
      3. If only *.adicht files exist, exit with an explanation.
    """
    # 1. Try the filename recorded in the JSON
    ref = mapping.get('reference_mat_file', '')
    if ref:
        candidate = os.path.join(data_dir, ref)
        if os.path.exists(candidate):
            return candidate

    # 2. Glob for any .mat that isn't a BIDS bold file
    mats = [
        p for p in glob.glob(os.path.join(data_dir, '*.mat'))
        if 'bold' not in os.path.basename(p).lower()
    ]
    if mats:
        if len(mats) > 1:
            print(f"WARNING: multiple .mat files found; using {os.path.basename(mats[0])}")
        return mats[0]

    # 3. Check for .adicht and give an informative error
    adichts = glob.glob(os.path.join(data_dir, '*.adicht'))
    if adichts:
        names = [os.path.basename(p) for p in adichts]
        print(f"ERROR: Found {names} but no .mat file.")
        print("       adi-reader only ships Windows DLLs — .adicht cannot be read on macOS/Linux.")
        print("       Export the file to .mat from LabChart on Windows, then re-run.")
        sys.exit(1)

    print(f"ERROR: No .mat or .adicht file found in {data_dir}")
    sys.exit(1)


# ── Loaders ────────────────────────────────────────────────────────────────────

def load_channels(mat_path):
    """Return dict {name: np.ndarray} for all 4 channels (block1 format)."""
    mat = sio.loadmat(mat_path)

    if 'data_block1' not in mat:
        print("ERROR: 'data_block1' key not found in this .mat file.")
        print("       This file appears to be in the classic format.")
        print("       Use 3_parse.py instead.")
        sys.exit(1)

    data_block = mat['data_block1']   # shape (4, N)
    return {name: data_block[i].flatten()
            for i, name in enumerate(CHANNEL_NAMES)}


def load_mapping(json_path):
    with open(json_path) as f:
        return json.load(f)


# ── series_description → BIDS task name ───────────────────────────────────────
_TASK_PATTERNS = [
    (re.compile(r'REST_ep2d',      re.IGNORECASE), 'rest'),
    (re.compile(r'ContinuousStim', re.IGNORECASE), 'ContinuousStim'),
    (re.compile(r'BlockStim',      re.IGNORECASE), 'BlockStim'),
    (re.compile(r'TOPUP_AP',       re.IGNORECASE), 'AP'),
    (re.compile(r'TOPUP_PA',       re.IGNORECASE), 'PA'),
    (re.compile(r'FreeBreathe',    re.IGNORECASE), 'FreeBreath'),
    (re.compile(r'PaceBreathe',    re.IGNORECASE), 'PaceBreath'),
    (re.compile(r'BEAT_1p6',       re.IGNORECASE), 'BEAT'),
]

def _series_desc_to_task(series_desc):
    for pattern, task in _TASK_PATTERNS:
        if pattern.search(series_desc):
            return task
    return None


def _tr_from_json(fname, data_dir):
    """Read RepetitionTime from the BIDS JSON sidecar for this sequence."""
    if not data_dir:
        return None
    path = os.path.join(data_dir, fname)
    try:
        with open(path) as f:
            return json.load(f).get('RepetitionTime')
    except Exception:
        return None


def load_durations(tsv_path, pseudotime_mapping, data_dir=None):
    """
    Compute duration for each sequence by matching JSON filenames
    (task + run number) to dicominfo rows via series_description.

    Matching rules:
      - series_description prefix → BIDS task name  (see _TASK_PATTERNS)
      - first occurrence of a task in TSV row order → run-01, second → run-02, …

    Duration priority:
      1. dim4 × TR          — when heudiconv correctly populated both fields
      2. reps × TR_ms/1000  — single-slice sequences with explicit name encoding
      3. series_files × TR  — TR read from BIDS JSON sidecar (handles dim4=1 / TR=-1)

    Returns:
        durations : dict  { json_fname -> float }
        unmatched : list  [ (json_fname, reason) ]
    """
    unmatched = []

    if not os.path.exists(tsv_path):
        for fname in pseudotime_mapping:
            unmatched.append((fname, "dicominfo TSV not found"))
        return {}, unmatched

    with open(tsv_path) as f:
        lines = f.readlines()
    header = lines[0].strip().split('\t')

    # Build (task, run_number) → row, using TSV row order for run numbering
    task_run_rows    = {}
    task_run_counter = {}
    for line in lines[1:]:
        parts = line.strip().split('\t')
        if len(parts) < len(header):
            continue
        row  = dict(zip(header, parts))
        task = _series_desc_to_task(row.get('series_description', ''))
        if task is None:
            continue
        run = task_run_counter.get(task, 0) + 1
        task_run_counter[task] = run
        task_run_rows[(task, run)] = row

    def _duration(row, fname):
        dim4 = int(row['dim4'])
        tr   = float(row['TR'])
        name = row.get('series_description', '')

        # 1. heudiconv gave valid dim4 and TR
        if dim4 > 1 and tr > 0:
            return dim4 * tr

        # 2. single-slice sequence with TR and reps encoded in series name
        tr_m  = re.search(r'TR(\d+)ms',  name, re.IGNORECASE)
        rep_m = re.search(r'(\d+)reps',  name, re.IGNORECASE)
        if tr_m and rep_m:
            return int(rep_m.group(1)) * int(tr_m.group(1)) / 1000.0

        # 3. series_files × TR from BIDS JSON sidecar
        try:
            n_vols = int(row.get('series_files', 0))
        except (ValueError, TypeError):
            n_vols = 0
        tr_json = _tr_from_json(fname, data_dir)
        if n_vols > 0 and tr_json and tr_json > 0:
            return n_vols * tr_json

        raise ValueError(
            f"cannot determine duration — "
            f"dim4={dim4}, TR={tr}, series_files={row.get('series_files')}, "
            f"TR_json={tr_json}")

    durations = {}
    for fname in pseudotime_mapping:
        m = re.search(r'task-(\w+)_run-(\d+)', fname)
        if not m:
            unmatched.append((fname, 'cannot parse task/run from filename'))
            continue
        task = m.group(1)
        run  = int(m.group(2))

        row = task_run_rows.get((task, run))
        if row is None:
            unmatched.append((fname,
                f'no dicominfo row for task-{task} run-{run:02d} '
                f'(check _TASK_PATTERNS in script)'))
            continue

        try:
            durations[fname] = _duration(row, fname)
        except Exception as e:
            unmatched.append((fname, f'duration calculation failed: {e}'))

    return durations, unmatched


# ── Segment extractor ─────────────────────────────────────────────────────────

def extract_segment(channels, start_sample, duration_sec):
    """Slice each channel from start_sample for duration_sec seconds."""
    n_samples = int(duration_sec * FS)
    segment = {}
    for name, data in channels.items():
        end = min(start_sample + n_samples, len(data))
        segment[name] = data[start_sample:end]
    return segment


# ── Plot ──────────────────────────────────────────────────────────────────────

def plot_segment(segment, title, plot_path):
    fig, axes = plt.subplots(4, 1, figsize=(14, 8), sharex=True)
    fig.suptitle(title, fontsize=13, fontweight='bold')

    for ax, name in zip(axes, CHANNEL_NAMES):
        sig = segment.get(name, np.array([]))
        t   = np.arange(len(sig)) / FS
        ax.plot(t, sig, linewidth=0.6, color=CHANNEL_COLORS[name])
        ax.set_ylabel(name, fontsize=9, fontweight='bold')
        ax.grid(True, alpha=0.3)

    axes[-1].set_xlabel('Time within segment (s)', fontsize=10)
    plt.tight_layout()
    plt.savefig(plot_path, dpi=120, bbox_inches='tight')
    plt.close(fig)


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Parse the session .mat (block1) into per-sequence segments.")
    ap.add_argument("data_dir",   help="Folder holding pseudotime_mapping.json (step 1 output)")
    ap.add_argument("output_dir", help="Where to write parsed segments")
    ap.add_argument("--json-dir", default=None,
                    help="BIDS func dir with *_bold.json sidecars (default: data_dir)")
    ap.add_argument("--dicominfo", default=None,
                    help="Path to dicominfo_ses-01.tsv from .heudiconv "
                         "(default: <json-dir>/dicominfo_ses-01.tsv)")
    args = ap.parse_args()

    data_dir   = args.data_dir
    output_dir = args.output_dir
    json_dir   = args.json_dir if args.json_dir else data_dir
    json_file  = os.path.join(data_dir, 'pseudotime_mapping.json')
    tsv_file   = args.dicominfo if args.dicominfo \
                 else os.path.join(json_dir, 'dicominfo_ses-01.tsv')
    log_file   = os.path.join(output_dir, 'unmatched_sequences.log')

    os.makedirs(output_dir, exist_ok=True)
    plots_dir = os.path.join(output_dir, 'plots')
    os.makedirs(plots_dir, exist_ok=True)

    print("Loading pseudotime mapping …")
    mapping   = load_mapping(json_file)
    pseud_map = mapping['pseudotime_mapping']

    # mat is found via the absolute reference_mat_file stored by step 1
    mat_file = find_source_file(data_dir, mapping)
    print(f"Loading {os.path.basename(mat_file)} …")
    channels = load_channels(mat_file)
    for name, ch in channels.items():
        print(f"  {name}: {len(ch)} samples ({len(ch)/FS:.1f} s)")
    print()

    print("Matching sequences to dicominfo …")
    durations, unmatched = load_durations(tsv_file, pseud_map, json_dir)

    # Write unmatched log
    if unmatched:
        with open(log_file, 'w') as log:
            log.write("Sequences skipped (no dicominfo match)\n")
            log.write("=" * 60 + "\n")
            for fname, reason in unmatched:
                log.write(f"{fname}\n  reason: {reason}\n\n")
        print(f"  {len(unmatched)} sequence(s) unmatched — see {log_file}")

    matched = {k: v for k, v in pseud_map.items() if k in durations}
    print(f"  {len(matched)} / {len(pseud_map)} sequences matched\n")

    print(f"Parsing {len(matched)} sequences …\n")

    for json_fname, info in sorted(matched.items(), key=lambda x: x[1]['pseudotime_sec']):
        start_sample = info['pseudotime_sample']
        duration_sec = durations[json_fname]

        # Short output stem: extract task-..._run-.. regardless of subject/session prefix
        m = re.search(r'(task-.+_run-\d+)', json_fname)
        stem = m.group(1) if m else json_fname.replace('_bold.json', '')

        print(f"  {stem}")
        print(f"    pseudotime: {info['pseudotime_sec']:.3f} s  |  "
              f"start sample: {start_sample}  |  duration: {duration_sec:.1f} s")

        segment = extract_segment(channels, start_sample, duration_sec)

        # Save .mat
        mat_out   = os.path.join(output_dir, f'{stem}.mat')
        save_dict = {name: seg for name, seg in segment.items()}
        save_dict['pseudotime_sec']    = info['pseudotime_sec']
        save_dict['pseudotime_sample'] = start_sample
        save_dict['duration_sec']      = duration_sec
        save_dict['sampling_rate']     = FS
        sio.savemat(mat_out, save_dict)

        # Save plot
        title     = f"{stem}  |  pseudotime {info['pseudotime_sec']:.1f} s  |  dur {duration_sec:.1f} s"
        plot_path = os.path.join(plots_dir, f'{stem}.png')
        plot_segment(segment, title, plot_path)

        print(f"    saved → {os.path.basename(mat_out)}  +  plots/{os.path.basename(plot_path)}")

    print(f"\nDone. {len(matched)} segments saved to {output_dir}/")
    if unmatched:
        print(f"       {len(unmatched)} skipped — see {log_file}")


if __name__ == '__main__':
    main()
