"""Small read-only helpers for AArch64 ELF relocatable objects.

The executable builder uses these before invoking ``ld`` so unsupported runtime
requirements and runtime-export drift are reported as asmpython compatibility
errors rather than late generic linker diagnostics.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass

from .elf import (
    ELFCLASS64,
    ELFDATA2LSB,
    EM_AARCH64,
    ET_REL,
    SHN_UNDEF,
    SHT_SYMTAB,
    STB_GLOBAL,
)


_ELF_HEADER = struct.Struct("<16sHHIQQQIHHHHHH")
_SECTION_HEADER = struct.Struct("<IIQQQQIIQQ")
_SYMBOL = struct.Struct("<IBBHQQ")


class Arm64ElfFormatError(ValueError):
    pass


@dataclass(frozen=True)
class ElfSymbol:
    name: str
    binding: int
    kind: int
    section_index: int
    value: int
    size: int

    @property
    def is_undefined(self) -> bool:
        return self.section_index == SHN_UNDEF

    @property
    def is_global(self) -> bool:
        return self.binding == STB_GLOBAL


def _checked_slice(blob: bytes, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size < 0 or offset + size > len(blob):
        raise Arm64ElfFormatError(
            f"{label} range {offset}:{offset + size} is outside "
            f"{len(blob)}-byte object"
        )
    return blob[offset : offset + size]


def _cstring(table: bytes, offset: int) -> str:
    if not 0 <= offset < len(table):
        raise Arm64ElfFormatError(
            f"symbol-name offset {offset} is outside "
            f"{len(table)}-byte string table"
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


def symbols(blob: bytes) -> tuple[ElfSymbol, ...]:
    """Return every symbol-table entry from one AArch64 ``ET_REL`` object."""
    sections = section_headers(blob)
    output: list[ElfSymbol] = []
    found_symtab = False

    for section in sections:
        kind = section[1]
        if kind != SHT_SYMTAB:
            continue
        found_symtab = True
        offset = section[4]
        size = section[5]
        linked_index = section[6]
        entry_size = section[9]
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
        symbol_data = _checked_slice(blob, offset, size, "symbol table")
        for item_offset in range(0, size, entry_size):
            (
                name_offset,
                info,
                _other,
                section_index,
                value,
                symbol_size,
            ) = _SYMBOL.unpack_from(symbol_data, item_offset)
            name = "" if name_offset == 0 else _cstring(strings, name_offset)
            output.append(
                ElfSymbol(
                    name=name,
                    binding=info >> 4,
                    kind=info & 0xF,
                    section_index=section_index,
                    value=value,
                    size=symbol_size,
                )
            )

    if not found_symtab:
        raise Arm64ElfFormatError("AArch64 object has no symbol table")
    return tuple(output)


def undefined_symbols(blob: bytes) -> frozenset[str]:
    """Return all named ``SHN_UNDEF`` symbols."""
    return frozenset(
        symbol.name
        for symbol in symbols(blob)
        if symbol.name and symbol.is_undefined
    )


def defined_global_symbols(blob: bytes) -> frozenset[str]:
    """Return all named global symbols defined by the object."""
    return frozenset(
        symbol.name
        for symbol in symbols(blob)
        if symbol.name and symbol.is_global and not symbol.is_undefined
    )
