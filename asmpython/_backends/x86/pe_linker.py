"""Builtin PE (Windows x86, 32-bit) executable linker -- no gcc/link.exe
involved.

Adapted from the x86-64 backend's own pe_linker.py, with the same
mechanism (merge COFF objects, resolve symbols, patch REL32
relocations, build an import table, lay out a final PE image) but
several real, substantial differences, not just narrower field widths:

  - PE32, not PE32+: a genuinely different optional-header format, not
    just PE32+'s fields truncated. Magic=0x010B (not 0x020B),
    ImageBase/stack-reserve/heap-reserve are 4-byte fields (not 8-byte
    Q-packed ones), and there is no BaseOfData-omission quirk PE32+
    has (PE32's optional header actually HAS a BaseOfData field PE32+
    dropped -- see _build_pe_image's own comment on this).

  - Windows x86's OWN system-call convention (DllMain, and every real
    Win32 API function this linker calls into -- ExitProcess,
    GetProcAddress, etc.) is __stdcall (WINAPI), not cdecl -- confirmed
    directly against Microsoft's own documentation, not assumed:
    right-to-left argument order like cdecl, but the CALLEE cleans the
    stack, unlike cdecl where the caller does. This backend's own
    compiled functions (main, DllMain's module-init callee) still use
    plain cdecl internally (this backend's own established, separately-
    verified convention -- see codegen.py's own _call), so the entry
    stub sits at a real calling-convention boundary between the two.
    In practice this rarely matters for the SPECIFIC calls this stub
    makes: ExitProcess/GetProcAddress etc. are called with 0-2
    arguments and their call sites never execute any caller-side stack
    cleanup afterward anyway (ExitProcess never returns; the ordinary
    entry stub's own `ud2` sits right after it) -- stdcall and cdecl
    encode byte-identically at the CALL SITE itself for any function
    neither call site needs to clean up after (the only difference is
    which side owns cleanup, and this stub's own call sites never
    reach the point where that would matter).

  - No 64-bit registers exist in 32-bit mode at all, so the
    `__acrt_iob_func` synthetic stub (used by the x86-64 linker to
    adapt UCRT's index-based stdio-handle accessor onto msvcrt.dll's
    older __iob_func) is rebuilt entirely with 32-bit registers and a
    cdecl-style stack-based argument instead of Win64's ECX-register
    argument passing.

Every relocation/thunk mechanism (IAT slot + `jmp dword [iat_slot]`,
DLL import directory construction, PE export directory for library
builds) is structurally the same as x86-64's version -- PE's own
import/export table FORMATS are themselves already machine-independent
between PE32 and PE32+ (confirmed against Microsoft's own PE/COFF spec:
the import directory table, ILT/IAT entry format's *shape*, and the
export directory are unchanged; only the OPTIONAL HEADER differs
between the two image formats).
"""

from __future__ import annotations

import struct

from .coff_parse import parse_coff, CoffObject

IMAGE_BASE     = 0x00400000   # conventional PE32 (32-bit) image base, vs PE32+'s 0x140000000
SECTION_ALIGN  = 0x1000
FILE_ALIGN     = 0x200

IMAGE_REL_I386_REL32 = 0x0014

_BUCKET_FOR_SECTION = {
    ".text": "text",
    ".data": "data",
    ".rdata": "rdata",
    ".rodata": "rdata",
    ".bss": "bss",
}

# symbol name -> DLL providing it. Same inventory/rationale as the
# x86-64 linker's own table -- msvcrt.dll's own exports are the same
# regardless of whether the CALLING code is 32-bit or 64-bit (msvcrt.dll
# itself ships both a 32-bit and 64-bit build with the same export
# names for the C-runtime-level functions listed here).
_DLL_FOR_SYMBOL: dict[str, str] = {}
for _name in (
    "malloc", "realloc", "free", "calloc",
    "printf", "sprintf", "putchar", "puts", "fputs", "fputc",
    "strlen", "strcmp", "strstr", "_strdup", "_atoi64",
    "atof", "strtod", "fgets", "fopen", "fgetc", "fclose", "fflush",
    "fread", "fseek", "ftell", "_popen", "_pclose",
    "exit", "__iob_func", "memset", "memcpy", "fmod", "pow",
    "fabs", "frexp", "ldexp", "log", "modf", "rand", "sqrt",
    "abs", "labs", "floor", "ceil", "difftime",
    "cos", "sin", "tan", "asin", "acos", "atan", "atan2",
    "sinh", "cosh", "tanh", "exp",
    "srand", "getenv", "clock", "remove", "_stat64", "_getpid",
    "time", "gmtime", "localtime", "mktime",
    "_mkdir", "_rmdir", "_chdir", "_getcwd", "_access",
    "system",
):
    _DLL_FOR_SYMBOL[_name] = "msvcrt.dll"

