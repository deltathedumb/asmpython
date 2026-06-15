"""contextlib module: context manager utilities.

The @contextmanager decorator is a stub since asmpython's `with` statement
is limited. suppress() catches and suppresses specified exceptions.
"""
from __future__ import annotations


def contextmanager(func: str) -> str:
    """Decorator to turn a generator into a context manager (stub)."""
    return func


def suppress(*exceptions) -> int:
    """Return a context manager that suppresses specified exceptions (stub)."""
    return 0


def nullcontext(enter_result: int = 0) -> int:
    """Return a no-op context manager (stub)."""
    return enter_result


class closing:
    """Context manager that calls .close() on exit."""

    def __init__(self, thing: str) -> None:
        self.thing: str = thing

    def __enter__(self) -> str:
        return self.thing

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        return 0


class ExitStack:
    """Context manager that manages a dynamic stack of context managers."""

    def __init__(self) -> None:
        self._callbacks: list = []

    def __enter__(self) -> ExitStack:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        i: int = len(self._callbacks) - 1
        while i >= 0:
            cb: int = self._callbacks[i]
            i = i - 1
        self._callbacks = []
        return 0

    def callback(self, func: int) -> int:
        """Register a callback to be called on exit."""
        self._callbacks.append(func)
        return func

    def pop_all(self) -> ExitStack:
        """Transfer callbacks to a new ExitStack."""
        new_stack: ExitStack = ExitStack()
        new_stack._callbacks = self._callbacks
        self._callbacks = []
        return new_stack

    def close(self) -> None:
        """Immediately unwind the context stack."""
        self._callbacks = []


class AsyncExitStack(ExitStack):
    """Async version of ExitStack (stub, identical to ExitStack in asmpython)."""
    pass


def asynccontextmanager(func: int) -> int:
    """Async context manager decorator (stub)."""
    return func


class AbstractContextManager:
    """ABC for context managers (stub)."""

    def __enter__(self) -> AbstractContextManager:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        return 0


class AbstractAsyncContextManager(AbstractContextManager):
    """ABC for async context managers (stub)."""
    pass


def redirect_stdout(new_target: int) -> int:
    """Redirect stdout (stub, returns 0)."""
    return 0


def redirect_stderr(new_target: int) -> int:
    """Redirect stderr (stub, returns 0)."""
    return 0


class chdir:
    """Non-reentrant context manager to change the current directory (stub)."""

    def __init__(self, path: str) -> None:
        self.path: str = path
        self._old: str = ""

    def __enter__(self) -> str:
        return self.path

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        return 0
