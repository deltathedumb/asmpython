"""pprint module: data pretty-printing.

In asmpython, pprint() formats using str() since we have no reflection.
"""
from __future__ import annotations


def pformat(obj: int, indent: int = 1, width: int = 80, depth: int = -1) -> str:
    return str(obj)


def pprint(obj: int, indent: int = 1, width: int = 80, depth: int = -1) -> None:
    print(pformat(obj, indent, width, depth))


def isreadable(obj: int) -> int:
    return 1


def isrecursive(obj: int) -> int:
    return 0


def saferepr(obj: int) -> str:
    return str(obj)


class PrettyPrinter:
    def __init__(self, indent: int = 1, width: int = 80, depth: int = -1) -> None:
        self._indent: int = indent
        self._width: int = width
        self._depth: int = depth

    def pprint(self, obj: int) -> None:
        print(self.pformat(obj))

    def pformat(self, obj: int) -> str:
        return str(obj)

    def isreadable(self, obj: int) -> int:
        return 1

    def isrecursive(self, obj: int) -> int:
        return 0
