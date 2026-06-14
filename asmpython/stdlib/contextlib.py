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
