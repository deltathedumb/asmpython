"""Shim for asmpython: compile the case to a native binary, then run it.

Deliberately passes --no-pyinbin-fallback. Without it the CLI falls back to
interpreting a source the native backend REFUSED, prints "pyinbin fallback
executed successfully", and exits 0 -- so a compile refusal is indistinguishable
from a pass at the command line. A conformance suite must never score an
interpreted fallback as native conformance.

A refusal is reported as returncode None, which the harness renders REFUSED
rather than FAIL: "cannot compile this" and "compiles to the wrong answer" are
both non-conformance, but they are different bugs and lumping them together
loses the distinction.
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

# The compiler lives one directory up from the suite. This is the ONLY
# implementation-specific path in the project; every other file is portable.
_ASMPYTHON_ROOT = Path(__file__).resolve().parents[2]

_IS_WIN = sys.platform == "win32"
# Windows: build directly into a short path. Smart App Control blocks freshly
# compiled exes under some directories, and per-process naming keeps two
# concurrent runs from executing each other's binaries.
_BUILD = Path("C:/Temp") if _IS_WIN else Path(tempfile.gettempdir())
_TAG = f"cf{os.getpid()}"


def _artifact(stem: str) -> Path:
    return _BUILD / f"{stem}_{_TAG}{'.exe' if _IS_WIN else ''}"


def run(case_path: str, timeout: int):
    """-> (stdout, stderr, returncode); returncode None == refused to compile."""
    stem = Path(case_path).stem
    if _IS_WIN:
        # Strip installer-detection trigger words, which make Windows demand
        # elevation for a freshly built exe.
        import re
        stem = re.sub(r"(?i)(update|install|setup|patch)", "case", stem)
    out_bin = _artifact(stem)

    cmd = [sys.executable, "-m", "asmpython", str(case_path),
           "--target", "windows" if _IS_WIN else "linux",
           "--no-pyinbin-fallback", "-o", str(out_bin)]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True,
                            errors="replace", cwd=str(_ASMPYTHON_ROOT),
                            timeout=timeout)
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"compile of {case_path}")

    if cp.returncode != 0:
        return "", (cp.stderr or "") + (cp.stdout or ""), None
    if not out_bin.exists():
        # Exit 0 with no binary: the fallback ran despite --no-pyinbin-fallback,
        # or the driver took a path that writes nothing. Either way there is no
        # native artifact, so there is nothing to score.
        return "", (cp.stdout or "") + (cp.stderr or ""), None

    flags = {"creationflags": 0x08000000} if _IS_WIN else {}
    try:
        rp = subprocess.run([str(out_bin)], capture_output=True, text=True,
                            errors="replace", timeout=timeout, **flags)
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"run of {case_path}")
    finally:
        try:
            out_bin.unlink()
        except OSError:
            pass  # still mapped by a just-exited process; harmless
    return rp.stdout, rp.stderr, rp.returncode
