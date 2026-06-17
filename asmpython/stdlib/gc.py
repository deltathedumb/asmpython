"""gc module: garbage collector interface.

In asmpython, memory is not garbage-collected (malloc only, no GC).
These stubs allow code importing gc to compile and run without errors.
"""
from __future__ import annotations


def enable() -> None:
    """Enable automatic garbage collection (no-op in asmpython)."""
    pass


def disable() -> None:
    """Disable automatic garbage collection (no-op in asmpython)."""
    pass


def isenabled() -> int:
    """Return True if automatic garbage collection is enabled (always False)."""
    return 0


def collect(generation: int = 2) -> int:
    """Run a full collection (no-op, returns 0)."""
    return 0


def get_count() -> list:
    """Return collection counts (stub, [0, 0, 0])."""
    result: list = []
    result.append(0)
    result.append(0)
    result.append(0)
    return result


def get_threshold() -> list:
    """Return collection thresholds (stub)."""
    result: list = []
    result.append(700)
    result.append(10)
    result.append(10)
    return result


def set_threshold(threshold0: int, threshold1: int = 10,
                  threshold2: int = 10) -> None:
    """Set collection thresholds (no-op)."""
    pass


def get_objects(generation: int = -1) -> list:
    """Return list of all tracked objects (stub, empty)."""
    return []


def get_referrers(*objs) -> list:
    """Return list of objects that refer to objs (stub, empty)."""
    return []


def get_referents(*objs) -> list:
    """Return list of objects directly referred to by objs (stub, empty)."""
    return []


def is_tracked(obj: int) -> int:
    """Return True if obj is tracked by the GC (always False in asmpython)."""
    return 0


def is_finalized(obj: int) -> int:
    """Return True if obj has been finalized (stub, always False)."""
    return 0


def freeze() -> None:
    """Freeze all tracked objects (no-op)."""
    pass


def unfreeze() -> None:
    """Unfreeze all tracked objects (no-op)."""
    pass


def get_freeze_count() -> int:
    """Return the number of frozen objects (always 0)."""
    return 0


def set_debug(flags: int) -> None:
    """Set debugging flags (no-op)."""
    pass


def get_debug() -> int:
    """Return debugging flags (always 0)."""
    return 0


DEBUG_STATS: int = 1
DEBUG_COLLECTABLE: int = 2
DEBUG_UNCOLLECTABLE: int = 4
DEBUG_SAVEALL: int = 32
DEBUG_LEAK: int = 38
