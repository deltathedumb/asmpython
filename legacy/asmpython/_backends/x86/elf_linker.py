"""Builtin ELF (Linux i386) executable linker -- no gcc/ld involved.

Same mechanism as the x86-64 backend's own elf_linker.py (merge objects,
resolve symbols, patch relocations, synthesize thunks for external
calls, lay out a final container format), adapted to i386's ELF32
target. Two substantial, real structural differences from that file,
not just narrower field widths:

  - No RIP-relative addressing exists in 32-bit protected mode at all
    (this backend's own encoder.py/codegen.py docstrings). x86-64's GOT
    thunk is `jmp qword [rip+disp32]`; this file's own thunk is `jmp
    dword [disp32]` -- an ABSOLUTE address, not RIP-relative. Verified
    directly against real NASM output (`jmp dword [0x12345678]`
    assembles to `ff 25 78 56 34 12`, the exact same `0xFF 0x25` opcode
    bytes as x86-64's own thunk, but the trailing disp32 means "the
    absolute address of this dword" here instead of "signed offset from
    the next instruction's address" -- both encodings share ModRM's
    mod=00/rm=101 "disp32, no base register" form, they just differ in
    whether the CPU treats that disp32 as PC-relative or absolute,
    which is exactly the 64-bit-vs-32-bit distinction this whole
    backend has been built around from the start). This is actually
    SIMPLER than x86-64's version: an absolute address needs no disp
    arithmetic relative to the thunk's own position at all, since this
    linker already builds a fixed-address (non-PIE) ET_EXEC -- there is
    no reason for its own SYNTHESIZED thunks to be position-independent
    just because an individual linked-in OBJECT might itself be PIC.

  - i386's psABI genuinely uses REL (implicit addend, baked into the
    relocated field's own bytes) for EVERY relocation table, including
    the runtime `.rel.dyn` the dynamic linker itself consumes at
    process start -- not just this backend's own `.rel.text` (already
    established in elf.py/elf_parse.py). Confirmed via the i386 psABI
    documentation directly (not assumed from x86-64's RELA-based
    .rela.dyn): "the psABI specifies the use of DT_REL format rather
    than DT_RELA" for i386 specifically, since REL is more compact
    (8 bytes vs RELA's 12) and i386 was always REL-based project-wide,
    unlike x86-64 which is RELA-based project-wide. This means
    R_386_GLOB_DAT's addend (always 0 for this relocation type in
    practice, both here and on x86-64) is simply never written as a
    separate field at all -- Elf32_Rel has no addend field to write.

Every other piece keeps the same structure as the x86-64 file: GOT
slot + thunk for external functions (filled in by the dynamic linker
via R_386_GLOB_DAT before _start runs), a copy relocation
(R_386_COPY) into a local .bss slot for external data symbols, a
fixed-address (non-PIE) ET_EXEC/ET_DYN with no section header table
(program headers only), and no real lazy PLT (everything resolved
eagerly via .rel.dyn, exactly like the x86-64 version's own rationale).
"""

from __future__ import annotations

import struct

from .elf_parse import parse_elf, ElfObject

IMAGE_BASE = 0x08048000  # conventional i386 Linux ET_EXEC base (vs x86-64's 0x400000)
PAGE_SIZE = 0x1000

R_386_PC32 = 2
R_386_PLT32 = 4
R_386_COPY = 5
R_386_GLOB_DAT = 6
R_386_GOTPC = 10

# The synthetic GOT-base symbol codegen.py's PIC prologue relocates against
# (see its own docstring): every real linker defines this automatically as
# the runtime address of the GOT itself, never resolving it as an external
# .so import. This linker's own GOT lives at vaddr_rest + got_off_in_rest
# (the same address func_imports' thunk slots are indexed from below).
_GOT_BASE_SYMBOL = "_GLOBAL_OFFSET_TABLE_"

PT_LOAD = 1
PT_DYNAMIC = 2
PT_INTERP = 3
PT_GNU_STACK = 0x6474E551
PF_X, PF_W, PF_R = 1, 2, 4

DT_NEEDED, DT_HASH, DT_STRTAB, DT_SYMTAB = 1, 4, 5, 6
DT_REL, DT_RELSZ, DT_RELENT, DT_STRSZ, DT_SYMENT, DT_NULL = 17, 18, 19, 10, 11, 0
DT_SONAME = 14
DT_INIT_ARRAY, DT_INIT_ARRAYSZ = 25, 27

