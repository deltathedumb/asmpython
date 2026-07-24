"""
COFF i386 relocatable object file emitter.

Produces a .o / .obj file that gcc (MinGW, -m32), clang, and MSVC
link.exe (/MACHINE:X86) can link:
    gcc -m32 -o program main.o your_output.o        (MinGW)
    link.exe /out:program.exe /MACHINE:X86 main.obj out.obj  (MSVC)

Adapted from the x86-64 backend's own coff.py -- unlike the ELF32
adaptation (which needed a genuine structural change, REL vs RELA),
COFF's own relocation-entry shape (offset/symbol/type, 10 bytes) and
symbol-table shape are IDENTICAL in format between i386 and AMD64 COFF
-- the only real differences are the machine-type constant
(IMAGE_FILE_MACHINE_I386 vs _AMD64) and the relocation TYPE VALUE used
for PC-relative references (IMAGE_REL_I386_REL32=0x14, not
IMAGE_REL_AMD64_REL32=4 -- these are genuinely different numeric
values on the two machine types, confirmed directly against Microsoft's
own PE/COFF format specification's "Type Indicators for Intel 386
Processors" table rather than assumed, since an initial web search
surfaced a conflicting, wrong value (0x24) for this exact constant
before the primary-source table settled it: IMAGE_REL_I386_REL32 is
0x0014).

Sections:
  .text  — function code
  .data  — mutable initialized globals
  .rdata — read-only data (string literals)
"""

from __future__ import annotations

import struct
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .codegen import FuncCode
    from uasm import IRGlobal

# ── COFF constants ────────────────────────────────────────────────────────────

IMAGE_FILE_MACHINE_I386 = 0x14C

IMAGE_SCN_CNT_CODE             = 0x00000020
IMAGE_SCN_CNT_INITIALIZED_DATA = 0x00000040
IMAGE_SCN_ALIGN_1BYTES         = 0x00100000
IMAGE_SCN_ALIGN_4BYTES         = 0x00300000
IMAGE_SCN_ALIGN_8BYTES         = 0x00400000
IMAGE_SCN_ALIGN_16BYTES        = 0x00500000
IMAGE_SCN_MEM_EXECUTE          = 0x20000000
IMAGE_SCN_MEM_READ             = 0x40000000
IMAGE_SCN_MEM_WRITE            = 0x80000000

TEXT_CHARS  = (IMAGE_SCN_CNT_CODE | IMAGE_SCN_ALIGN_16BYTES
               | IMAGE_SCN_MEM_EXECUTE | IMAGE_SCN_MEM_READ)
DATA_CHARS  = (IMAGE_SCN_CNT_INITIALIZED_DATA | IMAGE_SCN_ALIGN_4BYTES
               | IMAGE_SCN_MEM_READ | IMAGE_SCN_MEM_WRITE)
RDATA_CHARS = (IMAGE_SCN_CNT_INITIALIZED_DATA | IMAGE_SCN_ALIGN_1BYTES
               | IMAGE_SCN_MEM_READ)

IMAGE_SYM_CLASS_EXTERNAL  = 2
IMAGE_SYM_CLASS_STATIC    = 3
IMAGE_SYM_TYPE_FUNCTION   = 0x20   # IMAGE_SYM_DTYPE_FUNCTION << 4
IMAGE_SYM_TYPE_NULL       = 0x00

# Verified directly against Microsoft's own PE/COFF spec ("Type
# Indicators for Intel 386 Processors") -- NOT the AMD64 REL32 value
# (which is 4), and not the 0x24 an initial (wrong) web search
# surfaced for this exact constant before the primary source settled it.
IMAGE_REL_I386_DIR32   = 0x0006   # target's 32-bit VA (used for data refs, matching AMD64's ADDR32NB role)
IMAGE_REL_I386_DIR32NB = 0x0007   # target's 32-bit RVA
IMAGE_REL_I386_REL32   = 0x0014   # 32-bit PC-relative displacement (calls)

COFF_HDR_SIZE    = 20
SECT_HDR_SIZE    = 40
SYM_ENTRY_SIZE   = 18
RELOC_ENTRY_SIZE = 10


# ── Struct builders ───────────────────────────────────────────────────────────
# Every one of these struct shapes is IDENTICAL between i386 and AMD64
# COFF (confirmed against the same Microsoft spec: COFF's file header,
# section header, symbol table entry, and relocation entry formats are
# all machine-independent -- only the machine-type/relocation-type
# CONSTANTS embedded in the data differ, not the struct layouts
# themselves). Kept byte-for-byte identical to the x86-64 file's own
# builders for exactly this reason.

