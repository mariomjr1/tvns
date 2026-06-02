#!/usr/bin/env python3
"""
Script: 2_plot_pseudotime_quality.py
Purpose: Visualize the original .mat file with pseudotime acquisition periods labeled

Creates a plot showing:
1. The physiological signals from the original .mat file
2. A timeline bar showing when each sequence was acquired
3. Colored regions for each sequence type
"""

import json
import re
import sys
import os
import scipy.io as sio
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle
from datetime import datetime

from _common import _TASK_PATTERNS, _series_desc_to_task, _tr_from_json

def load_mat_data(mat_path, sampling_rate=1000):
    """Load physiological data from .mat file"""
    try:
        mat_data = sio.loadmat(mat_path)

        if 'data' in mat_data and 'datastart' in mat_data and 'dataend' in mat_data:
            data = mat_data['data'].flatten()
            datastart = mat_data['datastart'].flatten().astype(int)
            dataend = mat_data['dataend'].flatten().astype(int)

            # Extract all 4 channels
            channels = {}
            channel_names = ['RESP', 'RPIEZO', 'STIMTRIG', 'MRTRIG']

            for i, name in enumerate(channel_names):
                if i < len(datastart) and i < len(dataend):
                    channels[name] = data[datastart[i]-1:dataend[i]]  # MATLAB 1-indexed

            # Time vector in seconds
            max_length = max(len(ch) for ch in channels.values())
            time_vector = np.arange(max_length) / sampling_rate

            return channels, time_vector, mat_data
        else:
            print("ERROR: Required data not found in .mat file")
            return None, None, None

    except Exception as e:
        print(f"ERROR loading .mat file: {e}")
        return None, None, None

