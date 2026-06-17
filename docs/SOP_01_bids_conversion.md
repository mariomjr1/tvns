# SOP 01 — BIDS Conversion (HeuDiConv)

**Script:** `step01_create_bids_v2.sh`
**Scope:** per study
**Runs on:** the cluster (needs the `heudiconv` env + `dcm2niix`)

---

## 1. Purpose
Convert each subject's raw DICOMs into a BIDS NIfTI dataset using HeuDiConv in
**two passes**: Pass 1 generates the conversion codes (dry run), Pass 2 applies
the heuristic to write BIDS NIfTI + JSON sidecars.

## GUI (in the app)
Step 01 is one panel with four tabs: **Pass 1 — Generate codes | Sequences (heuristic) |
Pass 2 — Convert to BIDS | BIDS Validator**.
1. **Pass 1 — Generate codes:** pick All/Specific subject → **Run** (heudiconv `-c none`)
   to detect the sequences (writes `dicominfo*.tsv`).
2. **Sequences (heuristic):** the **embedded Heuristic Builder**. **↻ Scan** → pick the
   subject → it loads the detected sequences and **auto-assigns each to its BIDS target
   from the default rules** (T1w / BlockStim / ContinuousStim / rest / fmap-AP / fmap-PA).
   Review them — **change** a row's Target, **Exclude selected**, or **↺ Auto-fill (default
   rules)** to reset — then **⚙ Generate → 💾 Save** (writes `utility/heuristic/<name>.py`
   + a `.log` of added/excluded) → **Use in Pass 2** (sets the active heuristic).
3. **Pass 2 — Convert to BIDS:** pick the heuristic + subjects → **Run**
   (`step01_create_bids_v2.sh`).
4. **BIDS Validator:** checks the result.
Done when `sourcedata/sub-XXXX/ses-01/{anat,func,fmap}/` is populated.

## 2. Prerequisites
- Step 00 complete: `rawdata/<subj>/DICOM/raw[/_NN]` exists.
- `utility/SubjectList.txt` (raw scanner IDs).
- A heuristic file. Default: `utility/heuristic.py`. Build a project-specific one
  with the **Heuristic builder** in the GUI (assigns Step-01 Pass-1 sequences to
  BIDS targets and writes `utility/heuristic/<name>.py`).
- The heudiconv virtualenv (script sources it from the lab `env/heudiconv`).

## 3. Inputs
| Input | Default |
|-------|---------|
| Subject list (arg 1, optional) | `utility/SubjectList.txt` |
| `raw_path` (edit in script) | `<project>/rawdata` |
| `sourcedata` (edit in script) | `<project>/sourcedata` |
| `heuristic` (edit in script) | `utility/heuristic.py` |

## 4. Run
```bash
bash step01_create_bids_v2.sh
# or:
bash step01_create_bids_v2.sh /path/to/CustomSubjectList.txt
```

## 5. What it does (per subject)
- Collects all raw folders: `DICOM/raw` → **ses-01**; `DICOM/raw_NN` → **ses-NN**.
- **Pass 1** (`-f convertall -c none`): generates conversion codes and
  `.heudiconv/<subj>/info/dicominfo_ses-NN.tsv` (used later by Step 03).
- **Pass 2** (`-f <heuristic> -c dcm2niix -b --overwrite`): writes BIDS NIfTI +
  JSON into `sourcedata/<subj>/ses-NN/`.

## 6. Outputs
```
<project>/sourcedata/
  sub-<ID>/ses-NN/{anat,func,fmap}/*.nii.gz + *.json
  .heudiconv/<rawID>/info/dicominfo_ses-NN.tsv
```

## 7. QC / verification
- Run the **BIDS validator** (also invoked at the top of Step 02):
  `bids-validator <project>/sourcedata`
- Confirm `func/*_bold.nii.gz` + matching `*_bold.json` exist for each task run,
  and fieldmaps are present in `fmap/` (needed for fMRIPrep SDC).
- Use the GUI **sequence viewer** to confirm sequences mapped to the right BIDS
  targets.

## 8. Troubleshooting
| Symptom | Cause / fix |
|---------|-------------|
| `no raw DICOM folder, skipping` | Step 00 didn't produce `raw/` — re-run Step 00. |
| Sequences missing in BIDS | Heuristic didn't match them — rebuild the heuristic (GUI) and re-run Pass 2. |
| Wrong session numbering | Came from `raw_NN` folder names — rename source folders if needed. |
| Validator errors | Fix critical BIDS errors before fMRIPrep / journal submission. |

## 9. Next step
[SOP 02 — Physioparse](SOP_02_physioparse.md)
