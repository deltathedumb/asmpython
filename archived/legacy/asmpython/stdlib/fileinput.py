"""fileinput: iterate over lines from multiple files or stdin.

Usage::

    import fileinput

    for line in fileinput.input(["file1.txt", "file2.txt"]):
        print(fileinput.filename(), fileinput.filelineno(), line)

    # Or as a context manager:
    with fileinput.input(files=["data.txt"]) as f:
        for line in f:
            process(line)
"""
from __future__ import annotations

import os
import sys

# Module-level current state
_state: object = None


class FileInput:
    """Iterate over lines from a sequence of files (or stdin)."""

    def __init__(self, files: list = [], inplace: int = 0,
                 backup: str = "", mode: str = "r",
                 openhook: int = 0) -> None:
        self._files: list = files
        self._inplace: int = inplace
        self._backup: str = backup
        self._mode: str = mode
        self._file_idx: int = 0
        self._lineno: int = 0
        self._filelineno: int = 0
        self._filename: str = ""
        self._lines: list = []
        self._line_idx: int = 0
        self._eof: int = 0
        self._load_next_file()

    def _load_next_file(self) -> None:
        if self._file_idx >= len(self._files):
            self._eof = 1
            return
        fname: str = self._files[self._file_idx]
        self._filename = fname
        self._file_idx = self._file_idx + 1
        self._filelineno = 0
        self._line_idx = 0
        if fname == "-":
            self._lines = []
        else:
            if not os.path.exists(fname):
                self._lines = []
            else:
                f = open(fname, "r")
                content: str = f.read()
                f.close()
                if content:
                    self._lines = content.split("\n")
                    # Restore newlines stripped by split
                    repaired: list = []
                    i: int = 0
                    while i < len(self._lines) - 1:
                        repaired.append(self._lines[i] + "\n")
                        i = i + 1
                    # Last element: only add newline if content ended with one
                    if self._lines:
                        last: str = self._lines[len(self._lines) - 1]
                        if last:
                            repaired.append(last)
                    self._lines = repaired
                else:
                    self._lines = []

    def __enter__(self) -> FileInput:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> int:
        self.close()
        return 0

    def __iter__(self) -> FileInput:
        return self

    def __next__(self) -> str:
        if self._eof:
            raise StopIteration()
        while self._line_idx >= len(self._lines):
            self._load_next_file()
            if self._eof:
                raise StopIteration()
        line: str = self._lines[self._line_idx]
        self._line_idx = self._line_idx + 1
        self._lineno = self._lineno + 1
        self._filelineno = self._filelineno + 1
        return line

    def filename(self) -> str:
        return self._filename

    def fileno(self) -> int:
        return -1

    def filelineno(self) -> int:
        return self._filelineno

    def lineno(self) -> int:
        return self._lineno

    def isfirstline(self) -> int:
        return self._filelineno == 1

    def isstdin(self) -> int:
        return self._filename == "-"

    def nextfile(self) -> None:
        """Skip remaining lines of current file."""
        self._line_idx = len(self._lines)

    def close(self) -> None:
        self._eof = 1
        self._lines = []


# ---------------------------------------------------------------------------
# Module-level convenience API
# ---------------------------------------------------------------------------

def input(files: list = [], inplace: int = 0, backup: str = "",
          mode: str = "r", openhook: int = 0) -> FileInput:
    """Return a FileInput object for iterating over *files* line by line."""
    global _state
    _state = FileInput(files, inplace, backup, mode, openhook)
    return _state


def filename() -> str:
    """Return the name of the file currently being read."""
    global _state
    if _state is None:
        return ""
    fi: FileInput = _state
    return fi.filename()


def fileno() -> int:
    """Return the file descriptor of the current file."""
    return -1


def filelineno() -> int:
    """Return the line number in the current file."""
    global _state
    if _state is None:
        return 0
    fi: FileInput = _state
    return fi.filelineno()


def lineno() -> int:
    """Return the cumulative line number across all files."""
    global _state
    if _state is None:
        return 0
    fi: FileInput = _state
    return fi.lineno()


def isfirstline() -> int:
    """Return 1 if the current line is the first line of its file."""
    global _state
    if _state is None:
        return 0
    fi: FileInput = _state
    return fi.isfirstline()


def isstdin() -> int:
    """Return 1 if the current file is stdin."""
    global _state
    if _state is None:
        return 0
    fi: FileInput = _state
    return fi.isstdin()


def nextfile() -> None:
    """Skip remaining lines of current file and advance to the next."""
    global _state
    if _state is not None:
        fi: FileInput = _state
        fi.nextfile()


def close() -> None:
    """Close the sequence."""
    global _state
    if _state is not None:
        fi: FileInput = _state
        fi.close()
        _state = None


def hook_compressed(filename: str, mode: str) -> object:
    """Open hook for compressed files (stub; returns regular open)."""
    return open(filename, mode)


def hook_encoded(encoding: str, errors: str = "strict") -> int:
    """Return an open hook that decodes files with the given encoding (stub)."""
    return open
