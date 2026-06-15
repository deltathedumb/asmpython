"""enum module: enumeration types.

Provides Enum and IntEnum base classes. In asmpython, enum members are
class-level integer constants. The @auto() function provides auto-incrementing
values. This is a source-level implementation that compiles to native code.
"""
from __future__ import annotations


_auto_counter: int = 0


def auto() -> int:
    """Return the next auto-incremented enum value (global counter)."""
    global _auto_counter
    _auto_counter = _auto_counter + 1
    return _auto_counter


class Enum:
    """Base class for enumerations.

    Subclass and define class-level attributes as int constants.
    In asmpython, enum values are plain integers; members() returns a
    list of (name, value) pairs populated by __init_subclass__ convention.
    """

    def __init__(self, value: int) -> None:
        self.value: int = value
        self.name: str = ""

    def __eq__(self, other: int) -> int:
        return 1 if self.value == other else 0

    def __ne__(self, other: int) -> int:
        return 1 if self.value != other else 0

    def __lt__(self, other: int) -> int:
        return 1 if self.value < other else 0

    def __le__(self, other: int) -> int:
        return 1 if self.value <= other else 0

    def __gt__(self, other: int) -> int:
        return 1 if self.value > other else 0

    def __ge__(self, other: int) -> int:
        return 1 if self.value >= other else 0

    def __hash__(self) -> int:
        return self.value

    def __str__(self) -> str:
        if self.name == "":
            return str(self.value)
        return self.name + "." + str(self.value)

    def __repr__(self) -> str:
        return self.__str__()

    def __int__(self) -> int:
        return self.value


class IntEnum(Enum):
    """Enumeration where members are also integers.

    IntEnum members compare equal to their integer values.
    """
    pass


class Flag(Enum):
    """Enumeration supporting bitwise operations."""

    def __or__(self, other: int) -> int:
        return self.value | other

    def __and__(self, other: int) -> int:
        return self.value & other

    def __xor__(self, other: int) -> int:
        return self.value ^ other

    def __invert__(self) -> int:
        return ~self.value


class IntFlag(Flag):
    """Flag enumeration that is also an integer."""
    pass


def unique(enumclass: int) -> int:
    """Decorator to ensure all enum values are unique (stub: returns enum unchanged)."""
    return enumclass


class EnumMeta:
    """Metaclass stub for Enum. Provides iteration over known member lists."""
    pass


class StrEnum(Enum):
    """Enumeration where members are also strings."""

    def __str__(self) -> str:
        return self.value


CONFORM: int = 0
EJECT: int = 1
KEEP: int = 2
STRICT: int = 3
CONTINUOUS: int = 4
NAMED_FLAGS: int = 5
UNIQUE: int = 6


class ReprEnum(Enum):
    """Enum whose str() is the member value's repr."""

    def __str__(self) -> str:
        return repr(self.value)


class EnumType(EnumMeta):
    """Alias for EnumMeta."""
    pass


class EnumCheck:
    """Enum verification constants."""
    UNIQUE: int = 0
    CONTINUOUS: int = 1
    NAMED_FLAGS: int = 2


class FlagBoundary:
    """Boundary behavior for Flag enums."""
    STRICT: int = 0
    CONFORM: int = 1
    EJECT: int = 2
    KEEP: int = 3


def verify(enum_cls: int, *checks: int) -> int:
    """Verify the enum class satisfies the given checks (stub: returns enum)."""
    return enum_cls


def member(value: int) -> int:
    """Force a value to be treated as an enum member."""
    return value


def nonmember(value: int) -> int:
    """Force a value to be treated as a non-member."""
    return value


def global_enum(cls: int) -> int:
    """Make enum members visible in the global namespace (stub)."""
    return cls


def show_flag_values(value: int) -> list[int]:
    """Return list of all power-of-2 values in a flag value."""
    result: list[int] = []
    bit: int = 1
    while bit <= value:
        if value & bit:
            result.append(bit)
        bit = bit * 2
    return result
