#!/usr/bin/env python3
"""
BIDS fMRI Pipeline GUI

A project-driven dashboard: select (or create) a project folder, then run the
pipeline steps (DICOM download → BIDS → fMRIPrep → physio/RETROICOR → first/
second-level → ROI). The left panel inventories the project and tracks changes.
"""

import csv
import datetime
import json
import os
import re
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

SCRIPTS_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(SCRIPTS_ROOT))          # so QCPanel can import utility.qc_snapshots
from runner import ScriptRunner

# ── Defaults extracted from the existing shell scripts ────────────────────────

_DEFAULTS = {
    "project_root": "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme",
    "out_path":     "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/rawdata",
    "sourcedata":   "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata",
    "heuristic":    str(SCRIPTS_ROOT / "utility" / "heuristic.py"),
    "env_activate": "/autofs/cluster/vagabond/USERS/MARIO/Packages/env/heudiconv/bin/activate",
    # Subject lists live in the project's codes/ folder (temporary working lists)
    "subjlist":      "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/codes/SubjectList.txt",
    "subjlist_bids": "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/codes/SubjectListBIDS.txt",
    # ── Common tool paths, shared by every step (set once in Setup) ───────────
    "fmriprep":     "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata/derivatives/fmriprep",
    "spm_dir":      "/autofs/cluster/vagabond/USERS/MARIO/Packages/matlab/spm12",
    "matlab_exe":   "matlab",
    "matlab_code":  str(SCRIPTS_ROOT / "utility" / "matlab_code"),
    "env_script":   str(SCRIPTS_ROOT / "utility" / "fmriprep_env.sh"),
    "python_exe":   sys.executable,
    "retro_code":   str(SCRIPTS_ROOT / "utility" / "retroicor"),
    "rdeco_code":   str(SCRIPTS_ROOT / "utility" / "r-deco-master"),
    # FreeSurfer 8.1+ home (sourced by the segmentation steps 05b/05c — needed for
    # PGlandsSeg which requires >= 8.1, and for brainstem substructures). Set in
    # Setup; the .sh scripts source <freesurfer_home>/SetUpFreeSurfer.sh.
    "freesurfer_home": "/usr/local/freesurfer/8.1.0",
    # Brainstem mask (Task 05) — binary mask built by the Brainstem Mask tool;
    # shared so steps 07/08/09/10 can restrict the analysis to the brainstem.
    "brainstem_mask": "",
    # Brainstem nuclei atlas (e.g. Brainstem Navigator, MNI space) — labeled NIfTI
    # used by step10 for per-nucleus (NTS/LC/raphe) ROIs. Set once here.
    "brainstem_atlas": "",
}


def numbered_subdirs(names):
    """Map names to zero-padded numbered folder names, e.g.
    ['stim','motion','bold'] -> {'stim':'01_stim', 'motion':'02_motion', 'bold':'03_bold'}.
    So multi-step procedures create ordered, self-documenting output folders."""
    return {n: f"{i:02d}_{n}" for i, n in enumerate(names, start=1)}


# ── Shared widgets ─────────────────────────────────────────────────────────────

class PathRow(ttk.Frame):
    """Label + Entry + Browse button.  Pass var= to share an external StringVar."""

    def __init__(self, parent, label, mode="file", filetypes=None,
                 on_change=None, var=None, label_width=22, **kwargs):
        super().__init__(parent, **kwargs)
        self._mode = mode
        self._filetypes = filetypes or [("All files", "*.*")]
        self._on_change = on_change

        ttk.Label(self, text=label, width=label_width, anchor="w").pack(side="left")
        self.var = var if var is not None else tk.StringVar()
        if on_change:
            self.var.trace_add("write", self._changed)
        ttk.Entry(self, textvariable=self.var).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(self, text="Browse…", width=9, command=self._browse).pack(side="left")

    def _browse(self):
        if self._mode == "dir":
            path = filedialog.askdirectory()
        elif self._mode == "save":
            path = filedialog.asksaveasfilename(filetypes=self._filetypes,
                                                defaultextension=self._filetypes[0][1])
        else:
            path = filedialog.askopenfilename(filetypes=self._filetypes)
        if path:
            self.var.set(path)

    def _changed(self, *_):
        if self._on_change:
            self._on_change(self.var.get())

    def get(self):
        return self.var.get().strip()

    def set(self, value):
        self.var.set(str(value))


class Console(ttk.Frame):
    """Dark scrollable console with colour-coded output."""

    _TAGS = {
        "error": "#f44747",
        "warn":  "#dcdcaa",
        "ok":    "#4ec9b0",
        "info":  "#ff4444",
        "dim":   "#6a6a6a",
    }

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._text = tk.Text(
            self, bg="#1e1e1e", fg="#d4d4d4",
            font=("Menlo", 11), wrap="word",
            state="disabled", relief="flat",
        )
        sb = ttk.Scrollbar(self, command=self._text.yview)
        self._text["yscrollcommand"] = sb.set
        sb.pack(side="right", fill="y")
        self._text.pack(side="left", fill="both", expand=True)
        for name, colour in self._TAGS.items():
            self._text.tag_config(name, foreground=colour)

    def append(self, line, tag=None):
        if tag is None:
            low = line.lower()
            if any(w in low for w in ("error", "traceback", "failed", "✗")):
                tag = "error"
            elif any(w in low for w in ("warning", "warn", "⚠")):
                tag = "warn"
            elif any(w in low for w in ("✓", "done", "saved", "complete", "ok")):
                tag = "ok"
            elif line.startswith("[Step") or line.startswith("==="):
                tag = "info"
        self._text.config(state="normal")
        self._text.insert("end", line + "\n", tag or "")
        self._text.see("end")
        self._text.config(state="disabled")

    def clear(self):
        self._text.config(state="normal")
        self._text.delete("1.0", "end")
        self._text.config(state="disabled")

    def separator(self):
        self.append("─" * 70, "dim")


# ── Scrollable tab container ───────────────────────────────────────────────────

