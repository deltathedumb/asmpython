"""tempfile module: generate temporary files and directories.

Uses os.tmpnam-style naming based on a counter. Actual file creation
uses os.fopen.
"""
from __future__ import annotations

import os

_counter: int = 0


def _next_name(prefix: str, suffix: str) -> str:
    """Generate a unique temp file name."""
    global _counter
    _counter = _counter + 1
    return prefix + "tmp" + str(_counter) + suffix


def mkstemp(suffix: str = "", prefix: str = "tmp",
            dir: str = "", text: int = 0) -> list:
    """Create a temporary file. Returns [fd, name] where fd is a FILE* str."""
    name: str = _next_name(prefix, suffix)
    if len(dir) > 0:
        name = dir + "/" + name
    mode: str = "w" if text else "wb"
    fp: str = os.fopen(name, mode)
    return [fp, name]


def mktemp(suffix: str = "", prefix: str = "tmp", dir: str = "") -> str:
    """Return a unique temp file name without creating the file (matches
    real Python's deprecated-but-still-present mktemp — callers that need
    the file to actually exist should use mkstemp() instead)."""
    name: str = _next_name(prefix, suffix)
    use_dir: str = dir if len(dir) > 0 else gettempdir()
    return use_dir + "/" + name


def mkdtemp(suffix: str = "", prefix: str = "tmp", dir: str = "") -> str:
    """Create a temporary directory. Returns path."""
    name: str = _next_name(prefix, suffix)
    if len(dir) > 0:
        name = dir + "/" + name
    os.mkdir(name, 511)
    return name


def gettempdir() -> str:
    """Return the default temporary directory."""
    import sys
    if sys.platform == "win32":
        return "C:\\Windows\\Temp"
    return "/tmp"


def gettempprefix() -> str:
    """Return the default prefix for temporary files."""
    return "tmp"


def NamedTemporaryFile(mode: str = "w+b", suffix: str = "",
                       prefix: str = "tmp", dir: str = "",
                       delete: int = 1) -> int:
    """Return a file-like object for a temporary file (stub: returns 0)."""
    return 0


def TemporaryFile(mode: str = "w+b", suffix: str = "",
                  prefix: str = "tmp", dir: str = "") -> int:
    """Create a temporary file (stub: returns 0)."""
    return 0


def TemporaryDirectory(suffix: str = "", prefix: str = "tmp",
                       dir: str = "") -> str:
    """Create a temporary directory and return its path."""
    return mkdtemp(suffix, prefix, dir)


def SpooledTemporaryFile(max_size: int = 0, mode: str = "w+b",
                         suffix: str = "", prefix: str = "tmp") -> int:
    """Spooled temporary file (stub: returns 0)."""
    return 0
