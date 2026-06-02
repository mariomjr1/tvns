#!/bin/bash

##############################################################################
# Script: 1b_times_acquisition_block1.sh
# Purpose: Same as 1_times_acquisition.sh but for the newer LabChart export
#          format where channels are stored as a 2-D array (data_block1).
#
#          data_block1 shape: (4, N)
#            Row 0: RESP  (Channel 1 - Respiration)
#            Row 1: RPIEZO (Channel 2 - Piezo)
#            Row 2: STIMTRIG (Channel 3 - Stim Trigger)
#            Row 3: MRTRIG (Channel 4 - MRI Trigger)
#
#          ticktimes_block1 provides the time axis (seconds, 1000 Hz).
#          comtick_block1 / comtext_block1 hold labelled event markers.
#
# Usage: bash step01b_times_acquisition_block1.sh <json_dir> <mat_file> <output_dir> [python_exe]
#
#   json_dir    BIDS func directory holding the *_bold.json sidecars,
#               e.g. <sourcedata>/<subject>/ses-01/func
#   mat_file    Full path to the manually-selected LabChart .mat file (block1 format)
#   output_dir  Where pseudotime_mapping.json is written
#   python_exe  Optional Python executable (default: python3)
##############################################################################

if [ $# -lt 3 ]; then
    echo "Usage: $0 <json_dir> <mat_file> <output_dir> [python_exe]"
    echo "Example: $0 /sourcedata/sub-XXXX/ses-01/func /path/to/subject.mat /sourcedata/derivatives/physio/sub-XXXX"
    exit 1
fi

JSON_DIR="$1"
MAT_FILE="$2"
OUTPUT_DIR="$3"
PYTHON_EXE="${4:-python3}"

if [ ! -d "$JSON_DIR" ]; then
    echo "Error: JSON (BIDS func) directory not found: $JSON_DIR"
    exit 1
fi

if [ ! -f "$MAT_FILE" ]; then
    echo "Error: MAT file not found: $MAT_FILE"
    exit 1
fi

mkdir -p "$OUTPUT_DIR"

echo "=========================================="
echo "Pseudotime Mapper — Block1 Format"
echo "=========================================="
echo "JSON dir:   $JSON_DIR"
echo "MAT File:   $MAT_FILE"
echo "Output dir: $OUTPUT_DIR"
echo ""

TEMP_SCRIPT=$(mktemp)

cat > "$TEMP_SCRIPT" << 'PYTHON_SCRIPT'
import json
import os
import sys
import scipy.io as sio
import numpy as np

SAMPLING_RATE = 1000  # Hz

def time_to_seconds(time_str):
    """HH:MM:SS.ffffff → total seconds"""
    try:
        parts = time_str.split(':')
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except:
        return None

def extract_json_times(folder):
    """Read AcquisitionTime from every JSON in the folder."""
    result = {}
    for fname in sorted(os.listdir(folder)):
        if not fname.endswith('.json'):
            continue
        try:
            with open(os.path.join(folder, fname)) as f:
                data = json.load(f)
            acq = data.get('AcquisitionTime')
            if acq:
                result[fname] = {'acq_time': acq, 'acq_seconds': time_to_seconds(acq)}
        except:
            pass
    return result

def extract_mrtrig(mat_data):
    """
    Extract MRTRIG from either .mat format.

    Block1 format: data_block1 is (4, N); row 3 = MRTRIG.
    Classic format fallback: data/datastart/dataend (1-indexed), Ch4 = MRTRIG.
    """
    if 'data_block1' in mat_data:
        return mat_data['data_block1'][3].flatten()
    # Fallback for classic-format parcel files
    data      = mat_data['data'].flatten()
    datastart = mat_data['datastart'].flatten().astype(int)
    dataend   = mat_data['dataend'].flatten().astype(int)
    return data[datastart[3] - 1 : dataend[3]]

def find_trigger_indices(mrtrig, min_gap_sec=0.1):
    """
    Detect rising edges in a TTL-style MRTRIG signal.
    Uses 30 % of signal range as threshold; rejects detections
    closer than min_gap_sec apart to suppress noise.
    """
    sig_range = float(np.max(mrtrig) - np.min(mrtrig))
    if sig_range == 0:
        return np.array([], dtype=int)
    threshold = sig_range * 0.30
    diff = np.diff(mrtrig.astype(float))
    raw = np.where(diff > threshold)[0]
    if len(raw) == 0:
        return raw
    min_gap = int(min_gap_sec * SAMPLING_RATE)
    filtered = [raw[0]]
    for idx in raw[1:]:
        if idx - filtered[-1] >= min_gap:
            filtered.append(idx)
    return np.array(filtered)

def find_parcels(folder):
    """Collect existing parsed bold .mat files."""
    parcels = {}
    for fname in os.listdir(folder):
        if fname.endswith('.mat') and 'bold' in fname and 'task-' in fname:
            task = fname.split('task-')[1].split('_run')[0]
            run  = fname.split('_run-')[1].split('_')[0] if '_run-' in fname else '01'
            parcels[f"{task}_run-{run}"] = fname
    return parcels

def verify_parcel(parcel_path, big_mrtrig, expected_start_sample):
    """
    Compare trigger counts between the parcel's own MRTRIG and the
    corresponding window in the full recording.
    A matching count means the computed pseudotime is correct.
    """
    try:
        pdata    = sio.loadmat(parcel_path)
        p_mrtrig = extract_mrtrig(pdata)
        p_len    = len(p_mrtrig)
        p_trigs  = find_trigger_indices(p_mrtrig)

        buf   = 2 * SAMPLING_RATE
        start = max(0, expected_start_sample - buf)
        end   = min(len(big_mrtrig), start + p_len + 2 * buf)
        b_trigs = find_trigger_indices(big_mrtrig[start:end])

        return {
            'parcel_duration_sec': p_len / SAMPLING_RATE,
            'parcel_triggers':     int(len(p_trigs)),
            'big_file_triggers':   int(len(b_trigs)),
            'match':               len(p_trigs) == len(b_trigs)
        }
    except Exception as e:
        return {'error': str(e)}

# ─────────────────────────────────────────────────────────
def main():
    json_dir   = sys.argv[1]   # BIDS func dir with *_bold.json
    mat_file   = sys.argv[2]   # full path to the .mat (block1)
    output_dir = sys.argv[3]   # where pseudotime_mapping.json is written

    # ── STEP 1: Acquisition times ──────────────────────────
    print("\n=== STEP 1: Acquisition Times ===")
    times = extract_json_times(json_dir)
    sorted_times = sorted(times.items(), key=lambda x: x[1]['acq_seconds'])

    print(f"\nFound {len(times)} JSON files:\n")
    for fname, info in sorted_times:
        task = fname.split('task-')[1].split('_run')[0] if 'task-' in fname else '?'
        run  = fname.split('_run-')[1].split('_')[0]   if '_run-' in fname  else '?'
        print(f"  {task:20s} run-{run} | {info['acq_time']}")

    # ── Anchor: task-rest_run-01 ───────────────────────────
    rest_candidates = [(k, v) for k, v in times.items() if 'task-rest_run-01' in k]
    if not rest_candidates:
        print("\nERROR: task-rest_run-01 JSON not found — cannot anchor pseudotime!")
        sys.exit(1)
    rest_json, rest_info = rest_candidates[0]
    rest_acq_sec = rest_info['acq_seconds']
    print(f"\nAnchor: task-rest_run-01  real time = {rest_info['acq_time']}  ({rest_acq_sec:.3f} s)")

    # ── STEP 2: Load MRTRIG, find first trigger ─────────────
    print("\n=== STEP 2: MRTRIG — First Trigger (block1 format) ===")
    mat_data = sio.loadmat(mat_file)

    if 'data_block1' not in mat_data:
        print("ERROR: 'data_block1' key not found in this .mat file.")
        print("       This file appears to be in the classic format.")
        print("       Use 1_times_acquisition.sh instead.")
        sys.exit(1)

    data_block = mat_data['data_block1']   # shape (4, N)
    n_samples  = data_block.shape[1]

    # Print channel info from titles if available
    ch_names = ['RESP', 'RPIEZO', 'STIMTRIG', 'MRTRIG']
    if 'titles_block1' in mat_data:
        ch_names = [str(t).strip() for t in mat_data['titles_block1'].flatten()]

    print(f"\nChannels (data_block1 — 4 × {n_samples} array):")
    for i, name in enumerate(ch_names):
        print(f"  Ch{i+1} {name:30s}: {n_samples} samples  ({n_samples / SAMPLING_RATE:.1f} s)")

    # Print event markers from comtext if present
    if 'comtick_block1' in mat_data and 'comtext_block1' in mat_data:
        ticks = mat_data['comtick_block1'].flatten()
        texts = mat_data['comtext_block1'].flatten()
        print(f"\nEvent markers ({len(ticks)}):")
        for tick, text in zip(ticks, texts):
            tick = int(tick)
            print(f"  sample {tick:7d}  ({tick / SAMPLING_RATE:8.2f} s)  →  {str(text).strip()}")

    mrtrig = extract_mrtrig(mat_data)
    trigs  = find_trigger_indices(mrtrig)

    print(f"\nMRTRIG: {len(mrtrig)} samples = {len(mrtrig) / SAMPLING_RATE:.1f} s")
    print(f"Total triggers detected: {len(trigs)}")

    if len(trigs) == 0:
        print("ERROR: No triggers found in MRTRIG channel!")
        sys.exit(1)

    first_trig_idx   = int(trigs[0])
    first_trig_ptime = first_trig_idx / SAMPLING_RATE

    print(f"\nFirst trigger: sample {first_trig_idx}  →  physio time {first_trig_ptime:.3f} s")
    print(f"  (anchor: task-rest_run-01, real time {rest_info['acq_time']})")
    print(f"  Pseudotime = physio time; sequences before rest land at < {first_trig_ptime:.1f} s")

    # ── STEP 3: Compute pseudotimes ────────────────────────
    print("\n=== STEP 3: Pseudotime for All Sequences ===\n")
    mapping = {}
    for fname, info in sorted_times:
        offset       = info['acq_seconds'] - rest_acq_sec
        ptime_sec    = first_trig_ptime + offset
        ptime_sample = int(round(ptime_sec * SAMPLING_RATE))

        task = fname.split('task-')[1].split('_run')[0] if 'task-' in fname else '?'
        run  = fname.split('_run-')[1].split('_')[0]   if '_run-' in fname  else '?'
        tag  = " ← ANCHOR" if 'task-rest_run-01' in fname else ""
        print(f"  {task:20s} run-{run} | {ptime_sec:9.3f} s  (sample {ptime_sample:7d}){tag}")

        mapping[fname] = {
            'acq_time':          info['acq_time'],
            'pseudotime_sec':    round(ptime_sec, 6),
            'pseudotime_sample': ptime_sample
        }

    # ── STEP 4: Verify with existing parcels ───────────────
    # Parcels are not present in the BIDS func dir, so this is normally skipped.
    print("\n=== STEP 4: Verification Against Existing Parcels ===\n")
    parcels = find_parcels(json_dir)
    all_ok  = True

    if not parcels:
        print("  No parsed parcels found — skipping verification.\n")
    else:
        for pname, pfile in sorted(parcels.items()):
            task = pname.rsplit('_run-', 1)[0]
            run  = pname.rsplit('_run-', 1)[1]
            jkey = next((k for k in mapping if f'task-{task}_run-{run}' in k), None)

            if jkey not in mapping:
                print(f"  ? {pname:30s} | no matching JSON")
                continue

            exp_sample = mapping[jkey]['pseudotime_sample']
            ptime_sec  = mapping[jkey]['pseudotime_sec']
            res = verify_parcel(os.path.join(json_dir, pfile), mrtrig, exp_sample)

            if 'error' in res:
                print(f"  ✗ {pname:30s} | ERROR: {res['error']}")
                all_ok = False
            elif res['match']:
                print(f"  ✓ {pname:30s} | {res['parcel_triggers']:3d} triggers  pseudotime {ptime_sec:.3f} s")
            else:
                diff = abs(res['parcel_triggers'] - res['big_file_triggers'])
                print(f"  ✗ {pname:30s} | parcel: {res['parcel_triggers']} triggers, "
                      f"big file at expected pos: {res['big_file_triggers']} (off by {diff})")
                all_ok = False

        print()
        if all_ok:
            print("  ✓ All parcels verified — pseudotime anchor is consistent.")
        else:
            print("  ⚠  Some parcels did not verify — review trigger detection or timing.")

    # ── STEP 5: Save output ────────────────────────────────
    # Store the ABSOLUTE .mat path so later steps can locate it.
    output = {
        'reference_mat_file': os.path.abspath(mat_file),
        'mat_format':         'block1',
        'sampling_rate':      SAMPLING_RATE,
        'anchor': {
            'sequence':                     'task-rest_run-01',
            'real_time':                    rest_info['acq_time'],
            'real_time_sec':                rest_acq_sec,
            'first_trigger_sample':         first_trig_idx,
            'first_trigger_pseudotime_sec': first_trig_ptime
        },
        'mrtrig_length_samples': len(mrtrig),
        'mrtrig_duration_sec':   len(mrtrig) / SAMPLING_RATE,
        'total_triggers':        int(len(trigs)),
        'pseudotime_mapping':    mapping
    }

    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, 'pseudotime_mapping.json')
    with open(out_path, 'w') as f:
        json.dump(output, f, indent=2)

    print(f"\n✓ Pseudotime mapping saved to: {out_path}\n")

if __name__ == '__main__':
    main()

PYTHON_SCRIPT

"$PYTHON_EXE" "$TEMP_SCRIPT" "$JSON_DIR" "$MAT_FILE" "$OUTPUT_DIR"

echo "=========================================="
echo "Complete!"
echo "=========================================="
