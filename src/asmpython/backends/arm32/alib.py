"""`arm32.alib` -- the 32-bit ARM architecture library.

SYSTEM STATE LIVES BEHIND A COPROCESSOR HERE, not behind `mrs`/`msr` as it
does on AArch64. `mrc`/`mcr` take five numeric operands naming a coprocessor,
an opcode, and two register numbers -- so this alib exposes them numerically
and names the common ones on top, which is the only way to keep the
common case readable and the general case reachable.

RASPBERRY PI is the reason this architecture is worth having: it is the
freestanding ARM32 target most people can actually run, and its peripherals
are MMIO at a base address that differs per board generation. The base is NOT
declared here -- it is a property of the board, not of the architecture, and
an alib that hardcoded one would be wrong on three of the four Pis.

Nothing here is emitted yet.
"""
from __future__ import annotations

from ...backend.alib import Alib, Intrinsic, mmio_group

ALIB = Alib(
    arch="arm32",
    doc="ARMv7-A: coprocessor registers, and the Raspberry Pi's peripheral base.",
    groups={
        "instructions": (
        Intrinsic("wfi", (), "void", "Wait for an interrupt.", freestanding_only=True),
        Intrinsic("wfe", (), "void", "Wait for an event.", freestanding_only=True),
        Intrinsic("sev", (), "void", "Signal an event to other cores."),
        Intrinsic("yield_", (), "void", "Hint that this is a spin-wait loop."),
        Intrinsic("rbit32", ("i32",), "i32", "Reverse bit order."),
        Intrinsic("clz32", ("i32",), "i32", "Count leading zeros."),
        Intrinsic("rev32", ("i32",), "i32", "Reverse byte order."),
        ),
        "mmio": mmio_group(),
        "barriers": (
        Intrinsic("dmb", (), "void", "Data memory barrier."),
        Intrinsic("dsb", (), "void", "Data synchronisation barrier."),
        Intrinsic("isb", (), "void", "Instruction synchronisation barrier."),
        ),
        "sysregs": (
        Intrinsic("mrc", ("i32", "i32", "i32", "i32", "i32"), "i32", "Read a coprocessor register: cp, op1, crn, crm, op2.", freestanding_only=True),
        Intrinsic("mcr", ("i32", "i32", "i32", "i32", "i32", "i32"), "void", "Write a coprocessor register: cp, op1, crn, crm, op2, value.", freestanding_only=True),
        Intrinsic("read_sctlr", (), "i32", "Read the system control register.", freestanding_only=True),
        Intrinsic("read_cpsr", (), "i32", "Read the current program status register.", freestanding_only=True),
        ),
        "interrupts": (
        Intrinsic("cpsid_i", (), "void", "Disable IRQs.", freestanding_only=True),
        Intrinsic("cpsie_i", (), "void", "Enable IRQs.", freestanding_only=True),
        Intrinsic("set_vbar", ("ptr",), "void", "Set the vector base address.", freestanding_only=True),
        ),
        "emit_raw": (
        Intrinsic("emit", ("i32",), "void", "Emit one raw 32-bit instruction word."),
        ),
    },
)
