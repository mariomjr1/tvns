# Step 1 — Compute Pseudotime

**Scripts:** `step01_times_acquisition.sh` (Classic) · `step01b_times_acquisition_block1.sh` (Block1)  
**Language:** Bash shell script (embeds a Python script internally)  
**Run time:** A few seconds to under a minute

---

## What it does

Step 1 is the foundation of the entire pipeline. It answers the question: **at what position in the physiological recording did each MRI sequence start?**

It does this in five stages:

1. Reads the `AcquisitionTime` from every BIDS JSON file in the data folder
2. Loads the MRTRIG channel from the `.mat` file and detects every MRI trigger (rising edge)
3. Identifies the first trigger as the anchor point for `task-rest_run-01`
4. Calculates the pseudotime of every other sequence by adding timing offsets
5. Optionally verifies the result against any pre-existing parsed `.mat` files
6. Saves everything to `pseudotime_mapping.json`

---

## Choosing the right script

| Your `.mat` file contains | Use this script |
|--------------------------|----------------|
| `data`, `datastart`, `dataend` | `step01_times_acquisition.sh` (Classic) |
| `data_block1` (4 × N array) | `step01b_times_acquisition_block1.sh` (Block1) |

Select the correct format using the **MAT file format** radio buttons in the GUI before clicking Run. Both scripts produce the same `pseudotime_mapping.json` output. The block1 variant additionally records `"mat_format": "block1"` in the JSON.

---

## Inputs

| Input | Description |
|-------|-------------|
| Data folder | The folder containing the `.mat` file and all BIDS JSON files |
| MAT filename | Just the filename (not the full path), e.g. `subject.mat` |

---

## Outputs

| Output | Location | Description |
|--------|----------|-------------|
| `pseudotime_mapping.json` | Inside the data folder | The timing map for all sequences |
| Terminal output | Console | Detailed log of what was found and computed |

---

## How to run it

### Via the GUI

Open the GUI (`bash gui/run.sh`), go to the **"1 · Compute Pseudotime"** tab, select the **MAT file format**, fill in the Data folder and MAT file, and click **▶ Run Step 1**.

### From the terminal

**Classic format:**
```bash
bash step01_times_acquisition.sh /path/to/data subject.mat
```

**Block1 format:**
```bash
bash step01b_times_acquisition_block1.sh /path/to/data subject.mat
```

The first argument is the full path to the data folder. The second is just the filename of the `.mat` file (not its full path — the script assumes it is inside the data folder).

---

## How it works internally

The shell script is a thin wrapper. Its only jobs are to check that the arguments and files exist, and then write a Python script to a temporary file and run it. All the real logic is in the embedded Python code.

### Stage 1: Read acquisition times

The script scans the data folder for every file ending in `.json` and reads its `AcquisitionTime` field. It converts the time string `HH:MM:SS.ffffff` into total seconds since midnight for easy arithmetic. The result is a dictionary mapping each JSON filename to its acquisition time.

### Stage 2: Load MRTRIG and find triggers

The MRTRIG channel is extracted from the `.mat` file. The extraction method depends on the format:

- **Classic:** `data[datastart[3]-1 : dataend[3]]` — channel 4 (1-indexed)
- **Block1:** `data_block1[3].flatten()` — row 3 of the 2-D array (0-indexed)

The block1 script also prints any event markers stored in `comtick_block1` / `comtext_block1`.

Trigger detection works as follows:
- Compute the signal range (maximum minus minimum)
- Set a threshold at 30% of the range — anything above this is considered a "high" state
- Find every sample where the signal jumps upward by more than the threshold (a rising edge)
- Filter out detections that are less than 100 ms apart (to suppress noise bounces on the same edge)

The result is a list of sample indices where an MRI trigger occurred.

### Stage 3: Anchor to task-rest_run-01

