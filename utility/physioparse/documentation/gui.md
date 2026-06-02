# The Graphical Interface (GUI)

The GUI is a desktop application that lets you run all three pipeline steps without touching the terminal. It provides file pickers for every input, a MAT format selector on each step tab, a live console showing script output, and a status bar at the bottom.

---

## How to launch the GUI

Open a terminal and run:

```bash
bash /path/to/pseudotime/gui/run.sh
```

Pass your conda environment name as an optional argument to use that environment's Python automatically:

```bash
bash /path/to/pseudotime/gui/run.sh Neuroimaging
```

---

## Window layout

```
┌─────────────────────────────────────────────────────────────────┐
│ Pseudotime Pipeline                  Scripts root: [...] [Change]│
│ Conda env: [______] [Apply]  Python executable: [__________] […]│
├─────────────────────────────────────────────────────────────────┤
│ ┌─ Quick Setup ──────────────────────────────────────────────┐  │
│ │ Data folder: [_________________________________] [Browse…]  │  │
│ │ Picks the .mat, pseudotime_mapping.json, and output folder  │  │
│ └────────────────────────────────────────────────────────────┘  │
│                                                                   │
│ ┌─ 1 · Compute Pseudotime ─┐ ┌─ 2 · Plot Quality ─┐ ┌─ 3 ... ┐│
│ │ MAT file format:          │ │                     │ │        ││
│ │ ○ Classic  ● Block1       │ │                     │ │        ││
│ │ [Step 1 form fields]      │ │                     │ │        ││
│ └───────────────────────────┘ └─────────────────────┘ └────────┘│
│                                                                   │
│ ┌─ Console Output ──────────────────────────────────────────────┐│
│ │ (dark terminal area showing script output)                    ││
│ │                                                               ││
│ │                                                         [Clear]│
│ └───────────────────────────────────────────────────────────────┘│
│ Ready                                                             │
└─────────────────────────────────────────────────────────────────┘
```

---

## Each part of the window explained

### Scripts root (top right)

Shows the folder where the pipeline scripts are located. This is detected automatically when the GUI opens — it is the parent folder of the `gui/` folder.

**When to change it:** Only if you have moved the scripts to a different location. Click `Change…` and select the folder that contains the pipeline scripts. The GUI will look for scripts in this folder whenever you press a Run button.

---

### Conda env / Python executable (second row)

Two ways to tell the GUI which Python to use:

- **Conda env** — type your environment name (e.g. `Neuroimaging`) and click **Apply**. The GUI searches common Anaconda/Miniconda install locations and fills in the Python executable path automatically.
- **Python executable** — type or browse to the full path of any Python interpreter directly.

The active Python executable is used for all Step 2 and Step 3 runs, and is passed as an argument to Step 1's bash script.

---

### Quick Setup banner

The most important part of the GUI for first-time use. You only need to set **one path** here — the data folder — and the GUI will automatically fill in all the fields in all three tabs.

**What it auto-fills:**
- The `.mat` file (first non-bold `.mat` found in the folder)
- The `pseudotime_mapping.json` (if it already exists from a previous Step 1 run)
- The output image path for Step 2 (set to `../pseudotime_plot.png` relative to the data folder)
- The output folder for Step 3 (set to `../parsed/` relative to the data folder)

**Typical workflow:** Set this once and then select the MAT format on each tab and click Run in order.

---

### MAT file format selector (on every step tab)

Each step tab has a **MAT file format** panel with two radio buttons:

| Option | Selects script | Use when |
|--------|---------------|----------|
| **Classic** — data / datastart / dataend | `step01_times_acquisition.sh` / `step02_plot_pseudotime_quality.py` / `step03_parse.py` | Your `.mat` file has `data`, `datastart`, `dataend` keys |
| **Block1** — data_block1 (4 × N array) | `step01b_times_acquisition_block1.sh` / `step02b_plot_pseudotime_quality_block1.py` / `step03b_parse_block1.py` | Your `.mat` file has a `data_block1` key |

A hint label next to the radio buttons always shows the exact script name that will run. If you are unsure which format your file uses, open it in MATLAB or Python and check whether `data_block1` exists.

---

### Tab: 1 · Compute Pseudotime

**What runs:** `bash step01_times_acquisition.sh <data_folder> <mat_filename> <python_exe>`  
or `bash step01b_times_acquisition_block1.sh <data_folder> <mat_filename> <python_exe>` (Block1)

| Field | What to put here |
|-------|-----------------|
| MAT file format | Select Classic or Block1 to match your `.mat` file |
| Data folder | The folder containing your `.mat` file and all BIDS `.json` files |
| MAT file | The full path to the physiological `.mat` file |

**When you click Run Step 1:**
1. The button becomes disabled to prevent double-clicking
2. A progress bar starts animating (back and forth — this shows the script is running, not a percentage)
3. The console shows the script's output in real time
4. When finished, the button is re-enabled and the status bar shows "Step 1 complete ✓" or an error

**After Step 1 finishes:** `pseudotime_mapping.json` is saved into your data folder. You can now run Step 2 and Step 3.

---

### Tab: 2 · Plot Quality

**What runs:** `python step02_plot_pseudotime_quality.py <mat_file> <json_file> <output_image>`  
or `python step02b_plot_pseudotime_quality_block1.py ...` (Block1)

