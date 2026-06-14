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
