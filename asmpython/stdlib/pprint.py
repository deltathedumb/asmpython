"""pprint module: data pretty-printing.

In asmpython, pprint() formats using str() since we have no reflection.
"""
from __future__ import annotations


def pformat(obj, indent: int = 1, width: int = 80, depth: int = -1) -> str:
    return str(obj)


def pprint(obj, indent: int = 1, width: int = 80, depth: int = -1) -> None:
    print(obj)


def isreadable(obj) -> int:
    return 1


def isrecursive(obj) -> int:
    return 0


def saferepr(obj) -> str:
    return str(obj)


class PrettyPrinter:
    def __init__(self, indent: int = 1, width: int = 80, depth: int = -1) -> None:
        self._indent: int = indent
        self._width: int = width
        self._depth: int = depth

    def pprint(self, obj) -> None:
        print(obj)

    def pformat(self, obj) -> str:
        return str(obj)

    def isreadable(self, obj) -> int:
        return 1

    def isrecursive(self, obj) -> int:
        return 0
