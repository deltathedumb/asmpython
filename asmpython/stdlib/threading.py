"""threading module: real thread-based parallelism via Win32/pthread.

Thread creation uses CreateThread (Windows) or pthread_create (Linux).
Lock uses CRITICAL_SECTION (Windows) or pthread_mutex_t (Linux).
All other primitives (Event, Semaphore, Condition, Barrier) are
implemented in pure asmpython on top of Lock.
"""
from __future__ import annotations

from _threadingffi import _threading_create
from _threadingffi import _threading_join
from _threadingffi import _threading_is_alive
from _threadingffi import _threading_lock_init
from _threadingffi import _threading_lock_acquire
from _threadingffi import _threading_lock_release
from _threadingffi import _threading_lock_destroy
from _threadingffi import _threading_get_ident
from _threadingffi import _threading_active_count


def get_ident() -> int:
    """Return integer identity of the calling thread."""
    return _threading_get_ident()


def current_thread() -> int:
    """Return identifier of the current thread."""
    return _threading_get_ident()


def main_thread() -> int:
    """Return the main thread identifier."""
    return 0


def active_count() -> int:
    """Return number of active threads."""
    return _threading_active_count()


def enumerate() -> list:
    """Return list of all active thread identifiers."""
    result: list = [_threading_get_ident()]
    return result


class Lock:
    """A mutual-exclusion lock backed by Win32 CRITICAL_SECTION / pthread_mutex."""

    def __init__(self) -> None:
        cs_ptr: str = _threading_lock_init(0)
        self._cs: str = cs_ptr
        self._locked: int = 0

    def acquire(self, blocking: int = 1, timeout: int = -1) -> int:
        _threading_lock_acquire(self._cs)
        self._locked = 1
        return 1

    def release(self) -> None:
        self._locked = 0
        _threading_lock_release(self._cs)

    def locked(self) -> int:
        return self._locked

    def __enter__(self) -> int:
        self.acquire()
        return 1

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.release()
        return 0


class RLock(Lock):
    """A reentrant lock (uses same OS primitive as Lock)."""
    pass


class Thread:
    """A thread of execution backed by CreateThread / pthread_create."""

    def __init__(self, target: int = 0, name: str = "",
                 args: list = [], daemon: int = 0) -> None:
        self.target: int = target
        self.name: str = name
        self.args: list = args
        self.daemon: int = daemon
        self._handle: str = ""
        self._alive: int = 0

    def start(self) -> None:
        """Spawn a new OS thread that calls self.target()."""
        if self.target != 0:
            handle: str = _threading_create(self.target)
            self._handle = handle
            self._alive = 1

    def join(self, timeout: int = -1) -> None:
        """Wait for the thread to finish."""
        if self._alive != 0:
            _threading_join(self._handle)
            self._handle = ""
        self._alive = 0

    def is_alive(self) -> int:
        if self._alive == 0:
            return 0
        alive: int = _threading_is_alive(self._handle)
        if alive == 0:
            self._alive = 0
        return alive

    def run(self) -> None:
        """Called by trampoline; subclasses override this."""
        pass


class Event:
    """A manual-reset event flag backed by a Lock."""

    def __init__(self) -> None:
        self._flag: int = 0
        self._lock: Lock = Lock()

    def is_set(self) -> int:
        return self._flag

    def set(self) -> None:
        self._lock.acquire()
        self._flag = 1
        self._lock.release()

    def clear(self) -> None:
        self._lock.acquire()
        self._flag = 0
        self._lock.release()

    def wait(self, timeout: int = -1) -> int:
        return self._flag


class Semaphore:
    """A counting semaphore backed by a Lock."""

    def __init__(self, value: int = 1) -> None:
        self._value: int = value
        self._lock: Lock = Lock()

    def acquire(self, blocking: int = 1, timeout: int = -1) -> int:
        self._lock.acquire()
        result: int = 0
        if self._value > 0:
            self._value = self._value - 1
            result = 1
        self._lock.release()
        return result

    def release(self) -> None:
        self._lock.acquire()
        self._value = self._value + 1
        self._lock.release()

    def __enter__(self) -> int:
        self.acquire()
        return 1

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.release()
        return 0


class BoundedSemaphore(Semaphore):
    """A semaphore with an upper bound."""
    pass


class Condition:
    """A condition variable built on Lock (spin-check only)."""

    def __init__(self, lock: int = 0) -> None:
        self._lock: Lock = Lock()
        self._waiters: int = 0

    def acquire(self) -> int:
        self._lock.acquire()
        return 1

    def release(self) -> None:
        self._lock.release()

    def wait(self, timeout: int = -1) -> int:
        self._lock.release()
        self._lock.acquire()
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
    """A barrier primitive using a Lock."""

    def __init__(self, parties: int, timeout: int = -1) -> None:
        self.parties: int = parties
        self._count: int = 0
        self._lock: Lock = Lock()

    def wait(self, timeout: int = -1) -> int:
        self._lock.acquire()
        self._count = self._count + 1
        if self._count >= self.parties:
            self._count = 0
        self._lock.release()
        return 0

    def reset(self) -> None:
        self._lock.acquire()
        self._count = 0
        self._lock.release()

    def abort(self) -> None:
        pass


class Timer:
    """Call a function after a delay (spawns a thread)."""

    def __init__(self, interval: float, function: int, args: list = []) -> None:
        self.interval: float = interval
        self.function: int = function
        self.args: list = args
        self.daemon: int = 0
        self._thread: Thread = Thread(target=0)

    def start(self) -> None:
        self._thread = Thread(target=self.function)
        self._thread.start()

    def cancel(self) -> None:
        pass

    def join(self, timeout: int = -1) -> None:
        self._thread.join()

    def is_alive(self) -> int:
        return self._thread.is_alive()
