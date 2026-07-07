"""Builtin ELF (Linux x86-64) executable linker -- no gcc/ld involved.

Same mechanism as pe_linker.py (merge objects, resolve symbols, patch
REL32-ish relocations, synthesize thunks for external calls, lay out a
final container format) adapted to ELF's dynamic-linking model instead of
PE's import table:

  - external *function* symbols (malloc, printf, ...) get a GOT slot plus
    a local thunk (`jmp qword [rip+disp32]` into that slot, exactly like
    PE's import thunks) -- call sites relocate to the thunk, not the
    slot. The GOT slot is filled in by the dynamic linker before _start
    runs, via an R_X86_64_GLOB_DAT relocation in .rela.dyn.

  - external *data* symbols (stdin, ...) get a copy relocation
    (R_X86_64_COPY) into a local .bss slot instead -- the loader copies
    the real variable's initial value in before _start runs, and our own
    code just treats that local slot as if it were the real thing
    (matches how non-PIC C compilers have always handled this, predating
    GOT-indirected data access entirely).

This builds a non-PIE ET_EXEC at a fixed load address (no PIE/ASLR, no
R_X86_64_RELATIVE entries needed -- every one of our own addresses is
already absolute). A real lazy PLT with a resolver stub is deliberately
not implemented: nothing here needs lazy binding's only benefit (faster
startup for huge symbol counts), and resolving everything eagerly via
.rela.dyn is far simpler to get right.
"""

from __future__ import annotations

import struct

from .elf_parse import parse_elf, ElfObject

IMAGE_BASE = 0x400000
PAGE_SIZE = 0x1000

R_X86_64_PC32 = 2
R_X86_64_PLT32 = 4
R_X86_64_COPY = 5
R_X86_64_GLOB_DAT = 6

PT_LOAD = 1
PT_DYNAMIC = 2
PT_INTERP = 3
PT_GNU_STACK = 0x6474E551
PF_X, PF_W, PF_R = 1, 2, 4

DT_NEEDED, DT_HASH, DT_STRTAB, DT_SYMTAB = 1, 4, 5, 6
DT_RELA, DT_RELASZ, DT_RELAENT, DT_STRSZ, DT_SYMENT, DT_NULL = 7, 8, 9, 10, 11, 0

STB_GLOBAL = 1
STT_FUNC, STT_OBJECT, STT_NOTYPE = 2, 1, 0
SHN_UNDEF = 0

_BUCKET_FOR_SECTION = {".text": "text", ".data": "data", ".rodata": "rdata", ".bss": "bss"}

# symbol name -> .so providing it. libc.so.6 covers most things; libm.so.6
# is still separate on this glibc (confirmed via `nm -D` against the real
# system libraries: pow/fmod/log/sqrt/etc aren't in libc.so.6 itself,
# unlike msvcrt.dll where everything lands in one DLL on Windows).
_SO_FOR_SYMBOL: dict[str, str] = {}
for _name in (
    "malloc", "realloc", "free", "calloc",
    "printf", "sprintf", "putchar", "puts", "fputs", "fputc",
    "strlen", "strcmp", "strstr", "strdup", "strtoll",
    "atof", "strtod", "fgets", "fopen", "fgetc", "fclose", "fflush", "access",
    "fread", "fseek", "ftell",
    "exit", "memset", "memcpy", "rand", "modf",
    "abs", "labs",
):
    _SO_FOR_SYMBOL[_name] = "libc.so.6"
for _name in ("fmod", "pow", "fabs", "frexp", "ldexp", "log", "sqrt"):
    _SO_FOR_SYMBOL[_name] = "libm.so.6"

# External *data* symbols needing a copy relocation instead of a function
# thunk (see module docstring). size in bytes, as glibc defines it.
_DATA_SYMBOLS: dict[str, int] = {"stdin": 8}


def _align(n: int, a: int) -> int:
    return (n + a - 1) & ~(a - 1)


class LinkError(Exception):
    pass


