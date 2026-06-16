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


def _run(cmd: list[str], extra_path_dirs: list[str] | None = None) -> None:
    print("$", " ".join(cmd))
    env = None
    if extra_path_dirs:
        env = os.environ.copy()
        env["PATH"] = (
            os.pathsep.join(extra_path_dirs) + os.pathsep + env.get("PATH", "")
        )
    proc = subprocess.run(cmd, capture_output=True, text=True, env=env)
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
) -> BuildResult:
    # Whole-program compilation: when we know the entry file, follow its imports
    # and merge every reachable project module's classes/functions into one unit
    # so cross-file constructors (`SourcePos(...)`) and inherited methods resolve.
    # Falls back to single-file parse when no entry path is available.
    if whole_program and entry_path is not None:
        module = load_program(src, entry_path)
    else:
        tokens = Lexer(src).tokenize()
        module = Parser(tokens).parse()
    sema_analyze(module, source_dir=source_dir, collect_errors=all_errors)

    if target == "linux":
        gen = LinuxCodegen(module, use_runtime_lib=use_runtime_lib)
        nasm_fmt = "elf64"
        obj_suffix = ".o"
    elif target == "windows":
        gen = WindowsCodegen(module, use_runtime_lib=use_runtime_lib)
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

    # Decide where intermediates go.
    out_path = out_path.resolve()
    stem = out_path.with_suffix("")
    asm_path = stem.with_suffix(".asm")
    obj_path = stem.with_suffix(obj_suffix)
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
        icon_obj = _build_icon_resource(icon_path, stem, gcc)

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
        _run(link_cmd, extra_path_dirs=[gcc_dir])
    elif bundle_mode == "onedir":
        exe_path = _link_onedir(
            target=target,
            obj_path=obj_path,
            out_path=exe_path,
            gcc=gcc,
            gcc_dir=gcc_dir,
            icon_obj=icon_obj,
        )
    else:
        link_cmd = [gcc, str(obj_path)]
        if icon_obj is not None:
            link_cmd.append(str(icon_obj))
        link_cmd += ["-o", str(exe_path)]
        if target == "linux":
            # The generated code uses absolute (non-PIC) relocations against
            # libc symbols, which modern gcc rejects under its default PIE mode.
            link_cmd.append("-no-pie")
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
        _run(link_cmd, extra_path_dirs=[gcc_dir])

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


def _link_onedir(
    *,
    target: str,
    obj_path: Path,
    out_path: Path,
    gcc: str,
    gcc_dir: str | None = None,
    icon_obj: Path | None = None,
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
