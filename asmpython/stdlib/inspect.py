"""inspect module: inspect live objects.

In asmpython, runtime introspection of objects is not available (no
reflection). These stubs allow code importing inspect to compile
without errors. Most functions return safe default values.
"""
from __future__ import annotations


def isfunction(obj: int) -> int:
    """Return True if object is a Python function (stub, always False)."""
    return 0


def ismethod(obj: int) -> int:
    return 0


def isclass(obj: int) -> int:
    return 0


def ismodule(obj: int) -> int:
    return 0


def isbuiltin(obj: int) -> int:
    return 0


def iscoroutinefunction(obj: int) -> int:
    return 0


def isgeneratorfunction(obj: int) -> int:
    return 0


def isgenerator(obj: int) -> int:
    return 0


def iscoroutine(obj: int) -> int:
    return 0


def isframe(obj: int) -> int:
    return 0


def iscode(obj: int) -> int:
    return 0


def istraceback(obj: int) -> int:
    return 0


def isroutine(obj: int) -> int:
    return 0


def isabstract(cls: int) -> int:
    return 0


def getmembers(obj: int) -> list:
    """Return all attributes of an object (stub, empty list)."""
    return []


def getmembers_static(obj: int) -> list:
    return []


def getmodule(obj: int) -> str:
    """Return the module where obj was defined (stub)."""
    return ""


def getfile(obj: int) -> str:
    """Return the name of the file where obj was defined (stub)."""
    return "<unknown>"


def getsource(obj: int) -> str:
    """Return the source code of obj (stub)."""
    return ""


def getsourcelines(obj: int) -> list:
    """Return source lines (stub, empty)."""
    lines: list = []
    return lines


def getsourcefile(obj: int) -> str:
    """Return source file of obj (stub)."""
    return ""


def getdoc(obj: int) -> str:
    """Return the docstring of obj (stub)."""
    return ""


def getcomments(obj: int) -> str:
    """Return comment block above obj's source (stub)."""
    return ""


def signature(obj: int) -> int:
    """Return a Signature object (stub, returns 0)."""
    return 0


def currentframe() -> int:
    """Return the current stack frame (stub, returns 0)."""
    return 0


def stack(context: int = 1) -> list:
    """Return list of frame records for the caller's stack (stub, empty)."""
    return []


def trace(context: int = 1) -> list:
    """Return list of frame records for the current exception (stub)."""
    return []


def getframeinfo(frame: int, context: int = 1) -> list:
    """Get info about a frame (stub, returns empty list)."""
    return []


def getinnerframes(tb: int, context: int = 1) -> list:
    return []


def getouterframes(frame: int, context: int = 1) -> list:
    return []


def getargs(co: int) -> list:
    return []


def getargvalues(frame: int) -> list:
    return []


def formatargvalues(args: list, varargs: str, varkw: str,
                    locals_dict: int) -> str:
    return "()"


class Parameter:
    """Represents a function parameter (stub)."""
    POSITIONAL_ONLY: int = 0
    POSITIONAL_OR_KEYWORD: int = 1
    VAR_POSITIONAL: int = 2
    KEYWORD_ONLY: int = 3
    VAR_KEYWORD: int = 4
    empty: int = 0

    def __init__(self, name: str, kind: int = 1) -> None:
        self.name: str = name
        self.kind: int = kind
        self.default: int = 0
        self.annotation: int = 0


class Signature:
    """Represents a function signature (stub)."""

    def __init__(self) -> None:
        self.parameters: list = []
        self.return_annotation: int = 0
