"""`llvm.alib` -- the LLVM IR architecture library.

LLVM HAS A NAME FOR EVERY ONE OF THESE, which makes this the easiest alib to
implement and the reason it is worth having: `llvm.readcyclecounter`,
`llvm.ctpop.i64`, `llvm.bswap.i64`, a `fence` instruction, and `volatile` on a
load or store. Nothing needs inventing.

WHAT IS STILL ABSENT is ports and system registers -- LLVM IR is not a
machine, and reaching those means naming a target and using its own
intrinsics, which is what the machine alibs are for.

Nothing here is emitted yet.
"""
from __future__ import annotations

from ...backend.alib import Alib, Intrinsic, mmio_group

ALIB = Alib(
    arch="llvm",
    doc="LLVM IR: volatile load/store, fences, and the named intrinsics.",
    groups={
        "instructions": (
        Intrinsic("readcyclecounter", (), "i64", "Read the target's cycle counter."),
        Intrinsic("popcnt64", ("i64",), "i64", "Count set bits."),
        Intrinsic("clz64", ("i64",), "i64", "Count leading zeros."),
        Intrinsic("ctz64", ("i64",), "i64", "Count trailing zeros."),
        Intrinsic("bswap64", ("i64",), "i64", "Reverse byte order."),
        Intrinsic("trap", (), "void", "Trap immediately."),
        Intrinsic("prefetch", ("ptr", "i32"), "void", "Prefetch an address."),
        ),
        "mmio": mmio_group(),
        "barriers": (
        Intrinsic("fence_seq_cst", (), "void", "A sequentially-consistent fence."),
        Intrinsic("fence_acquire", (), "void", "An acquire fence."),
        Intrinsic("fence_release", (), "void", "A release fence."),
        ),
        "emit_raw": (
        Intrinsic("emit", ("str", "str"), "void", "Emit inline assembly with a constraint string."),
        ),
    },
)