_SYMBOL_ALIASES: dict[str, str] = {
    "access": "_access",
    "strtoll": "_strtoi64",
    "copysign": "_copysign",
    "hypot": "_hypot",
}
for _ref, _real in _SYMBOL_ALIASES.items():
    _DLL_FOR_SYMBOL[_real] = "msvcrt.dll"
_ACRT_IOB_FUNC_STUB_SYM = "__acrt_iob_func"
_FILE_STRUCT_SIZE = 32  # sizeof(FILE) in this msvcrt.dll ABI (stable, well-known constant)
for _name in (
    "LoadLibraryA", "GetProcAddress", "ExitProcess",
    "GetSystemTimeAsFileTime", "QueryPerformanceCounter",
    "QueryPerformanceFrequency", "Sleep", "CloseHandle", "CreateThread",
    "GetCurrentThreadId", "GetExitCodeThread", "WaitForSingleObject",
    "InitializeCriticalSection", "EnterCriticalSection",
    "LeaveCriticalSection", "DeleteCriticalSection",
):
    _DLL_FOR_SYMBOL[_name] = "kernel32.dll"
for _name in (
    "socket", "bind", "connect", "listen", "accept", "closesocket",
    "send", "recv", "htons", "inet_addr", "gethostname",
    "WSAGetLastError", "getsockopt", "setsockopt",
):
    _DLL_FOR_SYMBOL[_name] = "ws2_32.dll"


def _align(n: int, a: int) -> int:
    return (n + a - 1) & ~(a - 1)


class LinkError(Exception):
    pass


