"""Shim for the embedded interpreter, run on the HOST rather than in a binary.

The same subject as `embedded.py` -- `_pylex`, `_pyparse`, `_pyvalidate` and
`_pyrun`, the Python-in-Python compiler and tree-walker that gets spliced into
a produced program -- but imported and run under CPython instead of compiled
first.

WHY BOTH EXIST. `embedded.py` measures the thing that ships and costs a native
compile per batch; this costs about a tenth of a second per case. They can
disagree, and a disagreement is itself a finding: the bundled modules are
compiled by asmpython in one and interpreted by CPython in the other, so a case
passing here and failing there is a bug in the COMPILER's handling of the
interpreter's own source, not in the interpreter. That is the same
three-paths-must-agree argument the corpus makes, one level up.

This is the loop to develop `_pyrun` inside. `embedded.py` is the checkpoint.

WHAT IT DOES NOT MEASURE, and the difference matters when reading a pass: the
bundled modules run here on CPython's object model, so a case can pass because
CPython's `list` behaves rather than because anything asmpython built does.
Only `embedded.py` closes that gap.
"""
from __future__ import annotations

import io
import contextlib
import sys
import traceback
from pathlib import Path

_BUNDLED = (Path(__file__).resolve().parents[2] / "src" / "asmpython"
            / "frontends" / "python" / "bundled")


def _load():
    """`_pyrun`, imported with the bundled directory on the path only for as
    long as the import takes -- it holds an `io.py`, a `typing.py` and a dozen
    more names the standard library also has, and leaving it on `sys.path`
    would shadow them for everything that runs afterwards."""
    sys.path.insert(0, str(_BUNDLED))
    try:
        import _pyrun
        return _pyrun
    finally:
        if sys.path and sys.path[0] == str(_BUNDLED):
            del sys.path[0]


_pyrun = _load()


def run(case_path: str, timeout: int):
    """-> (stdout, stderr, returncode); returncode None == refused to run.

    `timeout` is accepted and not enforced. Honouring it would mean a
    subprocess per case, which is the cost this shim exists to avoid -- and a
    tree walk that does not terminate is a bug worth hanging on rather than
    one worth scoring as a timeout. Run this shim in the foreground.
    """
    source = Path(case_path).read_text(encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            # A FRESH NAMESPACE, which is what a module scope is. `__name__` is
            # set because a case may write the `if __name__ == "__main__"`
            # guard, and without it the guarded half would silently not run.
            _pyrun.exec(source, {"__name__": "__main__"})
    except SyntaxError as exc:
        # THE INTERPRETER REFUSED THE PROGRAM, which is a different answer from
        # running it wrongly: the harness renders returncode None as REFUSED,
        # and for a subset implementation that distinction is most of the
        # signal. `_pyvalidate` raising here means it judged the case
        # ill-formed -- and every case is well-formed, so it is always a bug.
        return out.getvalue(), "".join(
            traceback.format_exception_only(type(exc), exc)), None
    except BaseException as exc:
        # An exception the case did not catch. Reported as a non-zero exit with
        # the traceback on stderr, which is what CPython does and what the
        # `cpython` shim therefore records.
        return (out.getvalue(),
                err.getvalue() + "".join(
                    traceback.format_exception(type(exc), exc,
                                               exc.__traceback__)),
                1)
    return out.getvalue(), err.getvalue(), 0
