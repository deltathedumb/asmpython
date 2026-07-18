"""End-to-end driver: source.py -> .asm -> .o -> executable."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .lexer import Lexer
from .parser import Parser
from .program import load_program
from .sema import analyze as sema_analyze
from .target_freestanding import FreestandingCodegen
from .target_linux import LinuxCodegen
from .target_windows import WindowsCodegen


@dataclass
class BuildResult:
    asm_path: Path
    obj_path: Path | None
    exe_path: Path | None


def _resolve_tool(name: str, *, override: Path | None, env_var: str) -> str:
    """Find an external toolchain binary, checking in priority order:

    1. ``--<name>`` CLI override (caller-provided ``override`` Path).
    2. ``$MAMBA_<NAME>`` environment variable.
    3. A ``bin/`` directory adjacent to the install (lets the standalone
       archive ship NASM/gcc next to ``asmpython.bat``).
    4. The system PATH.

    Raises if none of those produce a runnable file. Errors mention every
    location we tried so the user knows where to drop a binary.
    """
    tried: list[str] = []
    if override is not None:
        p = Path(override)
        if p.is_file():
            return str(p.resolve())
        tried.append(f"--{name} {override}")
    env = os.environ.get(env_var)
    if env:
        p = Path(env)
        if p.is_file():
            return str(p.resolve())
        tried.append(f"${env_var}={env}")
    # Look in <repo-root>/bin for a bundled copy. driver.py lives at
    # asmpython/_compiler/driver.py, so the repo root is three levels up.
    repo_root = Path(__file__).resolve().parents[2]
    bundled = repo_root / "bin"
    for suffix in ("", ".exe"):
        cand = bundled / f"{name}{suffix}"
        if cand.is_file():
            return str(cand)
    tried.append(f"{bundled}/{name}[.exe]")
    # Also check tool-specific subdirectories under tools/ (dev layout).
    # nasm lives at tools/nasm/nasm.exe; gcc at tools/mingw64/bin/gcc.exe.
    tools = repo_root / "tools"
    tool_subdirs = [
        tools / name,
        tools / "mingw64" / "bin",
    ]
    for subdir in tool_subdirs:
        for suffix in ("", ".exe"):
            cand = subdir / f"{name}{suffix}"
            if cand.is_file():
                return str(cand)
    tried.append(f"{tools}/<subdir>/{name}[.exe]")
    path_hit = shutil.which(name)
    if path_hit:
        return path_hit
    tried.append("$PATH")
    raise RuntimeError(f"could not find '{name}'. Looked in: " + ", ".join(tried))


def _vendor_sdl2_dir(target: str) -> Path | None:
    """Directory holding asmpython's bundled SDL2/SDL2_ttf/SDL2_mixer
    binaries for `target`, or None if this target has no vendored copy.

    Windows has no universal system package manager, so a `lumen` program
    that needs `-lSDL2` would otherwise fail to link (or, with a DLL but no
    import lib, fail at the linker step) unless the user manually installs
    SDL2 dev libraries. asmpython ships its own copies under
    `asmpython/_vendor/sdl2/windows/` instead -- see that directory's
    README.md for exactly what's vendored and why. Linux relies on the
    system SDL2 (a single `apt install` away on every major distro), so
    there's no Linux vendor directory.
    """
    if target != "windows":
        return None
    d = Path(__file__).resolve().parents[1] / "_vendor" / "sdl2" / "windows"
    return d if d.is_dir() else None


# The vendored runtime DLLs (not the .dll.a import libs) under
# asmpython/_vendor/sdl2/windows/ -- listed explicitly rather than
# discovered via Path.glob(), which asmpython's own Path model doesn't
# support (this file is part of the compiler's own self-compiled source).
_VENDOR_SDL2_WINDOWS_DLLS = (
    "SDL2.dll",
    "SDL2_mixer.dll",
    "libSDL2_ttf.dll",
    "libfreetype.dll",
)


def _vendor_sdl2_runtime_dlls(target: str) -> list[Path]:
    """The vendored .dll files (not the .dll.a import libs) for `target`,
    so callers can copy them next to a built executable. Empty if this
    target has no vendor directory."""
    d = _vendor_sdl2_dir(target)
    if d is None:
        return []
    out: list[Path] = []
    for name in _VENDOR_SDL2_WINDOWS_DLLS:
        p = d / name
        if p.is_file():
            out.append(p)
    return out


def _copy_vendor_sdl2_dlls(target: str, dest_dir: Path) -> None:
    """Copy every vendored SDL2 runtime DLL into `dest_dir` (typically next
    to a freshly built .exe) so the program can actually start without the
    user having SDL2 on PATH. No-op if nothing needed it (the caller only
    invokes this when needs_gui/needs_audio/needs_ttf is true) or if this
    target has no vendor directory (Linux)."""
    for dll in _vendor_sdl2_runtime_dlls(target):
        dst = dest_dir / dll.name
        if dst.exists() and dst.stat().st_mtime >= dll.stat().st_mtime:
            continue
        dst.write_bytes(dll.read_bytes())


def _quote_cmd_part(s: str) -> str:
    """Wrap an argv element in double quotes if it contains a space, for
    the os.system()-backed subprocess stub (which only takes one command
    string, not a real argv array)."""
    has_space = False
    i = 0
    n = len(s)
    while i < n:
        if s[i] == " ":
            has_space = True
            break
        i = i + 1
    if not has_space:
        return s
    return '"' + s + '"'


def _run(cmd: list[str], extra_path_dirs: list[str] | None = None) -> None:
    print("$", " ".join(cmd))
    if extra_path_dirs:
        # Sema can't give `os.environ.copy()`'s result a real "dict" type
        # (os.environ is an opaque external attribute; only `.get()` is
        # special-cased), so it infers "any" -- codegen then picks the
        # generic LIST subscript-assign path for `env["PATH"] = ...`
        # (direct-index-into-buffer semantics) even though the runtime
        # value is dict-shaped, corrupting the stack. Confirmed via gdb
        # on a selfhost rebuild. Mutate the real process environment
        # directly instead of building a wrapper dict: under the
        # CPython-hosted compiler this changes os.environ in place
        # (inherited by subprocess.run's default env=None passthrough);
        # asmpython's subprocess stub is os.system()-backed and has no
        # env parameter regardless, so this is a no-op there either way,
        # same as before this PATH-prepend feature existed.
        new_path = os.pathsep.join(extra_path_dirs) + os.pathsep + os.environ.get("PATH", "")
        os.environ["PATH"] = new_path
    parts: list[str] = []
    for c in cmd:
        parts.append(_quote_cmd_part(c))
    cmd_str = " ".join(parts)
    proc = subprocess.run(cmd_str, capture_output=True, text=True)
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        raise RuntimeError(f"{cmd[0]} exited {proc.returncode}")


def _png_to_ico(png_path: Path, ico_path: Path) -> None:
    """Convert a PNG file to an ICO file.

    Tries Pillow first for high-quality multi-size ICO output.
    Falls back to a minimal ICO wrapper that embeds the PNG data directly
    (supported by Windows Vista+ and all modern browsers/toolchains).
    """
    try:
        from PIL import Image  # type: ignore
        img = Image.open(png_path).convert("RGBA")
        sizes = [(s, s) for s in (16, 32, 48, 64, 128, 256) if s <= img.width and s <= img.height]
        if not sizes:
            sizes = [(img.width, img.height)]
        img.save(str(ico_path), format="ICO", sizes=sizes)
        return
    except ImportError:
        pass

    # Pillow not available — embed PNG verbatim as a single ICO image.
    # The ICO format allows PNG-compressed images in the image directory
    # when the size is 256×256 (the PNG is stored as-is).
    import struct as _struct
    png_data = png_path.read_bytes()

    # Parse PNG IHDR to get width/height
    width = height = 0
    if len(png_data) >= 24 and png_data[:8] == b'\x89PNG\r\n\x1a\n':
        width  = _struct.unpack(">I", png_data[16:20])[0]
        height = _struct.unpack(">I", png_data[20:24])[0]
    if width == 0 or height == 0:
        raise RuntimeError(f"--icon: could not parse PNG dimensions from {png_path}")

    # ICO header: reserved(2) + type(2=ICO) + count(2)
    ico_header = _struct.pack("<HHH", 0, 1, 1)
    # Image directory entry: width(1) height(1) palette(1) reserved(1)
    #   planes(2) bitcount(2) bytes_in_res(4) offset(4)
    # Width/height = 0 means 256 in ICO format
    w_byte = 0 if width >= 256 else width
    h_byte = 0 if height >= 256 else height
    entry_offset = 6 + 16  # header + one entry
    dir_entry = _struct.pack(
        "<BBBBHHII",
        w_byte, h_byte,  # width, height
        0,               # palette colors (0 = no palette)
        0,               # reserved
        1,               # color planes
        32,              # bits per pixel
        len(png_data),   # bytes in resource
        entry_offset,    # offset from start of file
    )
    ico_path.write_bytes(ico_header + dir_entry + png_data)


def _build_icon_resource(icon_path: Path, stem: Path, gcc: str) -> Path:
    """Compile `icon_path` into a COFF object containing a Windows ICON
    resource (resource id 1), suitable for linking straight into a PE
    executable or DLL so it shows a custom icon in Explorer/the taskbar.

    Accepts .ico directly or .png (auto-converted to .ico). Uses `windres`
    from the same toolchain directory as `gcc` (mingw-w64 ships both).
    """
    if not icon_path.is_file():
        raise RuntimeError(f"--icon file not found: {icon_path}")

    actual_icon = icon_path
    if icon_path.suffix.lower() == ".png":
        converted = stem.with_suffix(".icon.ico")
        _png_to_ico(icon_path, converted)
        actual_icon = converted
    elif icon_path.suffix.lower() != ".ico":
        print(
            f"asmpython: warning: --icon {icon_path} does not have a .ico "
            "extension; Windows expects the ICO format for executable icons",
            file=sys.stderr,
        )

    rc_path = stem.with_suffix(".icon.rc")
    res_obj = stem.with_suffix(".icon.o")
    # Forward slashes are accepted (and unambiguous) inside .rc string literals
    # on Windows, avoiding backslash-escaping headaches.
    icon_posix = actual_icon.resolve().as_posix()
    rc_path.write_text(f'1 ICON "{icon_posix}"\n', encoding="utf-8")
    windres_dir = Path(gcc).parent
    windres = "windres"
    for suffix in ("", ".exe"):
        cand = windres_dir / f"windres{suffix}"
        if cand.is_file():
            windres = str(cand)
            break
    _run([windres, str(rc_path), "-O", "coff", "-o", str(res_obj)])
    return res_obj


def _compile_program(
    src: str,
    *,
    source_dir: Path | None,
    entry_path: Path | None,
    whole_program: bool,
    all_errors: bool,
    active_extensions: "frozenset[str] | None" = None,
):
    """Lex / parse / sema (target-independent front-end). Returns the typed module.

    `active_extensions` is always empty now -- the opt-in compiler-syntax
    extension system was withdrawn (see `asmpython/_compiler/extensions.py`
    and `archived/extensions/`). The parameter is kept only so this
    function's signature (and every caller's) doesn't need to change.
    """
    if whole_program and entry_path is not None:
        module = load_program(src, entry_path, active_extensions=active_extensions)
    else:
        tokens = Lexer(src).tokenize()
        module = Parser(tokens, active_extensions).parse()
    sema_analyze(
        module,
        source_dir=source_dir,
        collect_errors=all_errors,
        active_extensions=active_extensions,
    )
    return module


def _run_backend_x86_64(
    module,
    target: str,
    out_path: Path,
    *,
    gcc_path: Path | None = None,
    linker: str | None = None,
) -> BuildResult:
    """asmpython's built-in x86-64 backend: AST -> ir_lower.py -> the
    vendored backend (asmpython/_backends/x86_64), which compiles to an
    object file and links it via whichever linker plugin is selected
    (asmpython/_linkers) -- the backend's own default ("builtin": no gcc/
    ld involved at all) unless --linker overrides it.

    Scope is intentionally narrow for this first wiring: windows/linux
    onefile executables, no icon/SDL2/networking/threading (ir_lower.py
    doesn't lower those yet, and the builtin linkers' import mechanisms
    only cover what hardware.py/the runtime actually need -- see
    pe_linker.py / elf_linker.py's docstrings). Anything outside that
    raises clearly rather than silently falling back to the legacy
    backend.
    """
    if target not in ("windows", "linux"):
        raise ValueError(
            f"--backend x86-64 only supports --target windows/linux for now, got {target!r}"
        )
    abi = "win64" if target == "windows" else "sysv"

    from . import ir_lower
    from .._backends.x86_64 import __module_backend__ as backend
    from .._runtime.build import build_abi_shims, build_runtime, runtime_object_path

    ir_mod = ir_lower.lower_module(module)
    compiled = backend.compile(ir_mod, {"target_os": target, "abi": abi})
    program_obj = next(iter(compiled.values()))

    shim_obj = build_abi_shims(target).read_bytes()
    build_runtime(target)  # ensures runtime_object_path's file is current
    runtime_obj = runtime_object_path(target).read_bytes()

    # threading_shims.asm (Windows only) provides _threading_create/
    # _threading_join/etc, kept as a SEPARATE object from the always-
    # linked shim_obj above specifically because _threading_trampoline
    # references _threading_bootstrap, a real user-program-level
    # function that only exists in a program that actually imports
    # threading -- declaring that extern in the always-linked object
    # broke every program's link step (see threading_shims.asm's own
    # header comment). Only append it when the merged program actually
    # defines _threading_bootstrap (a direct, reliable signal that
    # stdlib/threading.py's source was pulled in), rather than trying to
    # infer "imports threading" from module aliasing.
    extra_objs: list[bytes] = []
    if target == "windows" and any(f.name == "_threading_bootstrap" for f in module.funcs):
        from .._runtime.build import build_threading_shims

        extra_objs.append(build_threading_shims(target).read_bytes())

    # asmpython.mlang: any Code(...) literal sema.py compiled via a real
    # external compiler (mlang_support._run_mlang_code) contributes its
    # own object file here. Default to the gcc linker when mlang is in
    # play (it's the only one currently able to link real compiler
    # output at all), but don't hard-reject an explicit --linker builtin
    # -- let it try and fail with its own ordinary error rather than a
    # special mlang-specific rejection. In practice that error currently
    # comes very early: real g++/gcc output on this toolchain is a
    # "bigobj" PE-COFF variant (a different header/section-count
    # encoding than classic COFF once a translation unit has enough
    # sections/symbols -- confirmed via `objdump`'s own "pe-bigobj-
    # x86-64" format label on a real compiled object, and no
    # -mno-big-obj-style flag exists to suppress it), which
    # coff_parse.py's narrow parser (explicitly scoped to what NASM/
    # coff.py emit, per its own docstring) doesn't understand at all --
    # not a buffer-size edge case, a genuinely different binary layout.
    # Supporting --linker builtin for real compiler output needs a real
    # bigobj-format COFF parser addition, not a bugfix; tracked in
    # RESUME.md rather than attempted here.
    mlang_objects = getattr(module, "mlang_objects", [])
    effective_linker = linker or ("gcc" if mlang_objects else backend.default_linker)
    gcc = None
    if effective_linker == "gcc":
        gcc = _resolve_tool("gcc", override=gcc_path, env_var="ASMPYTHON_GCC")

    link_args = {
        "target_os": target,
        "abi": abi,
        "entry_symbol": "main",
        "linker": effective_linker,
        "gcc_path": gcc,
        "extra_args": ["-mconsole"] if target == "windows" else [],
    }
    objects = [program_obj, shim_obj, runtime_obj]
    objects += extra_objs
    objects += [obj_bytes for obj_bytes, _obj_ext in mlang_objects]
    linked = backend.link(objects, link_args)
    out_bytes = next(iter(linked.values()))

    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out_bytes)

    print(f"wrote {out_path}")
    return BuildResult(asm_path=out_path, obj_path=out_path, exe_path=out_path)


def _run_backend_ternary(module, out_path: Path) -> BuildResult:
    """Compile a Python module to a flat ternary binary image.

    Output is a .tern file: a sequence of 4-byte little-endian signed
    integers, one per balanced-ternary memory cell (8-trit values).
    Load into TernarySystem.mem starting at address 0 and run from PC=0.
    """
    from . import ir_lower
    from .._backends.ternary import __module_backend__ as backend

    ir_mod = ir_lower.lower_module(module)
    compiled = backend.compile(ir_mod, {})
    out_bytes = next(iter(compiled.values()))

    out_path = out_path.with_suffix(".tern").resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out_bytes)
    print(f"wrote {out_path}")
    return BuildResult(asm_path=out_path, obj_path=out_path, exe_path=out_path)


def _run_backend_registered(module, backend_name: str, out_path: Path) -> BuildResult:
    """Compile+link `module` via a third-party `IRBackend` registered under
    `backend_name` (see `asmpython._backends.get_backend`/
    `asmpython.backend.Backend(...)`, the public authoring API). Mirrors
    `_run_backend_ternary`'s simple compile-then-link-then-write shape --
    third-party backends get no bespoke per-backend wiring beyond the
    `IRBackend` contract itself (`requested_args`/`default_linker`/
    `compile`/`link`)."""
    from . import ir_lower
    from .._backends import get_backend

    backend = get_backend(backend_name)
    if backend is None:
        names = ["legacy", "x86-64", "ternary", *_registered_backend_names()]
        raise ValueError(f"unknown backend {backend_name!r} (have: {', '.join(names)})")

    ir_mod = ir_lower.lower_module(module)
    compiled = backend.compile(ir_mod, {})
    program_obj = next(iter(compiled.values()))
    linked = backend.link([program_obj], {})
    out_bytes = next(iter(linked.values()))

    out_path = out_path.resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(out_bytes)
    print(f"wrote {out_path}")
    return BuildResult(asm_path=out_path, obj_path=out_path, exe_path=out_path)


def _registered_backend_names() -> list[str]:
    from .._backends import registered_names

    return registered_names()


def _run_backend(
    module,
    target: str,
    out_path: Path,
    *,
    emit_asm_only: bool = False,
    keep_intermediates: bool = False,
    keep_assembly: bool = False,
    use_runtime_lib: bool = False,
    nasm_path: Path | None = None,
    gcc_path: Path | None = None,
    bundle_mode: str = "onefile",
    output_type: str = "executable",
    icon_path: Path | None = None,
    entry_path: Path | None = None,
    backend: str = "legacy",
    linker: str | None = None,
    _asm_stem_suffix: str = "",
) -> BuildResult:
    """Target-specific back-end: codegen -> nasm -> gcc."""
    if backend == "x86-64" and (
        any(getattr(f, "asm_body", None) is not None for f in module.funcs)
        or any(
            getattr(m, "asm_body", None) is not None
            for c in module.classes
            for m in c.methods
        )
    ):
        # `@assembly_func` (raw inline NASM function/method bodies): only
        # the legacy (NASM-text codegen.py) backend actually emits the
        # NASM -- ir_lower.py's lower_func never inspects FuncDef.asm_body
        # at all, so under the default x86-64 backend the real body is
        # silently skipped and the function falls through to its ordinary
        # (empty, docstring-only) statement body's implicit `return 0`.
        # Confirmed via tests/cases/75_assembly_func.py: legacy prints the
        # correct 42/7/100; x86-64 compiles cleanly (no error at all) and
        # prints 0/0/51 -- a silent wrong-output miscompile, not a loud
        # failure. Refuse clearly instead, mirroring the overload
        # extension's own x86-64-only guard just below (same shape,
        # opposite backend).
        raise ValueError(
            "this program uses '@assembly_func' (raw inline NASM), which "
            "is only supported on --backend legacy today -- --backend "
            "x86-64 silently discards the assembly body instead of "
            "raising an error, so this is refused explicitly rather than "
            "risk a wrong-output build"
        )
    if backend != "x86-64" and getattr(module, "uses_overload", False):
        # `overload` extension: dispatch/symbol-mangling is only wired up
        # in the x86-64 IR backend today. The legacy (NASM-text codegen.py)
        # backend's call-emission path for a resolved-overload call was
        # investigated and found to route through machinery deeper than
        # this wave's scope (the call site for an overloaded function
        # isn't emitted at all under --use-runtime-lib, a separate gap from
        # the two symbol-lookup sites this wave already fixed) -- rather
        # than silently miscompile (confirmed: prints "(null)" instead of
        # dispatching), refuse clearly and point at the supported backend.
        raise ValueError(
            "this program uses the 'overload' extension, which is only "
            "supported on --backend x86-64 today (not 'legacy' or any "
            "other registered backend)"
        )
    if backend == "x86-64":
        if emit_asm_only or keep_assembly:
            raise ValueError("--backend x86-64 has no assembly stage; drop --emit-asm/--keep-assembly")
        if bundle_mode != "onefile" or output_type != "executable" or icon_path is not None:
            raise ValueError(
                "--backend x86-64 only supports plain onefile executables for now "
                "(no --onedir, --output-type library, or --icon)"
            )
        return _run_backend_x86_64(
            module, target, out_path,
            gcc_path=gcc_path, linker=linker,
        )
    elif backend == "ternary":
        return _run_backend_ternary(module, out_path)
    elif backend != "legacy":
        # Not one of the two built-in IR-backend names -- check the
        # third-party registry (asmpython.backend.Backend(...)) before giving up.
        return _run_backend_registered(module, backend, out_path)
    elif linker is not None and linker != "gcc":
        raise ValueError(f"--backend legacy only supports --linker gcc, got {linker!r}")

    if bundle_mode == "onedir":
        use_runtime_lib = True

    entry_path_str = str(entry_path) if entry_path is not None else None
    if target == "linux":
        gen = LinuxCodegen(module, use_runtime_lib=use_runtime_lib, entry_path=entry_path_str)
        nasm_fmt = "elf64"
        obj_suffix = ".o"
    elif target == "windows":
        gen = WindowsCodegen(module, use_runtime_lib=use_runtime_lib, entry_path=entry_path_str)
        nasm_fmt = "win64"
        obj_suffix = ".obj"
    elif target == "freestanding":
        gen = FreestandingCodegen(module, use_runtime_lib=False)
        nasm_fmt = "bin"
        obj_suffix = ".bin"
    elif target == "freestanding16":
        from .target_freestanding16 import Freestanding16Codegen

        gen = Freestanding16Codegen(module, use_runtime_lib=False)
        nasm_fmt = "bin"
        obj_suffix = ".img"
    else:
        raise ValueError(f"unknown target {target}")

    asm = gen.generate()

    # Decide where intermediates go.  When building multiple targets in one
    # pass, `_asm_stem_suffix` (e.g. "-windows", "-linux") disambiguates the
    # intermediate files so they don't collide when targets share a stem.
    out_path = out_path.resolve()
    stem = out_path.with_suffix("")
    int_base = stem.with_name(stem.name + _asm_stem_suffix) if _asm_stem_suffix else stem
    asm_path = int_base.with_suffix(".asm")
    obj_path = int_base.with_suffix(obj_suffix)
    exe_path = out_path

    asm_path.parent.mkdir(parents=True, exist_ok=True)
    asm_path.write_text(asm, encoding="utf-8")
    print(f"wrote {asm_path}")

    if emit_asm_only:
        return BuildResult(asm_path=asm_path, obj_path=None, exe_path=None)

    if icon_path is not None and target != "windows":
        print(
            f"asmpython: --icon is only supported for --target windows; "
            f"ignoring {icon_path}",
            file=sys.stderr,
        )
        icon_path = None

    nasm = _resolve_tool("nasm", override=nasm_path, env_var="ASMPYTHON_NASM")
    # `-w-label-redef-late`: NASM 2.16+ promotes "label changed between
    # passes" to an error by default. We hit this in the dict runtime when
    # forward references force the encoder to pick a different instruction
    # size on the second pass. The final object code is still correct.
    _run([
        nasm,
        "-f",
        nasm_fmt,
        "-w-label-redef-late",
        str(asm_path),
        "-o",
        str(obj_path),
    ])

    # Freestanding: -f bin produces the final binary directly; no gcc needed.
    if target in ("freestanding", "freestanding16"):
        if target == "freestanding16":
            # The boot sector issues a fixed-size INT 13h read; INT 13h fails the
            # whole read if any requested sector is past end-of-file, so pad the
            # image to (1 boot + BOOT_READ_SECTORS) whole sectors.
            from .target_freestanding16 import Freestanding16Codegen as _F16

            need = (_F16.BOOT_READ_SECTORS + 1) * 512
            data = obj_path.read_bytes()
            if len(data) < need:
                obj_path.write_bytes(data + b"\x00" * (need - len(data)))
        if not keep_assembly:
            try:
                asm_path.unlink()
            except OSError:
                pass
        print(f"wrote {obj_path}")
        return BuildResult(asm_path=asm_path, obj_path=obj_path, exe_path=obj_path)

    gcc = _resolve_tool("gcc", override=gcc_path, env_var="ASMPYTHON_GCC")
    gcc_dir = str(Path(gcc).parent)

    icon_obj: Path | None = None
    if icon_path is not None:
        icon_obj = _build_icon_resource(icon_path, int_base, gcc)

    if output_type == "library":
        # Emit a shared library (.dll / .so) instead of an executable: link the
        # object with `gcc -shared`. The output path is given the platform's
        # shared-library extension if the caller didn't already.
        exe_path = _shared_lib_path(exe_path, target)
        link_cmd = [gcc, "-shared", str(obj_path)]
        if icon_obj is not None:
            link_cmd.append(str(icon_obj))
        link_cmd += ["-o", str(exe_path)]
        if target == "windows":
            # Export everything so the library's symbols are usable by loaders.
            link_cmd += ["-Wl,--export-all-symbols"]
        if use_runtime_lib:
            from .._runtime.build import build_runtime, _build_dir

            build_runtime(target)
            link_cmd += [
                f"-L{_build_dir()}",
                f"-lasmpython_rt_{'win' if target == 'windows' else 'linux'}",
            ]
        needs_gui = getattr(gen, "needs_gui", False)
        needs_audio = getattr(gen, "needs_audio", False)
        needs_ttf = getattr(gen, "needs_ttf", False)
        vendor_dir = _vendor_sdl2_dir(target)
        if (needs_gui or needs_audio or needs_ttf) and vendor_dir is not None:
            link_cmd.append(f"-L{vendor_dir}")
        if needs_gui:
            link_cmd.append("-lSDL2")
        if needs_audio:
            # _audio_load_wav's stub calls SDL_RWFromFile (core SDL2, not
            # SDL2_mixer) but doesn't route through ffi_called/ffi_externs
            # the way a normal FFI call does (it's a hand-written assembly
            # helper, not a user-facing binding), so needs_gui's "any
            # SDL_-prefixed symbol" check never sees it -- audio-only
            # programs need -lSDL2 unconditionally alongside -lSDL2_mixer.
            link_cmd.append("-lSDL2_mixer")
            if not needs_gui:
                link_cmd.append("-lSDL2")
        if needs_ttf:
            link_cmd.append("-lSDL2_ttf")
        _run(link_cmd, extra_path_dirs=[gcc_dir])
        if needs_gui or needs_audio or needs_ttf:
            _copy_vendor_sdl2_dlls(target, exe_path.parent)
    elif bundle_mode == "onedir":
        _od_needs_gui = getattr(gen, "needs_gui", False)
        _od_needs_audio = getattr(gen, "needs_audio", False)
        _od_needs_ttf = getattr(gen, "needs_ttf", False)
        _onedir_extra: list = []
        _od_vendor_dir = _vendor_sdl2_dir(target)
        if (_od_needs_gui or _od_needs_audio or _od_needs_ttf) and _od_vendor_dir is not None:
            _onedir_extra.append(f"-L{_od_vendor_dir}")
        if _od_needs_gui:
            _onedir_extra.append("-lSDL2")
        if _od_needs_audio:
            # See the non-bundled link path's comment: _audio_load_wav
            # always needs core SDL2 (SDL_RWFromFile) regardless of needs_gui.
            _onedir_extra.append("-lSDL2_mixer")
            if not _od_needs_gui:
                _onedir_extra.append("-lSDL2")
        if _od_needs_ttf:
            _onedir_extra.append("-lSDL2_ttf")
        exe_path = _link_onedir(
            target=target,
            obj_path=obj_path,
            out_path=exe_path,
            gcc=gcc,
            gcc_dir=gcc_dir,
            icon_obj=icon_obj,
            extra_libs=_onedir_extra,
        )
        if _od_needs_gui or _od_needs_audio or _od_needs_ttf:
            # _link_onedir's bundle keeps a side-by-side copy of the runtime
            # next to the exe (see its own comment on why); SDL2's DLLs need
            # the same treatment since the Windows loader searches the exe's
            # own directory, not the resources/ subfolder.
            _copy_vendor_sdl2_dlls(target, exe_path.parent)
    else:
        link_cmd = [gcc, str(obj_path)]
        if icon_obj is not None:
            link_cmd.append(str(icon_obj))
        link_cmd += ["-o", str(exe_path)]
        if target == "linux":
            # The generated code uses absolute (non-PIC) relocations against
            # libc symbols, which modern gcc rejects under its default PIE mode.
            link_cmd.append("-no-pie")
        if target == "windows":
            # Force console subsystem so the CRT calls main() not WinMain().
            # Newer mingw-w64/w64devkit (gcc 16+) no longer infers this from
            # the presence of main vs WinMain and may default to GUI.
            link_cmd.append("-mconsole")
        if use_runtime_lib:
            from .._runtime.build import build_runtime, _build_dir

            # Ensure the runtime archive is up to date for this target.
            build_runtime(target)
            link_cmd += [
                f"-L{_build_dir()}",
                f"-lasmpython_rt_{'win' if target == 'windows' else 'linux'}",
            ]
        if target == "windows" and getattr(gen, "needs_net", False):
            link_cmd.append("-lws2_32")
        if target == "linux" and any(s in getattr(gen, "ffi_externs", set())
                                     for s in getattr(gen, "_THREAD_SYMS", ())):
            link_cmd.append("-lpthread")
        if target == "linux" and getattr(gen, "imported_funcs", None):
            # dlopen/dlsym: pre-2.34 glibc ships them in libdl, not libc.
            link_cmd.append("-ldl")
        _df_needs_gui = getattr(gen, "needs_gui", False)
        _df_needs_audio = getattr(gen, "needs_audio", False)
        _df_needs_ttf = getattr(gen, "needs_ttf", False)
        _df_vendor_dir = _vendor_sdl2_dir(target)
        if (_df_needs_gui or _df_needs_audio or _df_needs_ttf) and _df_vendor_dir is not None:
            link_cmd.append(f"-L{_df_vendor_dir}")
        if _df_needs_gui:
            link_cmd.append("-lSDL2")
        if _df_needs_audio:
            # See the "library" output path's comment on why audio-only
            # programs need -lSDL2 unconditionally too.
            link_cmd.append("-lSDL2_mixer")
            if not _df_needs_gui:
                link_cmd.append("-lSDL2")
        if _df_needs_ttf:
            link_cmd.append("-lSDL2_ttf")
        _run(link_cmd, extra_path_dirs=[gcc_dir])
        if _df_needs_gui or _df_needs_audio or _df_needs_ttf:
            _copy_vendor_sdl2_dlls(target, exe_path.parent)

    if not keep_intermediates:
        try:
            obj_path.unlink()
        except OSError:
            pass
        if icon_obj is not None:
            try:
                icon_obj.unlink()
                icon_obj.with_suffix(".rc").unlink()
            except OSError:
                pass

    if not keep_assembly:
        try:
            asm_path.unlink()
        except OSError:
            pass

    print(f"wrote {exe_path}")
    return BuildResult(asm_path=asm_path, obj_path=obj_path, exe_path=exe_path)


def compile_source(
    src: str,
    target: str,
    out_path: Path,
    *,
    emit_asm_only: bool = False,
    keep_intermediates: bool = False,
    keep_assembly: bool = False,
    use_runtime_lib: bool = False,
    nasm_path: Path | None = None,
    gcc_path: Path | None = None,
    bundle_mode: str = "onefile",
    source_dir: Path | None = None,
    entry_path: Path | None = None,
    whole_program: bool = True,
    output_type: str = "executable",
    icon_path: Path | None = None,
    all_errors: bool = False,
    backend: str = "legacy",
    linker: str | None = None,
    active_extensions: "frozenset[str] | None" = None,
) -> BuildResult:
    module = _compile_program(
        src,
        source_dir=source_dir,
        entry_path=entry_path,
        whole_program=whole_program,
        all_errors=all_errors,
        active_extensions=active_extensions,
    )
    return _run_backend(
        module,
        target,
        out_path,
        emit_asm_only=emit_asm_only,
        keep_intermediates=keep_intermediates,
        keep_assembly=keep_assembly,
        use_runtime_lib=use_runtime_lib,
        nasm_path=nasm_path,
        gcc_path=gcc_path,
        bundle_mode=bundle_mode,
        output_type=output_type,
        icon_path=icon_path,
        entry_path=entry_path,
        backend=backend,
        linker=linker,
    )


def compile_targets(
    src: str,
    targets: list[str],
    out_paths: list[Path],
    *,
    emit_asm_only: bool = False,
    keep_intermediates: bool = False,
    keep_assembly: bool = False,
    use_runtime_lib: bool = False,
    nasm_path: Path | None = None,
    gcc_path: Path | None = None,
    bundle_mode: str = "onefile",
    source_dir: Path | None = None,
    entry_path: Path | None = None,
    whole_program: bool = True,
    output_type: str = "executable",
    icon_path: Path | None = None,
    all_errors: bool = False,
    backend: str = "legacy",
    linker: str | None = None,
    active_extensions: "frozenset[str] | None" = None,
) -> list[BuildResult]:
    """Compile src for multiple targets, sharing the front-end (lex/parse/sema).

    Each target gets its own codegen, nasm, and link step.  Intermediate files
    get a ``-<target>`` suffix (e.g. ``hello-windows.asm``) to avoid collisions
    when two targets share the same output stem.
    """
    module = _compile_program(
        src,
        source_dir=source_dir,
        entry_path=entry_path,
        whole_program=whole_program,
        all_errors=all_errors,
        active_extensions=active_extensions,
    )
    results: list[BuildResult] = []
    for target, out_path in zip(targets, out_paths):
        results.append(_run_backend(
            module,
            target,
            out_path,
            emit_asm_only=emit_asm_only,
            keep_intermediates=keep_intermediates,
            keep_assembly=keep_assembly,
            use_runtime_lib=use_runtime_lib,
            nasm_path=nasm_path,
            gcc_path=gcc_path,
            bundle_mode=bundle_mode,
            output_type=output_type,
            backend=backend,
            linker=linker,
            icon_path=icon_path,
            entry_path=entry_path,
            _asm_stem_suffix=f"-{target}",
        ))
    return results


def _link_onedir(
    *,
    target: str,
    obj_path: Path,
    out_path: Path,
    gcc: str,
    gcc_dir: str | None = None,
    icon_obj: Path | None = None,
    extra_libs: list | None = None,
) -> Path:
    """Produce a bundle directory containing the executable plus a resources
    sub-folder holding the shared libraries (the runtime, and eventually one
    .dll/.so per imported user module). The layout is:

        <bundle>/
          <app>.exe                 (Windows)   |  <app>.elf  (Linux)
          resources/                (Windows)   |  .resources/ (Linux, hidden)
            libasmpython_rt_*.dll/.so
            ...

    The bundle directory is the `-o` path itself (treated as a folder); the
    executable inside is named after that path's stem. Returns the exe path.
    """
    from .._runtime.build import build_runtime_shared

    # The output path is the bundle directory. `-o build/app` -> build/app/ with
    # app.exe inside. The exe basename comes from the path's stem.
    bundle_dir = out_path.with_suffix("")
    app_name = bundle_dir.name
    # Hidden resources dir on Linux (leading dot), plain on Windows.
    res_name = "resources" if target == "windows" else ".resources"
    res_dir = bundle_dir / res_name
    res_dir.mkdir(parents=True, exist_ok=True)

    # Build the shared runtime library and copy it into resources/.
    shared_path = build_runtime_shared(target)
    bundled_shared = res_dir / shared_path.name
    if bundled_shared.exists():
        bundled_shared.unlink()
    bundled_shared.write_bytes(shared_path.read_bytes())

    exe_ext = ".exe" if target == "windows" else ".elf"
    exe_path = bundle_dir / f"{app_name}{exe_ext}"

    # Link against the shared library by short name. On Linux use rpath/$ORIGIN
    # so the loader looks in the resources dir next to the executable.
    short = "asmpython_rt_" + ("win" if target == "windows" else "linux")
    link_cmd = [gcc, str(obj_path)]
    if icon_obj is not None:
        link_cmd.append(str(icon_obj))
    link_cmd += ["-o", str(exe_path), f"-L{res_dir}", f"-l{short}"]
    if target == "linux":
        link_cmd += [f"-Wl,-rpath,$ORIGIN/{res_name}"]
    if extra_libs:
        link_cmd.extend(extra_libs)
    _run(link_cmd, extra_path_dirs=[gcc_dir] if gcc_dir else None)

    # On Windows the loader searches the .exe's own directory and PATH, not an
    # arbitrary subfolder, so drop a copy of the runtime .dll next to the exe
    # too. (The canonical copy stays in resources/ for the per-module dlls to
    # live alongside; this side-by-side copy just satisfies the loader.)
    if target == "windows":
        side_by_side = bundle_dir / shared_path.name
        if side_by_side.exists():
            side_by_side.unlink()
        side_by_side.write_bytes(shared_path.read_bytes())

    return exe_path


def _shared_lib_path(out_path: Path, target: str) -> Path:
    """Give `out_path` the platform's shared-library extension if it doesn't
    already have a .dll/.so suffix (so `-o foo` -> `foo.dll` / `foo.so`)."""
    ext = ".dll" if target == "windows" else ".so"
    if out_path.suffix in (".dll", ".so"):
        return out_path
    return out_path.with_suffix(ext)


def detect_default_target() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    raise RuntimeError(f"unsupported host platform: {sys.platform}")
