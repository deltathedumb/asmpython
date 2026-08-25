"""`x86_32.alib` -- the 32-bit x86 architecture library.

THE SAME MACHINE AS `x86_64.alib` WITH A NARROWER WORD, and it is a separate
alib rather than a flag on that one because the TYPES differ: a pointer is
`i32`, `rdtsc` still answers 64 bits in two registers, and `popcnt` takes an
i32. An alib whose signatures depended on a target flag would type-check
against the wrong widths for one of the two.

Nothing here is emitted yet.
"""
from __future__ import annotations

from ...backend.alib import Alib, Intrinsic, mmio_group

ALIB = Alib(
    arch="x86_32",
    doc="x86-32: the same two address spaces, in 32 bits.",
    groups={
        "instructions": (
        Intrinsic("rdtsc", (), "i64", "Read the timestamp counter."),
        Intrinsic("cpuid", ("i32", "i32"), "ptr", "Query CPU identification."),
        Intrinsic("pause", (), "void", "Hint that this is a spin-wait loop."),
        Intrinsic("popcnt32", ("i32",), "i32", "Count set bits."),
        Intrinsic("bswap32", ("i32",), "i32", "Reverse byte order."),
        Intrinsic("hlt", (), "void", "Halt until the next interrupt.", freestanding_only=True),
        ),
        "ports": (
        Intrinsic("inb", ("i16",), "i8", "Read a byte from an I/O port.", freestanding_only=True),
        Intrinsic("inw", ("i16",), "i16", "Read a word from an I/O port.", freestanding_only=True),
        Intrinsic("inl", ("i16",), "i32", "Read a dword from an I/O port.", freestanding_only=True),
        Intrinsic("outb", ("i16", "i8"), "void", "Write a byte to an I/O port.", freestanding_only=True),
        Intrinsic("outw", ("i16", "i16"), "void", "Write a word to an I/O port.", freestanding_only=True),
        Intrinsic("outl", ("i16", "i32"), "void", "Write a dword to an I/O port.", freestanding_only=True),
        ),
        "mmio": mmio_group(),
        "barriers": (
        Intrinsic("mfence", (), "void", "Order all loads and stores."),
        Intrinsic("clflush", ("ptr",), "void", "Flush a cache line to memory."),
        ),
        "sysregs": (
        Intrinsic("read_cr0", (), "i32", "Read control register 0.", freestanding_only=True),
        Intrinsic("read_cr3", (), "i32", "Read the page-table base.", freestanding_only=True),
        ),
        "interrupts": (
        Intrinsic("cli", (), "void", "Disable maskable interrupts.", freestanding_only=True),
        Intrinsic("sti", (), "void", "Enable maskable interrupts.", freestanding_only=True),
        ),
        "emit_raw": (
        Intrinsic("emit", ("ptr", "i32"), "void", "Emit raw bytes into the instruction stream."),
        ),
    },
)
