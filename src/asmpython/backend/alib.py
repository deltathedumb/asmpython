"""What an alib IS, before any architecture declares one.

An **alib** is the architecture library: the part of a target a portable
language cannot reach. `math.sqrt` is a call into a runtime that every backend
shares; `rdtsc` is four bytes of x86 that no other machine has, and `mmio32`
is a load the optimiser must never move, fold or elide. Neither belongs in the
standard library, because the standard library is the half that is the same
everywhere.

    from x86_64.alib import rdtsc, outb
    from arm64.alib import mmio32_write

ONE NAME PER ARCHITECTURE, `<arch>.alib`, and it is never implicit. A program
that reaches for `rdtsc` has named x86-64 in its own source and stops being
portable at that line, which is the honest place for it to stop -- rather than
in a build for a machine that has no such instruction, months later.

WHY A TABLE AND NOT A LIBRARY OF FUNCTIONS. There is nothing to call. Every
entry here is realised INLINE by a backend -- `rdtsc` is an instruction, not a
symbol -- so what travels from an architecture to a backend is a description,
and the backend decides how to spell it. That is the same bargain
`Backend.modules` already makes, and this reuses the shape so the frontend
needs no second mechanism.

STUBS ARE MARKED, NOT IMPLIED. `Intrinsic.lowering` empty means no backend
emits this yet: the surface is declared, the code generation is not written,
and `asmpython alibs` says so per entry. A declared intrinsic a backend
silently ignored would be the worst of the three states -- a program that
compiles, links, and does not do what it says.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: The capabilities an architecture may or may not have. Declared per alib
#: rather than assumed, because the answer really does differ: a JVM has no
#: address space to map a device into, and saying so is more useful than
#: offering an `mmio32` that cannot work.
CAPABILITIES = (
    #: Reads and writes at an absolute address that must not be reordered,
    #: cached or elided. The whole of device programming.
    "mmio",
    #: A separate I/O address space reached by dedicated instructions.
    #: x86 only; every other machine maps devices into memory.
    "ports",
    #: Named instructions with no portable equivalent -- `rdtsc`, `cpuid`,
    #: `wfi`, `dmb`.
    "instructions",
    #: Ordering and cache-maintenance operations.
    "barriers",
    #: Privileged/system registers, read and written by name.
    "sysregs",
    #: Interrupt enable/disable and the vector table. Freestanding only.
    "interrupts",
    #: Raw bytes emitted into the instruction stream, for what nothing else
    #: here covers.
    "emit_raw",
)


@dataclass(frozen=True, slots=True)
class Intrinsic:
    """One thing an architecture can do that the IR has no opcode for."""

    #: As a program writes it: `from x86_64.alib import rdtsc`.
    name: str
    #: IR type names, in order. `()` for a niladic instruction.
    params: tuple[str, ...]
    #: IR type name, or "void".
    result: str
    #: One line, in the imperative. Shown by `asmpython alibs`.
    doc: str
    #: HOW A BACKEND REALISES IT -- a mnemonic for a machine backend, a
    #: pseudo-op elsewhere. EMPTY MEANS UNIMPLEMENTED, and is the difference
    #: between a declared surface and a working one.
    lowering: str = ""
    #: True when this may only be used by a freestanding build. Reading a
    #: system register under an operating system is a fault, not a value.
    freestanding_only: bool = False

    @property
    def implemented(self) -> bool:
        return bool(self.lowering)

    @property
    def signature(self) -> str:
        return f"({', '.join(self.params)}) -> {self.result}"


@dataclass(frozen=True, slots=True)
class Alib:
    """One architecture's library, as `<arch>.alib`."""

    #: Matches `Target.arch`, and is the first half of the module name.
    arch: str
    #: What this architecture is, and what its alib does NOT offer.
    doc: str
    #: Capability -> the intrinsics in it. Keys come from `CAPABILITIES`.
    groups: dict[str, tuple[Intrinsic, ...]] = field(default_factory=dict)

    @property
    def module_name(self) -> str:
        """`x86_64.alib` -- what an `import` statement names."""
        return f"{self.arch}.alib"

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(k for k in CAPABILITIES if k in self.groups)

    def all_intrinsics(self) -> tuple[Intrinsic, ...]:
        return tuple(i for group in self.groups.values() for i in group)

    def validate(self) -> list[str]:
        """Everything wrong with this declaration, as messages.

        Checked rather than trusted: an alib is data, and the failure mode of
        wrong data is a name that type-checks and then reaches a backend with
        no idea what it is.
        """
        problems: list[str] = []
        for key in self.groups:
            if key not in CAPABILITIES:
                problems.append(
                    f"{self.arch}: {key!r} is not a capability "
                    f"(known: {', '.join(CAPABILITIES)})")
        seen: set[str] = set()
        for i in self.all_intrinsics():
            if i.name in seen:
                problems.append(f"{self.arch}: {i.name!r} declared twice")
            seen.add(i.name)
            if not i.doc:
                problems.append(f"{self.arch}: {i.name!r} has no doc")
        return problems


