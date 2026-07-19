"""Minimal ELF64 relocatable-object writer for the AArch64 backend.

This is the first linkable Stage-1 object format checkpoint. It intentionally
emits only the sections needed to validate real code generation and the three
relocation kinds currently produced by :mod:`.codegen`:

* ``R_AARCH64_CALL26`` for direct ``BL`` calls;
* ``R_AARCH64_ADR_PREL_PG_HI21`` for ``ADRP`` symbol-page materialization;
* ``R_AARCH64_ADD_ABS_LO12_NC`` for the paired low-12 ``ADD``.

The richer x86-64 writer additionally emits DWARF line/unwind information.
Those sections are deliberately deferred here until the core AArch64 object,
relocation, link, and execution path is independently verified.
"""
from __future__ import annotations

import struct
from typing import TYPE_CHECKING

from .codegen import (
    R_AARCH64_ADD_ABS_LO12_NC,
    R_AARCH64_ADR_PREL_PG_HI21,
    R_AARCH64_CALL26,
)

if TYPE_CHECKING:
    from .codegen import FuncCode
    from asmpython._compiler.ir import IRGlobal


ET_REL = 1
EM_AARCH64 = 183
EV_CURRENT = 1
ELFCLASS64 = 2
ELFDATA2LSB = 1

SHT_NULL = 0
SHT_PROGBITS = 1
SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_RELA = 4

SHF_WRITE = 0x1
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4
SHF_TLS = 0x400

STB_LOCAL = 0
STB_GLOBAL = 1
STT_NOTYPE = 0
STT_OBJECT = 1
STT_FUNC = 2

SHN_UNDEF = 0


_TYPE_SIZES = {
    "i8": 1,
    "u8": 1,
    "i16": 2,
    "u16": 2,
    "i32": 4,
    "u32": 4,
    "i64": 8,
    "u64": 8,
    "f32": 4,
    "f64": 8,
    "ptr": 8,
}


def _align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def _ehdr(section_offset: int, section_count: int, shstr_index: int) -> bytes:
    ident = bytes(
        [
            0x7F,
            0x45,
            0x4C,
            0x46,
            ELFCLASS64,
            ELFDATA2LSB,
            EV_CURRENT,
            0,
        ]
    ) + bytes(8)
    return struct.pack(
        "<16sHHIQQQIHHHHHH",
        ident,
        ET_REL,
        EM_AARCH64,
        EV_CURRENT,
        0,
        0,
        section_offset,
        0,
        64,
        0,
        0,
        64,
        section_count,
        shstr_index,
    )


def _shdr(
    name: int,
    kind: int,
    flags: int,
    offset: int,
    size: int,
    link: int = 0,
    info: int = 0,
    alignment: int = 1,
    entry_size: int = 0,
) -> bytes:
    return struct.pack(
        "<IIQQQQIIQQ",
        name,
        kind,
        flags,
        0,
        offset,
        size,
        link,
        info,
        alignment,
        entry_size,
    )


def _sym(
    name: int,
    binding: int,
    kind: int,
    section_index: int,
    value: int,
    size: int,
) -> bytes:
    return struct.pack(
        "<IBBHQQ",
        name,
        (binding << 4) | kind,
        0,
        section_index,
        value,
        size,
    )


def _rela(offset: int, symbol_index: int, relocation_type: int, addend: int = 0) -> bytes:
    info = (symbol_index << 32) | (relocation_type & 0xFFFFFFFF)
    return struct.pack("<QQq", offset, info, addend)


def _build_string_table(names: list[str]) -> tuple[bytes, dict[str, int]]:
    data = bytearray(b"\x00")
    offsets = {"": 0}
    for name in names:
        if name and name not in offsets:
            offsets[name] = len(data)
            data.extend(name.encode("utf-8") + b"\x00")
    return bytes(data), offsets


def _global_bytes(global_: "IRGlobal") -> bytes:
    type_name = global_.type.name
    value = global_.value
    size = _TYPE_SIZES.get(type_name, 8)

    if isinstance(value, str):
        return value.encode("utf-8") + b"\x00"
    if isinstance(value, float):
        return struct.pack("<f" if type_name == "f32" else "<d", value)
    if isinstance(value, list):
        return bytes(int(item) & 0xFF for item in value)
    if isinstance(value, int):
        signed = not type_name.startswith("u")
        return value.to_bytes(size, "little", signed=signed)
    return bytes(size)


def _visibility_value(func_code: "FuncCode") -> str | None:
    visibility = func_code.visibility
    return getattr(visibility, "value", visibility)