class _ScrolledTab(ttk.Frame):
    """Wrap a Notebook tab in a Canvas so its content scrolls vertically.
    Instantiate with the Notebook as parent; put widgets in .inner."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)
        self._canvas = tk.Canvas(self, highlightthickness=0)
        _vsb = ttk.Scrollbar(self, orient="vertical", command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=_vsb.set)
        _vsb.pack(side="right", fill="y")
        self._canvas.pack(side="left", fill="both", expand=True)
        self.inner = ttk.Frame(self._canvas)
        self._win  = self._canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfig(
            self._win, width=e.width))
        self._canvas.bind("<Enter>", lambda _: self._canvas.bind_all(
            "<MouseWheel>", self._on_wheel))
        self._canvas.bind("<Leave>", lambda _: self._canvas.unbind_all("<MouseWheel>"))

    def _on_wheel(self, e):
        self._canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")


# ── Pipeline state tracker ─────────────────────────────────────────────────────

class PipelineState:
    """Persists per-subject step completion in pipeline_state.json."""

    STEPS = [
        ("step_00",  "00"),
        ("step_01",  "01 BIDS"),
        ("bids_val", "BIDS Val"),
        ("step_05",  "05 fMRIPrep"),
        ("fd_qc",    "FD QC"),
    ]

    def __init__(self, path: Path):
        self._path      = path
        self._data: dict = {}
        self._callbacks: list = []
        self._load()

    def _load(self):
        if self._path.exists():
            try:
                with open(self._path) as f:
                    self._data = json.load(f)
            except Exception:
                self._data = {}

    def save(self):
        with open(self._path, "w") as f:
            json.dump(self._data, f, indent=2)

    def update(self, subject: str, step: str, status: str, note: str = ""):
        if subject not in self._data:
            self._data[subject] = {}
        self._data[subject][step] = {
            "status": status,
            "time":   datetime.datetime.now().isoformat(timespec="seconds"),
            "note":   note,
        }
        self.save()
        for cb in self._callbacks:
            cb()

    def update_many(self, subjects, step: str, status: str, note: str = ""):
        for s in subjects:
            self.update(s, step, status, note)

    def on_change(self, cb):
        self._callbacks.append(cb)

    def subjects(self):
        return sorted(self._data.keys())

    def get_status(self, subject: str, step: str) -> str:
        return self._data.get(subject, {}).get(step, {}).get("status", "")

    def get_note(self, subject: str, step: str) -> str:
        return self._data.get(subject, {}).get(step, {}).get("note", "")


# ── FD / registration QC (called post-fMRIPrep) ────────────────────────────────

def _run_fd_qc(derivatives_dir: str, subjects: list, threshold: float = 0.9,
               session: str = "01") -> dict:
    """Parse fMRIPrep confounds TSVs; return {subj: {mean_fd, flagged, has_output}}."""
    results = {}
    for subj in subjects:
        func_dir = Path(derivatives_dir) / subj / f"ses-{session}" / "func"
        if not func_dir.is_dir():
            results[subj] = {"has_output": False, "mean_fd": None, "flagged": False}
            continue
        fds = []
        for tsv in func_dir.glob("*_desc-confounds_timeseries.tsv"):
            try:
                with open(tsv) as f:
                    for row in csv.DictReader(f, delimiter="\t"):
                        v = row.get("framewise_displacement", "n/a")
                        if v not in ("n/a", "", "NA"):
                            fds.append(float(v))
            except Exception:
                pass
        if fds:
            mfd = sum(fds) / len(fds)
            results[subj] = {"has_output": True, "mean_fd": round(mfd, 4), "flagged": mfd > threshold}
        else:
            results[subj] = {"has_output": True, "mean_fd": None, "flagged": False}
        # Basic registration check: MNI BOLD must exist
        mni = list(func_dir.glob("*MNI152*desc-preproc_bold.nii.gz"))
        results[subj]["has_mni_bold"] = len(mni) > 0
    return results


# ── Subject List Editor ────────────────────────────────────────────────────────

class SubjectListEditor(ttk.Frame):
    """Listbox-based editor for SubjectList.txt files."""

    def __init__(self, parent, list_path_var: tk.StringVar, **kwargs):
        super().__init__(parent, **kwargs)
        self._path_var = list_path_var

        # File path row
        file_row = ttk.Frame(self)
        file_row.pack(fill="x", pady=(0, 6))
        ttk.Label(file_row, text="File:", width=6, anchor="w").pack(side="left")
        ttk.Entry(file_row, textvariable=self._path_var).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(file_row, text="Browse…", command=self._browse).pack(side="left", padx=(0, 2))
        ttk.Button(file_row, text="New…",    command=self._new).pack(side="left", padx=(0, 2))
        ttk.Button(file_row, text="Reload",  command=self._reload).pack(side="left")

        # Listbox
        lb_frame = ttk.Frame(self)
        lb_frame.pack(fill="both", expand=True)
        sb = ttk.Scrollbar(lb_frame, orient="vertical")
        self._lb = tk.Listbox(
            lb_frame, selectmode="extended", height=8,
            bg="#2d2d2d", fg="#d4d4d4", selectbackground="#264f78",
            font=("Menlo", 11), yscrollcommand=sb.set,
        )
        sb.config(command=self._lb.yview)
        sb.pack(side="right", fill="y")
        self._lb.pack(side="left", fill="both", expand=True)

        # Buttons row
        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", pady=(6, 0))
        self._entry_var = tk.StringVar()
        entry = ttk.Entry(btn_row, textvariable=self._entry_var, width=26)
        entry.pack(side="left", padx=(0, 4))
        entry.bind("<Return>", lambda _: self._add())
        ttk.Button(btn_row, text="Add",        command=self._add).pack(side="left", padx=(0, 2))
        ttk.Button(btn_row, text="Remove",     command=self._remove).pack(side="left", padx=(0, 2))
        ttk.Button(btn_row, text="↑",          command=lambda: self._move(-1), width=3).pack(side="left", padx=(0, 2))
        ttk.Button(btn_row, text="↓",          command=lambda: self._move(1),  width=3).pack(side="left", padx=(0, 8))
        ttk.Button(btn_row, text="Save",       command=self.save).pack(side="right")

        self._path_var.trace_add("write", lambda *_: self._reload())
        self._reload()

    def _browse(self):
        p = filedialog.askopenfilename(filetypes=[("Text files", "*.txt"), ("All", "*.*")])
        if p:
            self._path_var.set(p)

    def _new(self):
        p = filedialog.asksaveasfilename(
            defaultextension=".txt",
            filetypes=[("Text files", "*.txt")],
            initialfile="SubjectList.txt",
        )
        if p:
            Path(p).touch()
            self._path_var.set(p)

    def _reload(self):
        path = self._path_var.get().strip()
        self._lb.delete(0, "end")
        if path and os.path.isfile(path):
            with open(path) as f:
                for line in f:
                    s = line.strip()
                    if s:
                        self._lb.insert("end", s)

    def _add(self):
        s = self._entry_var.get().strip()
        if s:
            self._lb.insert("end", s)
            self._entry_var.set("")

    def _remove(self):
        for idx in reversed(self._lb.curselection()):
            self._lb.delete(idx)

    def _move(self, direction):
        sel = list(self._lb.curselection())
        if not sel:
            return
        if direction == -1 and sel[0] == 0:
            return
        if direction == 1 and sel[-1] == self._lb.size() - 1:
            return
        items = [self._lb.get(i) for i in range(self._lb.size())]
        new_sel = []
        indices = sel if direction == 1 else reversed(sel)
        for idx in indices:
            target = idx + direction
            items[idx], items[target] = items[target], items[idx]
            new_sel.append(target)
        self._lb.delete(0, "end")
        for item in items:
            self._lb.insert("end", item)
        self._lb.selection_clear(0, "end")
        for i in new_sel:
            self._lb.selection_set(i)

    def save(self):
        path = self._path_var.get().strip()
        if not path:
            path = filedialog.asksaveasfilename(
                defaultextension=".txt",
                filetypes=[("Text files", "*.txt")],
                initialfile="SubjectList.txt",
            )
            if not path:
                return
            self._path_var.set(path)
        subjects = self.get_subjects()
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w") as f:
            for s in subjects:
                f.write(s + "\n")
        messagebox.showinfo("Saved", f"SubjectList saved:\n{path}")

    def get_subjects(self):
        return [self._lb.get(i) for i in range(self._lb.size())]


# ── Setup Panel ────────────────────────────────────────────────────────────────

class SetupPanel(ttk.Frame):
    """Configure all paths and edit SubjectList.txt."""

    def __init__(self, parent, cfg: dict, **kwargs):
        super().__init__(parent, padding=14, **kwargs)

        ttk.Label(self, text="Pipeline Setup",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 10))

        # ── Paths ──────────────────────────────────────────────────────────
        paths_frame = ttk.LabelFrame(self, text="Paths", padding=(10, 6))
        paths_frame.pack(fill="x", pady=(0, 10))

        path_rows = [
            ("Raw data path:",      "out_path",     "dir",  None),
            ("BIDS sourcedata:",    "sourcedata",   "dir",  None),
            ("Heuristic file:",     "heuristic",    "file", [("Python", "*.py"), ("All", "*.*")]),
            ("Env activate script:", "env_activate", "file", None),
            ("SubjectList.txt:",    "subjlist",     "file", [("Text", "*.txt"), ("All", "*.*")]),
        ]
        for label, key, mode, filetypes in path_rows:
            PathRow(paths_frame, label, mode=mode, filetypes=filetypes,
                    var=cfg[key]).pack(fill="x", pady=2)

        # ── Common tool paths (shared by every step) ────────────────────────
        tools_frame = ttk.LabelFrame(
            self, text="Common tool paths  (set once — used by all steps)", padding=(10, 6))
        tools_frame.pack(fill="x", pady=(0, 10))
        tool_rows = [
            ("fMRIPrep derivatives:", "fmriprep",    "dir",  None),
            ("SPM12 dir:",            "spm_dir",     "dir",  None),
            ("MATLAB exe:",           "matlab_exe",  "file", None),
            ("MATLAB code dir:",      "matlab_code", "dir",  None),
            ("Environment script:",   "env_script",  "file", [("Shell", "*.sh"), ("All", "*.*")]),
            ("Python exe:",           "python_exe",  "file", None),
            ("RETROICOR code dir:",   "retro_code",  "dir",  None),
            ("R-DECO code dir:",      "rdeco_code",  "dir",  None),
            ("FreeSurfer 8.1+ home:", "freesurfer_home", "dir", None),
            ("Brainstem atlas (NIfTI):", "brainstem_atlas", "file", [("NIfTI", "*.nii *.nii.gz"), ("All", "*.*")]),
        ]
        for label, key, mode, filetypes in tool_rows:
            PathRow(tools_frame, label, mode=mode, filetypes=filetypes,
                    var=cfg[key], label_width=22).pack(fill="x", pady=2)
        ttk.Label(tools_frame,
                  text="fMRIPrep auto-derives from BIDS sourcedata; override if needed.",
                  foreground="gray").pack(anchor="w", pady=(2, 0))

        # ── Subject list ───────────────────────────────────────────────────
        subj_frame = ttk.LabelFrame(self, text="Subject List", padding=(10, 6))
        subj_frame.pack(fill="both", expand=True)
        SubjectListEditor(subj_frame, cfg["subjlist"]).pack(fill="both", expand=True)


# ── Step 00 Panel ──────────────────────────────────────────────────────────────

# Robust download script. Per subject it:
#   - skips subjects already downloaded (step0_DONE.txt present)
#   - runs findsession and collects ALL DICOM PATHs (one subject ID may map to
#     several sessions → each goes to its own folder: raw, or raw_01/raw_02/…)
#   - skips subjects/paths with no access (and keeps going — never aborts)
# Step 00 (DICOM download) now runs via step00_unpack_V2.sh in foreground mode —
# see DownloadPanel._run. (No inline bash in the GUI — Task 36.)


class DownloadPanel(ttk.Frame):
    """Download raw DICOMs via findsession + rsync (sequential, output in console)."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner,
                 state: "PipelineState | None" = None, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner
        self._state   = state
        self._last_subjects: list = []

        ttk.Label(self, text="Step 00 — Download DICOMs",
                  font=("Helvetica", 13, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Uses findsession to locate DICOM archives on the cluster "
                  "and rsync-copies them to <raw_path>/<subjectID>/DICOM/raw/.\n"
                  "Subjects run sequentially; output appears in the console below."),
            wraplength=580, foreground="gray",
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        # Paths summary
        summary = ttk.LabelFrame(self, text="Active paths (from Setup)", padding=(8, 4))
        summary.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._lbl_raw  = ttk.Label(summary, foreground="#ff4444")
        self._lbl_subj = ttk.Label(summary, foreground="#ff4444")
        self._lbl_raw.pack(anchor="w")
        self._lbl_subj.pack(anchor="w")
        cfg["out_path"].trace_add("write",  lambda *_: self._update_labels())
        cfg["subjlist"].trace_add("write",  lambda *_: self._update_labels())
        self._update_labels()

        # Subject selection
        sel_frame = ttk.LabelFrame(self, text="Subject selection", padding=(8, 4))
        sel_frame.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self._sel_mode = tk.StringVar(value="all")
        ttk.Radiobutton(sel_frame, text="All subjects from SubjectList.txt",
                        variable=self._sel_mode, value="all").pack(anchor="w")
        row_sp = ttk.Frame(sel_frame)
        row_sp.pack(anchor="w", fill="x")
        ttk.Radiobutton(row_sp, text="Specific subject:",
                        variable=self._sel_mode, value="specific").pack(side="left")
        self._specific_var = tk.StringVar()
        ttk.Entry(row_sp, textvariable=self._specific_var, width=30).pack(side="left", padx=6)

        ttk.Separator(self).grid(row=4, column=0, sticky="ew", pady=8)

        btn_row = ttk.Frame(self)
        btn_row.grid(row=5, column=0, sticky="w")
        self._run_btn = ttk.Button(btn_row, text="▶  Run Step 00", command=self._run)
        self._run_btn.pack(side="left")
        self._stop_btn = ttk.Button(btn_row, text="⏹ Stop", command=self._stop, state="disabled")
        self._stop_btn.pack(side="left", padx=4)
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=180)
        self._progress.pack(side="left", padx=12)

        self.columnconfigure(0, weight=1)

    def _update_labels(self):
        self._lbl_raw.config(text=f"Raw data:    {self._cfg['out_path'].get() or '(not set)'}")
        self._lbl_subj.config(text=f"SubjectList: {self._cfg['subjlist'].get() or '(not set)'}")

    def _subjects(self):
        if self._sel_mode.get() == "specific":
            s = self._specific_var.get().strip()
            return [s] if s else []
        path = self._cfg["subjlist"].get().strip()
        if not path or not os.path.isfile(path):
            return []
        with open(path) as f:
            return [ln.strip() for ln in f if ln.strip()]

    def _run(self):
        out_path = self._cfg["out_path"].get()
        subjects = self._subjects()

        if not out_path:
            messagebox.showerror("Error", "Set the raw data path in Setup.")
            return
        if not subjects:
            messagebox.showerror("Error", "No subjects found. Check SubjectList.txt.")
            return

        script = SCRIPTS_ROOT / "step00_unpack_V2.sh"
        if not script.is_file():
            messagebox.showerror("Error", f"Script not found:\n{script}")
            return

        # Write the selected subjects to a temp list and call the .sh in foreground
        # mode (sequential, streams output). No inline bash in the GUI.
        tmp = tempfile.NamedTemporaryFile(
            "w", suffix="_step00_subjects.txt", delete=False)
        tmp.write("\n".join(subjects) + "\n")
        tmp.close()
        self._tmp_subjlist = tmp.name
        cmd = ["bash", str(script), tmp.name, out_path, "foreground"]

        self._last_subjects = subjects
        if self._state:
            self._state.update_many(subjects, "step_00", "running")

        self._console.separator()
        self._console.append(f"[Step 00]  {len(subjects)} subject(s): {', '.join(subjects)}", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._progress.start(10)
        self._status.set("Step 00 running…")

        self._runner.run(
            cmd=cmd, cwd=out_path,
            on_line=self._console.append,
            on_done=self._done,
        )

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        tmp = getattr(self, "_tmp_subjlist", "")
        if tmp and os.path.isfile(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        if rc == 0:
            self._status.set("Step 00 complete ✓")
            self._console.append("[Step 00] Finished successfully.", "ok")
            if self._state:
                self._state.update_many(self._last_subjects, "step_00", "done")
        else:
            self._status.set(f"Step 00 failed (exit {rc})")
            self._console.append(f"[Step 00] Failed (exit {rc}).", "error")
            if self._state:
                self._state.update_many(self._last_subjects, "step_00", "failed")

    def _stop(self):
        """Stop the currently running process."""
        self._runner.stop()


# ── Sequence Viewer ────────────────────────────────────────────────────────────

class SequenceViewerPanel(ttk.Frame):
    """
    Parse dicominfo.tsv produced by heudiconv -c none (Step 01 Pass 1).

    File location:  {sourcedata}/.heudiconv/{subject}/info/dicominfo.tsv
    """

    _COLS = [
        ("series_id",          80),
        ("series_description", 230),
        ("dim3",               55),
        ("dim4",               55),
        ("TR",                 65),
        ("TE",                 65),
        ("protocol_name",      180),
        ("is_derived",         70),
    ]

    def __init__(self, parent, cfg: dict, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg = cfg

        ttk.Label(self, text="Sequence Viewer",
                  font=("Helvetica", 12, "bold")).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Label(
            self,
            text=("Shows DICOM sequences from dicominfo.tsv — generated by Step 01 Pass 1 (-c none).\n"
                  "Use this to identify series_description / dim3 values for writing your heuristic."),
            wraplength=620, foreground="gray",
        ).grid(row=1, column=0, columnspan=3, sticky="w", pady=(2, 10))

        ttk.Label(self, text="Subject:").grid(row=2, column=0, sticky="w")
        self._subj_var = tk.StringVar()
        self._combo = ttk.Combobox(self, textvariable=self._subj_var, width=30,
                                   state="readonly")
        self._combo.grid(row=2, column=1, sticky="w", padx=4)
        self._combo.bind("<<ComboboxSelected>>", lambda *_: self._load())
        ttk.Button(self, text="↻ Scan subjects",
                   command=self._scan).grid(row=2, column=2, padx=6)

        # Treeview
        tv_frame = ttk.Frame(self)
        tv_frame.grid(row=3, column=0, columnspan=3, sticky="nsew", pady=(8, 0))

        cols = [c for c, _ in self._COLS]
        self._tv = ttk.Treeview(tv_frame, columns=cols, show="headings", height=14)
        for col, width in self._COLS:
            self._tv.heading(col, text=col,
                             command=lambda c=col: self._sort(c))
            self._tv.column(col, width=width, minwidth=40,
                            stretch=(col == "series_description"))

        vsb = ttk.Scrollbar(tv_frame, orient="vertical",   command=self._tv.yview)
        hsb = ttk.Scrollbar(tv_frame, orient="horizontal", command=self._tv.xview)
        self._tv.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._tv.pack(side="left", fill="both", expand=True)

        self._sort_col = None
        self._sort_rev = False

        self.columnconfigure(1, weight=1)
        self.rowconfigure(3, weight=1)

    @staticmethod
    def _dicominfo_in(info_dir: Path):
        """Return the dicominfo TSV in info_dir (prefers ses-01), or None.
        heudiconv with -ss 01 names it dicominfo_ses-01.tsv, else dicominfo.tsv."""
        hits = sorted(info_dir.glob("dicominfo*.tsv"))
        if not hits:
            return None
        ses = [h for h in hits if "ses-01" in h.name]
        return ses[0] if ses else hits[0]

    def _tsv_path(self, subj):
        sd = self._cfg["sourcedata"].get().strip()
        return self._dicominfo_in(Path(sd) / ".heudiconv" / subj / "info")

    def _scan(self):
        sd = self._cfg["sourcedata"].get().strip()
        if not sd or not os.path.isdir(sd):
            messagebox.showwarning("Warning", "Set the sourcedata path in Setup first.")
            return
        hh = Path(sd) / ".heudiconv"
        if not hh.is_dir():
            messagebox.showinfo(
                "Not found",
                f"No .heudiconv folder in:\n{sd}\nRun Step 01 Pass 1 first.",
            )
            return
        subjects = sorted(
            p.name for p in hh.iterdir()
            if p.is_dir() and self._dicominfo_in(p / "info") is not None
        )
        self._combo["values"] = subjects
        if subjects:
            self._subj_var.set(subjects[0])
            self._load()

    def _load(self):
        subj = self._subj_var.get().strip()
        if not subj:
            return
        tsv = self._tsv_path(subj)
        if tsv is None or not tsv.is_file():
            messagebox.showwarning(
                "Not found",
                f"dicominfo*.tsv not found in:\n"
                f"{self._cfg['sourcedata'].get()}/.heudiconv/{subj}/info/")
            return
        self._tv.delete(*self._tv.get_children())
        cols = [c for c, _ in self._COLS]
        with open(tsv, newline="") as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                self._tv.insert("", "end", values=[row.get(c, "") for c in cols])

    def _sort(self, col):
        items = [(self._tv.set(k, col), k) for k in self._tv.get_children("")]
        reverse = (self._sort_col == col) and not self._sort_rev
        try:
            items.sort(key=lambda x: float(x[0]) if x[0] else -1, reverse=reverse)
        except ValueError:
            items.sort(key=lambda x: x[0].lower(), reverse=reverse)
        for idx, (_, k) in enumerate(items):
            self._tv.move(k, "", idx)
        self._sort_col, self._sort_rev = col, reverse


# ── Pass panels (Pass 1 and Pass 2 for Step 01) ───────────────────────────────

class _PassPanel(ttk.Frame):
    def __init__(self, parent, pass_num: int, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner,
                 state: "PipelineState | None" = None, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._pass_num = pass_num
        self._cfg      = cfg
        self._console  = console
        self._status   = status_var
        self._runner   = runner
        self._state    = state
        self._last_subjects: list = []

        if pass_num == 1:
            title = "Pass 1 — Generate Sequence Codes (-c none)"
            desc  = ("Runs heudiconv with -c none for each subject. No NIfTI files are produced.\n"
                     "After this pass, switch to the Sequences tab to inspect what was found, "
                     "then edit your heuristic.py accordingly.")
        else:
            title = "Pass 2 — Convert DICOMs to BIDS (dcm2niix)"
            desc  = ("Runs heudiconv with dcm2niix using the heuristic set in Setup.\n"
                     "NIfTI files are written to the sourcedata folder in BIDS layout.")

        ttk.Label(self, text=title,
                  font=("Helvetica", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(self, text=desc, wraplength=580,
                  foreground="gray").grid(row=1, column=0, sticky="w", pady=(0, 10))

        # Paths summary
        summary = ttk.LabelFrame(self, text="Active paths (from Setup)", padding=(8, 4))
        summary.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._lbl = [ttk.Label(summary, foreground="#ff4444") for _ in range(3)]
        for lbl in self._lbl:
            lbl.pack(anchor="w")
        for key in ("out_path", "sourcedata", "heuristic"):
            cfg[key].trace_add("write", lambda *_: self._update_summary())
        self._update_summary()

        # Session label
        ss_row = ttk.Frame(self)
        ss_row.grid(row=3, column=0, sticky="w", pady=(0, 8))
        ttk.Label(ss_row, text="Session label (-ss):").pack(side="left")
        self._ss_var = tk.StringVar(value="01")
        ttk.Entry(ss_row, textvariable=self._ss_var, width=8).pack(side="left", padx=6)
        ttk.Label(ss_row, text="(e.g. 01)", foreground="gray").pack(side="left")

        # Heuristic selector (Pass 2 only)
        self._heur_var = None
        if pass_num == 2:
            hr = ttk.Frame(self)
            hr.grid(row=3, column=0, sticky="e", pady=(0, 8))
            ttk.Label(hr, text="Heuristic:").pack(side="left")
            self._heur_var = tk.StringVar(value=cfg["heuristic"].get())
            self._heur_combo = ttk.Combobox(hr, textvariable=self._heur_var, width=34)
            self._heur_combo.pack(side="left", padx=(4, 4))
            ttk.Button(hr, text="↻", width=3, command=self._scan_heuristics).pack(side="left")
            self._scan_heuristics()

        # Subject selection
        sel_frame = ttk.LabelFrame(self, text="Subject selection", padding=(8, 4))
        sel_frame.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self._sel_mode = tk.StringVar(value="all")
        ttk.Radiobutton(sel_frame, text="All subjects from SubjectList.txt",
                        variable=self._sel_mode, value="all").pack(anchor="w")
        row_sp = ttk.Frame(sel_frame)
        row_sp.pack(anchor="w")
        ttk.Radiobutton(row_sp, text="Specific subject:",
                        variable=self._sel_mode, value="specific").pack(side="left")
        self._specific_var = tk.StringVar()
        ttk.Entry(row_sp, textvariable=self._specific_var, width=30).pack(side="left", padx=6)

        ttk.Separator(self).grid(row=5, column=0, sticky="ew", pady=8)

        btn_row = ttk.Frame(self)
        btn_row.grid(row=6, column=0, sticky="w")
        label = f"▶  Run Pass {pass_num}"
        self._run_btn = ttk.Button(btn_row, text=label, command=self._run)
        self._run_btn.pack(side="left")
        self._stop_btn = ttk.Button(btn_row, text="⏹ Stop", command=self._stop, state="disabled")
        self._stop_btn.pack(side="left", padx=4)
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=180)
        self._progress.pack(side="left", padx=12)

        self.columnconfigure(0, weight=1)

    def _update_summary(self):
        self._lbl[0].config(text=f"raw_path:   {self._cfg['out_path'].get()  or '(not set)'}")
        self._lbl[1].config(text=f"sourcedata: {self._cfg['sourcedata'].get() or '(not set)'}")
        self._lbl[2].config(text=f"heuristic:  {self._cfg['heuristic'].get() or '(not set)'}")

    def _subjects(self):
        if self._sel_mode.get() == "specific":
            s = self._specific_var.get().strip()
            return [s] if s else []
        path = self._cfg["subjlist"].get().strip()
        if not path or not os.path.isfile(path):
            return []
        with open(path) as f:
            return [ln.strip() for ln in f if ln.strip()]

    def _activate_prefix(self):
        act = self._cfg["env_activate"].get().strip()
        if act and os.path.isfile(act):
            return f"source '{act}' && "
        return ""

    def _scan_heuristics(self):
        """Populate the Pass-2 heuristic dropdown from utility/heuristic/ + Setup."""
        if self._heur_var is None:
            return
        heur_dir = SCRIPTS_ROOT / "utility" / "heuristic"
        opts = []
        if heur_dir.is_dir():
            opts = [str(p) for p in sorted(heur_dir.glob("*.py"))]
        default = self._cfg["heuristic"].get().strip()
        if default and default not in opts:
            opts.insert(0, default)
        self._heur_combo["values"] = opts
        if not self._heur_var.get() and opts:
            self._heur_var.set(opts[0])

    def _run(self):
        raw_path   = self._cfg["out_path"].get()
        sourcedata = self._cfg["sourcedata"].get()
        # Pass 2 uses the heuristic picked in this panel; else the Setup default.
        if self._heur_var is not None and self._heur_var.get().strip():
            heuristic = self._heur_var.get().strip()
        else:
            heuristic = self._cfg["heuristic"].get()
        ss         = self._ss_var.get().strip() or "01"
        subjects   = self._subjects()

        if not raw_path:
            messagebox.showerror("Error", "Set raw data path in Setup.")
            return
        if not sourcedata:
            messagebox.showerror("Error", "Set sourcedata path in Setup.")
            return
        if self._pass_num == 2 and not heuristic:
            messagebox.showerror("Error", "Set heuristic file in Setup.")
            return
        if not subjects:
            messagebox.showerror("Error", "No subjects. Check SubjectList.txt.")
            return

        prefix = self._activate_prefix()

        # Each subject may have one raw folder (.../DICOM/raw) or several from
        # step00 (raw_01, raw_02, …). Convert each as its own BIDS session:
        # plain "raw" → the session label below; "raw_NN" → ses-NN.
        parts = []
        for subj in subjects:
            dcm_root = f"{raw_path}/{subj}/DICOM"
            if self._pass_num == 1:
                hcmd = (f"heudiconv --files \"$dd\" -o '{sourcedata}' "
                        f"-f convertall -s {subj} -ss \"$sess\" -c none")
            else:
                hcmd = (f"heudiconv --files \"$dd\" -o '{sourcedata}' "
                        f"-f '{heuristic}' -s {subj} -ss \"$sess\" "
                        f"-c dcm2niix -b --overwrite")
            parts.append(
                f"echo '=== {subj} ==='\n"
                f"dirs=()\n"
                f"[ -d '{dcm_root}/raw' ] && dirs+=('{dcm_root}/raw')\n"
                f"for d in '{dcm_root}'/raw_*; do [ -d \"$d\" ] && dirs+=(\"$d\"); done\n"
                f"if [ ${{#dirs[@]}} -eq 0 ]; then echo '✗ no raw folder for {subj}'; else\n"
                f"  for dd in \"${{dirs[@]}}\"; do\n"
                f"    b=$(basename \"$dd\")\n"
                f"    if [[ \"$b\" =~ ^raw_([0-9]+)$ ]]; then sess=\"${{BASH_REMATCH[1]}}\"; else sess='{ss}'; fi\n"
                f"    echo \"  -> $dd  (ses-$sess)\"\n"
                f"    {hcmd} && echo \"✓ {subj} ses-$sess\" || echo \"✗ {subj} ses-$sess\"\n"
                f"  done\n"
                f"fi"
            )

        full_script = prefix + "\n".join(parts)
        cmd = ["bash", "-c", full_script]

        self._last_subjects = subjects
        step_key = "step_01_p1" if self._pass_num == 1 else "step_01"
        if self._state:
            self._state.update_many(subjects, step_key, "running")

        label = "Pass 1" if self._pass_num == 1 else "Pass 2"
        self._console.separator()
        self._console.append(
            f"[Step 01 {label}]  {len(subjects)} subject(s): {', '.join(subjects)}", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._progress.start(10)
        self._status.set(f"Step 01 {label} running…")

        self._runner.run(
            cmd=cmd, cwd=sourcedata or "/tmp",
            on_line=self._console.append,
            on_done=self._done,
        )

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        label = "Pass 1" if self._pass_num == 1 else "Pass 2"
        step_key = "step_01_p1" if self._pass_num == 1 else "step_01"
        if rc == 0:
            self._status.set(f"Step 01 {label} complete ✓")
            self._console.append(f"[Step 01 {label}] Finished.", "ok")
            if self._state:
                self._state.update_many(self._last_subjects, step_key, "done")
        else:
            self._status.set(f"Step 01 {label} failed (exit {rc})")
            self._console.append(f"[Step 01 {label}] Failed (exit {rc}).", "error")
            if self._state:
                self._state.update_many(self._last_subjects, step_key, "failed")

    def _stop(self):
        """Stop the currently running process."""
        self._runner.stop()


# ── Step 01 Panel ──────────────────────────────────────────────────────────────

class BidsPanel(ttk.Frame):
    """Inner notebook: Pass 1 | Sequences | Pass 2 | BIDS Validator."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner,
                 state: "PipelineState | None" = None, **kwargs):
        super().__init__(parent, padding=(6, 6), **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner

        ttk.Label(self, text="Step 01 — BIDS Conversion (heudiconv)",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 6))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        self._nb = nb

        self._pass1   = _PassPanel(nb, 1, cfg, console, status_var, runner, state=state)
        self._seqview = SequenceViewerPanel(nb, cfg)
        self._pass2   = _PassPanel(nb, 2, cfg, console, status_var, runner, state=state)
        self._bidsval = _BIDSValidatorTab(nb, cfg, console, status_var, runner, state=state)

        nb.add(self._pass1,   text="  Pass 1 — Generate codes  ")
        nb.add(self._seqview, text="  Sequences  ")
        nb.add(self._pass2,   text="  Pass 2 — Convert to BIDS  ")
        nb.add(self._bidsval, text="  BIDS Validator  ")

        nb.bind("<<NotebookTabChanged>>", self._on_tab_change)

    def _on_tab_change(self, _event):
        if self._nb.index("current") == 1:
            self._seqview._scan()


# ── BIDS Validator tab ─────────────────────────────────────────────────────────

class _BIDSValidatorTab(ttk.Frame):
    """Run bids-validator on the sourcedata directory and update pipeline state."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner,
                 state: "PipelineState | None" = None, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner
        self._state   = state
        self._output_lines: list = []

        ttk.Label(self, text="BIDS Validator",
                  font=("Helvetica", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Runs bids-validator on the sourcedata directory.\n"
                  "Requires bids-validator in PATH (available after sourcing fmriprep_env.sh)."),
            foreground="gray", wraplength=580,
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        summary = ttk.LabelFrame(self, text="BIDS directory (from Setup)", padding=(8, 4))
        summary.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._lbl_sd = ttk.Label(summary, foreground="#ff4444")
        self._lbl_sd.pack(anchor="w")
        cfg["sourcedata"].trace_add("write", lambda *_: self._update_label())
        self._update_label()

        env_row = ttk.Frame(self)
        env_row.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(env_row, text="Source env (for node/npm):", width=26, anchor="w").pack(side="left")
        self._env_var = cfg["env_script"]
        ttk.Entry(env_row, textvariable=self._env_var).pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(env_row, text="Browse…", width=9,
                   command=lambda: self._env_var.set(
                       filedialog.askopenfilename(filetypes=[("Shell", "*.sh"), ("All", "*.*")])
                       or self._env_var.get()
                   )).pack(side="left")

        ttk.Separator(self).grid(row=4, column=0, sticky="ew", pady=8)

        btn_row = ttk.Frame(self)
        btn_row.grid(row=5, column=0, sticky="w")
        self._run_btn = ttk.Button(btn_row, text="▶  Run BIDS Validator", command=self._run)
        self._run_btn.pack(side="left")
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=200)
        self._progress.pack(side="left", padx=12)

        self.columnconfigure(0, weight=1)

    def _update_label(self):
        self._lbl_sd.config(
            text=f"sourcedata: {self._cfg['sourcedata'].get() or '(not set)'}")

    def _run(self):
        sd = self._cfg["sourcedata"].get().strip()
        if not sd:
            messagebox.showerror("Error", "Set the sourcedata path in Setup.")
            return
        env = self._env_var.get().strip()
        prefix = f"source '{env}' && " if env and os.path.isfile(env) else ""
        script = (
            f"{prefix}"
            f"if command -v bids-validator &>/dev/null; then\n"
            f"  bids-validator '{sd}'\n"
            f"elif command -v npx &>/dev/null; then\n"
            f"  npx bids-validator '{sd}'\n"
            f"else\n"
            f"  echo 'ERROR: bids-validator not found — install: npm install -g bids-validator'\n"
            f"  exit 1\n"
            f"fi"
        )
        self._output_lines = []
        self._console.separator()
        self._console.append("[BIDS Validator] Starting…", "info")
        self._console.separator()
        self._run_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set("BIDS Validator running…")
        self._runner.run(
            cmd=["bash", "-c", script], cwd=sd,
            on_line=self._on_line,
            on_done=self._done,
        )

    def _on_line(self, line):
        self._output_lines.append(line)
        self._console.append(line)

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        has_errors = rc != 0 or any(
            "error" in ln.lower() for ln in self._output_lines
            if not ln.strip().startswith("#")
        )
        if not has_errors:
            self._status.set("BIDS Validator passed ✓")
            self._console.append("[BIDS Validator] Dataset valid.", "ok")
            bids_status, note = "done", "valid"
        else:
            self._status.set("BIDS Validator: issues found")
            self._console.append("[BIDS Validator] Issues found — review console.", "warn")
            bids_status, note = "failed", "errors"

        if self._state:
            sd = self._cfg["sourcedata"].get().strip()
            subjects = (
                [d.name for d in Path(sd).iterdir()
                 if d.is_dir() and d.name.startswith("sub-")]
                if sd and os.path.isdir(sd) else []
            )
            if not subjects:
                p = self._cfg["subjlist"].get().strip()
                if p and os.path.isfile(p):
                    with open(p) as f:
                        subjects = [ln.strip() for ln in f if ln.strip()]
            self._state.update_many(subjects, "bids_val", bids_status, note)


# ── BIDS Subject List tab — emits SubjectListBIDS.txt (a step 01 artifact) ────

class _BIDSListTab(ttk.Frame):
    """Convert SubjectList.txt entries to BIDS IDs and write SubjectListBIDS.txt."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, fp_subj_var: tk.StringVar, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg       = cfg
        self._console   = console
        self._status    = status_var
        self._fp_subj   = fp_subj_var

        ttk.Label(self, text="Generate BIDS Subject List",
                  font=("Helvetica", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Converts SubjectList.txt entries to BIDS format:\n"
                  "  7T1019HC_042726  →  sub-7T1019HC042726\n"
                  "(prepends 'sub-' and strips all underscores)"),
            foreground="gray", wraplength=580,
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        out_frame = ttk.LabelFrame(self, text="Output file", padding=(8, 4))
        out_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        PathRow(out_frame, "Save to:", mode="save",
                filetypes=[("Text files", "*.txt"), ("All", "*.*")],
                var=self._fp_subj, label_width=10).pack(fill="x")

        ttk.Label(self, text="Preview:").grid(row=3, column=0, sticky="w")

        tv_frame = ttk.Frame(self)
        tv_frame.grid(row=4, column=0, sticky="nsew", pady=(4, 8))
        self._tv = ttk.Treeview(tv_frame, columns=("original", "bids_id"),
                                show="headings", height=10)
        self._tv.heading("original", text="Original ID")
        self._tv.heading("bids_id",  text="BIDS ID (output)")
        self._tv.column("original", width=220)
        self._tv.column("bids_id",  width=280)
        vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=self._tv.yview)
        self._tv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._tv.pack(side="left", fill="both", expand=True)

        btn_row = ttk.Frame(self)
        btn_row.grid(row=5, column=0, sticky="w")
        ttk.Button(btn_row, text="↻ Preview",       command=self._preview).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="✓  Generate file", command=self._generate).pack(side="left")

        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        cfg["subjlist"].trace_add("write", lambda *_: self._preview())
        self._preview()

    # ── helpers ────────────────────────────────────────────────────────────────

    def _subjects(self):
        path = self._cfg["subjlist"].get().strip()
        if not path or not os.path.isfile(path):
            return []
        with open(path) as f:
            return [ln.strip() for ln in f if ln.strip()]

    @staticmethod
    def _to_bids(raw_id: str) -> str:
        return "sub-" + raw_id.replace("_", "")

    def _preview(self):
        self._tv.delete(*self._tv.get_children())
        for raw in self._subjects():
            self._tv.insert("", "end", values=(raw, self._to_bids(raw)))

    def _generate(self):
        subjects = self._subjects()
        if not subjects:
            messagebox.showerror("Error", "No subjects found — check SubjectList.txt in Setup.")
            return
        out_path = self._fp_subj.get().strip()
        if not out_path:
            messagebox.showerror("Error", "Set the output file path first.")
            return
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w") as f:
            for raw in subjects:
                f.write(self._to_bids(raw) + "\n")
        self._console.append(f"[BIDS list] saved: {out_path}", "ok")
        self._status.set(f"BIDS list saved → {Path(out_path).name}")
        messagebox.showinfo("Saved", f"Saved:\n{out_path}")


# ── Step 01 — BIDS conversion sub-tab (calls step01_create_bids_v2.sh) ────────

class _BIDSConvTab(ttk.Frame):
    """Run step01_create_bids_v2.sh directly (heudiconv two-pass BIDS conversion)."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner,
                 state: "PipelineState | None" = None, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner
        self._state   = state
        self._last_subjects: list = []

        ttk.Label(self, text="BIDS Conversion — step01_create_bids_v2.sh",
                  font=("Helvetica", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Runs step01_create_bids_v2.sh: heudiconv Pass 1 (-c none) "
                  "then Pass 2 (dcm2niix).\n"
                  "Paths are taken from Setup."),
            foreground="gray", wraplength=580,
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        script_frame = ttk.LabelFrame(self, text="Script", padding=(8, 4))
        script_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._script_var = tk.StringVar(value=str(SCRIPTS_ROOT / "step01_create_bids_v2.sh"))
        PathRow(script_frame, "Script path:", mode="file",
                filetypes=[("Shell", "*.sh"), ("All", "*.*")],
                var=self._script_var, label_width=14).pack(fill="x")

        summary = ttk.LabelFrame(self, text="Active paths (from Setup)", padding=(8, 4))
        summary.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self._lbl = [ttk.Label(summary, foreground="#ff4444") for _ in range(4)]
        for lbl in self._lbl:
            lbl.pack(anchor="w")
        for key in ("out_path", "sourcedata", "heuristic", "env_activate"):
            cfg[key].trace_add("write", lambda *_: self._update_summary())
        self._update_summary()

        sel_frame = ttk.LabelFrame(self, text="Subject selection", padding=(8, 4))
        sel_frame.grid(row=4, column=0, sticky="ew", pady=(0, 8))
        self._sel_mode = tk.StringVar(value="all")
        ttk.Radiobutton(sel_frame, text="All subjects from SubjectList.txt",
                        variable=self._sel_mode, value="all").pack(anchor="w")
        row_sp = ttk.Frame(sel_frame)
        row_sp.pack(anchor="w")
        ttk.Radiobutton(row_sp, text="Specific subject:",
                        variable=self._sel_mode, value="specific").pack(side="left")
        self._specific_var = tk.StringVar()
        ttk.Entry(row_sp, textvariable=self._specific_var, width=30).pack(side="left", padx=6)

        ttk.Separator(self).grid(row=5, column=0, sticky="ew", pady=8)

        btn_row = ttk.Frame(self)
        btn_row.grid(row=6, column=0, sticky="w")
        self._run_btn = ttk.Button(btn_row, text="▶  Run BIDS Conversion", command=self._run)
        self._run_btn.pack(side="left")
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=200)
        self._progress.pack(side="left", padx=12)

        self.columnconfigure(0, weight=1)

    def _update_summary(self):
        keys    = ("out_path", "sourcedata", "heuristic", "env_activate")
        labels  = ("raw_path:   ", "sourcedata: ", "heuristic:  ", "env:        ")
        for lbl, key, prefix in zip(self._lbl, keys, labels):
            lbl.config(text=f"{prefix}{self._cfg[key].get() or '(not set)'}")

    def _subjects(self):
        if self._sel_mode.get() == "specific":
            s = self._specific_var.get().strip()
            return [s] if s else []
        path = self._cfg["subjlist"].get().strip()
        if not path or not os.path.isfile(path):
            return []
        with open(path) as f:
            return [ln.strip() for ln in f if ln.strip()]

    def _run(self):
        script = self._script_var.get().strip()
        if not script or not os.path.isfile(script):
            messagebox.showerror("Error", f"Script not found:\n{script}")
            return
        subjects = self._subjects()
        if not subjects:
            messagebox.showerror("Error", "No subjects found. Check SubjectList.txt in Setup.")
            return

        if self._sel_mode.get() == "specific":
            tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
            tmp.write(subjects[0] + "\n")
            tmp.close()
            subj_list_arg = tmp.name
        else:
            subj_list_arg = self._cfg["subjlist"].get().strip()

        env = self._cfg["env_activate"].get().strip()
        prefix = f"source '{env}' && " if env and os.path.isfile(env) else ""
        cmd = ["bash", "-c", f"{prefix}bash '{script}' '{subj_list_arg}'"]

        self._last_subjects = subjects
        if self._state:
            self._state.update_many(subjects, "step_01", "running")

        self._console.separator()
        self._console.append(
            f"[Step 01]  BIDS conversion — {len(subjects)} subject(s): {', '.join(subjects)}", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set("Step 01 (BIDS) running…")

        self._runner.run(
            cmd=cmd, cwd=str(SCRIPTS_ROOT),
            on_line=self._console.append,
            on_done=self._done,
        )

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        if rc == 0:
            self._status.set("Step 01 (BIDS) complete ✓")
            self._console.append("[Step 01] BIDS conversion finished.", "ok")
            if self._state:
                self._state.update_many(self._last_subjects, "step_01", "done")
        else:
            self._status.set(f"Step 01 (BIDS) failed (exit {rc})")
            self._console.append(f"[Step 01] BIDS conversion failed (exit {rc}).", "error")
            if self._state:
                self._state.update_many(self._last_subjects, "step_01", "failed")


# ── Step 05 — fMRIPrep sub-tab (local, via ScriptRunner; on RETROICOR-corrected) ──

class _FmriprepTab(ttk.Frame):
    """Run fMRIPrep locally via Singularity, one subject at a time."""

    # NEW ORDER (RETROICOR → fMRIPrep): fMRIPrep ingests the RETROICOR-corrected
    # BIDS (sourcedata_retrocorr), but writes derivatives under the RAW sourcedata
    # so downstream paths are unchanged.
    _DEFAULTS_FP = {
        "raw_bids":   "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata",
        "bids_dir":   "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata_retrocorr",
        "fp_der":     "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata/derivatives/fmriprep",
        "fs_dir":     "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata/derivatives/freesurfer",
        "work_dir":   "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/codes/working-fmriprep",
        "simg":       "/autofs/cluster/vagabond/USERS/MARIO/Pipelines/my_images/fmriprep-25.2.3.simg",
        "fs_license": "/autofs/cluster/vagabond/USERS/MARIO/Pipelines/license.txt",
    }

    def __init__(self, parent, console: Console, status_var: tk.StringVar,
                 runner: ScriptRunner, fp_env_var: tk.StringVar,
                 fp_subj_var: tk.StringVar,
                 python_exe_var: "tk.StringVar | None" = None,
                 fs_home_var: "tk.StringVar | None" = None,
                 state: "PipelineState | None" = None, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._console        = console
        self._status         = status_var
        self._runner         = runner
        self._fp_env_var     = fp_env_var
        self._fp_subj        = fp_subj_var
        self._python_exe_var  = python_exe_var
        self._fs_home_var     = fs_home_var   # FreeSurfer >= 8.1 home (Setup)
        self._state           = state
        self._last_subjects: list = []
        self._tmp_subj_file: "str | None" = None
        self._vars       = {k: tk.StringVar(value=v) for k, v in self._DEFAULTS_FP.items()}
        self._mni_tmpl_var = tk.StringVar(value="")   # MNI template for step05c refine

        ttk.Label(self, text="fMRIPrep",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self,
            text="Runs fMRIPrep locally via Singularity, sequentially per subject.",
            foreground="gray", wraplength=580,
        ).pack(anchor="w", pady=(0, 10))

        paths_frame = ttk.LabelFrame(self, text="Paths", padding=(10, 6))
        paths_frame.pack(fill="x", pady=(0, 8))
        for label, key, mode, filetypes in [
            ("Raw BIDS (source):", "raw_bids", "dir",  None),
            ("Corrected BIDS:",   "bids_dir",  "dir",  None),
            ("Derivatives dir:",  "fp_der",    "dir",  None),
            ("FreeSurfer dir:",   "fs_dir",    "dir",  None),
            ("Working dir:",      "work_dir",  "dir",  None),
            ("Singularity img:",  "simg",      "file", [("Singularity", "*.simg *.sif"), ("All", "*.*")]),
            ("FS license:",       "fs_license","file", None),
        ]:
            PathRow(paths_frame, label, mode=mode, filetypes=filetypes,
                    var=self._vars[key], label_width=18).pack(fill="x", pady=2)

        env_frame = ttk.LabelFrame(self, text="Environment & subject list", padding=(10, 6))
        env_frame.pack(fill="x", pady=(0, 8))
        PathRow(env_frame, "fmriprep_env.sh:", mode="file",
                filetypes=[("Shell", "*.sh"), ("All", "*.*")],
                var=self._fp_env_var, label_width=18).pack(fill="x", pady=2)
        PathRow(env_frame, "BIDS subject list:", mode="file",
                filetypes=[("Text", "*.txt"), ("All", "*.*")],
                var=self._fp_subj, label_width=18).pack(fill="x", pady=2)

        opts_frame = ttk.LabelFrame(self, text="Options", padding=(10, 6))
        opts_frame.pack(fill="x", pady=(0, 8))

        row_spaces = ttk.Frame(opts_frame)
        row_spaces.pack(fill="x", pady=2)
        ttk.Label(row_spaces, text="Output spaces:", width=18, anchor="w").pack(side="left")
        self._spaces_var = tk.StringVar(value="T1w MNI152NLin2009cAsym")
        ttk.Entry(row_spaces, textvariable=self._spaces_var).pack(side="left", fill="x", expand=True)

        row_mem = ttk.Frame(opts_frame)
        row_mem.pack(fill="x", pady=2)
        ttk.Label(row_mem, text="Memory (MB):", width=18, anchor="w").pack(side="left")
        self._mem_var = tk.StringVar(value="50000")
        ttk.Entry(row_mem, textvariable=self._mem_var, width=10).pack(side="left")

        chk_row = ttk.Frame(opts_frame)
        chk_row.pack(fill="x", pady=2)
        # STC is now ON by default: fMRIPrep does neural slice-timing correction on the
        # RETROICOR-corrected data (RETROICOR used slice timing for physio phase upstream).
        self._ignore_st = tk.BooleanVar(value=False)
        self._skip_bids = tk.BooleanVar(value=True)
        self._cifti     = tk.BooleanVar(value=True)
        self._assemble  = tk.BooleanVar(value=True)
        ttk.Checkbutton(chk_row, text="--ignore slicetiming",   variable=self._ignore_st).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(chk_row, text="--skip-bids-validation", variable=self._skip_bids).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(chk_row, text="--cifti-output",         variable=self._cifti).pack(side="left")
        ttk.Checkbutton(opts_frame, text="Assemble RETROICOR-corrected BIDS first (from step04 output)",
                        variable=self._assemble).pack(anchor="w", pady=(4, 0))

        sel_frame = ttk.LabelFrame(self, text="Subject selection", padding=(8, 4))
        sel_frame.pack(fill="x", pady=(0, 8))
        self._sel_mode = tk.StringVar(value="all")
        ttk.Radiobutton(sel_frame, text="All subjects from BIDS list",
                        variable=self._sel_mode, value="all").pack(anchor="w")
        row_sp = ttk.Frame(sel_frame)
        row_sp.pack(anchor="w")
        ttk.Radiobutton(row_sp, text="Specific subject (BIDS ID):",
                        variable=self._sel_mode, value="specific").pack(side="left")
        self._specific_var = tk.StringVar()
        ttk.Entry(row_sp, textvariable=self._specific_var, width=32).pack(side="left", padx=6)

        ttk.Separator(self).pack(fill="x", pady=8)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x")
        self._run_btn = ttk.Button(btn_row, text="▶  Run fMRIPrep", command=self._run)
        self._run_btn.pack(side="left")
        self._stop_btn = ttk.Button(btn_row, text="⏹ Stop", command=self._stop, state="disabled")
        self._stop_btn.pack(side="left", padx=4)
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=200)
        self._progress.pack(side="left", padx=12)

        # ── Post-recon-all segmentation extras (step05b) — after fMRIPrep ─────────
        seg_frame = ttk.LabelFrame(
            self, text="FreeSurfer segmentation extras (step05b — after recon-all)",
            padding=(10, 6))
        seg_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(seg_frame, foreground="gray", wraplength=560,
                  text=("Runs on the FreeSurfer dir after fMRIPrep's recon-all. Brainstem "
                        "substructures produce the subject-space mask used by step05c "
                        "co-registration refinement; PGlandsSeg needs FreeSurfer ≥ 8.1 "
                        "(set in Setup). Flag + log + continue — never skips silently.")
                  ).pack(anchor="w", pady=(0, 4))
        seg_row = ttk.Frame(seg_frame); seg_row.pack(fill="x")
        self._bs_btn = ttk.Button(seg_row, text="▶ Brainstem segmentation",
                                  command=lambda: self._run_seg("brainstem"))
        self._bs_btn.pack(side="left", padx=(0, 6))
        self._pit_btn = ttk.Button(seg_row, text="▶ Pituitary/pineal (PGlandsSeg)",
                                   command=lambda: self._run_seg("pituitary"))
        self._pit_btn.pack(side="left")
        PathRow(seg_frame, "MNI template (05c):", mode="file",
                filetypes=[("NIfTI", "*.nii *.nii.gz"), ("All", "*.*")],
                var=self._mni_tmpl_var, label_width=18).pack(fill="x", pady=(6, 2))
        coreg_row = ttk.Frame(seg_frame); coreg_row.pack(fill="x")
        self._coreg_btn = ttk.Button(
            coreg_row, text="▶ Brainstem co-reg refine (step05c, masked SyN)",
            command=self._run_coreg)
        self._coreg_btn.pack(side="left")

    def _subjects(self):
        if self._sel_mode.get() == "specific":
            s = self._specific_var.get().strip()
            return [s] if s else []
        p = self._fp_subj.get().strip()
        if not p or not os.path.isfile(p):
            return []
        with open(p) as f:
            return [ln.strip() for ln in f if ln.strip()]

    def _run(self):
        subjects = self._subjects()
        if not subjects:
            messagebox.showerror("Error", "No subjects. Generate the BIDS list first.")
            return

        raw_bids = self._vars["raw_bids"].get()
        bids_dir = self._vars["bids_dir"].get()
        fp_der   = self._vars["fp_der"].get()
        fs_dir   = self._vars["fs_dir"].get()
        work_dir = self._vars["work_dir"].get()
        simg     = self._vars["simg"].get()
        fs_lic   = self._vars["fs_license"].get()
        fp_env   = self._fp_env_var.get()
        spaces   = self._spaces_var.get() or "T1w MNI152NLin2009cAsym"
        mem      = self._mem_var.get() or "50000"
        python_exe = (self._python_exe_var.get() if self._python_exe_var else None) or "python3"

        # Build subject list file: use the file directly if we read from it;
        # write a temp file when running a single specific subject.
        self._tmp_subj_file = None
        if self._sel_mode.get() == "specific":
            tmp = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", prefix="step05_subj_", delete=False)
            tmp.write("\n".join(subjects) + "\n")
            tmp.close()
            self._tmp_subj_file = tmp.name
            subj_list_path = tmp.name
        else:
            subj_list_path = self._fp_subj.get().strip()

        # Optional fMRIPrep flags
        extra_flags = []
        if self._ignore_st.get(): extra_flags.append("--ignore slicetiming")
        if self._skip_bids.get(): extra_flags.append("--skip-bids-validation")
        if self._cifti.get():     extra_flags.append("--cifti-output")
        if not self._assemble.get(): extra_flags.append("--no-assemble")

        script = str(SCRIPTS_ROOT / "step05_fmriprep_v2.sh")
        cmd = (
            ["bash", script,
             subj_list_path, raw_bids, bids_dir, fp_der,
             fs_dir, work_dir, simg, fs_lic,
             python_exe, spaces, mem, fp_env or ""]
            + extra_flags
        )

        self._last_subjects = subjects
        if self._state:
            self._state.update_many(subjects, "step_05", "running")

        self._console.separator()
        self._console.append(
            f"[Step 05]  fMRIPrep (on RETROICOR-corrected) — {len(subjects)} subject(s): "
            f"{', '.join(subjects)}", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._progress.start(10)
        self._status.set("Step 05 (fMRIPrep) running…")

        self._runner.run(
            cmd=cmd, cwd=bids_dir or "/tmp",
            on_line=self._console.append,
            on_done=self._done,
        )

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        if getattr(self, "_tmp_subj_file", None):
            try:
                os.unlink(self._tmp_subj_file)
            except OSError:
                pass
            self._tmp_subj_file = None
        if rc == 0:
            self._status.set("Step 05 (fMRIPrep) complete ✓")
            self._console.append("[Step 05] fMRIPrep finished.", "ok")
            if self._state:
                self._state.update_many(self._last_subjects, "step_05", "done")
            self._run_qc()
        else:
            self._status.set(f"Step 05 (fMRIPrep) failed (exit {rc})")
            self._console.append(f"[Step 05] fMRIPrep failed (exit {rc}).", "error")
            if self._state:
                self._state.update_many(self._last_subjects, "step_05", "failed")

    def _run_seg(self, what):
        """step05b: FreeSurfer brainstem / pituitary segmentation (flag+log+continue)."""
        script = SCRIPTS_ROOT / "step05b_freesurfer_segment_v2.sh"
        if not script.is_file():
            messagebox.showerror("Error", f"Script not found:\n{script}"); return
        subjects = self._subjects()
        if not subjects:
            messagebox.showerror("Error", "No subjects. Generate the BIDS list first."); return
        fs_home = (self._fs_home_var.get().strip() if self._fs_home_var else "")
        if not fs_home:
            messagebox.showerror("Error", "Set 'FreeSurfer 8.1+ home' in Setup first."); return
        fs_dir = self._vars["fs_dir"].get().strip()
        if not fs_dir:
            messagebox.showerror("Error", "Set the FreeSurfer dir first."); return

        tmp = tempfile.NamedTemporaryFile(
            "w", suffix="_step05b_subj.txt", prefix="seg_", delete=False)
        tmp.write("\n".join(subjects) + "\n"); tmp.close()
        self._tmp_subj_file = tmp.name
        cmd = ["bash", str(script), tmp.name, fs_home, fs_dir, what]

        label = {"brainstem": "Brainstem segmentation",
                 "pituitary": "Pituitary/pineal segmentation"}.get(what, "FS segmentation")
        self._console.separator()
        self._console.append(f"[step05b] {label} — {len(subjects)} subject(s)…", "info")
        self._console.separator()
        for b in (self._run_btn, self._bs_btn, self._pit_btn, self._coreg_btn):
            b.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._progress.start(10)
        self._status.set(f"{label} running…")
        self._runner.run(cmd=cmd, cwd=fs_dir if os.path.isdir(fs_dir) else "/tmp",
                         on_line=self._console.append,
                         on_done=lambda rc, lbl=label: self._seg_done(rc, lbl))

    def _seg_done(self, rc, label):
        self._progress.stop()
        for b in (self._run_btn, self._bs_btn, self._pit_btn, self._coreg_btn):
            b.config(state="normal")
        self._stop_btn.config(state="disabled")
        if getattr(self, "_tmp_subj_file", None):
            try:
                os.unlink(self._tmp_subj_file)
            except OSError:
                pass
            self._tmp_subj_file = None
        if rc == 0:
            self._status.set(f"{label} complete ✓")
            self._console.append(f"[step05b] {label} finished (review FLAG lines).", "ok")
        else:
            self._status.set(f"{label} failed (exit {rc})")
            self._console.append(f"[step05b] {label} failed (exit {rc}).", "error")

    def _run_coreg(self):
        """step05c: brainstem cost-function-masked SyN refinement (flag+log+continue)."""
        script = SCRIPTS_ROOT / "step05c_brainstem_coreg_v2.sh"
        if not script.is_file():
            messagebox.showerror("Error", f"Script not found:\n{script}"); return
        subjects = self._subjects()
        if not subjects:
            messagebox.showerror("Error", "No subjects. Generate the BIDS list first."); return
        fs_home = (self._fs_home_var.get().strip() if self._fs_home_var else "")
        fs_dir  = self._vars["fs_dir"].get().strip()
        fp_der  = self._vars["fp_der"].get().strip()
        mni     = self._mni_tmpl_var.get().strip()
        if not (fs_home and fs_dir and fp_der):
            messagebox.showerror("Error", "Set FreeSurfer home, FreeSurfer dir, and derivatives dir."); return
        if not mni:
            messagebox.showerror("Error", "Set the MNI template (05c) path."); return
        out_dir = str(Path(fp_der).parent / "brainstem_coreg")

        tmp = tempfile.NamedTemporaryFile(
            "w", suffix="_step05c_subj.txt", prefix="coreg_", delete=False)
        tmp.write("\n".join(subjects) + "\n"); tmp.close()
        self._tmp_subj_file = tmp.name
        cmd = ["bash", str(script), tmp.name, fs_home, fs_dir, fp_der, mni, out_dir]

        label = "Brainstem co-reg refine"
        self._console.separator()
        self._console.append(f"[step05c] {label} — {len(subjects)} subject(s)…", "info")
        self._console.separator()
        for b in (self._run_btn, self._bs_btn, self._pit_btn, self._coreg_btn):
            b.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._progress.start(10)
        self._status.set(f"{label} running…")
        self._runner.run(cmd=cmd, cwd=out_dir if os.path.isdir(out_dir) else "/tmp",
                         on_line=self._console.append,
                         on_done=lambda rc, lbl=label: self._seg_done(rc, lbl))

    def _stop(self):
        """Stop the currently running process."""
        self._runner.stop()

    def _run_qc(self):
        fp_der = self._vars["fp_der"].get()
        if not fp_der or not self._last_subjects:
            return
        self._console.separator()
        self._console.append("[QC] Checking mean FD and registration outputs…", "info")
        qc = _run_fd_qc(fp_der, self._last_subjects)
        for subj, info in qc.items():
            if not info["has_output"]:
                self._console.append(f"[QC] {subj}: no fMRIPrep output found", "error")
                if self._state:
                    self._state.update(subj, "fd_qc", "failed", "no output")
                continue
            if info["mean_fd"] is None:
                self._console.append(f"[QC] {subj}: confounds TSV not found", "warn")
                if self._state:
                    self._state.update(subj, "fd_qc", "failed", "no confounds")
                continue
            mfd   = info["mean_fd"]
            flag  = info["flagged"]
            reg_ok = info.get("has_mni_bold", False)
            reg_str = "" if reg_ok else " | ⚠ MNI BOLD missing"
            tag   = "warn" if flag else "ok"
            self._console.append(
                f"[QC] {subj}: mean FD = {mfd:.4f} mm"
                f"{'  ⚠ FLAGGED (>0.9mm)' if flag else '  ✓'}{reg_str}", tag)
            if self._state:
                note = f"FD={mfd:.4f}"
                if flag:     note += " FLAGGED"
                if not reg_ok: note += " no-MNI-bold"
                self._state.update(subj, "fd_qc", "flagged" if flag else "done", note)


# ── fMRIPrep pre/post QC GIF tab (optional; compares fMRIPrep input vs output) ─

class _Step13QCTab(ttk.Frame):
    """Run step13 to generate a pre/post GIF for any two matched 4D BOLDs."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner

        ttk.Label(self, text="Pre/Post QC — optional (step13)",
                  font=("Helvetica", 12, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Optional. Generates an animated GIF comparing two 4D BOLD volumes (Pre | Post | Diff).\n"
                  "Typical use: raw BOLD vs fMRIPrep output, or pre- vs post-RETROICOR.\n"
                  "Both files must have identical shape. Skip if not needed."),
            foreground="gray", wraplength=580,
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        # Script path
        sf = ttk.LabelFrame(self, text="Script", padding=(8, 4))
        sf.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        self._script_var = tk.StringVar(
            value=str(SCRIPTS_ROOT / "step13_preproc_optional_check_pre-post.py"))
        PathRow(sf, "Script path:", mode="file",
                filetypes=[("Python", "*.py"), ("All", "*.*")],
                var=self._script_var, label_width=14).pack(fill="x")

        # File pairs
        files_frame = ttk.LabelFrame(self, text="Input files", padding=(10, 6))
        files_frame.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        self._pre_var  = tk.StringVar()
        self._post_var = tk.StringVar()
        PathRow(files_frame, "Pre  (raw / pre-correction):",
                mode="file", filetypes=[("NIfTI", "*.nii.gz *.nii"), ("All", "*.*")],
                var=self._pre_var,  label_width=30).pack(fill="x", pady=2)
        PathRow(files_frame, "Post (fMRIPrep / post-correction):",
                mode="file", filetypes=[("NIfTI", "*.nii.gz *.nii"), ("All", "*.*")],
                var=self._post_var, label_width=30).pack(fill="x", pady=2)

        # Output + options
        opts_frame = ttk.LabelFrame(self, text="Output & options", padding=(10, 6))
        opts_frame.grid(row=4, column=0, sticky="ew", pady=(0, 8))

        self._out_var = tk.StringVar(value="retroicor_compare.gif")
        PathRow(opts_frame, "Output GIF:", mode="save",
                filetypes=[("GIF", "*.gif"), ("All", "*.*")],
                var=self._out_var, label_width=14).pack(fill="x", pady=2)

        row_opts = ttk.Frame(opts_frame)
        row_opts.pack(fill="x", pady=2)
        ttk.Label(row_opts, text="Plane:", width=8, anchor="w").pack(side="left")
        self._plane_var = tk.StringVar(value="axial")
        for p in ("axial", "coronal", "sagittal"):
            ttk.Radiobutton(row_opts, text=p, variable=self._plane_var,
                            value=p).pack(side="left", padx=4)

        row_fps = ttk.Frame(opts_frame)
        row_fps.pack(fill="x", pady=2)
        ttk.Label(row_fps, text="FPS:", width=8, anchor="w").pack(side="left")
        self._fps_var = tk.StringVar(value="10")
        ttk.Entry(row_fps, textvariable=self._fps_var, width=6).pack(side="left", padx=(0, 20))
        ttk.Label(row_fps, text="Step (every Nth TR):", anchor="w").pack(side="left")
        self._step_var = tk.StringVar(value="2")
        ttk.Entry(row_fps, textvariable=self._step_var, width=6).pack(side="left", padx=4)

        ttk.Separator(self).grid(row=5, column=0, sticky="ew", pady=8)

        btn_row = ttk.Frame(self)
        btn_row.grid(row=6, column=0, sticky="w")
        self._run_btn = ttk.Button(btn_row, text="▶  Generate GIF", command=self._run)
        self._run_btn.pack(side="left")
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=200)
        self._progress.pack(side="left", padx=12)

        self.columnconfigure(0, weight=1)

    def _run(self):
        script = self._script_var.get().strip()
        pre    = self._pre_var.get().strip()
        post   = self._post_var.get().strip()
        out    = self._out_var.get().strip()

        if not script or not os.path.isfile(script):
            messagebox.showerror("Error", f"Script not found:\n{script}")
            return
        if not pre or not os.path.isfile(pre):
            messagebox.showerror("Error", f"Pre file not found:\n{pre}")
            return
        if not post or not os.path.isfile(post):
            messagebox.showerror("Error", f"Post file not found:\n{post}")
            return
        if not out:
            messagebox.showerror("Error", "Set an output GIF path.")
            return

        env = sys.executable  # run with same Python that launched the GUI
        cmd = [
            env, script,
            pre, post,
            "-o", out,
            "--plane", self._plane_var.get(),
            "--fps",   self._fps_var.get() or "10",
            "--step",  self._step_var.get() or "2",
        ]

        self._console.separator()
        self._console.append(f"[Step 13]  {Path(pre).name}  vs  {Path(post).name}", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set("Step 13 (QC GIF) running…")

        self._runner.run(
            cmd=cmd, cwd=str(Path(out).parent),
            on_line=self._console.append,
            on_done=self._done,
        )

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        if rc == 0:
            out = self._out_var.get()
            self._status.set("Step 13 GIF written ✓")
            self._console.append(f"[Step 13] GIF saved: {out}", "ok")
        else:
            self._status.set(f"Step 13 failed (exit {rc})")
            self._console.append(f"[Step 13] Failed (exit {rc}).", "error")


# ── Step 05 Panel — fMRIPrep (runs after RETROICOR, on the corrected BOLD) ──────

class FmriprepPanel(ttk.Frame):
    """Inner notebook: Generate BIDS List | fMRIPrep | Pre/Post QC (optional)."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner,
                 state: "PipelineState | None" = None, **kwargs):
        super().__init__(parent, padding=(6, 6), **kwargs)

        fp_subj_var = cfg["subjlist_bids"]
        fp_env_var  = cfg["env_script"]

        ttk.Label(self, text="Step 05 — fMRIPrep (on RETROICOR-corrected BOLD)",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 6))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        bids_list_tab = _BIDSListTab(nb, cfg, console, status_var, fp_subj_var)
        fp_tab        = _FmriprepTab(nb, console, status_var, runner, fp_env_var, fp_subj_var,
                                     python_exe_var=cfg["python_exe"],
                                     fs_home_var=cfg["freesurfer_home"], state=state)
        qc_tab        = _Step13QCTab(nb, cfg, console, status_var, runner)

        nb.add(bids_list_tab, text="  Generate BIDS List  ")
        nb.add(fp_tab,        text="  fMRIPrep  ")
        nb.add(qc_tab,        text="  Pre/Post QC — optional  ")


# ── Heuristic Editor ───────────────────────────────────────────────────────────

_HEURISTIC_TEMPLATE = '''\
import os


def create_key(template, outtype=('nii.gz',), annotation_classes=None):
    if template is None or not template:
        raise ValueError('Template must be a valid format string')
    return template, outtype, annotation_classes


def infotodict(seqinfo):
    """Heuristic evaluator for determining which runs belong where.

    seqinfo fields:
      series_id, series_description, dim1, dim2, dim3, dim4, TR, TE,
      protocol_name, is_motion_corrected, is_derived, patient_id,
      study_description, referring_physician_name, image_type
    """

    # ── Section 1: Define output keys ─────────────────────────────────────────
    t1w = create_key('sub-{subject}/{session}/anat/sub-{subject}_{session}_T1w')

    t2w = create_key('sub-{subject}/{session}/anat/sub-{subject}_{session}_T2w')

    func_rest = create_key(
        'sub-{subject}/{session}/func/sub-{subject}_{session}_task-rest_run-{item:02d}_bold')

    func_task_block_stim = create_key(
        'sub-{subject}/{session}/func/sub-{subject}_{session}_task-BlockStim_run-{item:02d}_bold')

    func_task_continuous_stim = create_key(
        'sub-{subject}/{session}/func/sub-{subject}_{session}_task-ContinuousStim_run-{item:02d}_bold')

    info = {t1w: [], t2w: [], func_rest: [],
            func_task_block_stim: [], func_task_continuous_stim: []}

    # ── Section 2: Matching criteria ──────────────────────────────────────────
    # Inspect dicominfo.tsv (Sequences tab) to find the right values.
    for s in seqinfo:
        if (s.dim3 == 176) and ('MEMP' in s.series_description):
            info[t1w].append(s.series_id)

        if (s.dim3 == 114) and ('t2' in s.series_description):
            info[t2w].append(s.series_id)

        if (s.dim3 == 92) and ('REST_ep2d_bold' in s.series_description):
            info[func_rest].append(s.series_id)

        if (s.dim3 == 92) and ('BlockStim' in s.series_description):
            info[func_task_block_stim].append(s.series_id)

        if (s.dim3 == 92) and ('ContinuousStim' in s.series_description):
            info[func_task_continuous_stim].append(s.series_id)

    # ── Section 3: Validation ─────────────────────────────────────────────────
    msg = []
    if len(info[t1w])                    != 1: msg.append('WARNING: Expected 1 T1w')
    if len(info[t2w])                    != 1: msg.append('WARNING: Expected 1 T2w')
    if len(info[func_rest])              != 1: msg.append('WARNING: Expected 1 func_rest')
    if len(info[func_task_block_stim])   != 1: msg.append('WARNING: Expected 1 func_task_block_stim')
    if len(info[func_task_continuous_stim]) != 1: msg.append('WARNING: Expected 1 func_task_continuous_stim')
    if msg:
        print('\\n'.join(msg))

    return info
'''


class HeuristicPanel(ttk.Frame):
    """Heuristic builder: load the step01 sequences for a subject, assign each
    sequence to a BIDS target (T1w / T2w / task-*), auto-generate heuristic.py,
    and save it (with an added/excluded log) to utility/heuristic/."""

    HEUR_DIR = SCRIPTS_ROOT / "utility" / "heuristic"
    TMPL_DIR = SCRIPTS_ROOT / "utility" / "heuristic" / "template"

    # sequence table columns (first col = assigned target)
    _COLS = [
        ("target",             110),
        ("series_id",          70),
        ("series_description", 240),
        ("dim3",               50),
        ("dim4",               50),
        ("TR",                 60),
        ("protocol_name",      170),
    ]
    _TARGETS = ["(exclude)", "T1w", "T2w", "task-rest",
                "task-BlockStim", "task-ContinuousStim",
                "fmap-AP", "fmap-PA"]

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, **kwargs):
        super().__init__(parent, padding=12, **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var

        ttk.Label(self, text="Heuristic Builder",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Load the sequences detected by Step 01 Pass 1, assign each to a "
                  "BIDS target, then generate + save a heuristic.py.\n"
                  "Matching is on series_description and/or dim3. Saved to "
                  "utility/heuristic/<name>.py (+ <name>.log)."),
            foreground="gray", wraplength=620,
        ).pack(anchor="w", pady=(0, 8))

        # ── Subject row ────────────────────────────────────────────────────────
        sr = ttk.Frame(self); sr.pack(fill="x", pady=(0, 6))
        ttk.Label(sr, text="Subject (.heudiconv):").pack(side="left")
        self._subj_var = tk.StringVar()
        self._combo = ttk.Combobox(sr, textvariable=self._subj_var, width=28, state="readonly")
        self._combo.pack(side="left", padx=(4, 6))
        self._combo.bind("<<ComboboxSelected>>", lambda *_: self._load_sequences())
        ttk.Button(sr, text="↻ Scan", command=self._scan).pack(side="left")

        # ── Sequences table ────────────────────────────────────────────────────
        tv_frame = ttk.Frame(self); tv_frame.pack(fill="both", expand=True)
        cols = [c for c, _ in self._COLS]
        self._tv = ttk.Treeview(tv_frame, columns=cols, show="headings",
                                height=10, selectmode="extended")
        for col, width in self._COLS:
            self._tv.heading(col, text=col)
            self._tv.column(col, width=width, minwidth=40,
                            stretch=(col == "series_description"))
        self._tv.tag_configure("assigned", foreground="#4ec9b0")
        self._tv.tag_configure("excluded", foreground="#6a6a6a")
        vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=self._tv.yview)
        self._tv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._tv.pack(side="left", fill="both", expand=True)

        # ── Assign controls ────────────────────────────────────────────────────
        ar = ttk.Frame(self); ar.pack(fill="x", pady=(6, 4))
        ttk.Label(ar, text="Target:").pack(side="left")
        self._target_var = tk.StringVar(value="T1w")
        ttk.Combobox(ar, textvariable=self._target_var, width=20,
                     values=self._TARGETS).pack(side="left", padx=(4, 6))
        ttk.Button(ar, text="Assign to selected", command=self._assign).pack(side="left", padx=(0, 4))
        ttk.Button(ar, text="Exclude selected", command=lambda: self._set_target("(exclude)")).pack(side="left", padx=(0, 4))
        ttk.Button(ar, text="Clear", command=lambda: self._set_target("")).pack(side="left")

        mr = ttk.Frame(self); mr.pack(fill="x", pady=(0, 4))
        self._match_dim3 = tk.BooleanVar(value=True)
        self._match_desc = tk.BooleanVar(value=True)
        ttk.Checkbutton(mr, text="match dim3", variable=self._match_dim3).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(mr, text="match series_description", variable=self._match_desc).pack(side="left")

        # ── Generate / save row ────────────────────────────────────────────────
        # ── Templates (shared starting points for projects with the same pattern)
        tr = ttk.Frame(self); tr.pack(fill="x", pady=(4, 2))
        ttk.Label(tr, text="Template:").pack(side="left")
        self._tmpl_var = tk.StringVar()
        self._tmpl_combo = ttk.Combobox(tr, textvariable=self._tmpl_var, width=26, state="readonly")
        self._tmpl_combo.pack(side="left", padx=(4, 4))
        ttk.Button(tr, text="↻", width=3, command=self._scan_templates).pack(side="left", padx=(0, 4))
        ttk.Button(tr, text="Load template", command=self._load_template).pack(side="left", padx=(0, 4))
        ttk.Button(tr, text="Save as template…", command=self._save_template).pack(side="left")
        self._scan_templates()

        gr = ttk.Frame(self); gr.pack(fill="x", pady=(4, 4))
        ttk.Button(gr, text="⚙ Generate", command=self._generate).pack(side="left", padx=(0, 6))
        ttk.Label(gr, text="Name:").pack(side="left")
        self._name_var = tk.StringVar(value="heuristic_new")
        ttk.Entry(gr, textvariable=self._name_var, width=22).pack(side="left", padx=(4, 6))
        ttk.Button(gr, text="💾 Save", command=self._save).pack(side="left", padx=(0, 4))
        ttk.Button(gr, text="Open…", command=self._open).pack(side="left", padx=(0, 4))
        ttk.Button(gr, text="Use in Pass 2", command=self._use).pack(side="left")

        ttk.Label(gr, text="active:", foreground="gray").pack(side="left", padx=(10, 2))
        self._active_lbl = ttk.Label(gr, foreground="#4ec9b0")
        self._active_lbl.pack(side="left")
        cfg["heuristic"].trace_add("write", lambda *_: self._update_active())
        self._update_active()

        # ── Editor ─────────────────────────────────────────────────────────────
        ed = ttk.Frame(self); ed.pack(fill="both", expand=True, pady=(4, 0))
        self._editor = tk.Text(ed, bg="#1e1e1e", fg="#d4d4d4", font=("Menlo", 11),
                               wrap="none", insertbackground="white", undo=True, height=12)
        evsb = ttk.Scrollbar(ed, orient="vertical", command=self._editor.yview)
        ehsb = ttk.Scrollbar(ed, orient="horizontal", command=self._editor.xview)
        self._editor.configure(yscrollcommand=evsb.set, xscrollcommand=ehsb.set)
        evsb.pack(side="right", fill="y")
        ehsb.pack(side="bottom", fill="x")
        self._editor.pack(side="left", fill="both", expand=True)

        self._scan()

    # ── data ──────────────────────────────────────────────────────────────────
    def _tsv_path(self, subj):
        sd = self._cfg["sourcedata"].get().strip()
        info = Path(sd) / ".heudiconv" / subj / "info"
        hits = sorted(info.glob("dicominfo*.tsv")) if info.is_dir() else []
        if not hits:
            return None
        ses = [h for h in hits if "ses-01" in h.name]
        return ses[0] if ses else hits[0]

    def _scan(self):
        sd = self._cfg["sourcedata"].get().strip()
        hh = Path(sd) / ".heudiconv" if sd else None
        subs = []
        if hh and hh.is_dir():
            subs = sorted(p.name for p in hh.iterdir()
                          if p.is_dir() and self._tsv_path(p.name))
        self._combo["values"] = subs
        if subs and not self._subj_var.get():
            self._subj_var.set(subs[0])
            self._load_sequences()

    def _load_sequences(self):
        subj = self._subj_var.get().strip()
        if not subj:
            return
        tsv = self._tsv_path(subj)
        if not tsv or not tsv.is_file():
            messagebox.showwarning("Not found",
                f"dicominfo*.tsv not found for {subj}.\nRun Step 01 Pass 1 first.")
            return
        self._tv.delete(*self._tv.get_children())
        with open(tsv, newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                self._tv.insert("", "end", values=(
                    "",                                     # target (unassigned)
                    row.get("series_id", ""),
                    row.get("series_description", ""),
                    row.get("dim3", ""),
                    row.get("dim4", ""),
                    row.get("TR", ""),
                    row.get("protocol_name", ""),
                ), tags=("excluded",))
        # pre-fill the name from the subject
        self._name_var.set(f"heuristic_{subj}")
        self._console.append(f"[Heuristic] Loaded {len(self._tv.get_children())} "
                             f"sequence(s) for {subj}", "info")

    # ── assignment ────────────────────────────────────────────────────────────
    def _assign(self):
        self._set_target(self._target_var.get().strip())

    def _set_target(self, target):
        sel = self._tv.selection()
        if not sel:
            messagebox.showwarning("No selection", "Select sequence row(s) first.")
            return
        for iid in sel:
            vals = list(self._tv.item(iid, "values"))
            vals[0] = target
            tag = "assigned" if (target and target != "(exclude)") else "excluded"
            self._tv.item(iid, values=vals, tags=(tag,))

    # ── code generation ────────────────────────────────────────────────────────
    def _rows(self):
        out = []
        for iid in self._tv.get_children():
            v = self._tv.item(iid, "values")
            out.append({"target": v[0].strip(), "series_id": v[1],
                        "desc": v[2], "dim3": v[3]})
        return out

    @staticmethod
    def _target_key(target):
        """Return (var_name, template) for a BIDS target."""
        if target == "T1w":
            return "t1w", "sub-{subject}/{session}/anat/sub-{subject}_{session}_T1w"
        if target == "T2w":
            return "t2w", "sub-{subject}/{session}/anat/sub-{subject}_{session}_T2w"
        if target in ("fmap-AP", "fmap-PA"):
            d = target.split("-")[1]            # AP or PA
            return ("fmap_" + d.lower(),
                    "sub-{subject}/{session}/fmap/sub-{subject}_{session}_dir-" + d + "_epi")
        if target.startswith("task-"):
            task = target[len("task-"):]
            var = "func_" + re.sub(r"[^A-Za-z0-9]", "_", task).lower()
            tmpl = ("sub-{subject}/{session}/func/sub-{subject}_{session}_"
                    + target + "_run-{item:02d}_bold")
            return var, tmpl
        # generic fallback (anat-like)
        var = re.sub(r"[^A-Za-z0-9]", "_", target).lower() or "misc"
        return var, "sub-{subject}/{session}/anat/sub-{subject}_{session}_" + target

    def _build_code(self):
        rows = self._rows()
        assigned = [r for r in rows if r["target"] and r["target"] != "(exclude)"]
        if not assigned:
            return None
        m_dim3 = self._match_dim3.get()
        m_desc = self._match_desc.get()

        # unique targets in order of first appearance
        targets, seen = [], set()
        for r in assigned:
            if r["target"] not in seen:
                seen.add(r["target"]); targets.append(r["target"])

        keymap = {t: self._target_key(t) for t in targets}
        has_fmap = any(t in ("fmap-AP", "fmap-PA") for t in targets)

        L = ["import os", ""]
        if has_fmap:
            # PEPOLAR fieldmaps need IntendedFor set so fMRIPrep applies TOPUP-based
            # SDC; let heudiconv (>= 0.10) populate it by matching imaging geometry.
            L += ["",
                  "# Auto-populate each PEPOLAR fieldmap's IntendedFor with the BOLD",
                  "# runs it should correct, so fMRIPrep applies TOPUP-based SDC.",
                  "POPULATE_INTENDED_FOR_OPTS = {",
                  "    'matching_parameters': 'ImagingVolume',",
                  "    'criterion': 'Closest',",
                  "}"]
        L += ["", "",
              "def create_key(template, outtype=('nii.gz',), annotation_classes=None):",
              "    if template is None or not template:",
              "        raise ValueError('Template must be a valid format string')",
              "    return template, outtype, annotation_classes", "", "",
              "def infotodict(seqinfo):",
              '    """Auto-generated by the BIDS fMRI Pipeline Heuristic Builder."""', ""]
        # Section 1: keys
        for t in targets:
            var, tmpl = keymap[t]
            L.append(f"    {var} = create_key('{tmpl}')")
        L.append("")
        L.append("    info = {" + ", ".join(f"{keymap[t][0]}: []" for t in targets) + "}")
        L.append("")
        # Section 2: matching
        L.append("    for s in seqinfo:")
        for r in assigned:
            var = keymap[r["target"]][0]
            conds = []
            if m_dim3 and str(r["dim3"]).strip():
                conds.append(f"(s.dim3 == {int(float(r['dim3']))})")
            if r["target"] in ("fmap-AP", "fmap-PA"):
                # Match the platform-independent token so one heuristic works for
                # both TOPUP_AP_..._SMS4 and TOPUP_AP_..._SMS4_5x5 naming.
                token = "TOPUP_AP" if r["target"] == "fmap-AP" else "TOPUP_PA"
                conds.append(f"({token!r} in s.series_description)")
            elif m_desc and str(r["desc"]).strip():
                conds.append(f"({r['desc']!r} in s.series_description)")
            cond = " and ".join(conds) if conds else "True"
            L.append(f"        if {cond}:")
            L.append(f"            info[{var}].append(s.series_id)")
        L.append("")
        L.append("    return info")
        L.append("")
        return "\n".join(L)

    def _generate(self):
        code = self._build_code()
        if code is None:
            messagebox.showwarning("Nothing assigned",
                "Assign at least one sequence to a BIDS target first.")
            return
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", code)
        self._console.append("[Heuristic] Generated from assignments.", "ok")

    # ── save / open / use ──────────────────────────────────────────────────────
    def _save(self):
        code = self._editor.get("1.0", "end-1c").strip()
        if not code:
            self._generate()
            code = self._editor.get("1.0", "end-1c").strip()
            if not code:
                return
        self.HEUR_DIR.mkdir(parents=True, exist_ok=True)
        name = self._name_var.get().strip() or "heuristic_new"
        if not name.endswith(".py"):
            name += ".py"
        path = self.HEUR_DIR / name
        with open(path, "w") as f:
            f.write(code if code.endswith("\n") else code + "\n")
        self._write_log(path.with_suffix(".log"))
        self._console.append(f"[Heuristic] Saved: {path}", "ok")
        self._status.set(f"Heuristic saved → {path.name}")
        messagebox.showinfo("Saved", f"Saved heuristic:\n{path}\n\nLog:\n{path.with_suffix('.log')}")

    def _write_log(self, log_path):
        rows = self._rows()
        added = [r for r in rows if r["target"] and r["target"] != "(exclude)"]
        excl  = [r for r in rows if not r["target"] or r["target"] == "(exclude)"]
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(log_path, "w") as f:
            f.write(f"Heuristic log\nBuilt from subject: {self._subj_var.get()}\nSaved: {ts}\n\n")
            f.write(f"ADDED ({len(added)} sequence(s) mapped to a BIDS target):\n")
            for r in added:
                f.write(f"  {r['target']:18s} <- dim3={r['dim3']:>4}  "
                        f"{r['desc']!r}  (series_id {r['series_id']})\n")
            f.write(f"\nEXCLUDED ({len(excl)} sequence(s) not mapped):\n")
            for r in excl:
                f.write(f"  dim3={r['dim3']:>4}  {r['desc']!r}  (series_id {r['series_id']})\n")

    def _open(self):
        p = filedialog.askopenfilename(
            title="Open heuristic", initialdir=str(self.HEUR_DIR),
            filetypes=[("Python", "*.py"), ("All", "*.*")])
        if p:
            with open(p) as f:
                self._editor.delete("1.0", "end")
                self._editor.insert("1.0", f.read())
            self._name_var.set(Path(p).name)
            self._console.append(f"[Heuristic] Opened: {p}", "info")

    # ── templates ──────────────────────────────────────────────────────────────
    def _scan_templates(self):
        names = []
        if self.TMPL_DIR.is_dir():
            names = sorted(p.name for p in self.TMPL_DIR.glob("*.py"))
        self._tmpl_combo["values"] = names
        if names and not self._tmpl_var.get():
            self._tmpl_var.set(names[0])

    def _load_template(self):
        name = self._tmpl_var.get().strip()
        if not name:
            messagebox.showwarning("No template", "No template selected.")
            return
        path = self.TMPL_DIR / name
        if not path.is_file():
            messagebox.showerror("Not found", f"Template not found:\n{path}")
            return
        with open(path) as f:
            self._editor.delete("1.0", "end")
            self._editor.insert("1.0", f.read())
        # suggest a project-specific name to save under utility/heuristic/
        subj = self._subj_var.get().strip()
        self._name_var.set(f"heuristic_{subj}" if subj else Path(name).stem)
        self._console.append(f"[Heuristic] Loaded template: {path}", "info")

    def _save_template(self):
        code = self._editor.get("1.0", "end-1c").strip()
        if not code:
            messagebox.showwarning("Empty", "Generate or load a heuristic first.")
            return
        self.TMPL_DIR.mkdir(parents=True, exist_ok=True)
        path = filedialog.asksaveasfilename(
            title="Save as template", initialdir=str(self.TMPL_DIR),
            defaultextension=".py", initialfile="template_new.py",
            filetypes=[("Python", "*.py")])
        if not path:
            return
        with open(path, "w") as f:
            f.write(code if code.endswith("\n") else code + "\n")
        self._scan_templates()
        self._tmpl_var.set(Path(path).name)
        self._console.append(f"[Heuristic] Saved template: {path}", "ok")
        self._status.set(f"Template saved → {Path(path).name}")

    def _use(self):
        name = self._name_var.get().strip()
        if not name.endswith(".py"):
            name += ".py"
        path = self.HEUR_DIR / name
        if not path.is_file():
            if messagebox.askyesno("Save first?",
                    f"{path.name} is not saved yet. Save it now and use it?"):
                self._save()
            else:
                return
        self._cfg["heuristic"].set(str(path))
        self._status.set(f"Active heuristic → {path.name}")
        self._console.append(f"[Heuristic] Active for Pass 2: {path}", "ok")

    def _update_active(self):
        p = self._cfg["heuristic"].get().strip()
        self._active_lbl.config(text=Path(p).name if p else "(none)")


# ── QC Panel ────────────────────────────────────────────────────────────────────

class QCPanel(ttk.Frame):
    """Pipeline QC snapshot generator.

    For every pipeline step that produces or transforms a subject image, generates:
      - A mid-brain 3-plane montage (axial / coronal / sagittal) saved to
        <project>/codes/qc/<subject>/NN_<step>_<subject>.png
      - A combined pipeline strip  <project>/codes/qc/<subject>/00_pipeline_montage_<subject>.png
      - A JSON manifest at <project>/codes/qc/qc_manifest.json

    The panel can target a single subject (selected from SubjectListBIDS.txt) or
    run a "check" sweep over all subjects.
    """

    # Path to the QC engine script (sibling of heuristic.py in utility/)
    _QC_SCRIPT = SCRIPTS_ROOT / "utility" / "qc_snapshots.py"
    # Cross-stage cardinality audit (Task 25) — audit only, never fails
    _AUDIT_SCRIPT = SCRIPTS_ROOT / "utility" / "audit_cardinality.py"
    # Batch analysis provenance (Task 29) — record only, never fails
    _PROV_SCRIPT = SCRIPTS_ROOT / "utility" / "collect_provenance.py"
    # SDC verification audit (Task 16) — flag + log, never fails
    _SDC_SCRIPT = SCRIPTS_ROOT / "utility" / "audit_sdc.py"

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=12, **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner

        ttk.Label(self, text="Pipeline QC Snapshots",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self,
            text=(
                "Generates mid-brain axial/coronal/sagittal montages for every pipeline step "
                "that produces or transforms a subject image.  Results land in "
                "<project>/codes/qc/<subject>/.  A combined pipeline strip and a JSON manifest "
                "are also written.  Choose a single subject or 'Check all' to sweep every entry "
                "in SubjectListBIDS.txt."
            ),
            foreground="gray", wraplength=660,
        ).pack(anchor="w", pady=(0, 10))

        # ── Paths summary ──────────────────────────────────────────────────────
        path_frame = ttk.LabelFrame(self, text="Active paths (from Setup)", padding=(8, 4))
        path_frame.pack(fill="x", pady=(0, 8))
        self._lbl_sd   = ttk.Label(path_frame, foreground="#ff4444")
        self._lbl_pr   = ttk.Label(path_frame, foreground="#ff4444")
        self._lbl_out  = ttk.Label(path_frame, foreground="#4ec9b0")
        self._lbl_sd.pack(anchor="w")
        self._lbl_pr.pack(anchor="w")
        self._lbl_out.pack(anchor="w")
        for key in ("sourcedata", "project_root"):
            cfg[key].trace_add("write", lambda *_: self._update_labels())
        self._update_labels()

        # ── Subject selection ──────────────────────────────────────────────────
        sel_frame = ttk.LabelFrame(self, text="Subject selection", padding=(8, 4))
        sel_frame.pack(fill="x", pady=(0, 8))

        self._sel_mode = tk.StringVar(value="specific")
        ttk.Radiobutton(sel_frame, text="Check ALL subjects (SubjectListBIDS.txt)",
                        variable=self._sel_mode, value="all",
                        command=self._on_mode_change).pack(anchor="w")

        sp_row = ttk.Frame(sel_frame)
        sp_row.pack(anchor="w", fill="x", pady=(4, 0))
        ttk.Radiobutton(sp_row, text="Single subject:",
                        variable=self._sel_mode, value="specific",
                        command=self._on_mode_change).pack(side="left")
        self._subj_var = tk.StringVar()
        self._subj_combo = ttk.Combobox(sp_row, textvariable=self._subj_var,
                                        width=36, state="readonly")
        self._subj_combo.pack(side="left", padx=(6, 4))
        ttk.Button(sp_row, text="↻ Scan", command=self._scan_subjects).pack(side="left")

        ttk.Label(sel_frame,
                  text="(BIDS IDs scanned from sourcedata; or type a subject ID directly)",
                  foreground="gray").pack(anchor="w", pady=(4, 0))

        # ── Steps info ────────────────────────────────────────────────────────
        info_frame = ttk.LabelFrame(self, text="QC steps covered", padding=(8, 4))
        info_frame.pack(fill="x", pady=(0, 8))
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location(
                "qc_snapshots", str(self._QC_SCRIPT))
            _qcmod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_qcmod)
            _steps = _qcmod.STEPS
        except Exception:
            _steps = []
        if _steps:
            for _num, _name, _desc in _steps:
                ttk.Label(info_frame,
                          text=f"  {_num}  {_desc}",
                          foreground="#9cdcfe").pack(anchor="w")
        else:
            ttk.Label(info_frame,
                      text="(could not load step list — qc_snapshots.py not found)",
                      foreground="gray").pack(anchor="w")

        # ── Manifest viewer ───────────────────────────────────────────────────
        mf = ttk.LabelFrame(self, text="QC manifest  (qc_manifest.json)", padding=(8, 4))
        mf.pack(fill="x", pady=(0, 8))
        mf_row = ttk.Frame(mf); mf_row.pack(fill="x")
        self._manifest_lbl = ttk.Label(mf_row, foreground="#6a6a6a",
                                       text="(not yet generated)")
        self._manifest_lbl.pack(side="left", fill="x", expand=True)
        ttk.Button(mf_row, text="↻ Refresh status",
                   command=self._refresh_manifest).pack(side="right")

        # Treeview showing per-subject step status
        tv_frame = ttk.Frame(self)
        tv_frame.pack(fill="both", expand=True, pady=(0, 8))
        cols = ("subject", "steps_done", "steps_missing", "montage", "last_run")
        self._tv = ttk.Treeview(tv_frame, columns=cols, show="headings", height=8)
        self._tv.heading("subject",       text="Subject")
        self._tv.heading("steps_done",    text="Done")
        self._tv.heading("steps_missing", text="Missing")
        self._tv.heading("montage",       text="Montage")
        self._tv.heading("last_run",      text="Last run")
        self._tv.column("subject",       width=240, stretch=True)
        self._tv.column("steps_done",    width=55,  anchor="center")
        self._tv.column("steps_missing", width=65,  anchor="center")
        self._tv.column("montage",       width=65,  anchor="center")
        self._tv.column("last_run",      width=160)
        self._tv.tag_configure("done",    foreground="#4ec9b0")
        self._tv.tag_configure("partial", foreground="#dcdcaa")
        self._tv.tag_configure("empty",   foreground="#f44747")
        vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=self._tv.yview)
        self._tv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._tv.pack(side="left", fill="both", expand=True)

        # ── Buttons ───────────────────────────────────────────────────────────
        ttk.Separator(self).pack(fill="x", pady=6)

        btn_row = ttk.Frame(self)
        btn_row.pack(anchor="w")
        self._run_btn = ttk.Button(btn_row, text="▶  Generate QC snapshots",
                                   command=self._run)
        self._run_btn.pack(side="left")
        self._stop_btn = ttk.Button(btn_row, text="⏹ Stop", command=self._stop,
                                    state="disabled")
        self._stop_btn.pack(side="left", padx=4)
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=200)
        self._progress.pack(side="left", padx=12)

        # Cross-stage cardinality audit (Task 25)
        self._audit_btn = ttk.Button(btn_row, text="▶  Cardinality audit",
                                     command=self._run_audit)
        self._audit_btn.pack(side="left", padx=(16, 0))

        # Batch provenance (Task 29)
        self._prov_btn = ttk.Button(btn_row, text="▶  Capture provenance",
                                    command=self._run_provenance)
        self._prov_btn.pack(side="left", padx=(8, 0))

        # SDC verification audit (Task 16)
        self._sdc_btn = ttk.Button(btn_row, text="▶  SDC audit",
                                   command=self._run_sdc)
        self._sdc_btn.pack(side="left", padx=(8, 0))

        # Open output folder
        ttk.Button(btn_row, text="Open QC folder",
                   command=self._open_folder).pack(side="left", padx=(16, 0))

        # Initial scan
        self._scan_subjects()
        self._refresh_manifest()

    # ── helpers ───────────────────────────────────────────────────────────────

    def _project_root(self) -> str:
        pr = self._cfg.get("project_root", tk.StringVar()).get().strip()
        if pr:
            return pr
        # Fallback: derive from sourcedata parent
        sd = self._cfg["sourcedata"].get().strip()
        if sd:
            return str(Path(sd).parent)
        return ""

    def _qc_dir(self) -> Path | None:
        pr = self._project_root()
        if pr:
            return Path(pr) / "codes" / "qc"
        return None

    def _update_labels(self):
        sd = self._cfg["sourcedata"].get() or "(not set)"
        pr = self._project_root() or "(not set)"
        qd = str(self._qc_dir() or "(not set)")
        self._lbl_sd.config(text=f"sourcedata:    {sd}")
        self._lbl_pr.config(text=f"project root:  {pr}")
        self._lbl_out.config(text=f"QC output dir: {qd}")

    def _scan_subjects(self):
        """Populate the subject combobox from sourcedata BIDS subjects."""
        sd = self._cfg["sourcedata"].get().strip()
        subjects = []
        if sd and os.path.isdir(sd):
            subjects = sorted(
                d.name for d in Path(sd).iterdir()
                if d.is_dir() and d.name.startswith("sub-")
            )
        # Also try SubjectListBIDS.txt
        if not subjects:
            p = self._cfg.get("subjlist_bids", tk.StringVar()).get().strip()
            if p and os.path.isfile(p):
                with open(p) as f:
                    subjects = [ln.strip() for ln in f if ln.strip()]
        self._subj_combo["values"] = subjects
        if subjects and not self._subj_var.get():
            self._subj_var.set(subjects[0])

    def _on_mode_change(self):
        state = "readonly" if self._sel_mode.get() == "specific" else "disabled"
        self._subj_combo.config(state=state)

    def _refresh_manifest(self):
        """Load qc_manifest.json and populate the status treeview."""
        self._tv.delete(*self._tv.get_children())
        qd = self._qc_dir()
        if qd is None:
            self._manifest_lbl.config(text="(project root not set)")
            return
        mpath = qd / "qc_manifest.json"
        self._manifest_lbl.config(text=str(mpath) if mpath.exists() else "(not yet generated)")
        if not mpath.is_file():
            return
        try:
            with open(mpath) as f:
                manifest = json.load(f)
        except Exception:
            return
        for subj, rec in sorted(manifest.items()):
            steps = rec.get("steps", {})
            done    = sum(1 for s in steps.values() if s.get("status") == "done")
            missing = sum(1 for s in steps.values() if s.get("status") == "missing")
            mont    = rec.get("pipeline_montage", {}).get("status", "?")
            ts      = rec.get("generated", "")[:16]
            total   = len(steps)
            if done == total and total > 0:
                tag = "done"
            elif done == 0:
                tag = "empty"
            else:
                tag = "partial"
            self._tv.insert("", "end", values=(
                subj,
                f"{done}/{total}",
                str(missing),
                "✓" if mont == "done" else "·",
                ts,
            ), tags=(tag,))

    def _subjects_to_run(self) -> list:
        """Return the list of BIDS subject IDs to process."""
        if self._sel_mode.get() == "all":
            # Try SubjectListBIDS.txt first
            p = self._cfg.get("subjlist_bids", tk.StringVar()).get().strip()
            if p and os.path.isfile(p):
                with open(p) as f:
                    return [ln.strip() for ln in f if ln.strip()]
            # Fall back to scanning sourcedata
            sd = self._cfg["sourcedata"].get().strip()
            if sd and os.path.isdir(sd):
                return sorted(
                    d.name for d in Path(sd).iterdir()
                    if d.is_dir() and d.name.startswith("sub-")
                )
            return []
        else:
            s = self._subj_var.get().strip()
            return [s] if s else []

    def _run(self):
        if not self._QC_SCRIPT.is_file():
            messagebox.showerror(
                "Error",
                f"QC script not found:\n{self._QC_SCRIPT}")
            return

        sourcedata = self._cfg["sourcedata"].get().strip()
        if not sourcedata or not os.path.isdir(sourcedata):
            messagebox.showerror("Error", "Set the sourcedata path in Setup.")
            return

        pr = self._project_root()
        if not pr:
            messagebox.showerror("Error",
                "Could not determine the project root. "
                "Set the project folder in the sidebar.")
            return

        subjects = self._subjects_to_run()
        if not subjects:
            messagebox.showerror("Error",
                "No subjects found. Check SubjectListBIDS.txt or scan sourcedata.")
            return

        python = sys.executable

        # Build command: one invocation with all subject IDs
        cmd = [python, str(self._QC_SCRIPT), pr, sourcedata] + subjects

        self._console.separator()
        mode = "all subjects" if self._sel_mode.get() == "all" else subjects[0]
        self._console.append(
            f"[QC] Generating snapshots for: {mode}  ({len(subjects)} subject(s))",
            "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._audit_btn.config(state="disabled")
        self._prov_btn.config(state="disabled")
        self._sdc_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._progress.start(10)
        self._status.set("QC snapshot generation running…")

        self._runner.run(
            cmd=cmd,
            cwd=pr,
            on_line=self._on_line,
            on_done=self._done,
        )

    def _run_audit(self):
        """Run the cross-stage cardinality audit (Task 25). Audit only — never fails."""
        if not self._AUDIT_SCRIPT.is_file():
            messagebox.showerror("Error", f"Audit script not found:\n{self._AUDIT_SCRIPT}")
            return
        sourcedata = self._cfg["sourcedata"].get().strip()
        if not sourcedata or not os.path.isdir(sourcedata):
            messagebox.showerror("Error", "Set the sourcedata path in Setup."); return
        subjects = self._subjects_to_run()
        if not subjects:
            messagebox.showerror("Error",
                "No subjects found. Check SubjectListBIDS.txt or scan sourcedata."); return
        pr = self._project_root()
        cmd = [sys.executable, str(self._AUDIT_SCRIPT), sourcedata] + subjects
        if pr:
            cmd += ["--out", str(Path(pr) / "codes" / "qc")]

        self._console.separator()
        self._console.append(
            f"[audit] Cardinality audit for {len(subjects)} subject(s)…", "info")
        self._console.separator()
        self._run_btn.config(state="disabled")
        self._audit_btn.config(state="disabled")
        self._prov_btn.config(state="disabled")
        self._sdc_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._progress.start(10)
        self._status.set("Cardinality audit running…")
        self._runner.run(cmd=cmd, cwd=pr or sourcedata,
                         on_line=self._on_line, on_done=self._audit_done)

    def _audit_done(self, rc: int):
        self._progress.stop()
        self._run_btn.config(state="normal")
        self._audit_btn.config(state="normal")
        self._prov_btn.config(state="normal")
        self._sdc_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        if rc == 0:
            self._status.set("Cardinality audit complete ✓")
            self._console.append(
                "[audit] Finished. See codes/qc/group_cardinality_audit.md", "ok")
        else:
            self._status.set(f"Cardinality audit failed (exit {rc})")
            self._console.append(f"[audit] Failed (exit {rc}).", "error")

    def _run_provenance(self):
        """Capture a batch analysis-provenance record (Task 29). Record only."""
        if not self._PROV_SCRIPT.is_file():
            messagebox.showerror("Error", f"Provenance script not found:\n{self._PROV_SCRIPT}")
            return
        pr = self._project_root()
        if not pr:
            messagebox.showerror("Error", "Set the project folder (sidebar) first."); return
        # Run under the configured analysis interpreter so package versions match.
        py = self._cfg["python_exe"].get().strip() or sys.executable
        out = str(Path(pr) / "codes" / "qc" / "provenance")
        cmd = [py, str(self._PROV_SCRIPT), "--out", out,
               "--repo", str(SCRIPTS_ROOT), "--python", py]
        for flag, key in (("--fmriprep-simg", "fmriprep"), ("--spm-dir", "spm_dir"),
                          ("--matlab", "matlab_exe"), ("--retro-code", "retro_code"),
                          ("--matlab-code", "matlab_code")):
            val = self._cfg[key].get().strip() if key in self._cfg else ""
            if val:
                cmd += [flag, val]

        self._console.separator()
        self._console.append("[provenance] Capturing analysis environment "
                             "(launches MATLAB for SPM/MATLAB version — may take ~1 min)…", "info")
        self._console.separator()
        self._run_btn.config(state="disabled")
        self._audit_btn.config(state="disabled")
        self._prov_btn.config(state="disabled")
        self._sdc_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._progress.start(10)
        self._status.set("Capturing provenance…")
        self._runner.run(cmd=cmd, cwd=pr, on_line=self._on_line,
                         on_done=self._provenance_done)

    def _provenance_done(self, rc: int):
        self._progress.stop()
        self._run_btn.config(state="normal")
        self._audit_btn.config(state="normal")
        self._prov_btn.config(state="normal")
        self._sdc_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        if rc == 0:
            self._status.set("Provenance captured ✓")
            self._console.append(
                "[provenance] Finished. See codes/qc/provenance/provenance_latest.json", "ok")
        else:
            self._status.set(f"Provenance failed (exit {rc})")
            self._console.append(f"[provenance] Failed (exit {rc}).", "error")

    def _run_sdc(self):
        """SDC verification audit (Task 16). Audit only — flag + log, never fails."""
        if not self._SDC_SCRIPT.is_file():
            messagebox.showerror("Error", f"SDC audit script not found:\n{self._SDC_SCRIPT}")
            return
        sourcedata = self._cfg["sourcedata"].get().strip()
        if not sourcedata or not os.path.isdir(sourcedata):
            messagebox.showerror("Error", "Set the sourcedata path in Setup."); return
        subjects = self._subjects_to_run()
        if not subjects:
            messagebox.showerror("Error",
                "No subjects found. Check SubjectListBIDS.txt or scan sourcedata."); return
        fmriprep = str(Path(sourcedata) / "derivatives" / "fmriprep")
        # --bids = raw sourcedata: its fmap/ JSONs carry the IntendedFor links
        # (the corrected-BIDS symlinks the same fmap files).
        cmd = [sys.executable, str(self._SDC_SCRIPT), fmriprep] + subjects + ["--bids", sourcedata]
        pr = self._project_root()
        if pr:
            cmd += ["--out", str(Path(pr) / "codes" / "qc")]

        self._console.separator()
        self._console.append(
            f"[sdc] SDC verification audit for {len(subjects)} subject(s)…", "info")
        self._console.separator()
        self._run_btn.config(state="disabled")
        self._audit_btn.config(state="disabled")
        self._prov_btn.config(state="disabled")
        self._sdc_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._progress.start(10)
        self._status.set("SDC audit running…")
        self._runner.run(cmd=cmd, cwd=pr or sourcedata,
                         on_line=self._on_line, on_done=self._sdc_done)

    def _sdc_done(self, rc: int):
        self._progress.stop()
        self._run_btn.config(state="normal")
        self._audit_btn.config(state="normal")
        self._prov_btn.config(state="normal")
        self._sdc_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        if rc == 0:
            self._status.set("SDC audit complete ✓")
            self._console.append(
                "[sdc] Finished. See codes/qc/group_sdc_audit.md", "ok")
        else:
            self._status.set(f"SDC audit failed (exit {rc})")
            self._console.append(f"[sdc] Failed (exit {rc}).", "error")

    def _on_line(self, line: str):
        self._console.append(line)

    def _done(self, rc: int):
        self._progress.stop()
        self._run_btn.config(state="normal")
        self._audit_btn.config(state="normal")
        self._prov_btn.config(state="normal")
        self._sdc_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        if rc == 0:
            self._status.set("QC snapshots generated ✓")
            self._console.append("[QC] Finished successfully.", "ok")
        else:
            self._status.set(f"QC generation failed (exit {rc})")
            self._console.append(f"[QC] Failed (exit {rc}).", "error")
        # Refresh the manifest table regardless
        self._refresh_manifest()

    def _stop(self):
        self._runner.stop()

    def _open_folder(self):
        qd = self._qc_dir()
        if qd is None or not qd.is_dir():
            messagebox.showinfo("QC folder",
                f"QC output folder does not exist yet:\n{qd}\n\n"
                "Run 'Generate QC snapshots' first.")
            return
        import subprocess
        try:
            subprocess.Popen(["open", str(qd)])
        except Exception:
            messagebox.showinfo("QC folder", str(qd))


class _PhysioSetupTab(ttk.Frame):
    """Paths, subject, and .mat format selector shared by all physio pipeline steps."""

    def __init__(self, parent, cfg: dict,
                 mat_var: tk.StringVar, subj_var: tk.StringVar,
                 physioparse_var: tk.StringVar, work_var: tk.StringVar,
                 fmt_var: tk.StringVar, oldname_var: tk.StringVar, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg     = cfg
        self._mat_var = mat_var
        self._subj    = subj_var
        self._pp_var  = physioparse_var
        self._work    = work_var
        self._fmt     = fmt_var
        self._oldname = oldname_var

        ttk.Label(self, text="Physio Pipeline Setup",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Configure paths once; all pipeline steps share them.\n"
                  "MAT file is the only input that must be provided manually."),
            foreground="gray", wraplength=580,
        ).pack(anchor="w", pady=(0, 10))

        # ── .mat file (manual) ─────────────────────────────────────────────────
        mat_frame = ttk.LabelFrame(self, text=".mat file  (LabChart export — provided manually)", padding=(8, 4))
        mat_frame.pack(fill="x", pady=(0, 8))
        PathRow(mat_frame, ".mat file:", mode="file",
                filetypes=[("MATLAB", "*.mat"), ("All", "*.*")],
                var=self._mat_var, label_width=16).pack(fill="x")

        # ── MAT format ─────────────────────────────────────────────────────────
        fmt_frame = ttk.LabelFrame(self, text=".mat format  (check in MATLAB: fieldnames(load(file)))", padding=(8, 6))
        fmt_frame.pack(fill="x", pady=(0, 8))
        ttk.Radiobutton(
            fmt_frame, text="Classic  — fields: data / datastart / dataend",
            variable=self._fmt, value="classic",
        ).pack(anchor="w")
        ttk.Radiobutton(
            fmt_frame, text="Block1   — field: data_block1  (4 × N array, newer LabChart)",
            variable=self._fmt, value="block1",
        ).pack(anchor="w")
        ttk.Label(fmt_frame,
                  text="Affects Steps 1 and 3 (different scripts per format). Steps 2 and 4 auto-detect.",
                  foreground="gray").pack(anchor="w", pady=(4, 0))

        # ── Subject ────────────────────────────────────────────────────────────
        subj_frame = ttk.LabelFrame(self, text="Subject", padding=(8, 4))
        subj_frame.pack(fill="x", pady=(0, 8))
        subj_row = ttk.Frame(subj_frame)
        subj_row.pack(fill="x")
        ttk.Label(subj_row, text="BIDS subject ID:", width=18, anchor="w").pack(side="left")
        self._combo = ttk.Combobox(subj_row, textvariable=self._subj, width=32)
        self._combo.pack(side="left", padx=(0, 6))
        ttk.Button(subj_row, text="↻ Scan", command=self._scan_subjects).pack(side="left")
        ttk.Label(subj_frame, text="(e.g. sub-7T1019HC042726 — auto-scanned from sourcedata)",
                  foreground="gray").pack(anchor="w", pady=(2, 0))

        # Old (.heudiconv) name — used to locate dicominfo_ses-01.tsv.
        # .heudiconv keeps the ORIGINAL SubjectList.txt name (with underscores),
        # not the BIDS name, so it must be selected/confirmed here.
        old_row = ttk.Frame(subj_frame)
        old_row.pack(fill="x", pady=(6, 0))
        ttk.Label(old_row, text="Old name (.heudiconv):", width=18, anchor="w").pack(side="left")
        self._old_combo = ttk.Combobox(old_row, textvariable=self._oldname, width=32)
        self._old_combo.pack(side="left", padx=(0, 6))
        ttk.Button(old_row, text="↻ Scan", command=self._scan_heudiconv).pack(side="left")
        ttk.Label(subj_frame,
                  text="(e.g. 7T1019HC_042726 — the SubjectList.txt name; locates "
                       "sourcedata/.heudiconv/<name>/info/dicominfo_ses-01.tsv)",
                  foreground="gray", wraplength=560).pack(anchor="w", pady=(2, 0))
        # Auto-pick the matching .heudiconv folder when the BIDS subject changes
        self._subj.trace_add("write", lambda *_: self._autopick_oldname())

        # ── Paths ──────────────────────────────────────────────────────────────
        paths_frame = ttk.LabelFrame(self, text="Paths", padding=(8, 4))
        paths_frame.pack(fill="x", pady=(0, 8))

        PathRow(paths_frame, "physioparse code:", mode="dir",
                var=self._pp_var, label_width=20).pack(fill="x", pady=2)

        out_row = ttk.Frame(paths_frame)
        out_row.pack(fill="x", pady=2)
        ttk.Label(out_row, text="Output (auto):", width=20, anchor="w").pack(side="left")
        self._work_lbl = ttk.Label(out_row, foreground="#ff4444")
        self._work_lbl.pack(side="left")

        self._subj.trace_add("write", lambda *_: self._update_work())
        cfg["sourcedata"].trace_add("write", lambda *_: self._update_work())
        self._update_work()

        cfg["sourcedata"].trace_add("write", lambda *_: self._scan_subjects())

    def _scan_subjects(self):
        sd = self._cfg["sourcedata"].get().strip()
        if not sd or not os.path.isdir(sd):
            return
        subjects = sorted(
            d.name for d in Path(sd).iterdir()
            if d.is_dir() and d.name.startswith("sub-")
        )
        self._combo["values"] = subjects
        if subjects and not self._subj.get():
            self._subj.set(subjects[0])
        self._scan_heudiconv()

    def _heudiconv_names(self):
        sd = self._cfg["sourcedata"].get().strip()
        root = Path(sd) / ".heudiconv" if sd else None
        if not root or not root.is_dir():
            return []
        return sorted(d.name for d in root.iterdir()
                      if d.is_dir() and not d.name.startswith("."))

    def _scan_heudiconv(self):
        names = self._heudiconv_names()
        self._old_combo["values"] = names
        self._autopick_oldname()

    def _autopick_oldname(self):
        """If the old name isn't a valid .heudiconv folder, pick the one whose
        normalised form (strip '_', lowercase) matches the BIDS subject."""
        names = self._heudiconv_names()
        cur = self._oldname.get().strip()
        if cur in names:
            return  # user already has a valid selection
        subj = self._subj.get().strip()
        if not subj:
            return
        bids_norm = subj.replace("sub-", "").replace("_", "").lower()
        for n in names:
            if n.replace("_", "").lower() == bids_norm:
                self._oldname.set(n)
                return

    def _update_work(self):
        sd   = self._cfg["sourcedata"].get().strip()
        subj = self._subj.get().strip()
        if sd and subj:
            work = str(Path(sd) / "derivatives" / "physio" / subj)
            self._work.set(work)
            self._work_lbl.config(text=work)
        else:
            self._work_lbl.config(text="(set sourcedata + subject)")


class _PhysioStepTab(ttk.Frame):
    """Generic run-tab for a single physioparse step."""

    def __init__(self, parent, step_num: int, title: str, description: str,
                 console: Console, status_var: tk.StringVar, runner: ScriptRunner,
                 build_cmd_fn, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._console      = console
        self._status       = status_var
        self._runner       = runner
        self._build_cmd_fn = build_cmd_fn
        self._step_num     = step_num

        ttk.Label(self, text=title,
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(self, text=description, foreground="gray",
                  wraplength=580).pack(anchor="w", pady=(0, 12))

        ttk.Separator(self).pack(fill="x", pady=(0, 8))

        btn_row = ttk.Frame(self)
        btn_row.pack(anchor="w")
        self._run_btn = ttk.Button(btn_row, text=f"▶  Run Step {step_num}", command=self._run)
        self._run_btn.pack(side="left")
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=220)
        self._progress.pack(side="left", padx=12)

    def _run(self):
        try:
            cmd, cwd = self._build_cmd_fn()
        except ValueError as e:
            messagebox.showerror("Error", str(e))
            return

        self._console.separator()
        self._console.append(f"[Physio Step {self._step_num}] Starting…", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set(f"Physio step {self._step_num} running…")

        self._runner.run(cmd=cmd, cwd=cwd,
                        on_line=self._console.append,
                        on_done=self._done)

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        tag = "ok" if rc == 0 else "error"
        msg = "Finished." if rc == 0 else f"Failed (exit {rc})."
        self._status.set(f"Physio step {self._step_num} {msg}")
        self._console.append(f"[Physio Step {self._step_num}] {msg}", tag)


class PhysioparsePanel(ttk.Frame):
    """Run the physioparse pipeline (steps 1-4) for a single tVNS subject."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=(6, 6), **kwargs)

        # ── Shared state vars ─────────────────────────────────────────────────
        self._mat_var     = tk.StringVar()
        self._subj_var    = tk.StringVar()
        self._pp_var      = tk.StringVar(
            value=str(SCRIPTS_ROOT / "utility" / "physioparse"))
        self._work_var    = tk.StringVar()
        self._fmt_var     = tk.StringVar(value="classic")
        self._oldname_var = tk.StringVar()   # .heudiconv folder name (old SubjectList name)
        self._cfg         = cfg

        ttk.Label(self, text="Step 02 — Physioparse",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 6))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        # Setup tab
        setup_tab = _PhysioSetupTab(nb, cfg, self._mat_var, self._subj_var,
                                    self._pp_var, self._work_var, self._fmt_var,
                                    self._oldname_var)

        # Step 1 – pseudotime mapping
        s1_tab = _PhysioStepTab(
            nb, 1, "Step 1 — Pseudotime Mapping",
            ("Reads AcquisitionTime from BIDS JSON sidecars, detects the first MR trigger\n"
             "in the MRTRIG channel, and anchors all sequences to a common time base.\n"
             "Output: pseudotime_mapping.json in the working directory."),
            console, status_var, runner,
            self._build_step1_cmd,
        )

        # Step 2 – quality visualisation
        s2_tab = _PhysioStepTab(
            nb, 2, "Step 2 — Quality Visualisation",
            ("Plots all four physiological channels with colour-coded sequence regions\n"
             "overlaid on a shared timeline.  Requires Step 1 to have been run first.\n"
             "Output: pseudotime_plot.png + pseudotime_plot_stats.png."),
            console, status_var, runner,
            self._build_step2_cmd,
        )

        # Step 3 – parse segments
        s3_tab = _PhysioStepTab(
            nb, 3, "Step 3 — Parse Segments",
            ("Cuts the full-session .mat into one .mat per BOLD run using the pseudotime\n"
             "mapping and sequence durations from dicominfo_ses-01.tsv.\n"
             "Output: parsed/task-*_run-*.mat  +  parsed/plots/."),
            console, status_var, runner,
            self._build_step3_cmd,
        )

        # Step 4 – QC (optional)
        s4_tab = _PhysioStepTab(
            nb, 4, "Step 4 — Signal QC  (optional)",
            ("Optional. Computes SNR, respiration rate, heart rate, and MR trigger jitter.\n"
             "Useful to flag channels with poor quality before running RETROICOR.\n"
             "Output: qc/physio_qc_plot.png  +  qc/physio_qc_metrics.csv."),
            console, status_var, runner,
            self._build_step4_cmd,
        )

        nb.add(setup_tab, text="  Setup  ")
        nb.add(s1_tab,    text="  Step 1 — Pseudotime  ")
        nb.add(s2_tab,    text="  Step 2 — Quality viz  ")
        nb.add(s3_tab,    text="  Step 3 — Parse  ")
        nb.add(s4_tab,    text="  Step 4 — QC (optional)  ")

        # Trigger initial subject scan
        sd = cfg["sourcedata"].get().strip()
        if sd and os.path.isdir(sd):
            setup_tab._scan_subjects()

    def _python(self):
        """Prefer the conda Neuroimaging python that launched the GUI."""
        return sys.executable

    def _validate(self):
        mat  = self._mat_var.get().strip()
        subj = self._subj_var.get().strip()
        pp   = self._pp_var.get().strip()
        work = self._work_var.get().strip()
        if not mat or not os.path.isfile(mat):
            raise ValueError(f".mat file not found:\n{mat}")
        if not subj:
            raise ValueError("Select a BIDS subject in the Setup tab.")
        if not pp or not os.path.isdir(pp):
            raise ValueError(f"physioparse directory not found:\n{pp}")
        if not work:
            raise ValueError("Working directory is empty — set sourcedata + subject.")
        return mat, subj, pp, work

    def _bids_func_dir(self, subj):
        """BIDS func dir holding the *_bold.json sidecars for this subject."""
        sd = self._cfg["sourcedata"].get().strip()
        if not sd:
            raise ValueError("Set the sourcedata path in Setup.")
        d = str(Path(sd) / subj / "ses-01" / "func")
        if not os.path.isdir(d):
            raise ValueError(
                f"BIDS func directory not found:\n{d}\n"
                "Run step01 (heudiconv BIDS conversion) first.")
        return d

    def _find_dicominfo(self, subj):
        """Locate dicominfo_ses-*.tsv in sourcedata/.heudiconv/<old_name>/info/.

        .heudiconv keeps the OLD SubjectList.txt name (e.g. 7T1019HC_042726),
        not the BIDS name. The folder is taken from the "Old name (.heudiconv)"
        field in Setup if set; otherwise it's matched to the BIDS subject by
        normalising both (strip '_', lowercase).

        heudiconv run with sessions (-ss 01) names the file dicominfo_ses-01.tsv,
        so we glob dicominfo*.tsv and prefer the ses-01 one. Returns '' if none.
        """
        sd = self._cfg["sourcedata"].get().strip()
        if not sd:
            return ""
        heudiconv_root = Path(sd) / ".heudiconv"
        if not heudiconv_root.is_dir():
            return ""

        # 1. Explicit old name selected in Setup
        candidate_dirs = []
        old = self._oldname_var.get().strip()
        if old:
            candidate_dirs.append(heudiconv_root / old)

        # 2. Fall back to normalised-name matching
        if not candidate_dirs:
            bids_norm = subj.replace("sub-", "").replace("_", "").lower()
            for hdir in heudiconv_root.iterdir():
                if hdir.is_dir() and hdir.name.replace("_", "").lower() == bids_norm:
                    candidate_dirs.append(hdir)

        for d in candidate_dirs:
            info = d / "info"
            if not info.is_dir():
                continue
            hits = sorted(info.glob("dicominfo*.tsv"))
            if hits:
                ses = [h for h in hits if "ses-01" in h.name]
                return str(ses[0] if ses else hits[0])
        return ""

    def _build_step1_cmd(self):
        mat, subj, pp, work = self._validate()

        # JSONs are read directly from the BIDS func dir (no copying):
        #   <sourcedata>/<subject>/ses-01/func
        sd = self._cfg["sourcedata"].get().strip()
        if not sd:
            raise ValueError("Set the sourcedata path in Setup.")
        json_dir = str(Path(sd) / subj / "ses-01" / "func")
        if not os.path.isdir(json_dir):
            raise ValueError(
                f"BIDS func directory not found:\n{json_dir}\n"
                "Run step01 (heudiconv BIDS conversion) for this subject first.")

        # Classic vs Block1: different bash scripts
        script = (
            "step01_times_acquisition.sh"
            if self._fmt_var.get() == "classic"
            else "step01b_times_acquisition_block1.sh"
        )
        # New signature: <json_dir> <mat_file (full path)> <output_dir> [python]
        cmd = ["bash", str(Path(pp) / script),
               json_dir, mat, work, self._python()]
        return cmd, work

    def _build_step2_cmd(self):
        mat, subj, pp, work = self._validate()
        mapping = str(Path(work) / "pseudotime_mapping.json")
        if not os.path.isfile(mapping):
            raise ValueError("pseudotime_mapping.json not found.\nRun Step 1 first.")
        json_dir  = self._bids_func_dir(subj)
        dicominfo = self._find_dicominfo(subj)
        if not dicominfo:
            raise ValueError(self._dicominfo_err(subj))
        # Classic vs Block1: different plot scripts
        script_name = (
            "step02_plot_pseudotime_quality.py"
            if self._fmt_var.get() == "classic"
            else "step02b_plot_pseudotime_quality_block1.py"
        )
        # The .mat is the manually-selected file (full path) — not copied into work.
        cmd = [self._python(), str(Path(pp) / script_name),
               mat, mapping,
               str(Path(work) / "pseudotime_plot.png"),
               "--json-dir", json_dir,
               "--dicominfo", dicominfo]
        return cmd, work

    def _build_step3_cmd(self):
        mat, subj, pp, work = self._validate()
        mapping = str(Path(work) / "pseudotime_mapping.json")
        if not os.path.isfile(mapping):
            raise ValueError("pseudotime_mapping.json not found.\nRun Step 1 first.")
        parsed_dir = str(Path(work) / "parsed")
        Path(parsed_dir).mkdir(parents=True, exist_ok=True)
        json_dir  = self._bids_func_dir(subj)
        dicominfo = self._find_dicominfo(subj)
        if not dicominfo:
            raise ValueError(self._dicominfo_err(subj))
        # Classic vs Block1: different Python scripts
        script_name = (
            "step03_parse.py"
            if self._fmt_var.get() == "classic"
            else "step03b_parse_block1.py"
        )
        cmd = [self._python(), str(Path(pp) / script_name), work, parsed_dir,
               "--json-dir", json_dir,
               "--dicominfo", dicominfo]
        return cmd, work

    def _dicominfo_err(self, subj):
        sd = self._cfg["sourcedata"].get().strip()
        old = self._oldname_var.get().strip()
        return (
            "dicominfo_ses-01.tsv could not be located.\n\n"
            f"Looked under: {sd}/.heudiconv/\n"
            f"Old name (.heudiconv): {old or '(not set — using normalised match)'}\n\n"
            "Set the correct 'Old name (.heudiconv)' in the Physio Setup tab "
            "(the SubjectList.txt name, e.g. 7T1019HC_042726). Without it the "
            "sequence durations fall back to 120 s and the QC timeline is wrong.")

    def _build_step4_cmd(self):
        mat, subj, pp, work = self._validate()
        mapping = str(Path(work) / "pseudotime_mapping.json")
        if not os.path.isfile(mapping):
            raise ValueError("pseudotime_mapping.json not found.\nRun Step 1 first.")
        qc_dir = str(Path(work) / "qc")
        Path(qc_dir).mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{pp}{os.pathsep}{env.get('PYTHONPATH', '')}"
        script = str(Path(pp) / "step04_qc.py")
        cmd = [self._python(), script, work, qc_dir]
        return cmd, work


# ── Step 03 — Filter physio + R-DECO launcher ─────────────────────────────────

class _FilterPhysioTab(ttk.Frame):
    """Run preproc_filter_per_sequence.m on physioparse parsed mats."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner,
                 preproc_dir_var: tk.StringVar, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg           = cfg
        self._console       = console
        self._status        = status_var
        self._runner        = runner
        self._preproc_dir   = preproc_dir_var  # shared with R-DECO tab

        ttk.Label(self, text="Filter Physio for RETROICOR",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Filters RPIEZO (cardiac) per-sequence — output is ready for R-DECO.\n"
                  "Each parsed mat becomes a *_filtered.mat (physio struct) and a\n"
                  "*_rpiezo.mat (plain signal, load this in R-DECO)."),
            foreground="gray", wraplength=580,
        ).pack(anchor="w", pady=(0, 10))

        # ── Paths ──────────────────────────────────────────────────────────────
        paths = ttk.LabelFrame(self, text="Paths", padding=(10, 6))
        paths.pack(fill="x", pady=(0, 8))

        self._subj_var    = tk.StringVar()
        self._parsed_var  = tk.StringVar()
        self._output_var  = tk.StringVar()
        self._matlab_var  = cfg["matlab_exe"]
        self._mcode_var   = cfg["matlab_code"]

        subj_row = ttk.Frame(paths)
        subj_row.pack(fill="x", pady=2)
        ttk.Label(subj_row, text="BIDS subject:", width=18, anchor="w").pack(side="left")
        self._subj_combo = ttk.Combobox(subj_row, textvariable=self._subj_var, width=32)
        self._subj_combo.pack(side="left", padx=(0, 6))
        ttk.Button(subj_row, text="↻", width=3, command=self._scan_subjects).pack(side="left")

        PathRow(paths, "Parsed mats dir:", mode="dir",
                var=self._parsed_var, label_width=18,
                on_change=lambda _: None).pack(fill="x", pady=2)
        PathRow(paths, "Output dir:", mode="dir",
                var=self._output_var, label_width=18).pack(fill="x", pady=2)
        PathRow(paths, "MATLAB exe:", mode="file",
                filetypes=[("MATLAB", "matlab*"), ("All", "*.*")],
                var=self._matlab_var, label_width=18).pack(fill="x", pady=2)
        PathRow(paths, "MATLAB code dir:", mode="dir",
                var=self._mcode_var, label_width=18).pack(fill="x", pady=2)

        # Auto-derive paths when subject changes
        self._subj_var.trace_add("write", lambda *_: self._derive_paths())
        cfg["sourcedata"].trace_add("write", lambda *_: self._scan_subjects())

        # ── Filter parameters ──────────────────────────────────────────────────
        filt = ttk.LabelFrame(self, text="Filter parameters", padding=(10, 6))
        filt.pack(fill="x", pady=(0, 8))

        def _entry_row(parent, label, var, width=8):
            r = ttk.Frame(parent)
            r.pack(fill="x", pady=2)
            ttk.Label(r, text=label, width=30, anchor="w").pack(side="left")
            ttk.Entry(r, textvariable=var, width=width).pack(side="left")

        self._sr_var  = tk.StringVar(value="1000")
        self._hp_var  = tk.StringVar(value="0.05")
        self._bpl_var = tk.StringVar(value="0.5")
        self._bph_var = tk.StringVar(value="2.0")

        _entry_row(filt, "Sampling rate (Hz):",        self._sr_var)
        _entry_row(filt, "Highpass cutoff (Hz):",      self._hp_var)
        _entry_row(filt, "Bandpass lower edge (Hz):",  self._bpl_var)
        _entry_row(filt, "Bandpass upper edge (Hz):",  self._bph_var)

        ttk.Separator(self).pack(fill="x", pady=8)

        btn_row = ttk.Frame(self)
        btn_row.pack(anchor="w")
        self._run_btn = ttk.Button(btn_row, text="▶  Run Filter Batch", command=self._run)
        self._run_btn.pack(side="left")
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=220)
        self._progress.pack(side="left", padx=12)

        self._scan_subjects()

    def _scan_subjects(self):
        sd = self._cfg["sourcedata"].get().strip()
        if not sd or not os.path.isdir(sd):
            return
        subjects = sorted(d.name for d in Path(sd).iterdir()
                          if d.is_dir() and d.name.startswith("sub-"))
        self._subj_combo["values"] = subjects
        if subjects and not self._subj_var.get():
            self._subj_var.set(subjects[0])

    def _derive_paths(self):
        sd   = self._cfg["sourcedata"].get().strip()
        subj = self._subj_var.get().strip()
        if sd and subj:
            base = Path(sd) / "derivatives" / "physio" / subj
            self._parsed_var.set(str(base / "parsed"))
            out = str(base / "preprocessed")
            self._output_var.set(out)
            self._preproc_dir.set(out)

    def _run(self):
        subj    = self._subj_var.get().strip()
        parsed  = self._parsed_var.get().strip()
        out     = self._output_var.get().strip()
        matlab  = self._matlab_var.get().strip()
        mcode   = self._mcode_var.get().strip()

        if not subj:
            messagebox.showerror("Error", "Select a BIDS subject."); return
        if not parsed or not os.path.isdir(parsed):
            messagebox.showerror("Error", f"Parsed mats directory not found:\n{parsed}"); return
        if not out:
            messagebox.showerror("Error", "Set an output directory."); return
        if not mcode or not os.path.isdir(mcode):
            messagebox.showerror("Error", f"MATLAB code directory not found:\n{mcode}"); return

        sr  = self._sr_var.get()  or "1000"
        hp  = self._hp_var.get()  or "0.05"
        bpl = self._bpl_var.get() or "0.5"
        bph = self._bph_var.get() or "2.0"

        matlab_cmd = (
            f"set(0,'DefaultFigureVisible','off'); "
            f"addpath('{mcode}'); "
            f"preproc_filter_per_sequence("
            f"'{parsed}','{out}','{subj}',"
            f"'SR',{sr},'HP',{hp},'BPL',{bpl},'BPH',{bph});"
        )
        cmd = [matlab, "-nodisplay", "-nosplash", "-batch", matlab_cmd]

        self._console.separator()
        self._console.append(f"[Step 03] Filter batch — subject: {subj}", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set("Step 03 filter running…")

        self._runner.run(cmd=cmd, cwd=out if os.path.isdir(out) else "/tmp",
                        on_line=self._console.append, on_done=self._done)

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        if rc == 0:
            self._status.set("Step 03 filter complete ✓")
            self._console.append("[Step 03] Filter batch finished. Open R-DECO tab next.", "ok")
        else:
            self._status.set(f"Step 03 filter failed (exit {rc})")
            self._console.append(f"[Step 03] Filter failed (exit {rc}).", "error")


class _RDecoTab(ttk.Frame):
    """List preprocessed mats; launch R-DECO manually OR run it automatically."""

    def __init__(self, parent, console: Console, status_var: tk.StringVar,
                 preproc_dir_var: tk.StringVar, runner: ScriptRunner = None,
                 cfg: dict = None, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._console     = console
        self._status      = status_var
        self._preproc_dir = preproc_dir_var
        self._runner      = runner
        cfg = cfg or {}

        ttk.Label(self, text="R-DECO — Cardiac R-peak Annotation",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Select a sequence, then either Launch R-DECO (manual GUI) or\n"
                  "Run Auto R-DECO (headless: detect peaks, ectopic removal, remove\n"
                  "doubled beats > HR threshold, save QC image + *_rdeco.mat).\n"
                  "The table tracks which files are done."),
            foreground="gray", wraplength=600,
        ).pack(anchor="w", pady=(0, 10))

        # Paths
        pf = ttk.LabelFrame(self, text="Paths", padding=(10, 6))
        pf.pack(fill="x", pady=(0, 8))
        PathRow(pf, "Preprocessed dir:", mode="dir",
                var=self._preproc_dir, label_width=18,
                on_change=lambda _: self._refresh()).pack(fill="x", pady=2)
        self._rdeco_var   = cfg.get("rdeco_code")  or tk.StringVar(value=str(SCRIPTS_ROOT / "utility" / "r-deco-master"))
        self._mcode_var   = cfg.get("matlab_code") or tk.StringVar(value=str(SCRIPTS_ROOT / "utility" / "matlab_code"))
        self._matlab_var2 = cfg.get("matlab_exe")  or tk.StringVar(value="matlab")
        PathRow(pf, "R-DECO code dir:", mode="dir",
                var=self._rdeco_var, label_width=18).pack(fill="x", pady=2)
        PathRow(pf, "MATLAB code dir:", mode="dir",
                var=self._mcode_var, label_width=18).pack(fill="x", pady=2)
        PathRow(pf, "MATLAB exe:", mode="file",
                filetypes=[("MATLAB", "matlab*"), ("All", "*.*")],
                var=self._matlab_var2, label_width=18).pack(fill="x", pady=2)

        # Auto-analysis parameters
        ap = ttk.LabelFrame(self, text="Auto R-DECO parameters", padding=(10, 6))
        ap.pack(fill="x", pady=(0, 8))
        self._fs_var      = tk.StringVar(value="1000")
        self._envmin_var  = tk.StringVar(value="300")
        self._envmax_var  = tk.StringVar(value="500")
        self._hrmax_var   = tk.StringVar(value="150")
        self._ectopic_var = tk.BooleanVar(value=True)
        self._inverted_var = tk.BooleanVar(value=False)

        def _erow(parent, label, var, w=7):
            r = ttk.Frame(parent); r.pack(side="left", padx=(0, 14))
            ttk.Label(r, text=label).pack(side="left")
            ttk.Entry(r, textvariable=var, width=w).pack(side="left", padx=(4, 0))

        row1 = ttk.Frame(ap); row1.pack(fill="x", pady=2)
        _erow(row1, "Resample to (Hz):", self._fs_var)
        _erow(row1, "Min envelope (ms):", self._envmin_var)
        _erow(row1, "Max envelope (ms):", self._envmax_var)
        row2 = ttk.Frame(ap); row2.pack(fill="x", pady=2)
        _erow(row2, "Delete doubled peaks > HR (bpm):", self._hrmax_var)
        ttk.Checkbutton(row2, text="Ectopic removal", variable=self._ectopic_var).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(row2, text="Inverted signal", variable=self._inverted_var).pack(side="left")

        # File table
        tv_frame = ttk.Frame(self)
        tv_frame.pack(fill="both", expand=True, pady=(0, 8))
        cols = ("file", "filtered", "rpiezo", "rdeco")
        self._tv = ttk.Treeview(tv_frame, columns=cols, show="headings", height=10)
        self._tv.heading("file",     text="Sequence")
        self._tv.heading("filtered", text="Filtered")
        self._tv.heading("rpiezo",   text="RPIEZO")
        self._tv.heading("rdeco",    text="R-DECO done")
        self._tv.column("file",     width=260, stretch=True)
        self._tv.column("filtered", width=70,  anchor="center")
        self._tv.column("rpiezo",   width=70,  anchor="center")
        self._tv.column("rdeco",    width=90,  anchor="center")
        self._tv.tag_configure("done",    foreground="#4ec9b0")
        self._tv.tag_configure("missing", foreground="#f44747")
        self._tv.tag_configure("partial", foreground="#dcdcaa")
        vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=self._tv.yview)
        self._tv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._tv.pack(side="left", fill="both", expand=True)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x")
        ttk.Button(btn_row, text="↻ Refresh",        command=self._refresh).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Launch R-DECO",    command=self._launch).pack(side="left", padx=(0, 6))
        self._auto_btn = ttk.Button(btn_row, text="⚡ Run Auto R-DECO", command=self._run_auto)
        self._auto_btn.pack(side="left", padx=(0, 6))
        self._auto_all_btn = ttk.Button(btn_row, text="⚡⚡ Auto all missing", command=self._run_auto_all)
        self._auto_all_btn.pack(side="left")
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=140)
        self._progress.pack(side="left", padx=10)

        self._preproc_dir.trace_add("write", lambda *_: self._refresh())
        self._refresh()

    def _refresh(self):
        self._tv.delete(*self._tv.get_children())
        d = self._preproc_dir.get().strip()
        if not d or not os.path.isdir(d):
            return
        stems = sorted({
            Path(f).name.replace("_filtered.mat", "").replace("_rpiezo.mat", "").replace("_rdeco.mat", "")
            for f in os.listdir(d)
            if f.endswith(("_filtered.mat", "_rpiezo.mat", "_rdeco.mat"))
        })
        for stem in stems:
            has_filt  = os.path.isfile(os.path.join(d, f"{stem}_filtered.mat"))
            has_piezo = os.path.isfile(os.path.join(d, f"{stem}_rpiezo.mat"))
            has_rdeco = os.path.isfile(os.path.join(d, f"{stem}_rdeco.mat"))
            tag = "done" if has_rdeco else ("partial" if has_filt else "missing")
            self._tv.insert("", "end", iid=stem, text=stem, values=(
                stem,
                "✓" if has_filt  else "·",
                "✓" if has_piezo else "·",
                "✓" if has_rdeco else "·",
            ), tags=(tag,))

    def _launch(self):
        sel = self._tv.selection()
        if not sel:
            messagebox.showwarning("No selection", "Select a sequence from the table first.")
            return
        stem = sel[0]
        d     = self._preproc_dir.get().strip()
        rdeco = self._rdeco_var.get().strip()
        matlab = self._matlab_var2.get().strip()

        if not rdeco or not os.path.isdir(rdeco):
            messagebox.showerror("Error", f"R-DECO directory not found:\n{rdeco}")
            return

        rpiezo_file = os.path.join(d, f"{stem}_rpiezo.mat")
        rdeco_out   = os.path.join(d, f"{stem}_rdeco.mat")

        # Build MATLAB command: open R-DECO GUI
        # We can't automate file loading inside R-DECO, so we print clear instructions.
        matlab_cmd = (
            f"addpath('{rdeco}'); "
            f"disp('=== R-DECO launched ==='); "
            f"disp('Load: {rpiezo_file}'); "
            f"disp('Save output as: {rdeco_out}'); "
            f"R_DECO;"
        )

        self._console.separator()
        self._console.append(f"[R-DECO] Launching for: {stem}", "info")
        self._console.append(f"[R-DECO] Load file:    {rpiezo_file}", "dim")
        self._console.append(f"[R-DECO] Save output:  {rdeco_out}", "dim")
        self._console.separator()

        import subprocess
        try:
            subprocess.Popen([matlab, "-r", matlab_cmd])
            self._status.set(f"R-DECO launched for {stem}")
        except FileNotFoundError:
            messagebox.showerror("Error", f"MATLAB not found: {matlab}")

    # ── Automatic (headless) R-DECO ───────────────────────────────────────────

    def _auto_matlab_cmd(self, stem):
        """Build the MATLAB -batch command for one sequence's auto analysis."""
        d      = self._preproc_dir.get().strip()
        rdeco  = self._rdeco_var.get().strip()
        mcode  = self._mcode_var.get().strip()
        rpiezo = os.path.join(d, f"{stem}_rpiezo.mat")
        out    = os.path.join(d, f"{stem}_rdeco.mat")
        qc     = os.path.join(d, f"{stem}_rdeco_qc.png")
        ect    = "true" if self._ectopic_var.get() else "false"
        inv    = "true" if self._inverted_var.get() else "false"
        return (
            f"set(0,'DefaultFigureVisible','off'); "
            f"addpath('{mcode}'); "
            f"rdeco_auto_analysis('{rpiezo}','{out}','{qc}','{rdeco}',"
            f"'Fs',{self._fs_var.get() or 1000},"
            f"'EnvMin',{self._envmin_var.get() or 300},"
            f"'EnvMax',{self._envmax_var.get() or 500},"
            f"'Ectopic',{ect},"
            f"'HrMaxBpm',{self._hrmax_var.get() or 150},"
            f"'Inverted',{inv});"
        )

    def _validate_auto(self):
        d     = self._preproc_dir.get().strip()
        rdeco = self._rdeco_var.get().strip()
        mcode = self._mcode_var.get().strip()
        if not d or not os.path.isdir(d):
            raise ValueError(f"Preprocessed directory not found:\n{d}")
        if not rdeco or not os.path.isdir(rdeco):
            raise ValueError(f"R-DECO directory not found:\n{rdeco}")
        if not mcode or not os.path.isdir(mcode):
            raise ValueError(f"MATLAB code directory not found:\n{mcode}")

    def _run_auto(self):
        sel = self._tv.selection()
        if not sel:
            messagebox.showwarning("No selection", "Select a sequence from the table first.")
            return
        try:
            self._validate_auto()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return
        self._run_auto_batch([sel[0]])

    def _run_auto_all(self):
        try:
            self._validate_auto()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return
        d = self._preproc_dir.get().strip()
        stems = []
        for iid in self._tv.get_children():
            stem = iid
            if (os.path.isfile(os.path.join(d, f"{stem}_rpiezo.mat"))
                    and not os.path.isfile(os.path.join(d, f"{stem}_rdeco.mat"))):
                stems.append(stem)
        if not stems:
            messagebox.showinfo("Nothing to do",
                                "Every sequence with an RPIEZO file already has an R-DECO result.")
            return
        self._run_auto_batch(stems)

    def _run_auto_batch(self, stems):
        if self._runner is None:
            messagebox.showerror("Error", "No runner available for auto analysis.")
            return
        matlab = self._matlab_var2.get().strip()
        # Chain all selected sequences in one MATLAB session
        body = "\n".join(self._auto_matlab_cmd(s) for s in stems)
        cmd = [matlab, "-nodisplay", "-nosplash", "-batch", body]

        self._console.separator()
        self._console.append(
            f"[Auto R-DECO] {len(stems)} sequence(s): {', '.join(stems)}", "info")
        self._console.separator()

        self._auto_btn.config(state="disabled")
        self._auto_all_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set(f"Auto R-DECO running ({len(stems)})…")

        self._runner.run(
            cmd=cmd, cwd=self._preproc_dir.get().strip() or "/tmp",
            on_line=self._console.append,
            on_done=self._auto_done,
        )

    def _auto_done(self, rc):
        self._progress.stop()
        self._auto_btn.config(state="normal")
        self._auto_all_btn.config(state="normal")
        if rc == 0:
            self._status.set("Auto R-DECO complete ✓")
            self._console.append("[Auto R-DECO] Finished. Review the *_rdeco_qc.png images.", "ok")
        else:
            self._status.set(f"Auto R-DECO failed (exit {rc})")
            self._console.append(f"[Auto R-DECO] Failed (exit {rc}).", "error")
        self._refresh()


class PreprocRdecoPanel(ttk.Frame):
    """Step 03: Filter physio per-sequence mats + R-DECO launcher."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=(6, 6), **kwargs)

        # Shared var: preprocessed dir is set by _FilterPhysioTab and read by _RDecoTab
        preproc_dir_var = tk.StringVar()

        ttk.Label(self, text="Step 03 — Preprocess for RETROICOR",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 6))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        filt_tab  = _FilterPhysioTab(nb, cfg, console, status_var, runner, preproc_dir_var)
        rdeco_tab = _RDecoTab(nb, console, status_var, preproc_dir_var, runner, cfg=cfg)

        nb.add(filt_tab,  text="  Filter Physio  ")
        nb.add(rdeco_tab, text="  R-DECO  ")

        # Sync R-DECO tab when filter tab completes
        nb.bind("<<NotebookTabChanged>>", lambda _: rdeco_tab._refresh())


# ── Step 04 — RETROICOR (native-space physio correction, before fMRIPrep) ─────

class RetroicorPanel(ttk.Frame):
    """Step 04: Generate 1D files + run RETROICOR (native-space physio correction)."""

    _DEFAULTS = {
        "sourcedata":    "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata",
        "matlab_exe":    "matlab",
        "session":       "01",
        "sms":           "1",
        "fs_out":        "40",
        "tr_fallback":   "1.19",
    }

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=(6, 6), **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner

        # ── Vars ──────────────────────────────────────────────────────────────
        self._subj_var    = tk.StringVar()
        self._preproc_var = tk.StringVar()
        self._input_var   = tk.StringVar()
        self._output_var  = tk.StringVar()
        self._matlab_var  = cfg["matlab_exe"]
        self._mcode_var   = cfg["matlab_code"]
        self._retro_var   = cfg["retro_code"]
        self._session_var = tk.StringVar(value=self._DEFAULTS["session"])
        self._sms_var     = tk.StringVar(value=self._DEFAULTS["sms"])
        self._fs_var      = tk.StringVar(value=self._DEFAULTS["fs_out"])
        self._tr_var      = tk.StringVar(value=self._DEFAULTS["tr_fallback"])
        # Cardiac source: "both" (cardiac+resp) or "resp" (respiration-only, bad piezo)
        self._cardiac_var = tk.StringVar(value="both")
        # Per-sequence piezo-QC review state
        self._decision_rows: list = []   # [{task, run, verdict, var}]
        self._thumb_refs: list = []      # keep PhotoImage refs alive (Tk GC)

        ttk.Label(self, text="Step 04 — RETROICOR",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 6))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        nb.add(self._build_cfg_tab(nb),    text="  Configuration  ")
        nb.add(self._build_review_tab(nb), text="  Piezo QC Review  ")
        nb.add(self._build_run_tab(nb),    text="  Run Pipeline  ")

        self._sync_paths()
        self._scan_subjects()
        cfg["sourcedata"].trace_add("write", lambda *_: (self._scan_subjects(), self._sync_paths()))
        self._subj_var.trace_add("write", lambda *_: self._sync_paths())

    # ── Configuration tab ─────────────────────────────────────────────────────

    def _build_cfg_tab(self, parent):
        tab = ttk.Frame(parent, padding=14)

        ttk.Label(tab, text="Subject", font=("Helvetica", 11, "bold")).pack(anchor="w", pady=(0, 4))
        subj_row = ttk.Frame(tab)
        subj_row.pack(fill="x", pady=(0, 8))
        ttk.Label(subj_row, text="BIDS subject:", width=18, anchor="w").pack(side="left")
        self._subj_combo = ttk.Combobox(subj_row, textvariable=self._subj_var, width=32)
        self._subj_combo.pack(side="left", padx=(0, 6))
        ttk.Button(subj_row, text="↻", width=3, command=self._scan_subjects).pack(side="left")

        # Paths
        pf = ttk.LabelFrame(tab, text="Paths", padding=(10, 6))
        pf.pack(fill="x", pady=(0, 8))

        def prow(parent, label, var, mode="dir", ft=None):
            PathRow(parent, label, mode=mode, filetypes=ft or [("All", "*.*")],
                    var=var, label_width=22).pack(fill="x", pady=2)

        prow(tab if False else pf, "Preprocessed dir (step03):", self._preproc_var)
        prow(pf, "Retroicor input dir:",       self._input_var)
        prow(pf, "Retroicor output dir:",      self._output_var)
        prow(pf, "MATLAB exe:",  self._matlab_var, mode="file")
        prow(pf, "MATLAB code dir:",           self._mcode_var)
        prow(pf, "Retroicor code dir:",        self._retro_var)

        # Parameters
        pm = ttk.LabelFrame(tab, text="Parameters", padding=(10, 6))
        pm.pack(fill="x", pady=(0, 8))

        def erow(parent, label, var, width=8):
            r = ttk.Frame(parent); r.pack(fill="x", pady=2)
            ttk.Label(r, text=label, width=30, anchor="w").pack(side="left")
            ttk.Entry(r, textvariable=var, width=width).pack(side="left")

        erow(pm, "BIDS session (ses-__):", self._session_var)
        erow(pm, "SMS / Multiband (1=yes, 0=no):", self._sms_var)
        erow(pm, "Output physio rate (FS_OUT Hz):", self._fs_var)
        erow(pm, "TR fallback (s, if JSON missing):", self._tr_var)

        return tab

    # ── Run tab ───────────────────────────────────────────────────────────────

    def _build_run_tab(self, parent):
        tab = ttk.Frame(parent, padding=14)

        ttk.Label(tab, text="Run RETROICOR Pipeline",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            tab,
            text=("Runs three sequential parts:\n"
                  "  Part 1 — Generate 1D files from preprocessed mats (with R-DECO if available)\n"
                  "  Part 2 — Copy BIDS BOLD + JSON into the input folder\n"
                  "  Part 3 — Apply RETROICOR via retroicor_batch.m\n\n"
                  "Configure paths in the Configuration tab before running."),
            foreground="gray", wraplength=580,
        ).pack(anchor="w", pady=(0, 12))

        # Summary
        self._summary_lbl = ttk.Label(tab, foreground="#ff4444", wraplength=560)
        self._summary_lbl.pack(anchor="w", pady=(0, 10))
        self._update_summary()
        self._subj_var.trace_add("write", lambda *_: self._update_summary())

        ttk.Separator(tab).pack(fill="x", pady=8)

        # ── Piezo cardiac quality + cardiac/resp choice ─────────────────────────
        cq = ttk.LabelFrame(tab, text="Piezo cardiac quality (global fallback)", padding=(10, 6))
        cq.pack(fill="x", pady=(0, 8))
        ttk.Label(cq, foreground="gray", wraplength=560,
                  text=("Bad piezo acquisition → unreliable R-peaks → cardiac RETROICOR "
                        "injects noise. Prefer the 'Piezo QC Review' tab to decide per "
                        "sequence; this global choice applies only to runs with no saved "
                        "decision:")).pack(anchor="w")
        cqr = ttk.Frame(cq); cqr.pack(fill="x", pady=(4, 2))
        ttk.Radiobutton(cqr, text="Both (cardiac + respiration)", value="both",
                        variable=self._cardiac_var).pack(side="left", padx=(0, 16))
        ttk.Radiobutton(cqr, text="Respiration only (bad piezo)", value="resp",
                        variable=self._cardiac_var).pack(side="left")
        cqb = ttk.Frame(cq); cqb.pack(fill="x", pady=(4, 0))
        self._qc_btn = ttk.Button(cqb, text="▶  Cardiac QC (visual)", command=self._run_cardiac_qc)
        self._qc_btn.pack(side="left", padx=(0, 6))
        ttk.Button(cqb, text="📂 Open QC", command=self._open_cardiac_qc).pack(side="left")

        # Individual part buttons
        parts_frame = ttk.LabelFrame(tab, text="Run individual parts", padding=(10, 6))
        parts_frame.pack(fill="x", pady=(0, 8))

        self._p1_btn = ttk.Button(parts_frame, text="▶  Part 1 — Generate 1D",
                                  command=self._run_part1)
        self._p1_btn.pack(side="left", padx=(0, 6))
        self._p2_btn = ttk.Button(parts_frame, text="▶  Part 2 — Copy BOLD",
                                  command=self._run_part2)
        self._p2_btn.pack(side="left", padx=(0, 6))
        self._p3_btn = ttk.Button(parts_frame, text="▶  Part 3 — RETROICOR",
                                  command=self._run_part3)
        self._p3_btn.pack(side="left")

        ttk.Separator(tab).pack(fill="x", pady=8)

        # Run all
        all_row = ttk.Frame(tab)
        all_row.pack(anchor="w")
        self._all_btn = ttk.Button(all_row, text="▶▶  Run All (Parts 1 → 2 → 3)",
                                   command=self._run_all)
        self._all_btn.pack(side="left")
        self._progress = ttk.Progressbar(all_row, mode="indeterminate", length=220)
        self._progress.pack(side="left", padx=12)

        return tab

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _scan_subjects(self):
        sd = self._cfg["sourcedata"].get().strip()
        if not sd or not os.path.isdir(sd):
            return
        subjects = sorted(d.name for d in Path(sd).iterdir()
                          if d.is_dir() and d.name.startswith("sub-"))
        self._subj_combo["values"] = subjects
        if subjects and not self._subj_var.get():
            self._subj_var.set(subjects[0])

    def _sync_paths(self):
        sd   = self._cfg["sourcedata"].get().strip()
        subj = self._subj_var.get().strip()
        if sd and subj:
            base = Path(sd) / "derivatives" / "physio" / subj
            self._preproc_var.set(str(base / "preprocessed"))
            self._input_var.set( str(base / "retroicor" / "input"))
            self._output_var.set(str(base / "retroicor" / "output"))

    def _update_summary(self):
        subj = self._subj_var.get().strip()
        self._summary_lbl.config(
            text=f"Subject: {subj or '(not set)'}")

    def _validate(self):
        subj = self._subj_var.get().strip()
        if not subj:
            raise ValueError("Select a BIDS subject.")
        preproc = self._preproc_var.get().strip()
        if not preproc or not os.path.isdir(preproc):
            raise ValueError(f"Preprocessed directory not found:\n{preproc}\nRun step03 first.")
        return subj, preproc

    def _all_buttons(self):
        return [self._p1_btn, self._p2_btn, self._p3_btn, self._all_btn,
                self._qc_btn, self._review_qc_btn]

    def _decision_arg(self):
        """MATLAB 'DecisionFile','...' fragment if a saved manifest exists, else ''."""
        dpath = self._decision_path()
        if dpath and dpath.exists():
            self._console.append(f"[Step 04] Using per-run decisions: {dpath}", "info")
            return f"'DecisionFile','{dpath}',"
        return ""

    def _lock(self):
        for b in self._all_buttons():
            b.config(state="disabled")
        self._progress.start(10)

    def _unlock(self):
        for b in self._all_buttons():
            b.config(state="normal")
        self._progress.stop()

    # ── Part 1: Generate 1D ───────────────────────────────────────────────────

    def _step04_cmd(self, part):
        """Build the step04_retroicor_v2.sh command for one part (Task 36).
        Args mirror the .sh positional order; the GUI passes all paths (no inline)."""
        dpath = self._decision_path()
        decision = str(dpath) if (dpath and dpath.exists()) else ""
        return [
            "bash", str(SCRIPTS_ROOT / "step04_retroicor_v2.sh"),
            self._subj_var.get().strip(),
            self._cfg["sourcedata"].get().strip(),
            self._preproc_var.get().strip(),
            self._input_var.get().strip(),
            self._output_var.get().strip(),
            self._matlab_var.get().strip(),
            self._mcode_var.get().strip(),
            self._retro_var.get().strip(),
            self._session_var.get().strip(),
            self._sms_var.get().strip(),
            self._fs_var.get().strip(),
            self._tr_var.get().strip(),
            "1" if self._cardiac_var.get() == "both" else "0",
            decision,
            part,
        ]

    def _run_part1(self):
        try:
            self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return
        inp = self._input_var.get().strip()
        Path(inp).mkdir(parents=True, exist_ok=True)
        self._run_cmd(self._step04_cmd("p1"), inp, "Part 1 — Generate 1D", "step_04_p1")

    # ── Cardiac (piezo) QC ────────────────────────────────────────────────────

    def _run_cardiac_qc(self):
        try:
            _subj, preproc = self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return
        self._run_cmd(self._step04_cmd("cardiacqc"), preproc,
                      "Cardiac QC", "step_04_cardiacqc")

    def _open_cardiac_qc(self):
        preproc = self._preproc_var.get().strip()
        if not preproc:
            return
        qc_dir = str(Path(preproc).parent / "cardiac_qc")
        if not os.path.isdir(qc_dir):
            messagebox.showinfo("Cardiac QC",
                f"No QC folder yet:\n{qc_dir}\nRun 'Cardiac QC' first.")
            return
        self._runner.run(cmd=["open", qc_dir], cwd=qc_dir,
                         on_line=self._console.append, on_done=lambda rc: None)

    # ── Piezo QC Review tab ────────────────────────────────────────────────────

    def _qc_dir(self):
        preproc = self._preproc_var.get().strip()
        return Path(preproc).parent / "cardiac_qc" if preproc else None

    def _decision_path(self):
        """Manifest path step04 reads: <preproc>/<subj>_cardiac_decision.csv."""
        preproc = self._preproc_var.get().strip()
        subj    = self._subj_var.get().strip()
        if not preproc or not subj:
            return None
        return Path(preproc) / f"{subj}_cardiac_decision.csv"

    def _build_review_tab(self, parent):
        tab = ttk.Frame(parent, padding=14)

        ttk.Label(tab, text="Per-sequence piezo quality review",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            tab,
            text=("Run Cardiac QC, then Load review. For each sequence, inspect the "
                  "piezo trace + R-peaks and choose whether to use cardiac RETROICOR "
                  "or respiration-only. The verdict pre-selects a suggestion; your "
                  "choice is saved to a decision manifest that step04 applies per run."),
            foreground="gray", wraplength=620,
        ).pack(anchor="w", pady=(0, 8))

        btns = ttk.Frame(tab); btns.pack(fill="x", pady=(0, 6))
        self._review_qc_btn = ttk.Button(btns, text="▶  Run / Refresh Cardiac QC",
                                         command=self._run_cardiac_qc)
        self._review_qc_btn.pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="⟳ Load review", command=self._load_review).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="💾 Save decisions", command=self._save_decisions).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="📂 Open QC folder", command=self._open_cardiac_qc).pack(side="left", padx=(0, 6))
        ttk.Button(btns, text="📊 Cohort report", command=self._run_piezo_report).pack(side="left")

        self._review_status = ttk.Label(tab, foreground="gray")
        self._review_status.pack(anchor="w", pady=(0, 6))

        # Scrollable list of per-sequence rows
        holder = ttk.Frame(tab)
        holder.pack(fill="both", expand=True)
        self._review_canvas = tk.Canvas(holder, highlightthickness=0, height=420)
        vsb = ttk.Scrollbar(holder, orient="vertical", command=self._review_canvas.yview)
        self._review_canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._review_canvas.pack(side="left", fill="both", expand=True)
        self._review_inner = ttk.Frame(self._review_canvas)
        win = self._review_canvas.create_window((0, 0), window=self._review_inner, anchor="nw")
        self._review_inner.bind("<Configure>", lambda _: self._review_canvas.configure(
            scrollregion=self._review_canvas.bbox("all")))
        self._review_canvas.bind("<Configure>", lambda e: self._review_canvas.itemconfig(win, width=e.width))

        return tab

    def _load_review(self):
        """Read the cardiac_qc CSV + PNGs and build one review row per sequence."""
        try:
            subj, preproc = self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return

        qc_dir   = self._qc_dir()
        csv_path = qc_dir / f"{subj}_cardiac_qc.csv" if qc_dir else None
        if not csv_path or not csv_path.exists():
            messagebox.showinfo("Piezo QC Review",
                f"No QC results found:\n{csv_path}\nRun 'Cardiac QC' first.")
            return

        # Clear any previous rows
        for child in self._review_inner.winfo_children():
            child.destroy()
        self._decision_rows = []
        self._thumb_refs = []

        try:
            with open(csv_path, newline="") as f:
                rows = list(csv.DictReader(f))
        except Exception as e:
            messagebox.showerror("Error", f"Could not read QC CSV:\n{e}"); return

        _vcol = {"GOOD": "#2e9650", "SUSPECT": "#d9a30a", "BAD": "#cc3326"}
        n_bad = 0
        for r in rows:
            task = (r.get("task") or "").strip()
            run  = (r.get("run")  or "").strip()
            verdict = (r.get("verdict") or "").strip().upper()
            rec     = (r.get("recommendation") or "").strip().lower()
            if rec not in ("both", "resp"):
                rec = "both" if verdict in ("GOOD", "SUSPECT") else "resp"
            if rec == "resp":
                n_bad += 1

            row = ttk.LabelFrame(self._review_inner,
                                 text=f"{task}  run-{run}", padding=(8, 6))
            row.pack(fill="x", pady=4, padx=2)

            png = qc_dir / f"{subj}_task-{task}_run-{run}_cardiacqc.png"
            self._add_thumbnail(row, png)

            right = ttk.Frame(row); right.pack(side="left", fill="x", expand=True, padx=(10, 0))
            ttk.Label(right, text=f"Verdict: {verdict or 'NA'}",
                      foreground=_vcol.get(verdict, "#555"),
                      font=("Helvetica", 11, "bold")).pack(anchor="w")
            metrics = (f"HR {r.get('mean_hr_bpm','?')} bpm   "
                       f"RR CV {r.get('cv_rr','?')}   "
                       f"implausible {r.get('pct_implausible','?')}%   "
                       f"max gap {r.get('max_gap_s','?')}s")
            ttk.Label(right, text=metrics, foreground="gray").pack(anchor="w", pady=(0, 4))

            var = tk.StringVar(value=rec)
            choice = ttk.Frame(right); choice.pack(anchor="w")
            ttk.Radiobutton(choice, text="Use cardiac (both)", value="both",
                            variable=var).pack(side="left", padx=(0, 14))
            ttk.Radiobutton(choice, text="Respiration only", value="resp",
                            variable=var).pack(side="left", padx=(0, 14))
            ttk.Button(choice, text="Open full",
                       command=lambda p=str(png): self._open_image(p)).pack(side="left")

            self._decision_rows.append({"task": task, "run": run,
                                        "verdict": verdict, "var": var})

        self._review_status.config(
            text=(f"{len(rows)} sequence(s) loaded — {n_bad} suggested respiration-only. "
                  f"Adjust as needed, then Save decisions."),
            foreground="#cc3326" if n_bad else "#2e9650")

    def _add_thumbnail(self, parent, png_path: Path):
        """Show a downscaled PNG thumbnail (native Tk PhotoImage, no PIL)."""
        if not png_path.exists():
            ttk.Label(parent, text="(no image)", foreground="gray", width=22).pack(side="left")
            return
        try:
            img = tk.PhotoImage(file=str(png_path))
            # QC PNGs are ~1500px wide; subsample to ~3x smaller to fit the row.
            factor = max(1, round(img.width() / 480))
            if factor > 1:
                img = img.subsample(factor, factor)
            self._thumb_refs.append(img)   # prevent garbage collection
            lbl = ttk.Label(parent, image=img, cursor="hand2")
            lbl.pack(side="left")
            lbl.bind("<Button-1>", lambda _e, p=str(png_path): self._open_image(p))
        except Exception:
            ttk.Button(parent, text="Open image",
                       command=lambda p=str(png_path): self._open_image(p)).pack(side="left")

    def _open_image(self, png_path: str):
        if not os.path.isfile(png_path):
            messagebox.showinfo("Image", f"Not found:\n{png_path}"); return
        self._runner.run(cmd=["open", png_path], cwd=os.path.dirname(png_path),
                         on_line=self._console.append, on_done=lambda rc: None)

    def _run_piezo_report(self):
        """Build the cohort piezo QC flag report (all subjects) via qc_snapshots.py."""
        sd = self._cfg["sourcedata"].get().strip()
        if not sd:
            messagebox.showerror("Error", "Set sourcedata first."); return
        pr = self._cfg["project_root"].get().strip() or sd
        py = self._cfg["python_exe"].get().strip() or "python3"
        script = str(SCRIPTS_ROOT / "utility" / "qc_snapshots.py")
        cmd = [py, script, pr, sd, "--piezo-report"]
        self._run_cmd(cmd, pr if os.path.isdir(pr) else "/tmp",
                      "Piezo cohort report", "step_04_piezoreport")

    def _save_decisions(self):
        if not self._decision_rows:
            messagebox.showinfo("Piezo QC Review", "Nothing to save — Load review first.")
            return
        dpath = self._decision_path()
        if dpath is None:
            messagebox.showerror("Error", "Set subject and preprocessed dir first."); return
        subj = self._subj_var.get().strip()
        ts   = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            dpath.parent.mkdir(parents=True, exist_ok=True)
            with open(dpath, "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(["subject", "task", "run", "use_cardiac",
                            "verdict", "decided_by", "timestamp"])
                for row in self._decision_rows:
                    use_cardiac = 1 if row["var"].get() == "both" else 0
                    w.writerow([subj, row["task"], row["run"], use_cardiac,
                                row["verdict"], "user", ts])
        except Exception as e:
            messagebox.showerror("Error", f"Could not write manifest:\n{e}"); return

        n_resp = sum(1 for r in self._decision_rows if r["var"].get() == "resp")
        self._console.append(
            f"[Piezo QC] Saved {len(self._decision_rows)} decision(s) "
            f"({n_resp} respiration-only) → {dpath}", "ok")
        messagebox.showinfo("Piezo QC Review",
            f"Saved {len(self._decision_rows)} decision(s) "
            f"({n_resp} respiration-only) to:\n{dpath}\n\n"
            "step04 Part 1 / Run All will apply these per run.")

    # ── Part 2: Copy BOLD ─────────────────────────────────────────────────────

    def _run_part2(self):
        try:
            self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return
        inp = self._input_var.get().strip()
        Path(inp).mkdir(parents=True, exist_ok=True)
        self._run_cmd(self._step04_cmd("p2"), inp, "Part 2 — Copy BOLD", "step_04_p2")

    # ── Part 3: RETROICOR ─────────────────────────────────────────────────────

    def _run_part3(self):
        try:
            self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return
        Path(self._output_var.get().strip()).mkdir(parents=True, exist_ok=True)
        self._run_cmd(self._step04_cmd("p3"), self._input_var.get().strip(),
                      "Part 3 — RETROICOR", "step_04_p3")

    # ── Run All ───────────────────────────────────────────────────────────────

    def _run_all(self):
        try:
            self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return
        Path(self._input_var.get().strip()).mkdir(parents=True, exist_ok=True)
        Path(self._output_var.get().strip()).mkdir(parents=True, exist_ok=True)
        self._run_cmd(self._step04_cmd("all"), self._input_var.get().strip(),
                      "Step 04 — All parts", "step_04")

    # ── Shared run helpers ────────────────────────────────────────────────────

    def _run_matlab(self, matlab_cmd: str, cwd: str, label: str, state_key: str):
        matlab = self._matlab_var.get().strip()
        cmd = [matlab, "-nodisplay", "-nosplash", "-batch", matlab_cmd]
        self._run_cmd(cmd, cwd, label, state_key)

    def _run_cmd(self, cmd, cwd, label, state_key):
        self._console.separator()
        self._console.append(f"[{label}] Starting…", "info")
        self._console.separator()
        self._lock()
        self._status.set(f"{label} running…")
        self._runner.run(
            cmd=cmd, cwd=cwd if os.path.isdir(cwd) else "/tmp",
            on_line=self._console.append,
            on_done=lambda rc, lbl=label: self._done(rc, lbl),
        )

    def _done(self, rc, label):
        self._unlock()
        if rc == 0:
            self._status.set(f"{label} complete ✓")
            self._console.append(f"[{label}] Finished.", "ok")
        else:
            self._status.set(f"{label} failed (exit {rc})")
            self._console.append(f"[{label}] Failed (exit {rc}).", "error")


# ── Step 06 — Stimulus trigger extraction ────────────────────────────────────

class StimPanel(ttk.Frame):
    """Step 06: Extract stim onsets from physioparse STIMTRIG, copy to fMRIPrep, prep first-level."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=(6, 6), **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner

        # ── Shared vars ───────────────────────────────────────────────────────
        self._subj_var      = tk.StringVar()
        self._parsed_var    = tk.StringVar()
        self._stim_var      = tk.StringVar()
        self._fmriprep_var  = cfg["fmriprep"]
        self._firstlvl_var  = tk.StringVar()
        self._retro_var     = tk.StringVar()   # retroicor output (regressors source)
        self._session_var   = tk.StringVar(value="01")
        self._threshold_var = tk.StringVar(value="1.5")
        self._debounce_var  = tk.StringVar(value="1.5")
        self._fd_thresh_var = tk.StringVar(value="0.5")   # FD spike threshold (mm)
        self._python_var    = cfg["python_exe"]
        self._do_qc         = tk.BooleanVar(value=True)
        self._do_prep       = tk.BooleanVar(value=True)

        ttk.Label(self, text="Step 06 — Stimulus Triggers",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 6))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        nb.add(self._build_cfg(nb),  text="  Configuration  ")
        nb.add(self._build_run(nb),  text="  Run  ")
        nb.add(self._build_view(nb), text="  Results  ")

        self._scan_subjects()
        self._sync_paths()
        cfg["sourcedata"].trace_add("write",
            lambda *_: (self._scan_subjects(), self._sync_paths()))
        self._subj_var.trace_add("write", lambda *_: self._sync_paths())

    # ── Configuration tab ─────────────────────────────────────────────────────
    def _build_cfg(self, parent):
        tab = ttk.Frame(parent, padding=14)

        # Subject
        ttk.Label(tab, text="Subject",
                  font=("Helvetica", 11, "bold")).pack(anchor="w", pady=(0, 4))
        sr = ttk.Frame(tab)
        sr.pack(fill="x", pady=(0, 8))
        ttk.Label(sr, text="BIDS subject:", width=18, anchor="w").pack(side="left")
        self._subj_combo = ttk.Combobox(sr, textvariable=self._subj_var, width=32)
        self._subj_combo.pack(side="left", padx=(0, 6))
        ttk.Button(sr, text="↻", width=3, command=self._scan_subjects).pack(side="left")

        # Paths
        pf = ttk.LabelFrame(tab, text="Paths", padding=(10, 6))
        pf.pack(fill="x", pady=(0, 8))

        def pr(lbl, var, mode="dir"):
            PathRow(pf, lbl, mode=mode, var=var, label_width=26).pack(fill="x", pady=2)

        pr("Parsed mats (physioparse step 02):", self._parsed_var)
        pr("Stim output dir:", self._stim_var)
        pr("fMRIPrep derivatives dir:", self._fmriprep_var)
        pr("Retroicor output (regressors):", self._retro_var)
        pr("First-level folder:", self._firstlvl_var)
        pr("Python exe:", self._python_var, mode="file")

        # Parameters
        pm = ttk.LabelFrame(tab, text="Parameters", padding=(10, 6))
        pm.pack(fill="x", pady=(0, 8))

        def er(lbl, var, w=8):
            r = ttk.Frame(pm); r.pack(fill="x", pady=2)
            ttk.Label(r, text=lbl, width=36, anchor="w").pack(side="left")
            ttk.Entry(r, textvariable=var, width=w).pack(side="left")

        er("Session (ses-__):", self._session_var)
        er("STIMTRIG step threshold:", self._threshold_var)
        er("Debounce (min sec between events):", self._debounce_var)
        er("FD spike threshold (mm):", self._fd_thresh_var)

        # Options
        opts = ttk.LabelFrame(tab, text="Options", padding=(10, 6))
        opts.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(opts, text="Generate QC plots per run (STIMTRIG + detected onsets)",
                        variable=self._do_qc).pack(anchor="w")
        ttk.Checkbutton(opts, text="Prepare first-level folder (stim + generated motion regressors + BOLD)",
                        variable=self._do_prep).pack(anchor="w")

        return tab

    # ── Run tab ───────────────────────────────────────────────────────────────
    def _build_run(self, parent):
        tab = ttk.Frame(parent, padding=14)

        ttk.Label(tab, text="Run Stimulus Pipeline",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            tab,
            text=("Part 1 — Extract onsets from STIMTRIG (runs for all task types)\n"
                  "Part 2 — Copy stim .txt to fMRIPrep func/ (replaces manual step 18)\n"
                  "Part 3 — Prepare first-level folder: stim + motion + BOLD  (optional)\n"
                  "QC     — PNG plots of STIMTRIG with detected onsets overlaid  (optional)"),
            foreground="gray", wraplength=580,
        ).pack(anchor="w", pady=(0, 10))

        self._summary_lbl = ttk.Label(tab, foreground="#ff4444", wraplength=560)
        self._summary_lbl.pack(anchor="w", pady=(0, 8))
        self._update_summary()
        self._subj_var.trace_add("write", lambda *_: self._update_summary())

        ttk.Separator(tab).pack(fill="x", pady=8)

        # Individual buttons
        ind = ttk.LabelFrame(tab, text="Run individual parts", padding=(10, 6))
        ind.pack(fill="x", pady=(0, 8))
        self._p1_btn = ttk.Button(ind, text="▶  Part 1 — Extract onsets",
                                  command=self._run_part1)
        self._p1_btn.pack(side="left", padx=(0, 6))
        self._p2_btn = ttk.Button(ind, text="▶  Part 2 — Copy to fMRIPrep",
                                  command=self._run_part2)
        self._p2_btn.pack(side="left", padx=(0, 6))
        self._p3_btn = ttk.Button(ind, text="▶  Part 3 — First-level prep",
                                  command=self._run_part3)
        self._p3_btn.pack(side="left")

        ttk.Separator(tab).pack(fill="x", pady=8)

        all_row = ttk.Frame(tab)
        all_row.pack(anchor="w")
        self._all_btn = ttk.Button(all_row, text="▶▶  Run All",
                                   command=self._run_all)
        self._all_btn.pack(side="left")
        self._progress = ttk.Progressbar(all_row, mode="indeterminate", length=220)
        self._progress.pack(side="left", padx=12)

        return tab

    # ── Results / QC view tab ─────────────────────────────────────────────────
    def _build_view(self, parent):
        tab = ttk.Frame(parent, padding=14)

        ttk.Label(tab, text="Results",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 4))

        # Treeview: one row per sequence
        cols = ("file", "n_events", "stim_txt", "qc_plot")
        self._tv = ttk.Treeview(tab, columns=cols, show="headings", height=12)
        self._tv.heading("file",     text="Sequence")
        self._tv.heading("n_events", text="Events")
        self._tv.heading("stim_txt", text=".txt")
        self._tv.heading("qc_plot",  text="QC plot")
        self._tv.column("file",     width=260, stretch=True)
        self._tv.column("n_events", width=60,  anchor="center")
        self._tv.column("stim_txt", width=50,  anchor="center")
        self._tv.column("qc_plot",  width=70,  anchor="center")
        self._tv.tag_configure("ok",   foreground="#4ec9b0")
        self._tv.tag_configure("warn", foreground="#dcdcaa")
        vsb = ttk.Scrollbar(tab, orient="vertical", command=self._tv.yview)
        self._tv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._tv.pack(side="left", fill="both", expand=True)

        ttk.Button(tab, text="↻ Refresh", command=self._refresh_results).pack(
            side="bottom", anchor="w", pady=(6, 0))

        self._stim_var.trace_add("write", lambda *_: self._refresh_results())
        return tab

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _scan_subjects(self):
        sd = self._cfg["sourcedata"].get().strip()
        if not sd or not os.path.isdir(sd):
            return
        subs = sorted(d.name for d in Path(sd).iterdir()
                      if d.is_dir() and d.name.startswith("sub-"))
        self._subj_combo["values"] = subs
        if subs and not self._subj_var.get():
            self._subj_var.set(subs[0])

    def _sync_paths(self):
        sd   = self._cfg["sourcedata"].get().strip()
        subj = self._subj_var.get().strip()
        if not sd or not subj:
            return
        base = Path(sd) / "derivatives" / "physio" / subj
        self._parsed_var.set(str(base / "parsed"))
        self._stim_var.set(str(base / "stimtrigger"))
        self._fmriprep_var.set(str(Path(sd) / "derivatives" / "fmriprep"))
        self._retro_var.set(str(base / "retroicor" / "output"))
        # SHARED first-level folder (not per-subject): step07 runs over all
        # subjects and reads every subject's files from one place. Files are
        # named by subject, so they coexist.
        self._firstlvl_var.set(str(Path(sd) / "derivatives" / "physio" / "first_level"))

    def _update_summary(self):
        subj = self._subj_var.get().strip()
        self._summary_lbl.config(text=f"Subject: {subj or '(not set)'}")

    def _validate(self):
        subj   = self._subj_var.get().strip()
        parsed = self._parsed_var.get().strip()
        if not subj:
            raise ValueError("Select a BIDS subject.")
        if not parsed or not os.path.isdir(parsed):
            raise ValueError(f"Parsed directory not found:\n{parsed}\nRun step02 (physioparse) first.")
        return subj, parsed

    def _extractor(self):
        return str(SCRIPTS_ROOT / "utility" / "extract_stim_onsets.py")

    def _all_btns(self):
        return [self._p1_btn, self._p2_btn, self._p3_btn, self._all_btn]

    def _lock(self):
        for b in self._all_btns(): b.config(state="disabled")
        self._progress.start(10)

    def _unlock(self):
        for b in self._all_btns(): b.config(state="normal")
        self._progress.stop()

    def _run_cmd(self, cmd, cwd, label):
        self._console.separator()
        self._console.append(f"[Step 06] {label} starting…", "info")
        self._console.separator()
        self._lock()
        self._status.set(f"Step 06 — {label} running…")
        self._runner.run(cmd=cmd, cwd=cwd if os.path.isdir(cwd) else "/tmp",
                        on_line=self._console.append,
                        on_done=lambda rc, lbl=label: self._done(rc, lbl))

    def _done(self, rc, label):
        self._unlock()
        if rc == 0:
            self._status.set(f"Step 06 — {label} ✓")
            self._console.append(f"[Step 06] {label} finished.", "ok")
            self._refresh_results()
        else:
            self._status.set(f"Step 06 — {label} failed (exit {rc})")
            self._console.append(f"[Step 06] {label} failed (exit {rc}).", "error")

    # ── Part runners ──────────────────────────────────────────────────────────
    def _run_part1(self):
        try:
            subj, parsed = self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return

        stim   = self._stim_var.get().strip()
        ses    = self._session_var.get().strip()
        thr    = self._threshold_var.get().strip()
        deb    = self._debounce_var.get().strip()
        python = self._python_var.get().strip()
        ext    = self._extractor()
        qc_flag = "--qc" if self._do_qc.get() else ""

        # Build a bash loop over all parsed mats
        qc_dir = str(Path(stim) / "qc")
        script = (
            f"set -euo pipefail\n"
            f"mkdir -p '{stim}'\n"
            f"for mat in '{parsed}'/task-*_run-*.mat; do\n"
            f"  [ -f \"$mat\" ] || continue\n"
            f"  echo \"[Step 06]  $(basename $mat)\"\n"
            f"  '{python}' '{ext}' \"$mat\" '{subj}' '{stim}' "
            f"--session '{ses}' --threshold {thr} --debounce {deb} "
            f"{qc_flag} --qc-dir '{qc_dir}'\n"
            f"done\n"
            f"echo 'Part 1 done.'"
        )
        self._run_cmd(["bash", "-c", script], stim, "Part 1 — Extract onsets")

    def _run_part2(self):
        try:
            subj, _ = self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return

        stim   = self._stim_var.get().strip()
        fmrip  = self._fmriprep_var.get().strip()
        ses    = self._session_var.get().strip()
        func_dir = str(Path(fmrip) / subj / f"ses-{ses}" / "func")

        script = (
            f"set -euo pipefail\n"
            f"if [ ! -d '{func_dir}' ]; then\n"
            f"  echo 'WARNING: fMRIPrep func dir not found: {func_dir}'\n"
            f"  exit 1\n"
            f"fi\n"
            f"n=0\n"
            f"for f in '{stim}'/*_bold_stim.txt; do\n"
            f"  [ -f \"$f\" ] || continue\n"
            f"  cp -f \"$f\" '{func_dir}/'\n"
            f"  echo \"  copied: $(basename $f)\"\n"
            f"  n=$((n+1))\n"
            f"done\n"
            f"echo \"Copied $n stim file(s) to {func_dir}\""
        )
        self._run_cmd(["bash", "-c", script], stim, "Part 2 — Copy to fMRIPrep")

    def _run_part3(self):
        try:
            subj, _ = self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return

        stim   = self._stim_var.get().strip()
        fmrip  = self._fmriprep_var.get().strip()
        ses    = self._session_var.get().strip()
        fl_dir = self._firstlvl_var.get().strip()
        fd     = self._fd_thresh_var.get().strip() or "0.5"
        python = self._python_var.get().strip()
        subj_dir = str(Path(fmrip) / subj)
        func_dir = str(Path(fmrip) / subj / f"ses-{ses}" / "func")
        motion_ext = str(SCRIPTS_ROOT / "utility" / "extract_motion_regressors.py")

        # NEW PIPELINE (RETROICOR → fMRIPrep): no 03_retroicor_regressors — physio
        # was removed from the BOLD before fMRIPrep, so GLM nuisance = motion only.
        d_stim   = f"{fl_dir}/01_stim_onsets"
        d_motion = f"{fl_dir}/02_motion_regressors"
        d_bold   = f"{fl_dir}/04_bolds"
        log_dir  = f"{fl_dir}/logs"
        script = (
            f"set -euo pipefail\n"
            f"mkdir -p '{d_stim}' '{d_motion}' '{d_bold}'\n"
            # Stim files
            f"for f in '{stim}'/*_bold_stim.txt; do [ -f \"$f\" ] && cp -f \"$f\" '{d_stim}/'; done\n"
            # Motion regressors — GENERATE from fMRIPrep confounds (6 rigid + FD spikes)
            f"if [ -f '{motion_ext}' ] && [ -d '{func_dir}' ]; then\n"
            f"  echo 'Generating motion regressors (6 rigid + FD>{fd} mm spikes)...'\n"
            f"  '{python}' '{motion_ext}' '{func_dir}' '{d_motion}' "
            f"--subject '{subj}' --session '{ses}' --fd-thresh {fd} --log-dir '{log_dir}' "
            f"|| echo '  WARNING: motion regressor generation failed'\n"
            f"else echo '  WARNING: cannot generate motion regressors (missing script or func dir)'; fi\n"
            # Legacy fallback: copy any pre-existing *_motion_regressors.txt not already present
            f"for f in '{subj_dir}'/*_motion_regressors.txt '{func_dir}'/*_motion_regressors.txt; do "
            f"[ -f \"$f\" ] || continue; bn=$(basename \"$f\"); "
            f"[ -f '{d_motion}/'\"$bn\" ] || cp -f \"$f\" '{d_motion}/'; done\n"
            # T1w BOLD (physio-cleaned by RETROICOR before fMRIPrep)
            f"for f in '{func_dir}'/*_space-T1w_desc-preproc_bold.nii.gz; do "
            f"[ -f \"$f\" ] && cp -f \"$f\" '{d_bold}/'; done\n"
            f"echo 'First-level folder ready: {fl_dir}'"
        )
        self._run_cmd(["bash", "-c", script], fl_dir, "Part 3 — First-level prep")

    def _run_all(self):
        try:
            subj, parsed = self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return

        script_path = str(SCRIPTS_ROOT / "step06_stim_v2.sh")
        if not os.path.isfile(script_path):
            messagebox.showerror("Error", f"step06_stim_v2.sh not found:\n{script_path}")
            return

        stim    = self._stim_var.get().strip()
        fmrip   = self._fmriprep_var.get().strip()
        fl_dir  = self._firstlvl_var.get().strip()
        retro   = self._retro_var.get().strip()
        ses     = self._session_var.get().strip()
        thr     = self._threshold_var.get().strip()
        deb     = self._debounce_var.get().strip()
        python  = self._python_var.get().strip()
        qc_flag = "1" if self._do_qc.get() else "0"
        prep    = "0" if self._do_prep.get() else "1"
        fd      = self._fd_thresh_var.get().strip() or "0.5"

        sd = self._cfg["sourcedata"].get().strip()
        cmd = [
            "bash", script_path,
            subj, sd, parsed, stim, fmrip, fl_dir,
            ses, thr, deb, python, qc_flag, prep, retro, fd,
        ]
        self._run_cmd(cmd, stim, "All parts")

    def _refresh_results(self):
        self._tv.delete(*self._tv.get_children())
        stim_dir = self._stim_var.get().strip()
        if not stim_dir or not os.path.isdir(stim_dir):
            return
        qc_dir = os.path.join(stim_dir, "qc")
        for f in sorted(os.listdir(stim_dir)):
            if not f.endswith("_bold_stim.txt"):
                continue
            stem  = f.replace("_bold_stim.txt", "")
            txt_ok = "✓"
            # Count events: skip header comment lines
            n_ev = 0
            try:
                with open(os.path.join(stim_dir, f)) as fh:
                    n_ev = sum(1 for ln in fh if ln.strip() and not ln.startswith("#"))
            except Exception:
                pass
            qc_base = stem + "_stim_qc.png"
            has_qc  = os.path.isfile(os.path.join(qc_dir, qc_base))
            tag = "ok" if n_ev > 0 else "warn"
            self._tv.insert("", "end", values=(
                stem, str(n_ev), txt_ok, "✓" if has_qc else "·",
            ), tags=(tag,))


