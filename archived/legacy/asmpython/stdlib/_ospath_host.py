"""CPython-side backing for `ospath`'s I/O helpers.

`ospath.py` is written against the asmpython `os` FFI so it self-compiles; at
compile time, though, the compiler runs it under plain CPython where those FFI
names don't exist. Importing this module (ospath does so at its bottom)
replaces `ospath.exists` / `ospath.read_file` with native implementations.

This file is intentionally OUTSIDE asmpython's compilable subset (lambdas,
`with`, `open`) — the whole-program loader skips modules it can't parse, which
is exactly what keeps these CPython bodies out of a self-hosted build.
"""
from __future__ import annotations

import os as _py_os

from asmpython.stdlib import ospath as _ospath

_ospath.exists = lambda p: 1 if _py_os.path.exists(p) else 0


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


_ospath.read_file = _read_file
