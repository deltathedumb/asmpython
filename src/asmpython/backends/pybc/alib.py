"""`pybc.alib` -- the CPython bytecode architecture library.

THE SMALLEST ALIB HERE, and honestly so. A program compiled to CPython
bytecode runs inside an interpreter that owns its memory, so there is no
address space to map a device into, no privileged mode, and no instruction
stream to write bytes at. `mmio`, `ports`, `sysregs`, `interrupts` and
`emit_raw` are all absent.

`peek`/`poke` ARE DELIBERATELY NOT HERE even though `ctypes` could implement
them. They would work, they would be catastrophic, and an alib whose most
prominent member is a way to corrupt the interpreter is not a low-level
library -- it is a footgun with a namespace. A program that genuinely needs
raw memory on this target should use `ctypes` directly and say so.

Nothing here is emitted yet.
"""
from __future__ import annotations

from ...backend.alib import Alib, Intrinsic, mmio_group

ALIB = Alib(
    arch="pybc",
    doc="CPython bytecode: a managed interpreter, with no machine underneath to reach.",
    groups={
        "instructions": (
        Intrinsic("perf_counter_ns", (), "i64", "Read the highest-resolution clock available."),
        Intrinsic("popcnt64", ("i64",), "i64", "Count set bits."),
        Intrinsic("bit_length", ("i64",), "i64", "Bits needed to represent this value."),
        Intrinsic("refcount", ("ptr",), "i64", "The reference count of an object."),
        ),
    },
)
