#!/usr/bin/env python3
"""
TVNS BIDS Pipeline GUI

Tabs:
  Setup      — configure all paths + edit SubjectList.txt
  Step 00    — download raw DICOMs via findsession + rsync
  Step 01    — heudiconv BIDS conversion (pass 1 / sequences / pass 2)
  Heuristic  — view · edit · create heuristic.py files
"""

import csv
import datetime
import json
import os
import sys
import tempfile
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

SCRIPTS_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).parent))
from runner import ScriptRunner

# ── Defaults extracted from the existing shell scripts ────────────────────────

_DEFAULTS = {
    "out_path":     "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/rawdata",
    "sourcedata":   "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata",
    "heuristic":    str(SCRIPTS_ROOT / "utility" / "heuristic.py"),
    "env_activate": "/autofs/cluster/vagabond/USERS/MARIO/Packages/env/heudiconv/bin/activate",
    "subjlist":     str(SCRIPTS_ROOT / "utility" / "SubjectList.txt"),
}


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
        "info":  "#9cdcfe",
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
        ("step_02",  "fMRIPrep"),
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

def _run_fd_qc(derivatives_dir: str, subjects: list, threshold: float = 0.9) -> dict:
    """Parse fMRIPrep confounds TSVs; return {subj: {mean_fd, flagged, has_output}}."""
    results = {}
    for subj in subjects:
        func_dir = Path(derivatives_dir) / subj / "func"
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

        # ── Subject list ───────────────────────────────────────────────────
        subj_frame = ttk.LabelFrame(self, text="Subject List", padding=(10, 6))
        subj_frame.pack(fill="both", expand=True)
        SubjectListEditor(subj_frame, cfg["subjlist"]).pack(fill="both", expand=True)


# ── Step 00 Panel ──────────────────────────────────────────────────────────────

