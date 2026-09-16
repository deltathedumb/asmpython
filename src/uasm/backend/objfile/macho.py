"""Writing a Mach-O relocatable object, byte by byte.

THE THIRD FORMAT, and the one that is least like the other two. `elf.py`
explains why a backend needs to write objects at all; what follows is where
Mach-O parts company, because those are the places a writer goes wrong.

  * SECTIONS LIVE IN SEGMENTS, and both are named. `.text` is `__text` in
    `__TEXT`; a section named `.text` is not a section a Mach-O linker will
    treat as code. The names are sixteen bytes each, NUL-padded and not
    NUL-terminated -- a sixteen-character name fills the field exactly.

  * ONE SEGMENT IN AN OBJECT FILE, holding every section, with `vmaddr` zero.
    Segments only become separate at link time.

  * THE SYMBOL TABLE IS SORTED, and `LC_DYSYMTAB` records where each run
    begins: locals, then defined externals, then undefined. This is not a
    convention -- the linker reads the counts and trusts them, so a symbol in
    the wrong run is looked up as the wrong kind.

  * THE ADDEND GOES IN THE SECTION DATA, as in COFF and unlike ELF, and the
    relocation carries `pcrel` and a length rather than the field width being
    implied by the type.

  * A NAME NEEDS ITS LEADING UNDERSCORE. That is the platform's C ABI rather
    than this file's business -- the backend's dialect applies it -- but it is
    the failure that looks like a linker bug, so it is written down here too.

WHAT THIS FILE DOES NOT DO. It does not know an instruction, and it does not
know what a relocation type means; both are the architecture's. See `elf.py`,
which says the same and for the same reason.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

MH_MAGIC_64 = 0xFEEDFACF
MH_OBJECT = 1
#: Says the sections may be divided at symbol boundaries, so the linker can
#: drop unreached pieces. TRUE ONLY IF EVERY PIECE HAS A SYMBOL: this compiler
#: gives one to every function and every global, and a backend that emitted
#: unnamed code or data between them would have to stop setting this.
MH_SUBSECTIONS_VIA_SYMBOLS = 0x2000

CPU_ARCH_ABI64 = 0x01000000
CPU_TYPE_X86_64 = CPU_ARCH_ABI64 | 7
CPU_TYPE_ARM64 = CPU_ARCH_ABI64 | 12
CPU_SUBTYPE_X86_64_ALL = 3
CPU_SUBTYPE_ARM64_ALL = 0

LC_SEGMENT_64 = 0x19
LC_SYMTAB = 0x02
LC_DYSYMTAB = 0x0B

# Section types and attributes.
S_REGULAR = 0x0
S_ZEROFILL = 0x1
S_CSTRING_LITERALS = 0x2
S_ATTR_PURE_INSTRUCTIONS = 0x80000000
S_ATTR_SOME_INSTRUCTIONS = 0x00000400

# `n_type` bits of an nlist entry.
N_STAB, N_PEXT, N_TYPE, N_EXT = 0xE0, 0x10, 0x0E, 0x01
N_UNDF, N_ABS, N_SECT, N_INDR = 0x0, 0x2, 0xE, 0xA

VM_PROT_READ, VM_PROT_WRITE, VM_PROT_EXECUTE = 1, 2, 4


@dataclass(slots=True)
class Symbol:
    """One name this object defines, or one it needs from elsewhere.

    THE SAME SHAPE as `elf.Symbol` and `coff.Symbol`, so a backend can build
    one list and hand it to whichever writer its target asks for. `binding`
    and `kind` are spelled in ELF's vocabulary and translated here.
    """

    name: str
    #: The section it lives in, by this file's own naming -- `.text`, not
    #: `__text`. Empty means UNDEFINED. See `SEGMENT_OF` for the mapping.
    section: str = ""
    value: int = 0
    size: int = 0
    #: ELF's `STB_LOCAL` (0) or `STB_GLOBAL` (1).
    binding: int = 1
    #: ELF's `STT_FUNC` (2) marks code. Mach-O has no field for it, so this is
    #: accepted and ignored -- kept so the three writers take one Symbol.
    kind: int = 0


@dataclass(slots=True)
class Relocation:
    """A place in a section that must be patched to reach a symbol.

    `pcrel` AND `length` ARE PART OF THE RECORD, unlike ELF where the type
    implies both. They are the architecture's to supply, which is why they are
    arguments rather than derived from `kind` here.
    """

    offset: int
    symbol: str
    #: The architecture's `r_type`. Not interpreted here.
    kind: int
    #: Added into the field at `offset`, as Mach-O spells an addend. Already
    #: Mach-O's value: converting an ELF one is the backend's job.
    addend: int = 0
    pcrel: bool = False
    #: log2 of the field width in bytes: 2 is four bytes, 3 is eight.
    length: int = 2


@dataclass(slots=True)
class Section:
    name: str
    data: bytes = b""
    flags: int = S_REGULAR
    align: int = 1
    size_override: int | None = None
    relocations: list[Relocation] = field(default_factory=list)

    @property
    def size(self) -> int:
        return self.size_override if self.size_override is not None \
            else len(self.data)

    @property
    def is_zerofill(self) -> bool:
        return self.flags & 0xFF == S_ZEROFILL


#: Which segment each section belongs to, and what Mach-O calls it. A section
#: this table does not name cannot be written: guessing a segment would put
#: code somewhere the linker does not look for code.
SEGMENT_OF: dict[str, tuple[str, str]] = {
    ".text": ("__TEXT", "__text"),
    ".rodata": ("__TEXT", "__const"),
    ".cstring": ("__TEXT", "__cstring"),
    ".data": ("__DATA", "__data"),
    ".bss": ("__DATA", "__bss"),
}


def _fixed(text: str, width: int = 16) -> bytes:
    """A name in a fixed field: NUL-padded, and NOT NUL-terminated.

    A NAME THAT FILLS THE FIELD HAS NO TERMINATOR, which is the trap: a writer
    reserving a byte for one truncates every sixteen-character section name by
    a character, and the linker then places it in a section nobody named.
    """
    raw = text.encode("utf-8")
    if len(raw) > width:
        raise ValueError(f"{text!r} does not fit a {width}-byte Mach-O name")
    return raw.ljust(width, b"\0")


def _align_log2(align: int) -> int:
    if align < 1:
        return 0
    return max(0, (align - 1).bit_length())


class MachoObject:
    """A relocatable Mach-O 64 object under construction.

        obj = MachoObject(CPU_TYPE_ARM64, CPU_SUBTYPE_ARM64_ALL)
        obj.section(".text", code, flags=CODE_FLAGS, align=4)
        obj.symbol(Symbol("_main", ".text", 0, len(code)))
        obj.relocate(".text", Relocation(0, "_printf", ARM64_RELOC_BRANCH26))
        data = obj.to_bytes()
    """

    def __init__(self, cpu_type: int, cpu_subtype: int) -> None:
        self.cpu_type = cpu_type
        self.cpu_subtype = cpu_subtype
        self.sections: dict[str, Section] = {}
        self.symbols: list[Symbol] = []

    # ── building ────────────────────────────────────────────────────────────
    def section(self, name: str, data: bytes = b"", *, flags: int = S_REGULAR,
                align: int = 1, size_override: int | None = None) -> Section:
        if name not in SEGMENT_OF:
            raise ValueError(
                f"no Mach-O segment is known for section {name!r}; add it to "
                f"SEGMENT_OF rather than letting the linker guess")
        sec = Section(name, data, flags, align, size_override)
        self.sections[name] = sec
        return sec

    def symbol(self, sym: Symbol) -> Symbol:
        self.symbols.append(sym)
        return sym

    def relocate(self, section: str, rel: Relocation) -> None:
        self.sections[section].relocations.append(rel)

    # ── writing ─────────────────────────────────────────────────────────────
    def to_bytes(self) -> bytes:
        order = list(self.sections.values())
        # ZEROFILL SECTIONS COME LAST IN THEIR SEGMENT, which Mach-O requires
        # rather than prefers: a `__bss` before a section with contents leaves
        # the segment's file range describing bytes that are not there, and
        # the addresses after it wrong. Insertion order happens to satisfy
        # this today, which is the reason to check rather than to rely on it.
        seen_zerofill = False
        for sec in order:
            if sec.is_zerofill:
                seen_zerofill = True
            elif seen_zerofill:
                raise ValueError(
                    f"{sec.name} has contents and follows a zerofill section; "
                    f"Mach-O requires the zerofill sections last")
        number_of = {sec.name: index + 1 for index, sec in enumerate(order)}

        # THE THREE RUNS, in the order `LC_DYSYMTAB` describes them. Sorting
        # is not tidiness: the linker reads the counts and trusts them, so a
        # symbol in the wrong run is resolved as the wrong kind of symbol.
        locals_ = [s for s in self.symbols if s.binding == 0 and s.section]
        defined = [s for s in self.symbols if s.binding != 0 and s.section]
        undefined = [s for s in self.symbols if not s.section]
        ordered = locals_ + defined + undefined
        index_of = {s.name: i for i, s in enumerate(ordered)}
        for sec in order:
            for rel in sec.relocations:
                if rel.symbol not in index_of:
                    raise ValueError(
                        f"relocation at {rel.offset:#x} in {sec.name} names "
                        f"{rel.symbol!r}, which is not a symbol in this object")

        bodies = {sec.name: _apply_addends(sec) for sec in order}

        # ADDRESSES FIRST, because a section header carries one and they are
        # assigned by laying the sections out end to end in one segment.
        addresses: dict[str, int] = {}
        cursor = 0
        for sec in order:
            cursor = _round_up(cursor, sec.align)
            addresses[sec.name] = cursor
            cursor += sec.size
        vmsize = cursor

        header_size = 32
        commands_size = (72 + 80 * len(order)) + 24 + 80
        at = header_size + commands_size
        offsets: dict[str, int] = {}
        for sec in order:
            if sec.is_zerofill:
                # NO FILE OFFSET. A zerofill section with one would have the
                # linker read whatever bytes follow it as initial data.
                offsets[sec.name] = 0
                continue
            at = _round_up(at, sec.align)
            offsets[sec.name] = at
            at += len(bodies[sec.name])
        file_size_end = at
        reloc_at: dict[str, int] = {}
        for sec in order:
            reloc_at[sec.name] = at if sec.relocations else 0
            at += 8 * len(sec.relocations)
        symbols_at = at
        at += 16 * len(ordered)
        strings_at = at

        strings = bytearray(b"\0")
        string_offset: dict[str, int] = {}
        for sym in ordered:
            string_offset[sym.name] = len(strings)
            strings += sym.name.encode("utf-8") + b"\0"

        out = bytearray()
        out += struct.pack(
            "<IiiIIIII", MH_MAGIC_64, self.cpu_type, self.cpu_subtype,
            MH_OBJECT, 3, commands_size, MH_SUBSECTIONS_VIA_SYMBOLS, 0)

        first_body = min((offsets[s.name] for s in order
                          if not s.is_zerofill), default=header_size
                         + commands_size)
        out += struct.pack(
            "<II16sQQQQiiII", LC_SEGMENT_64, 72 + 80 * len(order),
            _fixed(""),                 # one nameless segment in an object
            0, vmsize, first_body, file_size_end - first_body,
            VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE,
            VM_PROT_READ | VM_PROT_WRITE | VM_PROT_EXECUTE,
            len(order), 0)
        for sec in order:
            segment, name = SEGMENT_OF[sec.name]
            out += _fixed(name) + _fixed(segment) + struct.pack(
                "<QQIIIIIIII", addresses[sec.name], sec.size,
                offsets[sec.name], _align_log2(sec.align),
                reloc_at[sec.name], len(sec.relocations), sec.flags, 0, 0, 0)
        out += struct.pack("<IIIIII", LC_SYMTAB, 24, symbols_at, len(ordered),
                           strings_at, len(strings))
        out += struct.pack(
            "<IIIIIIIIIIIIIIIIIIII", LC_DYSYMTAB, 80,
            0, len(locals_),
            len(locals_), len(defined),
            len(locals_) + len(defined), len(undefined),
            0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

        for sec in order:
            if sec.is_zerofill:
                continue
            padding = offsets[sec.name] - len(out)
            # A NEGATIVE PADDING WOULD WRITE NOTHING AND SAY NOTHING, leaving
            # every section after this one at an offset its header does not
            # claim. The offsets and this loop walk the sections in the same
            # order, so it cannot happen -- which is exactly why it should be
            # asserted rather than trusted.
            assert padding >= 0, (
                f"{sec.name} was laid out at {offsets[sec.name]} but "
                f"{len(out)} bytes are already written")
            out += b"\0" * padding
            out += bodies[sec.name]
        for sec in order:
            for rel in sec.relocations:
                out += struct.pack(
                    "<iI", rel.offset,
                    (index_of[rel.symbol] & 0xFFFFFF)
                    | ((1 if rel.pcrel else 0) << 24)
                    | ((rel.length & 3) << 25)
                    | (1 << 27)             # r_extern: always by symbol here
                    | ((rel.kind & 0xF) << 28))
        for sym in ordered:
            n_type = N_EXT if sym.binding != 0 else 0
            n_sect = 0
            if sym.section:
                n_type |= N_SECT
                n_sect = number_of[sym.section]
            value = addresses[sym.section] + sym.value if sym.section else 0
            out += struct.pack("<IBBHQ", string_offset[sym.name], n_type,
                               n_sect, 0, value)
        out += strings
        return bytes(out)


def _round_up(value: int, align: int) -> int:
    if align < 2:
        return value
    return (value + align - 1) & ~(align - 1)


def _apply_addends(sec: Section) -> bytes:
    """The section's bytes with each addend added into the field it patches.

    MACH-O HAS NOWHERE ELSE TO PUT IT, as COFF does not: the relocation record
    is eight bytes of address, symbol, and flags. The value added is already
    Mach-O's -- see `Relocation.addend`.
    """
    if sec.is_zerofill or not sec.relocations:
        return sec.data
    data = bytearray(sec.data)
    for rel in sec.relocations:
        if not rel.addend:
            continue
        width = 1 << rel.length
        form = {1: "<b", 2: "<h", 4: "<i", 8: "<q"}[width]
        current = struct.unpack_from(form, data, rel.offset)[0]
        struct.pack_into(form, data, rel.offset, current + rel.addend)
    return bytes(data)
