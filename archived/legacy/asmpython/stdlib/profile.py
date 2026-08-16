"""profile: deterministic profiling of Python programs.

Provides a profiler that measures call counts and cumulative time.
Uses time.time() for timing (wall-clock only; no CPU-time support).

Usage::

    import profile

    def expensive():
        total = 0
        for i in range(1000):
            total += i
        return total

    profile.run("expensive()")

    # Or use the profiler object directly:
    pr = profile.Profile()
    pr.enable()
    expensive()
    pr.disable()
    pr.print_stats()
"""
from __future__ import annotations

import time as _time
import pstats as _pstats

# ---------------------------------------------------------------------------
# Stats accumulator
# ---------------------------------------------------------------------------

class _FuncStat:
    def __init__(self) -> None:
        self.ncalls: int = 0
        self.cumtime: float = 0.0
        self.tottime: float = 0.0
        self.callers: dict = {}


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------

class Profile:
    """Simple deterministic profiler."""

    def __init__(self, timer: int = 0, timeunit: float = 0.0,
                 subcalls: int = 1, builtins: int = 1) -> None:
        self._timer: int = timer
        self._timeunit: float = timeunit if timeunit > 0.0 else 1.0e-6
        self.stats: dict = {}  # func_name -> _FuncStat
        self._call_stack: list = []
        self._enabled: int = 0
        self._last_time: float = 0.0

    def enable(self) -> None:
        """Start collecting profiling data."""
        self._enabled = 1
        self._last_time = _time.time()

    def disable(self) -> None:
        """Stop collecting profiling data."""
        self._enabled = 0

    def clear(self) -> None:
        """Clear all profiling data."""
        self.stats = {}
        self._call_stack = []

    def _calibrate(self, m: int, verbose: int = 0) -> float:
        """Calibrate the profiler (stub — returns 0)."""
        return 0.0

    def run(self, cmd: str) -> Profile:
        """Profile a command string (stub — command is not executed)."""
        self.enable()
        self.disable()
        return self

    def runcall(self, func: int, arg0: object = None, arg1: object = None,
                arg2: object = None) -> object:
        """Profile a single function call."""
        func_name: str = str(func)
        if func_name not in self.stats:
            self.stats[func_name] = _FuncStat()
        stat: _FuncStat = self.stats[func_name]
        t0: float = _time.time()
        result: object = None
        nargs: int = 0
        if arg0 is not None:
            nargs = 1
        if arg1 is not None:
            nargs = 2
        if arg2 is not None:
            nargs = 3
        fn: int = func
        if nargs == 0:
            result = fn()
        elif nargs == 1:
            result = fn(arg0)
        elif nargs == 2:
            result = fn(arg0, arg1)
        else:
            result = fn(arg0, arg1, arg2)
        t1: float = _time.time()
        elapsed: float = t1 - t0
        stat.ncalls = stat.ncalls + 1
        stat.cumtime = stat.cumtime + elapsed
        stat.tottime = stat.tottime + elapsed
        return result

    def print_stats(self, sort: str = "cumulative") -> None:
        """Print profiling results."""
        ps: _pstats.Stats = _pstats.Stats(self)
        ps.print_stats()

    def dump_stats(self, filename: str) -> None:
        """Write profiling data to a file."""
        lines: list = []
        lines.append("# asmpython profile data")
        for name in self.stats:
            stat: _FuncStat = self.stats[name]
            lines.append(name + "\t" + str(stat.ncalls) + "\t" +
                         str(stat.cumtime) + "\t" + str(stat.tottime))
        f = open(filename, "w")
        f.write("\n".join(lines))
        f.close()

    def create_stats(self) -> None:
        """Create stats data (no-op; stats are built incrementally)."""
        pass

    def getstats(self) -> list:
        """Return list of [name, ncalls, cumtime, tottime] lists."""
        result: list = []
        for name in self.stats:
            stat: _FuncStat = self.stats[name]
            result.append([name, stat.ncalls, stat.cumtime, stat.tottime])
        return result


# ---------------------------------------------------------------------------
# Module-level run() convenience
# ---------------------------------------------------------------------------

def run(statement: str, filename: str = "", sort: str = "cumulative") -> None:
    """Profile *statement* (stub — statement is not executed)."""
    prof: Profile = Profile()
    prof.enable()
    prof.disable()
    if filename:
        prof.dump_stats(filename)
    else:
        prof.print_stats(sort)


def runctx(statement: str, globals: dict, locals: dict,
           filename: str = "", sort: str = "cumulative") -> None:
    """Profile *statement* in given context (stub)."""
    run(statement, filename, sort)
