"""`bisect`, as ordinary Python this compiler compiles.

COVERAGE: complete -- `bisect_left`, `bisect_right`, `bisect` (an alias for
`bisect_right`), `insort_left`, `insort_right`, `insort` (an alias for
`insort_right`), each with `lo`, `hi` and `key=` -- which is CPython 3.14's
whole public surface (`dir(bisect)` minus dunders and the `_bisect` C
fallback CPython prefers when it is present; this module's pure-Python
bodies are what CPython falls back to without it, and that is the version
restored here).

Restored from CPython's own `Lib/bisect.py`, algorithm and all: `mid = (lo +
hi) // 2`, and the `<` on the LEFT of the comparison for `_right` and on the
RIGHT for `_left` is the whole difference between the two -- get it backwards
and every call with a duplicate in the list still returns an index, just the
wrong one, which is precisely the kind of wrong answer this rebuild exists
to catch rather than the kind that raises.
"""


def bisect_left(a, x, lo=0, hi=None, *, key=None):
    """Return the index where to insert item x in list a, assuming a is sorted.

    The return value i is such that all e in a[:i] have e < x, and all e in
    a[i:] have e >= x. So if x already appears in the list, a.insert(i, x)
    will insert just before the leftmost x already there.

    Optional args lo (default 0) and hi (default len(a)) bound the slice of
    a to be searched.

    A custom key function can be supplied to customize the sort order.
    """
    if lo < 0:
        raise ValueError('lo must be non-negative')
    if hi is None:
        hi = len(a)
    # THE COMPARISON USES "<" TO MATCH __lt__, same as list.sort() and heapq
    # -- a type that defines only __lt__ still bisects correctly.
    if key is None:
        while lo < hi:
            mid = (lo + hi) // 2
            if a[mid] < x:
                lo = mid + 1
            else:
                hi = mid
    else:
        while lo < hi:
            mid = (lo + hi) // 2
            if key(a[mid]) < x:
                lo = mid + 1
            else:
                hi = mid
    return lo


def bisect_right(a, x, lo=0, hi=None, *, key=None):
    """Return the index where to insert item x in list a, assuming a is sorted.

    The return value i is such that all e in a[:i] have e <= x, and all e in
    a[i:] have e > x. So if x already appears in the list, a.insert(i, x)
    will insert just after the rightmost x already there.

    Optional args lo (default 0) and hi (default len(a)) bound the slice of
    a to be searched.

    A custom key function can be supplied to customize the sort order.
    """
    if lo < 0:
        raise ValueError('lo must be non-negative')
    if hi is None:
        hi = len(a)
    if key is None:
        while lo < hi:
            mid = (lo + hi) // 2
            if x < a[mid]:
                hi = mid
            else:
                lo = mid + 1
    else:
        while lo < hi:
            mid = (lo + hi) // 2
            if x < key(a[mid]):
                hi = mid
            else:
                lo = mid + 1
    return lo


def insort_left(a, x, lo=0, hi=None, *, key=None):
    """Insert item x in list a, and keep it sorted assuming a is sorted.

    If x is already in a, insert it to the left of the leftmost x.

    Optional args lo (default 0) and hi (default len(a)) bound the slice of
    a to be searched.

    A custom key function can be supplied to customize the sort order.
    """
    # WHEN key IS GIVEN, THE SEARCH TARGET IS key(x) TOO -- x itself is never
    # comparable to the keys a[mid] produces (a list of numbers keyed by
    # abs() is searched by the ABSOLUTE VALUE of x, not by x), so bisecting
    # unkeyed on x here would compare the wrong things.
    if key is None:
        lo = bisect_left(a, x, lo, hi)
    else:
        lo = bisect_left(a, key(x), lo, hi, key=key)
    a.insert(lo, x)


def insort_right(a, x, lo=0, hi=None, *, key=None):
    """Insert item x in list a, and keep it sorted assuming a is sorted.

    If x is already in a, insert it to the right of the rightmost x.

    Optional args lo (default 0) and hi (default len(a)) bound the slice of
    a to be searched.

    A custom key function can be supplied to customize the sort order.
    """
    if key is None:
        lo = bisect_right(a, x, lo, hi)
    else:
        lo = bisect_right(a, key(x), lo, hi, key=key)
    a.insert(lo, x)


#: Aliases, exactly as CPython defines them at the bottom of `Lib/bisect.py`.
bisect = bisect_right
insort = insort_right
