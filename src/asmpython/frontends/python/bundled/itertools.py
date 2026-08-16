"""`itertools`, as ordinary Python this compiler compiles.

Every one of these is a generator, which is the whole reason the module reads
naturally here: the laziness is the language's, not a data structure imitating
it. See `bundled.py` for how these reach the program that imports them.
"""


def count(start=0, step=1):
    """An unbounded counter. Infinite, so only ever consumed by something that
    stops -- `islice`, a `break`, a `zip` against a finite side."""
    n = start
    while True:
        yield n
        n = n + step


def repeat(value, *times):
    """`repeat(x)` forever, `repeat(x, n)` n times."""
    if times:
        for _ in range(times[0]):
            yield value
    else:
        while True:
            yield value


def chain(*iterables):
    """Every element of every argument, in order."""
    for one in iterables:
        for item in one:
            yield item


def islice(iterable, *bounds):
    """`islice(it, stop)` and `islice(it, start, stop)`.

    Counted rather than indexed: the argument may be an iterator with no
    length and no way back, which is the case the whole module is for.
    """
    if len(bounds) == 1:
        start = 0
        stop = bounds[0]
    else:
        start = bounds[0]
        stop = bounds[1]
    i = 0
    for item in iterable:
        if i >= stop:
            return
        if i >= start:
            yield item
        i = i + 1


def groupby(iterable):
    """Consecutive runs of equal elements, as (key, group) pairs.

    CONSECUTIVE, not sorted: `groupby` finds runs, and a caller that wants one
    group per distinct value sorts first. The group is a LIST here rather than
    a lazy sub-iterator, so a caller may hold on to it after moving on -- which
    CPython's does not allow and no program should rely on either way.
    """
    items = list(iterable)
    i = 0
    while i < len(items):
        key = items[i]
        run = []
        while i < len(items) and items[i] == key:
            run.append(items[i])
            i = i + 1
        yield (key, run)


def product(*iterables):
    """The cartesian product, rightmost varying fastest."""
    rows = [list(one) for one in iterables]
    out = [[]]
    for row in rows:
        wider = []
        for prefix in out:
            for item in row:
                wider.append(prefix + [item])
        out = wider
    for combo in out:
        yield tuple(combo)


def combinations(iterable, r):
    """Every r-element subsequence, in the order the elements arrived.

    The INDICES are advanced lexicographically -- rightmost first, and each
    one carrying the ones after it -- because that is the order CPython
    yields, and a set of the right tuples in the wrong order is still a wrong
    answer to a program that prints them.
    """
    items = list(iterable)
    n = len(items)
    if r < 0 or r > n:
        return
    idx = list(range(r))
    while True:
        yield tuple([items[i] for i in idx])
        i = r - 1
        while i >= 0 and idx[i] == i + n - r:
            i = i - 1
        if i < 0:
            return
        idx[i] = idx[i] + 1
        for j in range(i + 1, r):
            idx[j] = idx[j - 1] + 1
