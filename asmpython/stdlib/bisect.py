"""bisect module: array bisection algorithm.

Provides bisect_left, bisect_right, insort_left, insort_right for
maintaining sorted lists. All operations are O(log n).
"""
from __future__ import annotations


def bisect_left(a: list, x: int, lo: int = 0, hi: int = -1) -> int:
    """Return leftmost insertion point for x in sorted list a."""
    if hi == -1:
        hi = len(a)
    while lo < hi:
        mid: int = (lo + hi) >> 1
        if a[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def bisect_right(a: list, x: int, lo: int = 0, hi: int = -1) -> int:
    """Return rightmost insertion point for x in sorted list a."""
    if hi == -1:
        hi = len(a)
    while lo < hi:
        mid: int = (lo + hi) >> 1
        if x < a[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


def insort_left(a: list, x: int, lo: int = 0, hi: int = -1) -> None:
    """Insert x into sorted list a at leftmost position."""
    pos: int = bisect_left(a, x, lo, hi)
    i: int = len(a)
    a.append(x)
    while i > pos:
        a[i] = a[i - 1]
        i = i - 1
    a[pos] = x


def insort_right(a: list, x: int, lo: int = 0, hi: int = -1) -> None:
    """Insert x into sorted list a at rightmost position."""
    pos: int = bisect_right(a, x, lo, hi)
    i: int = len(a)
    a.append(x)
    while i > pos:
        a[i] = a[i - 1]
        i = i - 1
    a[pos] = x


bisect = bisect_right
insort = insort_right
