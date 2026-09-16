"""`itertools`, as ordinary Python this compiler compiles.

COVERAGE: count, repeat, chain, islice, groupby, product, combinations.

NOT COVERED: accumulate, batched, chain.from_iterable, compress, cycle, dropwhile, filterfalse, pairwise,
permutations, starmap, takewhile, tee, zip_longest, combinations_with_replacement.

Restored from `archived/stdlib-prerefactor/` and measured against CPython by
`tests/stdlib/itertools.py`. The coverage line above is the module's contract; see
`docs/STDLIB.md`.

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
    """`islice(it, stop)`, `islice(it, start, stop)` and a STEP.

    Counted rather than indexed: the argument may be an iterator with no
    length and no way back, which is the case the whole module is for.

    The third bound was accepted and IGNORED, so `islice(xs, 1, 6, 2)` gave
    every element rather than every second one -- a wrong answer that looks
    like a right one until the count is checked.
    """
    if len(bounds) == 1:
        start = 0
        stop = bounds[0]
        step = 1
    else:
        start = bounds[0]
        stop = bounds[1]
        step = bounds[2] if len(bounds) > 2 and bounds[2] is not None else 1
    if step < 1:
        raise ValueError("Step for islice() must be a positive integer or None.")
    # `stop=None` MEANS TO THE END, which is how `islice(it, 2, None)` is
    # spelled and is not the same as stopping at zero.
    i = 0
    for item in iterable:
        if stop is not None and i >= stop:
            return
        if i >= start and (i - start) % step == 0:
            yield item
        i = i + 1


def groupby(iterable, key=None):
    """Consecutive runs of equal elements, as (key, group) pairs.

    CONSECUTIVE, not sorted: `groupby` finds runs, and a caller that wants one
    group per distinct value sorts first.

    `key` DECIDES THE RUN AND IS WHAT IS YIELDED as the first element of each
    pair, while the group holds the ORIGINAL items -- `groupby(words, len)`
    groups by length and hands back the words. The group is a LIST here rather than
    a lazy sub-iterator, so a caller may hold on to it after moving on -- which
    CPython's does not allow and no program should rely on either way.
    """
    items = list(iterable)
    i = 0
    while i < len(items):
        # THE KEY DECIDES THE RUN and is what is yielded; the group holds the
        # ORIGINAL items. The local was called `key` too, which shadowed the
        # parameter -- so passing one was accepted and ignored.
        mark = items[i] if key is None else key(items[i])
        run = []
        while i < len(items):
            here = items[i] if key is None else key(items[i])
            if here != mark:
                break
            run.append(items[i])
            i = i + 1
        yield (mark, run)


def product(*iterables, repeat=1):
    """The cartesian product, rightmost varying fastest.

    `repeat` REPEATS THE WHOLE ARGUMENT LIST, so `product("ab", repeat=2)` is
    `product("ab", "ab")` -- which is how the common `repeat=n` spelling of a
    fixed-width product is written, and it was accepted as a keyword nobody
    declared before.
    """
    rows = [list(one) for one in iterables] * repeat
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