def _elf64_hash(name: str) -> int:
    h = 0
    for c in name.encode("ascii"):
        h = ((h << 4) + c) & 0xFFFFFFFF
        g = h & 0xF0000000
        if g:
            h ^= g >> 24
        h &= ~g & 0xFFFFFFFF
    return h


def link_elf(objects: list[bytes], entry_symbol: str = "main") -> bytes:
    parsed: list[ElfObject] = [parse_elf(o) for o in objects]

    # ── 1. Merge .text/.data/.rodata across objects (.bss sized below). ──
    bucket_bytes = {"text": bytearray(), "data": bytearray(), "rdata": bytearray()}
    bucket_align = {"text": 16, "data": 8, "rdata": 1}
    bss_size = 0
    sect_base: dict[tuple[int, str], int] = {}

    for oi, obj in enumerate(parsed):
        for sect in obj.sections:
            bucket = _BUCKET_FOR_SECTION.get(sect.name)
            if bucket is None:
                continue
            if bucket == "bss":
                # NASM leaves every .bss symbol's ELF `size` field at 0
                # (confirmed against the real runtime object), so per-
                # symbol sizing can't work -- trust the *section's*
                # reported size instead (len(sect.data) == its sh_size
                # even though .bss has no real file content backing it).
                bss_size = _align(bss_size, 8)
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

    if entry_symbol not in global_syms:
        raise LinkError(f"entry symbol {entry_symbol!r} not defined in any input object")

    # ── 3. Collect external symbols: functions need a GOT thunk, data
    # symbols (stdin) need a local .bss copy-relocation slot instead. ──
    func_imports: list[str] = []
    data_imports: list[str] = []
    seen: set[str] = set()
    data_slot_off: dict[str, int] = {}

    for obj in parsed:
        for sect in obj.sections:
            for r in sect.relocs:
                if r.section_idx is not None:
                    continue  # intra-object section-relative ref, not an import
                name = r.symbol
                if name in global_syms or name in seen:
                    continue
                if name in _DATA_SYMBOLS:
                    seen.add(name)
                    data_imports.append(name)
                    bss_size = _align(bss_size, 8)
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
    # exit() powers the entry stub even if nothing else calls it -- calling
    # it (rather than a raw exit_group syscall) matters because it runs
    # libc's atexit handlers, which is what actually flushes stdio's
    # buffers. A bare syscall skips that, so printf's output silently
    # never appears whenever stdout isn't a line-buffered tty (i.e.
    # always, when piped/redirected/captured).
    if "exit" not in seen:
        seen.add("exit")
        func_imports.append("exit")
    func_imports.sort(key=lambda n: (_SO_FOR_SYMBOL[n], n))

    # ── 4. Lay out .text: merged code, then one 6-byte thunk per function
    # import (`jmp qword [rip+disp32]` into its GOT slot), then the entry
    # stub. ──
    text_body_len = len(bucket_bytes["text"])
    thunk_off = {name: text_body_len + i * 6 for i, name in enumerate(func_imports)}
    entry_stub_off = text_body_len + 6 * len(func_imports)
    has_module_init = "__asmpy_module_init" in global_syms
    # and rsp,-16 ; [call rel32 module_init] ; call rel32 main ; mov edi,eax ;
    # call rel32 exit_thunk ; ud2
    entry_stub_len = 4 + (5 if has_module_init else 0) + 5 + 2 + 5 + 2
    text_total_len = entry_stub_off + entry_stub_len

    # ── 5. Decide segment layout (page-aligned: .text, then everything
    # else -- rodata/data/dynamic-linking metadata/got -- then .bss). ──
    HEADERS_RESERVE = 0x1000  # ELF header + phdrs; one page is plenty
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

    # ── 6. Dynamic-linking metadata (.dynstr/.dynsym/.hash/.got), laid
    # out within the "rest" segment right after rodata+data. ──
    needed_sos = sorted({_SO_FOR_SYMBOL[n] for n in func_imports})
    dynsym_names = ["", *func_imports, *data_imports]  # index 0 = STN_UNDEF, always empty name

    dynstr = bytearray(b"\x00")
    dynstr_off: dict[str, int] = {}
    for s in [*needed_sos, *func_imports, *data_imports]:
        if s not in dynstr_off:
            dynstr_off[s] = len(dynstr)
            dynstr.extend(s.encode("ascii") + b"\x00")
    dynstr_bytes = bytes(dynstr)

    dynsym = bytearray()
    for nm in dynsym_names:
        if nm == "":
            dynsym += struct.pack("<IBBHQQ", 0, 0, 0, SHN_UNDEF, 0, 0)
            continue
        typ = STT_OBJECT if nm in _DATA_SYMBOLS else STT_FUNC
        size = _DATA_SYMBOLS.get(nm, 0)
        dynsym += struct.pack(
            "<IBBHQQ", dynstr_off[nm], (STB_GLOBAL << 4) | typ, 0, SHN_UNDEF, 0, size
        )
    dynsym_bytes = bytes(dynsym)
    dynsym_idx = {nm: i for i, nm in enumerate(dynsym_names)}

    # SysV .hash, single bucket (correct for any bucket count >= 1, just
    # O(n) lookup -- fine for the handful of imports a program has here).
    n_dynsym = len(dynsym_names)
    hash_bucket = [1 if n_dynsym > 1 else 0]
    hash_chain = [0] * n_dynsym
    for i in range(1, n_dynsym - 1):
        hash_chain[i] = i + 1
    hash_bytes = struct.pack("<II", len(hash_bucket), n_dynsym)
    hash_bytes += struct.pack(f"<{len(hash_bucket)}I", *hash_bucket)
    hash_bytes += struct.pack(f"<{n_dynsym}I", *hash_chain)

    got_off_in_rest = rest_static_len
    got_bytes_len = 8 * len(func_imports)

    dynsym_off_in_rest = got_off_in_rest + got_bytes_len
    dynsym_off_in_rest = _align(dynsym_off_in_rest, 8)
    dynstr_off_in_rest = dynsym_off_in_rest + len(dynsym_bytes)
    hash_off_in_rest = _align(dynstr_off_in_rest + len(dynstr_bytes), 4)
    interp_str = b"/lib64/ld-linux-x86-64.so.2\x00"
    interp_off_in_rest = hash_off_in_rest + len(hash_bytes)

    # Sizes only, first -- vaddr_bss must be known before resolve() can be
    # called for data imports (their slots live in .bss), but the .bss
    # segment comes *after* all of this, so its address depends on all of
    # these sizes (not their content).
    n_rela = len(func_imports) + len(data_imports)
    n_dyn_entries = len(needed_sos) + 9  # +HASH/STRTAB/SYMTAB/RELA/RELASZ/RELAENT/STRSZ/SYMENT/NULL
    rela_dyn_off_in_rest = _align(interp_off_in_rest + len(interp_str), 8)
    dynamic_off_in_rest = rela_dyn_off_in_rest + n_rela * 24
    rest_total_len = dynamic_off_in_rest + n_dyn_entries * 16
    vaddr_bss = vaddr_rest + _align(rest_total_len, PAGE_SIZE)

    # .rela.dyn: one GLOB_DAT per function import (into its GOT slot),
    # one COPY per data import (into its .bss slot).
    rela_dyn = bytearray()
    for i, nm in enumerate(func_imports):
        got_slot_vaddr = vaddr_rest + got_off_in_rest + i * 8
        r_info = (dynsym_idx[nm] << 32) | R_X86_64_GLOB_DAT
        rela_dyn += struct.pack("<QQq", got_slot_vaddr, r_info, 0)
    for nm in data_imports:
        slot_vaddr = resolve(nm)
        r_info = (dynsym_idx[nm] << 32) | R_X86_64_COPY
        rela_dyn += struct.pack("<QQq", slot_vaddr, r_info, 0)
    rela_dyn_bytes = bytes(rela_dyn)
    assert len(rela_dyn_bytes) == n_rela * 24

    def _dyn(tag: int, val: int) -> bytes:
        return struct.pack("<qQ", tag, val)

    dynamic = bytearray()
    for so in needed_sos:
        dynamic += _dyn(DT_NEEDED, dynstr_off[so])
    dynamic += _dyn(DT_HASH, vaddr_rest + hash_off_in_rest)
    dynamic += _dyn(DT_STRTAB, vaddr_rest + dynstr_off_in_rest)
    dynamic += _dyn(DT_SYMTAB, vaddr_rest + dynsym_off_in_rest)
    dynamic += _dyn(DT_RELA, vaddr_rest + rela_dyn_off_in_rest)
    dynamic += _dyn(DT_RELASZ, len(rela_dyn_bytes))
    dynamic += _dyn(DT_RELAENT, 24)
    dynamic += _dyn(DT_STRSZ, len(dynstr_bytes))
    dynamic += _dyn(DT_SYMENT, 24)
    dynamic += _dyn(DT_NULL, 0)
    dynamic_bytes = bytes(dynamic)
    assert len(dynamic_bytes) == n_dyn_entries * 16

    # ── 7. Finalize .text: thunks (now that GOT slot addresses are
    # known), entry stub, then patch every relocation. ──
    text = bucket_bytes["text"]
    text.extend(b"\x90" * (text_total_len - len(text)))
    for i, name in enumerate(func_imports):
        pos = thunk_off[name]
        got_slot_vaddr = vaddr_rest + got_off_in_rest + i * 8
        disp = got_slot_vaddr - (vaddr_text + pos + 6)
        text[pos:pos + 6] = bytes([0xFF, 0x25]) + struct.pack("<i", disp)

    init_addr = resolve("__asmpy_module_init") if has_module_init else 0
    main_addr = resolve(entry_symbol)
    stub = bytearray()
    stub += bytes([0x48, 0x83, 0xE4, 0xF0])     # and rsp, -16
    if has_module_init:
        call_init_disp_pos = len(stub) + 1
        stub += bytes([0xE8, 0, 0, 0, 0])       # call rel32 module init
    call_disp_pos = len(stub) + 1
    stub += bytes([0xE8, 0, 0, 0, 0])           # call rel32 main
    stub += bytes([0x89, 0xC7])                 # mov edi, eax
    call2_disp_pos = len(stub) + 1
    stub += bytes([0xE8, 0, 0, 0, 0])           # call rel32 exit thunk
    stub += bytes([0x0F, 0x0B])                 # ud2 (unreachable -- exit() never returns)
    assert len(stub) == entry_stub_len
    stub_addr = vaddr_text + entry_stub_off
    if has_module_init:
        struct.pack_into("<i", stub, call_init_disp_pos, init_addr - (stub_addr + call_init_disp_pos + 4))
    struct.pack_into("<i", stub, call_disp_pos, main_addr - (stub_addr + call_disp_pos + 4))
    exit_thunk_addr = vaddr_text + thunk_off["exit"]
    struct.pack_into("<i", stub, call2_disp_pos, exit_thunk_addr - (stub_addr + call2_disp_pos + 4))
    text[entry_stub_off:entry_stub_off + entry_stub_len] = bytes(stub)

    for oi, obj in enumerate(parsed):
        for sect in obj.sections:
            if _BUCKET_FOR_SECTION.get(sect.name) != "text":
                continue
            base = sect_base[(oi, sect.name)]
            for r in sect.relocs:
                if r.rtype not in (R_X86_64_PC32, R_X86_64_PLT32):
                    raise LinkError(f"unsupported relocation type {r.rtype} for {r.symbol!r}")
                patch_off = base + r.offset
                patch_addr = vaddr_text + patch_off
                target_addr = (
                    resolve_section(oi, r.section_idx)
                    if r.section_idx is not None
                    else resolve(r.symbol)
                )
                # ELF carries the addend explicitly (-4 for PC32/PLT32,
                # matching "relative to the end of the 4-byte field");
                # PE's REL32 has the same -4 baked in implicitly instead.
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
    rest[rela_dyn_off_in_rest:rela_dyn_off_in_rest + len(rela_dyn_bytes)] = rela_dyn_bytes
    rest[dynamic_off_in_rest:dynamic_off_in_rest + len(dynamic_bytes)] = dynamic_bytes
    # GOT slots start zeroed -- the dynamic linker fills them via .rela.dyn
    # before _start runs, so the bytes here never actually get read.

    return _build_elf_image(
        text_bytes=bytes(text),
        rest_bytes=bytes(rest),
        vaddr_text=vaddr_text,
        vaddr_rest=vaddr_rest,
        vaddr_bss=vaddr_bss,
        bss_size=bss_size,
        entry_vaddr=stub_addr,
        interp_vaddr=vaddr_rest + interp_off_in_rest,
        interp_len=len(interp_str),
        dynamic_vaddr=vaddr_rest + dynamic_off_in_rest,
        dynamic_len=len(dynamic_bytes),
    )


