"""`jvm.alib` -- the Java virtual machine's architecture library.

THIS ALIB IS MOSTLY A LIST OF WHAT IS NOT POSSIBLE, and that is the useful
thing about it. The JVM has no addressable memory, no devices, no privileged
mode and no instruction stream a program may write into -- so `mmio`, `ports`,
`sysregs`, `interrupts` and `emit_raw` are all ABSENT, and a program naming one
gets a compile error that says the architecture cannot do it rather than a
link error about a missing symbol.

WHAT REMAINS is real but small: the bit-manipulation opcodes, a monotonic
clock, and the fences the Java memory model defines. They are here because
they are still things the IR has no opcode for.

Nothing here is emitted yet.
"""
from __future__ import annotations

from ...backend.alib import Alib, Intrinsic, mmio_group

ALIB = Alib(
    arch="jvm",
    doc="JVM: no address space, so almost none of what an alib usually offers.",
    groups={
        "instructions": (
        Intrinsic("nano_time", (), "i64", "Read a monotonic clock, in nanoseconds."),
        Intrinsic("popcnt64", ("i64",), "i64", "Count set bits."),
        Intrinsic("clz64", ("i64",), "i64", "Count leading zeros."),
        Intrinsic("ctz64", ("i64",), "i64", "Count trailing zeros."),
        Intrinsic("reverse_bytes64", ("i64",), "i64", "Reverse byte order."),
        Intrinsic("identity_hash", ("ptr",), "i32", "The identity hash of a reference."),
        ),
        "barriers": (
        Intrinsic("full_fence", (), "void", "A full fence, as VarHandle defines it."),
        Intrinsic("acquire_fence", (), "void", "An acquire fence."),
        Intrinsic("release_fence", (), "void", "A release fence."),
        ),
    },
)
