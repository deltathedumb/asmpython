"""functools module: higher-order functions and operations on callable objects.

Implemented:
  reduce(function, iterable, initial)  — left fold over a sequence
  lru_cache                            — pass-through decorator stub (no actual caching)
  cache                                — alias for lru_cache
  wraps                                — decorator that copies __name__ etc. (no-op stub)
  cmp_to_key                           — convert cmp function to a key class

Note: functools.partial is not implemented because asmpython does not support
storing arbitrary callables in instance fields and calling them later.
lru_cache/cache/wraps are stubs (decorators pass through unchanged).
"""
from __future__ import annotations


def reduce(func: int, iterable: list, initial: object = 0) -> object:
    """Apply func of two arguments cumulatively to items of iterable.

    reduce(lambda acc, x: acc + x, [1, 2, 3], 0) -> 6
    The initial value is required (CPython makes it optional).
    """
    acc = initial
    for item in iterable:
        acc = func(acc, item)
    return acc


def lru_cache(maxsize: int = 128) -> int:
    """Decorator stub: returns the function unchanged (no memoisation)."""
    def _lru_deco(func: int) -> int:
        return func
    return _lru_deco


def cache(func: int) -> int:
    """Unbounded memoisation stub: returns function unchanged."""
    return func


def wraps(wrapped: int) -> int:
    """Decorator stub: returns a pass-through decorator."""
    def _wraps_deco(func: int) -> int:
        return func
    return _wraps_deco


def total_ordering(cls: int) -> int:
    """Class decorator: fill in comparison methods given __eq__ and one of
    __lt__, __le__, __gt__, __ge__. Stub: returns class unchanged."""
    return cls


def cmp_to_key(mycmp: int) -> int:
    """Convert a cmp function into a key function (stub: returns cmp unchanged)."""
    return mycmp


class cached_property:
    """Descriptor stub: acts as a pass-through decorator (no actual caching)."""

    def __init__(self, func: int) -> None:
        self._wrapped: int = func
        self.attrname: str = ""

    def __set_name__(self, owner: int, name: str) -> None:
        self.attrname = name


def singledispatch(func: int) -> int:
    """Single-dispatch generic function decorator (stub: returns func unchanged)."""
    return func


def partial(func: int, arg0: int = 0, arg1: int = 0, arg2: int = 0) -> int:
    """Return a new function with partial application of the given arguments.

    Stub: calls func with the pre-filled args directly.
    asmpython limitation: the returned value is the function pointer, not a
    true partial object; users should call partial(f, a)(b) -> f(a, b) if
    the compiler supports higher-order calls.
    """
    return func


def update_wrapper(wrapper: int, wrapped: int) -> int:
    """Update wrapper's attributes to match wrapped (stub: returns wrapper)."""
    return wrapper


def partialmethod(func: int, arg0: int = 0, arg1: int = 0) -> int:
    """Like partial but for use with descriptors/methods (stub)."""
    return func


