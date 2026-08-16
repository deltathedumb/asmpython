"""Declare external native libraries without editing the linkers.

Both linkers decide which shared library provides an undefined symbol from a
hardcoded table -- ``pe_linker._DLL_FOR_SYMBOL`` on Windows,
``elf_linker._SO_FOR_SYMBOL`` on Linux. Those tables cover exactly the
externs asmpython's own runtime references, and anything else fails to link
with "add it to <module>._X_FOR_SYMBOL if it's a real import". That is a fine
contract for the runtime's own imports and a dead end for user code: linking
against SDL2, a BLAS, SQLite, or a CPython extension module meant editing the
compiler.

This module is the general mechanism. A :class:`NativeLibrary` names a
library as the *loader* will see it (``SDL2.dll``, ``libopenblas.so.0``) and
says which symbols it provides -- either listed explicitly, or discovered by
reading the real file's export table (see :func:`exported_symbols`). A
:class:`NativeLibraryRegistry` merges those declarations into the single
``symbol -> library`` map the linkers consult.

The builtin tables stay authoritative. A declaration can only ever ADD a
mapping for a symbol the linker did not already know, so no declaration can
retarget ``malloc`` away from libc, and a build that declares nothing links
byte-for-byte identically to before.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path


class NativeLibraryError(Exception):
    """A native library declaration is unusable (missing file, bad format)."""


# Module names this process has published into STDLIB_BINDINGS. Tracked so a
# second build in the same process can replace its own earlier registration
# without mistaking it for a real stdlib collision.
_INSTALLED_MODULES: set[str] = set()


@dataclass(frozen=True)
class NativeFunction:
    """One callable a declared library exposes to asmpython source.

    Linking only decides where an undefined symbol comes from. To *call* a
    library function, sema also needs its signature -- asmpython has no way to
    infer the argument or return kinds of a foreign symbol. These become
    ordinary ``stdlib.Func`` FFI bindings, so a declared library is reached
    with a plain ``import`` and behaves exactly like a built-in binding.
    """

    name: str
    arg_types: tuple[str, ...] = ()
    ret_type: str = "int"
    #: C symbol, when it differs from the asmpython-visible name.
    symbol: str | None = None
    #: Passed through to `Func.ret_conv` -- "ptr" for a real 64-bit pointer
    #: return, "f2i" for a double the caller wants truncated to int.
    ret_conv: str | None = None

    @property
    def c_symbol(self) -> str:
        return self.symbol or self.name


@dataclass(frozen=True)
class NativeLibrary:
    """An external shared library this build may import symbols from.

    ``name`` is the *load* name recorded in the produced binary's import
    table (PE) or ``DT_NEEDED`` list (ELF) -- ``SDL2.dll``, ``libm.so.6``. It
    is what the OS loader resolves at run time, so it must match the file the
    target machine actually has, which is not necessarily the file we read
    exports from at build time.

    ``symbols`` lists the exported names to accept. Leaving it empty and
    setting ``path`` instead discovers them from the file itself, which is
    the point of the whole exercise -- naming one library should not mean
    transcribing several thousand symbol names by hand.

    ``target_os`` scopes the declaration; ``None`` means every target. A
    project that names both ``SDL2.dll`` and ``libSDL2-2.0.so.0`` wants each
    one to apply only where it exists.
    """

    name: str
    symbols: tuple[str, ...] = ()
    path: str | None = None
    target_os: str | None = None
    #: Module name asmpython source imports to reach `functions`. ``None``
    #: declares a link-only library -- its symbols become resolvable, which is
    #: what a library referenced by *another* library needs, with nothing
    #: directly callable from Python source.
    module: str | None = None
    functions: tuple[NativeFunction, ...] = ()

    def __post_init__(self) -> None:
        if not self.name:
            raise NativeLibraryError("native library name must not be empty")
        if self.functions and not self.module:
            raise NativeLibraryError(
                f"native library {self.name!r} declares functions but no "
                "`module` name for asmpython source to import them from"
            )
        if self.target_os not in (None, "windows", "linux"):
            raise NativeLibraryError(
                f"native library {self.name!r}: unsupported target_os "
                f"{self.target_os!r} (expected 'windows', 'linux', or None)"
            )

    def applies_to(self, target_os: str) -> bool:
        return self.target_os is None or self.target_os == target_os

    def resolved_symbols(self, *, search_dirs: "tuple[Path, ...]" = ()) -> tuple[str, ...]:
        """Symbols this library provides: declared ones, else discovered.

        Anything reachable through a declared `functions` entry is always
        included -- declaring a callable and then failing to link it would be
        incoherent. Beyond that, an explicit `symbols` list wins outright: a
        declaration that names symbols is stating an intent narrower than
        "everything this file happens to export", and honouring that keeps a
        typo'd symbol a link error instead of letting it silently resolve
        against some unrelated export.
        """

        declared = tuple(fn.c_symbol for fn in self.functions)
        if self.symbols:
            return (*declared, *self.symbols)
        if self.path is not None:
            return (*declared, *exported_symbols(_locate(self.path, search_dirs)))
        if declared:
            return declared
        raise NativeLibraryError(
            f"native library {self.name!r} declares no symbols, functions, or "
            "path to read them from; give it `symbols=(...)`, `functions=(...)`, "
            "or `path=`"
        )

    def bindings(self) -> dict:
        """This library's `functions` as a stdlib-style FFI ``BINDINGS`` dict."""

        from ..stdlib import Func

        out: dict = {}
        for fn in self.functions:
            out[fn.name] = Func(
                arg_types=tuple(fn.arg_types),
                ret_type=fn.ret_type,
                c_name=fn.c_symbol,
                ret_conv=fn.ret_conv,
            )
        return out


