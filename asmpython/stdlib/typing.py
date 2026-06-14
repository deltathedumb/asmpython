"""typing module: type hints support.

In asmpython, type annotations are used by the semantic analyser but
the typing module itself is a stub that provides the common names so
that `from typing import X` works without errors.
"""
from __future__ import annotations


def _identity(x: int) -> int:
    return x


# Generic alias stubs — these are callable and return their argument
# when used as decorators or subscript-like calls.

def Optional(t: int) -> int:
    return t


def Union(*args) -> int:
    return 0


def List(t: int) -> int:
    return t


def Dict(k: int, v: int) -> int:
    return k


def Tuple(*args) -> int:
    return 0


def Set(t: int) -> int:
    return t


def FrozenSet(t: int) -> int:
    return t


def Type(t: int) -> int:
    return t


def Callable(*args) -> int:
    return 0


def Iterator(t: int) -> int:
    return t


def Iterable(t: int) -> int:
    return t


def Generator(*args) -> int:
    return 0


def Sequence(t: int) -> int:
    return t


def MutableSequence(t: int) -> int:
    return t


def Mapping(k: int, v: int) -> int:
    return k


def MutableMapping(k: int, v: int) -> int:
    return k


def ClassVar(t: int) -> int:
    return t


def Final(t: int) -> int:
    return t


def Literal(*args) -> int:
    return 0


def Annotated(*args) -> int:
    return 0


def overload(func: int) -> int:
    return func


def cast(t: int, val: int) -> int:
    return val


def no_type_check(func: int) -> int:
    return func


def runtime_checkable(cls: int) -> int:
    return cls


# TypeVar stub
class TypeVar:
    def __init__(self, name: str) -> None:
        self.__name__: str = name

    def __str__(self) -> str:
        return self.__name__


# Protocol stub
class Protocol:
    pass


# NamedTuple stub
class NamedTuple:
    pass


# TypedDict stub
class TypedDict:
    pass


# Special forms as int constants (used as type: ignore anchors)
Any: int = 0
Never: int = 0
NoReturn: int = 0
Self: int = 0
TYPE_CHECKING: int = 0
