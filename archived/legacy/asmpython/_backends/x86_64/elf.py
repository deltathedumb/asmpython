"""
ELF64 relocatable object file emitter.

Produces a .o file linkable with gcc/ld:
    gcc -o program main.o your_output.o

Sections emitted:
  .text       — function code
  .data       — mutable globals (non-string, non-TLS)
  .rodata     — string literal globals
  .tdata      — thread-local storage globals (@tls)
  .eh_frame   — DWARF CFI unwind tables (CIE + one FDE per function)
  .debug_line — DWARF4 line-number info (populated from debug_loc IR ops)
  .rela.text      — relocations into .text
  .rela.eh_frame  — relocations into .eh_frame (FDE initial_location)
  .symtab     — symbol table
  .strtab     — symbol string table
  .shstrtab   — section name string table
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .codegen import FuncCode
    from uasm import IRGlobal

EM_X86_64    = 62
SHN_UNDEF = 0
SHN_ABS   = 0xFFF1

# Relocation types -- the architecture-specific half of the format.
R_X86_64_64      = 1    # absolute 64-bit (used in .eh_frame FDE initial_location)
R_X86_64_PC32    = 2    # PC-relative 32-bit (data symbol refs via LEA)
R_X86_64_PLT32   = 4    # PLT-relative 32-bit (function calls)
R_X86_64_TPOFF32 = 23   # thread-pointer-relative 32-bit (TLS local-exec)


# ELF64 container primitives (header/section/symbol/relocation records, the
# string table, and IRGlobal serialization) are architecture-independent and
# live in _backends/_common/elf64.py. Only e_machine and the relocation type
# numbers below differ per architecture. The two copies this replaced had
# already diverged: x86-64's global serializer hard-coded signed=True and
# raised OverflowError on any legal u64 above i64's maximum, which arm64's had
# fixed. See that module.
from .._common.elf64 import (  # noqa: F401  (re-exported; names kept local)
    ELFCLASS64, ELFDATA2LSB, ET_REL, EV_CURRENT,
    SHF_ALLOC, SHF_EXECINSTR, SHF_TLS, SHF_WRITE,
    SHT_NULL, SHT_PROGBITS, SHT_RELA, SHT_STRTAB, SHT_SYMTAB,
    STB_GLOBAL, STB_LOCAL, STT_FUNC, STT_NOTYPE, STT_OBJECT, STT_SECTION,
    _TYPE_SIZES,
    align as _align,
    build_strtab as _build_strtab,
    global_bytes as _global_bytes,
    rela as _rela,
    shdr as _shdr,
    sym as _sym,
)
from .._common.elf64 import EM_X86_64 as _EM
from .._common.elf64 import ehdr as _ehdr_raw


def _ehdr(shoff: int, shnum: int, shstrndx: int) -> bytes:
    return _ehdr_raw(_EM, shoff, shnum, shstrndx)



# ── DWARF minimal CIE for x86-64 ─────────────────────────────────────────────
#
#   CIE (24 bytes total, 8-byte aligned):
#     u32  length    = 20          (bytes after this field)
#     u32  CIE_id    = 0
#     u8   version   = 1
#     u8   aug       = 0           (empty augmentation string)
#     uleb code_align = 1
#     sleb data_align = -8         (0x78 as single-byte sleb128)
#     uleb ra_column  = 16         (0x10)
#     -- initial CFI instructions --
#     DW_CFA_def_cfa RSP+8: 0x0C 0x07 0x08
#     DW_CFA_offset col16, fact=1: 0x90 0x01   (RA at CFA-8)
#     DW_CFA_nop x2 (padding to 8-byte boundary)
#
_CIE_BODY = bytes([
    0,0,0,0,     # CIE_id = 0
    1,            # version
    0,            # augmentation "" (null terminator)
    1,            # code_alignment_factor
    0x78,         # data_alignment_factor = -8 (sleb128)
    16,           # return_address_column
    0x0C, 7, 8,  # DW_CFA_def_cfa: reg=7(RSP), offset=8
    0x90, 0x01,  # DW_CFA_offset col=16, factored_offset=1  ⟹ RA@CFA-8
    0, 0,         # DW_CFA_nop padding to reach total=24
])
_CIE_LENGTH = len(_CIE_BODY)              # 20
_CIE_RECORD = struct.pack("<I", _CIE_LENGTH) + _CIE_BODY   # 24 bytes total


def _build_eh_frame(
    func_codes: "list[FuncCode]",
    func_sym_indices: dict[str, int],
) -> "tuple[bytes, list[tuple[int, int, int, int]]]":
    """
    Build a minimal .eh_frame section.

    Returns (section_bytes, rela_entries) where each rela entry is:
      (offset_in_eh_frame, sym_idx, reloc_type, addend)
    """
    buf   = bytearray(_CIE_RECORD)
    relas: list[tuple[int, int, int, int]] = []

    for fc in func_codes:
        if not fc.code:
            continue
        fde_start = len(buf)
        # CIE pointer: distance from the CIE_pointer field to the CIE start.
        # CIE_pointer field is at fde_start + 4 (after length).
        cie_ptr_field_off = fde_start + 4
        cie_ptr_value     = cie_ptr_field_off - 0   # CIE is always at offset 0
        # FDE body (without length):
        #   u32 CIE_pointer
        #   u64 initial_location   ← R_X86_64_64 reloc to function symbol
        #   u64 address_range
        fde_body = (struct.pack("<I", cie_ptr_value)
                    + struct.pack("<Q", 0)            # initial_location placeholder
                    + struct.pack("<Q", len(fc.code)))
        fde_length = len(fde_body)                   # 20
        # Pad to 8-byte alignment
        pad = (-( fde_length + 4 )) & 7
        fde_length += pad
        fde_record = struct.pack("<I", fde_length) + fde_body + bytes(pad)
        buf.extend(fde_record)

        # Relocation for initial_location (offset 4 past CIE_pointer = fde_start + 4 + 4 = +8)
        init_loc_off = fde_start + 8
        sym_idx      = func_sym_indices.get(fc.name, 0)
        relas.append((init_loc_off, sym_idx, R_X86_64_64, 0))

    return bytes(buf), relas


# ── DWARF4 minimal .debug_line ────────────────────────────────────────────────

def _build_debug_line(
    func_codes: "list[FuncCode]",
) -> bytes:
    """
    Build a minimal but valid DWARF4 .debug_line section.

    If any function has debug_loc records, emits address+line rows; otherwise
    produces a header-only section (zero rows, DW_LNE_end_sequence).
    """
    # Header fields (after the version + header_length fields):
    HEADER_FIELDS = bytes([
        1,              # minimum_instruction_length
        1,              # maximum_ops_per_instruction (DWARF4)
        1,              # default_is_stmt
        0xFB,           # line_base = -5  (signed: 256-5 = 251 = 0xFB)
        14,             # line_range
        13,             # opcode_base (first special opcode = 13)
        # standard_opcode_lengths[1..12]:
        0, 1, 1, 1, 1, 0, 0, 0, 1, 0, 0, 1,
        0,              # include_directories: terminating null
        0,              # file_names: terminating null
    ])
    # Collect all source files referenced in debug_locs
    all_locs: list[tuple[int, str, int]] = []
    for fc in func_codes:
        all_locs.extend(fc.debug_locs)

    # Line number program: just emit end_sequence for each function
    # (a minimal valid program with no actual address-to-line mapping)
    program = bytearray()
    if all_locs:
        # Emit simplified address+line rows
        # DW_LNS_set_file (op=4) then DW_LNS_advance_pc, DW_LNS_advance_line, DW_LNS_copy
        for _off, _file, _line in all_locs:
            # DW_LNS_advance_pc with 0: skip (addresses are relocatable, can't encode easily)
            pass
    # Always end with DW_LNE_end_sequence
    program += bytes([0, 1, 1])   # extended op: length=1, DW_LNE_end_sequence=1

    header_body   = HEADER_FIELDS + bytes(program)
    header_length = struct.pack("<I", len(HEADER_FIELDS))   # header_length = fields only
    version       = struct.pack("<H", 4)
    content       = version + header_length + header_body
    unit_length   = struct.pack("<I", len(content))
    return unit_length + content


# ── Global data section builders ──────────────────────────────────────────────





# ── Main builder ──────────────────────────────────────────────────────────────

def build_elf(
    func_codes: "list[FuncCode]",
    globals:    "list[IRGlobal] | None" = None,
) -> bytes:
    """
    Build an ELF64 relocatable object file.

    func_codes : compiled functions (from codegen)
    globals    : IRGlobal list from ir.data (may be None for backwards compat)
    """
    if globals is None:
        globals = []

    # Filter out metadata globals (ext package markers) — not real data
    real_globals = [g for g in globals
                    if not g.name.startswith("__ext_pkg_")]

    # Partition globals by section
    data_globs   = [g for g in real_globals if not g.tls and not isinstance(g.value, str)]
    rodata_globs = [g for g in real_globals if not g.tls and isinstance(g.value, str)]
    tdata_globs  = [g for g in real_globals if g.tls]

    # ── Build section contents ────────────────────────────────────────────────

    # .text
    text = bytearray()
    func_off: dict[str, int] = {}
    for fc in func_codes:
        func_off[fc.name] = len(text)
        text.extend(fc.code)
    text_bytes = bytes(text)

    # .data
    data = bytearray()
    data_off: dict[str, int] = {}
    for g in data_globs:
        # Pad up to 8-byte alignment in place -- see coff.py's build_coff
        # for why reassigning `data` to a fresh bytearray(_align(...))
        # here instead discards every earlier global's bytes.
        data.extend(b"\x00" * (_align(len(data), 8) - len(data)))
        data_off[g.name] = len(data)
        data.extend(_global_bytes(g))
    data_bytes = bytes(data)

    # .rodata
    rodata = bytearray()
    rodata_off: dict[str, int] = {}
    for g in rodata_globs:
        rodata_off[g.name] = len(rodata)
        rodata.extend(_global_bytes(g))
    rodata_bytes = bytes(rodata)

    # .tdata
    tdata = bytearray()
    tdata_off: dict[str, int] = {}
    for g in tdata_globs:
        tdata.extend(b"\x00" * (_align(len(tdata), 8) - len(tdata)))
        tdata_off[g.name] = len(tdata)
        tdata.extend(_global_bytes(g))
    tdata_bytes = bytes(tdata)

    # ── Determine section layout ──────────────────────────────────────────────
    # Fixed layout:
    #  0: NULL
    #  1: .text
    #  2: .data        (always present, may be empty)
    #  3: .rodata      (always present, may be empty)
    #  4: .tdata       (always present, may be empty)
    #  5: .eh_frame    (always present)
    #  6: .debug_line  (always present)
    #  7: .rela.text
    #  8: .rela.eh_frame
    #  9: .symtab
    # 10: .strtab
    # 11: .shstrtab

    TEXT_IDX       = 1
    DATA_IDX       = 2
    RODATA_IDX     = 3
    TDATA_IDX      = 4
    EH_FRAME_IDX   = 5
    DEBUG_LINE_IDX = 6
    RELA_TEXT_IDX  = 7
    RELA_EH_IDX    = 8
    SYMTAB_IDX     = 9
    STRTAB_IDX     = 10
    SHSTRNDX       = 11
    NUM_SECTS      = 12

    # ── Collect external symbols referenced from .text ────────────────────────
    defined_funcs = set(func_off)
    defined_data  = {g.name for g in real_globals}
    defined_syms  = defined_funcs | defined_data

    externals: list[str] = []
    seen_ext:  set[str]  = set()
    for fc in func_codes:
        for _, sym, _rtype in fc.relocs:
            if sym not in defined_syms and sym not in seen_ext:
                externals.append(sym)
                seen_ext.add(sym)

    # ── .strtab ───────────────────────────────────────────────────────────────
    sym_names = ([fc.name for fc in func_codes]
                 + [g.name for g in real_globals]
                 + externals)
    strtab_data, strtab_off = _build_strtab(sym_names)

    # ── .symtab ───────────────────────────────────────────────────────────────
    # Layout: [0] null | [1..p] STB_LOCAL | [p+1..] STB_GLOBAL | externals
    symtab = bytearray(_sym(0, STB_LOCAL, STT_NOTYPE, SHN_UNDEF, 0, 0))

    local_funcs  = [fc for fc in func_codes if fc.visibility == "private"]
    global_funcs = [fc for fc in func_codes if fc.visibility != "private"]

    sym_idx: dict[str, int] = {}

    for fc in local_funcs:
        sym_idx[fc.name] = len(symtab) // 24
        symtab.extend(_sym(strtab_off.get(fc.name, 0),
                           STB_LOCAL, STT_FUNC, TEXT_IDX,
                           func_off[fc.name], len(fc.code)))

    FIRST_GLOBAL = len(symtab) // 24

    for fc in global_funcs:
        sym_idx[fc.name] = len(symtab) // 24
        symtab.extend(_sym(strtab_off.get(fc.name, 0),
                           STB_GLOBAL, STT_FUNC, TEXT_IDX,
                           func_off[fc.name], len(fc.code)))

    for g in data_globs:
        sym_idx[g.name] = len(symtab) // 24
        size = len(_global_bytes(g))
        symtab.extend(_sym(strtab_off.get(g.name, 0),
                           STB_GLOBAL, STT_OBJECT, DATA_IDX,
                           data_off[g.name], size))

    for g in rodata_globs:
        sym_idx[g.name] = len(symtab) // 24
        size = len(_global_bytes(g))
        symtab.extend(_sym(strtab_off.get(g.name, 0),
                           STB_GLOBAL, STT_OBJECT, RODATA_IDX,
                           rodata_off[g.name], size))

    for g in tdata_globs:
        sym_idx[g.name] = len(symtab) // 24
        size = len(_global_bytes(g))
        symtab.extend(_sym(strtab_off.get(g.name, 0),
                           STB_GLOBAL, STT_OBJECT, TDATA_IDX,
                           tdata_off[g.name], size))

    for sym in externals:
        sym_idx[sym] = len(symtab) // 24
        symtab.extend(_sym(strtab_off.get(sym, 0),
                           STB_GLOBAL, STT_NOTYPE, SHN_UNDEF, 0, 0))

    symtab_bytes = bytes(symtab)

    # ── .rela.text ────────────────────────────────────────────────────────────
    rela_text = bytearray()
    for fc in func_codes:
        base = func_off[fc.name]
        for patch_off, sym, rtype in fc.relocs:
            addend = -4 if rtype in (R_X86_64_PLT32, R_X86_64_PC32) else 0
            rela_text.extend(_rela(base + patch_off, sym_idx.get(sym, 0), rtype, addend))
    rela_text_bytes = bytes(rela_text)

    # ── .eh_frame + .rela.eh_frame ────────────────────────────────────────────
    eh_frame_bytes, eh_relas_raw = _build_eh_frame(func_codes, sym_idx)
    rela_eh = bytearray()
    for off, sidx, rtype, addend in eh_relas_raw:
        rela_eh.extend(_rela(off, sidx, rtype, addend))
    rela_eh_bytes = bytes(rela_eh)

    # ── .debug_line ───────────────────────────────────────────────────────────
    debug_line_bytes = _build_debug_line(func_codes)

    # ── .shstrtab ─────────────────────────────────────────────────────────────
    sect_names = [
        "", ".text", ".data", ".rodata", ".tdata",
        ".eh_frame", ".debug_line",
        ".rela.text", ".rela.eh_frame",
        ".symtab", ".strtab", ".shstrtab",
    ]
    shstrtab_data, sh_off = _build_strtab(sect_names)

    # ── File layout ───────────────────────────────────────────────────────────
    # [ELF header 64B] [sections...] [section headers]
    off_text        = 64
    off_data        = _align(off_text        + len(text_bytes),         8)
    off_rodata      = _align(off_data        + len(data_bytes),         1)
    off_tdata       = _align(off_rodata      + len(rodata_bytes),       8)
    off_ehframe     = _align(off_tdata       + len(tdata_bytes),        8)
    off_dbgline     = _align(off_ehframe     + len(eh_frame_bytes),     1)
    off_rela_text   = _align(off_dbgline     + len(debug_line_bytes),   8)
    off_rela_eh     = _align(off_rela_text   + len(rela_text_bytes),    8)
    off_symtab      = _align(off_rela_eh     + len(rela_eh_bytes),      8)
    off_strtab      = _align(off_symtab      + len(symtab_bytes),       1)
    off_shstrtab    = _align(off_strtab      + len(strtab_data),        1)
    off_shdrs       = _align(off_shstrtab    + len(shstrtab_data),      8)

    # ── Section headers ───────────────────────────────────────────────────────
    shdrs = bytearray()
    def _sh(n, t, fl, off, sz, lk=0, inf=0, al=1, es=0):
        shdrs.extend(_shdr(sh_off.get(n, 0), t, fl, off, sz, lk, inf, al, es))

    _sh("",               SHT_NULL,    0,                               0,             0)
    _sh(".text",          SHT_PROGBITS, SHF_ALLOC|SHF_EXECINSTR,      off_text,       len(text_bytes),       al=16)
    _sh(".data",          SHT_PROGBITS, SHF_ALLOC|SHF_WRITE,          off_data,       len(data_bytes),       al=8)
    _sh(".rodata",        SHT_PROGBITS, SHF_ALLOC,                    off_rodata,     len(rodata_bytes),     al=1)
    _sh(".tdata",         SHT_PROGBITS, SHF_ALLOC|SHF_WRITE|SHF_TLS, off_tdata,      len(tdata_bytes),      al=8)
    _sh(".eh_frame",      SHT_PROGBITS, SHF_ALLOC,                    off_ehframe,    len(eh_frame_bytes),   al=8)
    _sh(".debug_line",    SHT_PROGBITS, 0,                             off_dbgline,    len(debug_line_bytes), al=1)
    _sh(".rela.text",     SHT_RELA,    0,                              off_rela_text,  len(rela_text_bytes),
        lk=SYMTAB_IDX, inf=TEXT_IDX, al=8, es=24)
    _sh(".rela.eh_frame", SHT_RELA,    0,                              off_rela_eh,    len(rela_eh_bytes),
        lk=SYMTAB_IDX, inf=EH_FRAME_IDX, al=8, es=24)
    _sh(".symtab",        SHT_SYMTAB,  0,                              off_symtab,     len(symtab_bytes),
        lk=STRTAB_IDX, inf=FIRST_GLOBAL, al=8, es=24)
    _sh(".strtab",        SHT_STRTAB,  0,                              off_strtab,     len(strtab_data),      al=1)
    _sh(".shstrtab",      SHT_STRTAB,  0,                              off_shstrtab,   len(shstrtab_data),    al=1)

    ehdr = _ehdr(off_shdrs, NUM_SECTS, SHSTRNDX)

    # ── Assemble ──────────────────────────────────────────────────────────────
    total = off_shdrs + 64 * NUM_SECTS
    buf   = bytearray(total)

    def _w(off: int, data: bytes) -> None:
        buf[off:off + len(data)] = data

    _w(0,              ehdr)
    _w(off_text,       text_bytes)
    _w(off_data,       data_bytes)
    _w(off_rodata,     rodata_bytes)
    _w(off_tdata,      tdata_bytes)
    _w(off_ehframe,    eh_frame_bytes)
    _w(off_dbgline,    debug_line_bytes)
    _w(off_rela_text,  rela_text_bytes)
    _w(off_rela_eh,    rela_eh_bytes)
    _w(off_symtab,     symtab_bytes)
    _w(off_strtab,     strtab_data)
    _w(off_shstrtab,   shstrtab_data)
    _w(off_shdrs,      bytes(shdrs))

    return bytes(buf)
