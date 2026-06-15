"""timeit module: measure execution time of small code snippets."""
from __future__ import annotations
import time


default_number: int = 1000000
default_repeat: int = 5


def timeit(stmt: str = "pass", setup: str = "pass", number: int = 1000000,
           globals: int = 0) -> float:
    """Time execution of stmt, running it number times.

    In asmpython, stmt/setup are strings but actual exec() is not supported.
    Returns a placeholder 0.0 timing.
    """
    start: float = time.perf_counter()
    i: int = 0
    while i < number:
        i = i + 1
    end: float = time.perf_counter()
    return end - start


def repeat(stmt: str = "pass", setup: str = "pass", number: int = 1000000,
           repeat_count: int = 5, globals: int = 0) -> list[float]:
    """Run timeit() repeat_count times and return list of results."""
    results: list[float] = []
    i: int = 0
    while i < repeat_count:
        results.append(timeit(stmt, setup, number, globals))
        i = i + 1
    return results


class Timer:
    """Class to measure execution time."""

    def __init__(self, stmt: str = "pass", setup: str = "pass",
                 globals: int = 0) -> None:
        self.stmt: str = stmt
        self.setup: str = setup

    def timeit(self, number: int = 1000000) -> float:
        return timeit(self.stmt, self.setup, number)

    def repeat(self, repeat_count: int = 5, number: int = 1000000) -> list[float]:
        return repeat(self.stmt, self.setup, number, repeat_count)

    def autorange(self, callback: int = 0) -> list[int]:
        """Find number of iterations for ~0.2 seconds."""
        i: int = 1
        while True:
            for j in [1, 2, 5]:
                number: int = i * j
                t: float = self.timeit(number)
                if t >= 0.2:
                    return [number, t]
            i = i * 10
