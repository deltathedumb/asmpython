"""Builtin PE (Windows x64) executable linker -- no gcc/link.exe involved.

Takes the relocatable COFF objects produced by this backend (the user
program, abi_shims.obj, and the runtime's single object), resolves every
symbol and relocation itself, builds the import table for the handful of
msvcrt.dll/kernel32.dll functions actually used, and emits a directly
loadable PE32+ executable.

Reached via driver.py's --linker builtin (windows + --backend x86-64
only for now). The mechanism this proves -- merge objects, resolve
symbols, patch REL32 relocations, build import thunks, lay out a final
container format -- is shared conceptually with the Linux ELF and macOS
Mach-O linkers planned as later stages; only the container format and
import mechanism (PE import table vs ELF PLT/GOT vs Mach-O lazy binding)
differ.
"""

from __future__ import annotations

import struct

from .coff_parse import parse_coff, CoffObject

IMAGE_BASE     = 0x140000000
SECTION_ALIGN  = 0x1000
FILE_ALIGN     = 0x200

IMAGE_REL_AMD64_REL32 = 4

# Real section names this backend / NASM produce, classified into the
# four buckets a PE executable actually needs (read-only data and NASM's
# ".rodata" vs coff.py's ".rdata" both collapse into "rdata").
_BUCKET_FOR_SECTION = {
    ".text": "text",
    ".data": "data",
    ".rdata": "rdata",
    ".rodata": "rdata",
    ".bss": "bss",
}

# symbol name -> DLL providing it. Covers every extern this backend's own
# output, abi_shims.asm, and the runtime archive currently reference (see
# target_windows.py's `extern` lines for the full inventory this was
# built from). Anything not listed here fails to link with a clear error
# rather than guessing.
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
    # Added triaging the x86-64 backend's full-corpus parity sweep
    # (2026-07-16) -- each confirmed a real msvcrt.dll export via
    # ctypes.WinDLL('msvcrt.dll') attribute lookup against the live
    # system DLL (dumpbin isn't installed in this environment; this is
    # an equivalent, tool-free way to confirm a symbol is real rather
    # than guessing).
    "cos", "sin", "tan", "asin", "acos", "atan", "atan2",
    "sinh", "cosh", "tanh", "exp",
    "srand", "getenv", "clock", "remove", "_stat64", "_getpid",
    "time", "gmtime", "localtime", "mktime",
    "_mkdir", "_rmdir", "_chdir", "_getcwd", "_access",
    # subprocess.system() (stdlib/os.py's `system` Func binding) --
    # confirmed a real msvcrt.dll export the same way as the rest of
    # this block (ctypes.WinDLL('msvcrt.dll') attribute lookup).
    "system",
):
    _DLL_FOR_SYMBOL[_name] = "msvcrt.dll"

# Some externs the legacy codegen calls under their POSIX/C99 name aren't
# actually exported by msvcrt.dll under that name -- it only exports the
# MS-specific equivalent (confirmed against the real system msvcrt.dll's
# export table, the same way __acrt_iob_func's mismatch was found).
# gcc/mingw papers over this via its own import library aliasing; this
# linker has no equivalent, so referenced-name -> real-export-name aliases
# are listed here instead. Both sides are ABI-compatible (same signature,
# just a different export name), unlike __acrt_iob_func which needed
# actual computation -- see _ACRT_IOB_FUNC_STUB_SYM for that one.
_SYMBOL_ALIASES: dict[str, str] = {
    "access": "_access",
    "strtoll": "_strtoi64",
    "copysign": "_copysign",
    "hypot": "_hypot",
}
for _ref, _real in _SYMBOL_ALIASES.items():
    _DLL_FOR_SYMBOL[_real] = "msvcrt.dll"
