# The Data Folder

The `data/` folder is where all input files for the pipeline must be placed. This page describes every file, what it contains, and which pipeline steps need it.

---

## Folder overview

```
data/
├── subject.mat                                          ← REQUIRED before Step 1
├── sub-..._ses-01_task-rest_run-01_bold.json            ← REQUIRED before Step 1
├── sub-..._ses-01_task-rest_run-02_bold.json            ← REQUIRED before Step 1
├── sub-..._ses-01_task-AP_run-01_bold.json              ← REQUIRED before Step 1
│   ... (one .json per sequence)
├── dicominfo_ses-01.tsv                                 ← needed for Step 3 durations
├── pseudotime_mapping.json                              ← created by Step 1
├── preproc_preprocessing.m                              ← reference MATLAB script (informational)
└── sub-..._task-..._bold.mat                            ← optional, used for verification in Step 1
```

---

## File-by-file reference

### `subject.mat` — the full physiological recording

**Required:** Yes, before Step 1  
**Created by:** ADInstruments LabChart, exported to MATLAB format  
**Used by:** Step 1, Step 2, Step 3

This is the main data file. It contains the complete physiological recording of the entire MRI session as a MATLAB `.mat` file. LabChart can produce this file in two formats — **Classic** or **Block1** — depending on the software version. The pipeline supports both; choose the matching format in the GUI before running each step.

#### Classic format

| Variable | Description |
|----------|-------------|
| `data` | A single long array containing all four channels concatenated end-to-end |
| `datastart` | Four integers telling where each channel starts inside `data` (1-indexed, MATLAB convention) |
| `dataend` | Four integers telling where each channel ends inside `data` |

#### Block1 format

| Variable | Description |
|----------|-------------|
| `data_block1` | A 2-D array of shape (4, N) — each row is one complete channel |
| `ticktimes_block1` | Time axis in seconds at 1000 Hz |
| `titles_block1` | Channel name strings |
| `comtick_block1` | Sample indices of labelled event markers |
| `comtext_block1` | Text labels for each event marker |

The four channels are stored in this fixed order in both formats:

| Index / Row | Channel | Signal |
|-------------|---------|--------|
| 0 (row) / 1 (MATLAB) | RESP | Respiration |
| 1 / 2 | RPIEZO | Heartbeat (piezoelectric) |
| 2 / 3 | STIMTRIG | Stimulus trigger TTL pulses |
| 3 / 4 | MRTRIG | MRI scanner trigger TTL pulses |

The file is large because it stores raw floating-point samples at 1000 Hz for the duration of the entire session.

**How to get it:** Export from ADInstruments LabChart using File → Export → MATLAB.

> Note: An `.adicht` file (the native LabChart format) may also be present in the folder. The pipeline cannot read `.adicht` directly on macOS because the reading library (`adi-reader`) only provides Windows DLLs. If you only have an `.adicht` file, open LabChart on a Windows machine and export to `.mat`.

---

### `sub-..._ses-01_task-..._run-..._bold.json` — BIDS sidecar files

**Required:** Yes, before Step 1 (one per MRI sequence)  
**Created by:** BIDS conversion tool (e.g., dcm2bids)  
**Used by:** Step 1 only

These are small text files in JSON format. The filename follows the BIDS naming convention:

```
sub-{SubjectID}_ses-{Session}_task-{TaskName}_run-{RunNumber}_bold.json
```

Example filenames from the dataset:
```
sub-{SubjectID}_ses-{Session}_task-rest_run-01_bold.json
sub-{SubjectID}_ses-{Session}_task-BlockStim_run-01_bold.json
sub-{SubjectID}_ses-{Session}_task-FreeBreath_run-03_bold.json
```

The pipeline only reads one field from each file:

```json
{
  "AcquisitionTime": "14:34:19.827500"
}
```

This is the clock time at which the scanner started acquiring that sequence. The format is `HH:MM:SS.ffffff`.

**How many files:** One per sequence. In the example dataset there are 19 sequences (19 JSON files).

**The sequences in the example dataset:**

| Task | Runs |
|------|------|
| rest | 01, 02 |
| AP (anterior-posterior field map) | 01 |
| PA (posterior-anterior field map) | 01 |
| BlockStim | 01 |
| ContinuousStim | 01 |
| FreeBreath | 01–09 |
| PaceBreath | 01–03 |

---

### `dicominfo_ses-01.tsv` — DICOM scan information table

**Required:** Strongly recommended for Step 3 (without it, all sequences are skipped)  
**Created by:** A DICOM inspection tool  
**Used by:** Step 2 (for sequence durations in the timeline), Step 3 (for cutting segment lengths)

