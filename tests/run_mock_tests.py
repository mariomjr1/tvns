#!/usr/bin/env python3
"""
run_mock_tests.py — data-free mock/logic test harness for the tVNS pipeline.

Runs WITHOUT real data and WITHOUT installing anything: it stubs the heavy tools
(matlab/findsession/rsync) for shell-plumbing tests, exercises the pure-Python
logic on synthetic fixtures, and statically checks the GUI. Tests whose deps are
absent (nibabel/matplotlib → roi_extract, extract_stim_onsets) are SKIPPED, not
failed. A PASS/FAIL/SKIP report image is written at the end (Pillow).

Usage:  python3 tests/run_mock_tests.py
Output: tests/test_report.png  (+ console summary)
"""
import csv
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "utility"))

RESULTS = []  # (group, name, status, detail)


def rec(group, name, status, detail=""):
    RESULTS.append((group, name, status, str(detail)[:80]))
    print(f"  [{status:4}] {group} :: {name}  {('- ' + str(detail)[:60]) if detail else ''}")


def have(mod):
    try:
        return importlib.util.find_spec(mod) is not None
    except Exception:
        return False


def check(group, name, fn):
    """Run a test fn that returns True/raises; record PASS/FAIL."""
    try:
        ok = fn()
        rec(group, name, "PASS" if ok else "FAIL", "" if ok else "assertion false")
    except Exception as e:  # noqa: BLE001
        rec(group, name, "FAIL", f"{type(e).__name__}: {e}")


# ── 1. Static sweep ───────────────────────────────────────────────────────────
def static_tests():
    shells = sorted(REPO.glob("step*.sh")) + sorted((REPO / "utility/physioparse").glob("*.sh"))
    bad = [s.name for s in shells if subprocess.run(["bash", "-n", str(s)]).returncode != 0]
    rec("static", f"bash -n all .sh ({len(shells)})", "PASS" if not bad else "FAIL", ",".join(bad))

    pys = [REPO / "gui/app.py"] + sorted(REPO.glob("utility/*.py")) + \
          sorted((REPO / "utility/physioparse").glob("*.py"))
    bad = [p.name for p in pys
           if subprocess.run([sys.executable, "-m", "py_compile", str(p)]).returncode != 0]
    rec("static", f"py_compile all .py ({len(pys)})", "PASS" if not bad else "FAIL", ",".join(bad))


