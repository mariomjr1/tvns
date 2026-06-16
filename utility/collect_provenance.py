#!/usr/bin/env python3
"""
collect_provenance.py  —  batch-level analysis provenance (Task 29)

Writes a single JSON recording the software environment used for a batch run, so a
scanner/platform effect is never confused with an analysis-environment change. Best
effort: every field is captured independently; a missing tool is recorded as null
with a note, never a crash. This is a record, not a gate — it always exits 0.

Captures:
  - pipeline git commit / branch / dirty state
  - fMRIPrep Singularity image (path, size, mtime, version-from-filename, optional sha256)
  - SPM (path + version from Contents.m; authoritative spm('Ver') if MATLAB is run)
  - MATLAB version (if --matlab given)
  - RETROICOR source (generate_1D_fun_1.m / retroicor_main_modi.m path + sha256)
  - R-DECO (R_DECO.m path + sha256 + version string if present)
  - Python: interpreter, version, key package versions, and a full pip-freeze snapshot
  - host / OS / user / timestamp

Outputs (in --out):
  provenance_<timestamp>.json   full record
  provenance_latest.json        copy of the most recent record
  requirements_frozen_<timestamp>.txt   pip freeze of the Python env (the real pin)

Usage:
  collect_provenance.py --out <dir> [--repo <pipeline repo>] [--fmriprep-simg <path>]
      [--spm-dir <dir>] [--matlab <exe>] [--python <exe>] [--retro-code <dir>]
      [--matlab-code <dir>] [--label <text>] [--hash-simg] [--no-matlab]
      [--matlab-timeout <s>]

Created by Mario Murakami
"""

import argparse
import datetime
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
from pathlib import Path


def _run(cmd, timeout=30, cwd=None):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:  # noqa: BLE001
        return None, "", str(e)


def _sha256(path, buf=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(buf), b""):
            h.update(chunk)
    return h.hexdigest()


