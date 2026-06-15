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
    """Return full path to executable. Returns name if it exists."""
    if os.path.exists(name):
        return name
    return ""


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
