# SOP 00 — Download raw DICOMs

**Script:** `step00_unpack_V2.sh`
**Scope:** per study (all subjects in the list)
**Runs on:** the Linux workstation (needs `findsession`, `screen`, `rsync`)

---

## 1. Purpose
Locate each subject's DICOM archive with `findsession` and `rsync`-copy it to the
project `rawdata/` tree. One `screen` session is spawned per subject so downloads
run in parallel and survive disconnects.

## 2. Prerequisites
- Logged into the workstation that has `findsession` on PATH and archive access.
- `utility/SubjectList.txt` populated with **raw scanner IDs**, one per line
  (e.g. `7T1019HC_042726`).
- Write access to the output path (`rawdata/`).

## 3. Inputs
| Input | Default |
|-------|---------|
| Subject list (arg 1, optional) | `utility/SubjectList.txt` |
| Output path (`out_path`, edit in script) | `<project>/rawdata` |

## 4. Run
```bash
bash step00_unpack_V2.sh
# or with a custom list:
bash step00_unpack_V2.sh /path/to/CustomSubjectList.txt
```

## 5. What it does (per subject)
- Skips the subject if `…/DICOM/LOG/step0_DONE.txt` already exists.
- Runs `findsession <subj>`; saves output to `…/DICOM/LOG/findsession.txt`.
- For **each** PATH returned (a subject may have several sessions):
  - 1 path → `…/DICOM/raw/`; multiple paths → `…/DICOM/raw_01/`, `raw_02/`, …
  - Skips paths with no read access (does not abort the run).
  - `rsync -av --progress`, logged to `…/DICOM/LOG/rsync_NN.log`.
- Writes `step0_DONE.txt` if ≥1 session copied, else `step0_ERROR.txt`.

## 6. Monitor
```bash
screen -ls                         # list running download sessions
screen -r <subjID>-download        # attach to one
```

## 7. Outputs
```
<project>/rawdata/<subjID>/DICOM/
  raw/  (or raw_01/, raw_02/, …)
  LOG/findsession.txt, rsync_NN.log, step0_DONE.txt | step0_ERROR.txt
```

## 8. QC / verification
- Every subject has a `step0_DONE.txt` and a non-trivial `raw/` folder.
- Investigate any `step0_ERROR.txt` (no access / no path returned by findsession).

## 9. Troubleshooting
| Symptom | Cause / fix |
|---------|-------------|
| `[SKIP] no DICOM path / no access` | `findsession` returned nothing or you lack archive access — check `findsession.txt`. |
| Subject silently skipped | `step0_DONE.txt` already present — delete it to re-download. |
| `rsync failed` | Source unreadable or disk full — see `rsync_NN.log`. |

## 10. Next step
[SOP 01 — BIDS Conversion](SOP_01_bids_conversion.md)