# ── 2. Shell plumbing (stubbed heavy tools) ───────────────────────────────────
def shell_tests():
    T = Path(tempfile.mkdtemp(prefix="mock_sh_"))
    binp = T / "bin"; binp.mkdir()
    (binp / "matlab").write_text('#!/bin/bash\necho "STUB_MATLAB $*"\nexit 0\n')
    (binp / "findsession").write_text(f'#!/bin/bash\necho "PATH {T}/dicomsrc"\n')
    (binp / "rsync").write_text('#!/bin/bash\necho "STUB_RSYNC $*"\nexit 0\n')
    for f in binp.iterdir():
        f.chmod(0o755)
    (T / "dicomsrc").mkdir(); (T / "dicomsrc/a.dcm").write_text("x")
    env = dict(os.environ, PATH=f"{binp}:{os.environ['PATH']}")

    # fake repo so step04 sources a harmless env (not the cluster one)
    fr = T / "fakerepo"; (fr / "utility").mkdir(parents=True)
    (fr / "utility/fmriprep_env.sh").write_text("")
    s4 = fr / "step04_retroicor_v2.sh"
    s4.write_text((REPO / "step04_retroicor_v2.sh").read_text())

    sd = T / "proj/sourcedata"
    func = sd / "sub-T/ses-01/func"; func.mkdir(parents=True)
    pre = sd / "derivatives/physio/sub-T/preprocessed"; pre.mkdir(parents=True)
    base = "sub-T_ses-01_task-BlockStim_run-01_bold"
    (func / f"{base}.nii.gz").write_text("x"); (func / f"{base}.json").write_text("{}")
    (pre / "sub-T_task-BlockStim_run-01_filtered.mat").write_text("x")
    IN = sd / "derivatives/physio/sub-T/retroicor/input"
    OUT = sd / "derivatives/physio/sub-T/retroicor/output"
    MC, RC = REPO / "utility/matlab_code", REPO / "utility/retroicor"

    def run(args):
        return subprocess.run(["bash"] + args, env=env, capture_output=True, text=True)

    base_args = [str(s4), "sub-T", str(sd), str(pre), str(IN), str(OUT),
                 str(binp / "matlab"), str(MC), str(RC), "01", "1", "40", "1.19", "1", ""]

    r = run([str(s4), "sub-T"])
    rec("shell", "step04 requires sourcedata", "PASS" if r.returncode == 1 and "required" in r.stdout else "FAIL")

    r = run(base_args + ["p2"])
    rec("shell", "step04 p2 copies BOLD+JSON",
        "PASS" if (IN / f"{base}.nii.gz").exists() and (IN / f"{base}.json").exists() else "FAIL")

    r = run(base_args + ["p1"])
    rec("shell", "step04 p1 -> preproc_generate_1D_v2",
        "PASS" if "preproc_generate_1D_v2" in r.stdout else "FAIL")

    r = run(base_args + ["cardiacqc"])
    rec("shell", "step04 cardiacqc -> cardiac_qc",
        "PASS" if "cardiac_qc(" in r.stdout else "FAIL")

    sl = T / "subj.txt"; sl.write_text("subA\n")
    r = run([str(REPO / "step00_unpack_V2.sh"), str(sl)])
    rec("shell", "step00 requires out_path", "PASS" if r.returncode == 1 and "required" in r.stdout else "FAIL")

    r = run([str(REPO / "step00_unpack_V2.sh"), str(sl), str(T / "out"), "foreground"])
    rec("shell", "step00 foreground processes subject",
        "PASS" if (T / "out/subA/DICOM/LOG/step0_DONE.txt").exists() else "FAIL")

    # step05b FreeSurfer segmentation (stub FS tools; PGlandsSeg intentionally absent)
    (binp / "segment_subregions").write_text('#!/bin/bash\necho "STUB_SEGSUB $*" >&2\nexit 0\n')
    (binp / "segment_subregions").chmod(0o755)
    fsh = T / "fs"; fsh.mkdir(); (fsh / "SetUpFreeSurfer.sh").write_text("")
    sdir = T / "subjects"; (sdir / "sub-T" / "mri").mkdir(parents=True)
    sl2 = T / "subj2.txt"; sl2.write_text("sub-T\n")
    s5b = str(REPO / "step05b_freesurfer_segment_v2.sh")

    r = run([s5b, str(sl2), str(fsh), str(sdir), "brainstem"])
    rec("shell", "step05b brainstem seg (stub segment_subregions)",
        "PASS" if r.returncode == 0 and "brainstemSsLabels written" in r.stdout else "FAIL")

    r = run([s5b, str(sl2), str(fsh), str(sdir), "pituitary"])
    rec("shell", "step05b pituitary skips when no FS8.1 (flag, exit 0)",
        "PASS" if r.returncode == 0 and "pituitary skipped" in r.stdout else "FAIL")

    r = run([s5b, str(sl2)])
    rec("shell", "step05b requires freesurfer_home + subjects_dir",
        "PASS" if r.returncode == 1 else "FAIL")

    # step05c brainstem co-reg refinement (stub FS/ANTs tools)
    for t in ("mri_binarize", "antsApplyTransforms", "antsRegistration"):
        (binp / t).write_text('#!/bin/bash\nexit 0\n'); (binp / t).chmod(0o755)
    (sdir / "sub-T" / "mri" / "brainstemSsLabels.v13.mgz").write_text("x")
    fp = T / "fp"; (fp / "sub-T" / "anat").mkdir(parents=True)
    (fp / "sub-T/anat/sub-T_from-T1w_to-MNI152NLin2009cAsym_mode-image_xfm.h5").write_text("x")
    (fp / "sub-T/anat/sub-T_space-MNI152NLin2009cAsym_desc-preproc_T1w.nii.gz").write_text("x")
    mni = T / "mni.nii.gz"; mni.write_text("x")
    s5c = str(REPO / "step05c_brainstem_coreg_v2.sh")
    r = run([s5c, str(sl2), str(fsh), str(sdir), str(fp), str(mni), str(T / "coreg_out")])
    rec("shell", "step05c masked-SyN refine (stub ANTs/FS)",
        "PASS" if r.returncode == 0 and "brainstemRefine" in r.stdout else "FAIL")

    r = run([s5c, str(sl2)])
    rec("shell", "step05c requires its inputs", "PASS" if r.returncode == 1 else "FAIL")

    # step10b atlas->native ROIs (stub ANTs + stub python for roi_extract)
    (fp / "sub-T/anat/sub-T_desc-preproc_T1w.nii.gz").write_text("x")            # native T1w
    (fp / "sub-T/anat/sub-T_from-MNI152NLin2009cAsym_to-T1w_mode-image_xfm.h5").write_text("x")
    coreg = T / "coreg"; (coreg / "sub-T").mkdir(parents=True)
    (coreg / "sub-T/sub-T_brainstemRefine_1InverseWarp.nii.gz").write_text("x")
    atlas = T / "atlas.nii.gz"; atlas.write_text("x")
    croot = T / "conroot"; (croot / "sub-T" / "BlockStim").mkdir(parents=True)
    (croot / "sub-T/BlockStim/con_0001.nii").write_text("x")
    (binp / "pystub").write_text('#!/bin/bash\nexit 0\n'); (binp / "pystub").chmod(0o755)
    s10b = str(REPO / "step10b_atlas_native_roi_v2.sh")
    r = run([s10b, str(sl2), str(fp), str(coreg), str(atlas), str(croot),
             "*/con_0001.nii", str(T / "natroi_out"), str(binp / "pystub")])
    rec("shell", "step10b atlas->native warp+extract (stubs)",
        "PASS" if r.returncode == 0 and "atlas-in-native" in r.stdout else "FAIL")

    r = run([s10b, str(sl2)])
    rec("shell", "step10b requires its inputs", "PASS" if r.returncode == 1 else "FAIL")


