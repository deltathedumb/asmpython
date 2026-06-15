"""threading module: thread-based parallelism stubs.

asmpython is single-threaded; threading primitives are provided as stubs
so that code importing threading compiles without errors. Lock/Event/etc.
are no-op implementations.
"""
from __future__ import annotations


_thread_count: int = 0


def current_thread() -> int:
    """Return a stub thread identifier (0 = main thread)."""
    return 0


def main_thread() -> int:
    """Return the main thread identifier."""
    return 0


def active_count() -> int:
    """Return number of active threads (always 1 in asmpython)."""
    return 1


def enumerate() -> list:
    """Return list of all active threads."""
    return [0]


def get_ident() -> int:
    """Return identifier of current thread."""
    return 0


class Lock:
    """A non-recursive lock (no-op in asmpython)."""

    def __init__(self) -> None:
        self._locked: int = 0

    def acquire(self, blocking: int = 1, timeout: int = -1) -> int:
        self._locked = 1
        return 1

    def release(self) -> None:
        self._locked = 0

    def locked(self) -> int:
        return self._locked

    def __enter__(self) -> int:
        self.acquire()
        return 1

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.release()
        return 0


class RLock(Lock):
    """A reentrant lock (no-op stub)."""
    pass


class Event:
    """A synchronization primitive (stub)."""

    def __init__(self) -> None:
        self._flag: int = 0

    def is_set(self) -> int:
        return self._flag

    def set(self) -> None:
        self._flag = 1

    def clear(self) -> None:
        self._flag = 0

    def wait(self, timeout: int = -1) -> int:
        return self._flag


class Semaphore:
    """A semaphore (stub, always available)."""

    def __init__(self, value: int = 1) -> None:
        self._value: int = value

    def acquire(self, blocking: int = 1, timeout: int = -1) -> int:
        if self._value > 0:
            self._value = self._value - 1
            return 1
        return 0

    def release(self) -> None:
        self._value = self._value + 1

    def __enter__(self) -> int:
        self.acquire()
        return 1

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.release()
        return 0


class BoundedSemaphore(Semaphore):
    """A semaphore with an upper bound (stub)."""
    pass


class Condition:
    """A condition variable (stub)."""

    def __init__(self, lock: int = 0) -> None:
        self._lock: int = lock
        self._waiters: int = 0

    def acquire(self) -> int:
        return 1

    def release(self) -> None:
        pass

    def wait(self, timeout: int = -1) -> int:
        return 1

    def notify(self, n: int = 1) -> None:
        pass

    def notify_all(self) -> None:
        pass

    def __enter__(self) -> int:
        self.acquire()
        return 1

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.release()
        return 0


class Barrier:
    """A barrier primitive (stub)."""

    def __init__(self, parties: int, timeout: int = -1) -> None:
        self.parties: int = parties
        self._count: int = 0

    def wait(self, timeout: int = -1) -> int:
        self._count = self._count + 1
        if self._count >= self.parties:
            self._count = 0
        return 0

    def reset(self) -> None:
        self._count = 0

    def abort(self) -> None:
        pass


class Timer:
    """Call a function after a delay (stub — runs immediately in asmpython)."""

    def __init__(self, interval: float, function: int, args: list = []) -> None:
        self.interval: float = interval
        self.function: int = function
        self.args: list = args
        self.daemon: int = 0

    def start(self) -> None:
        pass

    def cancel(self) -> None:
        pass

    def join(self, timeout: int = -1) -> None:
        pass

    def is_alive(self) -> int:
        return 0


class Thread:
    """A thread (stub — runs target immediately on start() in asmpython)."""

    def __init__(self, target: int = 0, name: str = "",
                 args: list = [], daemon: int = 0) -> None:
        self.target: int = target
        self.name: str = name
        self.args: list = args
        self.daemon: int = daemon
        self._alive: int = 0

    def start(self) -> None:
        self._alive = 1

    def join(self, timeout: int = -1) -> None:
        self._alive = 0

    def is_alive(self) -> int:
        return self._alive

    def run(self) -> None:
        pass