def build_elf(
    func_codes: list["FuncCode"],
    globals: list["IRGlobal"] | None = None,
) -> bytes:
    """Build one little-endian ELF64 ``ET_REL`` object for AArch64."""
    if globals is None:
        globals = []

    real_globals = [
        global_ for global_ in globals if not global_.name.startswith("__ext_pkg_")
    ]
    data_globals = [
        global_
        for global_ in real_globals
        if not global_.tls and not isinstance(global_.value, str)
    ]
    rodata_globals = [
        global_
        for global_ in real_globals
        if not global_.tls and isinstance(global_.value, str)
    ]
    tdata_globals = [global_ for global_ in real_globals if global_.tls]

    text = bytearray()
    function_offsets: dict[str, int] = {}
    for func_code in func_codes:
        text.extend(b"\x00" * (_align(len(text), 4) - len(text)))
        function_offsets[func_code.name] = len(text)
        text.extend(func_code.code)
    text_bytes = bytes(text)

    def build_data_section(
        section_globals: list["IRGlobal"], alignment: int
    ) -> tuple[bytes, dict[str, int]]:
        data = bytearray()
        offsets: dict[str, int] = {}
        for global_ in section_globals:
            data.extend(b"\x00" * (_align(len(data), alignment) - len(data)))
            offsets[global_.name] = len(data)
            data.extend(_global_bytes(global_))
        return bytes(data), offsets

    data_bytes, data_offsets = build_data_section(data_globals, 8)
    rodata_bytes, rodata_offsets = build_data_section(rodata_globals, 1)
    tdata_bytes, tdata_offsets = build_data_section(tdata_globals, 8)

    # Fixed section indexes keep relocation/symbol construction straightforward.
    TEXT_INDEX = 1
    DATA_INDEX = 2
    RODATA_INDEX = 3
    TDATA_INDEX = 4
    RELA_TEXT_INDEX = 5
    SYMTAB_INDEX = 6
    STRTAB_INDEX = 7
    SHSTRTAB_INDEX = 8
    SECTION_COUNT = 9

    defined_symbols = set(function_offsets) | {global_.name for global_ in real_globals}
    external_symbols: list[str] = []
    seen_external: set[str] = set()
    for func_code in func_codes:
        for _offset, symbol, _kind in func_code.relocs:
            if symbol not in defined_symbols and symbol not in seen_external:
                external_symbols.append(symbol)
                seen_external.add(symbol)

    symbol_names = (
        [func_code.name for func_code in func_codes]
        + [global_.name for global_ in real_globals]
        + external_symbols
    )
    string_table, string_offsets = _build_string_table(symbol_names)

    symbol_table = bytearray(_sym(0, STB_LOCAL, STT_NOTYPE, SHN_UNDEF, 0, 0))
    symbol_indexes: dict[str, int] = {}

    local_functions = [
        func_code for func_code in func_codes if _visibility_value(func_code) == "private"
    ]
    global_functions = [
        func_code for func_code in func_codes if _visibility_value(func_code) != "private"
    ]

    for func_code in local_functions:
        symbol_indexes[func_code.name] = len(symbol_table) // 24
        symbol_table.extend(
            _sym(
                string_offsets[func_code.name],
                STB_LOCAL,
                STT_FUNC,
                TEXT_INDEX,
                function_offsets[func_code.name],
                len(func_code.code),
            )
        )

    first_global = len(symbol_table) // 24

    for func_code in global_functions:
        symbol_indexes[func_code.name] = len(symbol_table) // 24
        symbol_table.extend(
            _sym(
                string_offsets[func_code.name],
                STB_GLOBAL,
                STT_FUNC,
                TEXT_INDEX,
                function_offsets[func_code.name],
                len(func_code.code),
            )
        )

    for section_globals, section_index, offsets in (
        (data_globals, DATA_INDEX, data_offsets),
        (rodata_globals, RODATA_INDEX, rodata_offsets),
        (tdata_globals, TDATA_INDEX, tdata_offsets),
    ):
        for global_ in section_globals:
            symbol_indexes[global_.name] = len(symbol_table) // 24
            payload = _global_bytes(global_)
            symbol_table.extend(
                _sym(
                    string_offsets[global_.name],
                    STB_GLOBAL,
                    STT_OBJECT,
                    section_index,
                    offsets[global_.name],
                    len(payload),
                )
            )

    for symbol in external_symbols:
        symbol_indexes[symbol] = len(symbol_table) // 24
        symbol_table.extend(
            _sym(
                string_offsets[symbol],
                STB_GLOBAL,
                STT_NOTYPE,
                SHN_UNDEF,
                0,
                0,
            )
        )

    symbol_table_bytes = bytes(symbol_table)

    relocation_bytes = bytearray()
    supported_relocations = {
        R_AARCH64_CALL26,
        R_AARCH64_ADR_PREL_PG_HI21,
        R_AARCH64_ADD_ABS_LO12_NC,
    }
    for func_code in func_codes:
        function_base = function_offsets[func_code.name]
        for patch_offset, symbol, relocation_type in func_code.relocs:
            if relocation_type not in supported_relocations:
                raise ValueError(
                    f"unsupported AArch64 text relocation {relocation_type} for {symbol}"
                )
            relocation_bytes.extend(
                _rela(
                    function_base + patch_offset,
                    symbol_indexes[symbol],
                    relocation_type,
                    0,
                )
            )
    relocation_table = bytes(relocation_bytes)

    section_names = [
        "",
        ".text",
        ".data",
        ".rodata",
        ".tdata",
        ".rela.text",
        ".symtab",
        ".strtab",
        ".shstrtab",
    ]
    section_name_table, section_name_offsets = _build_string_table(section_names)

    text_offset = 64
    data_offset = _align(text_offset + len(text_bytes), 8)
    rodata_offset = _align(data_offset + len(data_bytes), 1)
    tdata_offset = _align(rodata_offset + len(rodata_bytes), 8)
    rela_text_offset = _align(tdata_offset + len(tdata_bytes), 8)
    symtab_offset = _align(rela_text_offset + len(relocation_table), 8)
    strtab_offset = _align(symtab_offset + len(symbol_table_bytes), 1)
    shstrtab_offset = _align(strtab_offset + len(string_table), 1)
    section_headers_offset = _align(
        shstrtab_offset + len(section_name_table), 8
    )

    section_headers = bytearray()

    def add_section(
        name: str,
        kind: int,
        flags: int,
        offset: int,
        size: int,
        *,
        link: int = 0,
        info: int = 0,
        alignment: int = 1,
        entry_size: int = 0,
    ) -> None:
        section_headers.extend(
            _shdr(
                section_name_offsets[name],
                kind,
                flags,
                offset,
                size,
                link,
                info,
                alignment,
                entry_size,
            )
        )

    add_section("", SHT_NULL, 0, 0, 0)
    add_section(
        ".text",
        SHT_PROGBITS,
        SHF_ALLOC | SHF_EXECINSTR,
        text_offset,
        len(text_bytes),
        alignment=4,
    )
    add_section(
        ".data",
        SHT_PROGBITS,
        SHF_ALLOC | SHF_WRITE,
        data_offset,
        len(data_bytes),
        alignment=8,
    )
    add_section(
        ".rodata",
        SHT_PROGBITS,
        SHF_ALLOC,
        rodata_offset,
        len(rodata_bytes),
        alignment=1,
    )
    add_section(
        ".tdata",
        SHT_PROGBITS,
        SHF_ALLOC | SHF_WRITE | SHF_TLS,
        tdata_offset,
        len(tdata_bytes),
        alignment=8,
    )
    add_section(
        ".rela.text",
        SHT_RELA,
        0,
        rela_text_offset,
        len(relocation_table),
        link=SYMTAB_INDEX,
        info=TEXT_INDEX,
        alignment=8,
        entry_size=24,
    )
    add_section(
        ".symtab",
        SHT_SYMTAB,
        0,
        symtab_offset,
        len(symbol_table_bytes),
        link=STRTAB_INDEX,
        info=first_global,
        alignment=8,
        entry_size=24,
    )
    add_section(
        ".strtab",
        SHT_STRTAB,
        0,
        strtab_offset,
        len(string_table),
        alignment=1,
    )
    add_section(
        ".shstrtab",
        SHT_STRTAB,
        0,
        shstrtab_offset,
        len(section_name_table),
        alignment=1,
    )

    header = _ehdr(section_headers_offset, SECTION_COUNT, SHSTRTAB_INDEX)
    total_size = section_headers_offset + SECTION_COUNT * 64
    output = bytearray(total_size)

    def write(offset: int, payload: bytes) -> None:
        output[offset : offset + len(payload)] = payload

    write(0, header)
    write(text_offset, text_bytes)
    write(data_offset, data_bytes)
    write(rodata_offset, rodata_bytes)
    write(tdata_offset, tdata_bytes)
    write(rela_text_offset, relocation_table)
    write(symtab_offset, symbol_table_bytes)
    write(strtab_offset, string_table)
    write(shstrtab_offset, section_name_table)
    write(section_headers_offset, bytes(section_headers))
    return bytes(output)