#: The universal MMIO surface. WRITTEN ONCE because it is the same question on
#: every machine that has an address space -- a width, an address, and the
#: promise that the access happens exactly once and in this order. An
#: architecture with no address space simply does not list the group.
def mmio_group(prefix: str = "") -> tuple[Intrinsic, ...]:
    """The eight MMIO accessors, for an architecture that has memory.

    `prefix` is the backend's mnemonic stem, or "" to declare the surface
    without implementing it.
    """
    out: list[Intrinsic] = []
    for bits in (8, 16, 32, 64):
        out.append(Intrinsic(
            f"mmio{bits}_read", ("ptr",), f"i{bits}",
            f"Read {bits} bits from an address, exactly once.",
            lowering=f"{prefix}load{bits}" if prefix else ""))
        out.append(Intrinsic(
            f"mmio{bits}_write", ("ptr", f"i{bits}"), "void",
            f"Write {bits} bits to an address, exactly once.",
            lowering=f"{prefix}store{bits}" if prefix else ""))
    return tuple(out)


#: Another spelling of an architecture -> the canonical one. `Target.arch` says
#: "aarch64" where the backend is called "arm64", and a lookup must not depend
#: on which the caller happened to be holding.
ALIASES = {
    "aarch64": "arm64",
    "arm": "arm32", "armv7": "arm32", "armv7a": "arm32",
    "i386": "x86_32", "i686": "x86_32", "x86": "x86_32",
    "amd64": "x86_64", "x64": "x86_64",
    "wasm32": "wasm", "webassembly": "wasm",
    "python": "pybc", "python_bytecode": "pybc",
    "opencir": "apir",
}


def for_module(name: str) -> "Alib | None":
    """The alib a module name reaches, or None.

    `x86_64.alib` -> the x86-64 backend's. THE SUFFIX IS REQUIRED: a bare
    `x86_64` is not an alib, because a program should not acquire privileged
    instructions by naming an architecture in passing.

    ASKED OF THE BACKENDS rather than of a registry of its own -- an alib
    belongs to the code generator that can emit it, so the backend table is
    the only list there needs to be.
    """
    arch, _, tail = name.rpartition(".")
    if tail != "alib":
        return None
    return by_arch(ALIASES.get(arch, arch))


def by_arch(arch: str) -> "Alib | None":
    """The alib for an architecture name, or None if no backend declares one."""
    from .base import available, load_builtin

    load_builtin()
    arch = ALIASES.get(arch, arch)
    for be in available().values():
        if be.alib is not None and be.alib.arch == arch:
            return be.alib
    return None


def all_alibs() -> dict:
    """Every declared alib, by architecture."""
    from .base import available, load_builtin

    load_builtin()
    return {be.alib.arch: be.alib for be in available().values()
            if be.alib is not None}

#: How an intrinsic is spelled as a backend module member. `modules.py`
#: documents the member shapes; this is one more of them:
#:
#:     ("intrinsic", symbol, (argument IR types...), result IR type)
#:
#: WHY A KIND OF ITS OWN rather than reusing `("call", symbol, arity)`. A
#: `call` member reaches a runtime function that takes `apy_value`s -- boxed
#: Python objects. An intrinsic is an INSTRUCTION, and an instruction takes a
#: machine word. The types are carried here so the frontend can unbox each
#: argument to the width the instruction reads, which is the same conversion
#: a `ctypes` call already gets and the reason this needs no new lowering.
INTRINSIC_KIND = "intrinsic"

#: The prefix that marks a symbol as an instruction rather than a function.
#: Reserved, so that a backend which declares an intrinsic and does not lower
#: it fails at the link naming `__alib_outb` -- which says both what was
#: wanted and that it was meant to be emitted inline, rather than silently
#: calling a symbol nobody defined.
SYMBOL_PREFIX = "__alib_"


def symbol_for(name: str) -> str:
    return SYMBOL_PREFIX + name


def intrinsic_named(symbol: str) -> str:
    """The intrinsic a reserved symbol names, or "" if it names none."""
    if symbol.startswith(SYMBOL_PREFIX):
        return symbol[len(SYMBOL_PREFIX):]
    return ""


#: `void` HAS NO SPELLING in the marshalling path an intrinsic rides on, so
#: it is carried as `i64` and every void lowering leaves zero in the result
#: register. Making the path understand a void return would be a change to
#: code four other things depend on, to express "ignore this".
_RESULT = {"void": "i64"}


def alib_modules(alib: "Alib | None") -> dict:
    """An alib as a backend module, in the shape `Backend.modules` takes.

    THIS IS HOW AN ALIB REACHES A PROGRAM. `from x86_64.alib import outb` is
    an ordinary import of an ordinary module -- resolved by
    `frontends/python/modules.py` through the same table a backend uses to
    offer anything else -- so the frontend needs no second mechanism and the
    language does not move. Nothing here is syntax: CPython 3.14 parses that
    import and that call exactly as written.

    KEYED BY THE MODULE'S REAL NAME, `<arch>.alib`, and not by the backend's
    id. The prefixed path `<backend>.<name>` cannot be spelled for this
    backend -- `x86-64` is not a Python identifier -- so the bare-name rule is
    the one that carries it, which is fine because nothing else claims a
    dotted name ending in `.alib`.
    """
    if alib is None:
        return {}
    members = {}
    for intrinsic in alib.all_intrinsics():
        members[intrinsic.name] = (
            INTRINSIC_KIND,
            symbol_for(intrinsic.name),
            tuple(intrinsic.params),
            _RESULT.get(intrinsic.result, intrinsic.result),
        )
    return {alib.module_name: members}
