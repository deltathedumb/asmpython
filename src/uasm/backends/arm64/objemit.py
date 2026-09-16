"""One ELF object from a whole module, for AArch64.

THE SAME SHAPE AS THE x86-64 ONE, and deliberately: sections, symbols and
relocation shifting do not depend on the architecture, which is the claim
`backend/objfile` makes and this file is the second witness to it. What
differs is which relocations exist and what the assembler directives were,
so those are the only things spelled out here.

WHY IT IS NOT SHARED WITH THE x86-64 COPY. The two differ in the directive
list they drop, the machine number, and how a function's lines are obtained.
Factoring that into one function with three parameters would put the
architecture back inside the format writer -- the mistake the objfile
docstring names -- to save about forty lines.
"""
from __future__ import annotations

from ...backend.objfile import (
    EM_AARCH64, SHF_ALLOC, SHF_EXECINSTR, SHF_WRITE, SHT_NOBITS,
    STB_GLOBAL, STB_LOCAL, STT_FUNC, STT_OBJECT,
    ElfObject, Relocation, Symbol,
)
from ...ir import Module
from ...ir.module import Linkage
from .encode import encode_function

#: Assembler directives, which describe rather than encode. Their information
#: goes into the symbol's own fields; see the x86-64 objemit docstring, which
#: makes the argument at length.
_DIRECTIVES = (".globl", ".global", ".type", ".size", ".text", ".data",
               ".section", ".align", ".p2align", ".balign", ".zero", ".byte",
               ".quad", ".word", ".xword", ".comm", ".local", ".hidden",
               ".weak", ".file", ".ident", ".cfi_startproc", ".cfi_endproc")


def _is_directive(line: str) -> bool:
    stripped = line.split("//")[0].strip()
    if not stripped or stripped.startswith(("#", "//")):
        return True
    # A LOCAL LABEL STARTS WITH A DOT TOO. The colon is what tells them apart,
    # and dropping `.L...:` would leave every branch pointing at nothing.
    if stripped.endswith(":"):
        return False
    return stripped.split()[0].lower() in _DIRECTIVES


def object_bytes(backend, module: Module, abi, dialect) -> bytes:
    """The module as one relocatable ELF64 object."""
    obj = ElfObject(EM_AARCH64)
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
            binding=(STB_GLOBAL if fn.linkage is Linkage.EXPORT
                     else STB_LOCAL),
            kind=STT_FUNC))
        for at, sym, kind, addend in enc.relocs:
            # SHIFTED BY WHERE THIS FUNCTION LANDED. The encoder counts from
            # the start of one function because that is its only frame of
            # reference; the section is the concatenation.
            text_relocs.append(Relocation(offset=base + at, symbol=sym,
                                          kind=kind, addend=addend))

    obj.section(".text", bytes(text), flags=SHF_ALLOC | SHF_EXECINSTR,
                align=4)
    for rel in text_relocs:
        obj.relocate(".text", rel)

    data = bytearray()
    bss = 0
    for g in module.globals:
        binding = STB_GLOBAL if g.linkage is Linkage.EXPORT else STB_LOCAL
        name = backend.global_symbol(g.name, dialect)
        align = g.align or 8
        if g.data is None:
            bss = (bss + align - 1) & ~(align - 1)
            symbols.append(Symbol(name=name, section=".bss", value=bss,
                                  size=max(1, g.size), binding=binding,
                                  kind=STT_OBJECT))
            bss += max(1, g.size)
        else:
            at = (len(data) + align - 1) & ~(align - 1)
            data += b"\0" * (at - len(data))
            symbols.append(Symbol(name=name, section=".data", value=at,
                                  size=len(g.data), binding=binding,
                                  kind=STT_OBJECT))
            data += bytes(g.data)
    if data:
        obj.section(".data", bytes(data), flags=SHF_ALLOC | SHF_WRITE,
                    align=8)
    if bss:
        obj.section(".bss", b"", kind=SHT_NOBITS,
                    flags=SHF_ALLOC | SHF_WRITE, align=8, size_override=bss)

    # UNDEFINED SYMBOLS LAST, and only what nothing here defines. A relocation
    # naming a symbol with no entry is an object the linker rejects with an
    # index error rather than a missing-symbol message.
    defined = {s.name for s in symbols}
    for rel in text_relocs:
        if rel.symbol not in defined:
            defined.add(rel.symbol)
            symbols.append(Symbol(name=rel.symbol, section="",
                                  binding=STB_GLOBAL))

    obj.section(".note.GNU-stack", b"", flags=0, align=1)
    for sym in symbols:
        obj.symbol(sym)
    return obj.to_bytes()
