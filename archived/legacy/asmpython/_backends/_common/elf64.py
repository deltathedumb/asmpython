"""ELF64 object-file primitives shared by every machine backend.

The container is architecture-independent. An ELF64 header, section header,
symbol, and relocation have the same layout whatever CPU the code targets --
only ``e_machine`` and the relocation type numbers differ, and both are passed
in rather than baked here.

This was duplicated between ``x86_64/elf.py`` and ``arm64/elf.py``: identical
constants, identical ``struct.pack`` format strings, identical logic, differing
only in formatting. The copies had already diverged in a way neither backend
could see, because each one only ever tested itself:

    IRGlobal("big", U64, 2**63)      # a legal u64
        x86_64   OverflowError: int too big to convert
        arm64    0000000000000080

arm64 derived signedness from the type name; x86-64 hard-coded ``signed=True``.
The correct version is here, so both get it. That is the second divergence
found between these two backends' copies of the same logic, after the register
allocator's -- and they pointed in opposite directions, which is what makes
hand-synchronised duplicates worse than either copy alone.

What stays per-backend: ``build_elf``'s section layout policy and the
relocation vocabulary (``R_X86_64_*`` / ``R_AARCH64_*``). What lives here: the
format itself.

RISC-V (``EM_RISCV``) and MIPS (``EM_MIPS``) are pre-declared below so a third
and fourth copy never gets started.
"""

from __future__ import annotations

import struct
from typing import Any

# ── identification ───────────────────────────────────────────────────────────
ELFCLASS64 = 2
ELFDATA2LSB = 1
EV_CURRENT = 1
ET_REL = 1

# e_machine. The only field in the header that varies by architecture.
EM_X86_64 = 62
EM_AARCH64 = 183
EM_RISCV = 243
EM_MIPS = 8

# ── section header types / flags ─────────────────────────────────────────────
SHT_NULL = 0
SHT_PROGBITS = 1
SHT_SYMTAB = 2
SHT_STRTAB = 3
SHT_RELA = 4
SHT_NOBITS = 8

SHF_WRITE = 0x1
SHF_ALLOC = 0x2
SHF_EXECINSTR = 0x4
SHF_TLS = 0x400

# ── symbol binding / type ────────────────────────────────────────────────────
STB_LOCAL = 0
STB_GLOBAL = 1
STB_WEAK = 2

STT_NOTYPE = 0
STT_OBJECT = 1
STT_FUNC = 2
STT_SECTION = 3

# Storage width per IR scalar type, for serializing a global's initial value.
_TYPE_SIZES: dict[str, int] = {
    "i1": 1, "i8": 1, "i16": 2, "i32": 4, "i64": 8,
    "u8": 1, "u16": 2, "u32": 4, "u64": 8,
    "f32": 4, "f64": 8,
    "ptr": 8, "v128": 16,
}


def align(value: int, alignment: int) -> int:
    return (value + alignment - 1) & ~(alignment - 1)


def ehdr(machine: int, shoff: int, shnum: int, shstrndx: int) -> bytes:
    """ELF64 file header for a relocatable object."""
    ident = bytes([0x7F, 0x45, 0x4C, 0x46,          # \x7FELF
                   ELFCLASS64, ELFDATA2LSB, EV_CURRENT, 0]) + bytes(8)
    return struct.pack(
        "<16sHHIQQQIHHHHHH",
        ident, ET_REL, machine, EV_CURRENT,
        0, 0, shoff, 0,
        64, 0, 0, 64, shnum, shstrndx,
    )


def shdr(name: int, typ: int, flags: int, off: int, size: int,
         link: int = 0, info: int = 0, addralign: int = 1,
         entsize: int = 0) -> bytes:
    return struct.pack("<IIQQQQIIQQ",
                       name, typ, flags, 0, off, size, link, info,
                       addralign, entsize)


def sym(name: int, bind: int, typ: int, shndx: int,
        value: int, size: int) -> bytes:
    return struct.pack("<IBBHQQ",
                       name, (bind << 4) | typ, 0, shndx, value, size)


def rela(offset: int, sym_idx: int, rtype: int, addend: int = 0) -> bytes:
    r_info = (sym_idx << 32) | (rtype & 0xFFFFFFFF)
    return struct.pack("<QQq", offset, r_info, addend)


def build_strtab(names: "list[str]") -> "tuple[bytes, dict[str, int]]":
    """A NUL-separated string table plus each name's offset into it."""
    blob = bytearray(b"\x00")
    # The table always opens with a NUL, so the empty name is offset 0 and must
    # not be appended again -- a section or symbol with no name points there.
    offsets: dict[str, int] = {"": 0}
    for name in names:
        if name and name not in offsets:
            offsets[name] = len(blob)
            blob.extend(name.encode("utf-8"))
            blob.append(0)
    return bytes(blob), offsets


def global_bytes(g: Any) -> bytes:
    """Serialize an ``IRGlobal``'s initial value.

    Signedness comes from the type name. Hard-coding ``signed=True`` -- which
    the x86-64 copy did -- raises OverflowError on any ``u64`` above i64's
    maximum, a value the type is perfectly entitled to hold.
    """
    tname = g.type.name
    value = g.value
    size = _TYPE_SIZES.get(tname, 8)

    if isinstance(value, str):
        return value.encode("utf-8") + b"\x00"      # NUL-terminated UTF-8
    if isinstance(value, float):
        return struct.pack("<f" if tname == "f32" else "<d", value)
    if isinstance(value, list):
        return bytes(int(item) & 0xFF for item in value)
    if isinstance(value, int):
        return value.to_bytes(size, "little", signed=not tname.startswith("u"))
    return bytes(size)                               # None / 0 -> zero-filled


__all__ = [
    "ELFCLASS64", "ELFDATA2LSB", "ET_REL", "EV_CURRENT",
    "EM_AARCH64", "EM_MIPS", "EM_RISCV", "EM_X86_64",
    "SHF_ALLOC", "SHF_EXECINSTR", "SHF_TLS", "SHF_WRITE",
    "SHT_NOBITS", "SHT_NULL", "SHT_PROGBITS", "SHT_RELA", "SHT_STRTAB",
    "SHT_SYMTAB",
    "STB_GLOBAL", "STB_LOCAL", "STB_WEAK",
    "STT_FUNC", "STT_NOTYPE", "STT_OBJECT", "STT_SECTION",
    "align", "build_strtab", "ehdr", "global_bytes", "rela", "shdr", "sym",
]
