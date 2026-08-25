"""Writing an ELF64 relocatable object, byte by byte.

WHY A BACKEND NEEDS THIS AT ALL. A backend that emits assembly has not
finished: `as` does the encoding and `as` writes the object, so the backend
has never once decided what a byte of its output is. That reads as working --
the file appears, the linker takes it, the program runs -- and it means the
compiler cannot produce a program without a toolchain that includes an
assembler for the target. This module is the second half of that job for
every ELF platform; `coff.py` and `macho.py` are the same shape for the other
two formats.

ARCHITECTURE-NEUTRAL ON PURPOSE. Nothing here knows an instruction. It takes
bytes, symbols and relocations and lays out a file, so x86-64, AArch64 and
ARM32 share one implementation of a format that does not vary between them --
`machine` and the relocation numbers are the only difference, and both are
arguments.

WHAT AN ELF RELOCATABLE OBJECT IS, since the layout below assumes it:

    ELF header            what kind of file, and where the section table is
    section contents      .text, .data, .rodata, .bss (which has none)
    .symtab               every name this file defines or needs
    .strtab               the characters those names are made of
    .rela.*               "patch this offset to point at that symbol"
    .shstrtab             the characters the SECTION names are made of
    section header table  one 64-byte entry describing each of the above

THE TWO ORDERING RULES that are easy to get wrong and that a linker enforces:

  * LOCAL SYMBOLS COME FIRST in `.symtab`, and `sh_info` is the index of the
    first non-local one. A linker uses that index to skip locals when
    resolving; get it wrong and it either misses definitions or treats file
    scope as global.
  * `sh_link` ON A SYMBOL TABLE names its string table, and on a relocation
    section names the symbol table its indices refer to. They are not implied
    by order.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

# Machines, from the ELF register. Only the ones a backend here targets.
EM_386 = 3
EM_ARM = 40
EM_X86_64 = 62
EM_AARCH64 = 183

# Section types and flags.
SHT_NULL, SHT_PROGBITS, SHT_SYMTAB, SHT_STRTAB, SHT_RELA, SHT_NOBITS = (
    0, 1, 2, 3, 4, 8)
SHF_WRITE, SHF_ALLOC, SHF_EXECINSTR = 0x1, 0x2, 0x4

# Symbol binding and type, packed together into `st_info`.
STB_LOCAL, STB_GLOBAL, STB_WEAK = 0, 1, 2
STT_NOTYPE, STT_OBJECT, STT_FUNC, STT_SECTION = 0, 1, 2, 3

#: `st_shndx` for a symbol this file does not define -- an import.
SHN_UNDEF = 0

_EHDR = "<16sHHIQQQIHHHHHH"
_SHDR = "<IIQQQQIIQQ"
_SYM = "<IBBHQQ"
_RELA = "<QQq"


class _Strings:
    """A string table, and the offset of every string put in it.

    DEDUPLICATED, because a symbol name and a section name are often the same
    characters and an object with a thousand functions should not carry the
    name of each twice. The empty string is at offset 0 by construction: ELF
    requires a leading NUL, and "no name" is expressed as an offset to it.
    """

    def __init__(self) -> None:
        self._buf = bytearray(b"\0")
        self._at: dict[str, int] = {"": 0}

    def put(self, text: str) -> int:
        if text not in self._at:
            self._at[text] = len(self._buf)
            self._buf += text.encode("utf-8") + b"\0"
        return self._at[text]

    def bytes(self) -> bytes:
        return bytes(self._buf)


@dataclass(slots=True)
class Symbol:
    """One name this object defines, or one it needs from elsewhere."""

    name: str
    #: The section it lives in, by name. Empty means UNDEFINED -- a name this
    #: object calls and does not define, which the linker must find.
    section: str = ""
    value: int = 0
    size: int = 0
    binding: int = STB_GLOBAL
    kind: int = STT_NOTYPE

    @property
    def is_local(self) -> bool:
        return self.binding == STB_LOCAL


@dataclass(slots=True)
class Relocation:
    """A place in a section that must be patched to reach a symbol."""

    #: Byte offset within the section being relocated.
    offset: int
    #: Index into the object's symbol list, resolved at write time by name.
    symbol: str
    #: The architecture's relocation number. Not interpreted here: what
    #: `R_AARCH64_CALL26` means is the linker's business and the backend's,
    #: and encoding that knowledge into a format writer would be the same
    #: mistake as an assembly backend that knows about ELF.
    kind: int
    addend: int = 0


@dataclass(slots=True)
class Section:
    """One section's content and how it should be described."""

    name: str
    data: bytes = b""
    kind: int = SHT_PROGBITS
    flags: int = SHF_ALLOC
    align: int = 1
    #: Only for SHT_NOBITS (.bss), which occupies no space in the file.
    size_override: int | None = None
    relocations: list[Relocation] = field(default_factory=list)

    @property
    def size(self) -> int:
        return self.size_override if self.size_override is not None \
            else len(self.data)