def _coff_hdr(num_sects: int, sym_off: int, num_syms: int) -> bytes:
    return struct.pack("<HHIIIHH",
        IMAGE_FILE_MACHINE_I386,
        num_sects,
        0,          # TimeDateStamp — 0 for reproducible builds
        sym_off,
        num_syms,
        0,          # SizeOfOptionalHeader
        0,          # Characteristics
    )


def _sect_hdr(name: str, chars: int, raw_size: int, raw_off: int,
              reloc_off: int, num_relocs: int) -> bytes:
    name_b = name.encode()[:8].ljust(8, b"\x00")
    return struct.pack("<8sIIIIIIHHI",
        name_b,
        0,              # VirtualSize
        0,              # VirtualAddress
        raw_size,
        raw_off,
        reloc_off,
        0,              # PointerToLinenumbers
        num_relocs,
        0,              # NumberOfLinenumbers
        chars,
    )


def _reloc_entry(offset: int, sym_idx: int, rtype: int = IMAGE_REL_I386_REL32) -> bytes:
    return struct.pack("<IIH", offset, sym_idx, rtype)


def _sym_entry(name_field: bytes, value: int, section: int,
               sym_type: int, storage: int = IMAGE_SYM_CLASS_EXTERNAL) -> bytes:
    return struct.pack("<8sIhHBB",
        name_field,
        value,
        section,        # INT16: 0=undefined, 1-based for defined
        sym_type,
        storage,
        0,              # NumberOfAuxSymbols
    )


# ── String table ──────────────────────────────────────────────────────────────

def _build_strtab(long_names: "list[str]") -> "tuple[bytes, dict[str, int]]":
    data = bytearray(4)
    offs: dict[str, int] = {}
    for name in long_names:
        if name not in offs:
            offs[name] = len(data)
            data.extend(name.encode("utf-8") + b"\x00")
    data[:4] = struct.pack("<I", len(data))
    return bytes(data), offs


def _name_field(name: str, strtab_offs: "dict[str, int]") -> bytes:
    enc = name.encode("utf-8")
    if len(enc) <= 8:
        return enc.ljust(8, b"\x00")
    return struct.pack("<II", 0, strtab_offs[name])


# ── Global data serialiser ────────────────────────────────────────────────────

_TYPE_SIZES = {"i8": 1, "u8": 1, "i16": 2, "u16": 2,
               "i32": 4, "u32": 4, "i64": 8, "u64": 8,
               "f32": 4, "f64": 8, "ptr": 4}


def _global_bytes(g: "IRGlobal") -> bytes:
    tname = g.type.name
    val   = g.value
    size  = _TYPE_SIZES.get(tname, 4)
    if isinstance(val, str):
        return val.encode("utf-8") + b"\x00"
    if isinstance(val, float):
        return struct.pack("<f" if tname == "f32" else "<d", val)
    if isinstance(val, list):
        return bytes(int(x) & 0xFF for x in val)
    if isinstance(val, int):
        return val.to_bytes(size, "little", signed=True)
    return bytes(size)


def _align(n: int, a: int) -> int:
    return (n + a - 1) & ~(a - 1)


# ── Main builder ──────────────────────────────────────────────────────────────