def link_pe(
    objects: list[bytes],
    entry_symbol: str = "main",
    *,
    is_library: bool = False,
    exports: "list[str] | tuple[str, ...]" = (),
) -> bytes:
    """Link into a PE32 executable, or (`is_library=True`) a DLL. See
    the x86-64 linker's own docstring for the full is_library/exports
    semantics -- identical here."""
    parsed: list[CoffObject] = [parse_coff(o) for o in objects]

    # ── 1. Merge sections. ──
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
                continue
            buf = bucket_bytes[bucket]
            pad = (-len(buf)) & (bucket_align[bucket] - 1)
            buf.extend(b"\x90" * pad if bucket == "text" else b"\x00" * pad)
            sect_base[(oi, sect.name)] = len(buf)
            buf.extend(sect.data)

    for oi, obj in enumerate(parsed):
        for sym in obj.symbols:
            if not sym.name or sym.section_number <= 0:
                continue
            sect = obj.sections[sym.section_number - 1]
            if _BUCKET_FOR_SECTION.get(sect.name) != "bss":
                continue
            base = sect_base[(oi, sect.name)]
            bss_size = max(bss_size, base + sym.value + 4)

    # ── 2. Global symbol resolution. ──
    global_syms: dict[str, tuple[str, int]] = {}
    for oi, obj in enumerate(parsed):
        for sym in obj.symbols:
            if not sym.name or sym.section_number <= 0:
                continue
            if sym.name.startswith("."):
                continue
            sect = obj.sections[sym.section_number - 1]
            bucket = _BUCKET_FOR_SECTION.get(sect.name)
            if bucket is None:
                continue
            base = sect_base[(oi, sect.name)]
            if sym.name in global_syms:
                raise LinkError(
                    f"duplicate external symbol {sym.name!r} defined in "
                    f"more than one merged object -- likely a name "
                    f"collision between unrelated definitions rather "
                    f"than a genuine intentional redefinition"
                )
            global_syms[sym.name] = (bucket, base + sym.value)

    if not is_library and entry_symbol not in global_syms:
        raise LinkError(f"entry symbol {entry_symbol!r} not defined in any input object")
    for export_name in exports:
        if export_name not in global_syms:
            raise LinkError(f"export symbol {export_name!r} not defined in any input object")

    # ── 3. Collect DLL imports. ──
    imports: list[str] = []
    seen_imports: set[str] = set()
    needs_acrt_iob_stub = False

    def _want_import(name: str) -> None:
        if name in global_syms or name in seen_imports:
            return
        if name not in _DLL_FOR_SYMBOL:
            raise LinkError(
                f"undefined symbol {name!r} has no known DLL "
                f"(add it to pe_linker._DLL_FOR_SYMBOL if it's a real import)"
            )
        imports.append(name)
        seen_imports.add(name)

    for obj in parsed:
        for sect in obj.sections:
            for r in sect.relocs:
                if r.symbol == _ACRT_IOB_FUNC_STUB_SYM:
                    needs_acrt_iob_stub = True
                    _want_import("__iob_func")
                    continue
                if r.symbol in _BUCKET_FOR_SECTION:
                    continue
                if r.symbol in global_syms or r.symbol in seen_imports:
                    continue
                _want_import(_SYMBOL_ALIASES.get(r.symbol, r.symbol))
    if not is_library and "ExitProcess" not in global_syms:
        _want_import("ExitProcess")
    imports.sort(key=lambda n: (_DLL_FOR_SYMBOL[n], n))

    # ── 4. Lay out .text: merged code, 6-byte thunks (`jmp dword
    # [iat_slot]` -- absolute, no RIP-relative addressing exists here),
    # the __acrt_iob_func stub, then the entry stub. ──
    text_body_len = len(bucket_bytes["text"])
    thunk_off = {name: text_body_len + i * 6 for i, name in enumerate(imports)}
    thunk_region_len = 6 * len(imports)
    acrt_iob_stub_off = text_body_len + thunk_region_len
    # cdecl __acrt_iob_func(int index) -> FILE* :
    #   push ebp              (1)
    #   mov ebp, esp          (2)
    #   call [__iob_func]     (5)  -- msvcrt.dll's __iob_func takes NO
    #                                 arguments and returns the base
    #                                 FILE* array (confirmed via the
    #                                 x86-64 linker's own comment on
    #                                 this exact function's real
    #                                 signature)
    #   mov ecx, [ebp+8]      (3)  -- ecx = index (cdecl's own single
    #                                 stack argument)
    #   shl ecx, 5            (3)  -- *sizeof(FILE) == 32
    #   add eax, ecx          (2)
    #   pop ebp               (1)
    #   ret                   (1)
    acrt_iob_stub_len = 1 + 2 + 5 + 3 + 3 + 2 + 1 + 1  # == 18
    entry_stub_off = acrt_iob_stub_off + (acrt_iob_stub_len if needs_acrt_iob_stub else 0)
    has_module_init = "__asmpy_module_init" in global_syms
    library_init_symbol = None
    if is_library:
        if has_module_init:
            library_init_symbol = "__asmpy_module_init"
        elif "main" in global_syms:
            library_init_symbol = "main"
        has_module_init = library_init_symbol is not None
        # DllMain(hinst, reason, reserved) is __stdcall -- but since this
        # entry stub's own `ret` is what pops those 12 bytes of arguments
        # (stdcall callee-cleanup), it must be `ret 12`, not a bare
        # `ret` (cdecl's own zero-cleanup form) -- a real, easy-to-miss
        # difference from the x86-64 version's own DllMain stub, which
        # needed no stack cleanup at all since Win64 passes every
        # argument in registers, never on the stack.
        # cmp dword [esp+8], 1 ; jne +N ; [call rel32(module_init)] ; mov eax,1 ; ret 12
        entry_stub_len = 4 + 2 + (5 if has_module_init else 0) + 5 + 3
    else:
        # cdecl entry stub, matching this backend's own established
        # convention (elf_linker.py's identical stub, see that file's
        # own comment on why no 16-byte stack-alignment step is needed
        # here the way x86-64 SysV's own stub required):
        # [call rel32(module_init)] ; call rel32(main) ; push eax ;
        # call rel32(ExitProcess thunk) ; ud2
        entry_stub_len = (5 if has_module_init else 0) + 5 + 1 + 5 + 2
    text_total_len = entry_stub_off + entry_stub_len

    # ── 5. Decide PE section RVAs. ──
    rva_text  = SECTION_ALIGN
    rva_data  = rva_text + _align(text_total_len, SECTION_ALIGN)
    data_rdata_len = len(bucket_bytes["rdata"]) + len(bucket_bytes["data"])
    rva_idata = rva_data + _align(data_rdata_len, SECTION_ALIGN) if data_rdata_len else rva_data
    rdata_base_in_section = 0
    data_base_in_section = len(bucket_bytes["rdata"])

    def resolve(name: str) -> int:
        if name in global_syms:
            bucket, off = global_syms[name]
            if bucket == "text":
                return IMAGE_BASE + rva_text + off
            if bucket == "rdata":
                return IMAGE_BASE + rva_data + rdata_base_in_section + off
            if bucket == "data":
                return IMAGE_BASE + rva_data + data_base_in_section + off
            if bucket == "bss":
                return IMAGE_BASE + rva_bss + off
        name = _SYMBOL_ALIASES.get(name, name)
        if name in thunk_off:
            return IMAGE_BASE + rva_text + thunk_off[name]
        if name == _ACRT_IOB_FUNC_STUB_SYM and needs_acrt_iob_stub:
            return IMAGE_BASE + rva_text + acrt_iob_stub_off
        raise LinkError(f"unresolved symbol {name!r}")

    def resolve_local_section(oi: int, name: str) -> int:
        bucket = _BUCKET_FOR_SECTION.get(name)
        if bucket is None:
            raise LinkError(f"unhandled local section relocation target {name!r}")
        key = (oi, name)
        if key not in sect_base:
            raise LinkError(f"object {oi} has no section {name!r} for local relocation")
        off = sect_base[key]
        if bucket == "text":
            return IMAGE_BASE + rva_text + off
        if bucket == "rdata":
            return IMAGE_BASE + rva_data + rdata_base_in_section + off
        if bucket == "data":
            return IMAGE_BASE + rva_data + data_base_in_section + off
        if bucket == "bss":
            return IMAGE_BASE + rva_bss + off
        raise LinkError(f"unknown section bucket {bucket!r} for {name!r}")

    # ── 6. Build .idata. ──
    dlls: dict[str, list[str]] = {}
    for name in imports:
        dlls.setdefault(_DLL_FOR_SYMBOL[name], []).append(name)

    num_dlls = len(dlls)
    dir_table_len = (num_dlls + 1) * 20
    off = dir_table_len
    ilt_off: dict[str, int] = {}
    iat_off: dict[str, int] = {}
    for dll, names in dlls.items():
        ilt_off[dll] = off
        off += (len(names) + 1) * 4   # PE32: 4-byte ILT/IAT entries, not PE32+'s 8-byte ones
    for dll, names in dlls.items():
        iat_off[dll] = off
        off += (len(names) + 1) * 4
    hintname_off: dict[str, int] = {}
    for name in imports:
        if off % 2:
            off += 1
        hintname_off[name] = off
        off += 2 + len(name) + 1
    dllname_off: dict[str, int] = {}
    for dll in dlls:
        dllname_off[dll] = off
        off += len(dll) + 1
    idata_len = off

    idata = bytearray(idata_len)

    def w32(at: int, v: int) -> None:
        struct.pack_into("<I", idata, at, v & 0xFFFFFFFF)

    dir_i = 0
    for dll, names in dlls.items():
        entry = dir_i * 20
        w32(entry + 0, rva_idata + ilt_off[dll])
        w32(entry + 12, rva_idata + dllname_off[dll])
        w32(entry + 16, rva_idata + iat_off[dll])
        for j, name in enumerate(names):
            hn_rva = rva_idata + hintname_off[name]
            # PE32's ILT/IAT entry is a plain 32-bit RVA to the Hint/Name
            # entry (bit 31 clear means "import by name", the only form
            # this linker ever emits) -- PE32+'s equivalent is a 64-bit
            # field with the SAME low-31-bit RVA convention, just twice
            # the field width.
            w32(ilt_off[dll] + j * 4, hn_rva)
            w32(iat_off[dll] + j * 4, hn_rva)
        dir_i += 1
    for name in imports:
        at = hintname_off[name]
        struct.pack_into("<H", idata, at, 0)
        idata[at + 2: at + 2 + len(name)] = name.encode("ascii")
        idata[at + 2 + len(name)] = 0
    for dll, doff in dllname_off.items():
        idata[doff: doff + len(dll)] = dll.encode("ascii")
        idata[doff + len(dll)] = 0

    # ── 7. .bss placed after .idata. ──
    rva_bss = rva_idata + _align(idata_len, SECTION_ALIGN)

    def iat_slot_rva(name: str) -> int:
        dll = _DLL_FOR_SYMBOL[name]
        j = dlls[dll].index(name)
        return rva_idata + iat_off[dll] + j * 4

    # ── 8. Finalize .text: thunks, stub(s), then patch relocations. ──
    text = bucket_bytes["text"]
    text.extend(b"\x90" * (text_total_len - len(text)))
    for name in imports:
        pos = thunk_off[name]
        # jmp dword [abs_iat_slot] -- absolute, not RIP-relative (see
        # this module's own docstring, and elf_linker.py's identical
        # reasoning for its own GOT thunk).
        iat_addr = IMAGE_BASE + iat_slot_rva(name)
        text[pos:pos + 6] = bytes([0xFF, 0x25]) + struct.pack("<I", iat_addr)

    if needs_acrt_iob_stub:
        # cdecl __acrt_iob_func(int index) -> FILE* :
        #   push ebp
        #   mov ebp, esp
        #   call [__iob_func thunk]      ; eax = base FILE* array (msvcrt's __iob_func takes no args)
        #   mov ecx, [ebp+8]             ; ecx = index
        #   shl ecx, 5                   ; *sizeof(FILE) == 32
        #   add eax, ecx
        #   pop ebp
        #   ret
        iob = bytearray()
        iob += bytes([0x55])                              # push ebp
        iob += bytes([0x89, 0xE5])                         # mov ebp, esp
        call_disp_pos = len(iob) + 1
        iob += bytes([0xE8, 0, 0, 0, 0])                   # call rel32 __iob_func thunk
        iob += bytes([0x8B, 0x4D, 0x08])                   # mov ecx, [ebp+8]
        iob += bytes([0xC1, 0xE1, 0x05])                   # shl ecx, 5
        iob += bytes([0x01, 0xC8])                         # add eax, ecx
        iob += bytes([0x5D])                               # pop ebp
        iob += bytes([0xC3])                               # ret
        assert len(iob) == acrt_iob_stub_len, (len(iob), acrt_iob_stub_len)
        stub_addr = IMAGE_BASE + rva_text + acrt_iob_stub_off
        call_patch_addr = stub_addr + call_disp_pos
        struct.pack_into(
            "<i", iob, call_disp_pos,
            (IMAGE_BASE + rva_text + thunk_off["__iob_func"]) - (call_patch_addr + 4),
        )
        text[acrt_iob_stub_off:acrt_iob_stub_off + acrt_iob_stub_len] = bytes(iob)

    if is_library:
        init_addr = resolve(library_init_symbol) if has_module_init else 0
    else:
        init_addr = resolve("__asmpy_module_init") if has_module_init else 0
    stub_base_addr = IMAGE_BASE + rva_text + entry_stub_off
    if is_library:
        # DllMain(hinst, reason, reserved) -- __stdcall, so THIS stub
        # (acting as DllMain itself) must clean up its own 12 bytes of
        # arguments via `ret 12`, not a bare `ret`. `reason` is the
        # SECOND stdcall argument -- pushed second-to-last (stdcall is
        # right-to-left just like cdecl), landing at [esp+8] relative
        # to THIS function's own entry (before any push/mov ebp,esp
        # prologue -- there is none here, this stub reads directly off
        # ESP since it never needs a frame of its own).
        stub = bytearray()
        stub += bytes([0x83, 0x7C, 0x24, 0x08, 0x01])       # cmp dword [esp+8], 1
        skip_disp_pos = len(stub) + 1
        skip_len = (5 if has_module_init else 0)
        stub += bytes([0x75, skip_len & 0xFF])               # jne +skip_len
        if has_module_init:
            call_init_disp_pos = len(stub) + 1
            stub += bytes([0xE8, 0, 0, 0, 0])                # call rel32 module init
        stub += bytes([0xB8, 1, 0, 0, 0])                    # mov eax, 1
        stub += bytes([0xC2, 0x0C, 0x00])                    # ret 12  (stdcall callee cleanup)
        assert len(stub) == entry_stub_len
        if has_module_init:
            struct.pack_into(
                "<i", stub, call_init_disp_pos,
                init_addr - (stub_base_addr + call_init_disp_pos + 4),
            )
    else:
        main_addr = resolve(entry_symbol)
        stub = bytearray()
        if has_module_init:
            call_init_disp_pos = len(stub) + 1
            stub += bytes([0xE8, 0, 0, 0, 0])                # call rel32 module init
        call1_disp_pos = len(stub) + 1
        stub += bytes([0xE8, 0, 0, 0, 0])                    # call rel32 main
        stub += bytes([0x50])                                # push eax (cdecl ExitProcess arg)
        call2_disp_pos = len(stub) + 1
        stub += bytes([0xE8, 0, 0, 0, 0])                    # call rel32 ExitProcess thunk
        stub += bytes([0x0F, 0x0B])                          # ud2 (unreachable)
        assert len(stub) == entry_stub_len
        if has_module_init:
            struct.pack_into(
                "<i", stub, call_init_disp_pos,
                init_addr - (stub_base_addr + call_init_disp_pos + 4),
            )
        struct.pack_into("<i", stub, call1_disp_pos, main_addr - (stub_base_addr + call1_disp_pos + 4))
        exit_addr = resolve("ExitProcess")
        struct.pack_into(
            "<i", stub, call2_disp_pos,
            exit_addr - (stub_base_addr + call2_disp_pos + 4),
        )
    text[entry_stub_off:entry_stub_off + entry_stub_len] = bytes(stub)

    for oi, obj in enumerate(parsed):
        for sect in obj.sections:
            if _BUCKET_FOR_SECTION.get(sect.name) != "text":
                continue
            base = sect_base[(oi, sect.name)]
            for r in sect.relocs:
                if r.rtype != IMAGE_REL_I386_REL32:
                    raise LinkError(f"unsupported relocation type {r.rtype} for {r.symbol!r}")
                patch_off = base + r.offset
                patch_addr = IMAGE_BASE + rva_text + patch_off
                addend = struct.unpack_from("<i", text, patch_off)[0]
                if r.symbol in _BUCKET_FOR_SECTION:
                    target_addr = resolve_local_section(oi, r.symbol)
                else:
                    target_addr = resolve(r.symbol)
                rel = (target_addr + addend) - (patch_addr + 4)
                struct.pack_into("<i", text, patch_off, rel)

    # ── 9. Assemble the PE file. ──
    rdata_data_blob = bytes(bucket_bytes["rdata"]) + bytes(bucket_bytes["data"])
    entry_rva = rva_text + entry_stub_off

    rva_edata = rva_bss + _align(bss_size, SECTION_ALIGN) if bss_size else rva_idata + _align(idata_len, SECTION_ALIGN)
    edata_bytes = b""
    export_directory_rva_size = (0, 0)
    if is_library and exports:
        sorted_exports = sorted(exports)
        export_addrs = [resolve(name) - IMAGE_BASE for name in sorted_exports]
        edata_bytes = _build_pe_export_directory(rva_edata, sorted_exports, export_addrs)
        export_directory_rva_size = (rva_edata, len(edata_bytes))

    sections = [
        (".text", rva_text, bytes(text),
         0x00000020 | 0x20000000 | 0x40000000),
        (".rdata", rva_data, rdata_data_blob,
         0x00000040 | 0x40000000 | 0x80000000) if rdata_data_blob else None,
        (".idata", rva_idata, bytes(idata),
         0x00000040 | 0x40000000),
        (".bss", rva_bss, b"",
         0x00000080 | 0x40000000 | 0x80000000) if bss_size else None,
        (".edata", rva_edata, edata_bytes,
         0x00000040 | 0x40000000) if edata_bytes else None,
    ]
    sections = [s for s in sections if s is not None]

    return _build_pe_image(
        sections,
        entry_rva,
        bss_size if bss_size else 0,
        is_dll=is_library,
        export_directory=export_directory_rva_size,
    )


