# TVNS fMRI Pipeline

A GUI-driven pipeline that takes a **7 T transcutaneous vagus-nerve-stimulation (tVNS)
task-fMRI** study from raw scanner DICOMs all the way to group statistics and brainstem
ROIs — including physiological denoising (RETROICOR) and an optional brainstem-focused
co-registration path.

---

## 📖 How to use it → read `docs/`

**New here? Start with [`docs/SOP_X_everything.md`](docs/SOP_X_everything.md)** — a
plain-language, start-to-finish walkthrough of the whole GUI (every step, what to click,
what to check), written for someone using it for the first time.

- **[`docs/SOP_X_everything.md`](docs/SOP_X_everything.md)** — the full operator's manual (read this first).
- **[`docs/README.md`](docs/README.md)** — index of the per-step SOPs (00–10) with the exact commands, parameters, and outputs.

---

## Launch the GUI

```bash
bash gui/run.sh
# or, with a Python that has tkinter:
python gui/app.py
```

Set your paths **once** in the **Setup** tab, then run the steps top-to-bottom from the
left navigation. Each panel runs the matching `stepNN_*.sh` script and streams output to
the console.

---

## What it does (in one breath)

DICOMs → **BIDS** → parse & clean physiology (**RETROICOR**, *before* fMRIPrep) →
**fMRIPrep** (+ optional brainstem segmentation/refinement) → stimulus & motion →
**first-level GLM** → **group** cases-vs-controls → **threshold** → **ROIs** (spheres or
atlas nuclei, including native-space brainstem).

---

## Design principles (deliberate — don't change without discussion)

- **RETROICOR is applied to the image *before* fMRIPrep**; the first-level GLM is
  **motion-only** (no double-removal of physiology).
- **Flag + log + continue** — no step silently stops or excludes a subject/run; review the
  logs under `codes/qc/`.
- **Group test is one-tailed, cases > controls** (preliminary, directional hypothesis).
- **Minimal first-level nuisance:** 6 rigid-body motion params + FD-spike regressors only.

See **[`docs/`](docs/)** for everything else (and `methodology.md`, kept local, for the
paper-style Methods).

---

*Created by Mario Murakami.*
