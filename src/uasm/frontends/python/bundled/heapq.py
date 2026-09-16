"""`heapq`, as ordinary Python this compiler compiles.

COVERAGE: heappush, heappop, heappushpop, heapify, heapreplace, merge (with
key= and reverse=), nlargest, nsmallest (both with an optional key=) -- which
is the module's whole documented surface except the 3.14 max-heap family
(heapq_max's heappush_max and friends), which is NOT covered.

Restored from `archived/stdlib-prerefactor/` and measured against CPython by
`tests/stdlib/heapq.py`. The coverage line above is the module's contract;
see `docs/STDLIB.md`.

A heap here is an ordinary list obeying one invariant, 0-indexed: `a[k] <=
a[2*k+1]` and `a[k] <= a[2*k+2]` for every k. `_siftdown` and `_siftup` are
CPython's own algorithm for restoring that invariant after one element moves
-- not a call to `sorted()` that happens to produce the same top-level
answer, which would make `heapreplace`/`heappushpop` no cheaper than a plain
`heappush` followed by a `heappop`, and would give `heapify` the wrong
COMPLEXITY (`sorted()` is O(n log n); real `heapify` is O(n)) even where it
gave the right answer.

`merge` is a genuine k-way merge: a small heap holding one buffered item per
input, refilled one item at a time only once its buffered item has been
yielded and the caller asks for the next one. It is deliberately NOT
`sorted(itertools.chain(*iterables))`, which would read every input to
completion before producing anything -- CPython's own docs say `merge` is
meant for inputs too large to hold in memory all at once, and
`tests/stdlib/heapq.py` checks the laziness directly rather than trusting the
docstring.
"""


def heappush(heap, item):
    """Push item onto heap, maintaining the heap invariant."""
    heap.append(item)
    _siftdown(heap, 0, len(heap) - 1)


def heappop(heap):
    """Pop and return the smallest item off the heap, maintaining the heap
    invariant. Raises IndexError on an empty heap, from the plain list
    operations underneath -- there is no separate check to duplicate."""
    lastelt = heap.pop()
    if heap:
        returnitem = heap[0]
        heap[0] = lastelt
        _siftup(heap, 0)
        return returnitem
    return lastelt


def heapreplace(heap, item):
    """Pop and return the smallest item, then push item -- ATOMICALLY, in
    the sense that the heap is never seen with `item` and the old smallest
    both present or both absent. Cheaper than `heappop` followed by
    `heappush`: one sift instead of two, because the slot the old smallest
    left is filled immediately rather than by the last element first.

    The returned value may be LARGER than item; heapq never promises the
    heap shrinks, only that it stays a valid heap of the same size.
    """
    returnitem = heap[0]
    heap[0] = item
    _siftup(heap, 0)
    return returnitem


def heappushpop(heap, item):
    """Push item, then pop and return the smallest -- cheaper than the two
    calls separately when item may itself be the smallest, since then
    nothing about the heap needs to move at all."""
    if heap and heap[0] < item:
        item, heap[0] = heap[0], item
        _siftup(heap, 0)
    return item


