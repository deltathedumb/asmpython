"""Small, self-contained path / file helpers for the compiler's own use.

Written entirely in asmpython's compilable subset: string operations plus the
`os` file-I/O FFI (`fopen`/`fgetc`/`fclose`/`_access`), so this module compiles
under asmpython itself (needed for self-hosting the assembly-package loader).

Dual-world note: the same functions must also *run* under plain CPython, where
the compiler executes them at compile time and the `os` FFI names don't exist
(a bare `import os` there is CPython's stdlib os). The `_ospath_host` import at
the bottom handles that: under CPython it replaces `exists`/`read_file` with
native implementations; under asmpython that module is intentionally outside
the compilable subset, so the whole-program loader skips it and the FFI bodies
below are the ones that compile.

A separator-agnostic "/" is used: both Windows and POSIX accept forward slashes
in C stdio paths.
"""
from __future__ import annotations

import os


def join(a: str, b: str) -> str:
    """Join two path components with "/". Empty `a` -> `b`; an `a` that already
    ends in a separator gets no extra one."""
    if a == "":
        return b
    last = a[len(a) - 1:]
    if last == "/" or last == "\\":
        return a + b
    return a + "/" + b


def basename(p: str) -> str:
    """The final component of `p` (after the last "/" or "\\")."""
    start = 0
    i = 0
    n = len(p)
    while i < n:
        ch = p[i]
        if ch == "/" or ch == "\\":
            start = i + 1
        i = i + 1
    return p[start:]


def dirname(p: str) -> str:
    """Everything before the final component (no trailing separator), or "" if
    there is no separator."""
    cut = -1
    i = 0
    n = len(p)
    while i < n:
        ch = p[i]
        if ch == "/" or ch == "\\":
            cut = i
        i = i + 1
    if cut < 0:
        return ""
    return p[0:cut]


def exists(p: str) -> int:
    """1 if `p` exists (access mode 0 = F_OK), else 0."""
    if os._access(p, 0) == 0:
        return 1
    return 0


def read_file(path: str) -> str:
    """Read the whole file as text, or "" if it can't be opened. Uses the
    char-at-a-time `fgetc` FFI so no caller buffer is needed."""
    f = os.fopen(path, "r")
    if f == 0:
        return ""
    out = ""
    c = os.fgetc(f)
    while c != -1:
        out = out + chr(c)
        c = os.fgetc(f)
    os.fclose(f)
    return out


# Under CPython, swap in native implementations of the I/O helpers (the FFI
# names above don't exist there). Under asmpython this import is skipped: the
# host module is deliberately outside the compilable subset, and the loader
# treats unparseable modules as opaque.
try:
    from asmpython.stdlib import _ospath_host
except ImportError:
    pass
