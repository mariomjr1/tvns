#!/usr/bin/env python3
"""Render the project folder-structure diagram used in docs/_start_here.md.
Run:  python docs/images/make_structure_image.py   (regenerates project_structure.png)"""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).with_name("project_structure.png")

# (tree, tag, description). tag is the pipeline step that writes the folder.
ROWS = [
    ("<project>/", "", "the folder you Create or Open"),
    ("├─ rawdata/", "00", "downloaded DICOMs"),
    ("├─ sourcedata/", "01", "tidy BIDS dataset"),
    ("│   ├─ sub-XXXX/ses-01/{anat,func,fmap}/", "", "per-subject scans + JSON"),
    ("│   ├─ .heudiconv/", "", "conversion metadata"),
    ("│   └─ derivatives/", "", ""),
    ("│       ├─ physio/", "02-04", "parse + RETROICOR-corrected BOLD"),
    ("│       ├─ fmriprep/", "05", "preprocessing + HTML reports"),
    ("│       ├─ freesurfer/", "05b", "recon-all + brainstem / pituitary seg"),
    ("│       ├─ brainstem_coreg/", "05c", "brainstem refine warps"),
    ("│       └─ spm/", "", ""),
    ("│           ├─ first_level_mni/", "07", "MNI route        -> step 08"),
    ("│           ├─ first_level_t1w/", "07", "native route     -> step 10b"),
    ("│           ├─ second_level/", "08-09", "tasks / groups / thresholded"),
    ("│           └─ roi/", "10/10b", "ROI values, spheres, atlas-in-native"),
    ("└─ codes/", "", ""),
    ("    ├─ qc/", "", "ALL QC: logs, audits, qc_digest, snapshots"),
    ("    ├─ logs/", "", "dated project-check snapshots"),
    ("    ├─ SubjectList.txt", "", "raw scanner IDs        (you fill in)"),
    ("    └─ SubjectListBIDS.txt", "", "BIDS IDs               (auto, step 05)"),
]


def _font(size, mono=True):
    cands = (["/System/Library/Fonts/Menlo.ttc", "/System/Library/Fonts/Monaco.ttf",
              "/Library/Fonts/Menlo.ttc"] if mono else
             ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/SFNS.ttf"])
    for c in cands:
        try:
            return ImageFont.truetype(c, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    mono, monob, title_f = _font(20), _font(20), _font(26, mono=False)
    rowh, top, x_tree, x_tag, x_desc, W = 30, 96, 26, 470, 565, 1140
    H = top + rowh * len(ROWS) + 56
    img = Image.new("RGB", (W, H), (24, 24, 28))
    d = ImageDraw.Draw(img)

    d.text((26, 22), "Project folder structure", font=title_f, fill=(235, 235, 235))
    d.text((28, 58), "Every folder below is created automatically by  + Create  "
                     "(or already present when you Open an existing project).",
           font=_font(15, mono=False), fill=(150, 150, 150))

    for i, (tree, tag, desc) in enumerate(ROWS):
        y = top + i * rowh
        is_dir = tree.rstrip().endswith("/")
        tree_col = (120, 200, 170) if is_dir else (220, 220, 170)  # dirs green-ish, files yellow-ish
        if tree.strip() == "<project>/":
            tree_col = (240, 240, 240)
        d.text((x_tree, y), tree, font=mono, fill=tree_col)
        if tag:
            d.text((x_tag, y), f"[{tag}]", font=monob, fill=(86, 156, 214))   # blue step tag
        if desc:
            d.text((x_desc, y), desc, font=mono, fill=(150, 150, 150))

    fy = top + rowh * len(ROWS) + 14
    d.line((26, fy - 6, W - 26, fy - 6), fill=(60, 60, 66))
    d.text((26, fy), "[NN] = the pipeline step that writes the folder.   "
                     "green = folder · yellow = file.",
           font=_font(14, mono=False), fill=(130, 130, 130))
    img.save(OUT)
    print("wrote", OUT)


if __name__ == "__main__":
    main()