def heapify(x):
    """Rearrange a list into heap order, in place, in O(len(x)) time.

    Bottom-up: every index past the last one with a child is already a
    one-element heap by itself, so `_siftup` only has real work to do from
    `len(x) // 2 - 1` down to 0.
    """
    n = len(x)
    for i in reversed(range(n // 2)):
        _siftup(x, i)


def _siftdown(heap, startpos, pos):
    """`heap[pos]` may be too small for its place; walk it toward the root,
    one parent at a time, stopping as soon as it is not smaller than the
    parent it would displace."""
    newitem = heap[pos]
    while pos > startpos:
        parentpos = (pos - 1) // 2
        parent = heap[parentpos]
        if newitem < parent:
            heap[pos] = parent
            pos = parentpos
            continue
        break
    heap[pos] = newitem


def _siftup(heap, pos):
    """`heap[pos]` may be too large for its place, with both its children
    already valid heaps. Bubble the smaller child up repeatedly until
    reaching a leaf, then let `_siftdown` walk the displaced element back
    down from there -- which needs only a comparison per level rather than
    two, and is why this is not simply `_siftdown` run from the top."""
    endpos = len(heap)
    startpos = pos
    newitem = heap[pos]
    childpos = 2 * pos + 1
    while childpos < endpos:
        rightpos = childpos + 1
        if rightpos < endpos and not heap[childpos] < heap[rightpos]:
            childpos = rightpos
        heap[pos] = heap[childpos]
        pos = childpos
        childpos = 2 * pos + 1
    heap[pos] = newitem
    _siftdown(heap, startpos, pos)


# Max-heap mirrors of the four functions above, comparisons flipped. NOT
# part of this module's public surface -- CPython 3.14 exposes these as
# heapq_max's heappush_max/heappop_max/heapify_max/heapreplace_max, which
# this module does NOT cover -- they exist here only as the private engine
# `merge(reverse=True)` runs on, exactly as CPython's own `merge` runs its
# reverse case on its (public) max-heap family.
def _siftdown_max(heap, startpos, pos):
    newitem = heap[pos]
    while pos > startpos:
        parentpos = (pos - 1) // 2
        parent = heap[parentpos]
        if parent < newitem:
            heap[pos] = parent
            pos = parentpos
            continue
        break
    heap[pos] = newitem


def _siftup_max(heap, pos):
    endpos = len(heap)
    startpos = pos
    newitem = heap[pos]
    childpos = 2 * pos + 1
    while childpos < endpos:
        rightpos = childpos + 1
        if rightpos < endpos and not heap[rightpos] < heap[childpos]:
            childpos = rightpos
        heap[pos] = heap[childpos]
        pos = childpos
        childpos = 2 * pos + 1
    heap[pos] = newitem
    _siftdown_max(heap, startpos, pos)


def _heapify_max(x):
    n = len(x)
    for i in reversed(range(n // 2)):
        _siftup_max(x, i)


def _heappop_max(heap):
    lastelt = heap.pop()
    if heap:
        returnitem = heap[0]
        heap[0] = lastelt
        _siftup_max(heap, 0)
        return returnitem
    return lastelt


def _heapreplace_max(heap, item):
    returnitem = heap[0]
    heap[0] = item
    _siftup_max(heap, 0)
    return returnitem


def merge(*iterables, key=None, reverse=False):
    """Merge already-sorted iterables into one sorted generator.

    A genuine k-way merge, LAZY in the input: each iterable contributes one
    buffered item to a small heap (one entry per iterable, not per element),
    and pulling the next one only happens once the current one has been
    yielded and the caller comes back for another -- so an infinite or
    too-large-for-memory input works as long as the caller does not ask for
    everything at once, matching what CPython documents for this function.

    Each heap entry carries the INDEX of its iterable alongside the
    comparison key, purely to break ties deterministically -- among equal
    keys, the earlier iterable's item comes first, and every entry's index
    is unique so a tie is always settled there without ever comparing two
    raw values (which may not support `<` at all, only the key does).
    """
    entries = []
    order = 0
    for iterable in iterables:
        it = iter(iterable)
        try:
            value = next(it)
        except StopIteration:
            pass
        else:
            rank = value if key is None else key(value)
            tag = -order if reverse else order
            entries.append([rank, tag, value, it])
        order = order + 1

    if reverse:
        _heapify_max(entries)
    else:
        heapify(entries)

    while entries:
        rank, tag, value, it = entries[0]
        yield value
        try:
            new_value = next(it)
        except StopIteration:
            if reverse:
                _heappop_max(entries)
            else:
                heappop(entries)
        else:
            new_rank = new_value if key is None else key(new_value)
            new_entry = [new_rank, tag, new_value, it]
            if reverse:
                _heapreplace_max(entries, new_entry)
            else:
                heapreplace(entries, new_entry)


def nsmallest(n, iterable, key=None):
    """The n smallest elements of iterable, as a list, smallest first.

    Equivalent to `sorted(iterable, key=key)[:n]`, but never holds more than
    n elements: a max-heap of "the n smallest seen so far", so a new element
    only has to beat the current WORST of them (the root) to earn a place,
    at the cost of one comparison and one sift rather than a full re-sort.

    Each candidate is tagged with the order it was seen in, for the same
    reason `merge` tags its entries: to guarantee every tie is settled
    without falling back to comparing raw elements, so `key=` works even on
    values with no `<` of their own -- dicts, say.
    """
    it = iter(iterable)
    result = []
    order = 0
    while order < n:
        try:
            elem = next(it)
        except StopIteration:
            break
        rank = elem if key is None else key(elem)
        result.append((rank, order, elem))
        order = order + 1
    if not result:
        return []
    _heapify_max(result)
    top = result[0][0]
    for elem in it:
        rank = elem if key is None else key(elem)
        if rank < top:
            _heapreplace_max(result, (rank, order, elem))
            top = result[0][0]
            order = order + 1
    result.sort()
    return [entry[2] for entry in result]


def nlargest(n, iterable, key=None):
    """The n largest elements of iterable, as a list, largest first.

    The mirror of `nsmallest`: a min-heap of "the n largest seen so far", so
    a new element only has to beat the current worst of them (the root, now
    the smallest) to earn a place. The order tag is NEGATED rather than
    plain, so that the final descending sort -- which has to be descending
    on the whole entry to keep the heap and the sort using one consistent
    order -- still breaks ties by EARLIEST seen first, matching what
    `sorted(iterable, key=key, reverse=True)[:n]` promises: reverse flips
    the comparison, not which of two equal elements came first.
    """
    it = iter(iterable)
    result = []
    order = 0
    while order < n:
        try:
            elem = next(it)
        except StopIteration:
            break
        rank = elem if key is None else key(elem)
        result.append((rank, -order, elem))
        order = order + 1
    if not result:
        return []
    heapify(result)
    top = result[0][0]
    for elem in it:
        rank = elem if key is None else key(elem)
        if top < rank:
            heapreplace(result, (rank, -order, elem))
            top = result[0][0]
            order = order + 1
    result.sort(reverse=True)
    return [entry[2] for entry in result]