# ── Step 07 — First-level GLM + MNI ───────────────────────────────────────────

class FirstLevelPanel(ttk.Frame):
    """Step 07: First-level SPM GLM (masks located in fmriprep, not copied) + MNI warp."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner

        # ── Vars ──────────────────────────────────────────────────────────────
        self._sublist_var = cfg["subjlist_bids"]
        self._firstlvl_var = tk.StringVar()
        self._output_var   = tk.StringVar()
        self._spm_var      = cfg["spm_dir"]
        self._matlab_var   = cfg["matlab_exe"]
        self._mcode_var    = cfg["matlab_code"]
        self._env_var      = cfg["env_script"]
        self._session_var  = tk.StringVar(value="01")
        self._run_var      = tk.StringVar(value="01")
        self._tr_var       = tk.StringVar(value="1.19")
        self._smooth_var   = tk.StringVar(value="3")
        self._do_mni       = tk.BooleanVar(value=True)
        self._use_sourcedata = tk.BooleanVar(value=False)
        self._warp_only    = tk.BooleanVar(value=False)
        self._space_var    = tk.StringVar(value="MNI")   # MNI (default) | T1w (legacy) | both (Task 06)
        self._restrict_bs  = tk.BooleanVar(value=False)   # restrict GLM to brainstem (Task 05 C2)
        self._bs_mask_var  = cfg["brainstem_mask"]        # shared brainstem mask path
        self._bs_smooth_var = tk.StringVar(value="")      # optional brainstem smoothing (mm)
        # Single-folder warp (warp ANY con folder + a T1 → MNI)
        self._wf_con_var   = tk.StringVar()
        self._wf_t1_var    = tk.StringVar()
        self._wf_out_var   = tk.StringVar()
        self._wf_pat_var   = tk.StringVar(value="con_*.nii")

        ttk.Label(self, text="Step 07 — First-level GLM + MNI",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("First-level SPM GLM. Default models the direct fMRIPrep MNI BOLD "
                  "(no SPM renormalisation); the T1w + SPM-warp path is optional legacy.\n"
                  "Masks + BOLDs are LOCATED in derivatives/fmriprep (not copied).\n"
                  "Stim onsets + motion regressors come from the step06 first_level folder."),
            foreground="gray", wraplength=600,
        ).pack(anchor="w", pady=(0, 10))

        # ── Paths ──────────────────────────────────────────────────────────────
        pf = ttk.LabelFrame(self, text="Paths", padding=(10, 6))
        pf.pack(fill="x", pady=(0, 8))

        PathRow(pf, "BIDS subject list:", mode="file",
                filetypes=[("Text", "*.txt"), ("All", "*.*")],
                var=self._sublist_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "First-level dir (step06):", mode="dir",
                var=self._firstlvl_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "Output dir:", mode="dir",
                var=self._output_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "SPM12 dir:", mode="dir",
                var=self._spm_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "MATLAB exe:", mode="file",
                var=self._matlab_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "MATLAB code dir:", mode="dir",
                var=self._mcode_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "Environment script:", mode="file",
                filetypes=[("Shell", "*.sh"), ("All", "*.*")],
                var=self._env_var, label_width=22).pack(fill="x", pady=2)
        ttk.Label(pf, text="(sourced to put MATLAB/SPM on PATH — type 'none' to skip; "
                           "the script is sourced with set +u so FreeSurfer env files don't abort)",
                  foreground="gray", wraplength=560).pack(anchor="w", pady=(0, 2))

        # ── fMRIPrep summary (located, not configured — derived from Setup) ────
        info = ttk.LabelFrame(self, text="Mask + BOLD source (located in place)", padding=(8, 4))
        info.pack(fill="x", pady=(0, 8))
        self._fmriprep_lbl = ttk.Label(info, foreground="#ff4444", wraplength=560)
        self._fmriprep_lbl.pack(anchor="w")
        cfg["sourcedata"].trace_add("write", lambda *_: self._sync())
        self._sync()

        # ── Parameters ─────────────────────────────────────────────────────────
        pm = ttk.LabelFrame(self, text="Parameters", padding=(10, 6))
        pm.pack(fill="x", pady=(0, 8))

        def er(lbl, var, w=8):
            r = ttk.Frame(pm); r.pack(fill="x", pady=2)
            ttk.Label(r, text=lbl, width=30, anchor="w").pack(side="left")
            ttk.Entry(r, textvariable=var, width=w).pack(side="left")

        er("Session (ses-__):", self._session_var)
        er("Run (run-__):", self._run_var)
        er("TR (seconds):", self._tr_var)
        er("Smoothing FWHM (mm, isotropic):", self._smooth_var)

        spr = ttk.Frame(pm); spr.pack(fill="x", pady=(4, 0))
        ttk.Label(spr, text="First-level space:").pack(side="left")
        ttk.Combobox(spr, textvariable=self._space_var, width=7, state="readonly",
                     values=["MNI", "T1w", "both"]).pack(side="left", padx=(4, 0))
        ttk.Label(pm, foreground="gray", wraplength=560,
                  text=("'MNI' (default) models the fMRIPrep MNI BOLD directly (con already MNI → "
                        "wcon_*, no SPM warp). 'T1w' (optional legacy) models the fMRIPrep T1w BOLD "
                        "(con in T1w; optional SPM warp below — double-normalisation). 'both' does "
                        "T1w in <subj>/<task> and MNI in <subj>/<task>/mni.")
                  ).pack(anchor="w")

        ttk.Checkbutton(pm, text="Warp T1w contrasts to MNI (segment T1 → wcon_*.nii; legacy, only for T1w space)",
                        variable=self._do_mni).pack(anchor="w", pady=(4, 0))

        # ── Brainstem restriction (Task 05 C2) ───────────────────────────────
        ttk.Checkbutton(pm, text="Restrict GLM to brainstem mask (explicit mask = brainstem ∩ brain)",
                        variable=self._restrict_bs).pack(anchor="w", pady=(6, 0))
        PathRow(pm, "Brainstem mask:", mode="file",
                filetypes=[("NIfTI", "*.nii *.nii.gz"), ("All", "*.*")],
                var=self._bs_mask_var, label_width=18).pack(fill="x", pady=2)
        bsr = ttk.Frame(pm); bsr.pack(fill="x")
        ttk.Label(bsr, text="Brainstem smoothing FWHM (mm, blank = use above):").pack(side="left")
        ttk.Entry(bsr, textvariable=self._bs_smooth_var, width=6).pack(side="left", padx=(4, 0))
        ttk.Label(pm, foreground="gray", wraplength=560,
                  text=("Build the mask in the Brainstem Mask tool. It must match the modeling "
                        "space — use 'First-level space = MNI' with an MNI brainstem mask.")
                  ).pack(anchor="w")
        ttk.Checkbutton(pm, text="Use per-subject physio dirs (sourcedata/derivatives/physio/<subj>/stimtrigger/)",
                        variable=self._use_sourcedata).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(pm, text="Warp-only mode (skip GLM, only warp existing con_*.nii to MNI)",
                        variable=self._warp_only).pack(anchor="w", pady=(4, 0))
        ttk.Label(pm, foreground="gray", wraplength=560,
                  text=("Nuisance regressors = motion + FD spikes only. Physiological noise was "
                        "removed from the BOLD by RETROICOR before fMRIPrep, so no physio "
                        "regressors are added here.")).pack(anchor="w", pady=(4, 0))

        ttk.Separator(self).pack(fill="x", pady=8)

        btn_row = ttk.Frame(self)
        btn_row.pack(anchor="w")
        self._run_btn = ttk.Button(btn_row, text="▶  Run First-level + MNI", command=self._run)
        self._run_btn.pack(side="left")
        self._stop_btn = ttk.Button(btn_row, text="⏹ Stop", command=self._stop, state="disabled")
        self._stop_btn.pack(side="left", padx=4)
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=220)
        self._progress.pack(side="left", padx=12)

        # ── Optional: warp a single already-done first-level folder ────────────
        wf = ttk.LabelFrame(
            self, text="Optional — warp a single folder (con + T1 → MNI, no GLM)",
            padding=(10, 6))
        wf.pack(fill="x", pady=(12, 4))
        ttk.Label(wf,
                  text=("Point at any folder of con_*.nii (already-done first level) plus the "
                        "subject T1; segments the T1 and writes w<con>.nii. Use this instead of "
                        "the batch warp-only when your contrasts aren't in the <subj>/<task> tree."),
                  foreground="gray", wraplength=580).pack(anchor="w", pady=(0, 6))
        PathRow(wf, "Con folder:", mode="dir",
                var=self._wf_con_var, label_width=16).pack(fill="x", pady=2)
        PathRow(wf, "T1 file:", mode="file",
                filetypes=[("NIfTI", "*.nii *.nii.gz"), ("All", "*.*")],
                var=self._wf_t1_var, label_width=16).pack(fill="x", pady=2)
        PathRow(wf, "Output dir:", mode="dir",
                var=self._wf_out_var, label_width=16).pack(fill="x", pady=2)
        pr = ttk.Frame(wf); pr.pack(fill="x", pady=2)
        ttk.Label(pr, text="Con pattern:", width=16, anchor="w").pack(side="left")
        ttk.Entry(pr, textvariable=self._wf_pat_var, width=16).pack(side="left")
        self._wf_btn = ttk.Button(wf, text="▶  Warp this folder", command=self._run_warp_folder)
        self._wf_btn.pack(anchor="w", pady=(6, 0))

    def _run_warp_folder(self):
        script = str(SCRIPTS_ROOT / "step07b_warp_folder_v2.sh")
        if not os.path.isfile(script):
            messagebox.showerror("Error", f"step07b_warp_folder_v2.sh not found:\n{script}")
            return
        con = self._wf_con_var.get().strip()
        t1  = self._wf_t1_var.get().strip()
        out = self._wf_out_var.get().strip() or con
        if not con or not os.path.isdir(con):
            messagebox.showerror("Error", f"Con folder not found:\n{con}"); return
        if not t1 or not os.path.isfile(t1):
            messagebox.showerror("Error", f"T1 file not found:\n{t1}"); return
        cmd = ["bash", script, con, t1, out,
               self._spm_var.get().strip(), self._matlab_var.get().strip(),
               self._mcode_var.get().strip(), self._wf_pat_var.get().strip() or "con_*.nii",
               self._env_var.get().strip() or "none"]
        self._console.separator()
        self._console.append(f"[Step 07b] Warping folder: {con}", "info")
        self._console.separator()
        self._wf_btn.config(state="disabled"); self._progress.start(10)
        self._status.set("Step 07b (warp folder) running…")
        self._runner.run(cmd=cmd, cwd=str(SCRIPTS_ROOT),
                         on_line=self._console.append, on_done=self._warp_done)

    def _warp_done(self, rc):
        self._progress.stop(); self._wf_btn.config(state="normal")
        if rc == 0:
            self._status.set("Step 07b warp complete ✓")
            self._console.append("[Step 07b] Folder warped to MNI.", "ok")
        else:
            self._status.set(f"Step 07b failed (exit {rc})")
            self._console.append(f"[Step 07b] Failed (exit {rc}).", "error")

    def _sync(self):
        sd = self._cfg["sourcedata"].get().strip()
        if sd:
            self._fmriprep_lbl.config(
                text=f"{sd}/derivatives/fmriprep/<subj>/ses-01/func/\n"
                     f"  *_space-T1w_desc-preproc_bold.nii.gz  +  *_space-T1w_desc-brain_mask.nii.gz")
            if not self._firstlvl_var.get():
                self._firstlvl_var.set(str(Path(sd) / "derivatives" / "physio" / "first_level"))
            if not self._output_var.get():
                self._output_var.set(str(Path(sd) / "derivatives" / "spm" / "first_level"))
        else:
            self._fmriprep_lbl.config(text="(set sourcedata in Setup)")

    def _run(self):
        script = str(SCRIPTS_ROOT / "step07_firstlevel_mni_v2.sh")
        if not os.path.isfile(script):
            messagebox.showerror("Error", f"step07_firstlevel_mni_v2.sh not found:\n{script}")
            return

        sublist = self._sublist_var.get().strip()
        if not sublist or not os.path.isfile(sublist):
            messagebox.showerror("Error", f"Subject list not found:\n{sublist}")
            return

        sd = self._cfg["sourcedata"].get().strip()
        if not sd:
            messagebox.showerror("Error", "Set sourcedata in Setup."); return

        flvl   = self._firstlvl_var.get().strip()
        out    = self._output_var.get().strip()
        spm    = self._spm_var.get().strip()
        matlab = self._matlab_var.get().strip()
        mcode  = self._mcode_var.get().strip()
        env    = self._env_var.get().strip() or "none"
        ses    = self._session_var.get().strip() or "01"
        run    = self._run_var.get().strip() or "01"
        tr     = self._tr_var.get().strip() or "1.19"
        smooth = self._smooth_var.get().strip() or "3"
        do_mni = "1" if self._do_mni.get() else "0"
        warp_only = "1" if self._warp_only.get() else "0"
        use_sourcedata = "1" if self._use_sourcedata.get() else "0"
        space = self._space_var.get().strip() or "T1w"
        restrict_bs = "1" if self._restrict_bs.get() else "0"
        bs_mask = self._bs_mask_var.get().strip()
        bs_smooth = self._bs_smooth_var.get().strip()

        cmd = [
            "bash", script,
            sublist, sd, flvl, out, spm, matlab, mcode,
            ses, run, tr, smooth, do_mni, env, warp_only, use_sourcedata,
            space, restrict_bs, bs_mask, bs_smooth,
        ]

        self._console.separator()
        self._console.append("[Step 07] First-level GLM + MNI starting…", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._progress.start(10)
        self._status.set("Step 07 (first-level) running…")

        self._runner.run(cmd=cmd, cwd=str(SCRIPTS_ROOT),
                        on_line=self._console.append, on_done=self._done)

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        self._stop_btn.config(state="disabled")
        if rc == 0:
            self._status.set("Step 07 complete ✓")
            self._console.append("[Step 07] First-level + MNI finished.", "ok")
        else:
            self._status.set(f"Step 07 failed (exit {rc})")
            self._console.append(f"[Step 07] Failed (exit {rc}).", "error")

    def _stop(self):
        """Stop the currently running process."""
        self._runner.stop()


# ── Step 08 — Second-level (group) analysis ───────────────────────────────────

class SecondLevelPanel(ttk.Frame):
    """Step 08, two parts:
       Part 1 — Populate per-task folders from the step07 contrasts.
       Part 2 — Cases vs Controls two-sample analysis + contrasts."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=(6, 6), **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner

        # ── Shared vars ───────────────────────────────────────────────────────
        self._firstlvl_var = tk.StringVar()   # step07 output root (Part 1 input)
        self._taskroot_var = tk.StringVar()   # populated per-task root (Part 1 output / Part 2 input)
        self._group_out    = tk.StringVar()   # Part 2 results
        self._cases_var    = tk.StringVar()
        self._controls_var = tk.StringVar()
        self._spm_var      = cfg["spm_dir"]
        self._matlab_var   = cfg["matlab_exe"]
        self._mcode_var    = cfg["matlab_code"]
        self._env_var      = cfg["env_script"]
        self._con_var      = tk.StringVar(value="wcon_0001.nii")
        self._do_combined  = tk.BooleanVar(value=True)
        self._combined_mode = tk.StringVar(value="average")   # average (default) | pool (legacy)
        # Optional nuisance covariates (Task 08) — sourced from participants.tsv + fMRIPrep
        self._cov_age = tk.BooleanVar(value=False)
        self._cov_sex = tk.BooleanVar(value=False)
        self._cov_fd  = tk.BooleanVar(value=False)
        self._restrict_bs = tk.BooleanVar(value=False)   # restrict group to brainstem (C3)

        ttk.Label(self, text="Step 08 — Second-level (Group)",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 6))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)
        nb.add(self._build_part1(nb), text="  Part 1 — Populate task folders  ")
        nb.add(self._build_part2(nb), text="  Part 2 — Cases vs Controls  ")

        cfg["sourcedata"].trace_add("write", lambda *_: self._sync())
        self._sync()

        # progress + run-state shared across both parts
        self._busy_btns = []

    # ── Part 1 — Populate ─────────────────────────────────────────────────────
    def _build_part1(self, parent):
        tab = ttk.Frame(parent, padding=14)
        ttk.Label(tab, text="Populate per-task folders",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            tab,
            text=("Gathers the step07 contrast images (<subject>/<task>/<con>) into flat\n"
                  "per-task folders: <task root>/<task>/<subject>.nii  (+ _subjects.txt).\n"
                  "Group stats need MNI space — use wcon_0001.nii (step07's MNI warp)."),
            foreground="gray", wraplength=600,
        ).pack(anchor="w", pady=(0, 10))

        pf = ttk.LabelFrame(tab, text="Paths", padding=(10, 6))
        pf.pack(fill="x", pady=(0, 8))
        PathRow(pf, "Step07 output root:", mode="dir",
                var=self._firstlvl_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "Task root (output):", mode="dir",
                var=self._taskroot_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "SPM12 dir:", mode="dir",
                var=self._spm_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "MATLAB exe:", mode="file",
                var=self._matlab_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "MATLAB code dir:", mode="dir",
                var=self._mcode_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "Environment script:", mode="file",
                filetypes=[("Shell", "*.sh"), ("All", "*.*")],
                var=self._env_var, label_width=22).pack(fill="x", pady=2)

        cr = ttk.Frame(tab); cr.pack(fill="x", pady=(0, 8))
        ttk.Label(cr, text="Contrast image filename:", width=24, anchor="w").pack(side="left")
        ttk.Entry(cr, textvariable=self._con_var, width=20).pack(side="left")

        ttk.Separator(tab).pack(fill="x", pady=8)
        row = ttk.Frame(tab); row.pack(anchor="w")
        self._p1_btn = ttk.Button(row, text="▶  Run Part 1 — Populate", command=self._run_part1)
        self._p1_btn.pack(side="left")
        self._p1_prog = ttk.Progressbar(row, mode="indeterminate", length=200)
        self._p1_prog.pack(side="left", padx=12)
        return tab

    # ── Part 2 — Cases vs Controls ────────────────────────────────────────────
    def _build_part2(self, parent):
        tab = ttk.Frame(parent, padding=14)
        ttk.Label(tab, text="Cases vs Controls — two-sample t-test",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            tab,
            text=("Splits the populated subjects into cases and controls (two text files,\n"
                  "one BIDS subject per line) and runs a two-sample t-test per task with\n"
                  "contrasts: Cases>Controls, Controls>Cases, Cases mean, Controls mean.\n"
                  "A pooled Block+Continuous analysis is also run."),
            foreground="gray", wraplength=600,
        ).pack(anchor="w", pady=(0, 10))

        gf = ttk.LabelFrame(tab, text="Groups", padding=(10, 6))
        gf.pack(fill="x", pady=(0, 8))
        PathRow(gf, "Task root (Part 1 out):", mode="dir",
                var=self._taskroot_var, label_width=22).pack(fill="x", pady=2)
        PathRow(gf, "Cases list (.txt):", mode="file",
                filetypes=[("Text", "*.txt"), ("All", "*.*")],
                var=self._cases_var, label_width=22).pack(fill="x", pady=2)
        PathRow(gf, "Controls list (.txt):", mode="file",
                filetypes=[("Text", "*.txt"), ("All", "*.*")],
                var=self._controls_var, label_width=22).pack(fill="x", pady=2)
        ttk.Button(gf, text="↻ Preview populated subjects",
                   command=self._preview_subjects).pack(anchor="w", pady=(4, 0))

        of = ttk.LabelFrame(tab, text="Output", padding=(10, 6))
        of.pack(fill="x", pady=(0, 8))
        PathRow(of, "Group output dir:", mode="dir",
                var=self._group_out, label_width=22).pack(fill="x", pady=2)
        ttk.Checkbutton(of, text="Also run combined BlockStim + ContinuousStim",
                        variable=self._do_combined).pack(anchor="w", pady=(4, 0))
        cmr = ttk.Frame(of); cmr.pack(anchor="w", pady=(2, 0))
        ttk.Label(cmr, text="    Combined mode:").pack(side="left")
        ttk.Combobox(cmr, textvariable=self._combined_mode, width=9, state="readonly",
                     values=["average", "pool"]).pack(side="left", padx=(4, 0))
        ttk.Label(of, foreground="gray", wraplength=560,
                  text=("'average' (default, recommended): average each subject's Block+Continuous "
                        "into one image → one observation per subject. 'pool' (legacy): enters both "
                        "conditions per subject — double-counts, inflates false positives.")
                  ).pack(anchor="w")
        cvr = ttk.Frame(of); cvr.pack(anchor="w", pady=(6, 0))
        ttk.Label(cvr, text="Nuisance covariates (optional):").pack(side="left")
        ttk.Checkbutton(cvr, text="age", variable=self._cov_age).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(cvr, text="sex", variable=self._cov_sex).pack(side="left", padx=(8, 0))
        ttk.Checkbutton(cvr, text="mean_fd", variable=self._cov_fd).pack(side="left", padx=(8, 0))
        ttk.Label(of, foreground="gray", wraplength=560,
                  text=("age/sex from sourcedata/participants.tsv, mean_fd from the fMRIPrep "
                        "confounds (auto-built). A covariate incomplete for the analysed subjects "
                        "is dropped with a warning.")).pack(anchor="w")
        ttk.Checkbutton(of, text="Restrict group analysis to brainstem mask (explicit mask; Task 05)",
                        variable=self._restrict_bs).pack(anchor="w", pady=(6, 0))
        ttk.Label(of, foreground="gray", wraplength=560,
                  text="Uses the mask from the Brainstem Mask tool (cfg brainstem_mask); must be in wcon/MNI space."
                  ).pack(anchor="w")

        ttk.Separator(tab).pack(fill="x", pady=8)
        row = ttk.Frame(tab); row.pack(anchor="w")
        self._p2_btn = ttk.Button(row, text="▶  Run Part 2 — Group analysis", command=self._run_part2)
        self._p2_btn.pack(side="left")
        self._p2_prog = ttk.Progressbar(row, mode="indeterminate", length=200)
        self._p2_prog.pack(side="left", padx=12)
        return tab

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _sync(self):
        sd = self._cfg["sourcedata"].get().strip()
        if not sd:
            return
        if not self._firstlvl_var.get():
            self._firstlvl_var.set(str(Path(sd) / "derivatives" / "spm" / "first_level"))
        if not self._taskroot_var.get():
            self._taskroot_var.set(str(Path(sd) / "derivatives" / "spm" / "second_level" / "tasks"))
        if not self._group_out.get():
            self._group_out.set(str(Path(sd) / "derivatives" / "spm" / "second_level" / "groups"))

    def _preview_subjects(self):
        root = self._taskroot_var.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showwarning("Not found", f"Task root not found:\n{root}\nRun Part 1 first.")
            return
        lines = []
        for task in sorted(os.listdir(root)):
            td = os.path.join(root, task)
            if not os.path.isdir(td):
                continue
            subs = sorted(f[:-4] for f in os.listdir(td) if f.endswith(".nii"))
            lines.append(f"{task}: {len(subs)} subject(s)")
            for s in subs:
                lines.append(f"    {s}")
        messagebox.showinfo("Populated subjects",
                            "\n".join(lines) if lines else "No populated subjects found.")

    def _run_part1(self):
        script = str(SCRIPTS_ROOT / "step08a_populate_v2.sh")
        if not os.path.isfile(script):
            messagebox.showerror("Error", f"step08a_populate_v2.sh not found:\n{script}")
            return
        flroot = self._firstlvl_var.get().strip()
        troot  = self._taskroot_var.get().strip()
        if not flroot or not os.path.isdir(flroot):
            messagebox.showerror("Error", f"Step07 output root not found:\n{flroot}")
            return
        if not troot:
            messagebox.showerror("Error", "Set the task root (output) directory.")
            return
        cmd = ["bash", script, flroot, troot,
               self._spm_var.get().strip(), self._matlab_var.get().strip(),
               self._mcode_var.get().strip(), self._con_var.get().strip() or "wcon_0001.nii",
               self._env_var.get().strip() or "none"]
        self._launch(cmd, self._p1_btn, self._p1_prog, "Part 1 — Populate")

    def _run_part2(self):
        script = str(SCRIPTS_ROOT / "step08b_groups_v2.sh")
        if not os.path.isfile(script):
            messagebox.showerror("Error", f"step08b_groups_v2.sh not found:\n{script}")
            return
        troot = self._taskroot_var.get().strip()
        cases = self._cases_var.get().strip()
        ctrls = self._controls_var.get().strip()
        out   = self._group_out.get().strip()
        if not troot or not os.path.isdir(troot):
            messagebox.showerror("Error", f"Task root not found:\n{troot}\nRun Part 1 first.")
            return
        if not cases or not os.path.isfile(cases):
            messagebox.showerror("Error", f"Cases list not found:\n{cases}")
            return
        if not ctrls or not os.path.isfile(ctrls):
            messagebox.showerror("Error", f"Controls list not found:\n{ctrls}")
            return
        if not out:
            messagebox.showerror("Error", "Set the group output directory.")
            return
        comb = "1" if self._do_combined.get() else "0"
        covs = [c for c, v in (("age", self._cov_age), ("sex", self._cov_sex),
                               ("mean_fd", self._cov_fd)) if v.get()]
        cov_arg = ",".join(covs) if covs else "none"
        sd = self._cfg["sourcedata"].get().strip()
        bs_mask = self._cfg["brainstem_mask"].get().strip() if self._restrict_bs.get() else ""
        cmd = ["bash", script, troot, cases, ctrls, out,
               self._spm_var.get().strip(), self._matlab_var.get().strip(),
               self._mcode_var.get().strip(), comb,
               self._env_var.get().strip() or "none",
               self._combined_mode.get().strip() or "average",
               cov_arg, "", "", sd, "01",   # participants/fmriprep auto-derived from sourcedata
               bs_mask]
        self._launch(cmd, self._p2_btn, self._p2_prog, "Part 2 — Group analysis")

    def _launch(self, cmd, btn, prog, label):
        self._console.separator()
        self._console.append(f"[Step 08] {label} starting…", "info")
        self._console.separator()
        btn.config(state="disabled")
        prog.start(10)
        self._status.set(f"Step 08 — {label} running…")
        self._runner.run(
            cmd=cmd, cwd=str(SCRIPTS_ROOT),
            on_line=self._console.append,
            on_done=lambda rc, b=btn, p=prog, l=label: self._done(rc, b, p, l))

    def _done(self, rc, btn, prog, label):
        prog.stop()
        btn.config(state="normal")
        if rc == 0:
            self._status.set(f"Step 08 — {label} ✓")
            self._console.append(f"[Step 08] {label} finished.", "ok")
        else:
            self._status.set(f"Step 08 — {label} failed (exit {rc})")
            self._console.append(f"[Step 08] {label} failed (exit {rc}).", "error")


