"""gc module: garbage collector interface.

Backed by a real collector: a stop-the-world mark-sweep whose roots are the
machine stack, the module-globals area, and any exact roots on the shadow
stack, with registry membership as the "is this word really an object" test.

`collect()` returns the number of objects actually reclaimed, so it reports
real work rather than a fixed 0.

Automatic collection runs after `get_threshold()` object allocations, and is
armed by `enable()` (or by building with `--gc=on`, which only the legacy
targets honour). While disabled, an explicit `collect()` still works -- same
split as CPython.

TWO THINGS DO NOT MATCH CPython, both because this collector traces rather
than counts references:

  * `del obj` does not free at the `del`, and `__del__` does not run at a
    predictable point. That needs refcounting; `--gc=refcount` names the gap
    and refuses rather than pretending.
  * `collect()` returns 0 and frees NOTHING unless BOTH root sets are
    available -- the machine stack and the module-globals range. A missing
    root set is not a degraded collection but a wrong one: without the stack,
    every object held only by a local is freed; without the globals range,
    every object held only by a module-level name is freed. Refusing is the
    only safe answer.

    Neither root set needs help from the backend, which matters because the
    default x86-64 backend emits no entry prologue and so never calls
    `_runtime_gc_init`: on Win64 the stack base comes from the TEB and the
    globals range from walking the program's own PE headers. Linux has neither
    hook yet, so there `collect()` returns 0 and frees nothing -- a deliberate
    no-op, not a sign that nothing was garbage.
"""
from __future__ import annotations

from _gcffi import _gc_collect
from _gcffi import _gc_live_count
from _gcffi import _gc_set_enabled
from _gcffi import _gc_get_enabled
from _gcffi import _gc_set_threshold
from _gcffi import _gc_get_threshold


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
    """Return collection thresholds.

    Only the first is real -- the collector is not generational, so the two
    sub-generation thresholds are reported as CPython's defaults and ignored.
    """
    result: list = []
    result.append(_gc_get_threshold())
    result.append(10)
    result.append(10)
    return result


def set_threshold(threshold0: int, threshold1: int = 10,
                  threshold2: int = 10) -> None:
    """Set the number of object allocations between automatic collections.

    `threshold0` of 0 restores the default rather than collecting on every
    allocation, matching how the runtime reads an unset slot.
    """
    _gc_set_threshold(threshold0)


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
