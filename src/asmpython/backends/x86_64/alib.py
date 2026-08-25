"""`x86_64.alib` -- the x86-64 architecture library.

x86 IS THE ONE MACHINE WITH TWO ADDRESS SPACES. Every other target here
maps devices into memory; x86 also has 65536 I/O ports reached only by `in`
and `out`, which is why `ports` exists as a capability at all rather than
being folded into MMIO.

MOST OF THIS IS EMITTED. `alib_emit.py` holds the instruction sequence for
each intrinsic that carries a `lowering=`, and the backend splices it where
the call would have gone. The ones still without it are declared only, and
`asmpython alibs` says which -- a declared intrinsic a backend silently
ignored would be the worst of the three states.

It reaches a program as an ordinary module: `Backend.modules` carries it, so
`from x86_64.alib import outb` is an import and `outb(0x3F8, 65)` is a call,
both exactly as CPython parses them. No syntax was added to say this.
"""
from __future__ import annotations

from ...backend.alib import Alib, Intrinsic, mmio_group

ALIB = Alib(
    arch="x86_64",
    doc="x86-64: ports, MSRs, and the instructions no other machine has.",
    groups={
        "instructions": (
        Intrinsic("rdtsc", (), "i64", "Read the timestamp counter.", lowering="rdtsc"),
        Intrinsic("rdtscp", (), "i64", "Read the timestamp counter, serialising first.", lowering="rdtscp"),
        Intrinsic("cpuid", ("i32", "i32"), "ptr", "Query CPU identification; returns eax:ebx:ecx:edx."),
        Intrinsic("pause", (), "void", "Hint that this is a spin-wait loop.", lowering="pause"),
        Intrinsic("popcnt64", ("i64",), "i64", "Count set bits.", lowering="popcntq"),
        Intrinsic("bswap64", ("i64",), "i64", "Reverse byte order.", lowering="bswapq"),
        Intrinsic("rdrand64", (), "i64", "Read a hardware random value.", lowering="rdrand"),
        Intrinsic("hlt", (), "void", "Halt until the next interrupt.", freestanding_only=True, lowering="hlt"),
        # AN INDIRECT CALL, which is architectural in the way the rest of
        # this file is: WHICH REGISTERS carry arguments is the ABI's answer,
        # and shuffling a computed target out of the first one before
        # overwriting it has no spelling in any language. A VM that learns a
        # function's address at run time has no other way to reach it.
        #
        # FIVE ARGUMENTS ALWAYS, whatever the callee takes. System V passes
        # in registers and a callee reads only the ones it declares, so
        # setting all five to call a two-argument function is harmless --
        # which means one intrinsic covers every arity up to five instead of
        # six near-identical ones.
        Intrinsic("native_call", ("ptr", "i64", "i64", "i64", "i64", "i64"),
                  "i64", "Call a function by address, System V, five "
                         "argument registers.", lowering="call"),
        ),
        "ports": (
        Intrinsic("inb", ("i16",), "i8", "Read a byte from an I/O port.", freestanding_only=True, lowering="inb"),
        Intrinsic("inw", ("i16",), "i16", "Read a word from an I/O port.", freestanding_only=True, lowering="inw"),
        Intrinsic("inl", ("i16",), "i32", "Read a dword from an I/O port.", freestanding_only=True, lowering="inl"),
        Intrinsic("outb", ("i16", "i8"), "void", "Write a byte to an I/O port.", freestanding_only=True, lowering="outb"),
        Intrinsic("outw", ("i16", "i16"), "void", "Write a word to an I/O port.", freestanding_only=True, lowering="outw"),
        Intrinsic("outl", ("i16", "i32"), "void", "Write a dword to an I/O port.", freestanding_only=True, lowering="outl"),
        ),
        # On x86 a device access IS a mov -- there is no separate load
# instruction for it, only the promise that it happens once.
        "mmio": mmio_group("mov") + (
        # THE STRING INSTRUCTIONS, and they are here because a framebuffer
        # makes them structural rather than an optimisation. Filling a
        # 640x480 screen is 307,200 stores; written as a loop in a language
        # whose runtime allocates, that is eighty megabytes of garbage to
        # paint one colour. `rep stosl` is one instruction that does the
        # whole thing in registers.
        Intrinsic("mmio_fill32", ("ptr", "i32", "i64"), "void",
                  "Fill a run of 32-bit words with one value.",
                  lowering="rep stosl"),
        Intrinsic("mmio_copy32", ("ptr", "ptr", "i64"), "void",
                  "Copy a run of 32-bit words.", lowering="rep movsl"),
        ),
        "barriers": (
        Intrinsic("mfence", (), "void", "Order all loads and stores.", lowering="mfence"),
        Intrinsic("sfence", (), "void", "Order all stores.", lowering="sfence"),
        Intrinsic("lfence", (), "void", "Order all loads.", lowering="lfence"),
        Intrinsic("clflush", ("ptr",), "void", "Flush a cache line to memory.", lowering="clflush"),
        ),
        "sysregs": (
        Intrinsic("rdmsr", ("i32",), "i64", "Read a model-specific register.", freestanding_only=True, lowering="rdmsr"),
        Intrinsic("wrmsr", ("i32", "i64"), "void", "Write a model-specific register.", freestanding_only=True, lowering="wrmsr"),
        Intrinsic("read_cr0", (), "i64", "Read control register 0.", freestanding_only=True, lowering="movq"),
        Intrinsic("read_cr2", (), "i64", "Read the faulting address after a page fault.", freestanding_only=True, lowering="movq"),
        Intrinsic("read_cr4", (), "i64", "Read control register 4.", freestanding_only=True, lowering="movq"),
        Intrinsic("write_cr0", ("i64",), "void", "Write control register 0.", freestanding_only=True, lowering="movq"),
        Intrinsic("write_cr4", ("i64",), "void", "Write control register 4.", freestanding_only=True, lowering="movq"),
        Intrinsic("invlpg", ("ptr",), "void", "Drop one page's translation from the TLB.", freestanding_only=True, lowering="invlpg"),
        Intrinsic("read_cr3", (), "i64", "Read the page-table base.", freestanding_only=True, lowering="movq"),
        Intrinsic("write_cr3", ("i64",), "void", "Set the page-table base.", freestanding_only=True, lowering="xorl"),
        ),
        "interrupts": (
        Intrinsic("cli", (), "void", "Disable maskable interrupts.", freestanding_only=True, lowering="cli"),
        Intrinsic("sti", (), "void", "Enable maskable interrupts.", freestanding_only=True, lowering="sti"),
        Intrinsic("lidt", ("ptr",), "void", "Load the interrupt descriptor table.", freestanding_only=True, lowering="lidt"),
        Intrinsic("lgdt", ("ptr",), "void", "Load the global descriptor table.", freestanding_only=True, lowering="lgdt"),
        Intrinsic("ltr", ("i16",), "void", "Load the task register from a selector.", freestanding_only=True, lowering="ltr"),
        ),
        "emit_raw": (
        Intrinsic("emit", ("ptr", "i64"), "void", "Emit raw bytes into the instruction stream."),
        ),
    },
)