def _locate(raw: str, search_dirs: "tuple[Path, ...]") -> Path:
    """Resolve a declared path, trying each search directory in turn."""

    candidate = Path(raw)
    if candidate.is_absolute():
        if not candidate.is_file():
            raise NativeLibraryError(f"native library file not found: {candidate}")
        return candidate
    tried: list[Path] = []
    for directory in (Path.cwd(), *search_dirs):
        resolved = directory / candidate
        if resolved.is_file():
            return resolved
        tried.append(resolved)
    locations = "\n  ".join(str(p) for p in tried)
    raise NativeLibraryError(
        f"native library file {raw!r} not found. Looked in:\n  {locations}"
    )


# ─────────────────────────── export-table readers ───────────────────────────
#
# Reading a real library's exports is what makes a one-line declaration
# usable. Both readers parse only far enough to reach the name table: this is
# not a general object-file library, and deliberately so -- it never has to
# handle anything but "which names does this file export".


def exported_symbols(path: Path) -> tuple[str, ...]:
    """Return the symbol names ``path`` exports, dispatching on its format.

    Detection is by content (the PE/ELF magic), not by file extension: a
    versioned ``.so.6``, a bare ``.so``, and a ``.dll`` renamed by a vendor
    all have to work, and only the magic is reliable across those.
    """

    try:
        blob = path.read_bytes()
    except OSError as exc:
        raise NativeLibraryError(f"cannot read native library {path}: {exc}") from exc

    if blob[:4] == b"\x7fELF":
        return _elf_exported_symbols(blob, path)
    if blob[:2] == b"MZ":
        return _pe_exported_symbols(blob, path)
    raise NativeLibraryError(
        f"{path} is neither a PE (MZ) nor an ELF file; cannot read its exports"
    )