# ── Step 09 — Threshold group map (p<0.05) ────────────────────────────────────

class ThresholdPanel(ttk.Frame):
    """Step 09: threshold a step08b group contrast at p<0.05 → significance map."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner

        self._analysis_var = tk.StringVar()
        self._output_var   = tk.StringVar()
        self._spm_var      = cfg["spm_dir"]
        self._matlab_var   = cfg["matlab_exe"]
        self._mcode_var    = cfg["matlab_code"]
        self._env_var      = cfg["env_script"]
        self._p_var        = tk.StringVar(value="0.05")
        self._extent_var   = tk.StringVar(value="0")
        self._cidx_var     = tk.StringVar(value="1")
        self._tail_var     = tk.StringVar(value="pos")
        self._corr_var     = tk.StringVar(value="none")   # none | FWE | FDR (optional)
        self._restrict_bs  = tk.BooleanVar(value=False)    # restrict threshold to brainstem (C3)

        ttk.Label(self, text="Step 09 — Threshold group map",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Threshold a step08b group contrast (default 1 = Cases>Controls)\n"
                  "at p<0.05 → binary significance map + thresholded t-map.\n"
                  "Point at a step08b task folder (SPM.mat + spmT_000*.nii)."),
            foreground="gray", wraplength=600,
        ).pack(anchor="w", pady=(0, 10))

        pf = ttk.LabelFrame(self, text="Paths", padding=(10, 6))
        pf.pack(fill="x", pady=(0, 8))
        PathRow(pf, "Analysis dir (step08b task):", mode="dir",
                var=self._analysis_var, label_width=24).pack(fill="x", pady=2)
        PathRow(pf, "Output dir:", mode="dir",
                var=self._output_var, label_width=24).pack(fill="x", pady=2)
        PathRow(pf, "SPM12 dir:", mode="dir",
                var=self._spm_var, label_width=24).pack(fill="x", pady=2)
        PathRow(pf, "MATLAB exe:", mode="file",
                var=self._matlab_var, label_width=24).pack(fill="x", pady=2)
        PathRow(pf, "MATLAB code dir:", mode="dir",
                var=self._mcode_var, label_width=24).pack(fill="x", pady=2)
        PathRow(pf, "Environment script:", mode="file",
                filetypes=[("Shell", "*.sh"), ("All", "*.*")],
                var=self._env_var, label_width=24).pack(fill="x", pady=2)

        pm = ttk.LabelFrame(self, text="Threshold", padding=(10, 6))
        pm.pack(fill="x", pady=(0, 8))

        def er(lbl, var, w=8):
            r = ttk.Frame(pm); r.pack(side="left", padx=(0, 14))
            ttk.Label(r, text=lbl).pack(side="left")
            ttk.Entry(r, textvariable=var, width=w).pack(side="left", padx=(4, 0))

        er("p <", self._p_var, 6)
        er("Cluster extent (vox):", self._extent_var, 6)
        er("Contrast #:", self._cidx_var, 4)
        tr = ttk.Frame(pm); tr.pack(side="left", padx=(0, 6))
        ttk.Label(tr, text="Tail:").pack(side="left")
        ttk.Combobox(tr, textvariable=self._tail_var, width=6, state="readonly",
                     values=["pos", "neg", "two"]).pack(side="left", padx=(4, 0))
        cr = ttk.Frame(pm); cr.pack(side="left", padx=(0, 6))
        ttk.Label(cr, text="Correction:").pack(side="left")
        ttk.Combobox(cr, textvariable=self._corr_var, width=6, state="readonly",
                     values=["none", "FWE", "FDR"]).pack(side="left", padx=(4, 0))

        ttk.Label(self, foreground="gray", wraplength=600,
                  text=("Correction is OPTIONAL — 'none' (uncorrected) is fine for this pilot. "
                        "'FWE'/'FDR' add voxel-wise multiple-comparison control; output filenames "
                        "are tagged with the method (unc/FWE/FDR).")).pack(anchor="w", pady=(6, 0))
        ttk.Checkbutton(self, text="Restrict to brainstem mask (intersect with cfg brainstem_mask; Task 05)",
                        variable=self._restrict_bs).pack(anchor="w", pady=(2, 0))

        cfg["sourcedata"].trace_add("write", lambda *_: self._sync())
        self._sync()

        ttk.Separator(self).pack(fill="x", pady=8)
        row = ttk.Frame(self); row.pack(anchor="w")
        self._run_btn = ttk.Button(row, text="▶  Run Step 09 — Threshold", command=self._run)
        self._run_btn.pack(side="left")
        self._progress = ttk.Progressbar(row, mode="indeterminate", length=220)
        self._progress.pack(side="left", padx=12)

    def _sync(self):
        sd = self._cfg["sourcedata"].get().strip()
        if sd and not self._output_var.get():
            self._output_var.set(str(Path(sd) / "derivatives" / "spm" / "second_level" / "thresholded"))

    def _run(self):
        script = str(SCRIPTS_ROOT / "step09_p_value.sh")
        if not os.path.isfile(script):
            messagebox.showerror("Error", f"step09_p_value.sh not found:\n{script}"); return
        ana = self._analysis_var.get().strip()
        out = self._output_var.get().strip()
        if not ana or not os.path.isfile(os.path.join(ana, "SPM.mat")):
            messagebox.showerror("Error", f"SPM.mat not found in:\n{ana}"); return
        if not out:
            messagebox.showerror("Error", "Set an output dir."); return
        cmd = ["bash", script, ana, out,
               self._spm_var.get().strip(), self._matlab_var.get().strip(),
               self._mcode_var.get().strip(), self._p_var.get().strip() or "0.05",
               self._extent_var.get().strip() or "0", self._cidx_var.get().strip() or "1",
               self._tail_var.get().strip() or "pos", self._env_var.get().strip() or "none",
               self._corr_var.get().strip() or "none",
               (self._cfg["brainstem_mask"].get().strip() if self._restrict_bs.get() else "")]
        self._console.separator()
        self._console.append("[Step 09] Thresholding group map…", "info")
        self._console.separator()
        self._run_btn.config(state="disabled"); self._progress.start(10)
        self._status.set("Step 09 running…")
        self._runner.run(cmd=cmd, cwd=str(SCRIPTS_ROOT),
                         on_line=self._console.append, on_done=self._done)

    def _done(self, rc):
        self._progress.stop(); self._run_btn.config(state="normal")
        if rc == 0:
            self._status.set("Step 09 complete ✓")
            self._console.append("[Step 09] Threshold map written.", "ok")
        else:
            self._status.set(f"Step 09 failed (exit {rc})")
            self._console.append(f"[Step 09] Failed (exit {rc}).", "error")


# ── Step 10 — ROI extraction + spheres ────────────────────────────────────────

class RoiPanel(ttk.Frame):
    """Step 10: at a coordinate, extract per-subject wcon values, build spheres,
       and mask a con with the large sphere."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner

        self._x_var       = tk.StringVar()
        self._y_var       = tk.StringVar()
        self._z_var       = tk.StringVar()
        self._mode_var    = tk.StringVar(value="mm")
        self._wcon_var    = tk.StringVar()
        self._output_var  = tk.StringVar()
        self._con_var     = tk.StringVar()
        self._gcon_var    = tk.StringVar()   # group-comparison contrast to mask
        self._gmask_var   = tk.StringVar()   # 10 mm mask (manually selected)
        self._sigmask_var = tk.StringVar()   # optional step09 corrected significance mask
        self._roimask_var  = tk.StringVar()  # optional whole-mask ROI (e.g. brainstem) (C4)
        self._roiatlas_var = cfg["brainstem_atlas"]  # labeled atlas (set once in Setup)
        self._roilabels_var = tk.StringVar() # atlas label values
        self._roinames_var  = tk.StringVar() # atlas label names
        self._rsmall_var  = tk.StringVar(value="5")
        self._rlarge_var  = tk.StringVar(value="10")
        self._python_var  = cfg["python_exe"]

        ttk.Label(self, text="Step 10 — ROI extraction + spheres",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("At a coordinate (e.g. a peak from step09): extract each subject's\n"
                  "single-voxel + 5 mm-sphere wcon value → CSV, write 5/10 mm sphere masks,\n"
                  "and mask a manually-selected con with the 10 mm sphere."),
            foreground="gray", wraplength=600,
        ).pack(anchor="w", pady=(0, 10))

        cf = ttk.LabelFrame(self, text="Coordinate", padding=(10, 6))
        cf.pack(fill="x", pady=(0, 8))
        cr = ttk.Frame(cf); cr.pack(fill="x")
        ttk.Label(cr, text="X:").pack(side="left")
        ttk.Entry(cr, textvariable=self._x_var, width=7).pack(side="left", padx=(2, 10))
        ttk.Label(cr, text="Y:").pack(side="left")
        ttk.Entry(cr, textvariable=self._y_var, width=7).pack(side="left", padx=(2, 10))
        ttk.Label(cr, text="Z:").pack(side="left")
        ttk.Entry(cr, textvariable=self._z_var, width=7).pack(side="left", padx=(2, 16))
        ttk.Label(cr, text="as:").pack(side="left")
        ttk.Combobox(cr, textvariable=self._mode_var, width=7, state="readonly",
                     values=["mm", "voxel"]).pack(side="left", padx=(4, 0))

        pf = ttk.LabelFrame(self, text="Paths", padding=(10, 6))
        pf.pack(fill="x", pady=(0, 8))
        PathRow(pf, "wcon dir (per-subject):", mode="dir",
                var=self._wcon_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "Output dir:", mode="dir",
                var=self._output_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "Con to mask (10 mm):", mode="file",
                filetypes=[("NIfTI", "*.nii *.nii.gz"), ("All", "*.*")],
                var=self._con_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "Significance mask (opt):", mode="file",
                filetypes=[("NIfTI", "*.nii *.nii.gz"), ("All", "*.*")],
                var=self._sigmask_var, label_width=22).pack(fill="x", pady=2)
        ttk.Label(pf, text="(optional step09 corrected *_mask.nii — adds a sphere mean "
                           "restricted to significant voxels)",
                  foreground="gray", wraplength=560).pack(anchor="w")

        PathRow(pf, "Python exe:", mode="file",
                var=self._python_var, label_width=22).pack(fill="x", pady=2)

        # Mask a group-comparison contrast with a manually-selected mask
        gm = ttk.LabelFrame(self, text="Mask a group-comparison contrast (manually select both)",
                            padding=(10, 6))
        gm.pack(fill="x", pady=(0, 8))
        PathRow(gm, "Group contrast:", mode="file",
                filetypes=[("NIfTI", "*.nii *.nii.gz"), ("All", "*.*")],
                var=self._gcon_var, label_width=18).pack(fill="x", pady=2)
        PathRow(gm, "10 mm mask:", mode="file",
                filetypes=[("NIfTI", "*.nii *.nii.gz"), ("All", "*.*")],
                var=self._gmask_var, label_width=18).pack(fill="x", pady=2)
        ttk.Label(gm, text="Writes <contrast>_groupmasked.nii (values inside the mask, 0 outside).",
                  foreground="gray", wraplength=560).pack(anchor="w", pady=(2, 0))

        # Mask / atlas ROIs — coordinate-free per-subject means (Task 05 C4)
        am = ttk.LabelFrame(self, text="Mask / atlas ROIs (coordinate-free; X/Y/Z optional)",
                            padding=(10, 6))
        am.pack(fill="x", pady=(0, 8))
        PathRow(am, "ROI mask (whole-mask mean):", mode="file",
                filetypes=[("NIfTI", "*.nii *.nii.gz"), ("All", "*.*")],
                var=self._roimask_var, label_width=24).pack(fill="x", pady=2)
        PathRow(am, "Labeled atlas (per-nucleus):", mode="file",
                filetypes=[("NIfTI", "*.nii *.nii.gz"), ("All", "*.*")],
                var=self._roiatlas_var, label_width=24).pack(fill="x", pady=2)
        alr = ttk.Frame(am); alr.pack(fill="x", pady=2)
        ttk.Label(alr, text="Labels:").pack(side="left")
        ttk.Entry(alr, textvariable=self._roilabels_var, width=16).pack(side="left", padx=(4, 12))
        ttk.Label(alr, text="Names:").pack(side="left")
        ttk.Entry(alr, textvariable=self._roinames_var, width=20).pack(side="left", padx=(4, 0))
        ttk.Label(am, foreground="gray", wraplength=560,
                  text=("ROI mask → one mean column per subject (e.g. the brainstem mask). "
                        "Labeled atlas + label values (+ optional names) → one mean column per "
                        "nucleus. Leave X/Y/Z empty to run these alone.")).pack(anchor="w")

        rf = ttk.LabelFrame(self, text="Sphere radii (mm)", padding=(10, 6))
        rf.pack(fill="x", pady=(0, 8))
        rr = ttk.Frame(rf); rr.pack(fill="x")
        ttk.Label(rr, text="Small (values):").pack(side="left")
        ttk.Entry(rr, textvariable=self._rsmall_var, width=5).pack(side="left", padx=(4, 16))
        ttk.Label(rr, text="Large (mask con):").pack(side="left")
        ttk.Entry(rr, textvariable=self._rlarge_var, width=5).pack(side="left", padx=(4, 0))

        ttk.Separator(self).pack(fill="x", pady=8)
        row = ttk.Frame(self); row.pack(anchor="w")
        self._run_btn = ttk.Button(row, text="▶  Run Step 10 — Extract", command=self._run)
        self._run_btn.pack(side="left")
        self._progress = ttk.Progressbar(row, mode="indeterminate", length=220)
        self._progress.pack(side="left", padx=12)

    def _run(self):
        script = str(SCRIPTS_ROOT / "step10_ROI.sh")
        if not os.path.isfile(script):
            messagebox.showerror("Error", f"step10_ROI.sh not found:\n{script}"); return
        x, y, z = (self._x_var.get().strip(), self._y_var.get().strip(), self._z_var.get().strip())
        roi_mask  = self._roimask_var.get().strip()
        roi_atlas = self._roiatlas_var.get().strip()
        if not (x and y and z) and not (roi_mask or roi_atlas):
            messagebox.showerror("Error",
                "Enter X, Y, Z coordinates, or set a ROI mask / atlas (coordinate-free)."); return
        wcon = self._wcon_var.get().strip()
        out  = self._output_var.get().strip()
        if not wcon or not os.path.isdir(wcon):
            messagebox.showerror("Error", f"wcon directory not found:\n{wcon}"); return
        if not out:
            messagebox.showerror("Error", "Set an output dir."); return
        con   = self._con_var.get().strip()    # optional
        gcon  = self._gcon_var.get().strip()   # optional group contrast
        gmask = self._gmask_var.get().strip()  # optional mask
        cmd = ["bash", script, x, y, z, wcon, out, con,
               self._rsmall_var.get().strip() or "5",
               self._rlarge_var.get().strip() or "10",
               self._mode_var.get().strip() or "mm",
               self._python_var.get().strip(),
               gcon, gmask, self._sigmask_var.get().strip(),
               roi_mask, roi_atlas,
               self._roilabels_var.get().strip(), self._roinames_var.get().strip()]
        self._console.separator()
        self._console.append(f"[Step 10] ROI extraction at ({x},{y},{z}) {self._mode_var.get()}…", "info")
        self._console.separator()
        self._run_btn.config(state="disabled"); self._progress.start(10)
        self._status.set("Step 10 running…")
        self._runner.run(cmd=cmd, cwd=str(SCRIPTS_ROOT),
                         on_line=self._console.append, on_done=self._done)

    def _done(self, rc):
        self._progress.stop(); self._run_btn.config(state="normal")
        if rc == 0:
            self._status.set("Step 10 complete ✓")
            self._console.append("[Step 10] roi_values.csv + spheres written.", "ok")
        else:
            self._status.set(f"Step 10 failed (exit {rc})")
            self._console.append(f"[Step 10] Failed (exit {rc}).", "error")


