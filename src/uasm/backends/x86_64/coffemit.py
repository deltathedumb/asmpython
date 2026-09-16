"""One PE/COFF object from a whole module.

THE THIRD OF THESE, and the first that has to translate rather than transcribe.
`objemit.py` hands the encoder's relocations to the ELF writer unchanged
because the encoder speaks ELF's vocabulary; COFF's is different in two ways,
and both are this file's to bridge because both are facts about x86-64.

  * THE NUMBER. `R_X86_64_PC32` and `IMAGE_REL_AMD64_REL32` describe the same
    patch and are 2 and 4. A writer copying the number across would produce a
    file the linker reads as a different relocation entirely.

  * THE ADDEND. ELF spells a call's displacement as "measure from the symbol,
    minus four"; COFF's `REL32` measures from the end of the field already, so
    the same call carries no addend at all. Copying -4 across displaces every
    call four bytes earlier, into the middle of the previous instruction.

WHY THE TRANSLATION IS NOT IN `coff.py`. Deciding that -4 becomes zero
requires knowing that `REL32` is PC-relative and that its field is four bytes
-- which is what the number MEANS, and a format writer that knew would be the
same mistake as an assembly backend that knows about ELF.
"""
from __future__ import annotations

from ...backend.objfile import (
    IMAGE_FILE_MACHINE_AMD64, IMAGE_SCN_CNT_CODE,
    IMAGE_SCN_CNT_INITIALIZED_DATA, IMAGE_SCN_CNT_UNINITIALIZED_DATA,
    IMAGE_SCN_MEM_EXECUTE, IMAGE_SCN_MEM_READ, IMAGE_SCN_MEM_WRITE,
    CoffObject, CoffRelocation, CoffSymbol,
)
from ...ir import Module
from ...ir.module import Linkage
from .encode import R_X86_64_PC32, R_X86_64_PLT32, encode_function
from .objemit import _is_directive

#: COFF relocation numbers, from the PE specification.
IMAGE_REL_AMD64_ADDR64 = 0x0001
IMAGE_REL_AMD64_ADDR32NB = 0x0003
IMAGE_REL_AMD64_REL32 = 0x0004

#: What each of the encoder's relocations becomes, and what the field must
#: then hold. THE SECOND HALF IS THE IMPLICIT ADDEND COFF APPLIES: `REL32`
#: measures from the end of its own four-byte field, so a reference ELF
#: spells with an addend of -4 is spelled here with nothing.
_TRANSLATION = {
    R_X86_64_PC32: (IMAGE_REL_AMD64_REL32, -4),
    R_X86_64_PLT32: (IMAGE_REL_AMD64_REL32, -4),
}


def _translate(kind: int, addend: int) -> tuple[int, int]:
    """One ELF relocation as COFF spells it: its number and its stored addend."""
    try:
        number, implicit = _TRANSLATION[kind]
    except KeyError:
        raise NotImplementedError(
            f"no COFF spelling for ELF relocation {kind}; the x86-64 encoder "
            f"produced one this file has not been taught") from None
    return number, addend - implicit


_CODE = IMAGE_SCN_CNT_CODE | IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_MEM_READ
_DATA = (IMAGE_SCN_CNT_INITIALIZED_DATA | IMAGE_SCN_MEM_READ
         | IMAGE_SCN_MEM_WRITE)
_BSS = (IMAGE_SCN_CNT_UNINITIALIZED_DATA | IMAGE_SCN_MEM_READ
        | IMAGE_SCN_MEM_WRITE)


def object_bytes(backend, module: Module, abi, dialect) -> bytes:
    """The module as one relocatable PE/COFF object."""
    obj = CoffObject(IMAGE_FILE_MACHINE_AMD64)
    text = bytearray()
    text_relocs: list[CoffRelocation] = []
    symbols: list[CoffSymbol] = []

    for fn in module.defined_functions():
        lines = backend._function(fn, abi, dialect)
        enc = encode_function([one for one in lines if not _is_directive(one)])
        base = len(text)
        text += enc.code
        symbols.append(CoffSymbol(
            name=backend.symbol(fn.name, dialect), section=".text",
            value=base, size=len(enc.code),
            binding=(1 if fn.linkage is Linkage.EXPORT else 0), kind=2))
        for at, sym, kind, addend in enc.relocs:
            number, stored = _translate(kind, addend)
            text_relocs.append(CoffRelocation(offset=base + at, symbol=sym,
                                              kind=number, addend=stored))

    obj.section(".text", bytes(text), characteristics=_CODE, align=16)

    data = bytearray()
    bss = 0
    for g in module.globals:
        name = backend.global_symbol(g.name, dialect)
        binding = 1 if g.linkage is Linkage.EXPORT else 0
        align = g.align or 8
        if g.data is None:
            bss = (bss + align - 1) & ~(align - 1)
            symbols.append(CoffSymbol(name=name, section=".bss", value=bss,
                                      size=max(1, g.size), binding=binding))
            bss += max(1, g.size)
        else:
            at = (len(data) + align - 1) & ~(align - 1)
            data += b"\0" * (at - len(data))
            symbols.append(CoffSymbol(name=name, section=".data", value=at,
                                      size=len(g.data), binding=binding))
            data += bytes(g.data)
    if data:
        obj.section(".data", bytes(data), characteristics=_DATA, align=8)
    if bss:
        obj.section(".bss", b"", characteristics=_BSS, align=8,
                    size_override=bss)

    # UNDEFINED SYMBOLS LAST, and only what nothing here defines. COFF names a
    # relocation's symbol by INDEX, so one with no record at all is not a
    # missing-symbol message but an index into whatever happens to be there.
    defined = {s.name for s in symbols}
    for rel in text_relocs:
        if rel.symbol not in defined:
            defined.add(rel.symbol)
            symbols.append(CoffSymbol(name=rel.symbol, section="", binding=1))

    # THE SYMBOLS BEFORE THE RELOCATIONS, because the writer resolves a
    # relocation's symbol to its index and refuses one it cannot find.
    for sym in symbols:
        obj.symbol(sym)
    for rel in text_relocs:
        obj.relocate(".text", rel)
    return obj.to_bytes()