def build_coff(
    func_codes: "list[FuncCode]",
    globals:    "list[IRGlobal] | None" = None,
) -> bytes:
    """
    Build a COFF i386 relocatable object file.

    func_codes : compiled functions
    globals    : IRGlobal list from ir.data (may be None)
    """
    if globals is None:
        globals = []

    real_globals = [g for g in globals if not g.name.startswith("__ext_pkg_")]
    data_globs   = [g for g in real_globals if not isinstance(g.value, str)]
    rdata_globs  = [g for g in real_globals if isinstance(g.value, str)]

    # ── .text ─────────────────────────────────────────────────────────────────
    text = bytearray()
    func_off: dict[str, int] = {}
    for fc in func_codes:
        pad = (-len(text)) & 15
        text.extend(b"\x90" * pad)
        func_off[fc.name] = len(text)
        text.extend(fc.code)
    text_bytes = bytes(text)

    # ── .data ─────────────────────────────────────────────────────────────────
    data = bytearray()
    data_off: dict[str, int] = {}
    for g in data_globs:
        data.extend(b"\x00" * (_align(len(data), 4) - len(data)))
        data_off[g.name] = len(data)
        data.extend(_global_bytes(g))
    data_bytes = bytes(data)

    # ── .rdata ────────────────────────────────────────────────────────────────
    rdata = bytearray()
    rdata_off: dict[str, int] = {}
    for g in rdata_globs:
        rdata_off[g.name] = len(rdata)
        rdata.extend(_global_bytes(g))
    rdata_bytes = bytes(rdata)

    has_data  = bool(data_bytes)
    has_rdata = bool(rdata_bytes)
    num_sects = 1 + (1 if has_data else 0) + (1 if has_rdata else 0)

    TEXT_SECT  = 1
    DATA_SECT  = 2 if has_data else 0
    RDATA_SECT = (3 if has_data else 2) if has_rdata else 0

    defined_syms = set(func_off) | {g.name for g in real_globals}
    externals: list[str] = []
    seen_ext:  set[str]  = set()
    for fc in func_codes:
        for _, sym, _rtype in fc.relocs:
            if sym not in defined_syms and sym not in seen_ext:
                externals.append(sym)
                seen_ext.add(sym)

    all_names  = ([fc.name for fc in func_codes]
                  + [g.name for g in real_globals]
                  + externals)
    long_names = [n for n in all_names if len(n.encode()) > 8]
    strtab_bytes, strtab_offs = _build_strtab(long_names)

    sym_idx: dict[str, int] = {}
    sym_counter = 0
    for fc in func_codes:
        sym_idx[fc.name] = sym_counter; sym_counter += 1
    for g in data_globs:
        sym_idx[g.name] = sym_counter; sym_counter += 1
    for g in rdata_globs:
        sym_idx[g.name] = sym_counter; sym_counter += 1
    for sym in externals:
        sym_idx[sym] = sym_counter; sym_counter += 1

    # ── Relocations for .text ─────────────────────────────────────────────────
    text_relocs = bytearray()
    for fc in func_codes:
        base = func_off[fc.name]
        for patch_off, sym, _rtype in fc.relocs:
            text_relocs.extend(_reloc_entry(base + patch_off, sym_idx.get(sym, 0),
                                             IMAGE_REL_I386_REL32))
    text_relocs_bytes = bytes(text_relocs)
    num_text_relocs   = len(text_relocs_bytes) // RELOC_ENTRY_SIZE

    # ── Symbol table ──────────────────────────────────────────────────────────
    symtab = bytearray()
    for fc in func_codes:
        nf = _name_field(fc.name, strtab_offs)
        symtab.extend(_sym_entry(nf, func_off[fc.name], TEXT_SECT, IMAGE_SYM_TYPE_FUNCTION))
    for g in data_globs:
        nf = _name_field(g.name, strtab_offs)
        symtab.extend(_sym_entry(nf, data_off[g.name], DATA_SECT, IMAGE_SYM_TYPE_NULL))
    for g in rdata_globs:
        nf = _name_field(g.name, strtab_offs)
        symtab.extend(_sym_entry(nf, rdata_off[g.name], RDATA_SECT, IMAGE_SYM_TYPE_NULL))
    for sym in externals:
        nf = _name_field(sym, strtab_offs)
        symtab.extend(_sym_entry(nf, 0, 0, IMAGE_SYM_TYPE_NULL))
    symtab_bytes = bytes(symtab)
    num_syms     = len(symtab_bytes) // SYM_ENTRY_SIZE

    # ── File layout ───────────────────────────────────────────────────────────
    headers_size = COFF_HDR_SIZE + SECT_HDR_SIZE * num_sects
    off_text     = headers_size
    off_data     = off_text + len(text_bytes)
    off_rdata    = off_data + len(data_bytes)
    off_relocs   = off_rdata + len(rdata_bytes)
    off_symtab   = off_relocs + len(text_relocs_bytes)

    coff_hdr = _coff_hdr(num_sects, off_symtab, num_syms)

    sect_hdrs = bytearray()
    sect_hdrs.extend(_sect_hdr(
        ".text", TEXT_CHARS,
        len(text_bytes), off_text,
        off_relocs if num_text_relocs else 0,
        num_text_relocs,
    ))
    if has_data:
        sect_hdrs.extend(_sect_hdr(
            ".data", DATA_CHARS,
            len(data_bytes), off_data,
            0, 0,
        ))
    if has_rdata:
        sect_hdrs.extend(_sect_hdr(
            ".rdata", RDATA_CHARS,
            len(rdata_bytes), off_rdata,
            0, 0,
        ))

    return (coff_hdr + bytes(sect_hdrs)
            + text_bytes + data_bytes + rdata_bytes
            + text_relocs_bytes + symtab_bytes + strtab_bytes)