def _pe_exported_symbols(blob: bytes, path: Path) -> tuple[str, ...]:
    """Names in a PE image's export directory (``.edata``)."""

    if len(blob) < 0x40:
        raise NativeLibraryError(f"{path}: truncated DOS header")
    pe_off = struct.unpack_from("<I", blob, 0x3C)[0]
    if blob[pe_off:pe_off + 4] != b"PE\0\0":
        raise NativeLibraryError(f"{path}: missing PE signature")

    coff_off = pe_off + 4
    n_sections, = struct.unpack_from("<H", blob, coff_off + 2)
    opt_size, = struct.unpack_from("<H", blob, coff_off + 16)
    opt_off = coff_off + 20
    if opt_size == 0:
        raise NativeLibraryError(f"{path}: object file, not an image (no optional header)")

    # The data-directory array sits at a different offset in PE32 vs PE32+
    # because the optional header's 8 pointer-ish fields widen to 64 bits.
    magic, = struct.unpack_from("<H", blob, opt_off)
    if magic == 0x20B:      # PE32+
        dir_off = opt_off + 112
    elif magic == 0x10B:    # PE32
        dir_off = opt_off + 96
    else:
        raise NativeLibraryError(f"{path}: unknown optional-header magic {magic:#x}")

    export_rva, export_size = struct.unpack_from("<II", blob, dir_off)
    if export_rva == 0 or export_size == 0:
        return ()   # a library with no export directory exports nothing

    # Section headers map RVAs to file offsets; the export tables are all
    # addressed by RVA.
    sections: list[tuple[int, int, int, int]] = []
    sect_off = opt_off + opt_size
    for i in range(n_sections):
        base = sect_off + i * 40
        virt_size, virt_addr, raw_size, raw_ptr = struct.unpack_from("<IIII", blob, base + 8)
        sections.append((virt_addr, max(virt_size, raw_size), raw_ptr, raw_size))

    def to_file_offset(rva: int) -> int:
        for virt_addr, span, raw_ptr, raw_size in sections:
            if virt_addr <= rva < virt_addr + span:
                offset = raw_ptr + (rva - virt_addr)
                if offset < len(blob):
                    return offset
        raise NativeLibraryError(f"{path}: RVA {rva:#x} is outside every section")

    export_off = to_file_offset(export_rva)
    n_names, = struct.unpack_from("<I", blob, export_off + 24)
    names_rva, = struct.unpack_from("<I", blob, export_off + 32)
    if n_names == 0:
        return ()   # ordinal-only exports; nothing linkable by name

    names_off = to_file_offset(names_rva)
    out: list[str] = []
    for i in range(n_names):
        name_rva, = struct.unpack_from("<I", blob, names_off + i * 4)
        start = to_file_offset(name_rva)
        end = blob.index(b"\0", start)
        out.append(blob[start:end].decode("ascii", "replace"))
    return tuple(out)


