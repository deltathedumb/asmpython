"""One Mach-O object from a whole module, for AArch64.

WHAT IT TRANSLATES. Less than the x86-64 one, and for a pleasant reason: an
AArch64 address is already built from a page and an offset, and Mach-O spells
those with `PAGE21` and `PAGEOFF12` exactly as ELF spells them with
`ADR_PREL_PG_HI21` and `ADD_ABS_LO12_NC`. So the mapping is one number to
another, with no addend arithmetic at all -- every relocation this encoder
produces has an addend of zero.

WHICH IS WORTH SAYING, because Mach-O's way of carrying an addend on AArch64
is a SEPARATE `ARM64_RELOC_ADDEND` record placed before the one it modifies.
Nothing here needs it, and a non-zero addend is refused rather than dropped:
dropping one would resolve to the right symbol and the wrong field of it.
"""
from __future__ import annotations

from ...backend.objfile.macho import (
    CPU_SUBTYPE_ARM64_ALL, CPU_TYPE_ARM64, S_ATTR_PURE_INSTRUCTIONS,
    S_ATTR_SOME_INSTRUCTIONS, S_REGULAR, S_ZEROFILL,
    MachoObject, Relocation, Symbol,
)
from ...ir import Module
from ...ir.module import Linkage
from .encode import (
    R_AARCH64_ADD_ABS_LO12_NC, R_AARCH64_ADR_PREL_PG_HI21, R_AARCH64_CALL26,
    R_AARCH64_JUMP26, encode_function,
)
from .objemit import _is_directive

#: Mach-O relocation types for AArch64, from `mach-o/arm64/reloc.h`.
ARM64_RELOC_UNSIGNED = 0
ARM64_RELOC_BRANCH26 = 2
ARM64_RELOC_PAGE21 = 3
ARM64_RELOC_PAGEOFF12 = 4
ARM64_RELOC_ADDEND = 10

#: ELF relocation to Mach-O type, whether it is PC-relative, and the log2 of
#: its field width. Every AArch64 instruction is four bytes, so the length is
#: always 2 -- it describes the FIELD, and the field is the instruction.
_TRANSLATION = {
    R_AARCH64_CALL26: (ARM64_RELOC_BRANCH26, True, 2),
    R_AARCH64_JUMP26: (ARM64_RELOC_BRANCH26, True, 2),
    R_AARCH64_ADR_PREL_PG_HI21: (ARM64_RELOC_PAGE21, True, 2),
    R_AARCH64_ADD_ABS_LO12_NC: (ARM64_RELOC_PAGEOFF12, False, 2),
}

_CODE = S_REGULAR | S_ATTR_PURE_INSTRUCTIONS | S_ATTR_SOME_INSTRUCTIONS


def _translate(kind: int, addend: int, offset: int, symbol: str) -> Relocation:
    try:
        number, pcrel, length = _TRANSLATION[kind]
    except KeyError:
        raise NotImplementedError(
            f"no Mach-O spelling for ELF relocation {kind}; the AArch64 "
            f"encoder produced one this file has not been taught") from None
    if addend:
        # SEE THE MODULE DOCSTRING. Mach-O needs a second record for this, and
        # dropping the addend would reach the right symbol at the wrong offset.
        raise NotImplementedError(
            f"relocation against {symbol!r} carries an addend of {addend}, "
            f"which Mach-O spells with a separate ARM64_RELOC_ADDEND record "
            f"that this file does not write")
    return Relocation(offset, symbol, number, 0, pcrel=pcrel, length=length)


def object_bytes(backend, module: Module, abi, dialect) -> bytes:
    """The module as one relocatable Mach-O 64 object."""
    obj = MachoObject(CPU_TYPE_ARM64, CPU_SUBTYPE_ARM64_ALL)
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

    obj.section(".text", bytes(text), flags=_CODE, align=4)

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
    for rel in sorted(text_relocs, key=lambda r: -r.offset):
        obj.relocate(".text", rel)
    return obj.to_bytes()
