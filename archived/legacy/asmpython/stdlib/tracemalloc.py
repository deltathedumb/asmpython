"""tracemalloc: trace memory allocations.

In asmpython, memory tracking is not available at the allocator level.
This module provides the full API surface for code compatibility, but
actual memory tracing is not implemented. Take snapshots with take_snapshot()
and compare them for API compatibility testing.

Usage::

    import tracemalloc

    tracemalloc.start()
    # ... do some allocations ...
    snapshot = tracemalloc.take_snapshot()
    top = snapshot.statistics("lineno")
    for stat in top[:10]:
        print(stat)
    tracemalloc.stop()
"""
from __future__ import annotations

import time as _time

_tracing: int = 0
_max_frames: int = 1
_snapshots: list = []


# ---------------------------------------------------------------------------
# Traceback / Frame stubs
# ---------------------------------------------------------------------------

class Frame:
    """A single frame in a traceback."""

    def __init__(self, filename: str, lineno: int) -> None:
        self.filename: str = filename
        self.lineno: int = lineno

    def __repr__(self) -> str:
        return self.filename + ":" + str(self.lineno)

    def __eq__(self, other: Frame) -> int:
        return self.filename == other.filename and self.lineno == other.lineno

    def __lt__(self, other: Frame) -> int:
        if self.filename != other.filename:
            return self.filename < other.filename
        return self.lineno < other.lineno


class Traceback:
    """A traceback — a sequence of frames."""

    def __init__(self, frames: list) -> None:
        self._frames: list = frames

    def __len__(self) -> int:
        return len(self._frames)

    def __getitem__(self, index: int) -> Frame:
        return self._frames[index]

    def format(self, limit: int = 0, most_recent_first: int = 0) -> list:
        """Return formatted traceback lines."""
        frames: list = self._frames
        if limit > 0:
            frames = frames[:limit]
        if most_recent_first:
            frames = list(reversed(frames))
        result: list = []
        for f in frames:
            result.append("  File \"" + f.filename + "\", line " + str(f.lineno))
        return result

    def __repr__(self) -> str:
        return "<Traceback " + str(len(self._frames)) + " frames>"


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

class Statistic:
    """Memory allocation statistic for one traceback."""

    def __init__(self, traceback: Traceback, size: int, count: int) -> None:
        self.traceback: Traceback = traceback
        self.size: int = size
        self.count: int = count

    def __repr__(self) -> str:
        return (str(self.size) + " B × " + str(self.count) +
                " | " + repr(self.traceback))


class StatisticDiff:
    """Difference between two snapshots for one traceback."""

    def __init__(self, traceback: Traceback, size: int, size_diff: int,
                 count: int, count_diff: int) -> None:
        self.traceback: Traceback = traceback
        self.size: int = size
        self.size_diff: int = size_diff
        self.count: int = count
        self.count_diff: int = count_diff

    def __repr__(self) -> str:
        return (str(self.size_diff) + " B | " + str(self.count_diff) +
                " | " + repr(self.traceback))


# ---------------------------------------------------------------------------
# Snapshot
# ---------------------------------------------------------------------------

class Snapshot:
    """Memory snapshot (stub)."""

    def __init__(self, traces: list, traceback_limit: int) -> None:
        self.traces: list = traces
        self.traceback_limit: int = traceback_limit
        self._timestamp: float = _time.time()

    def statistics(self, key_type: str = "lineno",
                   cumulative: int = 0) -> list:
        """Return list of Statistic objects (always empty in asmpython)."""
        return []

    def compare_to(self, old_snapshot: Snapshot, key_type: str = "lineno",
                   cumulative: int = 0) -> list:
        """Return list of StatisticDiff (always empty in asmpython)."""
        return []

    def filter_traces(self, filters: list) -> Snapshot:
        """Return a new snapshot with filtered traces (returns self)."""
        return Snapshot(self.traces, self.traceback_limit)

    def dump(self, filename: str) -> None:
        """Write snapshot to file (stub)."""
        f = open(filename, "w")
        f.write("# tracemalloc snapshot (empty)\n")
        f.close()

    def __repr__(self) -> str:
        return "<tracemalloc.Snapshot>"


def load(filename: str) -> Snapshot:
    """Load a snapshot from a file (returns empty snapshot)."""
    return Snapshot([], 1)


# ---------------------------------------------------------------------------
# Filter
# ---------------------------------------------------------------------------

class Filter:
    """Filter for snapshots."""

    def __init__(self, inclusive: int, filename_pattern: str,
                 lineno: int = 0, all_frames: int = 0,
                 domain: int = 0) -> None:
        self.inclusive: int = inclusive
        self.filename_pattern: str = filename_pattern
        self.lineno: int = lineno
        self.all_frames: int = all_frames
        self.domain: int = domain

    def __repr__(self) -> str:
        inc: str = "include" if self.inclusive else "exclude"
        return "<Filter " + inc + " " + self.filename_pattern + ">"


class DomainFilter:
    """Filter by memory domain."""

    def __init__(self, inclusive: int, domain: int) -> None:
        self.inclusive: int = inclusive
        self.domain: int = domain


# ---------------------------------------------------------------------------
# Control API
# ---------------------------------------------------------------------------

def start(nframe: int = 1) -> None:
    """Start tracing memory allocations (no-op in asmpython)."""
    global _tracing, _max_frames
    _tracing = 1
    _max_frames = nframe


def stop() -> None:
    """Stop tracing memory allocations."""
    global _tracing
    _tracing = 0


def is_tracing() -> int:
    """Return 1 if tracing is active."""
    return _tracing


def get_traceback_limit() -> int:
    """Return the current traceback limit."""
    return _max_frames


def get_traced_memory() -> list:
    """Return [current_size, peak_size] in bytes (always 0 in asmpython)."""
    return [0, 0]


def reset_peak() -> None:
    """Reset the peak memory size (no-op)."""
    pass


def get_object_traceback(obj: object) -> Traceback:
    """Return the traceback of an object (returns None in asmpython)."""
    return None


def take_snapshot() -> Snapshot:
    """Take a snapshot of current memory allocations.

    Returns an empty snapshot (actual allocation tracking is not implemented).
    """
    return Snapshot([], _max_frames)


def clear_traces() -> None:
    """Clear traced memory blocks (no-op)."""
    pass