STB_GLOBAL = 1
STT_FUNC, STT_OBJECT, STT_NOTYPE = 2, 1, 0
SHN_UNDEF = 0
SHN_ABS = 0xFFF1
ET_EXEC = 2
ET_DYN = 3
EM_386 = 3

_BUCKET_FOR_SECTION = {".text": "text", ".data": "data", ".rodata": "rdata", ".bss": "bss"}

# symbol name -> .so providing it. Same rationale/split as the x86-64
# linker's own table (glibc's own libc.so.6/libm.so.6 split is
# architecture-independent -- this is a fact about glibc's own library
# layout, not about x86-64 vs i386).
_SO_FOR_SYMBOL: dict[str, str] = {}
for _name in (
    "malloc", "realloc", "free", "calloc",
    "printf", "sprintf", "putchar", "puts", "fputs", "fputc",
    "strlen", "strcmp", "strstr", "strdup", "strtoll",
    "atof", "strtod", "fgets", "fopen", "fgetc", "fclose", "fflush", "access",
    "fread", "fseek", "ftell",
    "exit", "memset", "memcpy", "rand", "modf",
    "abs", "labs",
    # This backend's own always-linked runtime helpers (abi_shims_x86_32.asm)
    # are NOT external .so imports -- they're expected to already be one of
    # the merged `objects` passed to link_elf, exactly like any other
    # asmpython-compiled object. Nothing added here for them.
):
    _SO_FOR_SYMBOL[_name] = "libc.so.6"
for _name in ("fmod", "pow", "fabs", "frexp", "ldexp", "log", "sqrt"):
    _SO_FOR_SYMBOL[_name] = "libm.so.6"
for _name in ("dlopen", "dlsym"):
    _SO_FOR_SYMBOL[_name] = "libdl.so.2"

_DATA_SYMBOLS: dict[str, int] = {"stdin": 4}  # a 32-bit FILE* is 4 bytes wide here, not 8


def _align(n: int, a: int) -> int:
    return (n + a - 1) & ~(a - 1)


class LinkError(Exception):
    pass


