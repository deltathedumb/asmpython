"""Small read-only helpers for AArch64 ELF objects emitted by this backend.

The executable builder uses this before invoking ``ld`` so unsupported runtime
requirements are reported as an asmpython compatibility error rather than a
late generic undefined-reference diagnostic.
"""
from __future__ import annotations

import struct

from .elf import ELFCLASS64, ELFDATA2LSB, EM_AARCH64, ET_REL, SHN_UNDEF, SHT_SYMTAB


_ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
_SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")
_SYMBOL = struct.Struct("<IBBHQQ")


class Arm64ElfFormatError(ValueError):
    pass


def _checked_slice(blob: bytes, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size < 0 or offset + size > len(blob):
        raise Arm64ElfFormatError(
            f"{label} range {offset}:{offset + size} is outside {len(blob)}-byte object"
        )
    return blob[offset : offset + size]


def _cstring(table: bytes, offset: int) -> str:
    if not 0 <= offset < len(table):
        raise Arm64ElfFormatError(
            f"symbol-name offset {offset} is outside {len(table)}-byte string table"
        )
    end = table.find(b"\x00", offset)
    if end < 0:
        raise Arm64ElfFormatError("unterminated symbol name in string table")
    try:
        return table[offset:end].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Arm64ElfFormatError("symbol table contains non-UTF-8 name") from exc


def section_headers(blob: bytes) -> list[tuple[int, ...]]:
    if len(blob) < _ELF_HEADER.size:
        raise Arm64ElfFormatError("object is shorter than an ELF64 header")
    header = _ELF_HEADER.unpack_from(blob, 0)
    ident, elf_type, machine = header[0], header[1], header[2]
    if ident[:4] != b"\x7fELF":
        raise Arm64ElfFormatError("object has no ELF magic")
    if ident[4] != ELFCLASS64 or ident[5] != ELFDATA2LSB:
        raise Arm64ElfFormatError("object is not little-endian ELF64")
    if elf_type != ET_REL or machine != EM_AARCH64:
        raise Arm64ElfFormatError(
            f"object is not AArch64 ET_REL (type={elf_type}, machine={machine})"
        )

    section_offset = header[6]
    section_entry_size = header[11]
    section_count = header[12]
    if section_entry_size != _SECTION_HEADER.size:
        raise Arm64ElfFormatError(
            f"unexpected ELF64 section-header size {section_entry_size}"
        )
    table = _checked_slice(
        blob,
        section_offset,
        section_entry_size * section_count,
        "section-header table",
    )
    return [
        _SECTION_HEADER.unpack_from(table, index * section_entry_size)
        for index in range(section_count)
    ]


def undefined_symbols(blob: bytes) -> frozenset[str]:
    """Return all named ``SHN_UNDEF`` symbols in one backend-emitted object."""
    sections = section_headers(blob)
    names: set[str] = set()
    found_symtab = False

    for section in sections:
        kind = section[1]
        if kind != SHT_SYMTAB:
            continue
        found_symtab = True
        offset, size, linked_index, entry_size = section[4], section[5], section[6], section[9]
        if entry_size != _SYMBOL.size or size % entry_size:
            raise Arm64ElfFormatError(
                f"invalid symbol table shape: size={size}, entsize={entry_size}"
            )
        if not 0 <= linked_index < len(sections):
            raise Arm64ElfFormatError(
                f"symbol table links invalid section index {linked_index}"
            )
        string_section = sections[linked_index]
        strings = _checked_slice(
            blob,
            string_section[4],
            string_section[5],
            "symbol string table",
        )
        symbols = _checked_slice(blob, offset, size, "symbol table")
        for item_offset in range(0, size, entry_size):
            name_offset, _info, _other, section_index, _value, _symbol_size = _SYMBOL.unpack_from(
                symbols, item_offset
            )
            if section_index != SHN_UNDEF or name_offset == 0:
                continue
            name = _cstring(strings, name_offset)
            if name:
                names.add(name)

    if not found_symtab:
        raise Arm64ElfFormatError("AArch64 object has no symbol table")
    return frozenset(names)
