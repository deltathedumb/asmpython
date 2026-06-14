"""heapq module: heap queue algorithm (min-heap).

Provides a min-heap implemented on top of a list. The heap invariant is
maintained by sift-up and sift-down operations. All operations are O(log n).
"""
from __future__ import annotations


def _siftdown(heap: list, startpos: int, pos: int) -> None:
    """Sift item at pos up to restore heap invariant."""
    newitem: int = heap[pos]
    while pos > startpos:
        parentpos: int = (pos - 1) >> 1
        parent: int = heap[parentpos]
        if newitem < parent:
            heap[pos] = parent
            pos = parentpos
            continue
        break
    heap[pos] = newitem


def _siftup(heap: list, pos: int) -> None:
    """Sift item at pos down to restore heap invariant."""
    endpos: int = len(heap)
    startpos: int = pos
    newitem: int = heap[pos]
    childpos: int = 2 * pos + 1
    while childpos < endpos:
        rightpos: int = childpos + 1
        if rightpos < endpos and heap[rightpos] < heap[childpos]:
            childpos = rightpos
        heap[pos] = heap[childpos]
        pos = childpos
        childpos = 2 * pos + 1
    heap[pos] = newitem
    _siftdown(heap, startpos, pos)


def heappush(heap: list, item: int) -> None:
    """Push item onto heap, maintaining heap invariant."""
    heap.append(item)
    _siftdown(heap, 0, len(heap) - 1)


def heappop(heap: list) -> int:
    """Pop the smallest item off the heap, maintaining heap invariant."""
    lastelt: int = heap[len(heap) - 1]
    heap.pop()
    if len(heap) > 0:
        returnitem: int = heap[0]
        heap[0] = lastelt
        _siftup(heap, 0)
        return returnitem
    return lastelt


def heappushpop(heap: list, item: int) -> int:
    """Push item then pop and return the smallest item from heap."""
    if len(heap) > 0 and heap[0] < item:
        tmp: int = heap[0]
        heap[0] = item
        _siftup(heap, 0)
        return tmp
    return item


def heapreplace(heap: list, item: int) -> int:
    """Pop and return the smallest item, then push the new item."""
    returnitem: int = heap[0]
    heap[0] = item
    _siftup(heap, 0)
    return returnitem


def heapify(heap: list) -> None:
    """Transform list into a heap, in-place, in O(n) time."""
    n: int = len(heap)
    i: int = (n >> 1) - 1
    while i >= 0:
        _siftup(heap, i)
        i = i - 1


def nsmallest(n: int, iterable: list) -> list:
    """Return the n smallest elements of the list."""
    h: list = []
    i: int = 0
    while i < len(iterable):
        h.append(iterable[i])
        i = i + 1
    heapify(h)
    result: list = []
    count: int = 0
    while count < n and len(h) > 0:
        result.append(heappop(h))
        count = count + 1
    return result


def nlargest(n: int, iterable: list) -> list:
    """Return the n largest elements of the list."""
    h: list = []
    i: int = 0
    while i < len(iterable):
        h.append(iterable[i])
        i = i + 1
    heapify(h)
    total: int = len(h)
    result: list = []
    count: int = 0
    while count < n and count < total:
        result.append(h[total - 1 - count])
        count = count + 1
    return result


def merge(*iterables) -> list:
    """Merge sorted iterables into a single sorted list."""
    result: list = []
    i: int = 0
    while i < len(iterables):
        j: int = 0
        src: list = iterables[i]
        while j < len(src):
            result.append(src[j])
            j = j + 1
        i = i + 1
    heapify(result)
    out: list = []
    while len(result) > 0:
        out.append(heappop(result))
    return out