# __acrt_iob_func (the UCRT stdin/stdout/stderr-by-index accessor NASM
# emits a call to for input()/_runtime_input) is *not* an msvcrt.dll
# export -- it's UCRT-only, which is why gcc/mingw resolves it by linking
# in a small static thunk from its own runtime objects instead of
# importing it from any DLL (confirmed: a gcc-linked build's import table
# never lists it at all). Importing it from msvcrt.dll anyway, as if it
# were a normal extern, made the loader fail to resolve the whole image
# (every import is resolved before any code runs, so one bad entry breaks
# programs that never call _runtime_input too). Synthesized below as a
# tiny local stub instead of a DLL import -- see _ACRT_IOB_FUNC_STUB_SYM.
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
    symbol_libraries: "dict[str, str] | None" = None,
) -> bytes:
    """Link into a PE32+ executable, or (`is_library=True`) a DLL.

    A library build skips the `main`/`ExitProcess` process-entry model
    entirely: `entry_symbol` is only used to seed relocation resolution for
    an ordinary executable and is IGNORED when `is_library` is True (a DLL
    has no required process entry point at all -- see `_dll_entry_stub`).
    `exports` (each name must be a real merged global symbol, checked
    below) becomes the PE export directory (`.edata`), letting an external
    host `GetProcAddress` each one by name.

    `symbol_libraries` supplies EXTRA symbol -> DLL mappings for this link,
    built from the project's declared native libraries (see
    `_compiler.native_libraries`). It is consulted only where
    `_DLL_FOR_SYMBOL` misses, so the runtime's own imports can never be
    retargeted by a project file and a build that declares nothing links
    byte-for-byte as it did before this parameter existed.
    """
    parsed: list[CoffObject] = [parse_coff(o) for o in objects]
    extra_libraries = symbol_libraries or {}

    def dll_for(name: str) -> str:
        """The DLL providing `name`: builtin table first, declarations second."""
        dll = _DLL_FOR_SYMBOL.get(name)
        return dll if dll is not None else extra_libraries[name]

    # ── 1. Merge .text/.data/.rdata sections, track each object's section
    # base offset within its bucket so symbol values can be translated. ──
    bucket_bytes = {"text": bytearray(), "data": bytearray(), "rdata": bytearray()}
    bucket_align = {"text": 16, "data": 8, "rdata": 1}
    bss_size = 0
    # (obj_idx, real_section_name) -> base offset within its bucket (or bss)
    sect_base: dict[tuple[int, str], int] = {}

    for oi, obj in enumerate(parsed):
        for sect in obj.sections:
            bucket = _BUCKET_FOR_SECTION.get(sect.name)
            if bucket is None:
                continue
            if bucket == "bss":
                bss_size = _align(bss_size, 8)
                sect_base[(oi, sect.name)] = bss_size
                # .bss has no raw bytes; its symbols' `value` is an offset
                # within a zero-initialized region sized by VirtualSize,
                # which coff_parse doesn't currently expose -- the symbol
                # table's own values already give us everything needed to
                # size it correctly via the max symbol value below, so a
                # second pass over symbols (after this loop) extends
                # bss_size precisely instead of guessing a size here.
                continue
            buf = bucket_bytes[bucket]
            pad = (-len(buf)) & (bucket_align[bucket] - 1)
            buf.extend(b"\x90" * pad if bucket == "text" else b"\x00" * pad)
            sect_base[(oi, sect.name)] = len(buf)
            buf.extend(sect.data)

    # Size .bss precisely from the symbols actually placed in it (their
    # value is an offset *within* their own object's .bss; the largest
    # value + a conservative tail covers every reservation, since this
    # backend never emits per-symbol sizes, only offsets).
    for oi, obj in enumerate(parsed):
        for sym in obj.symbols:
            if not sym.name or sym.section_number <= 0:
                continue
            sect = obj.sections[sym.section_number - 1]
            if _BUCKET_FOR_SECTION.get(sect.name) != "bss":
                continue
            base = sect_base[(oi, sect.name)]
            bss_size = max(bss_size, base + sym.value + 8)

    # ── 2. Global symbol resolution: name -> ("text"|"data"|"rdata"|"bss", offset). ──
    global_syms: dict[str, tuple[str, int]] = {}
    for oi, obj in enumerate(parsed):
        for sym in obj.symbols:
            if not sym.name or sym.section_number <= 0:
                continue
            # NASM's own dot-prefixed local labels (`._mic_no` etc, which
            # every generated function reuses freely, by design, as
            # purely-internal jump targets, plus real section-name
            # symbols like `.rodata`/`.text`) must NOT participate in
            # cross-object duplicate-name detection below; only a real
            # duplicate top-level definition is an actual bug. Matched
            # by NAME (leading `.`), not IMAGE_SYM_CLASS_STATIC storage
            # class: several of this backend's own shim functions
            # (abi_shims.asm's math.*/random.* additions) are bare,
            # intentionally-callable-cross-object labels that were never
            # given an explicit `global` NASM directive -- they still
            # come out IMAGE_SYM_CLASS_STATIC despite being real,
            # intended external entry points (confirmed via a real
            # regression: filtering by storage class alone broke every
            # one of them). The proper fix is adding `global` directives
            # to those shim files; until that's done, name-based
            # filtering is what actually matches this codebase's real
            # authoring convention.
            if sym.name.startswith("."):
                continue
            sect = obj.sections[sym.section_number - 1]
            bucket = _BUCKET_FOR_SECTION.get(sect.name)
            if bucket is None:
                continue
            base = sect_base[(oi, sect.name)]
            if sym.name in global_syms:
                # Two merged objects both define the same external
                # symbol name -- e.g. a user global variable and an
                # unrelated whole-program-merged stdlib function
                # happening to share a bare name (confirmed real bug:
                # `log = logging.getLogger(...)` colliding with
                # logging.py's own top-level `def log(...)`, previously
                # silently resolved to whichever definition's symbol
                # happened to appear first in the merged table, corrupting
                # the OTHER one's every reference into a wrong address).
                # Fail loudly instead of guessing.
                raise LinkError(
                    f"duplicate external symbol {sym.name!r} defined in "
                    f"more than one merged object -- likely a name "
                    f"collision between unrelated definitions (a global "
                    f"variable and a function, or two merged modules) "
                    f"rather than a genuine intentional redefinition"
                )
            global_syms[sym.name] = (bucket, base + sym.value)

    if not is_library and entry_symbol not in global_syms:
        raise LinkError(f"entry symbol {entry_symbol!r} not defined in any input object")
    for export_name in exports:
        if export_name not in global_syms:
            raise LinkError(f"export symbol {export_name!r} not defined in any input object")

    # ── 3. Collect every relocation target not resolvable as a global
    # symbol -- those are real DLL imports. __acrt_iob_func is special-
    # cased: it's not actually an import (see _ACRT_IOB_FUNC_STUB_SYM's
    # comment), it needs __iob_func imported instead and a synthetic
    # local stub generated in step 4. ──
    imports: list[str] = []
    seen_imports: set[str] = set()
    needs_acrt_iob_stub = False

    def _want_import(name: str) -> None:
        if name in global_syms or name in seen_imports:
            return
        if name not in _DLL_FOR_SYMBOL and name not in extra_libraries:
            raise LinkError(
                f"undefined symbol {name!r} has no known DLL. Declare the "
                f"library providing it -- `native_libraries` in project.json, "
                f"or --link-library NAME=PATH -- or, if it is part of "
                f"asmpython's own runtime, add it to pe_linker._DLL_FOR_SYMBOL."
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
                    # A relocation against a SECTION symbol itself (e.g.
                    # NASM emitting `.rodata`/`.text` as the relocation
                    # target for a rip-relative load with no dedicated
                    # global label) -- resolved per-object via
                    # resolve_local_section in the real patching pass
                    # below (step 8), never a DLL import candidate.
                    # Excluded from global_syms's cross-object duplicate-
                    # collision check (every object legitimately has its
                    # own local `.rodata` etc; that's expected merging,
                    # not a name collision), so it must be excluded here
                    # too or this pre-scan wrongly treats it as an
                    # unresolved external needing a DLL.
                    continue
                if r.symbol in global_syms or r.symbol in seen_imports:
                    continue
                _want_import(_SYMBOL_ALIASES.get(r.symbol, r.symbol))
    # ExitProcess powers the executable entry stub even if nothing else
    # calls it. A DLL has no such stub -- its DllMain returns instead of
    # exiting the process -- so this import is only forced for an exe link.
    if not is_library:
        _want_import("ExitProcess")
    imports.sort(key=lambda n: (dll_for(n), n))

    # ── 4. Lay out .text: merged code, then one 6-byte thunk per import
    # (`jmp qword [rip+disp32]` into its IAT slot), then the __acrt_iob_func
    # stub (if needed), then the entry stub. ──
    text_body_len = len(bucket_bytes["text"])
    thunk_off = {name: text_body_len + i * 6 for i, name in enumerate(imports)}
    thunk_region_len = 6 * len(imports)
    acrt_iob_stub_off = text_body_len + thunk_region_len
    # sub rsp,40 ; mov r10d,ecx ; call rel32(__iob_func thunk) ;
    # shl r10,5 ; add rax,r10 ; add rsp,40 ; ret
    acrt_iob_stub_len = 4 + 3 + 5 + 4 + 3 + 4 + 1
    entry_stub_off = acrt_iob_stub_off + (acrt_iob_stub_len if needs_acrt_iob_stub else 0)
    has_module_init = "__asmpy_module_init" in global_syms
    library_init_symbol = None
    if is_library:
        # ir_lower.py's lower_module only synthesizes __asmpy_module_init
        # when the source has an explicit `def main():` -- a "script style"
        # program (no explicit main -- the common case for a library-only
        # source with no process entry point at all, e.g. a pure C-ABI
        # module) instead gets ALL of its module-level init code (global
        # list/dict construction, etc) folded into a function literally
        # named "main" (ir_lower.py's own "preserve the existing script
        # model" branch). An executable's entry stub always calls that
        # "main" as its real entry point either way, so this was never
        # visible there -- but a library has no such call at all, so
        # without this fallback every module-level global was silently
        # NEVER INITIALIZED (confirmed: a real segfault appending to an
        # uninitialized global list on the very first exported call,
        # porting PortaPy's list_glue.c -- its generated source has no
        # explicit main(), only exported @access(Public) functions).
        if has_module_init:
            library_init_symbol = "__asmpy_module_init"
        elif "main" in global_syms:
            library_init_symbol = "main"
        has_module_init = library_init_symbol is not None
        # DllMain(rcx=hinst, edx=reason, r8=reserved) -> eax (BOOL). Only
        # DLL_PROCESS_ATTACH (reason == 1) runs module init -- DLL_PROCESS_
        # DETACH/THREAD_ATTACH/THREAD_DETACH (0/2/3) have nothing to do
        # since this backend has no per-thread or teardown state. No
        # ExitProcess: a DLL returns to its loader, it never terminates the
        # host process itself.
        # cmp edx,1 ; jne +N ; [call rel32(module_init)] ; mov eax,1 ; ret
        entry_stub_len = 3 + 2 + (5 if has_module_init else 0) + 5 + 1
    else:
        # sub rsp,40 ; [call rel32(module_init)] ; call rel32(main) ; mov ecx,eax ;
        # call rel32(ExitProcess thunk) ; ud2
        entry_stub_len = 4 + (5 if has_module_init else 0) + 5 + 2 + 5 + 2
    text_total_len = entry_stub_off + entry_stub_len

    # ── 5. Decide PE section RVAs (one page each, in this fixed order). ──
    rva_text  = SECTION_ALIGN
    rva_data  = rva_text + _align(text_total_len, SECTION_ALIGN)
    data_rdata_len = len(bucket_bytes["rdata"]) + len(bucket_bytes["data"])
    rva_idata = rva_data + _align(data_rdata_len, SECTION_ALIGN) if data_rdata_len else rva_data
    # .rdata/.data are merged into one RW section here -- see the module
    # docstring on why a few-section layout was chosen over either a
    # single RWX blob (security heuristics) or a fully faithful
    # per-original-section layout (more bookkeeping for no benefit yet).
    rdata_base_in_section = 0
    # Align the mutable-data bucket to 8. Without this it starts wherever the
    # rodata blob happens to end, so a module global lands at an arbitrary
    # address -- measured at 0x140007247, i.e. 7 mod 8.
    #
    # That is wrong for two independent reasons. A pointer-sized global should
    # be naturally aligned on general principle (unaligned loads are legal on
    # x86-64 but slower, and any SSE-aligned access to one would fault). And a
    # word-granular conservative scan CANNOT SEE an unaligned slot at all: the
    # GC's root scan walks the globals range eight bytes at a time from a
    # page-aligned base, so it visits only addresses ≡ 0 mod 8 and stepped
    # straight over every global. That is what made the collector free live
    # module-level data -- not a wrong range, which was correct all along.
    data_base_in_section = _align(len(bucket_bytes["rdata"]), 8)

    def resolve(name: str) -> int:
        """Final absolute address of a global symbol or import thunk."""
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
        """Final absolute address of one input object's own section base.

        COFF REL32 relocations against local NASM labels inside data sections
        are often emitted as relocations against the section symbol itself
        (e.g. `.rdata`) plus an addend already encoded in the instruction's
        disp32. Those must resolve to *this object's* merged section base,
        not whichever same-named section symbol happened to win global name
        resolution first.
        """
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

    # rva_bss assigned after .idata is sized (placed last); referenced by
    # `resolve` via closure, fine since resolve() is only called below.
    # ── 6. Build .idata (import directory + ILT/IAT + hint/name + DLL names). ──
    dlls: dict[str, list[str]] = {}
    for name in imports:
        dlls.setdefault(dll_for(name), []).append(name)

    num_dlls = len(dlls)
    dir_table_len = (num_dlls + 1) * 20
    off = dir_table_len
    ilt_off: dict[str, int] = {}
    iat_off: dict[str, int] = {}
    for dll, names in dlls.items():
        ilt_off[dll] = off
        off += (len(names) + 1) * 8
    for dll, names in dlls.items():
        iat_off[dll] = off
        off += (len(names) + 1) * 8
    hintname_off: dict[str, int] = {}
    for name in imports:
        # Each Hint/Name entry must start on a word (even) boundary -- pad
        # based on the *running offset's* parity, not the name's length:
        # padding from one entry can leave `off` odd even when this name's
        # own length wouldn't have, so checking len(name) alone drifts
        # after the first entry that needed a pad byte.
        if off % 2:
            off += 1
        hintname_off[name] = off
        off += 2 + len(name) + 1  # hint(2) + name + NUL
    dllname_off: dict[str, int] = {}
    for dll in dlls:
        dllname_off[dll] = off
        off += len(dll) + 1
    idata_len = off

    idata = bytearray(idata_len)

    def w32(at: int, v: int) -> None:
        struct.pack_into("<I", idata, at, v & 0xFFFFFFFF)

    def w64(at: int, v: int) -> None:
        struct.pack_into("<Q", idata, at, v & 0xFFFFFFFFFFFFFFFF)

    dir_i = 0
    for dll, names in dlls.items():
        entry = dir_i * 20
        w32(entry + 0, rva_idata + ilt_off[dll])
        w32(entry + 12, rva_idata + dllname_off[dll])
        w32(entry + 16, rva_idata + iat_off[dll])
        for j, name in enumerate(names):
            hn_rva = rva_idata + hintname_off[name]
            w64(ilt_off[dll] + j * 8, hn_rva)
            w64(iat_off[dll] + j * 8, hn_rva)
        dir_i += 1
    for name in imports:
        at = hintname_off[name]
        struct.pack_into("<H", idata, at, 0)
        idata[at + 2: at + 2 + len(name)] = name.encode("ascii")
        idata[at + 2 + len(name)] = 0
    for dll, doff in dllname_off.items():
        idata[doff: doff + len(dll)] = dll.encode("ascii")
        idata[doff + len(dll)] = 0

    # ── 7. .bss placed after .idata; now every RVA is known. ──
    rva_bss = rva_idata + _align(idata_len, SECTION_ALIGN)

    # IAT slot RVA for each imported symbol, needed by the thunks.
    def iat_slot_rva(name: str) -> int:
        dll = dll_for(name)
        j = dlls[dll].index(name)
        return rva_idata + iat_off[dll] + j * 8

    # ── 8. Finalize .text bytes: thunks, entry stub, then patch every
    # relocation (original object relocs + the entry stub's own two). ──
    text = bucket_bytes["text"]
    text.extend(b"\x90" * (text_total_len - len(text)))
    for name in imports:
        pos = thunk_off[name]
        disp = (IMAGE_BASE + iat_slot_rva(name)) - (IMAGE_BASE + rva_text + pos + 6)
        text[pos:pos + 6] = bytes([0xFF, 0x25]) + struct.pack("<i", disp)

    if needs_acrt_iob_stub:
        # __acrt_iob_func(index) -> __iob_func() + index*sizeof(FILE).
        # index is always 0/1/2 (stdin/stdout/stderr), so a plain 32-bit
        # mov/shl is enough -- no sign extension concerns.
        iob = bytearray()
        iob += bytes([0x48, 0x83, 0xEC, 0x28])           # sub rsp, 40
        iob += bytes([0x41, 0x89, 0xCA])                 # mov r10d, ecx
        call_disp_pos = len(iob) + 1
        iob += bytes([0xE8, 0, 0, 0, 0])                 # call rel32 __iob_func thunk
        iob += bytes([0x49, 0xC1, 0xE2, 0x05])           # shl r10, 5  (*sizeof(FILE)==32)
        iob += bytes([0x4C, 0x01, 0xD0])                 # add rax, r10
        iob += bytes([0x48, 0x83, 0xC4, 0x28])           # add rsp, 40
        iob += bytes([0xC3])                             # ret
        assert len(iob) == acrt_iob_stub_len
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
        # DllMain(hinst, reason, reserved) -> BOOL. Only
        # DLL_PROCESS_ATTACH (reason == 1, in edx per Win64 ABI) runs
        # module init; every other reason just returns TRUE.
        stub = bytearray()
        stub += bytes([0x83, 0xFA, 0x01])                # cmp edx, 1
        skip_disp_pos = len(stub) + 1
        skip_len = (5 if has_module_init else 0)
        stub += bytes([0x75, skip_len & 0xFF])            # jne +skip_len
        if has_module_init:
            call_init_disp_pos = len(stub) + 1
            stub += bytes([0xE8, 0, 0, 0, 0])              # call rel32 module init
        stub += bytes([0xB8, 1, 0, 0, 0])                  # mov eax, 1
        stub += bytes([0xC3])                              # ret
        assert len(stub) == entry_stub_len
        if has_module_init:
            struct.pack_into(
                "<i", stub, call_init_disp_pos,
                init_addr - (stub_base_addr + call_init_disp_pos + 4),
            )
    else:
        main_addr = resolve(entry_symbol)
        stub = bytearray()
        stub += bytes([0x48, 0x83, 0xEC, 0x28])             # sub rsp, 40
        if has_module_init:
            call_init_disp_pos = len(stub) + 1
            stub += bytes([0xE8, 0, 0, 0, 0])                # call rel32 module init
        call1_disp_pos = len(stub) + 1
        stub += bytes([0xE8, 0, 0, 0, 0])                    # call rel32 main
        stub += bytes([0x89, 0xC1])                          # mov ecx, eax
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
        struct.pack_into(
            "<i", stub, call2_disp_pos,
            (IMAGE_BASE + rva_text + thunk_off["ExitProcess"]) - (stub_base_addr + call2_disp_pos + 4),
        )
    text[entry_stub_off:entry_stub_off + entry_stub_len] = bytes(stub)

    for oi, obj in enumerate(parsed):
        for sect in obj.sections:
            if _BUCKET_FOR_SECTION.get(sect.name) != "text":
                continue
            base = sect_base[(oi, sect.name)]
            for r in sect.relocs:
                if r.rtype != IMAGE_REL_AMD64_REL32:
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
    # Pad to the aligned data base so the bytes land where resolve() says they
    # do; data_base_in_section is the single source of truth for both.
    _rdata_blob = bytes(bucket_bytes["rdata"])
    _rdata_blob += bytes(data_base_in_section - len(_rdata_blob))
    rdata_data_blob = _rdata_blob + bytes(bucket_bytes["data"])

    # The OS entry point (executable) / DllMain (library) must be the
    # stub, not function index 0 of .text. A library with no module init
    # doesn't strictly need a DllMain at all, but always emitting the
    # trivial "return TRUE" one is simpler than a third no-stub layout.
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
        # (name, rva, raw_data, characteristics)
        (".text", rva_text, bytes(text),
         0x00000020 | 0x20000000 | 0x40000000),                       # CODE | EXECUTE | READ
        (".rdata", rva_data, rdata_data_blob,
         0x00000040 | 0x40000000 | 0x80000000) if rdata_data_blob else None,
        (".idata", rva_idata, bytes(idata),
         0x00000040 | 0x40000000),                                    # INITIALIZED_DATA | READ
        (".bss", rva_bss, b"",
         0x00000080 | 0x40000000 | 0x80000000) if bss_size else None,  # UNINIT | READ | WRITE
        (".edata", rva_edata, edata_bytes,
         0x00000040 | 0x40000000) if edata_bytes else None,           # INITIALIZED_DATA | READ
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
    """Build a `.edata` section: IMAGE_EXPORT_DIRECTORY + EAT + name
    pointer table + ordinal table + name strings, per the standard PE
    export format. `sorted_names` must already be sorted (the Windows
    loader binary-searches the name pointer table by name), `addrs`
    holds each name's matching RVA (already relative to the image base).
    Ordinals are assigned densely (0..n-1) in `sorted_names` order --
    there is no separate "ordinal base" gap since every export here has
    a name (no ordinal-only exports).
    """
    n = len(sorted_names)
    DIR_SIZE = 40
    eat_off = DIR_SIZE
    eat_len = 4 * n
    names_off = eat_off + eat_len
    names_len = 4 * n
    ordinals_off = names_off + names_len
    ordinals_len = 2 * n
    strings_off = ordinals_off + ordinals_len

    dll_name = b"portapy.dll\x00"
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
        0,                          # Characteristics
        0,                          # TimeDateStamp
        0, 0,                       # MajorVersion, MinorVersion
        base_rva + dll_name_off,    # Name
        1,                          # Base (ordinal base)
        n,                          # NumberOfFunctions
        n,                          # NumberOfNames
        base_rva + eat_off,         # AddressOfFunctions
        base_rva + names_off,       # AddressOfNames
        base_rva + ordinals_off,    # AddressOfNameOrdinals
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
    struct.pack_into("<I", dos_stub, 0x3C, 64)  # e_lfanew

    opt_hdr_size = 112 + 16 * 8  # PE32+ optional header + 16 data directories
    coff_hdr_off = 64
    headers_size = coff_hdr_off + 4 + 20 + opt_hdr_size + num_sects * 40
    headers_size_aligned = _align(headers_size, FILE_ALIGN)

    # Decide file offsets (file-aligned) for each section's raw data.
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

    characteristics = 0x0002 | 0x0020 | 0x0200  # EXECUTABLE_IMAGE | LARGE_ADDRESS_AWARE | DEBUG_STRIPPED
    if is_dll:
        characteristics |= 0x2000  # IMAGE_FILE_DLL
    coff_hdr = struct.pack(
        "<HHIIIHH",
        0x8664,            # IMAGE_FILE_MACHINE_AMD64
        num_sects,
        0,
        0, 0,              # symbol table (none in the final exe)
        opt_hdr_size,
        characteristics,
    )

    opt_hdr = bytearray()
    opt_hdr += struct.pack("<HBB", 0x020B, 0, 0)          # Magic=PE32+, linker ver
    opt_hdr += struct.pack("<III", 0, 0, 0)               # SizeOfCode/Init/Uninit (informational)
    opt_hdr += struct.pack("<I", entry_rva)
    opt_hdr += struct.pack("<I", sections[0][1])          # BaseOfCode
    opt_hdr += struct.pack("<Q", IMAGE_BASE)
    opt_hdr += struct.pack("<II", SECTION_ALIGN, FILE_ALIGN)
    opt_hdr += struct.pack("<HHHHHH", 6, 0, 0, 0, 6, 0)    # OS/Image/Subsystem version
    opt_hdr += struct.pack("<I", 0)                        # Win32VersionValue
    opt_hdr += struct.pack("<II", size_of_image, headers_size_aligned)
    opt_hdr += struct.pack("<I", 0)                        # CheckSum
    # Subsystem=CONSOLE. DllCharacteristics=NX_COMPAT only -- DYNAMIC_BASE
    # (ASLR) is deliberately *not* set: there's no .reloc section, so a
    # relocated load would corrupt every absolute address this linker
    # already baked in against the fixed IMAGE_BASE.
    opt_hdr += struct.pack("<HH", 3, 0x0100)
    opt_hdr += struct.pack("<QQQQ", 0x100000, 0x1000, 0x100000, 0x1000)  # stack/heap reserve+commit
    opt_hdr += struct.pack("<I", 0)                         # LoaderFlags
    opt_hdr += struct.pack("<I", 16)                        # NumberOfRvaAndSizes
    for i in range(16):
        if i == 0:  # EXPORT directory
            opt_hdr += struct.pack("<II", export_directory[0], export_directory[1])
        elif i == 1:  # IMPORT directory
            opt_hdr += struct.pack("<II", idata_entry[0], idata_entry[1])
        else:
            opt_hdr += struct.pack("<II", 0, 0)

    sect_hdrs = bytearray()
    for name, rva, data, chars, foff, raw_size in layout:
        name_b = name.encode()[:8].ljust(8, b"\x00")
        sect_hdrs += struct.pack(
            "<8sIIIIIIHHI",
            name_b,
            _align(max(len(data), 1) if data else bss_size, SECTION_ALIGN),  # VirtualSize
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
