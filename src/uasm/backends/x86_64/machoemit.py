"""One Mach-O object from a whole module, for x86-64.

WHAT IT TRANSLATES. The encoder speaks ELF, and Mach-O differs in three ways
that are all this file's to bridge because all three are facts about x86-64:

  * THE NUMBER. A call and a data reference are one ELF relocation each --
    `PLT32` and `PC32` -- and two different Mach-O ones, `BRANCH` and
    `SIGNED`. They are not interchangeable: `SIGNED` on a call resolves
    through a different path in the linker.

  * THE ADDEND. ELF says "the symbol, minus four"; a Mach-O PC-relative
    relocation already measures from the end of the field, so the same
    reference carries nothing.

  * `pcrel` AND `length` are part of a Mach-O relocation record rather than
    implied by its type, so they must be supplied.

THE SYMBOL PREFIX IS NOT APPLIED HERE. `dialect.symbol_prefix` is an
underscore on this platform and `backend.symbol` applies it, which is what
keeps a definition and the call to it from disagreeing -- the same reason the
assembly path routes every name through one function.
"""
from __future__ import annotations

from ...backend.objfile.macho import (
    CPU_SUBTYPE_X86_64_ALL, CPU_TYPE_X86_64, S_ATTR_PURE_INSTRUCTIONS,
    S_ATTR_SOME_INSTRUCTIONS, S_REGULAR, S_ZEROFILL,
    MachoObject, Relocation, Symbol,
)
from ...ir import Module
from ...ir.module import Linkage
from .encode import R_X86_64_PC32, R_X86_64_PLT32, encode_function
from .objemit import _is_directive

#: Mach-O relocation types for x86-64, from `mach-o/x86_64/reloc.h`.
X86_64_RELOC_UNSIGNED = 0
X86_64_RELOC_SIGNED = 1
X86_64_RELOC_BRANCH = 2

#: ELF relocation to Mach-O type, whether it is PC-relative, the log2 of its
#: field width, and the addend Mach-O applies on its own.
_TRANSLATION = {
    R_X86_64_PLT32: (X86_64_RELOC_BRANCH, True, 2, -4),
    R_X86_64_PC32: (X86_64_RELOC_SIGNED, True, 2, -4),
}

_CODE = S_REGULAR | S_ATTR_PURE_INSTRUCTIONS | S_ATTR_SOME_INSTRUCTIONS


def _translate(kind: int, addend: int, offset: int, symbol: str) -> Relocation:
    try:
        number, pcrel, length, implicit = _TRANSLATION[kind]
    except KeyError:
        raise NotImplementedError(
            f"no Mach-O spelling for ELF relocation {kind}; the x86-64 "
            f"encoder produced one this file has not been taught") from None
    return Relocation(offset, symbol, number, addend - implicit,
                      pcrel=pcrel, length=length)


def object_bytes(backend, module: Module, abi, dialect) -> bytes:
    """The module as one relocatable Mach-O 64 object."""
    obj = MachoObject(CPU_TYPE_X86_64, CPU_SUBTYPE_X86_64_ALL)
    text = bytearray()
    text_relocs: list[Relocation] = []
    symbols: list[Symbol] = []

    for fn in module.defined_functions():
        lines = backend._function(fn, abi, dialect)
        enc = encode_function([one for one in lines if not _is_directive(one)])
        base = len(text)
        text += enc.code
        symbols.append(Symbol(
            name=backend.symbol(fn.name, dialect), section=".text",
            value=base, size=len(enc.code),
            binding=(1 if fn.linkage is Linkage.EXPORT else 0), kind=2))
        for at, sym, kind, addend in enc.relocs:
            text_relocs.append(_translate(kind, addend, base + at, sym))

    obj.section(".text", bytes(text), flags=_CODE, align=16)

    data = bytearray()
    bss = 0
    for g in module.globals:
        name = backend.global_symbol(g.name, dialect)
        binding = 1 if g.linkage is Linkage.EXPORT else 0
        align = g.align or 8
        if g.data is None:
            bss = (bss + align - 1) & ~(align - 1)
            symbols.append(Symbol(name=name, section=".bss", value=bss,
                                  size=max(1, g.size), binding=binding))
            bss += max(1, g.size)
        else:
            at = (len(data) + align - 1) & ~(align - 1)
            data += b"\0" * (at - len(data))
            symbols.append(Symbol(name=name, section=".data", value=at,
                                  size=len(g.data), binding=binding))
            data += bytes(g.data)
    if data:
        obj.section(".data", bytes(data), align=8)
    if bss:
        obj.section(".bss", b"", flags=S_ZEROFILL, align=8, size_override=bss)

    defined = {s.name for s in symbols}
    for rel in text_relocs:
        if rel.symbol not in defined:
            defined.add(rel.symbol)
            symbols.append(Symbol(name=rel.symbol, section="", binding=1))

    for sym in symbols:
        obj.symbol(sym)
    # DESCENDING BY ADDRESS, which is the order every Mach-O producer writes
    # and what ld64 expects to be able to scan.
    for rel in sorted(text_relocs, key=lambda r: -r.offset):
        obj.relocate(".text", rel)
    return obj.to_bytes()
