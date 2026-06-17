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


class MappingProxy:
    """Read-only proxy for a mapping (stub)."""

    def __init__(self, mapping: int) -> None:
        self._mapping: int = mapping


class _NamespaceMixin:
    """Mixin providing SimpleNamespace-like attribute storage."""

    def __init__(self) -> None:
        self._keys: list[str] = []
        self._vals: list = []

    def _set(self, key: str, val: int) -> None:
        i: int = 0
        while i < len(self._keys):
            if self._keys[i] == key:
                self._vals[i] = val
                return
            i = i + 1
        self._keys.append(key)
        self._vals.append(val)

    def _get(self, key: str) -> int:
        i: int = 0
        while i < len(self._keys):
            if self._keys[i] == key:
                return self._vals[i]
            i = i + 1
        return 0
