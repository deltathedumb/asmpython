"""Parser for the relocatable ELF32 object files elf.py emits (and that
GNU `as --32` produces too, for the section/symbol/relocation shapes
elf.py actually uses -- this is not a general ELF reader). Used by
elf_linker.py to merge multiple objects into one executable without an
external linker.

Adapted from the x86-64 backend's own elf_parse.py -- one real
structural difference, not just narrower struct widths: this backend's
own elf.py emits SHT_REL (implicit addend baked into the relocated
field's own bytes), not SHT_RELA (x86-64's explicit addend field) --
see that file's own docstring for why, verified against real `as --32`
output. This parser therefore reads the addend directly out of the
target section's own data bytes at the relocation's offset, rather
than from a dedicated addend field in the relocation entry itself
(Elf32_Rel has none -- it's `<II>`, offset+info only, 8 bytes, vs
Elf64_Rela's `<QQq>` offset+info+addend, 24 bytes).
"""

from __future__ import annotations

from dataclasses import dataclass, field
import struct

SHT_SYMTAB = 2
SHT_REL    = 9

STT_MASK = 0xF
STB_SHIFT = 4


@dataclass
class ElfReloc:
    offset: int     # byte offset within the *target* section's raw data
    symbol: str
    rtype: int
    addend: int
    # set instead of `symbol` (which is "" in this case) when the
    # relocation targets a section symbol -- see the x86-64 parser's own
    # docstring for the full convention this mirrors exactly.
    section_idx: int | None = None


@dataclass
class ElfSection:
    name: str
    data: bytes
    flags: int
    relocs: list[ElfReloc] = field(default_factory=list)


@dataclass
class ElfSymbol:
    name: str
    value: int
    size: int
    shndx: int        # 0 = undefined (external)
    info: int          # (bind << 4) | type

    @property
    def is_func(self) -> bool:
        return (self.info & STT_MASK) == 2

    @property
    def is_object(self) -> bool:
        return (self.info & STT_MASK) == 1


@dataclass
class ElfObject:
    sections: list[ElfSection]
    symbols: list[ElfSymbol]

    def section(self, name: str) -> "ElfSection | None":
        for s in self.sections:
            if s.name == name:
                return s
        return None


def _cstr(buf: bytes, off: int) -> str:
    end = buf.index(b"\x00", off)
    return buf[off:end].decode("utf-8")


def parse_elf(data: bytes) -> ElfObject:
    (_ident, _e_type, _e_machine, _e_version, _e_entry, _e_phoff, e_shoff,
     _e_flags, _e_ehsize, _e_phentsize, _e_phnum, e_shentsize, e_shnum,
     e_shstrndx) = struct.unpack_from("<16sHHIIIIIHHHHHH", data, 0)

    raw_shdrs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        name_off, sh_type, flags, _addr, sh_off, sh_size, link, info, _align, entsize = (
            struct.unpack_from("<IIIIIIIIII", data, off)
        )
        raw_shdrs.append((name_off, sh_type, flags, sh_off, sh_size, link, info, entsize))

    shstrtab_off = raw_shdrs[e_shstrndx][3]
    names = [_cstr(data, shstrtab_off + n[0]) for n in raw_shdrs]

    # Locate .symtab + its linked .strtab.
    symtab_idx = next(i for i, n in enumerate(names) if n == ".symtab")
    _n, _t, _f, sym_off, sym_size, sym_link, _info, sym_entsize = raw_shdrs[symtab_idx]
    strtab_off = raw_shdrs[sym_link][3]

    symbols: list[ElfSymbol] = []
    num_syms = sym_size // sym_entsize
    for i in range(num_syms):
        off = sym_off + i * sym_entsize
        # Elf32_Sym field order is name/value/size/info/other/shndx --
        # the REVERSE of Elf64_Sym's name/info/other/shndx/value/size
        # (see elf.py's own docstring for why: verified directly against
        # a real `as --32`-assembled object's raw symtab bytes).
        name_off, value, size, info, _other, shndx = struct.unpack_from("<IIIBBH", data, off)
        name = _cstr(data, strtab_off + name_off) if name_off else ""
        symbols.append(ElfSymbol(name, value, size, shndx, info))

    sections: list[ElfSection] = []
    sect_idx_by_pos: dict[int, int] = {}
    for i, (name_off, sh_type, flags, sh_off, sh_size, link, info, entsize) in enumerate(raw_shdrs):
        name = names[i]
        if sh_type == SHT_REL or sh_type == SHT_SYMTAB or name in ("", ".strtab", ".shstrtab"):
            continue
        sect_idx_by_pos[i] = len(sections)
        sections.append(ElfSection(name, data[sh_off:sh_off + sh_size] if sh_size else b"", flags))

    for sym in symbols:
        sym.shndx = sect_idx_by_pos.get(sym.shndx, -1) + 1 if sym.shndx in sect_idx_by_pos else 0

    # Second pass: attach REL entries to the section they patch (`info`
    # holds the target section's original index). Unlike RELA, REL has
    # no addend field of its own -- the addend is whatever bytes are
    # ALREADY in the target section's data at r_offset (codegen.py's own
    # pre-relocation placeholder, e.g. -4 for a PC32/PLT32 call-rel32
    # site -- see that file's own comment on this), read back here as a
    # signed little-endian 32-bit value.
    for name_off, sh_type, flags, sh_off, sh_size, link, info, entsize in raw_shdrs:
        if sh_type != SHT_REL:
            continue
        target_pos = sect_idx_by_pos.get(info)
        if target_pos is None:
            continue
        target = sections[target_pos]
        n = sh_size // entsize
        for i in range(n):
            off = sh_off + i * entsize
            r_offset, r_info = struct.unpack_from("<II", data, off)
            sym_idx = r_info >> 8
            rtype = r_info & 0xFF
            sym = symbols[sym_idx]
            is_section_sym = sym.name == "" and sym.shndx > 0
            addend = struct.unpack_from("<i", target.data, r_offset)[0]
            target.relocs.append(ElfReloc(
                r_offset, sym.name, rtype, addend,
                section_idx=sym.shndx if is_section_sym else None,
            ))

    return ElfObject(sections, symbols)
