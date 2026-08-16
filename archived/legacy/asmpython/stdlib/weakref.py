"""weakref module: weak references to objects.

In asmpython (no GC), weak references are identical to strong references.
This stub allows code using weakref to compile without errors.
"""
from __future__ import annotations


class ref:
    """A weak reference (in asmpython, a strong reference)."""

    def __init__(self, obj: int, callback: int = 0) -> None:
        self._obj: int = obj
        self._callback: int = callback

    def __call__(self) -> int:
        return self._obj

    def __eq__(self, other: ref) -> int:
        return 1 if self._obj == other._obj else 0

    def __hash__(self) -> int:
        return self._obj

    def __bool__(self) -> int:
        return 1 if self._obj != 0 else 0


def proxy(obj: int, callback: int = 0) -> int:
    """Return a proxy for the object (in asmpython, returns obj directly)."""
    return obj


def getweakrefcount(obj: int) -> int:
    """Return the number of weak references to obj (stub, returns 0)."""
    return 0


def getweakrefs(obj: int) -> list:
    """Return all weak references to obj (stub, empty list)."""
    return []


class WeakSet:
    """A set of weakly-referenced objects (stub, behaves as regular set)."""

    def __init__(self) -> None:
        self._data: list = []

    def add(self, obj: int) -> None:
        i: int = 0
        while i < len(self._data):
            if self._data[i] == obj:
                return
            i = i + 1
        self._data.append(obj)

    def discard(self, obj: int) -> None:
        new_data: list = []
        i: int = 0
        while i < len(self._data):
            if self._data[i] != obj:
                new_data.append(self._data[i])
            i = i + 1
        self._data = new_data

    def remove(self, obj: int) -> None:
        self.discard(obj)

    def __len__(self) -> int:
        return len(self._data)

    def __contains__(self, obj: int) -> int:
        i: int = 0
        while i < len(self._data):
            if self._data[i] == obj:
                return 1
            i = i + 1
        return 0

    def clear(self) -> None:
        self._data = []


class WeakKeyDictionary:
    """A mapping with weakly-referenced keys (stub)."""

    def __init__(self) -> None:
        self._keys: list = []
        self._vals: list = []

    def __setitem__(self, key: int, value: int) -> None:
        i: int = 0
        while i < len(self._keys):
            if self._keys[i] == key:
                self._vals[i] = value
                return
            i = i + 1
        self._keys.append(key)
        self._vals.append(value)

    def __getitem__(self, key: int) -> int:
        i: int = 0
        while i < len(self._keys):
            if self._keys[i] == key:
                return self._vals[i]
            i = i + 1
        return 0

    def __len__(self) -> int:
        return len(self._keys)

    def __contains__(self, key: int) -> int:
        i: int = 0
        while i < len(self._keys):
            if self._keys[i] == key:
                return 1
            i = i + 1
        return 0

    def get(self, key: int, default: int = 0) -> int:
        i: int = 0
        while i < len(self._keys):
            if self._keys[i] == key:
                return self._vals[i]
            i = i + 1
        return default

    def keys(self) -> list:
        result: list = []
        i: int = 0
        while i < len(self._keys):
            result.append(self._keys[i])
            i = i + 1
        return result

    def values(self) -> list:
        result: list = []
        i: int = 0
        while i < len(self._vals):
            result.append(self._vals[i])
            i = i + 1
        return result

    def clear(self) -> None:
        self._keys = []
        self._vals = []


class WeakValueDictionary(WeakKeyDictionary):
    """A mapping with weakly-referenced values (stub, same as WeakKeyDictionary)."""
    pass


finalize = ref