def _build_pe_export_directory(
    base_rva: int, sorted_names: list[str], addrs: list[int]
) -> bytes:
    """Identical format to the x86-64 linker's own export directory --
    the PE export table format itself is unchanged between PE32/PE32+
    (confirmed against Microsoft's own spec: only the optional header
    differs between the two image formats, not the export/import
    directory formats)."""
    n = len(sorted_names)
    DIR_SIZE = 40
    eat_off = DIR_SIZE
    eat_len = 4 * n
    names_off = eat_off + eat_len
    names_len = 4 * n
    ordinals_off = names_off + names_len
    ordinals_len = 2 * n
    strings_off = ordinals_off + ordinals_len

    dll_name = b"portapy32.dll\x00"
    dll_name_off = strings_off
    name_str_off: list[int] = []
    off = dll_name_off + len(dll_name)
    for name in sorted_names:
        name_str_off.append(off)
        off += len(name) + 1
    total_len = off
    encoded_names = [name.encode("ascii") + b"\x00" for name in sorted_names]

    buf = bytearray(total_len)
    buf[dll_name_off:dll_name_off + len(dll_name)] = dll_name
    for i, encoded in enumerate(encoded_names):
        at = name_str_off[i]
        buf[at:at + len(encoded)] = encoded

    for i, addr in enumerate(addrs):
        struct.pack_into("<I", buf, eat_off + i * 4, addr)
    for i in range(n):
        struct.pack_into("<I", buf, names_off + i * 4, base_rva + name_str_off[i])
    for i in range(n):
        struct.pack_into("<H", buf, ordinals_off + i * 2, i)

    struct.pack_into(
        "<IIHHIIIIIII",
        buf, 0,
        0, 0, 0, 0,
        base_rva + dll_name_off,
        1,
        n, n,
        base_rva + eat_off,
        base_rva + names_off,
        base_rva + ordinals_off,
    )
    return bytes(buf)


