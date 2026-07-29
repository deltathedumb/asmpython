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
    # _runtime_gc_set_enabled(flag) -> previous flag.
    # Turns automatic collection on/off without discarding the registry.
    "_gc_set_enabled": Func(
        arg_types=("int",), ret_type="int", c_name="_runtime_gc_set_enabled",
    ),
    # _runtime_gc_get_enabled() -> current flag.
    "_gc_get_enabled": Func(
        arg_types=(), ret_type="int", c_name="_runtime_gc_get_enabled",
    ),
    # _runtime_gc_mode() -> 0 off, 1 conservative, 2 precise, 3 refcount.
    # Baked in at build time by `--gc=MODE`; reported so `gc` can describe
    # what it is actually doing rather than guessing.
    "_gc_mode": Func(
        arg_types=(), ret_type="int", c_name="_runtime_gc_mode",
    ),
}