# ── 3. Python logic (synthetic fixtures) ──────────────────────────────────────
def logic_tests():
    # heuristic: TOPUP_AP/PA route to fmap dir-AP/PA
    def t_heur():
        import heuristic, types
        rows = [types.SimpleNamespace(series_id="11-TOPUP_AP", series_description="TOPUP_AP_ep2d_bold", dim3=92, dim4=2, patient_id="p"),
                types.SimpleNamespace(series_id="12-TOPUP_PA", series_description="TOPUP_PA_ep2d_bold", dim3=92, dim4=1, patient_id="p"),
                types.SimpleNamespace(series_id="10-BlockStim", series_description="BlockStim_ep2d_bold", dim3=92, dim4=326, patient_id="p")]
        info = heuristic.infotodict(rows)
        keys = {k[0].split("/")[-1]: v for k, v in info.items()}
        return (keys["sub-{subject}_{session}_dir-AP_epi"] == ["11-TOPUP_AP"]
                and keys["sub-{subject}_{session}_dir-PA_epi"] == ["12-TOPUP_PA"])
    check("logic", "heuristic routes TOPUP -> fmap", t_heur)

    # audit_cardinality flagging
    def t_card():
        import audit_cardinality as A
        ok = {"step01_raw_bold": 326, "step02_mrtrig": 326, "step04_retro": 326,
              "step05_fmriprep": 326, "step05_confounds": 326, "step06_motion": 326}
        bad = dict(ok); bad["step05_fmriprep"] = 323
        rest = {"step01_raw_bold": 200, "step02_mrtrig": 0, "step04_retro": 200,
                "step05_fmriprep": 200, "step05_confounds": 200, "step06_motion": None}
        return (A._flag(ok)[0] == "" and A._flag(bad)[0] == "FLAG" and A._flag(rest)[0] == "")
    check("logic", "cardinality flag (mismatch/triggerless)", t_card)

    # audit_sdc verdicts on synthetic fmriprep tree
    def t_sdc():
        import audit_sdc as S
        d = Path(tempfile.mkdtemp())
        fp = d / "fmriprep/sub-T"; func = fp / "ses-01/func"; fig = fp / "figures"
        func.mkdir(parents=True); fig.mkdir(parents=True)
        for task in ("BlockStim", "ContinuousStim", "rest"):
            (func / f"sub-T_ses-01_task-{task}_run-01_space-T1w_desc-preproc_bold.nii.gz").write_text("")
            (func / f"sub-T_ses-01_task-{task}_run-01_space-T1w_desc-preproc_bold.json").write_text("{}")
        (fig / "sub-T_ses-01_task-BlockStim_run-01_desc-sdc_bold.svg").write_text("<svg/>")
        bids = d / "bids/sub-T/ses-01/fmap"; bids.mkdir(parents=True)
        intended = ["ses-01/func/sub-T_ses-01_task-BlockStim_run-01_bold.nii.gz",
                    "ses-01/func/sub-T_ses-01_task-ContinuousStim_run-01_bold.nii.gz"]
        (bids / "sub-T_ses-01_dir-AP_epi.json").write_text(json.dumps({"IntendedFor": intended}))
        recs = {(r["task"]): r["verdict"] for r in S.audit_subject(d / "fmriprep", "sub-T", d / "bids")}
        return (recs["BlockStim"] == "SDC_APPLIED" and recs["ContinuousStim"] == "FMAP_BUT_NO_SDC"
                and recs["rest"] == "NO_FIELDMAP")
    check("logic", "SDC audit verdicts", t_sdc)

    # assemble_corrected_bids: metadata flag (bad PED) + non-fatal skip
    def t_asm():
        import assemble_corrected_bids as B
        d = Path(tempfile.mkdtemp())
        func = d / "src/sub-T/ses-01/func"; func.mkdir(parents=True)
        dfunc = d / "cor/sub-T/ses-01/func"; dfunc.mkdir(parents=True)
        meta = {"RepetitionTime": 1.19, "SliceTiming": [0.01 * i for i in range(92)],
                "PhaseEncodingDirection": "j", "TotalReadoutTime": 0.027}  # PED wrong
        b = "sub-T_ses-01_task-BlockStim_run-01_bold"
        (dfunc / f"{b}.nii.gz").write_text(""); (dfunc / f"{b}.json").write_text(json.dumps(meta))
        (func / f"{b}.nii.gz").write_text("")
        issues, _ = B.verify_subject(d / "src", "sub-T", d / "cor", "01")
        return any("PhaseEncodingDirection" in i for i in issues)
    check("logic", "corrected-BIDS metadata flag", t_asm)

    # extract_motion_regressors: 6 rigid + FD spike from synthetic confounds
    def t_motion():
        import extract_motion_regressors as M
        d = Path(tempfile.mkdtemp())
        tsv = d / "sub-T_ses-01_task-BlockStim_run-01_desc-confounds_timeseries.tsv"
        cols = M.RIGID_COLS + ["framewise_displacement"]
        rows = [dict(zip(cols, ["0.0"] * 6 + ["n/a"]))]
        rows += [dict(zip(cols, ["0.1"] * 6 + ["0.2"])) for _ in range(3)]
        rows += [dict(zip(cols, ["0.1"] * 6 + ["0.9"]))]  # one spike (>0.5)
        with open(tsv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols, delimiter="\t"); w.writeheader(); w.writerows(rows)
        s = M.process_one(str(tsv), str(d / "out"), fd_thresh=0.5)
        return s["n_motion_cols"] == 6 and s["n_spikes"] == 1 and s["n_volumes"] == 5
    check("logic", "motion regressors (6 rigid + FD spike)", t_motion)

    # qc_snapshots piezo cohort report
    def t_piezo():
        import qc_snapshots as Q
        d = Path(tempfile.mkdtemp())
        cq = d / "derivatives/physio/sub-T/cardiac_qc"; cq.mkdir(parents=True)
        with open(cq / "sub-T_cardiac_qc.csv", "w", newline="") as f:
            f.write("subject,task,run,verdict,recommendation\n")
            f.write("sub-T,BlockStim,01,GOOD,both\nsub-T,ContinuousStim,01,BAD,resp\n")
        out = d / "qc"
        summ = Q.build_piezo_cohort_report(str(d), str(out), print_fn=lambda *a: None)
        return summ["flagged"] == 1 and (out / "group_piezo_qc.md").exists()
    check("logic", "piezo cohort report", t_piezo)

    # collect_provenance writes a record
    def t_prov():
        import collect_provenance as P  # noqa: F401
        d = Path(tempfile.mkdtemp())
        subprocess.run([sys.executable, str(REPO / "utility/collect_provenance.py"),
                        "--out", str(d), "--repo", str(REPO), "--no-matlab",
                        "--python", sys.executable], capture_output=True)
        return (d / "provenance_latest.json").exists()
    check("logic", "provenance record written", t_prov)

    # dep-gated skips
    if have("nibabel"):
        check("logic", "roi_extract import", lambda: __import__("roi_extract") is not None)
    else:
        rec("logic", "roi_extract (needs nibabel)", "SKIP", "nibabel not installed")
    if have("matplotlib"):
        check("logic", "extract_stim_onsets import", lambda: __import__("extract_stim_onsets") is not None)
    else:
        rec("logic", "extract_stim_onsets (needs matplotlib)", "SKIP", "matplotlib not installed")


