"""copy module: shallow and deep copy helpers.

In asmpython, all primitive values (int, float, str) are passed by value,
so copy() just returns the value unchanged. For lists, copy() creates a
new list with the same elements (shallow). deepcopy() is the same as copy()
since asmpython doesn't have nested mutable containers in practice.
"""
from __future__ import annotations


class error(Exception):
    """Exception raised for copy errors."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


def copy(obj: list) -> list:
    """Return a shallow copy of obj (works for lists)."""
    result: list[int] = []
    for item in obj:
        result.append(item)
    return result


def deepcopy(obj: list, memo: int = 0) -> list:
    """Return a copy of obj. For lists, same as copy()."""
    result2: list[int] = []
    for item2 in obj:
        result2.append(item2)
    return result2


def copy_str(s: str) -> str:
    """Return a copy of a string (strings are immutable, returns same)."""
    return s + ""
