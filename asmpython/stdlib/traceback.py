"""traceback module: print and extract stack traces.

In asmpython, tracebacks are minimal stubs since native stack frames
are not inspectable at runtime. The module allows code that imports
traceback to compile and run without errors.
"""
from __future__ import annotations


def print_exc(limit: int = -1, file: int = 0, chain: int = 1) -> None:
    """Print the last exception traceback (stub, prints nothing)."""
    pass


def print_tb(tb: int, limit: int = -1, file: int = 0) -> None:
    """Print up to limit stack trace entries (stub)."""
    pass


def print_exception(etype: int, value: int, tb: int, limit: int = -1,
                    file: int = 0, chain: int = 1) -> None:
    """Print exception information (stub)."""
    pass


def print_last(limit: int = -1, file: int = 0, chain: int = 1) -> None:
    """Print the last unhandled exception (stub)."""
    pass


def print_stack(f: int = 0, limit: int = -1, file: int = 0) -> None:
    """Print a stack trace from the current frame (stub)."""
    pass


def extract_tb(tb: int, limit: int = -1) -> list:
    """Return a StackSummary (list) from traceback (stub, empty)."""
    return []


def extract_stack(f: int = 0, limit: int = -1) -> list:
    """Return a StackSummary from the current stack (stub, empty)."""
    return []


def format_exc(limit: int = -1, chain: int = 1) -> str:
    """Format the last exception as a string (stub)."""
    return ""


def format_tb(tb: int, limit: int = -1) -> list:
    """Format a traceback (stub, empty list)."""
    return []


def format_exception(etype: int, value: int, tb: int, limit: int = -1,
                     chain: int = 1) -> list:
    """Format an exception (stub, empty list)."""
    return []


def format_stack(f: int = 0, limit: int = -1) -> list:
    """Format the current stack (stub, empty list)."""
    return []


def format_exception_only(etype: int, value: int) -> list:
    """Format the exception part only (stub, empty list)."""
    return []


def clear_frames(tb: int) -> None:
    """Clear local variables in all frames of a traceback (stub)."""
    pass


def walk_tb(tb: int) -> list:
    """Walk a traceback (stub, empty)."""
    return []


def walk_stack(f: int = 0) -> list:
    """Walk a stack (stub, empty)."""
    return []


class StackSummary:
    """A list of FrameSummary objects (stub)."""

    def __init__(self) -> None:
        self._frames: list = []

    def __len__(self) -> int:
        return len(self._frames)

    def format(self) -> list:
        return []


class FrameSummary:
    """A single frame in a traceback (stub)."""

    def __init__(self, filename: str, lineno: int, name: str) -> None:
        self.filename: str = filename
        self.lineno: int = lineno
        self.name: str = name
        self.line: str = ""

    def __str__(self) -> str:
        return (self.filename + ":" + str(self.lineno) + " in " + self.name)


class TracebackException:
    """Lightweight exception information (stub)."""

    def __init__(self, exc_type: int, exc_value: int,
                 exc_tb: int, limit: int = -1) -> None:
        self.exc_type: int = exc_type
        self._str: str = str(exc_value)

    def format(self, chain: int = 1) -> list:
        return [self._str]

    def format_exception_only(self) -> list:
        return [self._str]