This is a tab-separated table where each row describes one DICOM series from the session. The pipeline reads these columns:

| Column | Description |
|--------|-------------|
| `time` | Acquisition start time in `HHMMSS.ffffff` format (no colons) |
| `dim4` | Number of volumes in the sequence |
| `TR` | Repetition time in seconds |
| `dim3` | Number of slices |
| `series_description` | Free-text name of the scan protocol |

**How the pipeline uses it:**

For each sequence in `pseudotime_mapping.json`, the pipeline finds the matching row in this table by comparing `AcquisitionTime` (from the JSON) to the `time` column (within a 5-second tolerance). Then it computes the duration:

- **Multi-volume BOLD sequences** (`dim4 > 1`): `duration = dim4 × TR`  
  Example: 200 volumes × 1.5 s TR = 300 s

- **Single-slice sequences** with a descriptive name like `TR1210ms_250reps`:  
  `duration = 250 reps × 1.210 s = 302.5 s`  
  The `dim4 × TR` formula doesn't work for these because the DICOM TR reflects the per-slice readout time, not the volume period.

**What happens if a sequence is not found:** That sequence is skipped and its name is written to `parsed/unmatched_sequences.log`. It will not produce a `.mat` or plot output.

---

### `pseudotime_mapping.json` — timing map

**Required:** Before Steps 2 and 3  
**Created by:** Step 1 (`step01_times_acquisition.sh` or `step01b_times_acquisition_block1.sh`)  
**Used by:** Step 2, Step 3

This file is the output of Step 1 and the input to Steps 2 and 3. It is automatically saved to the data folder when Step 1 finishes.

Example structure:

```json
{
  "reference_mat_file": "subject.mat",
  "mat_format": "block1",
  "sampling_rate": 1000,
  "anchor": {
    "sequence": "task-rest_run-01",
    "real_time": "14:34:19.827500",
    "real_time_sec": 52459.8275,
    "first_trigger_sample": 1217937,
    "first_trigger_pseudotime_sec": 1217.937
  },
  "mrtrig_length_samples": 5829650,
  "mrtrig_duration_sec": 5829.65,
  "total_triggers": 500,
  "pseudotime_mapping": {
    "sub-{SubjectID}_ses-{Session}_task-rest_run-01_bold.json": {
      "acq_time": "14:34:19.827500",
      "pseudotime_sec": 1217.937,
      "pseudotime_sample": 1217937
    },
    "sub-{SubjectID}_ses-{Session}_task-BlockStim_run-01_bold.json": {
      "acq_time": "14:47:21.010000",
      "pseudotime_sec": 1999.1195,
      "pseudotime_sample": 1999120
    }
  }
}
```

**Field explanations:**

| Field | Meaning |
|-------|---------|
| `reference_mat_file` | The `.mat` filename that was used as the source |
| `mat_format` | `"classic"` or `"block1"` — recorded by the block1 script; absent in classic-format output |
| `sampling_rate` | Always 1000 Hz |
| `anchor.sequence` | Always `task-rest_run-01` — the first BOLD sequence |
| `anchor.first_trigger_sample` | The sample index of the very first MRI trigger in the MRTRIG channel |
| `anchor.first_trigger_pseudotime_sec` | Same value divided by 1000 |
| `mrtrig_length_samples` | Total length of the MRTRIG channel |
| `total_triggers` | Number of MRI triggers detected across the whole recording |
| `pseudotime_mapping` | One entry per JSON file found in the data folder |
| `pseudotime_sec` | Where this sequence starts in the recording, in seconds |
| `pseudotime_sample` | Same value multiplied by 1000 (the actual array index to use) |

---

### `sub-..._task-..._bold.mat` — pre-parsed segment files (optional)

**Required:** No  
**Created by:** A previous processing step (not this pipeline)  
**Used by:** Step 1 only, for cross-verification

If these files are present, Step 1 will compare the number of MRI triggers inside each pre-existing segment against what the pipeline would predict based on the pseudotime it calculated. A matching trigger count confirms the timing is correct. A mismatch is printed as a warning.

These files are not required — Step 1 works fine without them.

---

### `preproc_preprocessing.m` — reference MATLAB script (informational)

**Required:** No  
**Created by:** The research team  
**Used by:** Not used by the pipeline at all

This is the original MATLAB preprocessing script that defined how the four channels are ordered inside the `.mat` file. It is kept in the data folder as documentation of the channel order. The Python pipeline uses the same channel ordering:

```matlab
RESP     = data(datastart(1):dataend(1));
RPIEZO   = data(datastart(2):dataend(2));
STIMTRIG = data(datastart(3):dataend(3));
MRTRIG   = data(datastart(4):dataend(4));
```