| Field | What to put here |
|-------|-----------------|
| MAT file format | Must match the format selected in Step 1 |
| MAT file | Same `.mat` file as Step 1 |
| Pseudotime JSON | The `pseudotime_mapping.json` created by Step 1 |
| Output image | Where to save the plot (`.png` path) |

**Note:** Step 2 can take 1–3 minutes because it loads the entire recording into memory and renders plots with millions of data points. The console will be quiet during loading — this is normal.

**After Step 2 finishes:** Two image files are saved:
- The path you specified → main 5-panel visualization
- Same path with `_stats` added → summary statistics figure

---

### Tab: 3 · Parse Segments

**What runs:** `python step03_parse.py <data_folder> <output_folder>`  
or `python step03b_parse_block1.py ...` (Block1)

| Field | What to put here |
|-------|-----------------|
| MAT file format | Must match the format selected in Step 1 |
| Data folder | Same data folder as Step 1. Must contain `pseudotime_mapping.json` and `dicominfo_ses-01.tsv`. |
| Output folder | Where to save the segment `.mat` files and plots. Created if it doesn't exist. |

**After Step 3 finishes:** The output folder contains one `.mat` and one `.png` per matched sequence, plus a `plots/` subfolder with all PNGs. If any sequences were skipped, `unmatched_sequences.log` explains which ones and why.

---

### Console Output

The dark terminal area at the bottom shows everything the running script prints — the same output you would see if you ran the script manually in a terminal.

**Color coding:**
- Blue text — step headers and divider lines
- Teal/green text — success messages (✓, "done", "saved", "complete")
- Yellow text — warnings (things that were skipped or couldn't be matched)
- Red text — errors (the script failed or something is wrong)
- White/light gray — normal informational output

The console scrolls automatically to always show the latest line. Click **Clear** to erase all previous output.

---

### Status bar (bottom edge)

A one-line summary of the current state:

| Status text | Meaning |
|------------|---------|
| `Ready` | No script has been run yet |
| `Step N running…` | A script is currently executing |
| `Step N complete ✓` | The last script finished successfully |
| `Step N failed (exit 1)` | The script exited with an error — check the console for details |

---

## Complete step-by-step walkthrough

This assumes you are starting from scratch with a new dataset.

### 1. Prepare your data folder

Place these files in a single folder:
- The full physiological `.mat` recording
- All BIDS `.json` sidecar files (one per MRI sequence)
- The `dicominfo_ses-01.tsv` file

### 2. Launch the GUI

```bash
bash /path/to/pseudotime/gui/run.sh Neuroimaging
```

### 3. Set the data folder (Quick Setup)

In the "Quick Setup" banner at the top, click **Browse…** next to "Data folder" and select the folder you prepared. The GUI will automatically find the `.mat` file, check for an existing `pseudotime_mapping.json`, and fill in all three step tabs.

### 4. Run Step 1

Click the **"1 · Compute Pseudotime"** tab. Select the correct **MAT file format** (Classic or Block1). Verify the Data folder and MAT file look correct, then click **▶ Run Step 1**. Watch the console output. The script prints the acquisition times of all sequences, the trigger count, and the computed pseudotimes. It ends with "Pseudotime mapping saved" and the green success message.

### 5. Run Step 2

Click the **"2 · Plot Quality"** tab. Select the same **MAT file format** as Step 1. The path fields should already be filled. Click **▶ Run Step 2**. Wait 1–3 minutes. When done, open the output images and verify that the MRTRIG pulses (green panel) line up with the colored timeline bars.

### 6. Run Step 3

Click the **"3 · Parse Segments"** tab. Select the same **MAT file format** as Step 1. The fields should already be filled. Click **▶ Run Step 3**. The console prints each sequence as it is processed. When done, check the output folder for the `.mat` files and the `plots/` folder for the PNG images.

---

## Technical details (for developers)

The GUI is built with Python's built-in `tkinter` library (no installation required). The code is split into two files:

### `gui/app.py` — the interface

Contains these classes:

| Class | What it is |
|-------|-----------|
| `PathRow` | A reusable widget: label + text entry + Browse button |
| `Console` | The dark scrollable text area with color-coded output |
| `Step1Panel` | The content of the Step 1 tab — includes format selector, dispatches to classic or block1 script |
| `Step2Panel` | The content of the Step 2 tab — includes format selector |
| `Step3Panel` | The content of the Step 3 tab — includes format selector |
| `ConfigBanner` | The Quick Setup banner that auto-fills all three tabs |
| `App` | The main window that assembles everything |

Each step panel holds a `_SCRIPTS` dict mapping `"classic"` and `"block1"` keys to the corresponding script filenames. The `_run()` method picks the active script from the radio button selection.

### `gui/runner.py` — the subprocess engine

Handles running scripts without freezing the GUI. The key problem: if you run a slow script directly on the interface's thread, the window freezes until it finishes. The solution:

1. The script is launched in a **background thread** (a parallel execution track)
2. Output lines are put into a **queue** (a thread-safe message buffer)
3. Every 50 milliseconds, the GUI's main thread checks the queue and prints any new lines to the Console

This way the window stays responsive (you can scroll, resize, etc.) while the script runs.

### `gui/run.sh` — the launcher

Finds the conda environment's Python by looking for the executable at a direct filesystem path (`~/anaconda3/envs/<env>/bin/python`). This is more reliable than `conda activate`, which requires the shell to be specially initialized. Falls back to system `python3` with a warning if the conda env is not found.