def link_elf(
    objects: list[bytes],
    entry_symbol: str = "main",
    *,
    is_library: bool = False,
    exports: "list[str] | tuple[str, ...]" = (),
    soname: str = "libportapy.so",
) -> bytes:
    """Link into an ELF32 ET_EXEC executable, or (`is_library=True`) an
    ET_DYN shared object. See the x86-64 linker's own docstring for the
    full is_library/exports/soname semantics -- identical here."""
    parsed: list[ElfObject] = [parse_elf(o) for o in objects]

    # ── 1. Merge .text/.data/.rodata across objects (.bss sized below). ──
    bucket_bytes = {"text": bytearray(), "data": bytearray(), "rdata": bytearray()}
    bucket_align = {"text": 16, "data": 4, "rdata": 1}
    bss_size = 0
    sect_base: dict[tuple[int, str], int] = {}

    for oi, obj in enumerate(parsed):
        for sect in obj.sections:
            bucket = _BUCKET_FOR_SECTION.get(sect.name)
            if bucket is None:
                continue
            if bucket == "bss":
                bss_size = _align(bss_size, 4)
                sect_base[(oi, sect.name)] = bss_size
                bss_size += len(sect.data)
                continue
            buf = bucket_bytes[bucket]
            pad = (-len(buf)) & (bucket_align[bucket] - 1)
            buf.extend(b"\x90" * pad if bucket == "text" else b"\x00" * pad)
            sect_base[(oi, sect.name)] = len(buf)
            buf.extend(sect.data)

    # ── 2. Global symbol resolution. ──
    global_syms: dict[str, tuple[str, int]] = {}
    for oi, obj in enumerate(parsed):
        for sym in obj.symbols:
            if not sym.name or sym.shndx <= 0:
                continue
            sect = obj.sections[sym.shndx - 1]
            bucket = _BUCKET_FOR_SECTION.get(sect.name)
            if bucket is None:
                continue
            base = sect_base[(oi, sect.name)]
            global_syms.setdefault(sym.name, (bucket, base + sym.value))

    if not is_library and entry_symbol not in global_syms:
        raise LinkError(f"entry symbol {entry_symbol!r} not defined in any input object")
    for export_name in exports:
        if export_name not in global_syms:
            raise LinkError(f"export symbol {export_name!r} not defined in any input object")

    # ── 3. Collect external symbols. ──
    func_imports: list[str] = []
    data_imports: list[str] = []
    seen: set[str] = set()
    data_slot_off: dict[str, int] = {}

    for obj in parsed:
        for sect in obj.sections:
            for r in sect.relocs:
                if r.section_idx is not None:
                    continue
                name = r.symbol
                if name == _GOT_BASE_SYMBOL:
                    # Never an external import -- resolve() and the patch
                    # loop below both special-case this name directly
                    # against the GOT's own runtime address.
                    continue
                if name in global_syms or name in seen:
                    continue
                if name in _DATA_SYMBOLS:
                    seen.add(name)
                    data_imports.append(name)
                    bss_size = _align(bss_size, 4)
                    data_slot_off[name] = bss_size
                    bss_size += _DATA_SYMBOLS[name]
                    global_syms[name] = ("bss", data_slot_off[name])
                    continue
                if name not in _SO_FOR_SYMBOL:
                    raise LinkError(
                        f"undefined symbol {name!r} has no known .so "
                        f"(add it to elf_linker._SO_FOR_SYMBOL if it's a real import)"
                    )
                seen.add(name)
                func_imports.append(name)
    # exit() powers the executable entry stub (see step 7) even if
    # nothing else calls it -- but only force it in as an EXTERNAL
    # libc.so.6 import if it isn't already a real, locally-defined
    # symbol from one of the merged input objects. `not in global_syms`
    # is the check that actually matters here; `not in seen` alone
    # (this file's earlier draft, inherited verbatim from the x86-64
    # linker) would unconditionally force the import regardless of
    # whether a real, merged `exit` already exists, silently shadowing
    # a legitimate local definition with an external one -- confirmed
    # via a real test that merged its own local `exit` object
    # specifically to avoid needing the dynamic linker at all (this
    # environment's real /lib/ld-linux.so.2 isn't installed): the
    # linker kept emitting PT_INTERP/PT_DYNAMIC/a libc.so.6 NEEDED
    # entry regardless, because this check never looked at
    # global_syms in the first place.
    if not is_library and "exit" not in global_syms and "exit" not in seen:
        seen.add("exit")
        func_imports.append("exit")
    func_imports.sort(key=lambda n: (_SO_FOR_SYMBOL[n], n))

    # ── 4. Lay out .text: merged code, one 6-byte thunk per function
    # import (`jmp dword [abs_got_slot]` -- absolute, no RIP-relative
    # addressing exists here), then the entry stub. ──
    text_body_len = len(bucket_bytes["text"])
    thunk_off = {name: text_body_len + i * 6 for i, name in enumerate(func_imports)}
    entry_stub_off = text_body_len + 6 * len(func_imports)
    has_module_init = "__asmpy_module_init" in global_syms
    library_init_symbol = None
    if is_library:
        if has_module_init:
            library_init_symbol = "__asmpy_module_init"
        elif "main" in global_syms:
            library_init_symbol = "main"
        has_module_init = library_init_symbol is not None
        entry_stub_len = 5 if has_module_init else 0  # jmp rel32 module_init
    else:
        # cdecl entry stub: [call rel32 module_init] ; call rel32 main ;
        # push eax ; call rel32 exit_thunk ; ud2. Unlike x86-64's `and
        # rsp,-16` (SysV's 16-byte stack-alignment requirement at the
        # call boundary) followed by `mov edi,eax` (passing the return
        # value as exit()'s argument via x86-64's register-based ABI),
        # cdecl has no equivalent alignment requirement to enforce here
        # (this backend's own codegen.py never assumes anything beyond
        # ESP being valid, and cdecl's own baseline convention has no
        # SysV-style "16-byte aligned at every call" rule at all -- that
        # requirement is unique to the x86-64 SysV ABI, not a general
        # x86 fact) and passes the exit code via a PUSH (cdecl's own
        # stack-based argument convention), not a register.
        entry_stub_len = (5 if has_module_init else 0) + 5 + 1 + 5 + 2

    text_total_len = entry_stub_off + entry_stub_len

    # ── 5. Decide segment layout. ──
    HEADERS_RESERVE = 0x1000
    vaddr_text = IMAGE_BASE + HEADERS_RESERVE
    vaddr_rest = vaddr_text + _align(text_total_len, PAGE_SIZE)

    rdata_off_in_rest = 0
    data_off_in_rest = len(bucket_bytes["rdata"])
    rest_static_len = data_off_in_rest + len(bucket_bytes["data"])

    def _bucket_vaddr(bucket: str, off: int) -> int:
        if bucket == "text":
            return vaddr_text + off
        if bucket == "rdata":
            return vaddr_rest + rdata_off_in_rest + off
        if bucket == "data":
            return vaddr_rest + data_off_in_rest + off
        if bucket == "bss":
            return vaddr_bss + off
        raise LinkError(f"unknown bucket {bucket!r}")

    def resolve(name: str) -> int:
        if name == _GOT_BASE_SYMBOL:
            # Assigned later in this function (step 6, once the .rest
            # layout is known) -- safe: every real call to resolve()
            # against this name happens in the relocation-patching loop
            # (step 7), well after got_off_in_rest exists.
            return vaddr_rest + got_off_in_rest
        if name in global_syms:
            bucket, off = global_syms[name]
            return _bucket_vaddr(bucket, off)
        if name in thunk_off:
            return vaddr_text + thunk_off[name]
        raise LinkError(f"unresolved symbol {name!r}")

    def resolve_section(oi: int, section_idx_1based: int) -> int:
        sect = parsed[oi].sections[section_idx_1based - 1]
        bucket = _BUCKET_FOR_SECTION.get(sect.name)
        if bucket is None:
            raise LinkError(f"section-relative relocation against unhandled section {sect.name!r}")
        return _bucket_vaddr(bucket, sect_base[(oi, sect.name)])

    # ── 6. Dynamic-linking metadata. ──
    needed_sos = sorted({_SO_FOR_SYMBOL[n] for n in func_imports})
    sorted_exports = sorted(set(exports))
    dynsym_names = ["", *func_imports, *data_imports, *sorted_exports]

    dynstr = bytearray(b"\x00")
    dynstr_off: dict[str, int] = {}
    for s in [*needed_sos, *func_imports, *data_imports, *sorted_exports, soname]:
        if s not in dynstr_off:
            dynstr_off[s] = len(dynstr)
            dynstr.extend(s.encode("ascii") + b"\x00")
    dynstr_bytes = bytes(dynstr)

    dynsym = bytearray()
    for nm in dynsym_names:
        if nm == "":
            # Elf32_Sym field order: name/value/size/info/other/shndx --
            # the reverse of Elf64_Sym (see elf.py's own docstring).
            dynsym += struct.pack("<IIIBBH", 0, 0, 0, 0, 0, SHN_UNDEF)
            continue
        if nm in sorted_exports:
            bucket, off = global_syms[nm]
            dynsym += struct.pack(
                "<IIIBBH",
                dynstr_off[nm], _bucket_vaddr(bucket, off), 0,
                (STB_GLOBAL << 4) | STT_FUNC, 0, SHN_ABS,
            )
            continue
        typ = STT_OBJECT if nm in _DATA_SYMBOLS else STT_FUNC
        size = _DATA_SYMBOLS.get(nm, 0)
        dynsym += struct.pack(
            "<IIIBBH", dynstr_off[nm], 0, size, (STB_GLOBAL << 4) | typ, 0, SHN_UNDEF
        )
    dynsym_bytes = bytes(dynsym)
    dynsym_idx = {nm: i for i, nm in enumerate(dynsym_names)}

    n_dynsym = len(dynsym_names)
    hash_bucket = [1 if n_dynsym > 1 else 0]
    hash_chain = [0] * n_dynsym
    for i in range(1, n_dynsym - 1):
        hash_chain[i] = i + 1
    hash_bytes = struct.pack("<II", len(hash_bucket), n_dynsym)
    hash_bytes += struct.pack(f"<{len(hash_bucket)}I", *hash_bucket)
    hash_bytes += struct.pack(f"<{n_dynsym}I", *hash_chain)

    got_off_in_rest = rest_static_len
    got_bytes_len = 4 * len(func_imports)  # 4-byte GOT slots, not 8

    dynsym_off_in_rest = got_off_in_rest + got_bytes_len
    dynsym_off_in_rest = _align(dynsym_off_in_rest, 4)
    dynstr_off_in_rest = dynsym_off_in_rest + len(dynsym_bytes)
    hash_off_in_rest = _align(dynstr_off_in_rest + len(dynstr_bytes), 4)
    interp_str = b"/lib/ld-linux.so.2\x00"
    interp_off_in_rest = hash_off_in_rest + len(hash_bytes)

    n_rel = len(func_imports) + len(data_imports)
    n_dyn_entries = len(needed_sos) + 9  # +HASH/STRTAB/SYMTAB/REL/RELSZ/RELENT/STRSZ/SYMENT/NULL
    has_init_array = is_library and has_module_init
    if is_library:
        n_dyn_entries += 1  # +SONAME
    if has_init_array:
        n_dyn_entries += 2  # +INIT_ARRAY, +INIT_ARRAYSZ
    rel_dyn_off_in_rest = _align(interp_off_in_rest + len(interp_str), 4)
    dynamic_off_in_rest = rel_dyn_off_in_rest + n_rel * 8   # Elf32_Rel = 8 bytes, not 24
    init_array_off_in_rest = dynamic_off_in_rest + n_dyn_entries * 8  # Elf32_Dyn = 8 bytes, not 16
    rest_total_len = init_array_off_in_rest + (4 if has_init_array else 0)
    vaddr_bss = vaddr_rest + _align(rest_total_len, PAGE_SIZE)

    # .rel.dyn: one GLOB_DAT per function import, one COPY per data
    # import -- REL, no addend field (i386's own psABI convention, see
    # this module's own docstring). ELF32_R_INFO(sym, type) =
    # (sym << 8) | type, matching elf.py's own _rel() exactly.
    rel_dyn = bytearray()
    for i, nm in enumerate(func_imports):
        got_slot_vaddr = vaddr_rest + got_off_in_rest + i * 4
        r_info = ((dynsym_idx[nm] & 0xFFFFFF) << 8) | R_386_GLOB_DAT
        rel_dyn += struct.pack("<II", got_slot_vaddr, r_info)
    for nm in data_imports:
        slot_vaddr = resolve(nm)
        r_info = ((dynsym_idx[nm] & 0xFFFFFF) << 8) | R_386_COPY
        rel_dyn += struct.pack("<II", slot_vaddr, r_info)
    rel_dyn_bytes = bytes(rel_dyn)
    assert len(rel_dyn_bytes) == n_rel * 8

    def _dyn(tag: int, val: int) -> bytes:
        # Elf32_Dyn: <ii> (d_tag, d_val/d_ptr share one union field, both
        # 4 bytes) -- 8 bytes total, not Elf64_Dyn's <qQ> 16 bytes.
        return struct.pack("<ii", tag, val)

    dynamic = bytearray()
    if is_library:
        dynamic += _dyn(DT_SONAME, dynstr_off[soname])
    for so in needed_sos:
        dynamic += _dyn(DT_NEEDED, dynstr_off[so])
    dynamic += _dyn(DT_HASH, vaddr_rest + hash_off_in_rest)
    dynamic += _dyn(DT_STRTAB, vaddr_rest + dynstr_off_in_rest)
    dynamic += _dyn(DT_SYMTAB, vaddr_rest + dynsym_off_in_rest)
    dynamic += _dyn(DT_REL, vaddr_rest + rel_dyn_off_in_rest)
    dynamic += _dyn(DT_RELSZ, len(rel_dyn_bytes))
    dynamic += _dyn(DT_RELENT, 8)
    dynamic += _dyn(DT_STRSZ, len(dynstr_bytes))
    dynamic += _dyn(DT_SYMENT, 16)
    if has_init_array:
        dynamic += _dyn(DT_INIT_ARRAY, vaddr_rest + init_array_off_in_rest)
        dynamic += _dyn(DT_INIT_ARRAYSZ, 4)
    dynamic += _dyn(DT_NULL, 0)
    dynamic_bytes = bytes(dynamic)
    assert len(dynamic_bytes) == n_dyn_entries * 8

    # ── 7. Finalize .text: thunks, entry stub, then patch relocations. ──
    text = bucket_bytes["text"]
    text.extend(b"\x90" * (text_total_len - len(text)))
    for i, name in enumerate(func_imports):
        pos = thunk_off[name]
        got_slot_vaddr = vaddr_rest + got_off_in_rest + i * 4
        # jmp dword [abs_addr] -- absolute, not RIP-relative (verified
        # against real NASM: see this module's own docstring).
        text[pos:pos + 6] = bytes([0xFF, 0x25]) + struct.pack("<I", got_slot_vaddr)

    if is_library:
        init_addr = resolve(library_init_symbol) if has_module_init else 0
    else:
        init_addr = resolve("__asmpy_module_init") if has_module_init else 0
    stub_addr = vaddr_text + entry_stub_off
    if is_library:
        if has_module_init:
            stub = bytearray()
            jmp_disp_pos = 1
            stub += bytes([0xE9, 0, 0, 0, 0])   # jmp rel32 module init
            assert len(stub) == entry_stub_len
            struct.pack_into("<i", stub, jmp_disp_pos, init_addr - (stub_addr + jmp_disp_pos + 4))
            text[entry_stub_off:entry_stub_off + entry_stub_len] = bytes(stub)
    else:
        main_addr = resolve(entry_symbol)
        stub = bytearray()
        if has_module_init:
            call_init_disp_pos = len(stub) + 1
            stub += bytes([0xE8, 0, 0, 0, 0])       # call rel32 module init
        call_disp_pos = len(stub) + 1
        stub += bytes([0xE8, 0, 0, 0, 0])           # call rel32 main
        stub += bytes([0x50])                       # push eax (cdecl exit() arg)
        call2_disp_pos = len(stub) + 1
        stub += bytes([0xE8, 0, 0, 0, 0])           # call rel32 exit thunk
        stub += bytes([0x0F, 0x0B])                 # ud2 (unreachable -- exit() never returns)
        assert len(stub) == entry_stub_len
        if has_module_init:
            struct.pack_into("<i", stub, call_init_disp_pos, init_addr - (stub_addr + call_init_disp_pos + 4))
        struct.pack_into("<i", stub, call_disp_pos, main_addr - (stub_addr + call_disp_pos + 4))
        # resolve(), not a direct thunk_off["exit"] lookup -- exit may
        # now be a real, locally-merged symbol (see the func_imports
        # fix above), not always an external thunk; resolve() already
        # handles both cases uniformly.
        exit_addr = resolve("exit")
        struct.pack_into("<i", stub, call2_disp_pos, exit_addr - (stub_addr + call2_disp_pos + 4))
        text[entry_stub_off:entry_stub_off + entry_stub_len] = bytes(stub)

    for oi, obj in enumerate(parsed):
        for sect in obj.sections:
            if _BUCKET_FOR_SECTION.get(sect.name) != "text":
                continue
            base = sect_base[(oi, sect.name)]
            for r in sect.relocs:
                if r.rtype not in (R_386_PC32, R_386_PLT32, R_386_GOTPC):
                    raise LinkError(f"unsupported relocation type {r.rtype} for {r.symbol!r}")
                patch_off = base + r.offset
                patch_addr = vaddr_text + patch_off
                target_addr = (
                    resolve_section(oi, r.section_idx)
                    if r.section_idx is not None
                    else resolve(r.symbol)
                )
                # r.addend was already read back from the ORIGINAL
                # pre-relocation bytes at parse time (elf_parse.py's own
                # REL-implicit-addend handling) -- codegen.py's own -4
                # convention for PC32/PLT32 call-rel32 sites, or its own
                # field-to-instruction-start byte offset (here, 2) for
                # GOTPC. Both share the identical `S + A - P` shape (the
                # i386 psABI's own relocation formula for both types --
                # GOTPC substitutes the GOT's own address for the
                # ordinary "symbol address" S), so one formula covers
                # both; only the meaning of `target_addr` differs, and
                # that's already handled by resolve()'s own GOT-base
                # special case.
                rel = target_addr + r.addend - patch_addr
                struct.pack_into("<i", text, patch_off, rel)

    # ── 8. Assemble the ELF file. ──
    rest = bytearray(rest_total_len)
    rest[rdata_off_in_rest:rdata_off_in_rest + len(bucket_bytes["rdata"])] = bucket_bytes["rdata"]
    rest[data_off_in_rest:data_off_in_rest + len(bucket_bytes["data"])] = bucket_bytes["data"]
    rest[dynsym_off_in_rest:dynsym_off_in_rest + len(dynsym_bytes)] = dynsym_bytes
    rest[dynstr_off_in_rest:dynstr_off_in_rest + len(dynstr_bytes)] = dynstr_bytes
    rest[hash_off_in_rest:hash_off_in_rest + len(hash_bytes)] = hash_bytes
    rest[interp_off_in_rest:interp_off_in_rest + len(interp_str)] = interp_str
    rest[rel_dyn_off_in_rest:rel_dyn_off_in_rest + len(rel_dyn_bytes)] = rel_dyn_bytes
    rest[dynamic_off_in_rest:dynamic_off_in_rest + len(dynamic_bytes)] = dynamic_bytes
    if has_init_array:
        struct.pack_into("<I", rest, init_array_off_in_rest, stub_addr)

    return _build_elf_image(
        text_bytes=bytes(text),
        rest_bytes=bytes(rest),
        vaddr_text=vaddr_text,
        vaddr_rest=vaddr_rest,
        vaddr_bss=vaddr_bss,
        bss_size=bss_size,
        entry_vaddr=stub_addr if not is_library else 0,
        interp_vaddr=vaddr_rest + interp_off_in_rest,
        interp_len=len(interp_str),
        dynamic_vaddr=vaddr_rest + dynamic_off_in_rest,
        dynamic_len=len(dynamic_bytes),
        is_dyn=is_library,
        omit_interp=is_library,
    )


