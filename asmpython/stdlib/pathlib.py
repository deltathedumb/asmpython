"""pathlib module: a Path class for filesystem paths.

A Path wraps a plain string (`self.p`); "/" works as the separator for both
the Windows and POSIX C runtimes, so no platform-specific separator handling
is needed for the FFI calls themselves. String-only methods (`.name`,
`.suffix`, `.parent`, ...) are pure text manipulation; filesystem methods
(`.exists`, `.mkdir`, `.read_text`, ...) go through the `os` FFI module.

This is bundled as asmpython *source* (unlike the FFI-only stdlib modules
such as `os`/`sys`): `Path` is a real class, merged into the program like a
project module by the whole-program loader, so `from pathlib import Path`
gets full constructor/field/method checking.
"""
from __future__ import annotations

import os
import ospath


class StatResult:
    """Result of `Path.stat()`. Only `st_mtime` is populated today (the
    field staleness-checking code in `_runtime/build.py` is the only
    consumer); other `os.stat_result` fields can be added the same way
    if/when something needs them."""

    def __init__(self, st_mtime: int) -> None:
        self.st_mtime = st_mtime


class Path:
    def __init__(self, p: str = "") -> None:
        self.p = p

    def __str__(self) -> str:
        return self.p

    def __repr__(self) -> str:
        return "Path('" + self.p + "')"

    def _join(self, other: str) -> Path:
        a = self.p
        if a == "":
            return Path(other)
        last = a[len(a) - 1:]
        if last == "/" or last == "\\":
            return Path(a + other)
        return Path(a + "/" + other)

    def __truediv__(self, other: str) -> Path:
        return self._join(other)

    def joinpath(self, other: str) -> Path:
        return self._join(other)

    def as_posix(self) -> str:
        return self.p.replace("\\", "/")

    def is_absolute(self) -> int:
        p = self.p
        if p == "":
            return 0
        if p[0:1] == "/" or p[0:1] == "\\":
            return 1
        if len(p) >= 2 and p[1:2] == ":":
            return 1
        return 0

    @property
    def name(self) -> str:
        p = self.p
        start = 0
        i = 0
        n = len(p)
        while i < n:
            ch = p[i]
            if ch == "/" or ch == "\\":
                start = i + 1
            i = i + 1
        return p[start:]

    @property
    def parent(self) -> Path:
        p = self.p
        cut = -1
        i = 0
        n = len(p)
        while i < n:
            ch = p[i]
            if ch == "/" or ch == "\\":
                cut = i
            i = i + 1
        if cut < 0:
            return Path("")
        if cut == 0:
            return Path(p[0:1])
        return Path(p[0:cut])

    @property
    def suffix(self) -> str:
        nm = self.name
        cut = -1
        i = 0
        n = len(nm)
        while i < n:
            if nm[i] == ".":
                cut = i
            i = i + 1
        if cut <= 0:
            return ""
        return nm[cut:]

    @property
    def stem(self) -> str:
        nm = self.name
        cut = -1
        i = 0
        n = len(nm)
        while i < n:
            if nm[i] == ".":
                cut = i
            i = i + 1
        if cut <= 0:
            return nm
        return nm[0:cut]

    def with_suffix(self, suffix: str) -> Path:
        nm = self.name
        cut = -1
        i = 0
        n = len(nm)
        while i < n:
            if nm[i] == ".":
                cut = i
            i = i + 1
        if cut <= 0:
            base = nm
        else:
            base = nm[0:cut]
        par = self.parent
        if par.p == "":
            return Path(base + suffix)
        return par._join(base + suffix)

    def with_name(self, name: str) -> Path:
        par = self.parent
        if par.p == "":
            return Path(name)
        return par._join(name)

    # --- filesystem queries / mutation --------------------------------------

    def exists(self) -> int:
        return int(os._access(self.p, 0) == 0)

    def is_dir(self) -> int:
        return ospath.isdir(self.p)

    def is_file(self) -> int:
        return ospath.isfile(self.p)

    def mkdir(self, mode: int = 511, parents: int = 0, exist_ok: int = 0) -> int:
        if parents == 1:
            par = self.parent
            if par.p != "" and par.exists() == 0:
                par.mkdir(mode, 1, 1)
        rc = os.mkdir(self.p, mode)
        if rc != 0 and exist_ok == 1 and self.is_dir() == 1:
            return 0
        return rc

    def unlink(self) -> int:
        return os.remove(self.p)

    def rmdir(self) -> int:
        return os.rmdir(self.p)

    def read_text(self, encoding: str = "utf-8") -> str:
        f = os.fopen(self.p, "r")
        if f == 0:
            return ""
        out = ""
        c = os.fgetc(f)
        while c != -1:
            out = out + chr(c)
            c = os.fgetc(f)
        os.fclose(f)
        return out

    def read_bytes(self) -> list[int]:
        f = os.fopen(self.p, "rb")
        if f == 0:
            return []
        out: list[int] = []
        c = os.fgetc(f)
        while c != -1:
            out.append(c)
            c = os.fgetc(f)
        os.fclose(f)
        return out

    def write_bytes(self, data: list[int]) -> int:
        f = os.fopen(self.p, "wb")
        if f == 0:
            return 0
        n = 0
        for b in data:
            os.fputc(b, f)
            n = n + 1
        os.fclose(f)
        return n

    def write_text(self, data: str, encoding: str = "utf-8") -> int:
        f = os.fopen(self.p, "w")
        if f == 0:
            return 0
        os.fputs(data, f)
        os.fclose(f)
        return len(data)

    def relative_to(self, other: Path) -> Path:
        a = self.as_posix()
        b = other.as_posix()
        if len(b) > 1 and b[len(b) - 1:] == "/":
            b = b[0:len(b) - 1]
        if a == b:
            return Path(".")
        if b == "":
            prefix = ""
        elif b[len(b) - 1:] == "/":
            prefix = b
        else:
            prefix = b + "/"
        if len(a) <= len(prefix) or a[0:len(prefix)] != prefix:
            raise "'" + self.p + "' is not in the subpath of '" + other.p + "'"
        return Path(a[len(prefix):])

    def stat(self) -> StatResult:
        buf: list[int] = []
        i = 0
        while i < os._ST_BUF_WORDS:
            buf.append(0)
            i = i + 1
        os._stat(self.p, buf)
        return StatResult(buf[os._ST_MTIME_WORD])

    def resolve(self) -> Path:
        if self.is_absolute():
            return Path(self.p)
        # getcwd(buf, size) needs a writable caller buffer; build one on the
        # heap via concatenation (string literals live in read-only memory).
        buf = ""
        i = 0
        while i < 512:
            buf = buf + " "
            i = i + 1
        cwd = os._getcwd(buf, 512)
        if cwd == 0:
            return Path(self.p)
        if self.p == "":
            return Path(cwd)
        return Path(cwd)._join(self.p)


