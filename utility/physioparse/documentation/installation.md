# Installation and Environment Setup

## Requirements

| Requirement | Version | Notes |
|------------|---------|-------|
| macOS | Any recent version | The pipeline was developed and tested on macOS |
| Anaconda or Miniconda | Any | Used to manage the Python environment |
| Conda environment | your conda environment | Must contain the packages listed below |

### Python packages (inside the conda environment)

| Package | Purpose |
|---------|---------|
| `scipy` | Reading and writing `.mat` files (`scipy.io.loadmat`, `scipy.io.savemat`) |
| `numpy` | Array operations on the physiological signal data |
| `matplotlib` | Creating the quality plots and per-segment plots |
| `tkinter` | The GUI (comes built-in with Python, no installation needed) |

> **Important:** scipy and numpy must be version-compatible. The pipeline requires **numpy < 2.0** (numpy 1.26.x works). numpy 2.x breaks scipy's compiled extensions.

---

## Checking your environment

Open a terminal and run:

```bash
conda activate <your_env>
python -c "import scipy, numpy, matplotlib; print('scipy', scipy.__version__, '| numpy', numpy.__version__)"
```

Expected output (versions may vary slightly):
```
scipy 1.15.3 | numpy 1.26.4
```

If you see an error about numpy version incompatibility, see the Troubleshooting section below.

---

## Locating the conda environment

This pipeline's GUI launcher (`gui/run.sh`) does **not** use `conda activate` because that command requires the shell to be specially initialized. Instead, it directly uses the Python executable inside the environment folder:

```
/Users/<your-username>/anaconda3/envs/<your_env>/bin/python
```

To find where your Anaconda is installed:

```bash
conda info --base
```

The environment's Python will be at `<conda_base>/envs/<your_env>/bin/python`.

You can also pass your environment name to the GUI launcher on the command line, or type it into the **Conda env** field in the GUI window and click **Apply**:

```bash
bash gui/run.sh Neuroimaging
```

---

## Troubleshooting

### "numpy 2.x" / scipy import error

This happens if numpy was accidentally upgraded to version 2.x. Fix it by downgrading:

```bash
/path/to/anaconda3/envs/<your_env>/bin/pip install "numpy<2" --force-reinstall
```

Then verify:

```bash
/path/to/anaconda3/envs/<your_env>/bin/python -c "import scipy.io; print('ok')"
```

### "Could not find conda environment: \<your_env\>"

This error appears in the terminal when running `run.sh` but does **not** stop the GUI — the launcher finds the Python executable directly by path, so this warning is harmless as long as the path below exists:

```
~/anaconda3/envs/<your_env>/bin/python
```

### GUI does not open

Check that tkinter is available in the environment:

```bash
/path/to/anaconda3/envs/<your_env>/bin/python -c "import tkinter; print('tkinter ok')"
```

If it fails, tkinter is not included in the conda environment. Install it with:

```bash
conda install -n <your_env> tk
```

### Script not found error

If the GUI shows "Script not found", the **Scripts root** field at the top of the GUI is pointing to the wrong folder. It should point to the `pseudotime/` folder that contains all six pipeline scripts:

```
step01_times_acquisition.sh
step01b_times_acquisition_block1.sh
2_plot_pseudotime_quality.py
2b_plot_pseudotime_quality_block1.py
step03_parse.py
3b_parse_block1.py
```

Click the `Change…` button next to the Scripts root field and select the correct folder.

### Wrong MAT format selected

If Step 1, 2, or 3 exits with an error like:

```
ERROR: 'data_block1' key not found in this .mat file.
       This file appears to be in the classic format.
```

or:

```
ERROR: 'data_block1' key not found in this .mat file.
       Use step03_parse.py instead.
```

you have selected the Block1 variant for a Classic-format file (or vice versa). Go back to the step tab, switch the **MAT file format** radio button to the other option, and re-run.
