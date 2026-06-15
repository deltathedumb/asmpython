"""queue module: synchronized queue classes.

Provides FIFO, LIFO, and priority queues. In asmpython these are
single-threaded, so the 'blocking' and 'timeout' parameters are ignored.
"""
from __future__ import annotations


class Empty(Exception):
    """Raised when get() is called on an empty queue."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return "Empty: " + self.msg


class Full(Exception):
    """Raised when put() is called on a full queue."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return "Full: " + self.msg


class Queue:
    """A FIFO queue."""

    def __init__(self, maxsize: int = 0) -> None:
        self.maxsize: int = maxsize
        self._data: list = []

    def qsize(self) -> int:
        return len(self._data)

    def empty(self) -> int:
        return 1 if len(self._data) == 0 else 0

    def full(self) -> int:
        if self.maxsize <= 0:
            return 0
        return 1 if len(self._data) >= self.maxsize else 0

    def put(self, item: int, block: int = 1, timeout: int = -1) -> None:
        """Put an item into the queue."""
        if self.maxsize > 0 and len(self._data) >= self.maxsize:
            return
        self._data.append(item)

    def put_nowait(self, item: int) -> None:
        self.put(item, 0)

    def get(self, block: int = 1, timeout: int = -1) -> int:
        """Remove and return an item from the queue."""
        if len(self._data) == 0:
            return 0
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

    def task_done(self) -> None:
        pass

    def join(self) -> None:
        pass


class LifoQueue(Queue):
    """A LIFO queue (stack)."""

    def __init__(self, maxsize: int = 0) -> None:
        self.maxsize: int = maxsize
        self._data: list = []

    def get(self, block: int = 1, timeout: int = -1) -> int:
        if len(self._data) == 0:
            return 0
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
    """A priority queue (min-heap by value)."""

    def __init__(self, maxsize: int = 0) -> None:
        self.maxsize: int = maxsize
        self._data: list = []

    def put(self, item: int, block: int = 1, timeout: int = -1) -> None:
        if self.maxsize > 0 and len(self._data) >= self.maxsize:
            return
        self._data.append(item)
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
            return 0
        item: int = self._data[0]
        self._data[0] = self._data[n - 1]
        new_data: list = []
        i: int = 0
        while i < n - 1:
            new_data.append(self._data[i])
            i = i + 1
        self._data = new_data
        n = n - 1
        i = 0
        while True:
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
    """A simple unbounded FIFO queue."""

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
            return 0
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