class ElfObject:
    """A relocatable ELF64 object under construction.

        obj = ElfObject(EM_AARCH64)
        obj.section(".text", code, flags=SHF_ALLOC | SHF_EXECINSTR, align=4)
        obj.symbol(Symbol("main", ".text", 0, len(code), STB_GLOBAL, STT_FUNC))
        obj.relocate(".text", Relocation(0x10, "printf", R_AARCH64_CALL26))
        data = obj.to_bytes()
    """

    def __init__(self, machine: int) -> None:
        self.machine = machine
        self.sections: dict[str, Section] = {}
        self.symbols: list[Symbol] = []

    # ── building ────────────────────────────────────────────────────────────
    def section(self, name: str, data: bytes = b"", *, kind: int = SHT_PROGBITS,
                flags: int = SHF_ALLOC, align: int = 1,
                size_override: int | None = None) -> Section:
        sec = Section(name, data, kind, flags, align, size_override)
        self.sections[name] = sec
        return sec

    def symbol(self, sym: Symbol) -> Symbol:
        self.symbols.append(sym)
        return sym

    def relocate(self, section: str, rel: Relocation) -> None:
        self.sections[section].relocations.append(rel)

    # ── writing ─────────────────────────────────────────────────────────────
    def to_bytes(self) -> bytes:
        shstr = _Strings()
        strtab = _Strings()

        # SYMBOLS, LOCALS FIRST. The order is the file's, not the caller's:
        # `sh_info` must be the index of the first global, so sorting here is
        # what makes that index meaningful rather than a guess.
        ordered = ([s for s in self.symbols if s.is_local]
                   + [s for s in self.symbols if not s.is_local])
        first_global = sum(1 for s in ordered if s.is_local) + 1  # +1: index 0
        sym_index = {s.name: i + 1 for i, s in enumerate(ordered)}

        # The section list, in file order. `.symtab` must come after the
        # content sections so their indices are already fixed when a symbol
        # names one.
        content = list(self.sections.values())
        names = [".symtab", ".strtab", ".shstrtab"]
        rela_for = [s for s in content if s.relocations]
        layout = [None] + content + [None] * len(rela_for) + [None] * 3
        index_of = {sec.name: i + 1 for i, sec in enumerate(content)}

        # Section header string table entries, in the order headers are
        # written, so the names resolve.
        shstr.put("")
        for sec in content:
            shstr.put(sec.name)
        for sec in rela_for:
            shstr.put(".rela" + sec.name)
        for n in names:
            shstr.put(n)

        # ── symbol table bytes ──
        symtab = bytearray(struct.pack(_SYM, 0, 0, 0, 0, 0, 0))  # index 0
        for s in ordered:
            shndx = index_of[s.section] if s.section else SHN_UNDEF
            info = (s.binding << 4) | (s.kind & 0xF)
            symtab += struct.pack(_SYM, strtab.put(s.name), info, 0,
                                  shndx, s.value, s.size)

        # ── relocation section bytes ──
        rela_bytes: dict[str, bytes] = {}
        for sec in rela_for:
            buf = bytearray()
            for r in sec.relocations:
                try:
                    idx = sym_index[r.symbol]
                except KeyError:
                    raise KeyError(
                        f"relocation in {sec.name} at {r.offset:#x} names "
                        f"{r.symbol!r}, which is not a symbol of this object "
                        f"-- an undefined symbol must still be DECLARED, with "
                        f"an empty section") from None
                buf += struct.pack(_RELA, r.offset,
                                   (idx << 32) | (r.kind & 0xFFFFFFFF),
                                   r.addend)
            rela_bytes[sec.name] = bytes(buf)

        # ── lay the file out ──
        blobs: list[tuple[str, bytes, int]] = []      # name, bytes, alignment
        for sec in content:
            blobs.append((sec.name, b"" if sec.kind == SHT_NOBITS else sec.data,
                          sec.align))
        for sec in rela_for:
            blobs.append((".rela" + sec.name, rela_bytes[sec.name], 8))
        blobs.append((".symtab", bytes(symtab), 8))
        blobs.append((".strtab", strtab.bytes(), 1))
        blobs.append((".shstrtab", shstr.bytes(), 1))

        out = bytearray(b"\0" * 64)                   # header, filled in last
        offsets: dict[str, int] = {}
        for name, data, align in blobs:
            if align > 1:
                out += b"\0" * (-len(out) % align)
            offsets[name] = len(out)
            out += data

        out += b"\0" * (-len(out) % 8)
        sh_off = len(out)

        n_content = len(content)
        symtab_index = 1 + n_content + len(rela_for)
        strtab_index = symtab_index + 1
        shstr_index = strtab_index + 1

        headers = [struct.pack(_SHDR, 0, SHT_NULL, 0, 0, 0, 0, 0, 0, 0, 0)]
        for sec in content:
            headers.append(struct.pack(
                _SHDR, shstr.put(sec.name), sec.kind, sec.flags, 0,
                offsets[sec.name], sec.size, 0, 0, sec.align, 0))
        for sec in rela_for:
            nm = ".rela" + sec.name
            headers.append(struct.pack(
                _SHDR, shstr.put(nm), SHT_RELA, 0, 0, offsets[nm],
                len(rela_bytes[sec.name]),
                # sh_link: which symbol table the indices are in.
                # sh_info: which section the offsets are in.
                symtab_index, index_of[sec.name], 8, 24))
        headers.append(struct.pack(
            _SHDR, shstr.put(".symtab"), SHT_SYMTAB, 0, 0, offsets[".symtab"],
            len(symtab), strtab_index, first_global, 8, 24))
        headers.append(struct.pack(
            _SHDR, shstr.put(".strtab"), SHT_STRTAB, 0, 0, offsets[".strtab"],
            len(strtab.bytes()), 0, 0, 1, 0))
        headers.append(struct.pack(
            _SHDR, shstr.put(".shstrtab"), SHT_STRTAB, 0, 0,
            offsets[".shstrtab"], len(shstr.bytes()), 0, 0, 1, 0))

        # The section-name table is written BEFORE these headers are packed,
        # so any name added by `shstr.put` above would be missing from it. It
        # is not: every one was interned earlier, and `put` on an existing
        # string only reads. Asserted rather than trusted, because the failure
        # is a section whose name is whatever bytes follow the table.
        assert shstr.bytes() == out[offsets[".shstrtab"]:
                                    offsets[".shstrtab"] + len(shstr.bytes())]

        out += b"".join(headers)

        ident = bytes([0x7F]) + b"ELF" + bytes([2, 1, 1, 0]) + bytes(8)
        out[:64] = struct.pack(
            _EHDR, ident,
            1,                      # e_type: ET_REL
            self.machine, 1, 0, 0, sh_off, 0,
            64,                     # e_ehsize
            0, 0,                   # no program headers in a relocatable
            64, len(headers), shstr_index)
        return bytes(out)