# ── 4. GUI ────────────────────────────────────────────────────────────────────
def gui_tests():
    r = subprocess.run([sys.executable, "-m", "py_compile", str(REPO / "gui/app.py")])
    rec("gui", "app.py compiles", "PASS" if r.returncode == 0 else "FAIL")

    # _step04_cmd arg order must match step04 positional order (static check)
    def t_argorder():
        app = (REPO / "gui/app.py").read_text()
        i = app.index("def _step04_cmd")
        j = app.index("return [", i)            # only check the returned arg list
        block = app[j:j + 700]
        order = ["_subj_var.get", '["sourcedata"]', "_preproc_var.get", "_input_var.get",
                 "_output_var.get", "_matlab_var.get", "_mcode_var.get", "_retro_var.get",
                 "_session_var.get", "_sms_var.get", "_fs_var.get", "_tr_var.get",
                 "_cardiac_var.get", "\n            decision,", "\n            part,"]
        pos = [block.find(tok) for tok in order]
        return all(p != -1 for p in pos) and pos == sorted(pos)
    check("gui", "_step04_cmd arg order matches .sh", t_argorder)

    if have("tkinter") or importlib.util.find_spec("_tkinter"):
        rec("gui", "launch GUI headless", "SKIP", "needs display")
    else:
        rec("gui", "launch GUI (no _tkinter here)", "SKIP", "tkinter unavailable in this env")


