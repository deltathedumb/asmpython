"""One ELF object from a whole module: the other half of finishing the backend.

`encode.py` answers what one instruction's bytes are. This lays those bytes
out into sections, gives every function and global a symbol, and shifts each
function's relocations by where that function landed -- which is the only
thing concatenation makes non-obvious.

WHAT IT DOES NOT DO. It does not link. A relocatable object still names what
it needs and leaves the addresses to `ld`, which is the right division: a
compiler that also linked would have to know about shared libraries, and the
system linker already does.

THE DIRECTIVE LINES ARE DROPPED HERE, and that is worth saying because it
looks like information loss. `.globl`, `.type` and `.size` describe a symbol,
and a symbol in an object file carries that description in its own fields --
binding, kind and size are arguments to `Symbol`, not text. The directives
were how those fields are spelled TO AN ASSEMBLER; with no assembler in the
path they are spelled directly instead.
"""
from __future__ import annotations

from ...backend.objfile import (
    EM_X86_64, SHF_ALLOC, SHF_EXECINSTR, SHF_WRITE, SHT_NOBITS, SHT_PROGBITS,
    STB_GLOBAL, STB_LOCAL, STT_FUNC, STT_OBJECT,
    ElfObject, Relocation, Symbol,
)
from ...ir import Module
from ...ir.module import Linkage
from .encode import encode_function

#: Assembler directives, which describe rather than encode. See the module
#: docstring for where the information they carried goes instead.
_DIRECTIVES = (".globl", ".type", ".size", ".text", ".data", ".section",
               ".align", ".p2align", ".def", ".scl", ".endef", ".zero",
               ".byte", ".quad", ".long")


def _is_directive(line: str) -> bool:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return True
    # A LOCAL LABEL STARTS WITH A DOT TOO -- `.Lmain_entry:` is not a
    # directive, and dropping it would leave every branch in the function
    # pointing at nothing. The colon is what tells them apart.
    if stripped.endswith(":"):
        return False
    return stripped.split()[0] in _DIRECTIVES


def object_bytes(backend, module: Module, abi, dialect) -> bytes:
    """The module as one relocatable ELF64 object."""
    obj = ElfObject(EM_X86_64)
    text = bytearray()
    text_relocs: list[Relocation] = []
    symbols: list[Symbol] = []

    for fn in module.defined_functions():
        lines = backend._function(fn, abi, dialect)
        enc = encode_function([one for one in lines if not _is_directive(one)])
        base = len(text)
        text += enc.code
        name = backend.symbol(fn.name, dialect)
        symbols.append(Symbol(
            name=name, section=".text", value=base, size=len(enc.code),
            binding=(STB_GLOBAL if fn.linkage is Linkage.EXPORT
                     else STB_LOCAL),
            kind=STT_FUNC))
        for at, sym, kind, addend in enc.relocs:
            # SHIFTED BY WHERE THIS FUNCTION LANDED. The encoder works in
            # offsets from the start of one function, because that is the only
            # frame of reference it has; the section is the concatenation, and
            # a relocation not moved with its bytes patches whatever happens
            # to sit at that offset in the first function.
            text_relocs.append(Relocation(offset=base + at, symbol=sym,
                                          kind=kind, addend=addend))

    obj.section(".text", bytes(text), flags=SHF_ALLOC | SHF_EXECINSTR,
                align=16)
    for rel in text_relocs:
        obj.relocate(".text", rel)

    # ── globals ─────────────────────────────────────────────────────────────
    #
    # INITIALISED ONES GO IN `.data` AND THE REST IN `.bss`, which is what
    # `SHT_NOBITS` is for: a zeroed global occupies no space in the file and
    # the loader provides the zeroes. A megabyte of zeroed runtime state would
    # otherwise be a megabyte of zeroes on disk.
    data = bytearray()
    bss = 0
    for g in module.globals:
        name = backend.global_symbol(g.name, dialect)
        binding = STB_GLOBAL if g.linkage is Linkage.EXPORT else STB_LOCAL
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

    # UNDEFINED SYMBOLS LAST, and only the ones nothing here defines. A
    # relocation naming a symbol with no entry at all is an object the linker
    # rejects with an index error rather than a missing-symbol message.
    defined = {s.name for s in symbols}
    for rel in text_relocs:
        if rel.symbol not in defined:
            defined.add(rel.symbol)
            symbols.append(Symbol(name=rel.symbol, section="",
                                  binding=STB_GLOBAL))

    # A NON-EXECUTABLE STACK, asserted the way the directive used to. Without
    # the marker a linker assumes the worst and marks the whole binary's stack
    # executable, which is a real difference in the produced program.
    obj.section(".note.GNU-stack", b"", flags=0, align=1)

    for sym in symbols:
        obj.symbol(sym)
    return obj.to_bytes()
