"""concurrent.futures: high-level interface for asynchronous execution.

Provides ThreadPoolExecutor backed by asmpython's threading module.
ProcessPoolExecutor raises NotImplementedError (no fork/spawn in asmpython).

Usage::

    from concurrent.futures import ThreadPoolExecutor, as_completed

    def work(x):
        return x * x

    with ThreadPoolExecutor(max_workers=4) as ex:
        futures = [ex.submit(work, i) for i in range(10)]
        for f in as_completed(futures):
            print(f.result())
"""
from __future__ import annotations

import threading as _threading
import time as _time

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

FIRST_COMPLETED: str = "FIRST_COMPLETED"
FIRST_EXCEPTION: str = "FIRST_EXCEPTION"
ALL_COMPLETED: str = "ALL_COMPLETED"

_PENDING: str = "PENDING"
_RUNNING: str = "RUNNING"
_CANCELLED: str = "CANCELLED"
_CANCELLED_AND_NOTIFIED: str = "CANCELLED_AND_NOTIFIED"
_FINISHED: str = "FINISHED"


class CancelledError(Exception):
    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return "CancelledError"


class TimeoutError(Exception):
    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return "TimeoutError"


class BrokenExecutor(RuntimeError):
    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg


class InvalidStateError(Exception):
    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg


# ---------------------------------------------------------------------------
# Future
# ---------------------------------------------------------------------------

class Future:
    """Represents a deferred computation result."""

    def __init__(self) -> None:
        self._state: str = _PENDING
        self._result: object = None
        self._exception: str = ""  # exception message
        self._has_exception: int = 0
        self._callbacks: list = []
        self._lock: _threading.Lock = _threading.Lock()

    def cancel(self) -> int:
        """Attempt to cancel the future. Returns True if successful."""
        self._lock.acquire()
        if self._state == _PENDING or self._state == _RUNNING:
            self._state = _CANCELLED
            self._lock.release()
            return 1
        self._lock.release()
        return 0

    def cancelled(self) -> int:
        return self._state in (_CANCELLED, _CANCELLED_AND_NOTIFIED)

    def running(self) -> int:
        return self._state == _RUNNING

    def done(self) -> int:
        return self._state == _FINISHED or self.cancelled()

    def result(self, timeout: float = 0.0) -> object:
        """Return the result, waiting up to *timeout* seconds."""
        deadline: float = 0.0
        if timeout > 0.0:
            deadline = _time.time() + timeout
        while not self.done():
            if timeout > 0.0 and _time.time() > deadline:
                raise TimeoutError("Future.result() timed out")
            _time.sleep(0.001)
        if self.cancelled():
            raise CancelledError()
        if self._has_exception:
            raise RuntimeError("Future raised: " + self._exception)
        return self._result

    def exception(self, timeout: float = 0.0) -> object:
        """Return the exception raised by the call (or None)."""
        deadline: float = 0.0
        if timeout > 0.0:
            deadline = _time.time() + timeout
        while not self.done():
            if timeout > 0.0 and _time.time() > deadline:
                raise TimeoutError("Future.exception() timed out")
            _time.sleep(0.001)
        if self.cancelled():
            raise CancelledError()
        if self._has_exception:
            return self._exception
        return None

    def add_done_callback(self, fn: int) -> None:
        """Add a callback to be called when the future completes."""
        self._callbacks.append(fn)
        if self.done():
            cb: int = fn
            cb(self)

    def set_result(self, result: object) -> None:
        """Set the result of the future (called by executor)."""
        self._lock.acquire()
        self._result = result
        self._state = _FINISHED
        self._lock.release()
        self._invoke_callbacks()

    def set_exception(self, exception: str) -> None:
        """Set the exception of the future (called by executor)."""
        self._lock.acquire()
        self._exception = exception
        self._has_exception = 1
        self._state = _FINISHED
        self._lock.release()
        self._invoke_callbacks()

    def _invoke_callbacks(self) -> None:
        for cb in self._callbacks:
            fn: int = cb
            fn(self)

    def set_running_or_notify_cancel(self) -> int:
        self._lock.acquire()
        if self._state == _CANCELLED:
            self._state = _CANCELLED_AND_NOTIFIED
            self._lock.release()
            return 0
        self._state = _RUNNING
        self._lock.release()
        return 1

    def __repr__(self) -> str:
        return "Future<state=" + self._state + ">"


# ---------------------------------------------------------------------------
# Thread worker
# ---------------------------------------------------------------------------

def _worker_run(future: Future, fn: int, arg0: object, arg1: object,
                arg2: object, nargs: int) -> None:
    """Thread target: run fn with args and store result in future."""
    if not future.set_running_or_notify_cancel():
        return
    try:
        result: object = None
        if nargs == 0:
            result = fn()
        elif nargs == 1:
            result = fn(arg0)
        elif nargs == 2:
            result = fn(arg0, arg1)
        else:
            result = fn(arg0, arg1, arg2)
        future.set_result(result)
    except Exception as e:
        future.set_exception("exception in worker")