def load_pseudotime_mapping(json_path):
    """Load pseudotime mapping from JSON"""
    try:
        with open(json_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        print(f"ERROR loading pseudotime mapping: {e}")
        return None

def load_dicominfo_durations(dicominfo_path, pseudotime_mapping, data_dir=None):
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

    Returns dict: json_file -> duration_sec
    """
    if not os.path.exists(dicominfo_path):
        print(f"WARNING: dicominfo not found at {dicominfo_path} — using 120s fallback")
        return {k: 120.0 for k in pseudotime_mapping}

    with open(dicominfo_path) as f:
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
            durations[fname] = 120.0
            continue
        task = m.group(1)
        run  = int(m.group(2))
        row  = task_run_rows.get((task, run))
        if row is None:
            print(f"  WARNING: no dicominfo row for task-{task} run-{run:02d} — using 120s fallback")
            durations[fname] = 120.0
        else:
            try:
                durations[fname] = _duration(row, fname)
            except Exception as e:
                print(f"  WARNING: duration failed for {fname}: {e} — using 120s fallback")
                durations[fname] = 120.0

    return durations

def group_sequences(pseudotime_mapping):
    """Group sequences by task type and get start/end times"""
    sequences = {}

    for json_file, timing_info in pseudotime_mapping['pseudotime_mapping'].items():
        # Extract task name
        if 'task-' in json_file:
            task = json_file.split('task-')[1].split('_run')[0]
            run = json_file.split('_run-')[1].split('_')[0] if '_run-' in json_file else '01'

            if task not in sequences:
                sequences[task] = []

            sequences[task].append({
                'run': run,
                'pseudotime': timing_info['pseudotime_sec'],
                'acq_time': timing_info['acq_time'],
                'json': json_file
            })

    # Sort by pseudotime within each task
    for task in sequences:
        sequences[task].sort(key=lambda x: x['pseudotime'])

    return sequences

def create_visualization(mat_path, json_path, output_path,
                         json_dir=None, dicominfo_path=None):
    """Create the visualization.

    json_dir       directory holding the *_bold.json sidecars (BIDS func dir).
                   Defaults to the folder containing pseudotime_mapping.json.
    dicominfo_path explicit path to dicominfo_ses-01.tsv (e.g. from .heudiconv).
                   Defaults to <json_dir>/dicominfo_ses-01.tsv.
    """

    print("Loading data...")
    channels, time_vector, mat_info = load_mat_data(mat_path)
    if channels is None:
        return False

    mapping = load_pseudotime_mapping(json_path)
    if mapping is None:
        return False

    sequences = group_sequences(mapping)
    # JSON sidecars come from the BIDS func dir; dicominfo from .heudiconv.
    if json_dir is None:
        json_dir = os.path.dirname(os.path.abspath(json_path))
    if dicominfo_path is None:
        dicominfo_path = os.path.join(json_dir, 'dicominfo_ses-01.tsv')
    durations = load_dicominfo_durations(dicominfo_path, mapping['pseudotime_mapping'], json_dir)

    rec_dur = len(time_vector) / 1000
    n_tasks = len(sequences)
    print(f"\nLoaded {len(channels)} physiological channels")
    print(f"Total recording duration: {rec_dur/60:.1f} minutes ({rec_dur:.0f} s)")
    print(f"Found {sum(len(runs) for runs in sequences.values())} sequences across {n_tasks} tasks")

    colors = {
        'rest':           '#1f77b4',
        'BlockStim':      '#ff7f0e',
        'ContinuousStim': '#2ca02c',
        'AP':             '#d62728',
        'PA':             '#9467bd',
        'FreeBreath':     '#8c564b',
        'PaceBreath':     '#e377c2',
        'BEAT':           '#bcbd22',
    }

    # All sequences sorted by pseudotime (used for vertical onset lines)
    all_seqs = sorted(
        [(task, seq) for task, runs in sequences.items() for seq in runs],
        key=lambda x: x[1]['pseudotime']
    )

    # All 5 panels share the same x-axis so signal and timeline are always aligned
    n_tl_rows = max(2, n_tasks)
    fig, axes = plt.subplots(
        5, 1,
        figsize=(max(30, int(rec_dur / 40)), 12 + n_tl_rows),
        sharex=True,
        gridspec_kw={'height_ratios': [2, 2, 1, 2, n_tl_rows]}
    )
    ax_resp, ax_piezo, ax_stim, ax_mr, ax_tl = axes
    fig.suptitle('Physiological Recording — Acquisition Timeline', fontsize=13, fontweight='bold')

    print("\nPlotting physiological signals...")

    # ── Signal panels ──────────────────────────────────────────────────────────
    sig_cfg = [
        (ax_resp,  'RESP',    'blue'),
        (ax_piezo, 'RPIEZO',  'red'),
        (ax_stim,  'STIMTRIG','orange'),
        (ax_mr,    'MRTRIG',  'green'),
    ]
    for ax, name, col in sig_cfg:
        if name in channels:
            sig = channels[name]
            t   = np.arange(len(sig)) / 1000
            ax.plot(t, sig, linewidth=0.5, color=col, alpha=0.8)
        ax.set_ylabel(name, fontsize=9, fontweight='bold')
        ax.grid(True, alpha=0.2)
        # Vertical dashed line at each sequence onset — same x-coords as the timeline bars
        for task, seq in all_seqs:
            ax.axvline(seq['pseudotime'], color=colors.get(task, '#888'),
                       linewidth=0.8, alpha=0.5, linestyle='--')

    ax_resp.set_title('Physiological Recording — Sequence Onsets', fontsize=12, fontweight='bold')

    # Sequence labels on the MRTRIG panel only (avoids clutter)
    if 'MRTRIG' in channels:
        mr_sig   = channels['MRTRIG']
        label_y  = float(np.max(mr_sig)) * 0.92
        for task, seq in all_seqs:
            ax_mr.text(seq['pseudotime'] + 1, label_y,
                       f"{task} r{seq['run']}", fontsize=6,
                       rotation=90, va='top', ha='left',
                       color=colors.get(task, '#333'), clip_on=True)

    # ── Timeline panel ─────────────────────────────────────────────────────────
    print("Plotting acquisition timeline...")
    y_pos = 0
    task_positions = {}
    for task in sorted(sequences.keys()):
        task_positions[task] = y_pos
        c = colors.get(task, '#999999')
        for seq in sequences[task]:
            dur   = durations.get(seq['json'], 120)
            start = seq['pseudotime']
            rect  = Rectangle((start, y_pos - 0.4), dur, 0.8,
                               linewidth=1, edgecolor='black', facecolor=c, alpha=0.75)
            ax_tl.add_patch(rect)
            if dur > 15:
                ax_tl.text(start + dur / 2, y_pos, f"r{seq['run']}",
                           ha='center', va='center', fontsize=7, fontweight='bold')
        y_pos += 1

    ax_tl.set_ylim(-0.6, y_pos + 0.1)
    ax_tl.set_xlabel('Physio recording time (s)', fontsize=11, fontweight='bold')
    ax_tl.set_yticks(list(task_positions.values()))
    ax_tl.set_yticklabels(list(task_positions.keys()), fontsize=9)
    ax_tl.grid(True, alpha=0.2, axis='x')

    # Shared x-axis range = full physio recording (set once, propagates to all panels)
    ax_resp.set_xlim(0, rec_dur)

    plt.tight_layout()

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    print(f"\nSaving visualization to: {output_path}")
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print("✓ Visualization saved!")

    # Create a summary statistics figure
    fig2, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig2.suptitle('Acquisition Summary Statistics', fontsize=14, fontweight='bold')

    # Plot 1: Sequence count by task
    ax = axes[0, 0]
    task_counts = {task: len(runs) for task, runs in sequences.items()}
    tasks = list(task_counts.keys())
    counts = list(task_counts.values())
    colors_list = [colors.get(task, '#999999') for task in tasks]
    ax.bar(tasks, counts, color=colors_list, alpha=0.7, edgecolor='black')
    ax.set_ylabel('Number of Runs', fontsize=10, fontweight='bold')
    ax.set_title('Sequences per Task', fontsize=11, fontweight='bold')
    ax.grid(True, alpha=0.3, axis='y')
    for i, (task, count) in enumerate(zip(tasks, counts)):
        ax.text(i, count + 0.1, str(count), ha='center', fontweight='bold')

    # Plot 2: Timeline distribution
    ax = axes[0, 1]
    pseudo_times = []
    pseudo_labels = []
    for task in sorted(sequences.keys()):
        for seq in sequences[task]:
            pseudo_times.append(seq['pseudotime'])
            pseudo_labels.append(f"{task}\n(run-{seq['run']})")

    colors_timeline = [colors.get(label.split('\n')[0], '#999999') for label in pseudo_labels]
    ax.scatter(pseudo_times, range(len(pseudo_times)), c=colors_timeline, s=100, alpha=0.7, edgecolor='black')
    ax.set_xlabel('Pseudotime (seconds)', fontsize=10, fontweight='bold')
    ax.set_ylabel('Sequence Index', fontsize=10, fontweight='bold')
    ax.set_title('Temporal Distribution of Acquisitions', fontsize=11, fontweight='bold')
    ax.grid(True, alpha=0.3)

    # Plot 3: Signal overview (raw counts)
    ax = axes[1, 0]
    channel_lengths = [(name, len(ch)) for name, ch in channels.items()]
    ch_names = [name for name, _ in channel_lengths]
    ch_lengths = [length/1000 for _, length in channel_lengths]  # Convert to seconds
    ax.barh(ch_names, ch_lengths, color=['blue', 'red', 'orange', 'green'], alpha=0.7, edgecolor='black')
    ax.set_xlabel('Duration (seconds)', fontsize=10, fontweight='bold')
    ax.set_title('Physiological Channel Durations', fontsize=11, fontweight='bold')
    ax.grid(True, alpha=0.3, axis='x')
    for i, (name, duration) in enumerate(zip(ch_names, ch_lengths)):
        ax.text(duration + 50, i, f'{duration:.0f}s', va='center', fontweight='bold')

    # Plot 4: Pseudotime statistics
    ax = axes[1, 1]
    ax.axis('off')

    anchor       = mapping.get('anchor', {})
    anchor_time  = anchor.get('real_time', 'N/A')
    anchor_ptime = anchor.get('first_trigger_pseudotime_sec', 'N/A')
    n_triggers   = mapping.get('total_triggers', 'N/A')

    stats_text = f"""
    PSEUDOTIME STATISTICS

    Anchor: task-rest_run-01
      Real time:      {anchor_time}
      Pseudotime:     {f"{anchor_ptime:.3f} s" if isinstance(anchor_ptime, (int, float)) else anchor_ptime}

    Total Sequences: {len(pseudo_times)}
    Total Tasks: {len(sequences)}

    Pseudotime Range:
      Min: {min(pseudo_times):.1f} s
      Max: {max(pseudo_times):.1f} s
      Range: {max(pseudo_times) - min(pseudo_times):.1f} s

    Physiological Recording:
      Duration: {len(time_vector)/60:.1f} minutes
      Sampling Rate: 1000 Hz
      Total Triggers: {n_triggers}
    """

    ax.text(0.1, 0.9, stats_text, transform=ax.transAxes, fontsize=11,
            verticalalignment='top', fontfamily='monospace',
            bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))

    plt.tight_layout()

    # Save statistics figure
    stats_path = output_path.replace('.png', '_stats.png')
    print(f"Saving statistics to: {stats_path}")
    plt.savefig(stats_path, dpi=150, bbox_inches='tight')
    print("✓ Statistics figure saved!")

    return True

def main():
    import argparse
    ap = argparse.ArgumentParser(
        description="Plot physiological recording with acquisition timeline.")
    ap.add_argument("mat_file",    help="Full path to the session .mat file")
    ap.add_argument("mapping_json", help="pseudotime_mapping.json from step 1")
    ap.add_argument("output_png",  help="Output figure path")
    ap.add_argument("--json-dir", default=None,
                    help="BIDS func dir with *_bold.json sidecars "
                         "(default: folder of mapping_json)")
    ap.add_argument("--dicominfo", default=None,
                    help="Path to dicominfo_ses-01.tsv "
                         "(default: <json-dir>/dicominfo_ses-01.tsv)")
    args = ap.parse_args()

    if not os.path.exists(args.mat_file):
        print(f"ERROR: MAT file not found: {args.mat_file}")
        sys.exit(1)
    if not os.path.exists(args.mapping_json):
        print(f"ERROR: JSON mapping not found: {args.mapping_json}")
        sys.exit(1)

    print("="*60)
    print("Pseudotime Quality Visualization")
    print("="*60)

    success = create_visualization(
        args.mat_file, args.mapping_json, args.output_png,
        json_dir=args.json_dir, dicominfo_path=args.dicominfo)

    if success:
        print("\n" + "="*60)
        print("✓ Visualization complete!")
        print("="*60)
    else:
        sys.exit(1)

if __name__ == '__main__':
    main()