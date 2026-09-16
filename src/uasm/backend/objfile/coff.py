"""Writing a PE/COFF relocatable object, byte by byte.

THE SAME JOB `elf.py` DOES, for Windows. Its docstring makes the argument for
why a backend needs this at all; what follows is only where COFF differs,
because the differences are the part that bites.

    ELF                              COFF
    ------------------------------   ------------------------------------
    addend in the relocation         addend in the SECTION DATA, under the
                                     field being patched
    sections named by an index       names inline, eight bytes, longer ones
    into a section string table      spilled into the symbol string table
    locals first, `sh_info` says     no ordering rule; storage class says
    where the globals start          which each symbol is
    `.bss` is SHT_NOBITS             `.bss` has a size and no file offset,
                                     spelled as PointerToRawData = 0
    alignment is a field             alignment is four bits of the section's
                                     characteristics, as log2 plus one

THE ADDEND DIFFERENCE IS THE ONE THAT MATTERS, and it is silent. Every
relocation this compiler makes is a PC-relative reference whose ELF addend is
-4, meaning "measure from the end of the field". COFF's `REL32` already
measures from the end of the field, so the same reference is spelled with
NOTHING stored -- and an addend copied across rather than translated would
displace every call by four bytes into the middle of an instruction. The
translation itself lives in the BACKEND, because which relocations are
PC-relative is a fact about the architecture; this file only writes down
whatever addend it is handed.

WHAT THIS FILE DOES NOT DO. It does not know an instruction, and it does not
know which relocation number means what; both are the architecture's. See
`elf.py`, which says the same and for the same reason.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

# Machines, from the PE specification. Only the ones a backend here targets.
IMAGE_FILE_MACHINE_I386 = 0x014C
IMAGE_FILE_MACHINE_AMD64 = 0x8664
IMAGE_FILE_MACHINE_ARM64 = 0xAA64

# Section characteristics: what the section holds and what may be done to it.
IMAGE_SCN_CNT_CODE = 0x00000020
IMAGE_SCN_CNT_INITIALIZED_DATA = 0x00000040
IMAGE_SCN_CNT_UNINITIALIZED_DATA = 0x00000080
IMAGE_SCN_LNK_REMOVE = 0x00000800
IMAGE_SCN_MEM_DISCARDABLE = 0x02000000
IMAGE_SCN_MEM_EXECUTE = 0x20000000
IMAGE_SCN_MEM_READ = 0x40000000
IMAGE_SCN_MEM_WRITE = 0x80000000

# Storage classes. Only two are needed: a name the linker may resolve from
# elsewhere, and one private to this file.
IMAGE_SYM_CLASS_EXTERNAL = 2
IMAGE_SYM_CLASS_STATIC = 3

#: `SectionNumber` for a name this object does not define.
IMAGE_SYM_UNDEFINED = 0

#: `Type` for a symbol that names code. The low byte is the base type and the
#: high byte the derived one, so a function is `0x20` and everything else `0`.
#: Only the linker's map file reads it, but a wrong value is a wrong file.
IMAGE_SYM_DTYPE_FUNCTION = 0x20


@dataclass(slots=True)
class Symbol:
    """One name this object defines, or one it needs from elsewhere.

    DELIBERATELY THE SAME SHAPE as `elf.Symbol`, so a backend's object
    emitter can build one list and hand it to whichever writer the target
    asks for. `binding` and `kind` are spelled in ELF's vocabulary and
    translated here -- the alternative is every backend knowing both.
    """

    name: str
    section: str = ""
    value: int = 0
    size: int = 0
    #: ELF's `STB_LOCAL` (0) or `STB_GLOBAL` (1). Translated to a storage class.
    binding: int = 1
    #: ELF's `STT_FUNC` (2) marks code; anything else does not.
    kind: int = 0


@dataclass(slots=True)
class Relocation:
    """A place in a section that must be patched to reach a symbol.

    `addend` IS NOT WRITTEN INTO THE RELOCATION, because COFF has no field for
    it. It is ADDED INTO THE SECTION DATA at `offset`, which is what COFF means
    by an addend, and it is the value COFF wants -- not the ELF one.

    THE TRANSLATION IS THE BACKEND'S, not this file's. Whether -4 becomes zero
    depends on whether the relocation is PC-relative and on where its field
    ends, both of which are facts about the ARCHITECTURE's relocation number.
    A format writer deciding it would have to know what `IMAGE_REL_AMD64_REL32`
    means, which is the knowledge `elf.py` refuses for the same reason.
    """

    offset: int
    symbol: str
    kind: int
    #: Added into the four bytes at `offset`, as COFF spells an addend.
    addend: int = 0


@dataclass(slots=True)
class Section:
    name: str
    data: bytes = b""
    characteristics: int = IMAGE_SCN_MEM_READ
    align: int = 1
    #: Set for `.bss`, which has a size and no bytes in the file.
    size_override: int | None = None
    relocations: list[Relocation] = field(default_factory=list)

    @property
    def size(self) -> int:
        return self.size_override if self.size_override is not None \
            else len(self.data)

    @property
    def is_bss(self) -> bool:
        return self.size_override is not None


class _Strings:
    """The string table: a four-byte length, then NUL-terminated names.

    THE LENGTH COUNTS ITSELF, which is why this starts at four rather than
    zero and why an offset of zero is impossible -- a symbol whose name fits
    in eight bytes carries it inline instead, and never reaches here.
    """

    def __init__(self) -> None:
        self.data = bytearray()

    def add(self, text: str) -> int:
        encoded = text.encode("utf-8") + b"\0"
        at = 4 + len(self.data)
        self.data += encoded
        return at

    def to_bytes(self) -> bytes:
        return struct.pack("<I", 4 + len(self.data)) + bytes(self.data)


def _align_bits(align: int) -> int:
    """Alignment as COFF spells it: log2 plus one, in bits 20 to 23.

    ONE-BASED, so a value of zero means "unspecified" rather than "byte
    aligned" -- and a writer computing plain log2 would mark every 16-byte
    section as 8-byte aligned, which the linker honours.
    """
    if align < 1:
        align = 1
    exponent = max(0, min(13, (align - 1).bit_length()))
    return (exponent + 1) << 20


class CoffObject:
    """A relocatable PE/COFF object under construction.

        obj = CoffObject(IMAGE_FILE_MACHINE_AMD64)
        obj.section(".text", code, characteristics=CODE, align=16)
        obj.symbol(Symbol("main", ".text", 0, len(code), kind=2))
        obj.relocate(".text", Relocation(0x10, "printf", IMAGE_REL_AMD64_REL32))
        data = obj.to_bytes()
    """

    def __init__(self, machine: int) -> None:
        self.machine = machine
        self.sections: dict[str, Section] = {}
        self.symbols: list[Symbol] = []

    # ── building ────────────────────────────────────────────────────────────
    def section(self, name: str, data: bytes = b"", *,
                characteristics: int = IMAGE_SCN_MEM_READ, align: int = 1,
                size_override: int | None = None) -> Section:
        sec = Section(name, data, characteristics, align, size_override)
        self.sections[name] = sec
        return sec

    def symbol(self, sym: Symbol) -> Symbol:
        self.symbols.append(sym)
        return sym

    def relocate(self, section: str, rel: Relocation) -> None:
        self.sections[section].relocations.append(rel)

    # ── writing ─────────────────────────────────────────────────────────────
    def to_bytes(self) -> bytes:
        strings = _Strings()
        order = list(self.sections.values())
        number_of = {sec.name: index + 1 for index, sec in enumerate(order)}

        # SYMBOL INDICES ARE DECIDED BEFORE ANYTHING IS WRITTEN, because a
        # relocation names one and the relocation records are written before
        # the symbol table is. Nothing may be appended to `self.symbols` after
        # this point or every index shifts.
        index_of = {sym.name: index for index, sym in enumerate(self.symbols)}
        for sec in order:
            for rel in sec.relocations:
                if rel.symbol not in index_of:
                    raise ValueError(
                        f"relocation at {rel.offset:#x} in {sec.name} names "
                        f"{rel.symbol!r}, which is not a symbol in this object")

        # Section data, with every addend folded into the bytes it patches.
        bodies = {sec.name: _apply_addends(sec) for sec in order}

        header_size = 20 + 40 * len(order)
        at = header_size
        raw_at: dict[str, int] = {}
        reloc_at: dict[str, int] = {}
        for sec in order:
            if sec.is_bss:
                # NO FILE OFFSET AT ALL. A `.bss` with a PointerToRawData set
                # would have the loader read whatever bytes follow it.
                raw_at[sec.name] = 0
                continue
            raw_at[sec.name] = at
            at += len(bodies[sec.name])
        for sec in order:
            if not sec.relocations:
                reloc_at[sec.name] = 0
                continue
            reloc_at[sec.name] = at
            at += 10 * len(sec.relocations)
        symbol_table_at = at

        out = bytearray()
        out += struct.pack("<HHIIIHH", self.machine, len(order), 0,
                           symbol_table_at, len(self.symbols), 0, 0)
        for sec in order:
            out += _section_header(sec, raw_at[sec.name], reloc_at[sec.name],
                                   strings)
        for sec in order:
            if not sec.is_bss:
                out += bodies[sec.name]
        for sec in order:
            for rel in sec.relocations:
                out += struct.pack("<IIH", rel.offset,
                                   index_of[rel.symbol], rel.kind)
        for sym in self.symbols:
            out += _symbol_record(sym, number_of, strings)
        out += strings.to_bytes()
        return bytes(out)


def _apply_addends(sec: Section) -> bytes:
    """The section's bytes with each addend added into the field it patches.

    COFF HAS NOWHERE ELSE TO PUT IT: the relocation record is ten bytes of
    offset, symbol and type, and that is all. The value added here is already
    COFF's -- see `Relocation.addend` for why converting an ELF one is the
    backend's job and not this file's.
    """
    if sec.is_bss or not sec.relocations:
        return sec.data
    data = bytearray(sec.data)
    for rel in sec.relocations:
        if not rel.addend:
            continue
        current = struct.unpack_from("<i", data, rel.offset)[0]
        struct.pack_into("<i", data, rel.offset, current + rel.addend)
    return bytes(data)


def _section_header(sec: Section, raw_at: int, reloc_at: int,
                    strings: _Strings) -> bytes:
    """One forty-byte section header.

    A NAME LONGER THAN EIGHT BYTES cannot fit the field, so COFF spells it as
    a slash and the decimal offset into the string table -- as ASCII, inside
    those same eight bytes. `.note.GNU-stack` would need it; `.text` does not.
    """
    raw = sec.name.encode("utf-8")
    if len(raw) <= 8:
        name = raw.ljust(8, b"\0")
    else:
        name = f"/{strings.add(sec.name)}".encode("ascii").ljust(8, b"\0")
    if len(sec.relocations) > 0xFFFF:
        raise ValueError(f"{sec.name} has more relocations than COFF can count")
    # `SizeOfRawData` CARRIES A .bss's SIZE, with `PointerToRawData` zero to
    # say there are no bytes to read. The name says otherwise and `VirtualSize`
    # looks like the field that means this -- it is where ELF's analogue would
    # go -- but `VirtualSize` is zero in an object file, and a .bss described
    # through it has a size every tool reads as nothing.
    return name + struct.pack(
        "<IIIIIIHHI",
        0,                                   # VirtualSize: zero in an object
        0,                                   # VirtualAddress: likewise
        sec.size,                            # SizeOfRawData, .bss included
        raw_at, reloc_at, 0,
        len(sec.relocations), 0,
        sec.characteristics | _align_bits(sec.align))


def _symbol_record(sym: Symbol, number_of: dict[str, int],
                   strings: _Strings) -> bytes:
    """One eighteen-byte symbol record.

    THE NAME IS INLINE OR IT IS AN OFFSET, and which one is decided by length:
    eight bytes or fewer are written into the field, and anything longer is
    four zero bytes followed by the string table offset. Mangled C++ names and
    this compiler's runtime symbols are mostly the second kind.
    """
    raw = sym.name.encode("utf-8")
    if len(raw) <= 8:
        name = raw.ljust(8, b"\0")
    else:
        name = struct.pack("<II", 0, strings.add(sym.name))
    section = number_of.get(sym.section, IMAGE_SYM_UNDEFINED) \
        if sym.section else IMAGE_SYM_UNDEFINED
    kind = IMAGE_SYM_DTYPE_FUNCTION if sym.kind == 2 else 0
    storage = IMAGE_SYM_CLASS_STATIC if sym.binding == 0 \
        else IMAGE_SYM_CLASS_EXTERNAL
    return name + struct.pack("<IhHBB", sym.value, section, kind, storage, 0)