# ---------------------------------------------------------------------------
# Executors
# ---------------------------------------------------------------------------

class Executor:
    """Abstract base for executors."""

    def submit(self, fn: int, arg0: object = None, arg1: object = None,
               arg2: object = None) -> Future:
        raise NotImplementedError("Executor.submit: abstract")

    def map(self, fn: int, iterable: list, timeout: float = 0.0,
            chunksize: int = 1) -> list:
        futures: list = []
        for item in iterable:
            futures.append(self.submit(fn, item))
        results: list = []
        for f in futures:
            results.append(f.result(timeout))
        return results

    def shutdown(self, wait: int = 1, cancel_futures: int = 0) -> None:
        pass

    def __enter__(self) -> Executor:
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> int:
        self.shutdown(wait=1)
        return 0


class ThreadPoolExecutor(Executor):
    """Execute calls asynchronously using a pool of threads.

    Up to *max_workers* threads are spawned. Each submitted task runs in
    a new daemon Thread (true pooling requires OS thread primitives not yet
    exposed in asmpython).
    """

    def __init__(self, max_workers: int = 0, thread_name_prefix: str = "",
                 initializer: int = 0, initargs: list = []) -> None:
        self._max_workers: int = max_workers if max_workers > 0 else 4
        self._prefix: str = thread_name_prefix
        self._shutdown: int = 0
        self._futures: list = []

    def submit(self, fn: int, arg0: object = None, arg1: object = None,
               arg2: object = None) -> Future:
        """Submit a callable for execution and return a Future."""
        if self._shutdown:
            raise RuntimeError("cannot schedule new futures after shutdown")
        nargs: int = 0
        if arg0 is not None:
            nargs = 1
        if arg1 is not None:
            nargs = 2
        if arg2 is not None:
            nargs = 3
        future: Future = Future()
        self._futures.append(future)
        t: _threading.Thread = _threading.Thread(
            target=_worker_run,
            args=[future, fn, arg0, arg1, arg2, nargs]
        )
        t.daemon = 1
        t.start()
        return future

    def shutdown(self, wait: int = 1, cancel_futures: int = 0) -> None:
        self._shutdown = 1
        if cancel_futures:
            for f in self._futures:
                f.cancel()
        if wait:
            for f in self._futures:
                if not f.done() and not f.cancelled():
                    f.result()


class ProcessPoolExecutor(Executor):
    """Execute calls in separate processes (raises NotImplementedError)."""

    def __init__(self, max_workers: int = 0, mp_context: object = None,
                 initializer: int = 0, initargs: list = []) -> None:
        self._max_workers: int = max_workers if max_workers > 0 else 4

    def submit(self, fn: int, arg0: object = None, arg1: object = None,
               arg2: object = None) -> Future:
        raise NotImplementedError(
            "ProcessPoolExecutor: multiprocessing not supported in asmpython; "
            "use ThreadPoolExecutor instead"
        )

    def shutdown(self, wait: int = 1, cancel_futures: int = 0) -> None:
        pass


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def as_completed(fs: list, timeout: float = 0.0) -> list:
    """Yield futures as they complete (returns a list in iteration order).

    Note: this is a blocking list-returning function, not an iterator generator,
    since asmpython does not support yield/generator syntax.
    """
    deadline: float = 0.0
    if timeout > 0.0:
        deadline = _time.time() + timeout
    remaining: list = []
    for f in fs:
        remaining.append(f)
    done: list = []
    while remaining:
        if timeout > 0.0 and _time.time() > deadline:
            raise TimeoutError("as_completed timed out")
        still_waiting: list = []
        for f in remaining:
            if f.done():
                done.append(f)
            else:
                still_waiting.append(f)
        remaining = still_waiting
        if remaining:
            _time.sleep(0.001)
    return done


def wait(fs: list, timeout: float = 0.0,
         return_when: str = ALL_COMPLETED) -> list:
    """Wait for futures to complete. Returns [done_set, not_done_set] as lists."""
    deadline: float = 0.0
    if timeout > 0.0:
        deadline = _time.time() + timeout
    while True:
        done: list = []
        not_done: list = []
        for f in fs:
            if f.done():
                done.append(f)
            else:
                not_done.append(f)
        if return_when == FIRST_COMPLETED and done:
            break
        if return_when == FIRST_EXCEPTION:
            found_exc: int = 0
            for f in done:
                if f._has_exception:
                    found_exc = 1
            if found_exc or not not_done:
                break
        if return_when == ALL_COMPLETED and not not_done:
            break
        if timeout > 0.0 and _time.time() > deadline:
            break
        if not_done:
            _time.sleep(0.001)
    done2: list = []
    not_done2: list = []
    for f in fs:
        if f.done():
            done2.append(f)
        else:
            not_done2.append(f)
    return [done2, not_done2]
