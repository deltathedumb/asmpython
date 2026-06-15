"""warnings module: issue warning messages.

In asmpython, warnings print to stdout with a "Warning: " prefix.
filterwarnings/simplefilter/resetwarnings are no-ops.
"""
from __future__ import annotations


def warn(message: str, category: int = 0, stacklevel: int = 1) -> None:
    """Issue a warning (prints to stdout)."""
    print("Warning: " + message)


def warn_explicit(message: str, category: int, filename: str,
                  lineno: int) -> None:
    print("Warning: " + message + " (" + filename + ":" + str(lineno) + ")")


def filterwarnings(action: str, message: str = "", category: int = 0,
                   module: str = "", lineno: int = 0,
                   append: int = 0) -> None:
    pass


def simplefilter(action: str, category: int = 0, lineno: int = 0,
                 append: int = 0) -> None:
    pass


def resetwarnings() -> None:
    pass


class DeprecationWarning:
    pass


class UserWarning:
    pass


class RuntimeWarning:
    pass


class SyntaxWarning:
    pass


class ResourceWarning:
    pass


class FutureWarning:
    pass


class PendingDeprecationWarning:
    pass


class ImportWarning:
    pass


class UnicodeWarning:
    pass


class BytesWarning:
    pass


class catch_warnings:
    """Context manager that saves and restores warning filters."""

    def __init__(self, record: int = 0) -> None:
        self._record: int = record
        self._log: list = []

    def __enter__(self) -> catch_warnings:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        return 0


def formatwarning(message: str, category: str, filename: str,
                  lineno: int, line: str = "") -> str:
    """Format a warning for display."""
    return filename + ":" + str(lineno) + ": " + category + ": " + message + "\n"


def showwarning(message: str, category: str, filename: str,
                lineno: int, file: int = 0, line: str = "") -> None:
    """Write a warning to stdout."""
    print(formatwarning(message, category, filename, lineno, line))
