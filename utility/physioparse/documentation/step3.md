# Step 3 — Parse Segments

**Scripts:** `step03_parse.py` (Classic) · `step03b_parse_block1.py` (Block1)  
**Language:** Python  
**Run time:** Several minutes (loads the full recording, then writes one `.mat` + one `.png` per sequence)

---

## What it does

Step 3 is the final output step. It takes the continuous physiological recording and **cuts it into individual segments**, one per MRI sequence. Each segment contains only the portion of the recording that corresponds to that sequence — from when the first MRI trigger of the sequence occurred until the sequence ended.

For each sequence it produces two output files:

1. A `.mat` file containing the four channel segments
2. A `.png` plot showing the four channels for that segment

---

## Choosing the right script

| Your `.mat` file contains | Use this script |
|--------------------------|----------------|
| `data`, `datastart`, `dataend` | `step03_parse.py` (Classic) |
| `data_block1` (4 × N array) | `step03b_parse_block1.py` (Block1) |

Use the same format you selected for Steps 1 and 2. Select it with the **MAT file format** radio buttons in the GUI before clicking Run.

---

## Inputs

| Input | Where it comes from |
|-------|-------------------|
| Data folder | Specified in the GUI or command line — must contain `pseudotime_mapping.json`, the `.mat` file, and `dicominfo_ses-01.tsv` |
| Output folder | Where to save all results (created automatically if it doesn't exist) |

The script finds the `.mat` file automatically: it first checks the filename stored in `pseudotime_mapping.json` (the `reference_mat_file` field), then scans the data folder for any `.mat` file that doesn't have "bold" in its name.

---

## Outputs

Everything is saved inside the output folder (default: `parsed/`):

```
parsed/
├── task-rest_run-01.mat
├── task-rest_run-02.mat
├── task-AP_run-01.mat
├── task-PA_run-01.mat
├── task-BlockStim_run-01.mat
├── task-ContinuousStim_run-01.mat
├── task-FreeBreath_run-01.mat
│   ... (one .mat per matched sequence)
│
├── plots/
│   ├── task-rest_run-01.png
│   ├── task-rest_run-02.png
│   │   ... (one .png per matched sequence)
│
└── unmatched_sequences.log      ← only created if some sequences had no duration match
```

---

## How to run it

### Via the GUI

Go to the **"3 · Parse Segments"** tab, select the correct **MAT file format**, fill in the Data folder and Output folder fields, and click **▶ Run Step 3**.

### From the terminal

**Classic format:**
```bash
python step03_parse.py /path/to/data /path/to/parsed
```

**Block1 format:**
```bash
python step03b_parse_block1.py /path/to/data /path/to/parsed
```

The first argument is the data folder. The second is the output folder. If the output folder doesn't exist it will be created.

---

## What is inside each output `.mat` file

Each segment `.mat` file contains these variables:

| Variable | Type | Description |
|----------|------|-------------|
| `RESP` | array | Respiration signal for this sequence |
| `RPIEZO` | array | Heart rate signal for this sequence |
| `STIMTRIG` | array | Stimulus trigger signal for this sequence |
| `MRTRIG` | array | MRI trigger signal for this sequence |
| `pseudotime_sec` | float | When this segment starts in the full recording (seconds) |
| `pseudotime_sample` | int | The sample index of the start in the full recording |
| `duration_sec` | float | How many seconds long this segment is |
| `sampling_rate` | int | Always 1000 Hz |

The output format is the same regardless of whether the input was Classic or Block1 — each segment is always saved with the four named channel arrays and the metadata fields above.

To load one of these files in MATLAB or Python:

**MATLAB:**
```matlab
d = load('task-rest_run-01.mat');
plot(d.MRTRIG)
```

**Python:**
```python
import scipy.io as sio
d = sio.loadmat('task-rest_run-01.mat')
import matplotlib.pyplot as plt
plt.plot(d['MRTRIG'].flatten())
```

---

## What each segment plot shows

Each `.png` in `parsed/plots/` has four panels stacked vertically, one per channel:

- **RESP** (blue) — respiration during the sequence
- **RPIEZO** (red) — heartbeat during the sequence
- **STIMTRIG** (orange) — stimulus triggers during the sequence (flat for rest/field maps)
- **MRTRIG** (green) — MRI triggers during the sequence (regular pulses, one per TR)

The horizontal axis is **time within the segment** in seconds (starting at 0, ending at the sequence duration). This is different from pseudotime — it always starts at 0 for each segment.

The title of each plot shows the sequence name, pseudotime start, and duration.

---

## How it works internally

### 1. Load everything

The script loads the full `.mat` recording into memory. The channel extraction differs between variants:

**Classic (`step03_parse.py`):**  
Reads `data`, `datastart`, and `dataend`. Extracts each channel as `data[datastart[i]-1 : dataend[i]]`.

**Block1 (`step03b_parse_block1.py`):**  
Reads `data_block1` (shape 4 × N). Extracts each channel as `data_block1[i].flatten()`.

Both variants produce the same channel dictionary `{name: array}` for all downstream steps.

### 2. Match sequences to durations

For each sequence in the mapping, the script searches `dicominfo_ses-01.tsv` for a row whose `time` field matches the sequence's `acq_time` within 5 seconds. If a match is found, the duration is computed from `dim4 × TR` (or the `TR × reps` formula for single-slice sequences).

**If no match is found:** The sequence is added to the unmatched log and skipped entirely. No `.mat` or `.png` is created for it. The log entry explains why the match failed (e.g., "nearest dicominfo row is 8.3 s away (>5 s threshold)").

### 3. Extract each segment

For a matched sequence:
```
start_sample = pseudotime_sample  (from pseudotime_mapping.json)
end_sample   = start_sample + int(duration_sec × 1000)
segment[channel] = channel_data[start_sample : end_sample]
```

The `min(end_sample, len(channel))` guard prevents an out-of-bounds error if a sequence runs close to the end of the recording.

### 4. Save the `.mat` file

The four channel arrays and the metadata fields are saved to a new `.mat` file using `scipy.io.savemat`.

### 5. Save the plot

A 4-panel matplotlib figure is created, saved to PNG at 120 DPI, and closed immediately to free memory. The figure is never displayed on screen.

### 6. Name the output files

The output filename is derived from the BIDS JSON filename by removing the subject/session prefix and the `_bold.json` suffix:

```
sub-{SubjectID}_ses-{Session}_task-rest_run-01_bold.json
                              ↓
                 task-rest_run-01
```

---

## The unmatched sequences log

If any sequences could not be matched to the dicominfo TSV, a file called `unmatched_sequences.log` is created in the output folder. Example:

```
Sequences skipped (no dicominfo match)
============================================================
sub-{SubjectID}_ses-{Session}_task-FreeBreath_run-01_bold.json
  reason: nearest dicominfo row is 12.4 s away (>5 s threshold)

sub-{SubjectID}_ses-{Session}_task-FreeBreath_run-02_bold.json
  reason: nearest dicominfo row is 12.4 s away (>5 s threshold)
```

**Why sequences might be skipped:**
- The `dicominfo_ses-01.tsv` was generated from a different session
- Some sequences share the same `AcquisitionTime` (e.g., multiple runs acquired simultaneously)
- The tolerance of 5 seconds is too tight for sequences with acquisition time delays

---

## What can go wrong

| Problem | Symptom | Cause |
|---------|---------|-------|
| Wrong format selected | "ERROR: 'data_block1' key not found" or missing `datastart` | Mismatch between selected format and actual `.mat` file |
| All sequences skipped | Every entry in the log, empty `parsed/` folder | `dicominfo_ses-01.tsv` is missing or from a different session |
| `.mat` file not found | "No .mat or .adicht file found" error | The data folder doesn't contain the physiological recording |
| Short or empty segments | Arrays in the output `.mat` are shorter than expected | The sequence's pseudotime is near the end of the recording |
| Slow run | Normal behavior | Loading a large recording into RAM and writing many output files takes time |