# ── Step navigation dashboard ─────────────────────────────────────────────────

class StepNav(ttk.Frame):
    """Scrollable left-nav column + right content area.

    Uses tk.Label (not tk.Button) for nav items because tk.Label
    respects bg/fg on macOS — tk.Button ignores them under Aqua.
    The nav column is a Canvas so it scrolls when items overflow.
    """

    _BG       = "#2d2d2d"
    _BG_HOVER = "#3e3e3e"
    _BG_SEL   = "#1a5c96"
    _FG       = "#d4d4d4"
    _FG_SEL   = "#ffffff"
    _FG_SECT  = "#888888"

    def __init__(self, parent, **kwargs):
        super().__init__(parent, **kwargs)

        # ── Left nav wrapper (fixed width) ────────────────────────────────────
        nav_outer = tk.Frame(self, bg=self._BG, width=205)
        nav_outer.pack(side="left", fill="y")
        nav_outer.pack_propagate(False)

        # Canvas makes the nav scrollable when items overflow
        self._canvas = tk.Canvas(nav_outer, bg=self._BG,
                                  highlightthickness=0, bd=0)
        self._canvas.pack(fill="both", expand=True)

        # Inner frame holds all nav labels/section headers
        self._nav = tk.Frame(self._canvas, bg=self._BG)
        self._win = self._canvas.create_window((0, 0), window=self._nav, anchor="nw")

        self._nav.bind("<Configure>", lambda _: self._canvas.configure(
            scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>", lambda e: self._canvas.itemconfig(
            self._win, width=e.width))

        # Mousewheel scroll while cursor is over the nav
        nav_outer.bind("<Enter>", lambda _: self._canvas.bind_all(
            "<MouseWheel>", self._on_wheel))
        nav_outer.bind("<Leave>", lambda _: self._canvas.unbind_all("<MouseWheel>"))

        # ── 1-px separator ────────────────────────────────────────────────────
        tk.Frame(self, bg="#505050", width=1).pack(side="left", fill="y")

        # ── Right content area ────────────────────────────────────────────────
        self._content = ttk.Frame(self)
        self._content.pack(side="left", fill="both", expand=True)

        self._panels:  dict = {}
        self._buttons: dict = {}
        self._active:  str | None = None

    def _on_wheel(self, event):
        self._canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def add(self, label: str, widget: tk.Widget, section: str | None = None):
        """Register *widget* under *label*.  *section* inserts a dim group header."""
        if section:
            tk.Label(
                self._nav, text=section.upper(),
                bg=self._BG, fg=self._FG_SECT,
                font=("Helvetica", 9, "bold"),
                anchor="w", padx=14, pady=0,
            ).pack(fill="x", pady=(10, 1))

        # All panels live in the content area, stacked via place()
        widget.place(in_=self._content, x=0, y=0, relwidth=1, relheight=1)

        # tk.Label respects bg/fg on macOS; tk.Button does not under Aqua
        lbl = tk.Label(
            self._nav, text=label,
            anchor="w", padx=14, pady=8,
            bg=self._BG, fg=self._FG,
            cursor="hand2", font=("Helvetica", 11),
        )
        lbl.pack(fill="x")
        lbl.bind("<Button-1>", lambda e, l=label: self.show(l))
        lbl.bind("<Enter>",    lambda e, b=lbl, l=label:
                 b.config(bg=self._BG_HOVER) if self._active != l else None)
        lbl.bind("<Leave>",    lambda e, b=lbl, l=label:
                 b.config(bg=self._BG_SEL if self._active == l else self._BG))

        self._panels[label]  = widget
        self._buttons[label] = lbl

        if self._active is None:
            self.show(label)

    def show(self, label: str):
        if self._active and self._active in self._buttons:
            self._buttons[self._active].config(bg=self._BG, fg=self._FG)
        self._active = label
        self._buttons[label].config(bg=self._BG_SEL, fg=self._FG_SEL)
        self._panels[label].lift()


