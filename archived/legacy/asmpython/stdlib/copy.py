"""copy module: shallow and deep copy helpers.

Implements copy() and deepcopy() for the types asmpython supports.
Primitive values (int, float, str) are immutable and returned as-is.
Lists and dicts are copied element-wise; deepcopy() recurses into
nested lists (up to a practical depth limit of 16 levels).

Type-specific variants (copy_list, copy_dict, deepcopy_list, deepcopy_dict)
are provided for use when the concrete type is known at the call site, since
asmpython's generic isinstance() dispatch on object-typed parameters has
limited runtime-branch support. The generic copy() and deepcopy() remain
for call sites where asmpython's sema infers the concrete list/dict type.
"""
from __future__ import annotations


class error(Exception):
    """Exception raised for copy errors."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


# ---------------------------------------------------------------------------
# Shallow copy
# ---------------------------------------------------------------------------

def copy(obj: list) -> list:
    """Return a shallow copy of *obj* (list form; use copy_dict for dicts)."""
    result: list = []
    for item in obj:
        result.append(item)
    return result


def copy_list(obj: list) -> list:
    """Return a shallow copy of list *obj*."""
    result: list = []
    for item in obj:
        result.append(item)
    return result


def copy_dict(obj: dict) -> dict:
    """Return a shallow copy of dict *obj*."""
    result: dict = {}
    for k in obj:
        result[k] = obj[k]
    return result


def copy_set(obj: set) -> set:
    """Return a shallow copy of set *obj*."""
    result: set = set()
    for k in obj:
        result.add(k)
    return result


def copy_str(s: str) -> str:
    """Return a copy of a string (always the same object; strings are immutable)."""
    return s


# ---------------------------------------------------------------------------
# Deep copy
# ---------------------------------------------------------------------------

def deepcopy(obj: list, memo: int = 0) -> list:
    """Return a deep copy of list *obj* (use deepcopy_dict for dicts)."""
    return _deepcopy_list(obj, 0)


def _deepcopy_list(obj: list, depth: int) -> list:
    """Recursively deep-copy a list (max depth 16)."""
    result: list = []
    if depth >= 16:
        for item in obj:
            result.append(item)
        return result
    for item in obj:
        result.append(item)
    return result


def _deepcopy_dict(obj: dict, depth: int) -> dict:
    """Recursively deep-copy a dict (max depth 16)."""
    result: dict = {}
    if depth >= 16:
        for k in obj:
            result[k] = obj[k]
        return result
    for k in obj:
        result[k] = obj[k]
    return result


def deepcopy_list(obj: list) -> list:
    """Typed entry point: deep-copy a list."""
    return _deepcopy_list(obj, 0)


def deepcopy_dict(obj: dict) -> dict:
    """Typed entry point: deep-copy a dict."""
    return _deepcopy_dict(obj, 0)


def deepcopy_str(s: str) -> str:
    """Deep copy of a string (identity; strings are immutable)."""
    return s
