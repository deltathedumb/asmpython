"""Internal FFI bindings for the garbage collector.

Raw entry points into the collector emitted by the target backends. User code
should import `gc`, not this module.

The collector is a stop-the-world mark-sweep over the object registry that
`_runtime_objalloc` threads: every tracked object carries a 16-byte header
holding its size, a kind tag, and the registry link. See
`docs/SUSPENSION-AND-TAGGING.md` for the header layout and
`_runtime_gc_trace` for how a list's elements are reached through its
(deliberately unregistered) buffer.
"""
from __future__ import annotations

from . import Func

BINDINGS: dict = {
    # _runtime_gc_collect() -> objects freed.
    # Scans the machine stack and the module-globals area for roots, marks
    # transitively, then sweeps. Callee-saved registers are pushed first so a
    # value held only in a register is on the stack and therefore seen.
    "_gc_collect": Func(
        arg_types=(), ret_type="int", c_name="_runtime_gc_collect",
    ),
    # _runtime_gc_count() -> number of live tracked objects.
    "_gc_live_count": Func(
        arg_types=(), ret_type="int", c_name="_runtime_gc_count",
    ),
    # NOTE: there is deliberately no binding for the build-time MODE. The
    # runtime library carries its own baked value, so a program built with
    # --gc=conservative would still read the library's, and reporting the
    # wrong mode is worse than not reporting one.

    # _runtime_gc_set_enabled(flag) -> previous flag.
    # Turns automatic collection on/off without discarding the registry.
    "_gc_set_enabled": Func(
        arg_types=("int",), ret_type="int", c_name="_runtime_gc_set_enabled",
    ),
    # _runtime_gc_set_threshold(n) -> previous threshold.
    # Objects allocated between automatic collections.
    "_gc_set_threshold": Func(
        arg_types=("int",), ret_type="int", c_name="_runtime_gc_set_threshold",
    ),
    # _runtime_gc_get_threshold() -> current threshold (the default if unset).
    "_gc_get_threshold": Func(
        arg_types=(), ret_type="int", c_name="_runtime_gc_get_threshold",
    ),
    # _runtime_gc_get_enabled() -> current flag.
    "_gc_get_enabled": Func(
        arg_types=(), ret_type="int", c_name="_runtime_gc_get_enabled",
    ),
}
