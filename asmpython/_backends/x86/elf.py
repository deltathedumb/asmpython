"""
ELF32 (i386) relocatable object file emitter.

Produces a .o file linkable with gcc/ld:
    gcc -m32 -o program main.o your_output.o

Adapted from the x86-64 backend's own elf.py, which this is modeled on --
one substantial, real structural difference from that file, not just
narrower field widths:

  - ELF32/i386 uses SHT_REL (implicit addend, baked directly into the
    relocated field's own bytes) for its relocation sections, not
    SHT_RELA (x86-64's explicit, separate addend field). Confirmed
    directly against real GNU `as` output (`as --32` + `readelf -r`/
    `-x .rel.text` on a real external-call reference) before writing
    this, rather than assumed from the RELA-based x86-64 file:
    Elf32_Rel is `<II>` (r_offset, r_info only, 8 bytes total) with
    ELF32_R_INFO(sym, type) = (sym << 8) | type -- an 8-bit type field
    and 24-bit symbol index packed into ONE 32-bit word, unlike
    ELF64_R_INFO's (sym << 32) | type split across a 64-bit field.
    codegen.py's own _call already bakes the correct addend (-4) into
    each external call-rel32 site's pre-relocation bytes for exactly
    this reason (see that file's own comment on the fix).

  Every other ELF32 struct layout used here (Elf32_Ehdr, Elf32_Shdr,
  Elf32_Sym) was verified the same way -- built a real object with
  `as --32`, decoded its raw bytes directly in Python, and compared
  field-by-field against what this file assumes, rather than trusting
  recollection of the format. Two real, non-obvious differences from
  the ELF64 shapes this way (not just width-narrowing every field):
    - Elf32_Shdr has NO padding field between sh_flags and sh_addr --
      it's ten uniform 4-byte fields (<IIIIIIIIII>, 40 bytes total),
      unlike Elf64_Shdr's field before addr being padded to align the
      following 8-byte fields.
    - Elf32_Sym packs st_value/st_size BEFORE st_info/st_other/st_shndx
      (<IIIBBH>, 16 bytes) -- the REVERSE field order from Elf64_Sym's
      <IBBHQQ> (name+info+other+shndx first, then the two 8-byte
      value/size fields last, there ordered last purely so the
      structure's own 8-byte fields end up naturally aligned).

Sections emitted:
  .text       — function code
  .data       — mutable globals (non-string, non-TLS)
  .rodata     — string literal globals
  .tdata      — thread-local storage globals (@tls)
  .rel.text   — relocations into .text (SHT_REL, not SHT_RELA)
  .symtab     — symbol table
  .strtab     — symbol string table
  .shstrtab   — section name string table

No .eh_frame/.debug_line equivalent is emitted here (unlike the x86-64
writer) -- this backend's own codegen.py does not produce DWARF CFI or
debug_loc-driven line tables at all yet (a real, explicitly-scoped
follow-up gap, not an oversight specific to this file); adding those
sections with no real producer behind them would just be dead weight.
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .codegen import FuncCode
    from uasm import IRGlobal

# ── ELF constants ─────────────────────────────────────────────────────────────
# Verified directly against a real `as --32`-assembled object's raw bytes
# (see this module's own docstring) rather than recalled from memory.

ET_REL       = 1
EM_386       = 3
EV_CURRENT   = 1
ELFCLASS32   = 1
ELFDATA2LSB  = 1

SHT_NULL     = 0
SHT_PROGBITS = 1
SHT_SYMTAB   = 2
SHT_STRTAB   = 3
SHT_REL      = 9   # NOT SHT_RELA=4 -- see this module's own docstring

SHF_WRITE     = 0x1
SHF_ALLOC     = 0x2
SHF_EXECINSTR = 0x4
SHF_TLS       = 0x400

STB_LOCAL  = 0
STB_GLOBAL = 1
STT_NOTYPE = 0
STT_OBJECT = 1
STT_FUNC   = 2
STT_SECTION = 3

SHN_UNDEF = 0
SHN_ABS   = 0xFFF1

# Relocation types (also defined in codegen.py; kept in sync there --
# see that file's own cross-check against pyelftools' ENUM_RELOC_TYPE_i386).
R_386_32        = 1
R_386_PC32      = 2
R_386_GOT32     = 3
R_386_PLT32     = 4
R_386_GOTOFF    = 9
R_386_GOTPC     = 10
R_386_TLS_TPOFF = 14


# ── Low-level struct builders ─────────────────────────────────────────────────

def _ehdr(shoff: int, shnum: int, shstrndx: int) -> bytes:
    ident = (bytes([0x7F, 0x45, 0x4C, 0x46,  # \x7FELF
                    ELFCLASS32, ELFDATA2LSB, EV_CURRENT, 0])
             + bytes(8))
    return struct.pack("<16sHHIIIIIHHHHHH",
        ident, ET_REL, EM_386, EV_CURRENT,
        0, 0, shoff, 0,
        52, 0, 0, 40, shnum, shstrndx,
    )


def _shdr(name: int, typ: int, flags: int, off: int, size: int,
          link: int = 0, info: int = 0, align: int = 1,
          entsize: int = 0) -> bytes:
    return struct.pack("<IIIIIIIIII",
        name, typ, flags, 0, off, size, link, info, align, entsize)


def _sym(name: int, bind: int, typ: int, shndx: int,
         value: int, size: int) -> bytes:
    return struct.pack("<IIIBBH",
        name, value, size, (bind << 4) | typ, 0, shndx)


def _rel(offset: int, sym_idx: int, rtype: int) -> bytes:
    # ELF32_R_INFO(sym, type) = (sym << 8) | type -- an 8-bit type field,
    # NOT the 32-bit type field ELF64_R_INFO uses (sym << 32) | type.
    r_info = ((sym_idx & 0xFFFFFF) << 8) | (rtype & 0xFF)
    return struct.pack("<II", offset, r_info)


# ── String table ──────────────────────────────────────────────────────────────

def _build_strtab(names: "list[str]") -> "tuple[bytes, dict[str, int]]":
    buf  = bytearray(b"\x00")
    offs: dict[str, int] = {"": 0}
    for n in names:
        if n and n not in offs:
            offs[n] = len(buf)
            buf.extend(n.encode() + b"\x00")
    return bytes(buf), offs


# ── Alignment ─────────────────────────────────────────────────────────────────

def _align(n: int, a: int) -> int:
    return (n + a - 1) & ~(a - 1)


# ── Global data section builders ──────────────────────────────────────────────

_TYPE_SIZES = {"i8": 1, "u8": 1, "i16": 2, "u16": 2,
               "i32": 4, "u32": 4, "i64": 8, "u64": 8,
               "f32": 4, "f64": 8, "ptr": 4}


def _global_bytes(g: "IRGlobal") -> bytes:
    """Serialize a global's initial value to bytes."""
    tname = g.type.name
    val   = g.value
    size  = _TYPE_SIZES.get(tname, 4)

    if isinstance(val, str):
        return val.encode("utf-8") + b"\x00"
    if isinstance(val, float):
        if tname == "f32":
            return struct.pack("<f", val)
        return struct.pack("<d", val)
    if isinstance(val, list):
        return bytes(int(x) & 0xFF for x in val)
    if isinstance(val, int):
        return val.to_bytes(size, "little", signed=True)
    return bytes(size)


