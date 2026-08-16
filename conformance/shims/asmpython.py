"""Shim for asmpython: compile the case to a native binary, then run it.

The subject is the compiler in `src/asmpython` -- the 3.14 tree. It is put on
the path explicitly rather than left to `import asmpython`, because a stale
copy of the SAME import name sits in site-packages on a normal developer
machine: without the explicit path the suite silently scores an installed build
that nobody in this checkout can edit, and every fix reads as having no effect.
(`archived/legacy/` holds the pre-rewrite compiler. It is archived, it is not
part of 3.14, and it is deliberately NOT what this measures.)

A refusal is reported as returncode None, which the harness renders REFUSED
rather than FAIL: "cannot compile this" and "compiles to the wrong answer" are
both non-conformance, but they are different bugs and lumping them together
loses the distinction. For a compiler that implements a subset of the language,
that distinction is most of the signal.
"""
from __future__ import annotations

import atexit
import itertools
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

# The compiler lives one directory up from the suite. This is the ONLY
# implementation-specific path in the project; every other file is portable.
_ASMPYTHON_ROOT = Path(__file__).resolve().parents[2]

# The compiler package sits under src/, not at the repo root. Putting it on the
# path explicitly is load-bearing rather than tidy: a normal developer machine
# has a released `asmpython` in site-packages answering to the same import name,
# and `-m asmpython` finds THAT unless the checkout is placed ahead of it. The
# failure is silent and expensive -- every fix appears to change nothing.
_COMPILER_PATH = _ASMPYTHON_ROOT / "src"


def _env() -> dict:
    env = dict(os.environ)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(_COMPILER_PATH) + (os.pathsep + existing if existing else "")
    )
    return env

_IS_WIN = sys.platform == "win32"
# Windows: build directly into a short path. Smart App Control blocks freshly
# compiled exes under some directories, and per-process naming keeps two
# concurrent runs from executing each other's binaries.
_BUILD = Path("C:/Temp") if _IS_WIN else Path(tempfile.gettempdir())
_TAG = f"cf{os.getpid()}"

# A per-call serial number, because the process id is NOT enough to make an
# artifact name unique: the harness runs cases on THREADS of one process, and a
# case's stem is only its file name. The cross-products reuse names by design --
# `tuple-elems.py` exists once per consumer directory, `int.py` once per trip --
# so eight workers were writing, running and deleting one shared path.
#
# That does not fail loudly. It reports as a REFUSED or a wrong answer for
# whichever case lost the race, and it MOVES between runs, so it reads exactly
# like a flaky implementation bug. It cost one falsely-reported regression
# (consumer/negative-index/tuple-elems) that reproduced as a clean pass the
# moment it was run on its own.
_SERIAL = itertools.count()
_SERIAL_LOCK = threading.Lock()

# The compiler builds its runtime into a SHARED directory (asmpython/_runtime/
# _build/) and caches it. That cache is a benefit once it exists and a hazard
# while it is being created: eight workers all missing it at once would build
# into the same place simultaneously. So the first compile of a run holds this
# lock for its whole duration and the rest proceed in parallel against a cache
# that is already warm. Costs one serial compile per run.
_WARMUP_LOCK = threading.Lock()
_warmed = False


def _artifact(stem: str) -> Path:
    with _SERIAL_LOCK:
        n = next(_SERIAL)
    return _BUILD / f"{stem}_{_TAG}_{n}{'.exe' if _IS_WIN else ''}"


def _remove(path: Path) -> None:
    """Delete a built binary, retrying briefly.

    On Windows a just-exited process can still hold its own image mapped for a
    moment, so the first unlink raises PermissionError. Swallowing that -- which
    is what this used to do -- leaks the binary permanently, and `_BUILD` is
    C:/Temp, shared with tests/runner.py: a directory with tens of thousands of
    stale entries slows every create for both. A few short retries clear the
    normal case; `_sweep` catches the rest.
    """
    for delay in (0.0, 0.02, 0.1):
        if delay:
            time.sleep(delay)
        try:
            path.unlink()
            return
        except FileNotFoundError:
            return
        except OSError:
            continue


def _remove_tree(path: Path) -> None:
    """Delete a per-compile workdir. Best effort, like `_remove`."""
    import shutil
    shutil.rmtree(path, ignore_errors=True)


def _sweep() -> None:
    """Remove anything this process built that `_remove` could not.

    Registered atexit so a run that ends normally leaves nothing behind. Scoped
    to this process's own `_TAG`, so a CONCURRENT run's artifacts are never
    touched -- deleting those would make the other run execute a missing binary
    and report it as a refusal.
    """
    for leftover in _BUILD.glob(f"*_{_TAG}_*"):
        if leftover.is_dir():
            _remove_tree(leftover)
            continue
        try:
            leftover.unlink()
        except OSError:
            pass  # held by something still exiting; nothing further to try


atexit.register(_sweep)


def run(case_path: str, timeout: int):
    """-> (stdout, stderr, returncode); returncode None == refused to compile."""
    stem = Path(case_path).stem
    if _IS_WIN:
        # Strip installer-detection trigger words, which make Windows demand
        # elevation for a freshly built exe.
        import re
        stem = re.sub(r"(?i)(update|install|setup|patch)", "case", stem)
    out_bin = _artifact(stem)

    # Each compile gets its OWN workdir. The default is `.asmpython` under the
    # cwd, and the harness runs cases on threads of one process -- eight workers
    # would write their intermediates over each other under one name, which
    # reports as a wrong answer or a refusal for whichever case lost the race
    # and MOVES between runs, reading exactly like a flaky implementation bug.
    workdir = _BUILD / f"wd_{_TAG}_{out_bin.stem}"
    cmd = [sys.executable, "-m", "asmpython", "build", str(case_path),
           "--target", "windows" if _IS_WIN else "linux",
           "--workdir", str(workdir), "-o", str(out_bin)]
    def _compile():
        return subprocess.run(cmd, capture_output=True, text=True,
                              errors="replace", cwd=str(_ASMPYTHON_ROOT),
                              env=_env(), timeout=timeout)

    global _warmed
    try:
        if _warmed:
            cp = _compile()
        else:
            with _WARMUP_LOCK:
                cp = _compile()
                _warmed = True
    except subprocess.TimeoutExpired:
        # A timed-out compile can still have written a partial binary.
        _remove(out_bin)
        raise TimeoutError(f"compile of {case_path}")

    if cp.returncode != 0:
        # A REFUSED compile may leave a partial artifact behind. Cleanup used to
        # happen only on the success path, so every refusal leaked -- and
        # refusals are the single largest bucket when scoring a compiler that
        # implements a subset.
        _remove(out_bin)
        _remove_tree(workdir)
        return "", (cp.stderr or "") + (cp.stdout or ""), None
    if not out_bin.exists():
        # Exit 0 with no binary: the driver took a path that writes nothing.
        # There is no native artifact, so there is nothing to score -- report it
        # as a refusal rather than as an empty (and therefore failing) run.
        _remove_tree(workdir)
        return "", (cp.stdout or "") + (cp.stderr or ""), None

    flags = {"creationflags": 0x08000000} if _IS_WIN else {}
    try:
        rp = subprocess.run([str(out_bin)], capture_output=True, text=True,
                            errors="replace", timeout=timeout, **flags)
    except subprocess.TimeoutExpired:
        raise TimeoutError(f"run of {case_path}")
    finally:
        _remove(out_bin)
        _remove_tree(workdir)
    return rp.stdout, rp.stderr, rp.returncode