def _build_elf_image(
    *, text_bytes: bytes, rest_bytes: bytes,
    vaddr_text: int, vaddr_rest: int, vaddr_bss: int, bss_size: int,
    entry_vaddr: int, interp_vaddr: int, interp_len: int,
    dynamic_vaddr: int, dynamic_len: int,
) -> bytes:
    EHDR_SIZE = 64
    PHDR_SIZE = 56
    num_phdrs = 5  # PT_LOAD(text), PT_LOAD(rest), PT_LOAD(bss), PT_INTERP, PT_DYNAMIC

    phoff = EHDR_SIZE
    off_text = PAGE_SIZE
    off_rest = off_text + _align(len(text_bytes), PAGE_SIZE)

    phdrs = bytearray()

    def phdr(p_type, p_flags, offset, vaddr, filesz, memsz, align):
        phdrs.extend(struct.pack(
            "<IIQQQQQQ", p_type, p_flags, offset, vaddr, vaddr, filesz, memsz, align
        ))

    # First LOAD segment covers the headers themselves too (offset 0),
    # mapped at vaddr_text - PAGE_SIZE (= IMAGE_BASE) so e_entry/phdrs and
    # .text share one mapping, like a normal ld-produced executable.
    phdr(PT_LOAD, PF_R | PF_X, 0, vaddr_text - PAGE_SIZE, off_text + len(text_bytes),
         off_text + len(text_bytes), PAGE_SIZE)
    phdr(PT_LOAD, PF_R | PF_W, off_rest, vaddr_rest, len(rest_bytes), len(rest_bytes), PAGE_SIZE)
    # filesz=0 (pure zero-fill), but p_offset must still satisfy
    # p_offset === p_vaddr (mod p_align) like every other PT_LOAD -- an
    # arbitrary unaligned offset here makes the kernel reject the whole
    # binary with ENOEXEC, even though no bytes are ever read from it.
    bss_offset = _align(off_rest + len(rest_bytes), PAGE_SIZE)
    phdr(PT_LOAD, PF_R | PF_W, bss_offset, vaddr_bss, 0, bss_size, PAGE_SIZE)
    phdr(PT_INTERP, PF_R, off_rest + (interp_vaddr - vaddr_rest), interp_vaddr,
         interp_len, interp_len, 1)
    phdr(PT_DYNAMIC, PF_R | PF_W, off_rest + (dynamic_vaddr - vaddr_rest), dynamic_vaddr,
         dynamic_len, dynamic_len, 8)

    ident = bytes([0x7F, 0x45, 0x4C, 0x46, 2, 1, 1, 0]) + bytes(8)
    ehdr = struct.pack(
        "<16sHHIQQQIHHHHHH",
        ident, 2, 62, 1,            # ET_EXEC, EM_X86_64, EV_CURRENT
        entry_vaddr, phoff, 0,       # e_entry, e_phoff, e_shoff (no section headers needed)
        0, EHDR_SIZE, PHDR_SIZE, num_phdrs, 0, 0, 0,
    )

    image = bytearray(off_rest + len(rest_bytes))
    image[0:EHDR_SIZE] = ehdr
    image[phoff:phoff + len(phdrs)] = phdrs
    image[off_text:off_text + len(text_bytes)] = text_bytes
    image[off_rest:off_rest + len(rest_bytes)] = rest_bytes
    return bytes(image)
