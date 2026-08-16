"""Shim for the EMBEDDED interpreter: run the case inside a compiled binary.

The subject is not the compiler's native path. It is `_pylex`, `_pyparse`,
`_pyvalidate` and `_pyrun` -- the Python-in-Python compiler and tree-walker
that `bundled.py` splices into any program naming `compile`, `eval` or `exec`.
A case is wrapped in a three-line driver that `exec`s it, that driver is
compiled to a native binary, and the binary runs the case. So the case is
parsed and executed by asmpython's own interpreter, which is itself compiled by
asmpython.

WHY THIS IS WORTH MEASURING SEPARATELY. The embedded interpreter was written to
answer nineteen conformance cases that call `compile()`, and what it is
measured against is those nineteen plus 87 probes read out of them
(`tests/asmpython/unit/test_bundled_compile.py`). That says it agrees with
CPython about which programs are WELL-FORMED. It says almost nothing about
whether it RUNS them correctly -- `_pyrun` is 537 lines of tree walk against a
language the rest of this suite spends 1668 cases on. Pointing the same oracle
at it is the cheapest honest answer to "how much Python does it actually do".

THE CASE IS NOT REWRITTEN. Its source goes in as a string literal, verbatim,
and `exec` runs it in a fresh namespace -- which is what a module scope is.
`cases/` stays the oracle here exactly as it is for every other shim.

IT IS EXPENSIVE, and that is inherent rather than fixable: the splice is ~3,300
lines of bundled Python on top of the case, so every compile is slow (~22s was
measured for `_pycompile` alone). USE `--merge`. A merged batch is one driver
holding many cases, so the splice is paid once for the batch instead of once
per case -- which takes a full sweep from thousands of compiles to dozens. The
shim needs no special support for that: it wraps whatever file the harness
hands it, merged or not.
"""
from __future__ import annotations

import atexit
import itertools
import shutil
import tempfile
import threading
from pathlib import Path

# The native shim already solves everything about building and running a case
# on this machine -- the per-thread artifact names, the Windows quirks, the
# warmup lock, the UTF-8 decode. This shim differs from it in ONE respect (what
# source gets compiled), so it borrows the rest rather than copying it. A
# second copy of that logic would drift, and the interesting failures here are
# the interpreter's, not the build harness's.
import importlib.util as _il

_spec = _il.spec_from_file_location(
    "_shim_native_for_embedded", Path(__file__).with_name("asmpython.py"))
_native = _il.module_from_spec(_spec)
_spec.loader.exec_module(_native)


#: The driver. `exec` is what pulls the whole embedded compiler in -- no import
#: brings a builtin, so naming it is what makes `bundled.py` splice it.
#:
#: A FRESH DICT for globals, because that is what a module scope is: the case
#: must not see the driver's own names, and `exec(src)` with no namespace would
#: hand it exactly those.
_DRIVER = "_embedded_source = %r\nexec(_embedded_source, {})\n"

#: NOT BESIDE THE CASE. `cases/` is the oracle and `discover()` walks it for
#: `*.py`, so a driver written next to its case would be picked up as a case by
#: any run that started while this one was in flight -- a suite that grew
#: 1,679 nonsense entries from being measured twice at once.
_WORK = Path(tempfile.mkdtemp(prefix="embedded_shim_"))
_SERIAL = itertools.count()
_SERIAL_LOCK = threading.Lock()

atexit.register(lambda: shutil.rmtree(_WORK, ignore_errors=True))


def run(case_path: str, timeout: int):
    """-> (stdout, stderr, returncode); returncode None == refused to compile.

    A refusal here means the DRIVER did not compile, which is one of two very
    different things: the splice failed, or the case's source could not be made
    into a literal. Neither is the interpreter getting an answer wrong, and the
    harness reports REFUSED rather than FAIL for exactly that reason.
    """
    source = Path(case_path).read_text(encoding="utf-8")
    with _SERIAL_LOCK:
        n = next(_SERIAL)
    # The stem is kept so a failure names the case, and the serial makes it
    # unique -- the harness runs cases on THREADS of one process and a case's
    # stem is only its file name, which the cross-product directories reuse
    # by design.
    driver = _WORK / ("%s_%d.py" % (Path(case_path).stem, n))
    driver.write_text(_DRIVER % source, encoding="utf-8")
    try:
        return _native.run(str(driver), timeout)
    finally:
        try:
            driver.unlink()
        except OSError:
            pass
