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
SHN_UNDEF = 0

# ELF64 container primitives (header/section/symbol/relocation records, the
# string table, and IRGlobal serialization) are architecture-independent and
# live in _backends/_common/elf64.py. Only e_machine -- and, where a backend
# has them, the relocation type numbers -- differ. This file previously carried
# its own copy of all of it, byte-identical to x86-64's apart from formatting,
# and the two had already diverged in a way neither could see: x86-64's global
# serializer raised OverflowError on any legal u64 above i64's maximum, which
# the version here had fixed. That fix now applies to both.
from .._common.elf64 import (  # noqa: F401  (re-exported; names kept local)
    ELFCLASS64, ELFDATA2LSB, ET_REL, EV_CURRENT,
    SHF_ALLOC, SHF_EXECINSTR, SHF_TLS, SHF_WRITE,
    SHT_NULL, SHT_PROGBITS, SHT_RELA, SHT_STRTAB, SHT_SYMTAB,
    STB_GLOBAL, STB_LOCAL, STT_FUNC, STT_NOTYPE, STT_OBJECT,
    _TYPE_SIZES,
    align as _align,
    build_strtab as _build_string_table,
    global_bytes as _global_bytes,
    rela as _rela,
    shdr as _shdr,
    sym as _sym,
)
from .._common.elf64 import EM_AARCH64
from .._common.elf64 import ehdr as _ehdr_raw


def _ehdr(section_offset: int, section_count: int, shstr_index: int) -> bytes:
    return _ehdr_raw(EM_AARCH64, section_offset, section_count, shstr_index)


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
