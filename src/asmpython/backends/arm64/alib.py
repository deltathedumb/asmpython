"""`arm64.alib` -- the AArch64 architecture library.

NO PORTS, AND THAT IS NOT AN OMISSION. AArch64 has one address space; a
device is memory. What it has instead of `in`/`out` is a rich set of SYSTEM
REGISTERS reached by `mrs`/`msr` with a symbolic name, which is why `sysregs`
here takes a string rather than a number as x86's MSRs do.

BARRIERS MATTER MORE HERE than on x86. x86's memory model orders most of what
a device driver needs by itself; AArch64's does not, so `dmb`/`dsb` are not an
optimisation but the difference between a working driver and an intermittent
one.

Nothing here is emitted yet.
"""
from __future__ import annotations

from ...backend.alib import Alib, Intrinsic, mmio_group

ALIB = Alib(
    arch="arm64",
    doc="AArch64: system registers by name, and the barriers that make MMIO mean anything.",
    groups={
        "instructions": (
        Intrinsic("wfi", (), "void", "Wait for an interrupt.", freestanding_only=True),
        Intrinsic("wfe", (), "void", "Wait for an event.", freestanding_only=True),
        Intrinsic("sev", (), "void", "Signal an event to other cores."),
        Intrinsic("yield_", (), "void", "Hint that this is a spin-wait loop."),
        Intrinsic("cntvct", (), "i64", "Read the virtual counter."),
        Intrinsic("cntfrq", (), "i64", "Read the counter frequency."),
        Intrinsic("mpidr", (), "i64", "Read this core's affinity register.", freestanding_only=True),
        Intrinsic("rbit64", ("i64",), "i64", "Reverse bit order."),
        Intrinsic("clz64", ("i64",), "i64", "Count leading zeros."),
        ),
        "mmio": mmio_group(),
        "barriers": (
        Intrinsic("dmb_sy", (), "void", "Data memory barrier, full system."),
        Intrinsic("dsb_sy", (), "void", "Data synchronisation barrier, full system."),
        Intrinsic("isb", (), "void", "Instruction synchronisation barrier."),
        Intrinsic("dc_civac", ("ptr",), "void", "Clean and invalidate a cache line."),
        Intrinsic("ic_iallu", (), "void", "Invalidate the instruction cache."),
        ),
        "sysregs": (
        Intrinsic("mrs", ("str",), "i64", "Read a system register by name.", freestanding_only=True),
        Intrinsic("msr", ("str", "i64"), "void", "Write a system register by name.", freestanding_only=True),
        Intrinsic("current_el", (), "i64", "Read the current exception level.", freestanding_only=True),
        ),
        "interrupts": (
        Intrinsic("daif_set", ("i64",), "void", "Mask interrupts.", freestanding_only=True),
        Intrinsic("daif_clear", ("i64",), "void", "Unmask interrupts.", freestanding_only=True),
        Intrinsic("vbar_write", ("ptr",), "void", "Set the vector base address.", freestanding_only=True),
        ),
        "emit_raw": (
        Intrinsic("emit", ("i32",), "void", "Emit one raw 32-bit instruction word."),
        ),
    },
)
