"""dataclasses module: data class support.

In asmpython, @dataclass is a no-op decorator since the compiler handles
class definitions directly. The module provides the decorator and field()
function so that `from dataclasses import dataclass, field` works.
"""
from __future__ import annotations


def dataclass(cls: int) -> int:
    """No-op decorator: asmpython compiles class bodies directly."""
    return cls


def field(default: int = 0, default_factory: int = 0) -> int:
    """Describe a dataclass field (stub). Returns the default value."""
    if default_factory != 0:
        return 0
    return default


def fields(obj: int) -> list:
    """Return fields of a dataclass (stub, returns empty list)."""
    return []


def asdict(obj: int) -> int:
    """Convert dataclass to dict (stub)."""
    return 0


def astuple(obj: int) -> list:
    """Convert dataclass to tuple (stub, returns empty list)."""
    return []


def make_dataclass(cls_name: str, fields_list: list) -> int:
    """Dynamically create a dataclass (stub)."""
    return 0


def replace(obj: int) -> int:
    """Create a copy of a dataclass with replaced fields (stub)."""
    return obj


def is_dataclass(obj: int) -> int:
    """Return True if obj is a dataclass (stub, always returns 0)."""
    return 0


class KW_ONLY:
    """Sentinel for keyword-only fields (stub)."""
    pass


class InitVar:
    """Pseudo-field for __init__ only (stub)."""
    def __init__(self, t: int) -> None:
        self.type: int = t
