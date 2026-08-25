"""`c.alib` -- the portable-C architecture library.

MMIO IS REAL HERE, which surprises people. `volatile` is exactly the promise
MMIO needs -- the access happens, once, in this order -- so the C backend can
implement the whole group with a cast and a dereference, and it is the only
non-machine target that can.

PORTS AND SYSTEM REGISTERS ARE ABSENT because they are not properties of C;
they are properties of the machine the C is compiled FOR, and this target does
not know which one that is. A program needing them should name the
architecture whose alib has them.

`emit` IS INLINE ASSEMBLY, and therefore only as portable as the compiler and
machine underneath -- which is the trade the whole target makes.

Nothing here is emitted yet.
"""
from __future__ import annotations

from ...backend.alib import Alib, Intrinsic, mmio_group

ALIB = Alib(
    arch="c",
    doc="C: MMIO through volatile, and everything else through the host compiler.",
    groups={
        "instructions": (
        Intrinsic("popcnt64", ("i64",), "i64", "Count set bits."),
        Intrinsic("clz64", ("i64",), "i64", "Count leading zeros."),
        Intrinsic("ctz64", ("i64",), "i64", "Count trailing zeros."),
        Intrinsic("bswap64", ("i64",), "i64", "Reverse byte order."),
        Intrinsic("unreachable", (), "void", "Assert that control never arrives here."),
        ),
        "mmio": mmio_group(),
        "barriers": (
        Intrinsic("compiler_barrier", (), "void", "Stop the compiler reordering across this point."),
        Intrinsic("full_fence", (), "void", "A sequentially-consistent fence."),
        ),
        "emit_raw": (
        Intrinsic("emit", ("str",), "void", "Emit a string of inline assembly verbatim."),
        ),
    },
)
