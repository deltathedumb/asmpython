"""queue module: synchronized queue classes.

Provides FIFO, LIFO, and priority queues. In asmpython these are
single-threaded unless used with the threading module. put()/get()
ignore the `block` and `timeout` parameters (non-blocking by default).

task_done() / join() are implemented with an unfinished-task counter.
join() busy-waits until all tasks are marked done; in a single-threaded
program it will block forever if task_done() is never called, matching
CPython's join() behaviour (just without OS-level blocking primitives).
"""
from __future__ import annotations


class Empty(Exception):
    """Raised when get() is called on an empty queue."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return "Empty: " + self.msg


class Full(Exception):
    """Raised when put() is called on a full queue (maxsize > 0)."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return "Full: " + self.msg


class Queue:
    """A FIFO queue."""

    def __init__(self, maxsize: int = 0) -> None:
        self.maxsize: int = maxsize
        self._data: list = []
        self._unfinished: int = 0

    def qsize(self) -> int:
        """Return the approximate number of items in the queue."""
        return len(self._data)

    def empty(self) -> int:
        """Return 1 if the queue is empty, 0 otherwise."""
        return 1 if len(self._data) == 0 else 0

    def full(self) -> int:
        """Return 1 if the queue is full, 0 otherwise."""
        if self.maxsize <= 0:
            return 0
        return 1 if len(self._data) >= self.maxsize else 0

    def put(self, item: int, block: int = 1, timeout: int = -1) -> None:
        """Put an item into the queue.

        Raises Full if maxsize is set and the queue is already full.
        (block and timeout are ignored in the single-threaded implementation.)
        """
        if self.maxsize > 0 and len(self._data) >= self.maxsize:
            raise Full("queue is full")
        self._data.append(item)
        self._unfinished = self._unfinished + 1

    def put_nowait(self, item: int) -> None:
        """Put an item into the queue without blocking (raises Full if full)."""
        self.put(item, 0)

    def get(self, block: int = 1, timeout: int = -1) -> int:
        """Remove and return an item from the queue.

        Raises Empty if the queue is empty.
        """
        if len(self._data) == 0:
            raise Empty("queue is empty")
        item: int = self._data[0]
        new_data: list = []
        i: int = 1
        while i < len(self._data):
            new_data.append(self._data[i])
            i = i + 1
        self._data = new_data
        return item

    def get_nowait(self) -> int:
        """Remove and return an item from the queue without blocking."""
        return self.get(0)

    def task_done(self) -> None:
        """Indicate that a formerly enqueued task is complete.

        For each item that is get()ted from the queue, call task_done() once
        to inform the queue that the item is processed. When the count of
        unfinished tasks drops to zero, join() unblocks.
        """
        if self._unfinished <= 0:
            raise ValueError("task_done() called more times than put()")
        self._unfinished = self._unfinished - 1

    def join(self) -> None:
        """Block until all items in the queue have been got and task_done() called.

        In asmpython (single-threaded) this is a spin-wait. When run within
        a thread, it will yield execution correctly as long as other threads
        call task_done(). For single-threaded use, ensure all tasks are done
        before calling join() to avoid an infinite busy loop.
        """
        while self._unfinished > 0:
            pass


class LifoQueue(Queue):
    """A LIFO queue (stack)."""

    def __init__(self, maxsize: int = 0) -> None:
        self.maxsize: int = maxsize
        self._data: list = []
        self._unfinished: int = 0

    def get(self, block: int = 1, timeout: int = -1) -> int:
        if len(self._data) == 0:
            raise Empty("queue is empty")
        n: int = len(self._data)
        item: int = self._data[n - 1]
        new_data: list = []
        i: int = 0
        while i < n - 1:
            new_data.append(self._data[i])
            i = i + 1
        self._data = new_data
        return item


class PriorityQueue(Queue):
    """A priority queue (min-heap)."""

    def __init__(self, maxsize: int = 0) -> None:
        self.maxsize: int = maxsize
        self._data: list = []
        self._unfinished: int = 0

    def put(self, item: int, block: int = 1, timeout: int = -1) -> None:
        if self.maxsize > 0 and len(self._data) >= self.maxsize:
            raise Full("queue is full")
        self._data.append(item)
        self._unfinished = self._unfinished + 1
        # Sift up.
        i: int = len(self._data) - 1
        while i > 0:
            parent: int = (i - 1) // 2
            if self._data[parent] > self._data[i]:
                tmp: int = self._data[parent]
                self._data[parent] = self._data[i]
                self._data[i] = tmp
                i = parent
            else:
                break

    def get(self, block: int = 1, timeout: int = -1) -> int:
        n: int = len(self._data)
        if n == 0:
            raise Empty("queue is empty")
        item: int = self._data[0]
        self._data[0] = self._data[n - 1]
        new_data: list = []
        i: int = 0
        while i < n - 1:
            new_data.append(self._data[i])
            i = i + 1
        self._data = new_data
        n = n - 1
        # Sift down.
        i = 0
        while 1:
            left: int = 2 * i + 1
            right: int = 2 * i + 2
            smallest: int = i
            if left < n and self._data[left] < self._data[smallest]:
                smallest = left
            if right < n and self._data[right] < self._data[smallest]:
                smallest = right
            if smallest == i:
                break
            tmp2: int = self._data[i]
            self._data[i] = self._data[smallest]
            self._data[smallest] = tmp2
            i = smallest
        return item


class SimpleQueue:
    """A simple unbounded FIFO queue (no task tracking)."""

    def __init__(self) -> None:
        self._data: list = []

    def qsize(self) -> int:
        return len(self._data)

    def empty(self) -> int:
        return 1 if len(self._data) == 0 else 0

    def put(self, item: int, block: int = 1, timeout: int = -1) -> None:
        self._data.append(item)

    def put_nowait(self, item: int) -> None:
        self._data.append(item)

    def get(self, block: int = 1, timeout: int = -1) -> int:
        if len(self._data) == 0:
            raise Empty("queue is empty")
        item: int = self._data[0]
        new_data: list = []
        i: int = 1
        while i < len(self._data):
            new_data.append(self._data[i])
            i = i + 1
        self._data = new_data
        return item

    def get_nowait(self) -> int:
        return self.get(0)