# ── Report image (Pillow) ─────────────────────────────────────────────────────
def render(out_png):
    from PIL import Image, ImageDraw
    n = len(RESULTS)
    rowh, top, pad = 26, 92, 16
    W, H = 940, top + rowh * n + 40
    col = {"PASS": (46, 160, 67), "FAIL": (210, 50, 45), "SKIP": (140, 140, 140)}
    img = Image.new("RGB", (W, H), (22, 22, 26)); d = ImageDraw.Draw(img)
    nP = sum(1 for r in RESULTS if r[2] == "PASS")
    nF = sum(1 for r in RESULTS if r[2] == "FAIL")
    nS = sum(1 for r in RESULTS if r[2] == "SKIP")
    import datetime
    d.text((pad, 14), "tVNS pipeline — mock/logic test report (no real data)", fill=(235, 235, 235))
    d.text((pad, 36), datetime.datetime.now().isoformat(timespec="seconds"), fill=(150, 150, 150))
    d.text((pad, 58), f"PASS {nP}    FAIL {nF}    SKIP {nS}    (total {n})",
           fill=(46, 200, 90) if nF == 0 else (220, 90, 80))
    d.line((pad, top - 6, W - pad, top - 6), fill=(60, 60, 70))
    for i, (group, name, status, detail) in enumerate(RESULTS):
        y = top + i * rowh
        c = col.get(status, (200, 200, 200))
        d.rectangle((pad, y + 4, pad + 54, y + 20), fill=c)
        d.text((pad + 8, y + 6), status, fill=(15, 15, 15))
        d.text((pad + 70, y + 6), f"[{group}] {name}", fill=(225, 225, 225))
        if detail:
            d.text((pad + 70 + 360, y + 6), detail[:60], fill=(160, 120, 120))
    img.save(out_png)


def main():
    print("Running mock/logic tests (no real data, no installs)...\n")
    static_tests()
    shell_tests()
    logic_tests()
    gui_tests()
    out = REPO / "tests" / "test_report.png"
    render(out)
    nF = sum(1 for r in RESULTS if r[2] == "FAIL")
    print(f"\nReport image: {out}")
    print(f"SUMMARY: {sum(1 for r in RESULTS if r[2]=='PASS')} pass, "
          f"{nF} fail, {sum(1 for r in RESULTS if r[2]=='SKIP')} skip")
    sys.exit(1 if nF else 0)


if __name__ == "__main__":
    main()
