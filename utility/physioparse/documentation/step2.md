# Step 2 — Plot Pseudotime Quality

**Scripts:** `step02_plot_pseudotime_quality.py` (Classic) · `step02b_plot_pseudotime_quality_block1.py` (Block1)  
**Language:** Python  
**Run time:** 1–3 minutes (loading the recording into memory and rendering large plots)

---

## What it does

Step 2 creates two image files that let you **visually verify** that the pseudotime calculation from Step 1 is correct. It plots all four physiological channels across the full recording and overlays colored bars showing when each MRI sequence was running. If the bars line up with the right parts of the signal (e.g., MRTRIG showing pulses exactly within the colored bars), the timing is correct.

---

## Choosing the right script

| Your `.mat` file contains | Use this script |
|--------------------------|----------------|
| `data`, `datastart`, `dataend` | `step02_plot_pseudotime_quality.py` (Classic) |
| `data_block1` (4 × N array) | `step02b_plot_pseudotime_quality_block1.py` (Block1) |

Use the same format you selected for Step 1. Select it with the **MAT file format** radio buttons in the GUI before clicking Run.

---

## Inputs

| Input | Description |
|-------|-------------|
| MAT file | The full physiological recording (e.g., `subject.mat`) |
| JSON mapping | The `pseudotime_mapping.json` created by Step 1 |
| Output image path | Where to save the main plot (e.g., `pseudotime_plot.png`) |

---

## Outputs

| Output | Description |
|--------|-------------|
| `pseudotime_plot.png` | Main visualization — 4 signal panels + timeline |
| `pseudotime_plot_stats.png` | Summary statistics — bar charts and tables |

---

## How to run it

### Via the GUI

Go to the **"2 · Plot Quality"** tab, select the correct **MAT file format**, fill in the three path fields, and click **▶ Run Step 2**.

### From the terminal

**Classic format:**
```bash
python step02_plot_pseudotime_quality.py \
  /path/to/data/subject.mat \
  /path/to/data/pseudotime_mapping.json \
  /path/to/output/pseudotime_plot.png
```

**Block1 format:**
```bash
python step02b_plot_pseudotime_quality_block1.py \
  /path/to/data/subject.mat \
  /path/to/data/pseudotime_mapping.json \
  /path/to/output/pseudotime_plot.png
```

---

## What the main plot shows (`pseudotime_plot.png`)

The main plot has 5 panels stacked vertically, all sharing the same horizontal time axis (seconds into the recording):

### Panel 1 — RESP (blue)

The raw respiration signal. You should see a smooth, regular wave pattern corresponding to the subject's breathing (roughly 0.2–0.4 Hz, or one breath every 2.5–5 seconds). Periods where the subject is holding their breath (during breath-hold tasks) will appear flat.

### Panel 2 — RPIEZO (red)

The heartbeat signal from the piezoelectric sensor. Each pulse corresponds to one heartbeat. At rest (~60–80 bpm) you will see sharp peaks roughly once per second.

### Panel 3 — STIMTRIG (orange)

The stimulus trigger channel. This is flat (zero) during rest and field map sequences, and shows square pulses during the BlockStim and ContinuousStim tasks when stimuli were being presented.

### Panel 4 — MRTRIG (green)

The MRI trigger channel. This shows a square pulse for every volume the scanner acquired. Dense regions of pulses correspond to sequences with a short TR (e.g., TR = 1.5 s means a pulse every 1.5 seconds). The total number of pulses across the recording is printed in the terminal output.

### Panel 5 — Acquisition timeline

A color-coded bar chart showing when each MRI sequence was running. Each sequence type has its own color. The horizontal axis is pseudotime in seconds (starting from the first MRI trigger). Each bar spans from the sequence's pseudotime start to its end (start + duration from dicominfo).

**How to verify correctness:** Look at the MRTRIG panel directly above the timeline. The dense regions of MRI trigger pulses should align exactly with the colored bars. If there is a systematic shift (e.g., every bar is off by 5 seconds), the anchor or timing computation has an error.

---

## What the statistics plot shows (`pseudotime_plot_stats.png`)

This is a 2×2 grid of four summary panels:

### Top-left — Sequences per Task

A bar chart counting how many runs exist for each task type. Useful to confirm that all sequences were found.

### Top-right — Temporal Distribution of Acquisitions

A scatter plot showing the pseudotime of each sequence on the horizontal axis and the sequence index on the vertical axis. This gives a visual sense of how the sequences are distributed across the session.

### Bottom-left — Physiological Channel Durations

A horizontal bar chart showing how many seconds of data exist in each channel. All four channels should have the same duration (since they are recorded simultaneously). Any difference would indicate a problem with the `.mat` file.

### Bottom-right — Pseudotime Statistics text box

A text summary including:
- The anchor real time and pseudotime
- Total number of sequences and task types
- Minimum, maximum, and range of pseudotimes
- Total recording duration
- Sampling rate
- Total triggers detected

---

## How it works internally

### Loading the data

The channel extraction differs between the two script variants:

**Classic (`step02_plot_pseudotime_quality.py`):**  
Reads `data`, `datastart`, and `dataend` from the `.mat` file. Extracts each channel as `data[datastart[i]-1 : dataend[i]]`.

**Block1 (`step02b_plot_pseudotime_quality_block1.py`):**  
Reads `data_block1` (shape 4 × N). Extracts each channel as `data_block1[i].flatten()`. No index arrays needed.

Both variants create a time vector in seconds by dividing each sample index by 1000. All subsequent plotting and statistics code is identical.

### Loading the pseudotime mapping

The `pseudotime_mapping.json` is parsed to get the pseudotime and `acq_time` of each sequence. Sequences are then grouped by task type for color-coded plotting.

### Computing durations

For each sequence, the script finds the matching row in `dicominfo_ses-01.tsv` by comparing acquisition times. It computes the duration using the `dim4 × TR` formula (or the `TR × reps` formula for single-slice sequences). The duration determines the width of each colored bar in the timeline.

### Rendering

All five panels of the main figure are created in a single `matplotlib` figure. The timeline panel uses `matplotlib.patches.Rectangle` to draw the colored bars — one rectangle per sequence, positioned at `(pseudotime_start, y_position)` with width equal to the duration.

---

## What can go wrong

| Problem | What to check |
|---------|--------------|
| Wrong format selected | "ERROR: 'data_block1' key not found" — switch the MAT file format radio button |
| Plot is blank or flat channels | The `.mat` file may not match — verify you selected the right file |
| Timeline bars are all at the same position | `pseudotime_mapping.json` may be from a different session |
| Script is slow | Normal — loading a large recording and rendering millions-of-sample plots takes 1–3 minutes |
