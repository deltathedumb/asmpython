"""Parser for the relocatable ELF64 object files elf.py emits (and that
NASM produces too, for the section/symbol/relocation shapes elf.py
actually uses -- this is not a general ELF reader). Used by elf_linker.py
to merge multiple objects into one executable without an external linker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import struct

SHT_SYMTAB = 2
SHT_RELA = 4

STT_MASK = 0xF
STB_SHIFT = 4


@dataclass
class ElfReloc:
    offset: int     # byte offset within the *target* section's raw data
    symbol: str
    rtype: int
    addend: int
    # set instead of `symbol` (which is "" in this case) when the
    # relocation targets a section symbol -- NASM's convention for any
    # reference to a label that doesn't have its own symtab entry
    # (intra-object LEAs/jumps to a non-global label in another section).
    # 1-based index into ElfObject.sections, matching ElfSymbol.shndx's
    # convention; the real target is "that section's base + addend".
    # Unlike COFF, ELF section symbols have an *empty* name (shndx alone
    # identifies them), so this can't just reuse `symbol`.
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
     e_shstrndx) = struct.unpack_from("<16sHHIQQQIHHHHHH", data, 0)

    raw_shdrs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        name_off, sh_type, flags, _addr, sh_off, sh_size, link, info, _align, entsize = (
            struct.unpack_from("<IIQQQQIIQQ", data, off)
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
        name_off, info, _other, shndx, value, size = struct.unpack_from("<IBBHQQ", data, off)
        name = _cstr(data, strtab_off + name_off) if name_off else ""
        symbols.append(ElfSymbol(name, value, size, shndx, info))

    sections: list[ElfSection] = []
    sect_idx_by_pos: dict[int, int] = {}
    for i, (name_off, sh_type, flags, sh_off, sh_size, link, info, entsize) in enumerate(raw_shdrs):
        name = names[i]
        if sh_type == SHT_RELA or sh_type == SHT_SYMTAB or name in ("", ".strtab", ".shstrtab"):
            continue
        sect_idx_by_pos[i] = len(sections)
        sections.append(ElfSection(name, data[sh_off:sh_off + sh_size] if sh_size else b"", flags))

    # Symbols carry the *raw* ELF section index (shndx); translate it to
    # an index into our filtered `sections` list (1-based, 0 = undefined,
    # matching coff_parse's convention) so the linker doesn't need to
    # know about the sections we dropped (.rela.*/.symtab/.strtab/...).
    for sym in symbols:
        sym.shndx = sect_idx_by_pos.get(sym.shndx, -1) + 1 if sym.shndx in sect_idx_by_pos else 0

    # Second pass: attach RELA entries to the section they patch (`info`
    # holds the target section's original index).
    for name_off, sh_type, flags, sh_off, sh_size, link, info, entsize in raw_shdrs:
        if sh_type != SHT_RELA:
            continue
        target_pos = sect_idx_by_pos.get(info)
        if target_pos is None:
            continue
        target = sections[target_pos]
        n = sh_size // entsize
        for i in range(n):
            off = sh_off + i * entsize
            r_offset, r_info, r_addend = struct.unpack_from("<QQq", data, off)
            sym_idx = r_info >> 32
            rtype = r_info & 0xFFFFFFFF
            sym = symbols[sym_idx]
            is_section_sym = sym.name == "" and sym.shndx > 0
            target.relocs.append(ElfReloc(
                r_offset, sym.name, rtype, r_addend,
                section_idx=sym.shndx if is_section_sym else None,
            ))

    return ElfObject(sections, symbols)
