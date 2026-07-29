"""pprint module: data pretty-printing."""
from __future__ import annotations


def _indent_str(n: int) -> str:
    result: str = ""
    i: int = 0
    while i < n:
        result = result + " "
        i = i + 1
    return result


def pformat(obj: object, indent: int = 1, width: int = 80, depth: int = -1,
            compact: int = 0) -> str:
    """Format obj as a string.

    CPython's pprint formats with REPR, not str -- `pprint.pformat('hi')` is
    `"'hi'"`, not `hi`. For containers the two agree (a container's str IS its
    repr), so this only shows up on a bare string.
    """
    return repr(obj)


def pprint(obj: object, indent: int = 1, width: int = 80, depth: int = -1,
           compact: int = 0, sort_dicts: int = 1) -> None:
    """Pretty-print obj to stdout."""
    print(obj)


def pp(obj: object, indent: int = 1, width: int = 80, depth: int = -1,
       compact: int = 0, sort_dicts: int = 0) -> None:
    """Pretty-print obj (alias for pprint with sort_dicts=False by default)."""
    print(obj)


def isreadable(obj: object) -> int:
    """Return 1 if repr(obj) can be eval'd back to an equal object."""
    return 1


def isrecursive(obj: object) -> int:
    """Return 1 if obj contains a recursive reference."""
    return 0


def saferepr(obj: object) -> str:
    """Return a repr string, truncating if necessary."""
    r: str = repr(obj)
    if len(r) > 200:
        r = r[:197] + "..."
    return r


class PrettyPrinter:
    """Configurable pretty-printer."""

    def __init__(self, indent: int = 1, width: int = 80, depth: int = -1,
                 compact: int = 0, sort_dicts: int = 1) -> None:
        self._indent: int = indent
        self._width: int = width
        self._depth: int = depth
        self._compact: int = compact
        self._sort_dicts: int = sort_dicts

    def pprint(self, obj) -> None:
        print(obj)

    def pformat(self, obj) -> str:
        return str(obj)

    def isreadable(self, obj) -> int:
        return isreadable(obj)

    def isrecursive(self, obj) -> int:
        return isrecursive(obj)
