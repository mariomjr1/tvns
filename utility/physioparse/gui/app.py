#!/usr/bin/env python3
"""
Pseudotime Pipeline GUI
Wraps:
  Step 1 — step01_times_acquisition.sh
  Step 2 — step02_plot_pseudotime_quality.py
  Step 3 — step03_parse.py

Run via:  bash run.sh   (activates Neuroimaging conda env automatically)
"""

import os
import re
import sys
import glob
import json
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

# The gui/ folder's parent holds the three pipeline scripts
SCRIPTS_ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).parent))
from runner import ScriptRunner


# ── Reusable widgets ───────────────────────────────────────────────────────────

class PathRow(ttk.Frame):
    """Single-row: Label + Entry + Browse button."""

    def __init__(self, parent, label, mode="file", filetypes=None,
                 on_change=None, **kwargs):
        super().__init__(parent, **kwargs)
        self._mode = mode
        self._filetypes = filetypes or [("All files", "*.*")]
        self._on_change = on_change

        ttk.Label(self, text=label, width=20, anchor="w").pack(side="left")
        self.var = tk.StringVar()
        self.var.trace_add("write", self._changed)
        self._entry = ttk.Entry(self, textvariable=self.var)
        self._entry.pack(side="left", fill="x", expand=True, padx=(0, 6))
        ttk.Button(self, text="Browse…", width=9,
                   command=self._browse).pack(side="left")

    def _browse(self):
        if self._mode == "dir":
            path = filedialog.askdirectory(title="Select folder")
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


# ── Step panels ────────────────────────────────────────────────────────────────