The first trigger in the entire MRTRIG channel marks the start of `task-rest_run-01`. This sample index is the **anchor** — the point where the physiological recording's sample clock and the real-world clock are connected.

```
anchor_sample     = first_trigger_index
anchor_pseudotime = anchor_sample / 1000  (seconds)
anchor_real_time  = AcquisitionTime of task-rest_run-01
```

### Stage 4: Compute pseudotimes

For every other sequence:

```
offset       = sequence_AcquisitionTime - anchor_AcquisitionTime  (seconds)
pseudotime   = anchor_pseudotime + offset  (seconds)
sample_index = round(pseudotime × 1000)
```

This gives the exact sample in the physiological recording where that sequence's first MRI volume started.

### Stage 5: Verification (optional)

If pre-parsed `.mat` files (e.g. `sub-..._task-rest_run-02_bold.mat`) exist in the data folder, Step 1 counts the MRI triggers inside each one and compares that count to the number of triggers it finds in the same time window of the full recording. A matching count confirms the pseudotime is correct. Mismatches are printed as warnings.

The verification logic auto-detects the format of each parcel file, so classic-format parcels can be verified even when using the block1 script.

### Stage 6: Save output

All computed pseudotimes are saved to `pseudotime_mapping.json` in the data folder. This file is the input to Steps 2 and 3.

---

## Reading the terminal output

When Step 1 runs, it prints a detailed log. Here is an annotated example (block1 format output):

```
=== STEP 1: Acquisition Times ===

Found 19 JSON files:

  rest                 run-01 | 14:34:19.827500
  rest                 run-02 | 14:35:51.695000
  AP                   run-01 | 14:43:11.325000
  ...

Anchor: task-rest_run-01  real time = 14:34:19.827500  (52459.828 s)
```
This section confirms all JSON files were found and their times were read correctly.

```
=== STEP 2: MRTRIG — First Trigger (block1 format) ===

Channels (data_block1 — 4 × 4823000 array):
  Ch1 RESP    : 4823000 samples  (4823.0 s)
  Ch2 RPIEZO  : 4823000 samples  (4823.0 s)
  Ch3 STIMTRIG: 4823000 samples  (4823.0 s)
  Ch4 MRTRIG  : 4823000 samples  (4823.0 s)

Event markers (15):
  sample  123456  (  123.46 s)  →  Rest
  sample  456789  (  456.79 s)  →  CONTINUOUS stim
  ...

MRTRIG: 4823000 samples = 4823.0 s
Total triggers detected: 500

First trigger: sample 1217937  →  pseudotime 1217.937 s
```
The block1 variant shows all four channels directly (no `datastart`/`dataend` needed) and lists any event markers exported from LabChart.

```
=== STEP 3: Pseudotime for All Sequences ===

  rest                 run-01 |  1217.937 s  (sample 1217937)  ← ANCHOR
  rest                 run-02 |  1309.805 s  (sample 1309805)
  BlockStim            run-01 |  1999.120 s  (sample 1999120)
  ...
```

```
=== STEP 4: Verification Against Existing Parcels ===

  ✓ rest_run-02      |  45 triggers  pseudotime 1309.805 s
  ✓ BlockStim_run-01 | 200 triggers  pseudotime 1999.120 s
  ...
  ✓ All parcels verified — pseudotime anchor is consistent.
```

---

## What can go wrong

| Problem | Symptom | Cause |
|---------|---------|-------|
| Wrong format selected | "ERROR: 'data_block1' key not found" or missing `datastart` key | Mismatch between selected format and actual `.mat` file |
| No triggers found | "ERROR: No triggers found in MRTRIG channel!" | The MRTRIG channel is flat (no scanner was running, or wrong channel order) |
| No anchor | "ERROR: task-rest_run-01 JSON not found" | The JSON file for the first sequence is missing or named differently |
| Verification mismatch | "✗ parcel: N triggers, big file: M" | The pseudotime is off — check whether the `.mat` file matches the session |
