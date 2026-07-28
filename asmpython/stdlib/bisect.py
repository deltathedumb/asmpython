"""bisect: maintain a list in sorted order without re-sorting it.

Mirrors CPython, including the `hi=None` sentinel and the `key` parameter added
in 3.10. The previous version here defaulted `hi` to -1, so an explicit
`hi=-1` searched the whole list where CPython searches an empty range.
"""
from __future__ import annotations


def bisect_left(a: list, x, lo: int = 0, hi=None, key=None) -> int:
    """Leftmost index where `x` can be inserted and keep `a` sorted."""
    if lo < 0:
        raise ValueError("lo must be non-negative")
    if hi is None:
        hi = len(a)
    if key is None:
        while lo < hi:
            mid = (lo + hi) // 2
            if a[mid] < x:
                lo = mid + 1
            else:
                hi = mid
        return lo
    while lo < hi:
        mid = (lo + hi) // 2
        if key(a[mid]) < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def bisect_right(a: list, x, lo: int = 0, hi=None, key=None) -> int:
    """Rightmost index where `x` can be inserted and keep `a` sorted."""
    if lo < 0:
        raise ValueError("lo must be non-negative")
    if hi is None:
        hi = len(a)
    if key is None:
        while lo < hi:
            mid = (lo + hi) // 2
            if x < a[mid]:
                hi = mid
            else:
                lo = mid + 1
        return lo
    while lo < hi:
        mid = (lo + hi) // 2
        if x < key(a[mid]):
            hi = mid
        else:
            lo = mid + 1
    return lo


def insort_left(a: list, x, lo: int = 0, hi=None, key=None) -> None:
    """Insert `x` into `a` before any equal entries, keeping it sorted."""
    if key is None:
        position = bisect_left(a, x, lo, hi)
    else:
        position = bisect_left(a, key(x), lo, hi, key)
    a.insert(position, x)


def insort_right(a: list, x, lo: int = 0, hi=None, key=None) -> None:
    """Insert `x` into `a` after any equal entries, keeping it sorted."""
    if key is None:
        position = bisect_right(a, x, lo, hi)
    else:
        position = bisect_right(a, key(x), lo, hi, key)
    a.insert(position, x)


# CPython's historical aliases: plain `bisect`/`insort` are the right-hand forms.
def bisect(a: list, x, lo: int = 0, hi=None, key=None) -> int:
    return bisect_right(a, x, lo, hi, key)


def insort(a: list, x, lo: int = 0, hi=None, key=None) -> None:
    insort_right(a, x, lo, hi, key)