def file_info(path, do_hash=False):
    if not path:
        return {"exists": False}
    p = Path(path)
    if not p.exists():
        return {"path": str(path), "exists": False}
    st = p.stat()
    d = {"path": str(p), "exists": True, "size_bytes": st.st_size,
         "mtime": datetime.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds")}
    if do_hash and p.is_file():
        try:
            d["sha256"] = _sha256(p)
        except Exception as e:  # noqa: BLE001
            d["sha256_error"] = str(e)
    return d


def git_info(repo):
    info = {"repo": str(repo)}
    if not (Path(repo) / ".git").exists():
        info["note"] = "not a git repository"
        return info
    for key, args in {"commit": ["rev-parse", "HEAD"],
                      "branch": ["rev-parse", "--abbrev-ref", "HEAD"],
                      "describe": ["describe", "--tags", "--always", "--dirty"]}.items():
        rc, out, _ = _run(["git", "-C", str(repo)] + args)
        info[key] = out if rc == 0 and out else None
    rc, out, _ = _run(["git", "-C", str(repo), "status", "--porcelain"])
    info["dirty"] = bool(out) if rc == 0 else None
    return info


def fmriprep_info(simg, do_hash):
    if not simg:
        return {"note": "no --fmriprep-simg given"}
    d = file_info(simg, do_hash=do_hash)
    m = re.search(r"fmriprep[-_]([0-9][0-9A-Za-z.\-]*)\.(?:simg|sif)",
                  os.path.basename(str(simg)))
    d["version_from_filename"] = m.group(1) if m else None
    if not do_hash and d.get("exists"):
        d["sha256"] = "(skipped — pass --hash-simg to compute)"
    return d


def spm_info(spm_dir):
    if not spm_dir:
        return {"note": "no --spm-dir given"}
    d = {"path": str(spm_dir), "exists": Path(spm_dir).is_dir()}
    contents = Path(spm_dir) / "Contents.m"
    if contents.is_file():
        try:
            txt = contents.read_text(errors="ignore")
            m = re.search(r"Version\s+([0-9]+)", txt)
            d["version_from_contents"] = m.group(1) if m else None
        except Exception as e:  # noqa: BLE001
            d["contents_error"] = str(e)
    return d


def matlab_runtime(matlab, spm_dir, timeout):
    """Authoritative MATLAB + SPM versions by launching MATLAB once (best effort)."""
    if not matlab:
        return {"note": "no --matlab given (runtime MATLAB/SPM capture skipped)"}
    spm_add = ""
    if spm_dir:
        spm_add = (f"try, addpath('{spm_dir}'); "
                   f"fprintf('SPM_VERSION=%s\\n', spm('Ver')); "
                   f"catch e, fprintf('SPM_ERROR=%s\\n', e.message); end")
    code = f"fprintf('MATLAB_VERSION=%s\\n', version); {spm_add}"
    rc, out, err = _run([matlab, "-nodisplay", "-nosplash", "-batch", code], timeout=timeout)
    res = {"returncode": rc}
    if rc is None:
        res["error"] = err
        return res
    mv = re.search(r"MATLAB_VERSION=(.+)", out)
    sv = re.search(r"SPM_VERSION=(.+)", out)
    se = re.search(r"SPM_ERROR=(.+)", out)
    res["matlab_version"] = mv.group(1).strip() if mv else None
    res["spm_version"] = sv.group(1).strip() if sv else None
    if se:
        res["spm_error"] = se.group(1).strip()
    return res


def retroicor_info(dirs):
    out = {}
    for name in ("generate_1D_fun_1.m", "retroicor_main_modi.m"):
        found = None
        for d in dirs:
            if not d:
                continue
            hits = sorted(Path(d).rglob(name)) if Path(d).is_dir() else []
            if hits:
                found = hits[0]
                break
        out[name] = file_info(found, do_hash=True) if found else {"exists": False}
    return out


def rdeco_info(dirs):
    for d in dirs:
        if not d:
            continue
        if not Path(d).is_dir():
            continue
        cand = sorted(Path(d).rglob("R_DECO.m"))
        if cand:
            info = file_info(cand[0], do_hash=True)
            try:
                txt = cand[0].read_text(errors="ignore")[:4000]
                m = re.search(r"[Vv]ersion[:\s]+([0-9][\w.\-]*)", txt)
                info["version_string"] = m.group(1) if m else None
            except Exception:  # noqa: BLE001
                pass
            return info
    return {"exists": False, "note": "R_DECO.m not found in searched dirs"}


def python_info():
    info = {"executable": sys.executable, "version": sys.version.split()[0],
            "platform": platform.platform()}
    pkgs = {}
    try:
        from importlib import metadata as imd
        for name in ("nibabel", "numpy", "scipy", "pandas", "matplotlib",
                     "nilearn", "pillow", "scikit-image"):
            try:
                pkgs[name] = imd.version(name)
            except Exception:  # noqa: BLE001
                pkgs[name] = None
    except Exception as e:  # noqa: BLE001
        info["pkg_error"] = str(e)
    info["packages"] = pkgs
    return info


def pip_freeze(python_exe):
    rc, out, _ = _run([python_exe or sys.executable, "-m", "pip", "freeze"], timeout=120)
    return out if rc == 0 else None


def main():
    ap = argparse.ArgumentParser(
        description="Batch-level analysis provenance (Task 29). Record only — exits 0.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    repo_default = Path(__file__).resolve().parents[1]
    ap.add_argument("--out", default="./provenance", help="output directory")
    ap.add_argument("--repo", default=str(repo_default), help="pipeline git repo")
    ap.add_argument("--fmriprep-simg", default="", help="fMRIPrep .simg/.sif path")
    ap.add_argument("--spm-dir", default="", help="SPM12 directory")
    ap.add_argument("--matlab", default="", help="MATLAB executable (runtime SPM/MATLAB ver)")
    ap.add_argument("--python", default="", help="Python interpreter for pip freeze")
    ap.add_argument("--retro-code", default="", help="RETROICOR code dir")
    ap.add_argument("--matlab-code", default="", help="MATLAB code dir (generate_1D_fun_1.m)")
    ap.add_argument("--label", default="", help="free-text batch label")
    ap.add_argument("--hash-simg", action="store_true", help="sha256 the (large) simg")
    ap.add_argument("--no-matlab", action="store_true", help="skip launching MATLAB")
    ap.add_argument("--matlab-timeout", type=int, default=180)
    args = ap.parse_args()

    ts = datetime.datetime.now().isoformat(timespec="seconds")
    search_dirs = [args.matlab_code, args.retro_code, str(repo_default)]

    prov = {
        "schema": "tvns-provenance/1",
        "generated": ts,
        "label": args.label or None,
        "host": platform.node(),
        "user": os.environ.get("USER") or os.environ.get("USERNAME"),
        "pipeline_git": git_info(args.repo),
        "fmriprep": fmriprep_info(args.fmriprep_simg, args.hash_simg),
        "spm": spm_info(args.spm_dir),
        "matlab_runtime": ({"note": "skipped (--no-matlab)"} if args.no_matlab
                           else matlab_runtime(args.matlab, args.spm_dir, args.matlab_timeout)),
        "retroicor": retroicor_info(search_dirs),
        "rdeco": rdeco_info([args.retro_code, str(repo_default)]),
        "python": python_info(),
    }

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = ts.replace(":", "").replace("-", "")

    # pip-freeze snapshot — the authoritative Python pin for this batch
    freeze = pip_freeze(args.python)
    if freeze is not None:
        req_path = out_dir / f"requirements_frozen_{stamp}.txt"
        req_path.write_text(freeze + "\n")
        prov["python"]["requirements_frozen"] = str(req_path)

    json_path = out_dir / f"provenance_{stamp}.json"
    json_path.write_text(json.dumps(prov, indent=2) + "\n")
    (out_dir / "provenance_latest.json").write_text(json.dumps(prov, indent=2) + "\n")

    # console summary
    g = prov["pipeline_git"]
    print(f"[provenance] pipeline {g.get('branch')}@{(g.get('commit') or '?')[:10]}"
          f"{' (dirty)' if g.get('dirty') else ''}")
    print(f"[provenance] fMRIPrep: {prov['fmriprep'].get('version_from_filename')}  "
          f"SPM: {prov['spm'].get('version_from_contents') or prov['matlab_runtime'].get('spm_version')}  "
          f"MATLAB: {prov['matlab_runtime'].get('matlab_version')}")
    print(f"[provenance] Python {prov['python']['version']} "
          f"(nibabel {prov['python']['packages'].get('nibabel')}, "
          f"numpy {prov['python']['packages'].get('numpy')}, "
          f"scipy {prov['python']['packages'].get('scipy')})")
    print(f"[provenance] Wrote {json_path}")
    print(f"[provenance] Wrote {out_dir / 'provenance_latest.json'}")


if __name__ == "__main__":
    main()
