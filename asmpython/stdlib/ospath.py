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
import sys


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
    """Read the whole file as text, or "" if it can't be opened. Uses
    fseek/ftell/fread for O(n) memory rather than char-at-a-time O(n²)."""
    f: str = os.fopen(path, "r")
    if f == 0:
        return ""
    os.fseek(f, 0, 2)
    n: int = os.ftell(f)
    os.fseek(f, 0, 0)
    if n <= 0:
        os.fclose(f)
        return ""
    buf: str = " " * (n + 1)
    nread: int = os.fread(buf, 1, n, f)
    os.fclose(f)
    if nread <= 0:
        return ""
    return buf[0:nread]


def getcwd() -> str:
    """Return current working directory path."""
    buf: str = "                                                                "
    result: str = os._getcwd(buf, 64)
    if result == "":
        return "."
    return result


def _stat_buf(p: str) -> list[int]:
    """Run `os._stat(p)` into a fresh word buffer; [] if `p` doesn't exist."""
    buf: list[int] = []
    i: int = 0
    while i < os._ST_BUF_WORDS:
        buf.append(0)
        i = i + 1
    if os._stat(p, buf) != 0:
        return []
    return buf


def _st_mode(buf: list[int]) -> int:
    """Extract the `st_mode` field (file-type + permission bits) from a
    buffer filled by `_stat_buf`."""
    word: int = buf[os._ST_MODE_WORD]
    if sys.platform == "win32":
        # MinGW `struct _stat64`: st_mode is the high 16 bits of word 0.
        return (word >> 48) & 0xFFFF
    # glibc `struct stat`: st_mode is the low 32 bits of word 3.
    return word & 0xFFFFFFFF


def isdir(p: str) -> int:
    """1 if p is a directory, else 0."""
    buf: list[int] = _stat_buf(p)
    if len(buf) == 0:
        return 0
    return int((_st_mode(buf) & os.S_IFMT) == os.S_IFDIR)


def isfile(p: str) -> int:
    """1 if p is a regular file (exists and is not a directory), else 0."""
    buf: list[int] = _stat_buf(p)
    if len(buf) == 0:
        return 0
    return int((_st_mode(buf) & os.S_IFMT) == os.S_IFREG)


def splitext(p: str) -> list:
    """Split path into (root, ext) where ext starts with '.'."""
    dot: int = -1
    i: int = 0
    n: int = len(p)
    while i < n:
        if p[i] == ".":
            dot = i
        i = i + 1
    if dot < 0:
        return [p, ""]
    return [p[0:dot], p[dot:]]


def split(p: str) -> list:
    """Split path into (head, tail) like dirname/basename."""
    return [dirname(p), basename(p)]


def abspath(p: str) -> str:
    """Return absolute path (best-effort; prepends cwd for relative paths)."""
    if len(p) == 0:
        return getcwd()
    first: str = p[0:1]
    if first == "/" or first == "\\":
        return p
    if len(p) >= 2 and p[1:2] == ":":
        return p
    cwd: str = getcwd()
    return join(cwd, p)


def normpath(p: str) -> str:
    """Normalize path by collapsing redundant separators (basic)."""
    result: str = ""
    i: int = 0
    n: int = len(p)
    prev_sep: int = 0
    while i < n:
        ch: str = p[i:i + 1]
        if ch == "/" or ch == "\\":
            if not prev_sep:
                result = result + "/"
            prev_sep = 1
        else:
            result = result + ch
            prev_sep = 0
        i = i + 1
    return result


# Under CPython, swap in native implementations of the I/O helpers (the FFI
# names above don't exist there). Under asmpython this import is skipped: the
# host module is deliberately outside the compilable subset, and the loader
# treats unparseable modules as opaque.
try:
    from asmpython.stdlib import _ospath_host
except ImportError:
    pass
