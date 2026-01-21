#!/usr/bin/env python3
# Each file has a code 7TNNNNLL_YYYYMMDD where N are digits and L are letters. And each file has a type inside the name (Rest, Block or Cont).
# Loop over each file in the input folder, extract the subject ID and session information, and rename the files following the structure described bellow.
# sub-7TNNNLLYYYYMMDD_ses-01_task-blank_run-01_bold_stim.txt
# "blank" is a variable, and it should be replaced with the actual task name. For files that has Rest, put "rest", for Block, put "BlockStim", for Cont put "ContinuousStim"
# Copy the original file in input_folder and paste in output_folder with the new name.

#!/usr/bin/env python3

import re
import shutil
from pathlib import Path
from datetime import datetime

input_folder = Path("/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/6_stimtrigger")
output_folder = Path("/autofs/cluster/vagabond/USERS/MARIO/Projects/7T/sourcedata/derivatives/spm/7_stim_correct_names")

# Subject always exists: 7TNNNNLL
subj_re = re.compile(r"(7T\d{4}[A-Za-z]{2})")

# Date is optional: YYYYMMDD (often appears after subject, but we’ll just search anywhere)
date_re = re.compile(r"(20\d{6})")  # 20000000-20999999

run_re = re.compile(r"run[-_]?0*([0-9]{1,3})", re.IGNORECASE)

# Detect group token as its own chunk (handles _Cont_, -Cont-, etc.)
group_re = re.compile(r"(?i)(?:^|[_-])(Rest|Cont|Block)(?:[_-]|$)")

task_map = {
    "Rest": "rest",
    "Block": "BlockStim",
    "Cont": "ContinuousStim",
}

def detect_group(fname: str) -> str:
    m = group_re.search(fname)
    if not m:
        raise ValueError(f"Could not detect group (Rest/Cont/Block) in: {fname}")
    g = m.group(1).lower()
    if g == "rest": return "Rest"
    if g == "block": return "Block"
    if g == "cont": return "Cont"
    raise ValueError(f"Unexpected group token in: {fname}")

def extract_subject(fname: str) -> str:
    m = subj_re.search(fname)
    if not m:
        raise ValueError(f"Could not find subject code 7TNNNNLL in: {fname}")
    return m.group(1)

def extract_mmddyy_if_present(fname: str) -> str | None:
    m = date_re.search(fname)
    if not m:
        return None
    yyyymmdd = m.group(1)
    try:
        return datetime.strptime(yyyymmdd, "%Y%m%d").strftime("%m%d%y")
    except ValueError:
        return None

# 1) Sort files into groups
groups = {"Rest": [], "Block": [], "Cont": []}
for src in sorted(input_folder.glob("*.txt")):
    g = detect_group(src.name)  # guaranteed per your statement
    groups[g].append(src)

# Create group subfolders (optional but matches “sort into groups”)
for g in groups:
    (output_folder / g).mkdir(parents=True, exist_ok=True)

# 2) Copy with renamed destination
n_total = n_copied = n_skipped = 0

for g in ["Rest", "Block", "Cont"]:
    for src in groups[g]:
        n_total += 1
        fname = src.name

        subj_code = extract_subject(fname)                 # e.g. 7T0111CI
        mmddyy = extract_mmddyy_if_present(fname)          # e.g. 110124 or None
        subj_part = f"{subj_code}{mmddyy}" if mmddyy else subj_code

        run_m = run_re.search(fname)
        run_int = int(run_m.group(1)) if run_m else 1
        run_2d = f"{run_int:02d}"

        task = task_map[g]
        new_name = f"sub-{subj_part}_ses-01_task-{task}_run-{run_2d}_bold_stim.txt"
        dst = output_folder / new_name

        if dst.exists():
            print(f"[SKIP] Exists: {dst.relative_to(output_folder)} (from {fname})")
            n_skipped += 1
            continue

        shutil.copy2(src, dst)  # COPY ONLY (does not change original)
        n_copied += 1

print("\nDone.")
print(f"  Input files: {n_total}")
print(f"  Copied:      {n_copied}")
print(f"  Skipped:     {n_skipped}")
print(f"  Output:      {output_folder}")