def _build_elf_image(
    *, text_bytes: bytes, rest_bytes: bytes,
    vaddr_text: int, vaddr_rest: int, vaddr_bss: int, bss_size: int,
    entry_vaddr: int, interp_vaddr: int, interp_len: int,
    dynamic_vaddr: int, dynamic_len: int,
    is_dyn: bool = False, omit_interp: bool = False,
) -> bytes:
    EHDR_SIZE = 52   # not 64 -- see elf.py's own docstring
    PHDR_SIZE = 32   # Elf32_Phdr, not Elf64_Phdr's 56
    num_phdrs = 4 if omit_interp else 5

    phoff = EHDR_SIZE
    off_text = PAGE_SIZE
    off_rest = off_text + _align(len(text_bytes), PAGE_SIZE)

    phdrs = bytearray()

    def phdr(p_type, p_flags, offset, vaddr, filesz, memsz, align):
        # Elf32_Phdr field order: type/offset/vaddr/paddr/filesz/memsz/
        # flags/align -- p_flags comes AFTER memsz here, unlike
        # Elf64_Phdr's type/flags/offset/vaddr/paddr/filesz/memsz/align
        # (flags moves from 2nd field to 7th between the two formats).
        phdrs.extend(struct.pack(
            "<IIIIIIII", p_type, offset, vaddr, vaddr, filesz, memsz, p_flags, align
        ))

    phdr(PT_LOAD, PF_R | PF_X, 0, vaddr_text - PAGE_SIZE, off_text + len(text_bytes),
         off_text + len(text_bytes), PAGE_SIZE)
    phdr(PT_LOAD, PF_R | PF_W, off_rest, vaddr_rest, len(rest_bytes), len(rest_bytes), PAGE_SIZE)
    bss_offset = _align(off_rest + len(rest_bytes), PAGE_SIZE)
    phdr(PT_LOAD, PF_R | PF_W, bss_offset, vaddr_bss, 0, bss_size, PAGE_SIZE)
    if not omit_interp:
        phdr(PT_INTERP, PF_R, off_rest + (interp_vaddr - vaddr_rest), interp_vaddr,
             interp_len, interp_len, 1)
    phdr(PT_DYNAMIC, PF_R | PF_W, off_rest + (dynamic_vaddr - vaddr_rest), dynamic_vaddr,
         dynamic_len, dynamic_len, 4)

    ident = bytes([0x7F, 0x45, 0x4C, 0x46, 1, 1, 1, 0]) + bytes(8)  # ELFCLASS32=1
    ehdr = struct.pack(
        "<16sHHIIIIIHHHHHH",
        ident, ET_DYN if is_dyn else ET_EXEC, EM_386, 1,
        entry_vaddr, phoff, 0,
        0, EHDR_SIZE, PHDR_SIZE, num_phdrs, 0, 0, 0,
    )

    image = bytearray(off_rest + len(rest_bytes))
    image[0:EHDR_SIZE] = ehdr
    image[phoff:phoff + len(phdrs)] = phdrs
    image[off_text:off_text + len(text_bytes)] = text_bytes
    image[off_rest:off_rest + len(rest_bytes)] = rest_bytes
    return bytes(image)