class Step1Panel(ttk.Frame):
    """Compute pseudotime — wraps step01_times_acquisition.sh or step01b_times_acquisition_block1.sh."""

    _SCRIPTS = {
        "classic": ("step01_times_acquisition.sh",
                    "Classic  —  data / datastart / dataend"),
        "block1":  ("step01b_times_acquisition_block1.sh",
                    "Block1   —  data_block1  (4 × N array)"),
    }

    def __init__(self, parent, console, status_var, runner, python_var, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._console    = console
        self._status     = status_var
        self._runner     = runner
        self._python_var = python_var

        ttk.Label(self, text="Step 1 — Compute Pseudotime",
                  font=("Helvetica", 13, "bold")).grid(
                      row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Detects the first MR trigger in the MRTRIG channel "
                  "and computes pseudotime for every JSON sequence found "
                  "in the data folder. Outputs pseudotime_mapping.json."),
            wraplength=580, foreground="gray",
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        # ── MAT format selector ────────────────────────────────────────────
        fmt_frame = ttk.LabelFrame(self, text="MAT file format", padding=(10, 4))
        fmt_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        self._fmt_var = tk.StringVar(value="classic")
        for key, (_, label) in self._SCRIPTS.items():
            ttk.Radiobutton(
                fmt_frame, text=label, variable=self._fmt_var, value=key,
                command=self._on_fmt_change,
            ).pack(side="left", padx=(0, 20))

        self._fmt_hint = ttk.Label(fmt_frame, foreground="gray",
                                   text="script: step01_times_acquisition.sh")
        self._fmt_hint.pack(side="left")

        # ── Path rows ──────────────────────────────────────────────────────
        self.data_dir = PathRow(self, "Data folder:",
                                mode="dir", on_change=self._autofill_mat)
        self.mat_file = PathRow(self, "MAT file:",
                                filetypes=[("MAT files", "*.mat"), ("All", "*.*")])

        self.data_dir.grid(row=3, column=0, sticky="ew", pady=3)
        self.mat_file.grid(row=4, column=0, sticky="ew", pady=3)

        ttk.Separator(self).grid(row=5, column=0, sticky="ew", pady=10)

        btn_row = ttk.Frame(self)
        btn_row.grid(row=6, column=0, sticky="w")
        self._run_btn = ttk.Button(btn_row, text="▶  Run Step 1",
                                   command=self._run)
        self._run_btn.pack(side="left")
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate",
                                          length=180)
        self._progress.pack(side="left", padx=12)

        self.columnconfigure(0, weight=1)

    def _on_fmt_change(self):
        script_name, _ = self._SCRIPTS[self._fmt_var.get()]
        self._fmt_hint.config(text=f"script: {script_name}")

    def populate(self, data_dir="", mat_file=""):
        if data_dir:  self.data_dir.set(data_dir)
        if mat_file:  self.mat_file.set(mat_file)

    def _autofill_mat(self, path):
        if not os.path.isdir(path) or self.mat_file.get():
            return
        mats = [f for f in glob.glob(os.path.join(path, "*.mat"))
                if "bold" not in os.path.basename(f).lower()]
        if mats:
            self.mat_file.set(mats[0])

    def _run(self):
        data_dir = self.data_dir.get()
        mat_path = self.mat_file.get()

        if not os.path.isdir(data_dir):
            messagebox.showerror("Error", "Select a valid data folder.")
            return
        if not os.path.isfile(mat_path):
            messagebox.showerror("Error", "Select a valid .mat file.")
            return

        script_name, _ = self._SCRIPTS[self._fmt_var.get()]
        script = SCRIPTS_ROOT / script_name
        if not script.exists():
            messagebox.showerror("Error", f"Script not found:\n{script}")
            return

        mat_name   = os.path.basename(mat_path)
        python_exe = self._python_var.get() or sys.executable
        cmd = ["bash", str(script), data_dir, mat_name, python_exe]

        self._console.separator()
        self._console.append(
            f"[Step 1]  bash {script_name}  {data_dir}  {mat_name}", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set("Step 1 running…")

        self._runner.run(
            cmd=cmd, cwd=data_dir,
            on_line=self._console.append,
            on_done=self._done,
        )

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        if rc == 0:
            self._status.set("Step 1 complete ✓")
            self._console.append("[Step 1] Finished successfully.", "ok")
        else:
            self._status.set(f"Step 1 failed (exit {rc})")
            self._console.append(f"[Step 1] Failed (exit {rc}).", "error")


class Step2Panel(ttk.Frame):
    """Plot pseudotime quality — wraps step02_plot_pseudotime_quality.py or block1 variant."""

    _SCRIPTS = {
        "classic": "step02_plot_pseudotime_quality.py",
        "block1":  "step02b_plot_pseudotime_quality_block1.py",
    }

    def __init__(self, parent, console, status_var, runner, python_var, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._console    = console
        self._status     = status_var
        self._runner     = runner
        self._python_var = python_var

        ttk.Label(self, text="Step 2 — Plot Quality",
                  font=("Helvetica", 13, "bold")).grid(
                      row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Generates a multi-panel visualisation of the four "
                  "physiological channels (RESP, RPIEZO, STIMTRIG, MRTRIG) "
                  "with coloured acquisition-period bars overlaid."),
            wraplength=580, foreground="gray",
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        # ── MAT format selector ────────────────────────────────────────────
        fmt_frame = ttk.LabelFrame(self, text="MAT file format", padding=(10, 4))
        fmt_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        self._fmt_var = tk.StringVar(value="classic")
        for key, label in (("classic", "Classic  —  data / datastart / dataend"),
                           ("block1",  "Block1   —  data_block1  (4 × N array)")):
            ttk.Radiobutton(
                fmt_frame, text=label, variable=self._fmt_var, value=key,
                command=self._on_fmt_change,
            ).pack(side="left", padx=(0, 20))

        self._fmt_hint = ttk.Label(fmt_frame, foreground="gray",
                                   text=f"script: {self._SCRIPTS['classic']}")
        self._fmt_hint.pack(side="left")

        # ── Path rows ──────────────────────────────────────────────────────
        self.mat_file   = PathRow(self, "MAT file:",
                                  filetypes=[("MAT files", "*.mat"), ("All", "*.*")])
        self.json_file  = PathRow(self, "Pseudotime JSON:",
                                  filetypes=[("JSON", "*.json"), ("All", "*.*")])
        self.output_img = PathRow(self, "Output image:", mode="save",
                                  filetypes=[("PNG", "*.png")])

        self.mat_file.grid(row=3, column=0, sticky="ew", pady=3)
        self.json_file.grid(row=4, column=0, sticky="ew", pady=3)
        self.output_img.grid(row=5, column=0, sticky="ew", pady=3)

        ttk.Separator(self).grid(row=6, column=0, sticky="ew", pady=10)

        btn_row = ttk.Frame(self)
        btn_row.grid(row=7, column=0, sticky="w")
        self._run_btn = ttk.Button(btn_row, text="▶  Run Step 2",
                                   command=self._run)
        self._run_btn.pack(side="left")
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate",
                                          length=180)
        self._progress.pack(side="left", padx=12)

        self.columnconfigure(0, weight=1)

    def _on_fmt_change(self):
        self._fmt_hint.config(text=f"script: {self._SCRIPTS[self._fmt_var.get()]}")

    def populate(self, mat_file="", json_file="", output_img=""):
        if mat_file:   self.mat_file.set(mat_file)
        if json_file:  self.json_file.set(json_file)
        if output_img: self.output_img.set(output_img)

    def _run(self):
        mat = self.mat_file.get()
        js  = self.json_file.get()
        out = self.output_img.get()

        if not os.path.isfile(mat):
            messagebox.showerror("Error", "Select a valid .mat file.")
            return
        if not os.path.isfile(js):
            messagebox.showerror("Error", "Select a valid pseudotime JSON file.")
            return
        if not out:
            messagebox.showerror("Error", "Specify an output image path.")
            return

        script_name = self._SCRIPTS[self._fmt_var.get()]
        script = SCRIPTS_ROOT / script_name
        if not script.exists():
            messagebox.showerror("Error", f"Script not found:\n{script}")
            return

        python_exe = self._python_var.get() or sys.executable
        cmd = [python_exe, str(script), mat, js, out]

        self._console.separator()
        self._console.append(f"[Step 2]  python {script_name}", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set("Step 2 running…")

        self._runner.run(
            cmd=cmd, cwd=str(SCRIPTS_ROOT),
            on_line=self._console.append,
            on_done=self._done,
        )

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        if rc == 0:
            self._status.set("Step 2 complete ✓")
            self._console.append("[Step 2] Finished successfully.", "ok")
        else:
            self._status.set(f"Step 2 failed (exit {rc})")
            self._console.append(f"[Step 2] Failed (exit {rc}).", "error")


class Step3Panel(ttk.Frame):
    """Parse segments — wraps step03_parse.py or step03b_parse_block1.py."""

    _SCRIPTS = {
        "classic": "step03_parse.py",
        "block1":  "step03b_parse_block1.py",
    }

    def __init__(self, parent, console, status_var, runner, python_var, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._console    = console
        self._status     = status_var
        self._runner     = runner
        self._python_var = python_var

        ttk.Label(self, text="Step 3 — Parse Segments",
                  font=("Helvetica", 13, "bold")).grid(
                      row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Slices the full .mat recording into per-sequence segments "
                  "using the pseudotime mapping. Saves each segment as a .mat "
                  "file and a 4-channel PNG plot."),
            wraplength=580, foreground="gray",
        ).grid(row=1, column=0, sticky="w", pady=(0, 10))

        # ── MAT format selector ────────────────────────────────────────────
        fmt_frame = ttk.LabelFrame(self, text="MAT file format", padding=(10, 4))
        fmt_frame.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        self._fmt_var = tk.StringVar(value="classic")
        for key, label in (("classic", "Classic  —  data / datastart / dataend"),
                           ("block1",  "Block1   —  data_block1  (4 × N array)")):
            ttk.Radiobutton(
                fmt_frame, text=label, variable=self._fmt_var, value=key,
                command=self._on_fmt_change,
            ).pack(side="left", padx=(0, 20))

        self._fmt_hint = ttk.Label(fmt_frame, foreground="gray",
                                   text=f"script: {self._SCRIPTS['classic']}")
        self._fmt_hint.pack(side="left")

        # ── Path rows ──────────────────────────────────────────────────────
        self.data_dir   = PathRow(self, "Data folder:", mode="dir")
        self.output_dir = PathRow(self, "Output folder:", mode="dir")

        self.data_dir.grid(row=3, column=0, sticky="ew", pady=3)
        self.output_dir.grid(row=4, column=0, sticky="ew", pady=3)

        ttk.Separator(self).grid(row=5, column=0, sticky="ew", pady=10)

        btn_row = ttk.Frame(self)
        btn_row.grid(row=6, column=0, sticky="w")
        self._run_btn = ttk.Button(btn_row, text="▶  Run Step 3",
                                   command=self._run)
        self._run_btn.pack(side="left")
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate",
                                          length=180)
        self._progress.pack(side="left", padx=12)

        self.columnconfigure(0, weight=1)

    def _on_fmt_change(self):
        self._fmt_hint.config(text=f"script: {self._SCRIPTS[self._fmt_var.get()]}")

    def populate(self, data_dir="", output_dir=""):
        if data_dir:   self.data_dir.set(data_dir)
        if output_dir: self.output_dir.set(output_dir)

    def _run(self):
        data_dir   = self.data_dir.get()
        output_dir = self.output_dir.get()

        if not os.path.isdir(data_dir):
            messagebox.showerror("Error", "Select a valid data folder.")
            return
        if not output_dir:
            messagebox.showerror("Error", "Specify an output folder.")
            return

        script_name = self._SCRIPTS[self._fmt_var.get()]
        script = SCRIPTS_ROOT / script_name
        if not script.exists():
            messagebox.showerror("Error", f"Script not found:\n{script}")
            return

        python_exe = self._python_var.get() or sys.executable
        cmd = [python_exe, str(script), data_dir, output_dir]

        self._console.separator()
        self._console.append(
            f"[Step 3]  python {script_name}  {data_dir}  {output_dir}", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set("Step 3 running…")

        self._runner.run(
            cmd=cmd, cwd=str(SCRIPTS_ROOT),
            on_line=self._console.append,
            on_done=self._done,
        )

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        if rc == 0:
            self._status.set("Step 3 complete ✓")
            self._console.append("[Step 3] Finished successfully.", "ok")
        else:
            self._status.set(f"Step 3 failed (exit {rc})")
            self._console.append(f"[Step 3] Failed (exit {rc}).", "error")


# ── Step 4 QC panel ───────────────────────────────────────────────────────────

class Step4Panel(ttk.Frame):
    """QC — wraps step04_qc.py."""

    _QC_FIELDS = [
        ("QC_RESP_SNR",         "RESP signal quality (SNR):"),
        ("QC_RESP_RATE_BPM",    "Breathing rate (bpm):"),
        ("QC_RESP_REG_CV",      "Breathing regularity (CV %):"),
        ("QC_CARDIAC_SNR",      "Cardiac signal quality (SNR):"),
        ("QC_CARDIAC_RATE_BPM", "Heart rate (bpm):"),
        ("QC_TRIGGER_CV_PCT",   "MR trigger regularity (CV %):"),
        ("QC_SEQ_COVERAGE",     "Sequence coverage (%):"),
    ]
    _STATUS_COLORS = {"ok": "#4ec97b", "warn": "#dcdcaa", "fail": "#f44747"}
    _STATUS_ICONS  = {"ok": "✓", "warn": "⚠", "fail": "✗"}

    def __init__(self, parent, console, status_var, runner, python_var, **kwargs):
        super().__init__(parent, padding=14, **kwargs)
        self._console       = console
        self._status        = status_var
        self._runner        = runner
        self._python_var    = python_var
        self._kv            = {}
        self._metric_labels = {}

        ttk.Label(self, text="Step 4 — Quality Control",
                  font=("Helvetica", 13, "bold")).grid(
                      row=0, column=0, columnspan=2, sticky="w", pady=(0, 4))
        ttk.Label(
            self,
            text=("Loads the full .mat recording and pseudotime_mapping.json, "
                  "detects respiratory cycles and cardiac peaks, checks MR trigger "
                  "regularity and sequence coverage, and scores each metric "
                  "as pass / warn / fail."),
            wraplength=580, foreground="gray",
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(0, 10))

        self.data_dir   = PathRow(self, "Data folder:",   mode="dir")
        self.output_dir = PathRow(self, "Output folder:", mode="dir")
        self.data_dir.grid(row=2, column=0, columnspan=2, sticky="ew", pady=3)
        self.output_dir.grid(row=3, column=0, columnspan=2, sticky="ew", pady=3)

        ttk.Separator(self).grid(row=4, column=0, columnspan=2, sticky="ew", pady=8)

        btn_row = ttk.Frame(self)
        btn_row.grid(row=5, column=0, columnspan=2, sticky="w")
        self._run_btn = ttk.Button(btn_row, text="▶  Run QC", command=self._run)
        self._run_btn.pack(side="left")
        self._progress = ttk.Progressbar(btn_row, mode="indeterminate", length=180)
        self._progress.pack(side="left", padx=12)

        ttk.Separator(self).grid(row=6, column=0, columnspan=2, sticky="ew", pady=8)

        # ── Two-column: metrics readout  |  guidance ──────────────────────
        left = ttk.LabelFrame(self, text="QC Metrics", padding=(10, 6))
        left.grid(row=7, column=0, sticky="nsew", padx=(0, 6))

        for i, (key, label) in enumerate(self._QC_FIELDS):
            ttk.Label(left, text=label, anchor="w", width=30).grid(
                row=i, column=0, sticky="w", pady=2)
            lbl = tk.Label(left, text="—", width=20, anchor="w",
                           bg="#2d2d2d", fg="#888888",
                           font=("Menlo", 11), relief="flat")
            lbl.grid(row=i, column=1, sticky="w", padx=(4, 0))
            self._metric_labels[key] = lbl

        right = ttk.LabelFrame(self, text="What to check", padding=(10, 6))
        right.grid(row=7, column=1, sticky="nsew")

        _guidance = (
            "RESP SNR > 5  — breathing belt well fitted\n"
            "  ⚠ 2–5: check belt; subject may be breathing shallowly\n"
            "  ✗ < 2: signal too noisy — re-check sensor placement\n\n"
            "Breathing rate 8–20 bpm  — normal adult range\n"
            "  ⚠ outside range: possible breath-hold or hyperventilation\n\n"
            "Breathing regularity CV < 20 %  — consistent cycles\n"
            "  ⚠ 20–40 %: irregular breathing; can corrupt csf_velocity\n"
            "    phase-binning (step 3–4 in that pipeline)\n\n"
            "Cardiac SNR > 5  — piezo detecting heartbeat clearly\n"
            "  ⚠ low: sensor poorly placed or signal absent\n\n"
            "Heart rate 50–100 bpm  — normal resting range\n\n"
            "MR trigger CV < 5 %  — triggers at exactly 1 TR apart\n"
            "  ⚠ high: missed/doubled triggers → wrong pseudotime\n"
            "    Re-run Step 1 and inspect pseudotime_mapping.json\n\n"
            "Sequence coverage > 80 %  — recording covers the session\n"
            "  ⚠ low: gaps between sequences or incomplete mapping"
        )
        txt = tk.Text(right, wrap="word", font=("Menlo", 10),
                      bg="#1e1e1e", fg="#d4d4d4",
                      relief="flat", state="normal", width=38)
        txt.insert("1.0", _guidance)
        txt.config(state="disabled")
        txt.pack(fill="both", expand=True)

        self.columnconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

    def populate(self, data_dir="", output_dir=""):
        if data_dir:   self.data_dir.set(data_dir)
        if output_dir: self.output_dir.set(output_dir)

    def _run(self):
        data_dir   = self.data_dir.get()
        output_dir = self.output_dir.get() or os.path.join(data_dir, "qc")
        self.output_dir.set(output_dir)

        if not os.path.isdir(data_dir):
            messagebox.showerror("Error", "Select a valid data folder.")
            return

        script = SCRIPTS_ROOT / "step04_qc.py"
        if not script.exists():
            messagebox.showerror("Error", f"Script not found:\n{script}")
            return

        python_exe = self._python_var.get() or sys.executable
        cmd = [python_exe, str(script), data_dir, output_dir]

        self._console.separator()
        self._console.append(f"[Step 4 QC]  {data_dir}  →  {output_dir}", "info")
        self._console.separator()

        self._run_btn.config(state="disabled")
        self._progress.start(10)
        self._status.set("QC running…")
        self._kv = {}

        self._runner.run(
            cmd=cmd, cwd=str(SCRIPTS_ROOT),
            on_line=self._on_line,
            on_done=self._done,
        )

    def _on_line(self, line):
        self._console.append(line)
        m = re.match(r"^([A-Z][A-Z0-9_]+)=(.+)$", line)
        if m:
            self._kv[m.group(1)] = m.group(2).strip()
            self._refresh_metrics()

    def _refresh_metrics(self):
        for key, lbl in self._metric_labels.items():
            val = self._kv.get(key)
            st  = self._kv.get(f"{key}_STATUS")
            if val is not None:
                icon  = self._STATUS_ICONS.get(st, "")
                color = self._STATUS_COLORS.get(st, "#d4d4d4")
                lbl.config(text=f"{icon}  {val}" if icon else val, fg=color)

    def _done(self, rc):
        self._progress.stop()
        self._run_btn.config(state="normal")
        if rc == 0:
            self._status.set("QC complete ✓")
            self._console.append("[Step 4 QC] Finished successfully.", "ok")
        else:
            self._status.set(f"QC failed (exit {rc})")
            self._console.append(f"[Step 4 QC] Failed (exit {rc}).", "error")


# ── Global config banner ───────────────────────────────────────────────────────

class ConfigBanner(ttk.LabelFrame):
    """
    Top banner: one data-folder entry that auto-populates all three step panels.
    """

    def __init__(self, parent, steps, **kwargs):
        kwargs.setdefault("padding", (10, 6))
        super().__init__(parent, text="Quick Setup — auto-fill all steps from a data folder",
                         **kwargs)
        self._steps = steps  # (step1, step2, step3, step4) set after construction

        self.data_dir = PathRow(self, "Data folder:", mode="dir",
                                on_change=self._propagate)
        self.data_dir.pack(fill="x")

        info = ttk.Label(self,
                         text=("Picks the first non-bold .mat, the "
                               "pseudotime_mapping.json, and suggests an "
                               "output folder next to the data folder."),
                         foreground="gray")
        info.pack(anchor="w", pady=(2, 0))

    def _propagate(self, path):
        if not os.path.isdir(path):
            return
        step1, step2, step3, step4 = self._steps

        # MAT file
        mats = sorted(
            f for f in glob.glob(os.path.join(path, "*.mat"))
            if "bold" not in os.path.basename(f).lower()
        )
        mat = mats[0] if mats else ""

        # pseudotime JSON
        js = os.path.join(path, "pseudotime_mapping.json")
        js = js if os.path.isfile(js) else ""

        # Default output: sibling folder named "parsed"
        out_dir = str(Path(path).parent / "parsed")

        # Default plot output
        out_img = str(Path(path).parent / "pseudotime_plot.png")

        step1.populate(data_dir=path, mat_file=mat)
        step2.populate(mat_file=mat, json_file=js, output_img=out_img)
        step3.populate(data_dir=path, output_dir=out_dir)
        step4.populate(data_dir=path, output_dir=os.path.join(path, "qc"))


# ── Main application window ────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Pseudotime Pipeline")
        self.geometry("860x800")
        self.minsize(720, 640)
        self._build()
        self._auto_populate()

    def _build(self):
        # ── Scripts-root row ───────────────────────────────────────────────
        root_frame = ttk.Frame(self, padding=(10, 8, 10, 0))
        root_frame.pack(fill="x")

        ttk.Label(root_frame, text="Pseudotime Pipeline",
                  font=("Helvetica", 16, "bold")).pack(side="left")

        right = ttk.Frame(root_frame)
        right.pack(side="right")
        ttk.Label(right, text="Scripts root:", foreground="gray").pack(side="left")
        self._scripts_root_var = tk.StringVar(value=str(SCRIPTS_ROOT))
        ttk.Entry(right, textvariable=self._scripts_root_var,
                  width=36, state="readonly").pack(side="left", padx=4)
        ttk.Button(right, text="Change…",
                   command=self._change_root).pack(side="left")

        # ── Conda env / Python row ─────────────────────────────────────────
        env_frame = ttk.Frame(self, padding=(10, 2, 10, 0))
        env_frame.pack(fill="x")

        ttk.Label(env_frame, text="Conda env:", foreground="gray").pack(side="left")
        self._conda_env_var = tk.StringVar()
        ttk.Entry(env_frame, textvariable=self._conda_env_var,
                  width=16).pack(side="left", padx=(4, 2))
        ttk.Button(env_frame, text="Apply",
                   command=self._apply_conda_env).pack(side="left", padx=(0, 14))

        ttk.Label(env_frame, text="Python executable:", foreground="gray").pack(side="left")
        self._python_var = tk.StringVar(value=sys.executable)
        ttk.Entry(env_frame, textvariable=self._python_var,
                  width=38).pack(side="left", padx=(4, 2))
        ttk.Button(env_frame, text="Browse…",
                   command=self._browse_python).pack(side="left")

        ttk.Separator(self, orient="horizontal").pack(fill="x", padx=10, pady=6)

        # ── Notebook (steps) ───────────────────────────────────────────────
        self._status_var = tk.StringVar(value="Ready")
        self._runner = ScriptRunner(self)

        nb = ttk.Notebook(self)
        nb.pack(fill="both", padx=10, pady=(0, 4))

        # Create panels with placeholder console (wired below)
        self._step1 = Step1Panel(nb, None, self._status_var, self._runner, self._python_var)
        self._step2 = Step2Panel(nb, None, self._status_var, self._runner, self._python_var)
        self._step3 = Step3Panel(nb, None, self._status_var, self._runner, self._python_var)
        self._step4 = Step4Panel(nb, None, self._status_var, self._runner, self._python_var)

        nb.add(self._step1, text="  1 · Compute Pseudotime  ")
        nb.add(self._step2, text="  2 · Plot Quality  ")
        nb.add(self._step3, text="  3 · Parse Segments  ")
        nb.add(self._step4, text="  4 · QC  ")

        # ── Quick setup banner (needs step refs) ───────────────────────────
        self._banner = ConfigBanner(
            self,
            steps=(self._step1, self._step2, self._step3, self._step4),
            padding=(10, 6),
        )
        # Insert banner between separator and notebook
        self._banner.pack(fill="x", padx=10, pady=(0, 6), before=nb)

        # ── Console ────────────────────────────────────────────────────────
        con_frame = ttk.LabelFrame(self, text="Console Output", padding=(6, 4))
        con_frame.pack(fill="both", expand=True, padx=10, pady=(0, 4))

        self._console = Console(con_frame)
        self._console.pack(fill="both", expand=True)

        btn_bar = ttk.Frame(con_frame)
        btn_bar.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_bar, text="Clear",
                   command=self._console.clear).pack(side="right")

        # Wire console into step panels
        for panel in (self._step1, self._step2, self._step3, self._step4):
            panel._console = self._console

        # ── Status bar ─────────────────────────────────────────────────────
        ttk.Label(self, textvariable=self._status_var,
                  relief="sunken", anchor="w",
                  padding=(6, 2)).pack(fill="x", side="bottom")

    def _apply_conda_env(self):
        env = self._conda_env_var.get().strip()
        if not env:
            messagebox.showwarning("Conda env", "Type a conda environment name first.")
            return
        for base in [Path.home() / "anaconda3", Path.home() / "miniconda3",
                     Path("/opt/anaconda3"), Path("/opt/miniconda3")]:
            candidate = base / "envs" / env / "bin" / "python"
            if candidate.exists():
                self._python_var.set(str(candidate))
                self._status_var.set(f"Python → {candidate}")
                return
        messagebox.showwarning(
            "Not found",
            f"Could not find conda environment '{env}'.\n"
            "Check the name or browse for the Python executable manually.")

    def _browse_python(self):
        path = filedialog.askopenfilename(
            title="Select Python executable",
            filetypes=[("Python", "python*"), ("All files", "*.*")])
        if path:
            self._python_var.set(path)

    def _change_root(self):
        global SCRIPTS_ROOT
        path = filedialog.askdirectory(title="Select scripts root folder")
        if path:
            SCRIPTS_ROOT = Path(path)
            self._scripts_root_var.set(str(SCRIPTS_ROOT))

    def _auto_populate(self):
        """Pre-fill fields when running from the expected project layout."""
        default_data = SCRIPTS_ROOT / "data"
        if default_data.is_dir():
            self._banner.data_dir.set(str(default_data))
            # Trigger propagation
            self._banner._propagate(str(default_data))


if __name__ == "__main__":
    app = App()
    app.mainloop()
