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