class Step00Panel(ttk.Frame):
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
        self._lbl_raw  = ttk.Label(summary, foreground="#9cdcfe")
        self._lbl_subj = ttk.Label(summary, foreground="#9cdcfe")
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

        # Build one bash script that processes subjects in sequence
        lines = ["set -e"]
        for subj in subjects:
            dest    = f"{out_path}/{subj}/DICOM/raw"
            log_dir = f"{out_path}/{subj}/DICOM/LOG"
            lines += [
                "",
                f"echo '============================================'",
                f"echo ' Subject : {subj}'",
                f"echo ' Started : '$(date)",
                f"echo '============================================'",
                f"mkdir -p '{log_dir}'",
                f"findsession_out=$(findsession '{subj}' 2>&1)",
                f"echo \"$findsession_out\" | tee '{log_dir}/findsession.txt'",
                f"dcmdir=$(echo \"$findsession_out\" | grep '^PATH' | awk '{{print $NF}}')",
                f"if [ -z \"$dcmdir\" ] || [ ! -d \"$dcmdir\" ]; then",
                f"  echo \"ERROR: could not resolve DICOM dir for {subj}\"",
                f"  date | tee '{log_dir}/step0_ERROR.txt'",
                f"  exit 1",
                f"fi",
                f"echo \"DICOM source: $dcmdir\"",
                f"mkdir -p '{dest}'",
                f"rsync -av --progress \"$dcmdir/\" '{dest}/' 2>&1 | tee '{log_dir}/rsync.log'",
                f"date | tee '{log_dir}/step0_DONE.txt'",
                f"echo 'Done: {subj}'",
            ]

        cmd = ["bash", "-c", "\n".join(lines)]

        self._last_subjects = subjects
        if self._state:
            self._state.update_many(subjects, "step_00", "running")

        self._console.separator()
        self._console.append(f"[Step 00]  {len(subjects)} subject(s): {', '.join(subjects)}", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
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
        self._lbl = [ttk.Label(summary, foreground="#9cdcfe") for _ in range(3)]
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

    def _run(self):
        raw_path   = self._cfg["out_path"].get()
        sourcedata = self._cfg["sourcedata"].get()
        heuristic  = self._cfg["heuristic"].get()
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

        parts = []
        for subj in subjects:
            dicom_dir = f"{raw_path}/{subj}/DICOM/raw"
            if self._pass_num == 1:
                cmd_str = (
                    f"heudiconv --files '{dicom_dir}' "
                    f"-o '{sourcedata}' "
                    f"-f convertall -s {subj} -ss {ss} -c none"
                )
            else:
                cmd_str = (
                    f"heudiconv --files '{dicom_dir}' "
                    f"-o '{sourcedata}' "
                    f"-f '{heuristic}' "
                    f"-s {subj} -ss {ss} -c dcm2niix -b --minmeta --overwrite"
                )
            parts.append(
                f"echo '=== {subj} ===' && {cmd_str} "
                f"&& echo '✓ Done: {subj}' || echo '✗ Failed: {subj}'"
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


# ── Step 01 Panel ──────────────────────────────────────────────────────────────

class Step01Panel(ttk.Frame):
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
        self._lbl_sd = ttk.Label(summary, foreground="#9cdcfe")
        self._lbl_sd.pack(anchor="w")
        cfg["sourcedata"].trace_add("write", lambda *_: self._update_label())
        self._update_label()

        env_row = ttk.Frame(self)
        env_row.grid(row=3, column=0, sticky="ew", pady=(0, 8))
        ttk.Label(env_row, text="Source env (for node/npm):", width=26, anchor="w").pack(side="left")
        self._env_var = tk.StringVar(value=str(SCRIPTS_ROOT / "utility" / "fmriprep_env.sh"))
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


# ── Step 02 — BIDS Subject List tab (step03 logic) ────────────────────────────

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
        with open(out_path, "w") as f:
            for raw in subjects:
                f.write(self._to_bids(raw) + "\n")
        self._console.append(f"[Step 02] BIDS list saved: {out_path}", "ok")
        self._status.set(f"BIDS list saved → {Path(out_path).name}")
        messagebox.showinfo("Saved", f"Saved:\n{out_path}")


# ── Step 02 — BIDS conversion sub-tab (calls step01_create_bids_v2.sh) ────────

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
        self._lbl = [ttk.Label(summary, foreground="#9cdcfe") for _ in range(4)]
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


# ── Step 02 — fMRIPrep sub-tab (local, via ScriptRunner) ──────────────────────

class _FmriprepTab(ttk.Frame):
    """Run fMRIPrep locally via Singularity, one subject at a time."""

    _DEFAULTS_FP = {
        "bids_dir":   "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata",
        "fp_der":     "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata/derivatives/fmriprep",
        "fs_dir":     "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/sourcedata/derivatives/freesurfer",
        "work_dir":   "/autofs/cluster/vagabond/USERS/MARIO/Projects/lyme/codes/working-fmriprep",
        "simg":       "/autofs/cluster/vagabond/USERS/MARIO/Pipelines/my_images/fmriprep-25.2.3.simg",
        "fs_license": "/autofs/cluster/vagabond/USERS/MARIO/Pipelines/license.txt",
    }

    def __init__(self, parent, console: Console, status_var: tk.StringVar,
                 runner: ScriptRunner, fp_env_var: tk.StringVar,
                 fp_subj_var: tk.StringVar,
                 state: "PipelineState | None" = None, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._console    = console
        self._status     = status_var
        self._runner     = runner
        self._fp_env_var = fp_env_var
        self._fp_subj    = fp_subj_var
        self._state      = state
        self._last_subjects: list = []
        self._vars       = {k: tk.StringVar(value=v) for k, v in self._DEFAULTS_FP.items()}

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
            ("BIDS dir:",         "bids_dir",  "dir",  None),
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
        self._ignore_st = tk.BooleanVar(value=True)
        self._skip_bids = tk.BooleanVar(value=True)
        self._cifti     = tk.BooleanVar(value=True)
        ttk.Checkbutton(chk_row, text="--ignore slicetiming",   variable=self._ignore_st).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(chk_row, text="--skip-bids-validation", variable=self._skip_bids).pack(side="left", padx=(0, 12))
        ttk.Checkbutton(chk_row, text="--cifti-output",         variable=self._cifti).pack(side="left")

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
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=200)
        self._progress.pack(side="left", padx=12)

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

        bids_dir = self._vars["bids_dir"].get()
        fp_der   = self._vars["fp_der"].get()
        fs_dir   = self._vars["fs_dir"].get()
        work_dir = self._vars["work_dir"].get()
        simg     = self._vars["simg"].get()
        fs_lic   = self._vars["fs_license"].get()
        fp_env   = self._fp_env_var.get()
        spaces   = self._spaces_var.get()
        mem      = self._mem_var.get() or "50000"

        opt_flags = []
        if self._ignore_st.get(): opt_flags.append("--ignore slicetiming")
        if self._skip_bids.get(): opt_flags.append("--skip-bids-validation")
        if self._cifti.get():     opt_flags.append("--cifti-output")
        opt_str = (" \\\n    " + " \\\n    ".join(opt_flags)) if opt_flags else ""

        parts = []
        for subj in subjects:
            parts.append(
                f"echo '=== {subj} ==='\n"
                f"singularity run --cleanenv \\\n"
                f"    -B /autofs -B /usr/pubsw \\\n"
                f"    -B /cluster -B /homes -B /space -B /vast -B /run/user \\\n"
                f"    '{simg}' \\\n"
                f"    '{bids_dir}' '{fp_der}' participant \\\n"
                f"    --participant-label {subj} \\\n"
                f"    --output-spaces {spaces} \\\n"
                f"    --fs-subjects-dir '{fs_dir}' \\\n"
                f"    --work-dir '{work_dir}' \\\n"
                f"    --fs-license-file '{fs_lic}' \\\n"
                f"    --mem_mb {mem}{opt_str} \\\n"
                f"    && echo '✓ Done: {subj}' || echo '✗ Failed: {subj}'"
            )

        prefix = f"source '{fp_env}'\n" if fp_env and os.path.isfile(fp_env) else ""
        cmd = ["bash", "-c", prefix + "\n\n".join(parts)]

        self._last_subjects = subjects
        if self._state:
            self._state.update_many(subjects, "step_02", "running")

        self._console.separator()
        self._console.append(
            f"[Step 02]  fMRIPrep — {len(subjects)} subject(s): {', '.join(subjects)}", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set("Step 02 (fMRIPrep) running…")

        self._runner.run(
            cmd=cmd, cwd=bids_dir or "/tmp",
            on_line=self._console.append,
            on_done=self._done,
        )

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        if rc == 0:
            self._status.set("Step 02 (fMRIPrep) complete ✓")
            self._console.append("[Step 02] fMRIPrep finished.", "ok")
            if self._state:
                self._state.update_many(self._last_subjects, "step_02", "done")
            self._run_qc()
        else:
            self._status.set(f"Step 02 (fMRIPrep) failed (exit {rc})")
            self._console.append(f"[Step 02] fMRIPrep failed (exit {rc}).", "error")
            if self._state:
                self._state.update_many(self._last_subjects, "step_02", "failed")

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


# ── Step 13 QC tab (pre vs post RETROICOR GIF) ────────────────────────────────

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


# ── Step 02 Panel ──────────────────────────────────────────────────────────────

class Step02Panel(ttk.Frame):
    """Inner notebook: Generate BIDS List | fMRIPrep | Pre/Post QC (optional)."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner,
                 state: "PipelineState | None" = None, **kwargs):
        super().__init__(parent, padding=(6, 6), **kwargs)

        fp_subj_var = tk.StringVar(value=str(SCRIPTS_ROOT / "utility" / "SubjectListBIDS.txt"))
        fp_env_var  = tk.StringVar(value=str(SCRIPTS_ROOT / "utility" / "fmriprep_env.sh"))

        ttk.Label(self, text="Step 02 — fMRIPrep",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 6))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        bids_list_tab = _BIDSListTab(nb, cfg, console, status_var, fp_subj_var)
        fp_tab        = _FmriprepTab(nb, console, status_var, runner, fp_env_var, fp_subj_var, state=state)
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
    """Browse, view, edit, and save heuristic.py files."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg          = cfg
        self._console      = console
        self._status       = status_var
        self._current_path = None
        self._suppress_trace = False

        ttk.Label(self, text="Heuristic Editor",
                  font=("Helvetica", 13, "bold")).grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Edit the heuristic.py that tells heudiconv how to map DICOM sequences to BIDS files.\n"
                  "Open an existing file, create a new one from template, then click "
                  "\"Use in Step 01\" to set it as active."),
            wraplength=620, foreground="gray",
        ).grid(row=1, column=0, sticky="w", pady=(0, 8))

        # ── File toolbar ───────────────────────────────────────────────────
        toolbar = ttk.Frame(self)
        toolbar.grid(row=2, column=0, sticky="ew", pady=(0, 6))

        ttk.Label(toolbar, text="File:").pack(side="left")
        self._file_var = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self._file_var, width=38).pack(
            side="left", padx=(4, 6), fill="x", expand=True)
        ttk.Button(toolbar, text="Open…",            command=self._open).pack(side="left", padx=2)
        ttk.Button(toolbar, text="New from template", command=self._new_template).pack(side="left", padx=2)
        ttk.Separator(toolbar, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(toolbar, text="Use in Step 01",   command=self._use).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Save",             command=self._save).pack(side="left", padx=2)
        ttk.Button(toolbar, text="Save As…",         command=self._save_as).pack(side="left", padx=2)

        # ── Active heuristic indicator ─────────────────────────────────────
        active_row = ttk.Frame(self)
        active_row.grid(row=3, column=0, sticky="ew", pady=(0, 4))
        ttk.Label(active_row, text="Active (in Step 01):", foreground="gray").pack(side="left")
        self._active_lbl = ttk.Label(active_row, foreground="#4ec9b0")
        self._active_lbl.pack(side="left", padx=6)
        cfg["heuristic"].trace_add("write", lambda *_: self._update_active_label())
        self._update_active_label()

        # ── Text editor ────────────────────────────────────────────────────
        editor_frame = ttk.Frame(self)
        editor_frame.grid(row=4, column=0, sticky="nsew")

        vsb = ttk.Scrollbar(editor_frame, orient="vertical")
        hsb = ttk.Scrollbar(editor_frame, orient="horizontal")
        self._editor = tk.Text(
            editor_frame,
            bg="#1e1e1e", fg="#d4d4d4",
            font=("Menlo", 11), wrap="none",
            insertbackground="white",
            yscrollcommand=vsb.set, xscrollcommand=hsb.set,
            undo=True,
            tabs=("28", "56", "84", "112", "140"),
        )
        vsb.config(command=self._editor.yview)
        hsb.config(command=self._editor.xview)
        vsb.pack(side="right", fill="y")
        hsb.pack(side="bottom", fill="x")
        self._editor.pack(side="left", fill="both", expand=True)

        # Bind Ctrl+S / Cmd+S to save
        self._editor.bind("<Command-s>", lambda _: self._save())
        self._editor.bind("<Control-s>", lambda _: self._save())

        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=1)

        # Auto-load the heuristic set in cfg
        self._load_from_cfg()
        cfg["heuristic"].trace_add("write", self._on_cfg_heuristic)

    def _update_active_label(self):
        p = self._cfg["heuristic"].get().strip()
        self._active_lbl.config(text=Path(p).name if p else "(none)")

    def _on_cfg_heuristic(self, *_):
        if self._suppress_trace:
            return
        p = self._cfg["heuristic"].get().strip()
        if p and os.path.isfile(p) and p != self._current_path:
            self._load_file(p)

    def _load_from_cfg(self):
        p = self._cfg["heuristic"].get().strip()
        if p and os.path.isfile(p):
            self._load_file(p)

    def _load_file(self, path):
        with open(path) as f:
            content = f.read()
        self._editor.delete("1.0", "end")
        self._editor.insert("1.0", content)
        self._current_path = path
        self._file_var.set(path)
        self._console.append(f"[Heuristic] Loaded: {path}", "info")

    def _open(self):
        p = filedialog.askopenfilename(
            title="Open heuristic file",
            initialdir=str(SCRIPTS_ROOT / "utility"),
            filetypes=[("Python", "*.py"), ("All", "*.*")],
        )
        if p:
            self._load_file(p)

    def _new_template(self):
        p = filedialog.asksaveasfilename(
            title="Save new heuristic",
            initialdir=str(SCRIPTS_ROOT / "utility"),
            defaultextension=".py",
            filetypes=[("Python", "*.py")],
            initialfile="heuristic_new.py",
        )
        if not p:
            return
        with open(p, "w") as f:
            f.write(_HEURISTIC_TEMPLATE)
        self._load_file(p)
        self._console.append(f"[Heuristic] Created from template: {p}", "ok")

    def _use(self):
        p = self._current_path or self._file_var.get().strip()
        if not p:
            messagebox.showwarning("No file", "Open a heuristic file first.")
            return
        self._suppress_trace = True
        self._cfg["heuristic"].set(p)
        self._suppress_trace = False
        self._status.set(f"Heuristic set → {Path(p).name}")
        self._console.append(f"[Heuristic] Set as active: {p}", "ok")

    def _save(self):
        p = self._current_path or self._file_var.get().strip()
        if not p:
            self._save_as()
            return
        content = self._editor.get("1.0", "end-1c")
        with open(p, "w") as f:
            f.write(content)
        self._console.append(f"[Heuristic] Saved: {p}", "ok")
        self._status.set(f"Saved {Path(p).name}")

    def _save_as(self):
        p = filedialog.asksaveasfilename(
            title="Save heuristic as",
            initialdir=str(SCRIPTS_ROOT / "utility"),
            defaultextension=".py",
            filetypes=[("Python", "*.py")],
        )
        if not p:
            return
        self._current_path = p
        self._file_var.set(p)
        self._save()


