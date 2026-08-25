"""`wasm.alib` -- the WebAssembly architecture library.

THERE IS NO MMIO AND THERE NEVER WILL BE. WebAssembly's linear memory is a
sandbox with no devices behind it: an address is an offset into a byte array
the host owns, and there is nothing at any particular one. The `mmio` group is
absent rather than present-and-failing, because a program that asks for MMIO
here has made a category error that should be a compile error.

WHAT IT HAS INSTEAD is memory MANAGEMENT -- `memory.grow` is the only way a
wasm program gets more of it -- and the bit-manipulation opcodes the IR has no
equivalent for. `unreachable` is here because it is the only way to trap
deliberately.

Nothing here is emitted yet.
"""
from __future__ import annotations

from ...backend.alib import Alib, Intrinsic, mmio_group

ALIB = Alib(
    arch="wasm",
    doc="WebAssembly: linear memory, and no devices at all.",
    groups={
        "instructions": (
        Intrinsic("memory_size", (), "i32", "Pages of linear memory currently allocated."),
        Intrinsic("memory_grow", ("i32",), "i32", "Grow linear memory; answers the old size or -1."),
        Intrinsic("memory_copy", ("ptr", "ptr", "i32"), "void", "Copy within linear memory."),
        Intrinsic("memory_fill", ("ptr", "i32", "i32"), "void", "Fill a range of linear memory."),
        Intrinsic("popcnt64", ("i64",), "i64", "Count set bits."),
        Intrinsic("clz64", ("i64",), "i64", "Count leading zeros."),
        Intrinsic("ctz64", ("i64",), "i64", "Count trailing zeros."),
        Intrinsic("unreachable", (), "void", "Trap immediately."),
        ),
        "barriers": (
        Intrinsic("atomic_fence", (), "void", "Order accesses for the threads proposal."),
        ),
        "emit_raw": (
        Intrinsic("emit", ("ptr", "i32"), "void", "Emit raw bytes into the code section."),
        ),
    },
)