# ── Main builder ──────────────────────────────────────────────────────────────

def build_elf(
    func_codes: "list[FuncCode]",
    globals:    "list[IRGlobal] | None" = None,
) -> bytes:
    """
    Build an ELF32 relocatable object file.

    func_codes : compiled functions (from codegen.py)
    globals    : IRGlobal list from ir.data (may be None)
    """
    if globals is None:
        globals = []

    real_globals = [g for g in globals
                    if not g.name.startswith("__ext_pkg_")]

    data_globs   = [g for g in real_globals if not g.tls and not isinstance(g.value, str)]
    rodata_globs = [g for g in real_globals if not g.tls and isinstance(g.value, str)]
    tdata_globs  = [g for g in real_globals if g.tls]

    # ── Build section contents ────────────────────────────────────────────────

    text = bytearray()
    func_off: dict[str, int] = {}
    for fc in func_codes:
        func_off[fc.name] = len(text)
        text.extend(fc.code)
    text_bytes = bytes(text)

    data = bytearray()
    data_off: dict[str, int] = {}
    for g in data_globs:
        data.extend(b"\x00" * (_align(len(data), 4) - len(data)))
        data_off[g.name] = len(data)
        data.extend(_global_bytes(g))
    data_bytes = bytes(data)

    rodata = bytearray()
    rodata_off: dict[str, int] = {}
    for g in rodata_globs:
        rodata_off[g.name] = len(rodata)
        rodata.extend(_global_bytes(g))
    rodata_bytes = bytes(rodata)

    tdata = bytearray()
    tdata_off: dict[str, int] = {}
    for g in tdata_globs:
        tdata.extend(b"\x00" * (_align(len(tdata), 4) - len(tdata)))
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
    #  5: .rel.text
    #  6: .symtab
    #  7: .strtab
    #  8: .shstrtab

    TEXT_IDX     = 1
    DATA_IDX     = 2
    RODATA_IDX   = 3
    TDATA_IDX    = 4
    REL_TEXT_IDX = 5
    SYMTAB_IDX   = 6
    STRTAB_IDX   = 7
    SHSTRNDX     = 8
    NUM_SECTS    = 9

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

    sym_names = ([fc.name for fc in func_codes]
                 + [g.name for g in real_globals]
                 + externals)
    strtab_data, strtab_off = _build_strtab(sym_names)

    symtab = bytearray(_sym(0, STB_LOCAL, STT_NOTYPE, SHN_UNDEF, 0, 0))

    local_funcs  = [fc for fc in func_codes if fc.visibility == "private"]
    global_funcs = [fc for fc in func_codes if fc.visibility != "private"]

    sym_idx: dict[str, int] = {}

    for fc in local_funcs:
        sym_idx[fc.name] = len(symtab) // 16
        symtab.extend(_sym(strtab_off.get(fc.name, 0),
                           STB_LOCAL, STT_FUNC, TEXT_IDX,
                           func_off[fc.name], len(fc.code)))

    FIRST_GLOBAL = len(symtab) // 16

    for fc in global_funcs:
        sym_idx[fc.name] = len(symtab) // 16
        symtab.extend(_sym(strtab_off.get(fc.name, 0),
                           STB_GLOBAL, STT_FUNC, TEXT_IDX,
                           func_off[fc.name], len(fc.code)))

    for g in data_globs:
        sym_idx[g.name] = len(symtab) // 16
        size = len(_global_bytes(g))
        symtab.extend(_sym(strtab_off.get(g.name, 0),
                           STB_GLOBAL, STT_OBJECT, DATA_IDX,
                           data_off[g.name], size))

    for g in rodata_globs:
        sym_idx[g.name] = len(symtab) // 16
        size = len(_global_bytes(g))
        symtab.extend(_sym(strtab_off.get(g.name, 0),
                           STB_GLOBAL, STT_OBJECT, RODATA_IDX,
                           rodata_off[g.name], size))

    for g in tdata_globs:
        sym_idx[g.name] = len(symtab) // 16
        size = len(_global_bytes(g))
        symtab.extend(_sym(strtab_off.get(g.name, 0),
                           STB_GLOBAL, STT_OBJECT, TDATA_IDX,
                           tdata_off[g.name], size))

    for sym in externals:
        sym_idx[sym] = len(symtab) // 16
        symtab.extend(_sym(strtab_off.get(sym, 0),
                           STB_GLOBAL, STT_NOTYPE, SHN_UNDEF, 0, 0))

    symtab_bytes = bytes(symtab)

    # ── .rel.text ─────────────────────────────────────────────────────────────
    # SHT_REL, not SHT_RELA -- no addend field here at all; codegen.py's
    # own pre-relocation bytes already carry whatever addend each site
    # needs (e.g. -4 for R_386_PC32/PLT32 call-rel32 sites -- see that
    # file's own comment on this).
    rel_text = bytearray()
    for fc in func_codes:
        base = func_off[fc.name]
        for patch_off, sym, rtype in fc.relocs:
            rel_text.extend(_rel(base + patch_off, sym_idx.get(sym, 0), rtype))
    rel_text_bytes = bytes(rel_text)

    # ── .shstrtab ─────────────────────────────────────────────────────────────
    sect_names = [
        "", ".text", ".data", ".rodata", ".tdata",
        ".rel.text", ".symtab", ".strtab", ".shstrtab",
    ]
    shstrtab_data, sh_off = _build_strtab(sect_names)

    # ── File layout ───────────────────────────────────────────────────────────
    # [ELF header 52B] [sections...] [section headers]
    off_text      = 52
    off_data      = _align(off_text      + len(text_bytes),      4)
    off_rodata    = _align(off_data      + len(data_bytes),      1)
    off_tdata     = _align(off_rodata    + len(rodata_bytes),    4)
    off_rel_text  = _align(off_tdata     + len(tdata_bytes),     4)
    off_symtab    = _align(off_rel_text  + len(rel_text_bytes),  4)
    off_strtab    = _align(off_symtab    + len(symtab_bytes),    1)
    off_shstrtab  = _align(off_strtab    + len(strtab_data),     1)
    off_shdrs     = _align(off_shstrtab  + len(shstrtab_data),   4)

    # ── Section headers ───────────────────────────────────────────────────────
    shdrs = bytearray()
    def _sh(n, t, fl, off, sz, lk=0, inf=0, al=1, es=0):
        shdrs.extend(_shdr(sh_off.get(n, 0), t, fl, off, sz, lk, inf, al, es))

    _sh("",            SHT_NULL,     0,                           0,            0)
    _sh(".text",       SHT_PROGBITS, SHF_ALLOC|SHF_EXECINSTR,     off_text,     len(text_bytes),      al=16)
    _sh(".data",       SHT_PROGBITS, SHF_ALLOC|SHF_WRITE,         off_data,     len(data_bytes),       al=4)
    _sh(".rodata",     SHT_PROGBITS, SHF_ALLOC,                   off_rodata,   len(rodata_bytes),     al=1)
    _sh(".tdata",      SHT_PROGBITS, SHF_ALLOC|SHF_WRITE|SHF_TLS, off_tdata,    len(tdata_bytes),       al=4)
    _sh(".rel.text",   SHT_REL,      0,                           off_rel_text, len(rel_text_bytes),
        lk=SYMTAB_IDX, inf=TEXT_IDX, al=4, es=8)
    _sh(".symtab",     SHT_SYMTAB,   0,                           off_symtab,   len(symtab_bytes),
        lk=STRTAB_IDX, inf=FIRST_GLOBAL, al=4, es=16)
    _sh(".strtab",     SHT_STRTAB,   0,                           off_strtab,   len(strtab_data),      al=1)
    _sh(".shstrtab",   SHT_STRTAB,   0,                           off_shstrtab, len(shstrtab_data),    al=1)

    ehdr = _ehdr(off_shdrs, NUM_SECTS, SHSTRNDX)

    # ── Assemble ──────────────────────────────────────────────────────────────
    total = off_shdrs + 40 * NUM_SECTS
    buf   = bytearray(total)

    def _w(off: int, data: bytes) -> None:
        buf[off:off + len(data)] = data

    _w(0,             ehdr)
    _w(off_text,      text_bytes)
    _w(off_data,      data_bytes)
    _w(off_rodata,    rodata_bytes)
    _w(off_tdata,     tdata_bytes)
    _w(off_rel_text,  rel_text_bytes)
    _w(off_symtab,    symtab_bytes)
    _w(off_strtab,    strtab_data)
    _w(off_shstrtab,  shstrtab_data)
    _w(off_shdrs,     bytes(shdrs))

    return bytes(buf)
