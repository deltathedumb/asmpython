"""sched module: event scheduler."""
from __future__ import annotations
import time


class Event:
    """A scheduled event."""

    def __init__(self, time: float, priority: int, sequence: int,
                 action: int, argument: list, kwargs: dict) -> None:
        self.time: float = time
        self.priority: int = priority
        self.sequence: int = sequence
        self.action: int = action
        self.argument: list = argument
        self.kwargs: dict = kwargs

    def __lt__(self, other: "Event") -> int:
        if self.time < other.time:
            return 1
        if self.time > other.time:
            return 0
        if self.priority < other.priority:
            return 1
        if self.priority > other.priority:
            return 0
        return 1 if self.sequence < other.sequence else 0

    def __le__(self, other: "Event") -> int:
        return 1 if not other.__lt__(self) else 0

    def __eq__(self, other: "Event") -> int:
        return 1 if (self.time == other.time and
                     self.priority == other.priority and
                     self.sequence == other.sequence) else 0

    def __gt__(self, other: "Event") -> int:
        return other.__lt__(self)

    def __ge__(self, other: "Event") -> int:
        return 1 if not self.__lt__(other) else 0


class scheduler:
    """A general-purpose event scheduler."""

    def __init__(self, timefunc: int = 0, delayfunc: int = 0) -> None:
        self._queue: list[Event] = []
        self._sequence: int = 0

    def enter(self, delay: float, priority: int, action: int,
              argument: list = [], kwargs: dict = {}) -> Event:
        """Schedule an event to run after delay seconds."""
        t: float = time.monotonic() + delay
        return self.enterabs(t, priority, action, argument, kwargs)

    def enterabs(self, when: float, priority: int, action: int,
                 argument: list = [], kwargs: dict = {}) -> Event:
        """Schedule an event at absolute time when."""
        ev: Event = Event(when, priority, self._sequence, action, argument, kwargs)
        self._sequence = self._sequence + 1
        self._queue.append(ev)
        self._queue.sort()
        return ev

    def cancel(self, event: Event) -> None:
        """Remove an event from the queue."""
        new_queue: list[Event] = []
        for e in self._queue:
            if e is not event:
                new_queue.append(e)
        self._queue = new_queue

    def empty(self) -> int:
        """Return True if the queue is empty."""
        return 1 if len(self._queue) == 0 else 0

    def queue(self) -> list[Event]:
        """Return a list of upcoming events in order."""
        result: list[Event] = []
        for e in self._queue:
            result.append(e)
        return result
