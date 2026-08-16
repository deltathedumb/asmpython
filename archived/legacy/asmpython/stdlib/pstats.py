"""pstats: statistics object for use with the profile module.

Usage::

    import profile, pstats

    pr = profile.Profile()
    pr.enable()
    # ... run code ...
    pr.disable()

    ps = pstats.Stats(pr)
    ps.sort_stats("cumulative")
    ps.print_stats(20)        # top 20 entries
"""
from __future__ import annotations

import time as _time


class Stats:
    """Statistics object for profiling data."""

    def __init__(self, profile_obj: object = None, stream: object = None,
                 filename: str = "") -> None:
        self._profile: object = profile_obj
        self._stream: object = stream
        self._sort_key: str = "cumulative"
        self._stats: list = []
        if profile_obj is not None:
            self._stats = profile_obj.getstats()
        elif filename:
            self._load(filename)

    def _load(self, filename: str) -> None:
        f = open(filename, "r")
        content: str = f.read()
        f.close()
        self._stats = []
        for line in content.split("\n"):
            if not line or line.startswith("#"):
                continue
            parts: list = line.split("\t")
            if len(parts) >= 4:
                name: str = parts[0]
                ncalls: int = int(parts[1])
                cumtime: float = float(parts[2])
                tottime: float = float(parts[3])
                self._stats.append([name, ncalls, cumtime, tottime])

    def sort_stats(self, key0: str = "cumulative", key1: str = "",
                   key2: str = "") -> Stats:
        """Sort stats by key. Supported: 'cumulative', 'tottime', 'ncalls', 'name'."""
        self._sort_key = key0
        if key0 == "ncalls":
            # Sort by ncalls descending
            n: int = len(self._stats)
            i: int = 0
            while i < n:
                j: int = i + 1
                while j < n:
                    a: list = self._stats[i]
                    b: list = self._stats[j]
                    if b[1] > a[1]:
                        self._stats[i] = b
                        self._stats[j] = a
                    j = j + 1
                i = i + 1
        elif key0 == "tottime":
            n = len(self._stats)
            i = 0
            while i < n:
                j = i + 1
                while j < n:
                    a = self._stats[i]
                    b = self._stats[j]
                    if b[3] > a[3]:
                        self._stats[i] = b
                        self._stats[j] = a
                    j = j + 1
                i = i + 1
        elif key0 == "name":
            n = len(self._stats)
            i = 0
            while i < n:
                j = i + 1
                while j < n:
                    a = self._stats[i]
                    b = self._stats[j]
                    if b[0] < a[0]:
                        self._stats[i] = b
                        self._stats[j] = a
                    j = j + 1
                i = i + 1
        else:
            # Default: cumulative time descending
            n = len(self._stats)
            i = 0
            while i < n:
                j = i + 1
                while j < n:
                    a = self._stats[i]
                    b = self._stats[j]
                    if b[2] > a[2]:
                        self._stats[i] = b
                        self._stats[j] = a
                    j = j + 1
                i = i + 1
        return self

    def print_stats(self, maxentries: int = 0) -> Stats:
        """Print the statistics."""
        entries: list = self._stats
        if maxentries > 0 and len(entries) > maxentries:
            entries = entries[:maxentries]
        header: str = "{:>10} {:>10} {:>10}  {}".format(
            "ncalls", "cumtime", "tottime", "function")
        print(header)
        print("-" * 60)
        for entry in entries:
            name: str = entry[0]
            ncalls: int = entry[1]
            cumtime: float = entry[2]
            tottime: float = entry[3]
            row: str = "{:>10} {:>10.3f} {:>10.3f}  {}".format(
                str(ncalls), cumtime, tottime, name)
            print(row)
        return self

    def print_callers(self, maxentries: int = 0) -> Stats:
        """Print callers for each function (stub — no caller data)."""
        print("(No caller data available)")
        return self

    def print_callees(self, maxentries: int = 0) -> Stats:
        """Print callees for each function (stub — no callee data)."""
        print("(No callee data available)")
        return self

    def dump_stats(self, filename: str) -> None:
        """Dump stats to a file."""
        if self._profile is not None:
            self._profile.dump_stats(filename)

    def add(self, arg: object) -> Stats:
        """Add stats from another profiler or file."""
        if isinstance(arg, str):
            other: Stats = Stats(filename=arg)
            self._stats = self._stats + other._stats
        return self

    def strip_dirs(self) -> Stats:
        """Remove directory prefixes from filenames (no-op)."""
        return self

    def get_stats_profile(self) -> list:
        """Return a StatsProfile (stub — returns raw stats list)."""
        return self._stats

    def fcn_list(self) -> list:
        """Return sorted list of function names."""
        result: list = []
        for entry in self._stats:
            result.append(entry[0])
        return result

    def total_calls(self) -> int:
        """Return total number of calls recorded."""
        total: int = 0
        for entry in self._stats:
            total = total + entry[1]
        return total

    def total_tt(self) -> float:
        """Return total time recorded."""
        total: float = 0.0
        for entry in self._stats:
            total = total + entry[3]
        return total

    def __repr__(self) -> str:
        return "<pstats.Stats: " + str(len(self._stats)) + " entries>"
