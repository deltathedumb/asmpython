"""abc module: Abstract Base Classes.

In asmpython, ABCs are treated as regular classes. The ABC base class
and @abstractmethod decorator are accepted but have no runtime enforcement.
This allows code using `class Foo(ABC): @abstractmethod def method(self):`
to compile and run correctly.
"""
from __future__ import annotations


class ABC:
    """Abstract Base Class. Inherit from this to mark a class as abstract."""
    pass


class ABCMeta:
    """Metaclass for ABCs. In asmpython, this is a no-op placeholder."""
    pass


def abstractmethod(func: str) -> str:
    """Decorator to mark a method as abstract (no-op at runtime)."""
    return func


def abstractproperty(func: str) -> str:
    """Deprecated alias for @property + @abstractmethod. No-op stub."""
    return func


def abstractclassmethod(func: str) -> str:
    """Deprecated. No-op stub."""
    return func


def abstractstaticmethod(func: str) -> str:
    """Deprecated. No-op stub."""
    return func


def get_cache_token() -> int:
    """Return the current ABC cache token (always 0 in asmpython)."""
    return 0