def _build_pe_image(
    sections: list[tuple[str, int, bytes, int]],
    entry_rva: int,
    bss_size: int,
    *,
    is_dll: bool = False,
    export_directory: "tuple[int, int]" = (0, 0),
) -> bytes:
    num_sects = len(sections)

    dos_stub = bytearray(64)
    dos_stub[0:2] = b"MZ"
    struct.pack_into("<I", dos_stub, 0x3C, 64)

    # PE32's optional header is 96 bytes of fixed fields + 16 data
    # directories (8 bytes each) = 96 + 128 = 224 -- NOT PE32+'s
    # 112 + 128 = 240. The difference is exactly one field: PE32 HAS a
    # BaseOfData field (right after BaseOfCode) that PE32+ genuinely
    # omits entirely (not just widened) -- PE32+ folds ImageBase to 8
    # bytes and drops BaseOfData outright rather than widening it,
    # since a 64-bit ImageBase already made the header wider than
    # comfortable. Confirmed against Microsoft's own IMAGE_OPTIONAL_
    # HEADER32 vs IMAGE_OPTIONAL_HEADER64 struct definitions.
    opt_hdr_size = 96 + 16 * 8
    coff_hdr_off = 64
    headers_size = coff_hdr_off + 4 + 20 + opt_hdr_size + num_sects * 40
    headers_size_aligned = _align(headers_size, FILE_ALIGN)

    file_off = headers_size_aligned
    layout = []
    for name, rva, data, chars in sections:
        raw_size = _align(len(data), FILE_ALIGN) if data else 0
        layout.append((name, rva, data, chars, file_off, raw_size))
        file_off += raw_size

    size_of_image = _align(
        max((rva + _align(max(len(d), 1), SECTION_ALIGN) for _n, rva, d, *_ in layout), default=SECTION_ALIGN)
        if not bss_size else
        max(rva + _align(max(len(d), 1) if d else bss_size, SECTION_ALIGN) for _n, rva, d, *_ in layout),
        SECTION_ALIGN,
    )

    idata_entry = next(((rva, len(d)) for n, rva, d, *_ in layout if n == ".idata"), (0, 0))

    characteristics = 0x0002 | 0x0100 | 0x0200  # EXECUTABLE_IMAGE | 32BIT_MACHINE | DEBUG_STRIPPED
    if is_dll:
        characteristics |= 0x2000  # IMAGE_FILE_DLL
    coff_hdr = struct.pack(
        "<HHIIIHH",
        0x014C,            # IMAGE_FILE_MACHINE_I386
        num_sects,
        0,
        0, 0,
        opt_hdr_size,
        characteristics,
    )

    opt_hdr = bytearray()
    opt_hdr += struct.pack("<HBB", 0x010B, 0, 0)          # Magic=PE32 (not PE32+'s 0x020B)
    opt_hdr += struct.pack("<III", 0, 0, 0)               # SizeOfCode/Init/Uninit
    opt_hdr += struct.pack("<I", entry_rva)
    opt_hdr += struct.pack("<I", sections[0][1])          # BaseOfCode
    opt_hdr += struct.pack("<I", sections[1][1] if len(sections) > 1 else sections[0][1])  # BaseOfData (PE32-only field)
    opt_hdr += struct.pack("<I", IMAGE_BASE)               # 4-byte ImageBase, not PE32+'s 8-byte Q
    opt_hdr += struct.pack("<II", SECTION_ALIGN, FILE_ALIGN)
    opt_hdr += struct.pack("<HHHHHH", 6, 0, 0, 0, 6, 0)
    opt_hdr += struct.pack("<I", 0)
    opt_hdr += struct.pack("<II", size_of_image, headers_size_aligned)
    opt_hdr += struct.pack("<I", 0)
    opt_hdr += struct.pack("<HH", 3, 0x0100)               # Subsystem=CONSOLE, DllCharacteristics=NX_COMPAT
    # PE32's stack/heap reserve+commit fields are 4-byte (I), not
    # PE32+'s 8-byte (Q) ones.
    opt_hdr += struct.pack("<IIII", 0x100000, 0x1000, 0x100000, 0x1000)
    opt_hdr += struct.pack("<I", 0)
    opt_hdr += struct.pack("<I", 16)
    for i in range(16):
        if i == 0:
            opt_hdr += struct.pack("<II", export_directory[0], export_directory[1])
        elif i == 1:
            opt_hdr += struct.pack("<II", idata_entry[0], idata_entry[1])
        else:
            opt_hdr += struct.pack("<II", 0, 0)
    assert len(opt_hdr) == opt_hdr_size, (len(opt_hdr), opt_hdr_size)

    sect_hdrs = bytearray()
    for name, rva, data, chars, foff, raw_size in layout:
        name_b = name.encode()[:8].ljust(8, b"\x00")
        sect_hdrs += struct.pack(
            "<8sIIIIIIHHI",
            name_b,
            _align(max(len(data), 1) if data else bss_size, SECTION_ALIGN),
            rva,
            raw_size,
            foff if raw_size else 0,
            0, 0, 0, 0,
            chars,
        )

    header_blob = bytes(dos_stub) + b"PE\x00\x00" + coff_hdr + bytes(opt_hdr) + bytes(sect_hdrs)
    header_blob = header_blob.ljust(headers_size_aligned, b"\x00")

    body = bytearray()
    for _name, _rva, data, _chars, foff, raw_size in layout:
        pad_before = foff - (headers_size_aligned + len(body))
        if pad_before > 0:
            body += b"\x00" * pad_before
        body += data
        body += b"\x00" * (raw_size - len(data))

    return header_blob + bytes(body)
