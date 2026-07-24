"""Parser for the relocatable COFF i386 object files coff.py emits (and
that NASM/MSVC produce too, for the symbol/section/relocation shapes
coff.py actually uses -- this is not a general COFF reader). Used by
pe_linker.py to merge multiple objects into one executable without an
external linker.

Adapted from the x86-64 backend's own coff_parse.py -- no logic changes
at all: COFF's struct shapes (file header, section header, symbol
table entry, relocation entry) are genuinely machine-independent
between i386 and AMD64 (see coff.py's own docstring, verified against
Microsoft's PE/COFF specification), so this file is functionally
identical to its x86-64 counterpart.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import struct

COFF_HDR_SIZE  = 20
SECT_HDR_SIZE  = 40
SYM_ENTRY_SIZE = 18
RELOC_ENTRY_SIZE = 10

IMAGE_SYM_UNDEFINED = 0


@dataclass
class CoffReloc:
    offset: int       # byte offset within the section's raw data
    symbol: str
    rtype: int


@dataclass
class CoffSection:
    name: str
    data: bytes
    characteristics: int
    relocs: list[CoffReloc] = field(default_factory=list)


@dataclass
class CoffSymbol:
    name: str
    value: int
    section_number: int   # 0 = undefined (external), 1-based otherwise
    sym_type: int
    storage_class: int


@dataclass
class CoffObject:
    sections: list[CoffSection]
    symbols: list[CoffSymbol]

    def section(self, name: str) -> "CoffSection | None":
        for s in self.sections:
            if s.name == name:
                return s
        return None


def _read_name(raw8: bytes, strtab: bytes) -> str:
    if raw8[:4] == b"\x00\x00\x00\x00":
        off = struct.unpack_from("<I", raw8, 4)[0]
        end = strtab.index(b"\x00", off)
        return strtab[off:end].decode("utf-8")
    return raw8.rstrip(b"\x00").decode("utf-8")


def parse_coff(data: bytes) -> CoffObject:
    machine, num_sects, _ts, sym_off, num_syms, opt_hdr_sz, _chars = struct.unpack_from(
        "<HHIIIHH", data, 0
    )

    sect_hdr_off = COFF_HDR_SIZE + opt_hdr_sz
    raw_sections = []
    for i in range(num_sects):
        off = sect_hdr_off + i * SECT_HDR_SIZE
        name8, vsize, vaddr, raw_size, raw_ptr, reloc_ptr, _lp, num_relocs, _nl, chars = (
            struct.unpack_from("<8sIIIIIIHHI", data, off)
        )
        raw_sections.append((name8, raw_size, raw_ptr, reloc_ptr, num_relocs, chars))

    strtab_off = sym_off + num_syms * SYM_ENTRY_SIZE
    strtab_size = struct.unpack_from("<I", data, strtab_off)[0] if num_syms else 0
    strtab = data[strtab_off:strtab_off + strtab_size] if strtab_size else b"\x00\x00\x00\x00"

    symbols: list[CoffSymbol] = []
    i = 0
    while i < num_syms:
        off = sym_off + i * SYM_ENTRY_SIZE
        name8, value, section_number, sym_type, storage, naux = struct.unpack_from(
            "<8sIhHBB", data, off
        )
        symbols.append(CoffSymbol(_read_name(name8, strtab), value, section_number, sym_type, storage))
        i += 1 + naux
        for _ in range(naux):
            symbols.append(CoffSymbol("", 0, 0, 0, 0))

    sections: list[CoffSection] = []
    for name8, raw_size, raw_ptr, reloc_ptr, num_relocs, chars in raw_sections:
        name = name8.rstrip(b"\x00").decode("ascii")
        sect_data = data[raw_ptr:raw_ptr + raw_size] if raw_size else b""
        relocs = []
        for r in range(num_relocs):
            roff = reloc_ptr + r * RELOC_ENTRY_SIZE
            patch_off, sym_idx, rtype = struct.unpack_from("<IIH", data, roff)
            relocs.append(CoffReloc(patch_off, symbols[sym_idx].name, rtype))
        sections.append(CoffSection(name, sect_data, chars, relocs))

    return CoffObject(sections, symbols)
