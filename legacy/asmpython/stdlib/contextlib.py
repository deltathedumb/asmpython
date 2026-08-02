"""contextlib module: context manager utilities.

The @contextmanager decorator is a stub since asmpython's `with` statement
is limited. suppress() catches and suppresses specified exceptions.
"""
from __future__ import annotations


def contextmanager(func: str) -> str:
    """Decorator to turn a generator into a context manager (stub)."""
    return func


class suppress:
    """Context manager that suppresses specified exceptions.

    In asmpython, exception type matching is not inspectable at runtime,
    so __exit__ returns 1 to suppress any exception that was raised.
    Pass exception types positionally: with suppress(ValueError, KeyError):
    """

    def __init__(self) -> None:
        pass

    def __enter__(self) -> suppress:
        return self

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        return 1 if exc_type != 0 else 0


class nullcontext:
    """A context manager that does nothing (returns enter_result from __enter__)."""

    def __init__(self, enter_result: int = 0) -> None:
        self.enter_result: int = enter_result

    def __enter__(self) -> int:
        return self.enter_result

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        return 0


class closing:
    """Context manager that calls .close() on exit."""

    def __init__(self, thing: str) -> None:
        self.thing: str = thing

    def __enter__(self) -> str:
        return self.thing

    def __exit__(self, exc_type: int, exc_val: int, exc_tb: int) -> int:
        self.thing.close()
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
            cb()
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


def redirect_stdin(new_target: int) -> int:
    """Redirect stdin to new_target within a with block (stub)."""
    return new_target


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
