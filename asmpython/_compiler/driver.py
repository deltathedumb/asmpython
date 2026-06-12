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


def compile_source(
    src: str,
    target: str,
    out_path: Path,
    *,
    emit_asm_only: bool = False,
    keep_intermediates: bool = False,
    use_runtime_lib: bool = False,
    nasm_path: Path | None = None,
    gcc_path: Path | None = None,
    bundle_mode: str = "onefile",
    source_dir: Path | None = None,
    entry_path: Path | None = None,
    whole_program: bool = True,
    output_type: str = "executable",
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
    sema_analyze(module, source_dir=source_dir)

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

    # Freestanding: -f bin produces the final binary directly; no linker needed.
    if target == "freestanding":
        if not keep_intermediates:
            pass  # obj_path IS the final binary for freestanding
        print(f"wrote {obj_path}")
        return BuildResult(asm_path=asm_path, obj_path=obj_path, exe_path=obj_path)

    # Both targets now use gcc as the linker driver so the C runtime
    # (msvcrt on Windows, libc on Linux) is linked in transparently.
    gcc = _resolve_tool("gcc", override=gcc_path, env_var="ASMPYTHON_GCC")
    # gcc needs to find sibling tools (ld, as, collect2) — when we resolve
    # gcc by absolute path from the bundled MinGW, its directory may not be
    # on PATH, so add it explicitly.
    gcc_dir = str(Path(gcc).parent)

    if output_type == "library":
        # Emit a shared library (.dll / .so) instead of an executable: link the
        # object with `gcc -shared`. The output path is given the platform's
        # shared-library extension if the caller didn't already.
        exe_path = _shared_lib_path(exe_path, target)
        link_cmd = [gcc, "-shared", str(obj_path), "-o", str(exe_path)]
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
        )
    else:
        link_cmd = [gcc, str(obj_path), "-o", str(exe_path)]
        if use_runtime_lib:
            from .._runtime.build import build_runtime, _build_dir

            # Ensure the runtime archive is up to date for this target.
            build_runtime(target)
            link_cmd += [
                f"-L{_build_dir()}",
                f"-lasmpython_rt_{'win' if target == 'windows' else 'linux'}",
            ]
        _run(link_cmd, extra_path_dirs=[gcc_dir])

    if not keep_intermediates:
        try:
            obj_path.unlink()
        except OSError:
            pass

    print(f"wrote {exe_path}")
    return BuildResult(asm_path=asm_path, obj_path=obj_path, exe_path=exe_path)


def _link_onedir(
    *, target: str, obj_path: Path, out_path: Path, gcc: str, gcc_dir: str | None = None
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
    link_cmd = [
        gcc,
        str(obj_path),
        "-o",
        str(exe_path),
        f"-L{res_dir}",
        f"-l{short}",
    ]
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
