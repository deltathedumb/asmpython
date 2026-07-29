"""gc module: garbage collector interface.

Backed by a real collector when the program is built with `--gc=MODE`:

    off           allocations are never reclaimed (the historical behaviour,
                  and still the default so nothing changes unasked)
    conservative  mark-sweep; roots found by scanning the machine stack and
                  the module-globals area, with registry membership as the
                  "is this really an object" test
    precise       mark-sweep with exact roots from a shadow stack
    refcount      CPython-parity: deterministic frees, mark-sweep kept as the
                  cycle collector

`collect()` returns the number of objects reclaimed, so it reports real work
rather than a fixed 0. Under `off` it returns 0 because there is nothing to
sweep, which is honest rather than a stub.
"""
from __future__ import annotations

from _gcffi import _gc_collect
from _gcffi import _gc_live_count
from _gcffi import _gc_set_enabled
from _gcffi import _gc_get_enabled
from _gcffi import _gc_mode


def enable() -> None:
    """Enable automatic garbage collection."""
    _gc_set_enabled(1)


def disable() -> None:
    """Disable automatic garbage collection. Explicit collect() still works."""
    _gc_set_enabled(0)


def isenabled() -> int:
    """Return True if automatic garbage collection is enabled."""
    return _gc_get_enabled()


def collect(generation: int = 2) -> int:
    """Run a full collection; returns the number of objects reclaimed.

    `generation` is accepted for CPython compatibility and ignored: the
    collector is not generational, so every collection is a full one.
    """
    return _gc_collect()


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
