# Key Concepts

This page explains the background ideas behind the pipeline. You do not need to understand all of this to run the pipeline, but it helps you interpret the results.

---

## The MRI session and physiological recording

During an MRI session, the subject goes through multiple **sequences** one after another. A sequence is a specific type of scan — for example, a resting-state scan, a visual stimulation task, or a breathing task. Each sequence produces one set of MRI images.

At the same time, a separate recording device (ADInstruments LabChart) records physiological signals from the subject continuously throughout the entire session without stopping between sequences. This creates a single long recording file that spans the whole session.

---

## The four physiological channels

The recording device captures four signals simultaneously. They are stored as four **channels** in the `.mat` file:

| Channel | Name | What it measures |
|---------|------|-----------------|
| 1 | **RESP** | Respiration — the subject's breathing pattern. The signal goes up when the chest expands and down when it contracts. |
| 2 | **RPIEZO** | Heart rate via a piezoelectric sensor placed on the chest or finger. The signal shows a pulse peak with each heartbeat. |
| 3 | **STIMTRIG** | Stimulus trigger — a TTL pulse sent by the stimulus computer when a visual or other stimulus is shown to the subject. Only active during stimulation tasks. |
| 4 | **MRTRIG** | MRI trigger — a TTL pulse sent by the MRI scanner at the beginning of every volume acquisition. This is the most important channel for timing. |

A **TTL pulse** (channels 3 and 4) is a digital on/off signal: the voltage jumps from 0 to a high value at the moment of the event, then drops back to 0. The rising edge (the jump upward) marks the exact moment the trigger occurred.

---

## Pseudotime

The physiological recording is one continuous stream of samples from the moment the device started recording to the moment it stopped. Every sample has a position in this stream, measured either as a **sample number** (e.g., sample 1,217,937) or as a **time in seconds** from the start of the recording (e.g., 1217.937 s at 1000 Hz sampling rate).

However, the MRI scanner uses **clock time** (e.g., 14:34:19) to timestamp when each sequence started, not the physiological recording's internal sample counter. These two time systems are independent and start at different moments.

**Pseudotime** bridges this gap. It is defined as:

> The position of each event within the physiological recording, expressed in seconds, using the **first MRI trigger** as the anchor point (time zero).

### How the anchor works

1. The pipeline finds the very first rising edge in the MRTRIG channel. This is the first volume of the first MRI sequence (always `task-rest_run-01`).
2. That sample number becomes the **anchor** — the point where the physiological clock and the MRI clock are synchronized.
3. The real clock time of `task-rest_run-01` is read from its BIDS JSON file (`AcquisitionTime`).
4. Every other sequence has a known `AcquisitionTime` too. The difference in seconds between that sequence's `AcquisitionTime` and the anchor's `AcquisitionTime` is the **offset**.
5. Adding that offset to the anchor's sample position gives the pseudotime of the other sequence.

### Example

```
Anchor (task-rest_run-01):
  AcquisitionTime  = 14:34:19  →  52,459 seconds since midnight
  First MRTRIG at  sample 1,217,937  →  pseudotime 1217.937 s

task-BlockStim_run-01:
  AcquisitionTime  = 14:47:21  →  53,241 seconds since midnight
  Offset from anchor = 53,241 − 52,459 = 782 seconds
  Pseudotime = 1217.937 + 782 = 1999.937 s
  Sample in recording = 1,999,937
```

This means that if you take sample 1,999,937 from the MRTRIG channel, you should see the first MRI trigger of the BlockStim sequence.

---

## Sampling rate

The recording device samples all four channels at **1000 Hz**, meaning 1000 data points are recorded every second. Therefore:

```
time (seconds) = sample number / 1000
sample number  = time (seconds) × 1000
```

A 10-minute session produces 10 × 60 × 1000 = 600,000 samples per channel.

---

## The `.mat` file formats

The `.mat` file is a MATLAB data file. LabChart can export it in two different layouts depending on the software version.

### Classic format

The four channels are concatenated into a single long array called `data`. Two index arrays (`datastart`, `dataend`) describe where each channel begins and ends:

```
data[datastart[0]-1 : dataend[0]]  →  RESP channel (MATLAB uses 1-based indexing)
data[datastart[1]-1 : dataend[1]]  →  RPIEZO channel
data[datastart[2]-1 : dataend[2]]  →  STIMTRIG channel
data[datastart[3]-1 : dataend[3]]  →  MRTRIG channel
```

Python uses 0-based indexing, so 1 is subtracted from the MATLAB start indices.

### Block1 format

The four channels are stored as a single 2-D array called `data_block1` with shape **(4, N)**, where N is the number of samples. Each row is a complete channel:

```
data_block1[0]  →  RESP channel
data_block1[1]  →  RPIEZO channel
data_block1[2]  →  STIMTRIG channel
data_block1[3]  →  MRTRIG channel
```

Additional variables present in block1 files:

| Variable | Description |
|----------|-------------|
| `ticktimes_block1` | Time axis in seconds at 1000 Hz |
| `titles_block1` | Channel name strings |
| `comtick_block1` | Sample indices of labelled event markers |
| `comtext_block1` | Text labels for each event marker |

The pipeline scripts detect which format is present and process accordingly. The GUI provides a **MAT file format** radio selector on each step tab so you can choose the correct variant for your file.

---

## BIDS JSON sidecar files

BIDS (Brain Imaging Data Structure) is a standard way of organizing neuroimaging data. When an MRI sequence is converted from DICOM to NIfTI format, a companion `.json` file is created alongside it. This file contains metadata about the scan, including:

```json
{
  "AcquisitionTime": "14:34:19.827500",
  "RepetitionTime": 1.5,
  "EchoTime": 0.03,
  ...
}
```

The `AcquisitionTime` field is what this pipeline uses to know when each sequence started. The format is `HH:MM:SS.ffffff` (hours:minutes:seconds.microseconds).

---

## dicominfo TSV file

The `dicominfo_ses-01.tsv` file is a tab-separated table generated from the DICOM headers of all scans in the session. Each row represents one scan series and contains information like the number of volumes (`dim4`), repetition time (`TR`), and acquisition time.

The pipeline uses this file to calculate **how long each sequence lasted**:
- For multi-volume BOLD sequences: `duration = number_of_volumes × TR`
- For single-slice sequences with a name like `TR1210ms_250reps`: `duration = 250 × 1.210 s`

This duration tells the pipeline where to stop cutting when it extracts a segment from the continuous recording.