def _elf_exported_symbols(blob: bytes, path: Path) -> tuple[str, ...]:
    """Defined global names in an ELF shared object's ``.dynsym``."""

    if blob[4] != 2:
        raise NativeLibraryError(f"{path}: only 64-bit ELF is supported")
    e_shoff, = struct.unpack_from("<Q", blob, 0x28)
    e_shentsize, = struct.unpack_from("<H", blob, 0x3A)
    e_shnum, = struct.unpack_from("<H", blob, 0x3C)
    if e_shoff == 0 or e_shnum == 0:
        # Fully stripped of section headers. The dynamic segment still has the
        # data, but reaching it means walking program headers -- not worth it
        # until something real needs it, and a clear error beats a wrong list.
        raise NativeLibraryError(
            f"{path}: no section headers; declare `symbols=(...)` explicitly"
        )

    SHT_DYNSYM = 11
    for i in range(e_shnum):
        base = e_shoff + i * e_shentsize
        sh_type, = struct.unpack_from("<I", blob, base + 4)
        if sh_type != SHT_DYNSYM:
            continue
        sh_offset, sh_size = struct.unpack_from("<QQ", blob, base + 24)
        sh_link, = struct.unpack_from("<I", blob, base + 40)
        sh_entsize, = struct.unpack_from("<Q", blob, base + 56)
        if sh_entsize == 0:
            sh_entsize = 24

        str_base = e_shoff + sh_link * e_shentsize
        str_offset, = struct.unpack_from("<Q", blob, str_base + 24)

        out: list[str] = []
        for j in range(sh_size // sh_entsize):
            sym = sh_offset + j * sh_entsize
            st_name, = struct.unpack_from("<I", blob, sym)
            st_shndx, = struct.unpack_from("<H", blob, sym + 6)
            if st_name == 0 or st_shndx == 0:
                continue    # unnamed, or SHN_UNDEF: an import, not an export
            start = str_offset + st_name
            end = blob.index(b"\0", start)
            out.append(blob[start:end].decode("ascii", "replace"))
        return tuple(out)
    return ()


# ────────────────────────────── the registry ──────────────────────────────


@dataclass
class NativeLibraryRegistry:
    """Collects declarations and flattens them into one symbol -> library map."""

    libraries: list[NativeLibrary] = field(default_factory=list)
    search_dirs: tuple[Path, ...] = ()

    def declare(self, library: NativeLibrary) -> None:
        self.libraries.append(library)

    def symbol_map(self, target_os: str, *, builtin: "dict[str, str] | None" = None) -> dict[str, str]:
        """Build the ``symbol -> library name`` map for ``target_os``.

        ``builtin`` is the linker's own hardcoded table. Symbols it already
        owns are skipped rather than overwritten: the runtime's imports are
        not up for renegotiation by a project file, and skipping them is what
        guarantees an undeclared build links exactly as it did before.

        Between two *declared* libraries claiming the same symbol, the first
        declaration wins and the second is ignored. That is deterministic and
        matches how a real linker resolves a duplicate across ``-l`` flags in
        command-line order.
        """

        owned = builtin or {}
        mapping: dict[str, str] = {}
        for library in self.libraries:
            if not library.applies_to(target_os):
                continue
            for symbol in library.resolved_symbols(search_dirs=self.search_dirs):
                if symbol in owned or symbol in mapping:
                    continue
                mapping[symbol] = library.name
        return mapping

    def install_bindings(self, target_oses: "tuple[str, ...] | list[str]") -> dict[str, dict]:
        """Publish each declared library's functions as an importable module.

        Registers into ``stdlib.STDLIB_BINDINGS``, which is the only registry
        sema consults (see ``sema._load_module``) -- so a declared library is
        imported by ordinary ``import <module>`` and type-checks through the
        same path as a built-in FFI binding, with no separate resolution rule.

        Every requested target is merged in one pass because sema runs ONCE
        for a multi-target build: a project declaring ``SDL2.dll`` for windows
        and ``libSDL2-2.0.so.0`` for linux under one module name needs both
        halves visible while type-checking, and which library actually
        provides a symbol is settled per-target later, at link time.

        Refuses to shadow a real stdlib module: silently redirecting ``import
        math`` to a user's DLL would be a miserable bug to track down.
        Returns the modules installed, for a caller that wants to undo it.
        """

        from ..stdlib import STDLIB_BINDINGS

        installed: dict[str, dict] = {}
        for library in self.libraries:
            if not library.module:
                continue
            if not any(library.applies_to(os_name) for os_name in target_oses):
                continue
            # A name already installed by an earlier build in this process is
            # ours to replace -- only a genuine stdlib collision is an error.
            # Without this, a second compile in one process (a test suite, an
            # embedding harness) would reject the very module it just
            # installed itself.
            first_use = library.module not in installed
            collides = library.module in STDLIB_BINDINGS and library.module not in _INSTALLED_MODULES
            if collides and first_use:
                raise NativeLibraryError(
                    f"native library {library.name!r} wants module name "
                    f"{library.module!r}, which is already an asmpython stdlib "
                    "module; choose another name"
                )
            module = installed.setdefault(library.module, {})
            module.update(library.bindings())
        STDLIB_BINDINGS.update(installed)
        _INSTALLED_MODULES.update(installed)
        return installed


def parse_declaration(raw: str, *, target_os: str | None = None) -> NativeLibrary:
    """Parse one CLI/config declaration into a :class:`NativeLibrary`.

    Accepted forms::

        SDL2.dll                    load name, discovered from a file of that name
        SDL2.dll=vendor/SDL2.dll    load name, exports read from an explicit path
        SDL2.dll:SDL_Init,SDL_Quit  load name, exactly these symbols

    The ``=path`` form is the common one: name the library as the loader will
    see it, and point at the copy on this machine to read exports from.
    """

    text = raw.strip()
    if not text:
        raise NativeLibraryError("empty native library declaration")

    if ":" in text and "=" not in text:
        name, _, symbol_text = text.partition(":")
        symbols = tuple(s.strip() for s in symbol_text.split(",") if s.strip())
        if not symbols:
            raise NativeLibraryError(f"{raw!r}: ':' given but no symbols listed")
        return NativeLibrary(name=name.strip(), symbols=symbols, target_os=target_os)

    name, sep, path_text = text.partition("=")
    name = name.strip()
    path = path_text.strip() if sep else name
    return NativeLibrary(name=name, path=path, target_os=target_os)


# The registry the current build is using. A process-level hook, matching how
# `site_packages.install_native_import_resolution()` extends resolution: the
# CLI populates it once from the project config and flags, and the driver
# reads it when it builds the link arguments, so nothing in between has to
# grow a parameter it would only ever forward.
_ACTIVE = NativeLibraryRegistry()


def active_registry() -> NativeLibraryRegistry:
    return _ACTIVE


def set_active_registry(registry: NativeLibraryRegistry) -> None:
    global _ACTIVE
    _ACTIVE = registry


def from_mapping(data: dict) -> NativeLibrary:
    """Build a :class:`NativeLibrary` from one ``project.json`` entry.

    Expected shape (only ``name`` is required)::

        {
          "name": "user32.dll",
          "path": "C:/Windows/System32/user32.dll",
          "target_os": "windows",
          "module": "user32",
          "functions": [
            {"name": "GetSystemMetrics", "args": ["int"], "ret": "int"}
          ]
        }
    """

    if not isinstance(data, dict):
        raise NativeLibraryError(
            f"native library entry must be an object, got {type(data).__name__}"
        )
    name = str(data.get("name") or "").strip()
    if not name:
        raise NativeLibraryError("native library entry is missing 'name'")

    functions: list[NativeFunction] = []
    for raw in data.get("functions") or ():
        if not isinstance(raw, dict):
            raise NativeLibraryError(
                f"{name}: each entry in 'functions' must be an object"
            )
        fn_name = str(raw.get("name") or "").strip()
        if not fn_name:
            raise NativeLibraryError(f"{name}: a 'functions' entry is missing 'name'")
        functions.append(
            NativeFunction(
                name=fn_name,
                arg_types=tuple(str(a) for a in raw.get("args") or ()),
                ret_type=str(raw.get("ret") or "int"),
                symbol=(str(raw["symbol"]) if raw.get("symbol") else None),
                ret_conv=(str(raw["ret_conv"]) if raw.get("ret_conv") else None),
            )
        )

    return NativeLibrary(
        name=name,
        symbols=tuple(str(s) for s in data.get("symbols") or ()),
        path=(str(data["path"]) if data.get("path") else None),
        target_os=(str(data["target_os"]) if data.get("target_os") else None),
        module=(str(data["module"]) if data.get("module") else None),
        functions=tuple(functions),
    )


def default_search_dirs(project_dir: "Path | None", library_dirs: "list[str] | None") -> tuple[Path, ...]:
    """Directories to look in for a declared library file, in priority order."""

    dirs: list[Path] = []
    if project_dir is not None:
        dirs.append(project_dir)
        for entry in library_dirs or ():
            dirs.append(project_dir / entry)
    return tuple(dirs)


__all__ = [
    "NativeFunction",
    "NativeLibrary",
    "NativeLibraryError",
    "NativeLibraryRegistry",
    "default_search_dirs",
    "exported_symbols",
    "from_mapping",
    "parse_declaration",
]
