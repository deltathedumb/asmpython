"""shutil module: high-level file operations.

Uses os for file system access. File copy uses the os.fopen/fgetc/fputc/fclose
C-level file I/O. Directory operations use os.mkdir/os.rmdir/os.rename.
"""
from __future__ import annotations

import os
import os.path


def _copy_file_content(src: str, dst: str) -> None:
    """Copy bytes from src file to dst file using CRT I/O."""
    fsrc: str = os.fopen(src, "rb")
    fdst: str = os.fopen(dst, "wb")
    while os.feof(fsrc) == 0:
        ch: int = os.fgetc(fsrc)
        if ch == -1:
            break
        os.fputc(ch, fdst)
    os.fclose(fsrc)
    os.fclose(fdst)


def copyfile(src: str, dst: str) -> str:
    """Copy file content from src to dst. Returns dst."""
    _copy_file_content(src, dst)
    return dst


def copy(src: str, dst: str) -> str:
    """Copy file src to file or directory dst. Returns dst path."""
    if os.path.exists(dst + os.sep + os.path.basename(src)):
        dst = os.path.join(dst, os.path.basename(src))
    _copy_file_content(src, dst)
    return dst


def move(src: str, dst: str) -> str:
    """Rename/move src to dst. Returns dst."""
    os.rename(src, dst)
    return dst


def rmtree(path: str, ignore_errors: int = 0) -> None:
    """Remove directory (stub: only removes empty directories)."""
    os.rmdir(path)


def make_archive(base_name: str, format: str, root_dir: str = "",
                 base_dir: str = "") -> str:
    """Create an archive (stub, returns archive name)."""
    return base_name + "." + format


def get_terminal_size(fallback: list = []) -> list:
    """Return [columns, lines]. Returns fallback or [80, 24]."""
    if len(fallback) == 2:
        return fallback
    result: list = []
    result.append(80)
    result.append(24)
    return result


def disk_usage(path: str) -> list:
    """Return [total, used, free] in bytes (stub, always returns zeros)."""
    result: list = []
    result.append(0)
    result.append(0)
    result.append(0)
    return result


def which(name: str, mode: int = 1, path: str = "") -> str:
    """Return full path to executable found by searching *path* (or $PATH),
    trying platform-appropriate executable extensions on Windows. Returns
    "" if not found."""
    import sys
    import ospath
    if ospath.isfile(name):
        return name
    search_path: str = path if path else os.environ.get("PATH", "")
    exts: list[str] = [""]
    if sys.platform == "win32":
        exts = ["", ".exe", ".bat", ".cmd"]
    for d in search_path.split(os.pathsep):
        if d == "":
            continue
        for ext in exts:
            cand = d + "/" + name + ext
            if ospath.isfile(cand):
                return cand
    return ""


def copystat(src: str, dst: str) -> None:
    """Copy permission bits and timestamps from src to dst (stub: no-op)."""
    pass


def copy2(src: str, dst: str) -> str:
    """Copy data and metadata from src to dst. Returns dst."""
    copy(src, dst)
    copystat(src, dst)
    return dst


def copytree(src: str, dst: str, symlinks: int = 0,
             ignore: int = 0, dirs_exist_ok: int = 0) -> str:
    """Recursively copy a directory tree from src to dst.

    Stub: copies directory structure but not contents (os.listdir not available).
    """
    import os
    os.mkdir(dst, 511)
    return dst


def copymode(src: str, dst: str) -> None:
    """Copy permission bits from src to dst (stub: no-op)."""
    pass


def ignore_patterns(*patterns) -> int:
    """Factory for ignore argument to copytree (stub: returns 0)."""
    return 0


class ReadError(Exception):
    """Raised on unreadable archive."""
    pass


class RegistryError(Exception):
    """Raised when a registry operation fails."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg


class Error(Exception):
    """Base class for shutil errors."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return "shutil.Error: " + self.msg


class SameFileError(Error):
    """Raised when src and dst are the same file."""
    pass


class ExecError(Error):
    """Raised when an external command failed."""
    pass