# ── Physio (step03_physioparse_v2) ────────────────────────────────────────────

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
        self._work_lbl = ttk.Label(out_row, foreground="#9cdcfe")
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


class PhysioPanel(ttk.Frame):
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

        ttk.Label(self, text="Step 03 — Physioparse",
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


# ── Step 04 — Filter physio + R-DECO launcher ─────────────────────────────────

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
        self._matlab_var  = tk.StringVar(value="matlab")
        self._mcode_var   = tk.StringVar(value=str(SCRIPTS_ROOT / "utility" / "matlab_code"))

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
        self._console.append(f"[Step 04] Filter batch — subject: {subj}", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set("Step 04 filter running…")

        self._runner.run(cmd=cmd, cwd=out if os.path.isdir(out) else "/tmp",
                        on_line=self._console.append, on_done=self._done)

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        if rc == 0:
            self._status.set("Step 04 filter complete ✓")
            self._console.append("[Step 04] Filter batch finished. Open R-DECO tab next.", "ok")
        else:
            self._status.set(f"Step 04 filter failed (exit {rc})")
            self._console.append(f"[Step 04] Filter failed (exit {rc}).", "error")


class _RDecoTab(ttk.Frame):
    """List preprocessed mats and launch R-DECO in MATLAB."""

    def __init__(self, parent, console: Console, status_var: tk.StringVar,
                 preproc_dir_var: tk.StringVar, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._console     = console
        self._status      = status_var
        self._preproc_dir = preproc_dir_var

        ttk.Label(self, text="R-DECO — Cardiac R-peak Annotation",
                  font=("Helvetica", 12, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Select a *_rpiezo.mat file and launch R-DECO to detect R-peaks.\n"
                  "In R-DECO: load the file → run detection → correct manually → save as *_rdeco.mat\n"
                  "in the same folder.  The table below tracks which files are done."),
            foreground="gray", wraplength=580,
        ).pack(anchor="w", pady=(0, 10))

        # Paths
        pf = ttk.LabelFrame(self, text="Paths", padding=(10, 6))
        pf.pack(fill="x", pady=(0, 8))
        PathRow(pf, "Preprocessed dir:", mode="dir",
                var=self._preproc_dir, label_width=18,
                on_change=lambda _: self._refresh()).pack(fill="x", pady=2)
        self._rdeco_var   = tk.StringVar(value=str(SCRIPTS_ROOT / "utility" / "r-deco-master"))
        self._matlab_var2 = tk.StringVar(value="matlab")
        PathRow(pf, "R-DECO code dir:", mode="dir",
                var=self._rdeco_var, label_width=18).pack(fill="x", pady=2)
        PathRow(pf, "MATLAB exe:", mode="file",
                filetypes=[("MATLAB", "matlab*"), ("All", "*.*")],
                var=self._matlab_var2, label_width=18).pack(fill="x", pady=2)

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
        ttk.Button(btn_row, text="↻ Refresh",     command=self._refresh).pack(side="left", padx=(0, 6))
        ttk.Button(btn_row, text="Launch R-DECO", command=self._launch).pack(side="left")
        ttk.Label(btn_row, text="(select a row first)",
                  foreground="gray").pack(side="left", padx=8)

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


class Step04Panel(ttk.Frame):
    """Step 04: Filter physio per-sequence mats + R-DECO launcher."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=(6, 6), **kwargs)

        # Shared var: preprocessed dir is set by _FilterPhysioTab and read by _RDecoTab
        preproc_dir_var = tk.StringVar()

        ttk.Label(self, text="Step 04 — Preprocess for RETROICOR",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 6))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        filt_tab  = _FilterPhysioTab(nb, cfg, console, status_var, runner, preproc_dir_var)
        rdeco_tab = _RDecoTab(nb, console, status_var, preproc_dir_var)

        nb.add(filt_tab,  text="  Filter Physio  ")
        nb.add(rdeco_tab, text="  R-DECO  ")

        # Sync R-DECO tab when filter tab completes
        nb.bind("<<NotebookTabChanged>>", lambda _: rdeco_tab._refresh())


# ── Step 05 — RETROICOR ───────────────────────────────────────────────────────

class Step05Panel(ttk.Frame):
    """Step 05: Generate 1D files + run RETROICOR (full pipeline)."""

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
        self._matlab_var  = tk.StringVar(value=self._DEFAULTS["matlab_exe"])
        self._mcode_var   = tk.StringVar(value=str(SCRIPTS_ROOT / "utility" / "matlab_code"))
        self._retro_var   = tk.StringVar(value=str(SCRIPTS_ROOT / "utility" / "retroicor"))
        self._session_var = tk.StringVar(value=self._DEFAULTS["session"])
        self._sms_var     = tk.StringVar(value=self._DEFAULTS["sms"])
        self._fs_var      = tk.StringVar(value=self._DEFAULTS["fs_out"])
        self._tr_var      = tk.StringVar(value=self._DEFAULTS["tr_fallback"])

        ttk.Label(self, text="Step 05 — RETROICOR",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 6))

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        nb.add(self._build_cfg_tab(nb),    text="  Configuration  ")
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

        prow(tab if False else pf, "Preprocessed dir (step04):", self._preproc_var)
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
        self._summary_lbl = ttk.Label(tab, foreground="#9cdcfe", wraplength=560)
        self._summary_lbl.pack(anchor="w", pady=(0, 10))
        self._update_summary()
        self._subj_var.trace_add("write", lambda *_: self._update_summary())

        ttk.Separator(tab).pack(fill="x", pady=8)

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
            raise ValueError(f"Preprocessed directory not found:\n{preproc}\nRun step04 first.")
        return subj, preproc

    def _all_buttons(self):
        return [self._p1_btn, self._p2_btn, self._p3_btn, self._all_btn]

    def _lock(self):
        for b in self._all_buttons():
            b.config(state="disabled")
        self._progress.start(10)

    def _unlock(self):
        for b in self._all_buttons():
            b.config(state="normal")
        self._progress.stop()

    # ── Part 1: Generate 1D ───────────────────────────────────────────────────

    def _run_part1(self):
        try:
            subj, preproc = self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return

        inp   = self._input_var.get().strip()
        Path(inp).mkdir(parents=True, exist_ok=True)
        sd    = self._cfg["sourcedata"].get().strip()
        mcode = self._mcode_var.get().strip()

        matlab_cmd = (
            f"set(0,'DefaultFigureVisible','off');"
            f"addpath('{mcode}');"
            f"preproc_generate_1D_v2("
            f"'{preproc}','{inp}','{subj}','{sd}',"
            f"'SMS',{self._sms_var.get()},"
            f"'FS_OUT',{self._fs_var.get()},"
            f"'TR_FALLBACK',{self._tr_var.get()},"
            f"'SESSION','{self._session_var.get()}');"
        )
        self._run_matlab(matlab_cmd, inp, "Part 1 — Generate 1D", "step_05_p1")

    # ── Part 2: Copy BOLD ─────────────────────────────────────────────────────

    def _run_part2(self):
        try:
            subj, _ = self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return

        sd      = self._cfg["sourcedata"].get().strip()
        ses     = self._session_var.get().strip()
        inp     = self._input_var.get().strip()
        func_dir = str(Path(sd) / subj / f"ses-{ses}" / "func")

        if not os.path.isdir(func_dir):
            messagebox.showerror("Error", f"BIDS func dir not found:\n{func_dir}"); return

        Path(inp).mkdir(parents=True, exist_ok=True)

        script = (
            f"set -euo pipefail\n"
            f"find '{func_dir}' -maxdepth 1 -name '*_bold.nii.gz' -exec cp -n {{}} '{inp}/' \\;\n"
            f"find '{func_dir}' -maxdepth 1 -name '*_bold.json'   -exec cp -n {{}} '{inp}/' \\;\n"
            f"echo 'Copied BOLDs and JSONs.'"
        )
        self._run_cmd(["bash", "-c", script], inp, "Part 2 — Copy BOLD", "step_05_p2")

    # ── Part 3: RETROICOR ─────────────────────────────────────────────────────

    def _run_part3(self):
        try:
            subj, _ = self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return

        inp   = self._input_var.get().strip()
        out   = self._output_var.get().strip()
        mcode = self._mcode_var.get().strip()
        rcode = self._retro_var.get().strip()

        Path(out).mkdir(parents=True, exist_ok=True)

        matlab_cmd = (
            f"set(0,'DefaultFigureVisible','off');"
            f"addpath('{mcode}');addpath('{rcode}');"
            f"retroicor_batch('{inp}','{out}','{rcode}');"
        )
        self._run_matlab(matlab_cmd, inp, "Part 3 — RETROICOR", "step_05_p3")

    # ── Run All ───────────────────────────────────────────────────────────────

    def _run_all(self):
        try:
            subj, preproc = self._validate()
        except ValueError as e:
            messagebox.showerror("Error", str(e)); return

        sd      = self._cfg["sourcedata"].get().strip()
        ses     = self._session_var.get().strip()
        inp     = self._input_var.get().strip()
        out     = self._output_var.get().strip()
        mcode   = self._mcode_var.get().strip()
        rcode   = self._retro_var.get().strip()
        func_dir = str(Path(sd) / subj / f"ses-{ses}" / "func")

        Path(inp).mkdir(parents=True, exist_ok=True)
        Path(out).mkdir(parents=True, exist_ok=True)

        # Part1 MATLAB + Part2 bash + Part3 MATLAB — chained in one bash -c
        script = (
            f"set -euo pipefail\n"
            # Part 1
            f"echo '[Step 05] Part 1 — Generate 1D ...'\n"
            f"matlab -nodisplay -nosplash -batch \""
            f"set(0,'DefaultFigureVisible','off');"
            f"addpath('{mcode}');"
            f"preproc_generate_1D_v2('{preproc}','{inp}','{subj}','{sd}',"
            f"'SMS',{self._sms_var.get()},'FS_OUT',{self._fs_var.get()},"
            f"'TR_FALLBACK',{self._tr_var.get()},'SESSION','{ses}');\"\n"
            # Part 2
            f"echo '[Step 05] Part 2 — Copy BOLD ...'\n"
            f"find '{func_dir}' -maxdepth 1 -name '*_bold.nii.gz' -exec cp -n {{}} '{inp}/' \\;\n"
            f"find '{func_dir}' -maxdepth 1 -name '*_bold.json'   -exec cp -n {{}} '{inp}/' \\;\n"
            # Part 3
            f"echo '[Step 05] Part 3 — RETROICOR ...'\n"
            f"matlab -nodisplay -nosplash -batch \""
            f"set(0,'DefaultFigureVisible','off');"
            f"addpath('{mcode}');addpath('{rcode}');"
            f"retroicor_batch('{inp}','{out}','{rcode}');\"\n"
            f"echo '[Step 05] Done.'"
        )
        self._run_cmd(["bash", "-c", script], inp, "Step 05 — All parts", "step_05")

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

class Step06Panel(ttk.Frame):
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
        self._fmriprep_var  = tk.StringVar()
        self._firstlvl_var  = tk.StringVar()
        self._session_var   = tk.StringVar(value="01")
        self._threshold_var = tk.StringVar(value="1.5")
        self._debounce_var  = tk.StringVar(value="1.5")
        self._python_var    = tk.StringVar(value=sys.executable)
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

        pr("Parsed mats (physioparse step 03):", self._parsed_var)
        pr("Stim output dir:", self._stim_var)
        pr("fMRIPrep derivatives dir:", self._fmriprep_var)
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

        # Options
        opts = ttk.LabelFrame(tab, text="Options", padding=(10, 6))
        opts.pack(fill="x", pady=(0, 8))
        ttk.Checkbutton(opts, text="Generate QC plots per run (STIMTRIG + detected onsets)",
                        variable=self._do_qc).pack(anchor="w")
        ttk.Checkbutton(opts, text="Prepare first-level folder (copy stim + motion + BOLD)",
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

        self._summary_lbl = ttk.Label(tab, foreground="#9cdcfe", wraplength=560)
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
        self._firstlvl_var.set(str(base / "first_level"))

    def _update_summary(self):
        subj = self._subj_var.get().strip()
        self._summary_lbl.config(text=f"Subject: {subj or '(not set)'}")

    def _validate(self):
        subj   = self._subj_var.get().strip()
        parsed = self._parsed_var.get().strip()
        if not subj:
            raise ValueError("Select a BIDS subject.")
        if not parsed or not os.path.isdir(parsed):
            raise ValueError(f"Parsed directory not found:\n{parsed}\nRun step03 first.")
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
        func_dir = str(Path(fmrip) / subj / f"ses-{ses}" / "func")

        script = (
            f"set -euo pipefail\n"
            f"mkdir -p '{fl_dir}/stim_onsets' '{fl_dir}/motion_regressors' '{fl_dir}/bolds'\n"
            # Stim files
            f"for f in '{stim}'/*_bold_stim.txt; do [ -f \"$f\" ] && cp -f \"$f\" '{fl_dir}/stim_onsets/'; done\n"
            # Motion regressors
            f"for f in '{func_dir}'/*_nuisance_regressors_for_GLM_no_header.txt; do "
            f"[ -f \"$f\" ] && cp -f \"$f\" '{fl_dir}/motion_regressors/'; done\n"
            # T1w BOLD
            f"for f in '{func_dir}'/*_space-T1w_desc-preproc_bold.nii.gz; do "
            f"[ -f \"$f\" ] && cp -f \"$f\" '{fl_dir}/bolds/'; done\n"
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
        ses     = self._session_var.get().strip()
        thr     = self._threshold_var.get().strip()
        deb     = self._debounce_var.get().strip()
        python  = self._python_var.get().strip()
        qc_flag = "1" if self._do_qc.get() else "0"
        prep    = "0" if self._do_prep.get() else "1"

        sd = self._cfg["sourcedata"].get().strip()
        cmd = [
            "bash", script_path,
            subj, sd, parsed, stim, fmrip, fl_dir,
            ses, thr, deb, python, qc_flag, prep,
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

class Step07Panel(ttk.Frame):
    """Step 07: First-level SPM GLM (masks located in fmriprep, not copied) + MNI warp."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner

        # ── Vars ──────────────────────────────────────────────────────────────
        self._sublist_var = tk.StringVar(value=str(SCRIPTS_ROOT / "utility" / "SubjectListBIDS.txt"))
        self._firstlvl_var = tk.StringVar()
        self._output_var   = tk.StringVar()
        self._spm_var      = tk.StringVar(
            value="/autofs/cluster/vagabond/USERS/MARIO/Packages/matlab/spm12")
        self._matlab_var   = tk.StringVar(value="matlab")
        self._mcode_var    = tk.StringVar(value=str(SCRIPTS_ROOT / "utility" / "matlab_code"))
        self._session_var  = tk.StringVar(value="01")
        self._run_var      = tk.StringVar(value="01")
        self._tr_var       = tk.StringVar(value="1.19")
        self._smooth_var   = tk.StringVar(value="3")
        self._do_mni       = tk.BooleanVar(value=True)

        ttk.Label(self, text="Step 07 — First-level GLM + MNI",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("First-level SPM GLM in native T1w space, then warp contrasts to MNI.\n"
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

        # ── fMRIPrep summary (located, not configured — derived from Setup) ────
        info = ttk.LabelFrame(self, text="Mask + BOLD source (located in place)", padding=(8, 4))
        info.pack(fill="x", pady=(0, 8))
        self._fmriprep_lbl = ttk.Label(info, foreground="#9cdcfe", wraplength=560)
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

        ttk.Checkbutton(pm, text="Warp contrasts to MNI (segment T1 → wcon_*.nii)",
                        variable=self._do_mni).pack(anchor="w", pady=(4, 0))

        ttk.Separator(self).pack(fill="x", pady=8)

        btn_row = ttk.Frame(self)
        btn_row.pack(anchor="w")
        self._run_btn = ttk.Button(btn_row, text="▶  Run First-level + MNI", command=self._run)
        self._run_btn.pack(side="left")
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=220)
        self._progress.pack(side="left", padx=12)

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
        ses    = self._session_var.get().strip() or "01"
        run    = self._run_var.get().strip() or "01"
        tr     = self._tr_var.get().strip() or "1.19"
        smooth = self._smooth_var.get().strip() or "3"
        do_mni = "1" if self._do_mni.get() else "0"

        cmd = [
            "bash", script,
            sublist, sd, flvl, out, spm, matlab, mcode,
            ses, run, tr, smooth, do_mni,
        ]

        self._console.separator()
        self._console.append("[Step 07] First-level GLM + MNI starting…", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set("Step 07 (first-level) running…")

        self._runner.run(cmd=cmd, cwd=str(SCRIPTS_ROOT),
                        on_line=self._console.append, on_done=self._done)

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        if rc == 0:
            self._status.set("Step 07 complete ✓")
            self._console.append("[Step 07] First-level + MNI finished.", "ok")
        else:
            self._status.set(f"Step 07 failed (exit {rc})")
            self._console.append(f"[Step 07] Failed (exit {rc}).", "error")


# ── Step 08 — Second-level (group) analysis ───────────────────────────────────

class Step08Panel(ttk.Frame):
    """Step 08: Group one-sample t-tests per task + combined Block/Continuous."""

    def __init__(self, parent, cfg: dict, console: Console,
                 status_var: tk.StringVar, runner: ScriptRunner, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._cfg     = cfg
        self._console = console
        self._status  = status_var
        self._runner  = runner

        # ── Vars ──────────────────────────────────────────────────────────────
        self._block_var   = tk.StringVar()
        self._cont_var    = tk.StringVar()
        self._rest_var     = tk.StringVar()
        self._output_var  = tk.StringVar()
        self._spm_var     = tk.StringVar(
            value="/autofs/cluster/vagabond/USERS/MARIO/Packages/matlab/spm12")
        self._matlab_var  = tk.StringVar(value="matlab")
        self._mcode_var   = tk.StringVar(value=str(SCRIPTS_ROOT / "utility" / "matlab_code"))
        self._con_var     = tk.StringVar(value="wcon_0001.nii")
        self._do_combined = tk.BooleanVar(value=True)

        ttk.Label(self, text="Step 08 — Second-level (Group)",
                  font=("Helvetica", 13, "bold")).pack(anchor="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Manually select each task folder (BlockStim, ContinuousStim, rest).\n"
                  "Each is searched recursively for the contrast image — one per subject —\n"
                  "and a one-sample t-test is run. A combined Block+Continuous test is also run.\n"
                  "Group stats require MNI space: use wcon_0001.nii (from step07's MNI warp)."),
            foreground="gray", wraplength=600,
        ).pack(anchor="w", pady=(0, 10))

        # ── Task folders ───────────────────────────────────────────────────────
        tf = ttk.LabelFrame(self, text="Task folders (select manually)", padding=(10, 6))
        tf.pack(fill="x", pady=(0, 8))
        PathRow(tf, "BlockStim folder:", mode="dir",
                var=self._block_var, label_width=20).pack(fill="x", pady=2)
        PathRow(tf, "ContinuousStim folder:", mode="dir",
                var=self._cont_var, label_width=20).pack(fill="x", pady=2)
        PathRow(tf, "rest folder:", mode="dir",
                var=self._rest_var, label_width=20).pack(fill="x", pady=2)
        ttk.Label(tf, text="Leave a folder blank to skip that task.",
                  foreground="gray").pack(anchor="w", pady=(2, 0))

        # Quick-fill from a step07 output root
        qf = ttk.Frame(tf)
        qf.pack(fill="x", pady=(6, 0))
        ttk.Button(qf, text="Auto-fill from step07 output root…",
                   command=self._autofill).pack(side="left")
        ttk.Label(qf, text="(points all three at the same root — recursive search separates tasks)",
                  foreground="gray").pack(side="left", padx=6)

        # ── Output + tools ─────────────────────────────────────────────────────
        of = ttk.LabelFrame(self, text="Output & tools", padding=(10, 6))
        of.pack(fill="x", pady=(0, 8))
        PathRow(of, "Output dir:", mode="dir",
                var=self._output_var, label_width=20).pack(fill="x", pady=2)
        PathRow(of, "SPM12 dir:", mode="dir",
                var=self._spm_var, label_width=20).pack(fill="x", pady=2)
        PathRow(of, "MATLAB exe:", mode="file",
                var=self._matlab_var, label_width=20).pack(fill="x", pady=2)
        PathRow(of, "MATLAB code dir:", mode="dir",
                var=self._mcode_var, label_width=20).pack(fill="x", pady=2)

        # ── Options ────────────────────────────────────────────────────────────
        op = ttk.LabelFrame(self, text="Options", padding=(10, 6))
        op.pack(fill="x", pady=(0, 8))
        cr = ttk.Frame(op)
        cr.pack(fill="x", pady=2)
        ttk.Label(cr, text="Contrast image filename:", width=24, anchor="w").pack(side="left")
        ttk.Entry(cr, textvariable=self._con_var, width=20).pack(side="left")
        ttk.Checkbutton(op, text="Run combined BlockStim + ContinuousStim test",
                        variable=self._do_combined).pack(anchor="w", pady=(4, 0))

        # Default output dir derived from sourcedata
        cfg["sourcedata"].trace_add("write", lambda *_: self._sync())
        self._sync()

        ttk.Separator(self).pack(fill="x", pady=8)

        btn_row = ttk.Frame(self)
        btn_row.pack(anchor="w")
        self._run_btn = ttk.Button(btn_row, text="▶  Run Second-level", command=self._run)
        self._run_btn.pack(side="left")
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=220)
        self._progress.pack(side="left", padx=12)

    def _sync(self):
        sd = self._cfg["sourcedata"].get().strip()
        if sd and not self._output_var.get():
            self._output_var.set(str(Path(sd) / "derivatives" / "spm" / "second_level"))

    def _autofill(self):
        d = filedialog.askdirectory(title="Select step07 first-level output root")
        if not d:
            return
        # All three task searches share the same root; recursive glob separates them
        # only if the per-task subfolders are named BlockStim/ContinuousStim/rest.
        # We point each var at root/<task> if those exist, else at root.
        for var, task in ((self._block_var, "BlockStim"),
                          (self._cont_var,  "ContinuousStim"),
                          (self._rest_var,  "rest")):
            # step07 layout is <root>/<subj>/<task>/wcon — task dirs are nested,
            # so the recursive search must start at root. Point each var at root;
            # the MATLAB side filters by the folder you give it.
            var.set(d)

    def _run(self):
        script = str(SCRIPTS_ROOT / "step08_secondlevel_v2.sh")
        if not os.path.isfile(script):
            messagebox.showerror("Error", f"step08_secondlevel_v2.sh not found:\n{script}")
            return

        block = self._block_var.get().strip()
        cont  = self._cont_var.get().strip()
        rest  = self._rest_var.get().strip()
        out   = self._output_var.get().strip()

        if not (block or cont or rest):
            messagebox.showerror("Error", "Select at least one task folder.")
            return
        if not out:
            messagebox.showerror("Error", "Set an output directory.")
            return

        spm    = self._spm_var.get().strip()
        matlab = self._matlab_var.get().strip()
        mcode  = self._mcode_var.get().strip()
        con    = self._con_var.get().strip() or "con_0001.nii"
        comb   = "1" if self._do_combined.get() else "0"

        cmd = [
            "bash", script,
            block, cont, rest, out,
            spm, matlab, mcode, con, comb,
        ]

        self._console.separator()
        self._console.append("[Step 08] Second-level group analysis starting…", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set("Step 08 (second-level) running…")

        self._runner.run(cmd=cmd, cwd=str(SCRIPTS_ROOT),
                        on_line=self._console.append, on_done=self._done)

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        if rc == 0:
            self._status.set("Step 08 complete ✓")
            self._console.append("[Step 08] Second-level finished.", "ok")
        else:
            self._status.set(f"Step 08 failed (exit {rc})")
            self._console.append(f"[Step 08] Failed (exit {rc}).", "error")


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


# ── Pipeline sidebar ───────────────────────────────────────────────────────────

class SidebarPanel(ttk.Frame):
    """Left sidebar: per-subject step completion Treeview backed by pipeline_state.json."""

    _ICON = {
        "done":    "✓",
        "failed":  "✗",
        "running": "⟳",
        "flagged": "⚠",
        "pending": "·",
        "":        "·",
    }
    _TAG = {
        "done":    "ok",
        "failed":  "error",
        "running": "info",
        "flagged": "warn",
        "pending": "dim",
        "":        "dim",
    }

    def __init__(self, parent, state: PipelineState, **kwargs):
        super().__init__(parent, **kwargs)
        self._state = state

        ttk.Label(self, text="Pipeline State",
                  font=("Helvetica", 11, "bold")).pack(anchor="w", padx=6, pady=(6, 2))

        cols = [key for key, _ in PipelineState.STEPS]
        self._tv = ttk.Treeview(self, columns=cols, show="tree headings",
                                height=24, selectmode="browse")
        self._tv.column("#0", width=130, minwidth=100, stretch=False)
        self._tv.heading("#0", text="Subject")
        for key, label in PipelineState.STEPS:
            self._tv.column(key, width=58, minwidth=44, anchor="center", stretch=False)
            self._tv.heading(key, text=label)
        self._tv.tag_configure("ok",    foreground="#4ec9b0")
        self._tv.tag_configure("error", foreground="#f44747")
        self._tv.tag_configure("warn",  foreground="#dcdcaa")
        self._tv.tag_configure("info",  foreground="#9cdcfe")
        self._tv.tag_configure("dim",   foreground="#6a6a6a")

        vsb = ttk.Scrollbar(self, orient="vertical", command=self._tv.yview)
        vsb.pack(side="right", fill="y")
        self._tv.pack(side="left", fill="both", expand=True, padx=(6, 0))

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", side="bottom", pady=(4, 6), padx=6)
        ttk.Button(btn_row, text="↻", width=3, command=self.refresh).pack(side="left", padx=(0, 2))
        ttk.Button(btn_row, text="Clear", command=self._clear).pack(side="left")

        # Tooltip: show note on hover
        self._tv.bind("<Motion>", self._on_hover)
        self._tooltip = tk.Toplevel(self)
        self._tooltip.withdraw()
        self._tooltip.overrideredirect(True)
        self._tip_lbl = ttk.Label(self._tooltip, background="#ffffcc", relief="solid",
                                  padding=(4, 2), wraplength=300)
        self._tip_lbl.pack()

        state.on_change(self.refresh)
        self.refresh()

    def refresh(self):
        self._tv.delete(*self._tv.get_children())
        step_keys = [k for k, _ in PipelineState.STEPS]
        for subj in self._state.subjects():
            values = []
            worst_tag = "dim"
            priority  = ["error", "warn", "ok", "info", "dim"]
            for key in step_keys:
                st   = self._state.get_status(subj, key)
                icon = self._ICON.get(st, "·")
                tag  = self._TAG.get(st, "dim")
                values.append(icon)
                if priority.index(tag) < priority.index(worst_tag):
                    worst_tag = tag
            self._tv.insert("", "end", iid=subj, text=subj, values=values, tags=(worst_tag,))

    def _clear(self):
        if messagebox.askyesno("Clear state?",
                               "Reset pipeline_state.json for all subjects?"):
            self._state._data.clear()
            self._state.save()
            self.refresh()

    def _on_hover(self, event):
        item = self._tv.identify_row(event.y)
        col  = self._tv.identify_column(event.x)
        if not item or col == "#0":
            self._tooltip.withdraw()
            return
        col_idx = int(col.replace("#", "")) - 1
        if col_idx < 0 or col_idx >= len(PipelineState.STEPS):
            self._tooltip.withdraw()
            return
        step_key = PipelineState.STEPS[col_idx][0]
        note = self._state.get_note(item, step_key)
        time_ = self._state._data.get(item, {}).get(step_key, {}).get("time", "")
        if not note and not time_:
            self._tooltip.withdraw()
            return
        tip_text = f"{item} / {step_key}"
        if time_:  tip_text += f"\n{time_}"
        if note:   tip_text += f"\n{note}"
        self._tip_lbl.config(text=tip_text)
        x = event.x_root + 12
        y = event.y_root + 12
        self._tooltip.geometry(f"+{x}+{y}")
        self._tooltip.deiconify()
        self._tooltip.lift()


# ── Main Application ───────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("TVNS BIDS Pipeline")
        self.geometry("1120x900")
        self.minsize(860, 680)
        self._build()

    def _build(self):
        # ── Header ────────────────────────────────────────────────────────────
        header = ttk.Frame(self, padding=(10, 8, 10, 0))
        header.pack(fill="x")
        ttk.Label(header, text="TVNS BIDS Pipeline",
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

        # ── Status bar (packed bottom first so it always shows) ───────────────
        ttk.Label(self, textvariable=status_var,
                  relief="sunken", anchor="w",
                  padding=(6, 2)).pack(fill="x", side="bottom")

        # ── Outer horizontal pane: [Sidebar] | [Nav + Console] ───────────────
        # Both sides are draggable — user controls sidebar width and
        # console height independently.
        h_pane = ttk.PanedWindow(self, orient="horizontal")
        h_pane.pack(fill="both", expand=True, padx=6, pady=(0, 4))

        # Left pane — Pipeline State sidebar (resizable width)
        sidebar_frame = ttk.Frame(h_pane)
        sidebar = SidebarPanel(sidebar_frame, pipeline)
        sidebar.pack(fill="both", expand=True)
        h_pane.add(sidebar_frame, weight=0)

        # Right pane — vertical split: step nav (top) | console (bottom)
        v_pane = ttk.PanedWindow(h_pane, orient="vertical")
        h_pane.add(v_pane, weight=1)

        # Console (resizable height, minimum visible at all times)
        con_frame = ttk.LabelFrame(v_pane, text="Console Output", padding=(6, 4))
        console = Console(con_frame)
        console.pack(fill="both", expand=True)
        btn_bar = ttk.Frame(con_frame)
        btn_bar.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_bar, text="Clear", command=console.clear).pack(side="right")

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
        t_step00 = panel(Step00Panel,    cfg, console, status_var, runner, state=pipeline)
        t_step01 = panel(Step01Panel,    cfg, console, status_var, runner, state=pipeline)
        t_step02 = panel(Step02Panel,    cfg, console, status_var, runner, state=pipeline)
        t_physio = panel(PhysioPanel,    cfg, console, status_var, runner)
        t_step04 = panel(Step04Panel,    cfg, console, status_var, runner)
        t_step05 = panel(Step05Panel,    cfg, console, status_var, runner)
        t_step06 = panel(Step06Panel,    cfg, console, status_var, runner)
        t_step07 = panel(Step07Panel,    cfg, console, status_var, runner)
        t_step08 = panel(Step08Panel,    cfg, console, status_var, runner)
        t_heur   = panel(HeuristicPanel, cfg, console, status_var)

        nav.add("⚙  Setup",               t_setup,  section="Config")
        nav.add("00  Download DICOMs",    t_step00, section="Pipeline")
        nav.add("01  BIDS Conversion",    t_step01)
        nav.add("02  fMRIPrep",           t_step02)
        nav.add("03  Physioparse",        t_physio)
        nav.add("04  Preprocess + RDECO", t_step04)
        nav.add("05  RETROICOR",          t_step05)
        nav.add("06  Stim Triggers",      t_step06)
        nav.add("07  First-level + MNI",  t_step07)
        nav.add("08  Second-level",       t_step08)
        nav.add("∷  Heuristic",           t_heur,   section="Tools")

        # Expose console/status so Save/Load config can report progress
        self._console    = console
        self._status_var = status_var

        console.append("TVNS BIDS Pipeline GUI ready.", "ok")
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
