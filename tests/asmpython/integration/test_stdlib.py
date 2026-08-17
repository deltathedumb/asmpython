"""The standard library, measured against CPython one module at a time.

Every `tests/stdlib/<module>.py` is a program exercising one bundled module.
It is run under CPython and under asmpython and the outputs must be IDENTICAL.

WHY CPYTHON IS THE ORACLE AND NOT A GOLDEN FILE. A recorded expectation is
whatever the implementation printed on the day someone recorded it, so a wrong
answer becomes the specification the moment it is committed. Running CPython
each time means the comparison is always against the thing being copied -- and
when CPython changes, the test changes with it rather than pinning a version
nobody chose.

THE TEST PROGRAMS ARE WRITTEN AGAINST THE SPECIFICATION. A program that prints
asmpython's answer and asserts it equals itself tests nothing; each one asserts
what the module is DOCUMENTED to do, so a gap shows up as a difference rather
than as a passing test. That is the same rule `conformance/` follows, applied
one level up: there the subject is the language, here it is the library.

WHAT A FAILURE MEANS. asmpython's, until shown otherwise. The bundled module is
ordinary Python compiled by this compiler, so a difference is either a missing
piece of the module or a gap in the compiler -- and the second is worth more
than the first, because `bundled.py`'s own header says a construct a bundled
module cannot use is a bug with a name rather than a reason to write C.

See `docs/STDLIB.md` for the rebuild order and what each module claims.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests import harness
from tests.harness import snapshot

ROOT = Path(__file__).resolve().parents[3]
SRC = snapshot.current(ROOT)
CASES = ROOT / "tests" / "stdlib"


def _modules() -> list[str]:
    """Every module with a test program, in a stable order."""
    return sorted(p.stem for p in CASES.glob("*.py"))


def _cpython(path: Path) -> subprocess.CompletedProcess:
    """THE ORACLE. Run in the case's own directory so `import <module>` finds
    the real standard library rather than the file being run -- a program
    named `keyword.py` importing `keyword` otherwise imports ITSELF, which
    prints something plausible and means nothing."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    return subprocess.run([sys.executable, "-I", str(path)],
                          capture_output=True, text=True, env=env,
                          cwd=str(ROOT))


def _asmpython(path: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    return subprocess.run(
        [sys.executable, "-m", "asmpython", "run", str(path)],
        capture_output=True, text=True, env=env, cwd=str(ROOT))


class TestEachModuleMatchesCPython:
    @harness.cases("module", _modules())
    def test_the_outputs_are_identical(self, module):
        path = CASES / f"{module}.py"
        want = _cpython(path)
        assert want.returncode == 0, (
            f"the ORACLE failed, so the test program is wrong rather than "
            f"asmpython:\n{want.stderr[-2000:]}")
        got = _asmpython(path)
        assert "Traceback" not in got.stderr, got.stderr[-2000:]
        assert got.returncode == 0, (
            f"asmpython could not run it:\n{got.stderr[-3000:]}")
        if got.stdout != want.stdout:
            wl, gl = want.stdout.split("\n"), got.stdout.split("\n")
            first = next((i for i, (a, b) in enumerate(zip(wl, gl)) if a != b),
                         min(len(wl), len(gl)))
            lines = "\n".join(
                f"  line {i + 1}\n    cpython: {wl[i] if i < len(wl) else '<end>'!r}"
                f"\n    asmpy:   {gl[i] if i < len(gl) else '<end>'!r}"
                for i in range(first, min(first + 3, max(len(wl), len(gl)))))
            harness.fail(f"{module} differs from CPython:\n{lines}")


class TestTheSuiteIsHonest:
    """The ways a differential suite silently stops testing anything."""

    def test_every_test_program_declares_its_coverage(self):
        """The first line says what of the module is claimed.

        The point of the rebuild is that coverage is the deliverable
        (`docs/STDLIB.md`), and a module whose test does not say what it covers
        has quietly gone back to being however deep somebody needed that day.
        """
        missing = [p.name for p in CASES.glob("*.py")
                   if not p.read_text(encoding="utf-8").startswith("# COVERAGE:")]
        assert not missing, f"no COVERAGE line: {missing}"

    def test_every_test_program_imports_the_module_it_names(self):
        """`tests/stdlib/enum.py` must exercise `enum`. A file that tests
        something else is a module counted as done that never was."""
        wrong = []
        for p in CASES.glob("*.py"):
            text = p.read_text(encoding="utf-8")
            if f"import {p.stem}" not in text:
                wrong.append(p.name)
        assert not wrong, f"does not import its own module: {wrong}"

    def test_a_bundled_module_has_a_test(self):
        """A module in `bundled/` with no test program is one nobody has
        measured. The `_py*` modules are exempt: they are the embedded
        compiler rather than the library, and are tested through the
        conformance cases that call `compile`, `eval` and `exec`."""
        bundled = {p.stem for p in
                   (Path(SRC) / "asmpython" / "frontends" / "python"
                    / "bundled").glob("*.py")
                   if not p.stem.startswith("_py")}
        untested = sorted(bundled - set(_modules()))
        assert not untested, f"bundled but unmeasured: {untested}"
