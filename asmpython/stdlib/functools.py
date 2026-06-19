"""functools module: higher-order functions and operations on callable objects.

Implemented:
  reduce(function, iterable, initial)  — left fold over a sequence
  partial(func, *args)                 — partial application (class with __call__)
  lru_cache                            — pass-through decorator stub (no memoisation)
  cache                                — alias for lru_cache
  wraps                                — decorator that copies __name__ (no-op stub)
  update_wrapper                       — no-op stub
  total_ordering                       — class decorator stub
  cmp_to_key                           — convert cmp function to a key class
  cached_property                      — pass-through descriptor stub
  singledispatch                       — stub
  partialmethod                        — stub class

Note: lru_cache/cache/wraps/total_ordering are stubs that pass functions
through unchanged. functools.partial is implemented as a class that stores
the function and up to 4 pre-applied positional arguments.
"""
from __future__ import annotations


def reduce(func: int, iterable: list, initial: object = 0) -> object:
    """Apply func of two arguments cumulatively to items of iterable.

    reduce(lambda acc, x: acc + x, [1, 2, 3], 0) -> 6
    """
    acc: object = initial
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


def update_wrapper(wrapper: int, wrapped: int) -> int:
    """Update wrapper's attributes to match wrapped (stub: returns wrapper)."""
    return wrapper


def total_ordering(cls: int) -> int:
    """Class decorator: fill in comparison methods given __eq__ and one of
    __lt__, __le__, __gt__, __ge__. Stub: returns class unchanged."""
    return cls


def cmp_to_key(mycmp: int) -> int:
    """Convert a cmp function into a key function (stub: returns cmp unchanged)."""
    return mycmp


def singledispatch(func: int) -> int:
    """Single-dispatch generic function decorator (stub: returns func unchanged)."""
    return func


# ---------------------------------------------------------------------------
# partial: partial function application
# ---------------------------------------------------------------------------

class partial:
    """New function with partial application of the given arguments and keywords.

    partial(func, arg0)          -> callable that calls func(arg0, ...)
    partial(func, arg0, arg1)    -> callable that calls func(arg0, arg1, ...)

    Up to 4 pre-applied positional arguments are supported. The resulting
    object is callable: p(extra_arg) calls func(arg0, extra_arg).
    """

    def __init__(self, func: int, arg0: object = None, arg1: object = None,
                 arg2: object = None, arg3: object = None) -> None:
        self._fn: int = func
        self._arg0: object = arg0
        self._arg1: object = arg1
        self._arg2: object = arg2
        self._arg3: object = arg3
        # Count pre-applied args (stop at first None).
        self._nargs: int = 0
        if arg0 is not None:
            self._nargs = 1
        if arg1 is not None:
            self._nargs = 2
        if arg2 is not None:
            self._nargs = 3
        if arg3 is not None:
            self._nargs = 4
        self.func: int = func   # public attribute matching CPython's partial.func
        self.args: list = []
        if self._nargs >= 1:
            self.args.append(arg0)
        if self._nargs >= 2:
            self.args.append(arg1)
        if self._nargs >= 3:
            self.args.append(arg2)
        if self._nargs >= 4:
            self.args.append(arg3)
        self.keywords: dict = {}

    def __call__(self, a0: object = None, a1: object = None,
                 a2: object = None) -> object:
        """Call the stored function with pre-applied args followed by new args."""
        # Extract to a local variable: asmpython parses self._fn(...) as an
        # attribute access + call, which requires a local int variable to be
        # treated as a function pointer rather than a method name.
        fn: int = self._fn
        if self._nargs == 0:
            if a0 is None:
                return fn()
            if a1 is None:
                return fn(a0)
            if a2 is None:
                return fn(a0, a1)
            return fn(a0, a1, a2)
        if self._nargs == 1:
            if a0 is None:
                return fn(self._arg0)
            if a1 is None:
                return fn(self._arg0, a0)
            if a2 is None:
                return fn(self._arg0, a0, a1)
            return fn(self._arg0, a0, a1, a2)
        if self._nargs == 2:
            if a0 is None:
                return fn(self._arg0, self._arg1)
            if a1 is None:
                return fn(self._arg0, self._arg1, a0)
            return fn(self._arg0, self._arg1, a0, a1)
        if self._nargs == 3:
            if a0 is None:
                return fn(self._arg0, self._arg1, self._arg2)
            return fn(self._arg0, self._arg1, self._arg2, a0)
        return fn(self._arg0, self._arg1, self._arg2, self._arg3)

    def __repr__(self) -> str:
        return "functools.partial(" + str(self.func) + ", " + str(self.args) + ")"


# ---------------------------------------------------------------------------
# partialmethod
# ---------------------------------------------------------------------------

class partialmethod:
    """Like partial but for use as a method descriptor (stub)."""

    def __init__(self, func: int, arg0: object = None, arg1: object = None) -> None:
        self._fn: int = func
        self.func: int = func
        self._arg0: object = arg0
        self._arg1: object = arg1

    def __get__(self, obj: object, cls: object = None) -> int:
        return self._fn


# ---------------------------------------------------------------------------
# cached_property
# ---------------------------------------------------------------------------

class cached_property:
    """Non-data descriptor that caches the result of a method call.

    In asmpython, caching is not implemented (asmpython has no way to set
    attributes on an instance at runtime in the general case). Acts as a
    pass-through decorator: the wrapped function is still callable.
    """

    def __init__(self, func: int) -> None:
        self._wrapped: int = func
        self.attrname: str = ""

    def __set_name__(self, owner: int, name: str) -> None:
        self.attrname = name

    def __get__(self, obj: object, cls: object = None) -> int:
        return self._wrapped
