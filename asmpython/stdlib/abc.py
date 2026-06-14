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


def abstractmethod(func: str) -> str:
    """Decorator to mark a method as abstract (no-op at runtime)."""
    return func


class ABCMeta:
    """Metaclass for ABCs. In asmpython, this is a no-op placeholder."""
    pass