def makedirs(path: str, mode: int = 511, exist_ok: int = 0) -> None:
    """Create directory and all intermediate-level directories.

    Equivalent to `mkdir -p`. Uses the os.mkdir FFI call.
    """
    parts: list[str] = []
    p: str = path
    while len(p) > 0:
        d: str = ""
        i: int = 0
        n: int = len(p)
        last_sep: int = -1
        while i < n:
            ch: str = p[i:i + 1]
            if ch == "/" or ch == "\\":
                last_sep = i
            i = i + 1
        if last_sep < 0:
            parts.append(p)
            p = ""
        else:
            parts.append(p)
            p = ""
    i2: int = 0
    while i2 < len(parts):
        seg: str = parts[i2]
        ret: int = os.mkdir(seg, mode)
        i2 = i2 + 1


def getcwd() -> str:
    """Return current working directory path."""
    buf: str = ""
    i: int = 0
    while i < 512:
        buf = buf + " "
        i = i + 1
    result: str = os._getcwd(buf, 512)
    if result == "":
        return "."
    return result


class PurePath:
    """Pure path base — no filesystem I/O, string manipulation only."""

    def __init__(self, p: str = "") -> None:
        self._p: str = p

    def __str__(self) -> str:
        return self._p

    def __truediv__(self, other: str) -> PurePath:
        if self._p == "" or self._p == ".":
            return PurePath(other)
        return PurePath(self._p + "/" + other)

    def as_posix(self) -> str:
        result: str = ""
        for ch in self._p:
            if ch == "\\":
                result = result + "/"
            else:
                result = result + ch
        return result

    @property
    def name(self) -> str:
        p: str = self.as_posix()
        i: int = len(p) - 1
        while i >= 0 and p[i] != "/":
            i = i - 1
        return p[i + 1:]

    @property
    def suffix(self) -> str:
        n: str = self.name
        i: int = len(n) - 1
        while i >= 0 and n[i] != ".":
            i = i - 1
        if i <= 0:
            return ""
        return n[i:]

    @property
    def stem(self) -> str:
        n: str = self.name
        suf: str = self.suffix
        return n[:len(n) - len(suf)]

    @property
    def parent(self) -> PurePath:
        p: str = self.as_posix()
        i: int = len(p) - 1
        while i > 0 and p[i] == "/":
            i = i - 1
        while i >= 0 and p[i] != "/":
            i = i - 1
        if i < 0:
            return PurePath(".")
        return PurePath(p[:i] if i > 0 else "/")


class PurePosixPath(PurePath):
    """PurePath variant using POSIX (forward-slash) paths."""
    pass


class PureWindowsPath(PurePath):
    """PurePath variant using Windows (backslash) paths."""

    def as_posix(self) -> str:
        result: str = ""
        for ch in self._p:
            if ch == "\\":
                result = result + "/"
            else:
                result = result + ch
        return result
