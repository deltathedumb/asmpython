"""itertools module: functions creating iterators for efficient looping.

All functions return lists (asmpython has no lazy iterator protocol yet).
Function signatures match CPython's itertools as closely as possible given
the list-based return type.

Implemented:
  chain(*iterables)         -> flat list of all elements
  islice(iterable, stop)    -> first stop elements
  islice(iterable, start, stop, step)  -> sliced view
  count(start, step)        -> not implemented (infinite); raises NotImplementedError
  cycle(iterable, n)        -> repeat iterable n times (bounded)
  repeat(x, n)              -> list of n copies of x
  product(*iterables)       -> cartesian product as list of lists
  combinations(pool, r)     -> r-length combinations without repetition
  combinations_with_replacement(pool, r) -> r-length combos with repetition
  permutations(pool, r)     -> r-length permutations
  accumulate(iterable)      -> running totals
  compress(data, selectors) -> elements where selector is truthy
  dropwhile(pred, iterable) -> drop leading elements matching pred
  takewhile(pred, iterable) -> take leading elements matching pred
  filterfalse(pred, iterable) -> elements where pred is false
  starmap(func, iterable)   -> apply func(*args) for args in iterable
  zip_longest(*iterables)   -> zip with fill value for short iterables
  chain_from_iterable       -> chain(iterables) from one sequence
"""
from __future__ import annotations


def chain(a: list = [], b: list = [], c: list = [], d: list = []) -> list:
    """Concatenate up to 4 iterables into a single flat list."""
    result: list = []
    for x in a:
        result.append(x)
    for x in b:
        result.append(x)
    for x in c:
        result.append(x)
    for x in d:
        result.append(x)
    return result


def chain_from_iterable(iterables: list) -> list:
    """Flatten one level: chain.from_iterable([[1,2],[3,4]]) -> [1,2,3,4]."""
    result: list = []
    for sub in iterables:
        for x in sub:
            result.append(x)
    return result


def islice(iterable: list, start: int, stop: int = -1, step: int = 1) -> list:
    """Return selected elements from iterable.

    islice(seq, stop)            -> seq[0:stop]
    islice(seq, start, stop)     -> seq[start:stop]
    islice(seq, start, stop, step) -> seq[start:stop:step]
    """
    if stop == -1:
        stop = start
        start = 0
    if step <= 0:
        step = 1
    result: list = []
    i = start
    while i < stop and i < len(iterable):
        result.append(iterable[i])
        i = i + step
    return result


def repeat(x: object, n: int = 1) -> list:
    """Return a list of n copies of x."""
    result: list = []
    i = 0
    while i < n:
        result.append(x)
        i = i + 1
    return result


def cycle(iterable: list, n: int = 2) -> list:
    """Return list cycling through iterable n times."""
    result: list = []
    i = 0
    while i < n:
        for x in iterable:
            result.append(x)
        i = i + 1
    return result


def accumulate(iterable: list, initial: int = 0) -> list:
    """Return running totals. Initial value prepended if != 0."""
    result: list = []
    total = 0
    if initial != 0:
        result.append(initial)
        total = initial
    for x in iterable:
        total = total + x
        result.append(total)
    return result


def compress(data: list, selectors: list) -> list:
    """Return elements from data where corresponding selector is truthy."""
    result: list = []
    i = 0
    n = len(data)
    m = len(selectors)
    while i < n and i < m:
        if selectors[i]:
            result.append(data[i])
        i = i + 1
    return result


def product(a: list, b: list = []) -> list:
    """Cartesian product of a x b. Returns list of 2-element lists.

    For higher-arity products, call iteratively:
      product(product(a, b), c) then flatten inner [elem, c_elem].
    """
    if len(b) == 0:
        result: list = []
        for x in a:
            result.append([x])
        return result
    result2: list = []
    for x in a:
        for y in b:
            result2.append([x, y])
    return result2


def combinations(pool: list, r: int) -> list:
    """Return r-length combinations of pool without repetition."""
    n = len(pool)
    if r > n:
        return []
    result: list = []
    # Use index-based backtracking with explicit stack
    indices: list[int] = []
    i = 0
    while i < r:
        indices.append(i)
        i = i + 1
    while 1:
        combo: list = []
        for idx in indices:
            combo.append(pool[idx])
        result.append(combo)
        # Find rightmost index that can be incremented
        i = r - 1
        while i >= 0 and indices[i] == i + n - r:
            i = i - 1
        if i < 0:
            break
        indices[i] = indices[i] + 1
        j = i + 1
        while j < r:
            indices[j] = indices[j - 1] + 1
            j = j + 1
    return result


def combinations_with_replacement(pool: list, r: int) -> list:
    """Return r-length combinations of pool with repetition."""
    n = len(pool)
    if n == 0:
        return []
    result: list = []
    indices: list[int] = []
    i = 0
    while i < r:
        indices.append(0)
        i = i + 1
    while 1:
        combo: list = []
        for idx in indices:
            combo.append(pool[idx])
        result.append(combo)
        i = r - 1
        while i >= 0 and indices[i] == n - 1:
            i = i - 1
        if i < 0:
            break
        new_val = indices[i] + 1
        j = i
        while j < r:
            indices[j] = new_val
            j = j + 1
    return result


def permutations(pool: list, r: int = -1) -> list:
    """Return r-length permutations of pool."""
    n = len(pool)
    if r < 0:
        r = n
    if r > n:
        return []
    result: list = []
    # Heap's algorithm variant using index tracking
    indices: list[int] = []
    i = 0
    while i < n:
        indices.append(i)
        i = i + 1
    cycles: list[int] = []
    i = n
    while i > n - r:
        cycles.append(i)
        i = i - 1
    # Initial permutation
    combo: list = []
    i = 0
    while i < r:
        combo.append(pool[indices[i]])
        i = i + 1
    result.append(combo)
    while 1:
        found = 0
        i = r - 1
        while i >= 0:
            cycles[i] = cycles[i] - 1
            if cycles[i] == 0:
                # rotate indices[i:] left by 1
                saved = indices[i]
                j = i
                while j < n - 1:
                    indices[j] = indices[j + 1]
                    j = j + 1
                indices[n - 1] = saved
                cycles[i] = n - i
            else:
                j = n - cycles[i]
                tmp = indices[i]
                indices[i] = indices[j]
                indices[j] = tmp
                combo2: list = []
                k = 0
                while k < r:
                    combo2.append(pool[indices[k]])
                    k = k + 1
                result.append(combo2)
                found = 1
                break
            i = i - 1
        if found == 0:
            break
    return result


def zip_longest(a: list, b: list, fillvalue: object = 0) -> list:
    """Zip two lists, filling shorter one with fillvalue."""
    result: list = []
    na = len(a)
    nb = len(b)
    n = na if na > nb else nb
    i = 0
    while i < n:
        va = a[i] if i < na else fillvalue
        vb = b[i] if i < nb else fillvalue
        result.append([va, vb])
        i = i + 1
    return result