# ── Project inventory ──────────────────────────────────────────────────────────

class ProjectInventory:
    """Scan the data-project folders (rawdata, sourcedata, derivatives, subject
    lists) and persist a snapshot to project_inventory.json in the project root.
    A re-scan diffs against the saved snapshot to report what changed."""

    def __init__(self, cfg: dict):
        self._cfg = cfg

    # ── locations ─────────────────────────────────────────────────────────────
    def project_root(self) -> Path:
        op = self._cfg["out_path"].get().strip()
        sd = self._cfg["sourcedata"].get().strip()
        if op and sd and Path(op).parent == Path(sd).parent:
            return Path(op).parent
        if sd:
            return Path(sd).parent
        return SCRIPTS_ROOT

    def json_path(self) -> Path:
        return self.project_root() / "project_inventory.json"

    # ── helpers ───────────────────────────────────────────────────────────────
    @staticmethod
    def _subdirs(path: str):
        if not path or not os.path.isdir(path):
            return []
        return sorted(d.name for d in Path(path).iterdir()
                      if d.is_dir() and not d.name.startswith("."))

    @staticmethod
    def _read_list(path: str):
        if not path or not os.path.isfile(path):
            return []
        with open(path) as f:
            return [ln.strip() for ln in f if ln.strip()]

    # ── scan ──────────────────────────────────────────────────────────────────
    def scan(self) -> dict:
        out_path   = self._cfg["out_path"].get().strip()
        sourcedata = self._cfg["sourcedata"].get().strip()
        deriv      = os.path.join(sourcedata, "derivatives") if sourcedata else ""
        heudiconv  = os.path.join(sourcedata, ".heudiconv") if sourcedata else ""

        inv = {
            "scanned":      datetime.datetime.now().isoformat(timespec="seconds"),
            "project_root": str(self.project_root()),
            "rawdata":      {"path": out_path,   "subjects": self._subdirs(out_path)},
            "sourcedata":   {"path": sourcedata,
                             "bids_subjects": [s for s in self._subdirs(sourcedata)
                                               if s.startswith("sub-")],
                             "heudiconv":     self._subdirs(heudiconv)},
            "derivatives":  {},
            "subject_lists": {
                "SubjectList":     self._read_list(self._cfg["subjlist"].get().strip()),
                "SubjectListBIDS": self._read_list(self._cfg["subjlist_bids"].get().strip()),
            },
        }
        if deriv and os.path.isdir(deriv):
            for sub in self._subdirs(deriv):
                inv["derivatives"][sub] = self._subdirs(os.path.join(deriv, sub))
        return inv

    def load(self) -> dict:
        p = self.json_path()
        if p.exists():
            try:
                with open(p) as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save(self, inv: dict):
        try:
            with open(self.json_path(), "w") as f:
                json.dump(inv, f, indent=2)
        except Exception:
            pass

    # ── diff ──────────────────────────────────────────────────────────────────
    def diff(self, old: dict, new: dict):
        """Return a list of human-readable change strings (added/removed)."""
        changes = []

        def _cmp(label, a, b):
            a, b = set(a or []), set(b or [])
            for x in sorted(b - a):
                changes.append(f"+ {label}: {x}")
            for x in sorted(a - b):
                changes.append(f"- {label}: {x}")

        if not old:
            return ["(first scan — baseline saved)"]

        _cmp("rawdata", old.get("rawdata", {}).get("subjects"),
                        new.get("rawdata", {}).get("subjects"))
        _cmp("sourcedata", old.get("sourcedata", {}).get("bids_subjects"),
                           new.get("sourcedata", {}).get("bids_subjects"))
        _cmp(".heudiconv", old.get("sourcedata", {}).get("heudiconv"),
                           new.get("sourcedata", {}).get("heudiconv"))
        old_d, new_d = old.get("derivatives", {}), new.get("derivatives", {})
        for d in sorted(set(old_d) | set(new_d)):
            _cmp(f"derivatives/{d}", old_d.get(d), new_d.get(d))
        old_l, new_l = old.get("subject_lists", {}), new.get("subject_lists", {})
        for lst in ("SubjectList", "SubjectListBIDS"):
            _cmp(f"list:{lst}", old_l.get(lst), new_l.get(lst))
        return changes

    def codes_logs_dir(self) -> Path:
        return self.project_root() / "codes" / "logs"

    def write_log(self, inv: dict):
        """Write a dated snapshot to <project>/codes/logs/ on every check."""
        ld = self.codes_logs_dir()
        try:
            ld.mkdir(parents=True, exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y-%m-%d_%H%M%S")
            log_path = ld / f"project_check_{stamp}.json"
            with open(log_path, "w") as f:
                json.dump(inv, f, indent=2)
            return log_path
        except Exception:
            return None

    def check(self):
        """Re-scan, diff, persist the snapshot, and append a dated log.
        Returns (inv, changes, log_path)."""
        new = self.scan()
        old = self.load()
        changes = self.diff(old, new)
        self.save(new)
        log_path = self.write_log(new)
        return new, changes, log_path


# ── Brainstem Mask tool (Task 05, Part C1) ────────────────────────────────────

class BrainstemMaskPanel(ttk.Frame):
    """Build a binary brainstem mask from a user-supplied atlas, resampled to the
    analysis grid. The mask (cfg['brainstem_mask']) restricts steps 07/08/09/10
    to the brainstem. BYO atlas — see the recommended sources below."""

    _RECOMMENDED = (
        "Recommended brainstem atlases (download/generate, in MNI — match fMRIPrep's "
        "MNI152NLin2009cAsym):\n"
        "  • Brainstem Navigator (Bianciardi lab) — probabilistic nuclei (NTS/NST, LC, "
        "raphe, PAG, PBN…): nitrc.org/projects/brainstemnavig\n"
        "  • Harvard Ascending Arousal Network (AAN) atlas — LC, raphe, PBN, PAG…\n"
        "  • SUIT cerebellum/brainstem, or a hand-drawn brainstem ROI.\n"
        "Probabilistic atlas → set a threshold; labeled atlas → list the label values; "
        "reference = a wcon_*.nii or the fMRIPrep MNI BOLD (sets the output grid)."
    )

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg = cfg; self._console = console
        self._status = status_var; self._runner = runner

        self._atlas_var  = tk.StringVar()
        self._ref_var    = tk.StringVar()
        self._out_var    = cfg["brainstem_mask"]
        self._thr_var    = tk.StringVar(value="0.0")
        self._labels_var = tk.StringVar(value="")
        self._dilate_var = tk.StringVar(value="0")
        self._python_var = cfg["python_exe"]

        ttk.Label(self, text="Brainstem Mask  (Task 05 — restrict analysis to the brainstem)",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(self, text=("Builds a binary brainstem mask from YOUR atlas, resampled to the "
                              "analysis grid. Used as the explicit mask in steps 07/08/09/10."),
                  foreground="gray", wraplength=600).pack(anchor="w", pady=(0, 8))

        pf = ttk.LabelFrame(self, text="Atlas → mask", padding=(10, 6))
        pf.pack(fill="x", pady=(0, 8))
        PathRow(pf, "Brainstem atlas/mask:", mode="file",
                filetypes=[("NIfTI", "*.nii *.nii.gz"), ("All", "*.*")],
                var=self._atlas_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "Reference grid (wcon/BOLD):", mode="file",
                filetypes=[("NIfTI", "*.nii *.nii.gz"), ("All", "*.*")],
                var=self._ref_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "Output mask:", mode="file",
                filetypes=[("NIfTI", "*.nii *.nii.gz"), ("All", "*.*")],
                var=self._out_var, label_width=22).pack(fill="x", pady=2)
        PathRow(pf, "Python exe:", mode="file",
                var=self._python_var, label_width=22).pack(fill="x", pady=2)

        pm = ttk.Frame(pf); pm.pack(fill="x", pady=(4, 0))
        ttk.Label(pm, text="Threshold (prob):").pack(side="left")
        ttk.Entry(pm, textvariable=self._thr_var, width=6).pack(side="left", padx=(4, 14))
        ttk.Label(pm, text="Labels (ints):").pack(side="left")
        ttk.Entry(pm, textvariable=self._labels_var, width=14).pack(side="left", padx=(4, 14))
        ttk.Label(pm, text="Dilate (vox):").pack(side="left")
        ttk.Entry(pm, textvariable=self._dilate_var, width=5).pack(side="left", padx=(4, 0))

        ttk.Label(self, text=self._RECOMMENDED, foreground="#4a6", wraplength=620,
                  justify="left").pack(anchor="w", pady=(8, 8))

        # default output under sourcedata derivatives
        def _sync(*_):
            sd = cfg["sourcedata"].get().strip()
            if sd and not self._out_var.get():
                self._out_var.set(str(Path(sd) / "derivatives" / "brainstem" / "brainstem_mask.nii"))
        cfg["sourcedata"].trace_add("write", _sync); _sync()

        row = ttk.Frame(self); row.pack(anchor="w")
        self._btn = ttk.Button(row, text="▶  Build brainstem mask", command=self._run)
        self._btn.pack(side="left")
        self._progress = ttk.Progressbar(row, mode="indeterminate", length=220)
        self._progress.pack(side="left", padx=12)

    def _run(self):
        script = str(SCRIPTS_ROOT / "utility" / "prep_brainstem_mask.py")
        atlas = self._atlas_var.get().strip()
        ref   = self._ref_var.get().strip()
        out   = self._out_var.get().strip()
        if not (atlas and os.path.isfile(atlas)):
            messagebox.showerror("Error", f"Atlas not found:\n{atlas}"); return
        if not (ref and os.path.isfile(ref)):
            messagebox.showerror("Error", f"Reference grid NIfTI not found:\n{ref}"); return
        if not out:
            messagebox.showerror("Error", "Set an output mask path."); return
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        cmd = [self._python_var.get().strip() or "python3", script,
               "--atlas", atlas, "--reference", ref, "--output", out,
               "--threshold", self._thr_var.get().strip() or "0.0"]
        labels = self._labels_var.get().replace(",", " ").split()
        if labels:
            cmd += ["--labels", *labels]
        dil = self._dilate_var.get().strip()
        if dil and dil != "0":
            cmd += ["--dilate", dil]
        self._console.separator()
        self._console.append("[Brainstem mask] building…", "info")
        self._btn.config(state="disabled"); self._progress.start(10)
        self._status.set("Building brainstem mask…")
        self._runner.run(cmd=cmd, cwd=str(SCRIPTS_ROOT),
                         on_line=self._console.append, on_done=self._done)

    def _done(self, rc):
        self._progress.stop(); self._btn.config(state="normal")
        if rc == 0:
            self._status.set("Brainstem mask built ✓")
            self._console.append("[Brainstem mask] done. Path stored for steps 07/08/09/10.", "ok")
        else:
            self._status.set(f"Brainstem mask failed (exit {rc})")
            self._console.append(f"[Brainstem mask] failed (exit {rc}).", "error")


# ── Project sidebar ────────────────────────────────────────────────────────────

class SidebarPanel(ttk.Frame):
    """Left sidebar: a project explorer that inventories rawdata / sourcedata /
    derivatives and the (temporary) subject lists, tracked in project_inventory.json.
    The Check button re-scans the folders and reports changes."""

    def __init__(self, parent, cfg: dict, console: "Console" = None, **kwargs):
        super().__init__(parent, **kwargs)
        self._inv     = ProjectInventory(cfg)
        self._console = console
        self._cfg     = cfg

        header = ttk.Frame(self)
        header.pack(fill="x", padx=6, pady=(6, 2))
        ttk.Label(header, text="Project",
                  font=("Helvetica", 11, "bold")).pack(side="left")
        ttk.Button(header, text="✓ Check", command=self.check).pack(side="right")

        # Project folder — manually selected; sets rawdata + sourcedata under it.
        proj_row = ttk.Frame(self)
        proj_row.pack(fill="x", padx=6, pady=(2, 0))
        ttk.Label(proj_row, text="Folder:", width=7, anchor="w").pack(side="left")
        ttk.Entry(proj_row, textvariable=cfg["project_root"]).pack(
            side="left", fill="x", expand=True, padx=(0, 4))
        ttk.Button(proj_row, text="…", width=3, command=self._browse_project).pack(side="left")

        # New project creator
        new_row = ttk.Frame(self)
        new_row.pack(fill="x", padx=6, pady=(2, 2))
        ttk.Label(new_row, text="New:", width=7, anchor="w").pack(side="left")
        self._newname_var = tk.StringVar()
        ent = ttk.Entry(new_row, textvariable=self._newname_var)
        ent.pack(side="left", fill="x", expand=True, padx=(0, 4))
        ent.bind("<Return>", lambda _: self._create_project())
        ttk.Button(new_row, text="+ Create", command=self._create_project).pack(side="left")

        self._root_lbl = ttk.Label(self, foreground="#9cdcfe", font=("Menlo", 9),
                                   wraplength=300)
        self._root_lbl.pack(anchor="w", padx=6)

        # When the project folder changes, point rawdata/sourcedata under it.
        cfg["project_root"].trace_add("write", self._on_project_change)

        tv_frame = ttk.Frame(self)
        tv_frame.pack(fill="both", expand=True, padx=(6, 0), pady=(4, 4))
        self._tv = ttk.Treeview(tv_frame, show="tree", selectmode="browse")
        vsb = ttk.Scrollbar(tv_frame, orient="vertical", command=self._tv.yview)
        self._tv.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        self._tv.pack(side="left", fill="both", expand=True)
        self._tv.tag_configure("temp",  foreground="#dcdcaa")
        self._tv.tag_configure("count", foreground="#4ec9b0")
        self._tv.tag_configure("empty", foreground="#f44747")

        self._scanned_lbl = ttk.Label(self, foreground="#6a6a6a", font=("Menlo", 9))
        self._scanned_lbl.pack(anchor="w", padx=6, pady=(0, 6))

        # Show the last saved snapshot immediately for fast startup
        saved = self._inv.load()
        if saved:
            self._render(saved)
        else:
            self._root_lbl.config(text="(scanning project…)")

        # Auto-check: once on launch, and whenever the project folder changes
        # (debounced so it doesn't fire on every keystroke).
        self._autocheck_id = None
        cfg["sourcedata"].trace_add("write", self._schedule_autocheck)
        self._schedule_autocheck()

    # ── actions ───────────────────────────────────────────────────────────────
    def check(self):
        inv, changes, log_path = self._inv.check()
        self._render(inv)
        if self._console:
            self._console.separator()
            self._console.append(f"[Project] Checked {inv['project_root']}", "info")
            if changes:
                for c in changes:
                    tag = "ok" if c.startswith("+") else ("warn" if c.startswith("-") else "dim")
                    self._console.append(f"  {c}", tag)
            else:
                self._console.append("  No changes since last check.", "dim")
            self._console.append(f"[Project] Inventory saved: {self._inv.json_path()}", "dim")
            if log_path:
                self._console.append(f"[Project] Log: {log_path}", "dim")

    def _schedule_autocheck(self, *_):
        """Debounced auto-check — fires once the project folder stops changing."""
        if self._autocheck_id is not None:
            try:
                self.after_cancel(self._autocheck_id)
            except Exception:
                pass
        self._autocheck_id = self.after(700, self._maybe_autocheck)

    def _maybe_autocheck(self):
        self._autocheck_id = None
        sd = self._inv._cfg["sourcedata"].get().strip()
        if sd and os.path.isdir(sd):
            self.check()

    # ── project folder selection / creation ──────────────────────────────────
    def _browse_project(self):
        d = filedialog.askdirectory(title="Select project folder")
        if d:
            self._cfg["project_root"].set(d)

    def _on_project_change(self, *_):
        """Point rawdata + sourcedata + the subject lists under the selected
        project folder. Changing sourcedata cascades (fMRIPrep + auto-check)."""
        pr = self._cfg["project_root"].get().strip()
        if not pr:
            return
        self._cfg["out_path"].set(str(Path(pr) / "rawdata"))
        self._cfg["sourcedata"].set(str(Path(pr) / "sourcedata"))
        # Subject lists live in <project>/codes/
        self._cfg["subjlist"].set(str(Path(pr) / "codes" / "SubjectList.txt"))
        self._cfg["subjlist_bids"].set(str(Path(pr) / "codes" / "SubjectListBIDS.txt"))

    def _create_project(self):
        parent = self._cfg["project_root"].get().strip()
        name   = self._newname_var.get().strip()
        if not parent:
            messagebox.showerror("Error", "Select a project folder (parent location) first.")
            return
        if not name:
            messagebox.showerror("Error", "Enter a name for the new project.")
            return
        proj = Path(parent) / name
        if proj.exists():
            if not messagebox.askyesno(
                    "Exists", f"{proj} already exists. Create missing subfolders inside it?"):
                return
        # Project skeleton
        subdirs = [
            "codes",
            "rawdata",
            "sourcedata",
            "sourcedata/derivatives",
            "sourcedata/derivatives/fmriprep",
            "sourcedata/derivatives/freesurfer",
        ]
        try:
            for s in subdirs:
                (proj / s).mkdir(parents=True, exist_ok=True)
        except Exception as e:
            messagebox.showerror("Create failed", str(e))
            return
        self._newname_var.set("")
        # Switch to the new project (triggers path derivation + check)
        self._cfg["project_root"].set(str(proj))
        if self._console:
            self._console.append(f"[Project] Created new project: {proj}", "ok")
            for s in subdirs:
                self._console.append(f"  + {s}", "dim")
        messagebox.showinfo("Project created",
                            f"Created:\n{proj}\n\n" + "\n".join(subdirs))

    # ── rendering ─────────────────────────────────────────────────────────────
    def _render(self, inv: dict):
        self._tv.delete(*self._tv.get_children())
        self._root_lbl.config(text=inv.get("project_root", ""))
        self._scanned_lbl.config(text=f"scanned: {inv.get('scanned', '—')}")

        def node(parent, text, items=None, temp=False):
            n = len(items) if items is not None else None
            label = text if n is None else f"{text}  ({n})"
            tag = "temp" if temp else ("count" if n else ("empty" if n == 0 else ""))
            iid = self._tv.insert(parent, "end", text=label, open=False, tags=(tag,))
            for it in (items or []):
                self._tv.insert(iid, "end", text=it)
            return iid

        node("", "rawdata",    inv.get("rawdata", {}).get("subjects", []))
        sd = inv.get("sourcedata", {})
        sd_node = node("", "sourcedata", sd.get("bids_subjects", []))
        node(sd_node, ".heudiconv", sd.get("heudiconv", []))

        deriv = inv.get("derivatives", {})
        d_node = self._tv.insert("", "end", text=f"derivatives  ({len(deriv)})",
                                 open=True, tags=("count" if deriv else "empty",))
        for name in sorted(deriv):
            node(d_node, name, deriv[name])

        lists = inv.get("subject_lists", {})
        l_node = self._tv.insert("", "end", text="subject lists (temporary)", open=True)
        node(l_node, "SubjectList.txt",     lists.get("SubjectList", []),     temp=True)
        node(l_node, "SubjectListBIDS.txt", lists.get("SubjectListBIDS", []), temp=True)


# ── Main Application ───────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("BIDS fMRI Pipeline")
        self.geometry("1120x900")
        self.minsize(860, 680)
        self._build()

    def _build(self):
        # ── Header ────────────────────────────────────────────────────────────
        header = ttk.Frame(self, padding=(10, 8, 10, 0))
        header.pack(fill="x")
        ttk.Label(header, text="BIDS fMRI Pipeline",
                  font=("Helvetica", 16, "bold")).pack(side="left")
        # Save / Load project configuration (all path fields across every panel)
        ttk.Button(header, text="💾 Save config", command=self._save_config).pack(side="left", padx=(16, 2))
        ttk.Button(header, text="📂 Load config", command=self._load_config).pack(side="left", padx=2)
        ttk.Label(header, text=str(SCRIPTS_ROOT),
                  foreground="gray", font=("Menlo", 10)).pack(side="right")
        ttk.Separator(self).pack(fill="x", padx=10, pady=6)

        # ── Shared state ──────────────────────────────────────────────────────
        cfg        = {k: tk.StringVar(value=v) for k, v in _DEFAULTS.items()}
        status_var = tk.StringVar(value="Ready")
        runner     = ScriptRunner(self)
        pipeline   = PipelineState(SCRIPTS_ROOT / "pipeline_state.json")

        # Auto-derive the fMRIPrep derivatives dir from BIDS sourcedata
        # (unless the user has set it to something else).
        def _derive_fmriprep(*_):
            sd = cfg["sourcedata"].get().strip()
            if sd:
                cfg["fmriprep"].set(str(Path(sd) / "derivatives" / "fmriprep"))
        cfg["sourcedata"].trace_add("write", _derive_fmriprep)

        # ── Status bar (packed bottom first so it always shows) ───────────────
        ttk.Label(self, textvariable=status_var,
                  relief="sunken", anchor="w",
                  padding=(6, 2)).pack(fill="x", side="bottom")

        # ── Outer horizontal pane: [Sidebar] | [Nav + Console] ───────────────
        # Both sides are draggable — user controls sidebar width and
        # console height independently.
        h_pane = ttk.PanedWindow(self, orient="horizontal")
        h_pane.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        # Right pane — vertical split: step nav (top) | console (bottom)
        v_pane = ttk.PanedWindow(h_pane, orient="vertical")

        # Console (created before the sidebar so the sidebar can log to it)
        con_frame = ttk.LabelFrame(v_pane, text="Console Output", padding=(6, 4))
        console = Console(con_frame)
        console.pack(fill="both", expand=True)
        btn_bar = ttk.Frame(con_frame)
        btn_bar.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_bar, text="Clear", command=console.clear).pack(side="right")

        # Left pane — Project explorer sidebar (resizable width)
        sidebar_frame = ttk.Frame(h_pane)
        sidebar = SidebarPanel(sidebar_frame, cfg, console)
        sidebar.pack(fill="both", expand=True)
        h_pane.add(sidebar_frame, weight=0)
        h_pane.add(v_pane, weight=1)

        # Step navigation (resizable height)
        nav_frame = ttk.Frame(v_pane)
        v_pane.add(nav_frame, weight=3)
        v_pane.add(con_frame, weight=1)

        # Set initial sash positions after the window is drawn
        def _set_sash():
            try:
                w = h_pane.winfo_width()
                if w > 10:
                    h_pane.sashpos(0, 340)
                h = v_pane.winfo_height()
                if h > 10:
                    v_pane.sashpos(0, max(100, h - 220))
            except Exception:
                pass
        self.after(200, _set_sash)

        # ── Step navigation dashboard ─────────────────────────────────────────
        nav = StepNav(nav_frame)
        nav.pack(fill="both", expand=True)

        def panel(Panel, *args, **kw):
            # Panels live inside the StepNav content area (place-managed)
            st = _ScrolledTab(nav._content)
            Panel(st.inner, *args, **kw).pack(fill="both", expand=True)
            return st

        t_setup  = panel(SetupPanel,     cfg)
        t_download = panel(DownloadPanel,    cfg, console, status_var, runner, state=pipeline)
        t_bids = panel(BidsPanel,    cfg, console, status_var, runner, state=pipeline)
        t_fmriprep = panel(FmriprepPanel,    cfg, console, status_var, runner, state=pipeline)
        t_physioparse = panel(PhysioparsePanel,    cfg, console, status_var, runner)
        t_preproc = panel(PreprocRdecoPanel,    cfg, console, status_var, runner)
        t_retroicor = panel(RetroicorPanel,    cfg, console, status_var, runner)
        t_stim = panel(StimPanel,    cfg, console, status_var, runner)
        t_firstlevel = panel(FirstLevelPanel,    cfg, console, status_var, runner)
        t_secondlevel = panel(SecondLevelPanel,    cfg, console, status_var, runner)
        t_threshold = panel(ThresholdPanel,    cfg, console, status_var, runner)
        t_roi = panel(RoiPanel,    cfg, console, status_var, runner)
        t_heur   = panel(HeuristicPanel, cfg, console, status_var)
        t_qc     = panel(QCPanel,        cfg, console, status_var, runner)
        t_bsmask = panel(BrainstemMaskPanel, cfg, console, status_var, runner)

        nav.add("⚙  Setup",               t_setup,  section="Config")
        # NEW ORDER: RETROICOR (native, physio image-correction) runs BEFORE fMRIPrep.
        nav.add("00  Download DICOMs",    t_download, section="Pipeline")
        nav.add("01  BIDS Conversion",    t_bids)
        nav.add("02  Physioparse",        t_physioparse)
        nav.add("03  Preprocess + RDECO", t_preproc)
        nav.add("04  RETROICOR",          t_retroicor)
        nav.add("05  fMRIPrep",           t_fmriprep)
        nav.add("06  Stim Triggers",      t_stim)
        nav.add("07  First-level + MNI",  t_firstlevel)
        nav.add("08  Second-level",       t_secondlevel)
        nav.add("09  Threshold p<0.05",   t_threshold)
        nav.add("10  ROI extraction",     t_roi)
        nav.add("∷  Heuristic",           t_heur,   section="Tools")
        nav.add("⬡  QC Snapshots",        t_qc)
        nav.add("⊟  Brainstem Mask",      t_bsmask)

        # Expose console/status so Save/Load config can report progress
        self._console    = console
        self._status_var = status_var

        console.append("BIDS fMRI Pipeline GUI ready.", "ok")
        console.append(f"Scripts root: {SCRIPTS_ROOT}", "dim")
        console.append(f"Pipeline state: {SCRIPTS_ROOT / 'pipeline_state.json'}", "dim")

    # ── Project configuration save/load ───────────────────────────────────────

    def _collect_path_vars(self) -> dict:
        """Walk the whole widget tree and collect every Entry / Combobox /
        Checkbutton / Radiobutton variable, keyed by its Tk variable name.

        Variable names (PY_VARn) are assigned in widget-creation order, which is
        deterministic for a given app version — so they round-trip reliably
        between Save and Load on the same build.
        """
        found: dict = {}

        def walk(w):
            cls = w.winfo_class()
            try:
                if cls in ("TEntry", "Entry", "TCombobox"):
                    name = str(w.cget("textvariable"))
                    if name:
                        found[name] = self.getvar(name)
                elif cls in ("TCheckbutton", "Checkbutton",
                             "TRadiobutton", "Radiobutton"):
                    name = str(w.cget("variable"))
                    if name:
                        found[name] = self.getvar(name)
            except tk.TclError:
                pass
            for child in w.winfo_children():
                walk(child)

        walk(self)
        return found

    def _save_config(self):
        path = filedialog.asksaveasfilename(
            title="Save project configuration",
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
            initialfile="tvns_project_config.json",
            initialdir=str(SCRIPTS_ROOT),
        )
        if not path:
            return
        fields = self._collect_path_vars()
        payload = {
            "_tvns_config_version": 1,
            "saved": datetime.datetime.now().isoformat(timespec="seconds"),
            "fields": fields,
        }
        try:
            with open(path, "w") as f:
                json.dump(payload, f, indent=2)
        except Exception as e:
            messagebox.showerror("Save failed", str(e))
            return
        self._console.append(f"[Config] Saved {len(fields)} fields → {path}", "ok")
        self._status_var.set(f"Config saved → {Path(path).name}")
        messagebox.showinfo("Saved", f"Saved {len(fields)} fields to:\n{path}")

    def _load_config(self):
        path = filedialog.askopenfilename(
            title="Load project configuration",
            filetypes=[("JSON", "*.json"), ("All", "*.*")],
            initialdir=str(SCRIPTS_ROOT),
        )
        if not path:
            return
        try:
            with open(path) as f:
                data = json.load(f)
        except Exception as e:
            messagebox.showerror("Load failed", str(e))
            return
        fields = data.get("fields", data)
        if not isinstance(fields, dict):
            messagebox.showerror("Load failed", "Unrecognised config file format.")
            return

        def apply():
            n = 0
            for name, val in fields.items():
                try:
                    self.setvar(name, val)
                    n += 1
                except tk.TclError:
                    pass
            return n

        # Apply once (fires traces that recompute derived fields), then again
        # after idle so the saved values win over any trace-driven recomputation.
        n = apply()
        self.after_idle(apply)
        self._console.append(f"[Config] Loaded {n} fields ← {path}", "ok")
        self._status_var.set(f"Config loaded ← {Path(path).name}")
        messagebox.showinfo("Loaded",
                            f"Restored {n} fields from:\n{path}\n\n"
                            "All path fields across the panels have been repopulated.")


if __name__ == "__main__":
    app = App()
    app.mainloop()
