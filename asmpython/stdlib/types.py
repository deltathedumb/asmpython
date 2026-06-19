"""types module: names for built-in types.

Provides type name constants that match CPython's types module.
In asmpython all these are effectively int stubs since there's
no first-class type system at runtime.
"""
from __future__ import annotations


NoneType: int = 0
FunctionType: int = 0
LambdaType: int = 0
CodeType: int = 0
MappingProxyType: int = 0
SimpleNamespace: int = 0
GeneratorType: int = 0
CoroutineType: int = 0
AsyncGeneratorType: int = 0
MethodType: int = 0
BuiltinFunctionType: int = 0
BuiltinMethodType: int = 0
WrapperDescriptorType: int = 0
MethodWrapperType: int = 0
MethodDescriptorType: int = 0
ClassMethodDescriptorType: int = 0
ModuleType: int = 0
TracebackType: int = 0
FrameType: int = 0
GetSetDescriptorType: int = 0
MemberDescriptorType: int = 0
DynamicClassAttribute: int = 0
UnionType: int = 0


def new_class(name: str, bases: list = [], kwds: int = 0,
              exec_body: int = 0) -> int:
    """Dynamically create a new class (stub: returns 0)."""
    return 0


def resolve_bases(bases: list) -> list:
    """Resolve MRO entries for base classes (stub: returns bases unchanged)."""
    return bases


def prepare_class(name: str, bases: list = [], kwds: int = 0) -> list:
    """Prepare class creation (stub: returns [0, 0, 0])."""
    return [0, 0, 0]


def coroutine(func: int) -> int:
    """Decorator to mark a function as a coroutine (stub: returns func)."""
    return func


class MappingProxyType:
    """Read-only proxy for a mapping."""

    def __init__(self, mapping: dict) -> None:
        self._mapping: dict = mapping

    def get(self, key: str, default: object = None) -> object:
        return self._mapping.get(key, default)

    def keys(self) -> list:
        return self._mapping.keys()

    def values(self) -> list:
        return self._mapping.values()

    def items(self) -> list:
        return self._mapping.items()

    def __contains__(self, key: str) -> int:
        return 1 if key in self._mapping else 0

    def copy(self) -> dict:
        return self._mapping.copy()

    def __repr__(self) -> str:
        return "mappingproxy(" + str(self._mapping) + ")"


class SimpleNamespace:
    """A simple namespace object (types.SimpleNamespace).

    Attributes are stored in a parallel list pair (no reflection needed).
    Use ns._set("name", value) / ns._get("name") for attribute access, or
    access known attributes directly as typed fields on subclasses.
    """

    def __init__(self) -> None:
        self._keys: list[str] = []
        self._vals: list = []

    def _set(self, key: str, val: object) -> None:
        """Set attribute *key* to *val*."""
        i: int = 0
        while i < len(self._keys):
            if self._keys[i] == key:
                self._vals[i] = val
                return
            i = i + 1
        self._keys.append(key)
        self._vals.append(val)

    def _get(self, key: str) -> object:
        """Return the value of attribute *key*, or None if not set."""
        i: int = 0
        while i < len(self._keys):
            if self._keys[i] == key:
                return self._vals[i]
            i = i + 1
        return None

    def _has(self, key: str) -> int:
        """Return 1 if attribute *key* is set."""
        i: int = 0
        while i < len(self._keys):
            if self._keys[i] == key:
                return 1
            i = i + 1
        return 0

    def _del(self, key: str) -> None:
        """Delete attribute *key*."""
        n: int = len(self._keys)
        i: int = 0
        while i < n:
            if self._keys[i] == key:
                new_keys: list[str] = []
                new_vals: list = []
                j: int = 0
                while j < n:
                    if j != i:
                        new_keys.append(self._keys[j])
                        new_vals.append(self._vals[j])
                    j = j + 1
                self._keys = new_keys
                self._vals = new_vals
                return
            i = i + 1
        raise AttributeError("namespace has no attribute '" + key + "'")

    def __repr__(self) -> str:
        parts: list[str] = []
        i: int = 0
        while i < len(self._keys):
            parts.append(self._keys[i] + "=" + str(self._vals[i]))
            i = i + 1
        return "namespace(" + ", ".join(parts) + ")"

    def __eq__(self, other: SimpleNamespace) -> int:
        if len(self._keys) != len(other._keys):
            return 0
        i: int = 0
        while i < len(self._keys):
            k: str = self._keys[i]
            if not other._has(k):
                return 0
            if other._get(k) != self._vals[i]:
                return 0
            i = i + 1
        return 1


class _NamespaceMixin(SimpleNamespace):
    """Backward-compatible alias: inherits SimpleNamespace's implementation."""
    pass
