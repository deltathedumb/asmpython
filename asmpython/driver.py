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
from .sema import analyze as sema_analyze
from .target_linux import LinuxCodegen
from .target_windows import WindowsCodegen


@dataclass
class BuildResult:
    asm_path: Path
    obj_path: Path | None
    exe_path: Path | None


def _which_required(name: str) -> str:
    p = shutil.which(name)
    if not p:
        raise RuntimeError(f"required tool not on PATH: {name}")
    return p


def _resolve_tool(name: str, *, override: Path | None, env_var: str) -> str:
    """Find an external toolchain binary, checking in priority order:

    1. ``--<name>`` CLI override (caller-provided ``override`` Path).
    2. ``$MAMBA_<NAME>`` environment variable.
    3. A ``bin/`` directory adjacent to the install (lets the standalone
       archive ship NASM/gcc next to ``serpent.bat``).
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
    # Look in <repo-root>/bin for a bundled copy. The repo root is the
    # grandparent of this file (driver.py is at serpent/driver.py).
    repo_root = Path(__file__).resolve().parent.parent
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
        env["PATH"] = os.pathsep.join(extra_path_dirs) + os.pathsep + env.get("PATH", "")
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
) -> BuildResult:
    tokens = Lexer(src).tokenize()
    module = Parser(tokens).parse()
    sema_analyze(module)

    if target == "linux":
        gen = LinuxCodegen(module, use_runtime_lib=use_runtime_lib)
        nasm_fmt = "elf64"
        obj_suffix = ".o"
    elif target == "windows":
        gen = WindowsCodegen(module, use_runtime_lib=use_runtime_lib)
        nasm_fmt = "win64"
        obj_suffix = ".obj"
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

    nasm = _resolve_tool("nasm", override=nasm_path, env_var="SERPENT_NASM")
    # `-w-label-redef-late`: NASM 2.16+ promotes "label changed between
    # passes" to an error by default. We hit this in the dict runtime when
    # forward references force the encoder to pick a different instruction
    # size on the second pass. The final object code is still correct.
    _run(
        [
            nasm,
            "-f",
            nasm_fmt,
            "-w-label-redef-late",
            str(asm_path),
            "-o",
            str(obj_path),
        ]
    )

    # Both targets now use gcc as the linker driver so the C runtime
    # (msvcrt on Windows, libc on Linux) is linked in transparently.
    gcc = _resolve_tool("gcc", override=gcc_path, env_var="SERPENT_GCC")
    # gcc needs to find sibling tools (ld, as, collect2) — when we resolve
    # gcc by absolute path from the bundled MinGW, its directory may not be
    # on PATH, so add it explicitly.
    gcc_dir = str(Path(gcc).parent)

    if bundle_mode == "onedir":
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
            from .runtime.build import build_runtime, _build_dir

            # Ensure the runtime archive is up to date for this target.
            build_runtime(target)
            link_cmd += [
                f"-L{_build_dir()}",
                f"-lserpent_rt_{'win' if target == 'windows' else 'linux'}",
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
    """Produce a `<stem>_onedir/` folder containing the exe and a `lib/`
    sibling with the shared runtime library (and, eventually, one .dll/.so
    per imported user module).

    Returns the final path of the executable inside that folder.
    """
    from .runtime.build import build_runtime_shared

    stem = out_path.with_suffix("")
    bundle_dir = stem.parent / f"{stem.name}_onedir"
    lib_dir = bundle_dir / "lib"
    lib_dir.mkdir(parents=True, exist_ok=True)

    # Build the shared runtime library and copy it into the bundle.
    shared_path = build_runtime_shared(target)
    bundled_shared = lib_dir / shared_path.name
    if bundled_shared.exists():
        bundled_shared.unlink()
    bundled_shared.write_bytes(shared_path.read_bytes())

    exe_path = bundle_dir / out_path.name

    # Link against the shared library by short name. Use rpath/$ORIGIN on
    # Linux so the loader looks in `./lib/` next to the executable.
    short = "serpent_rt_" + ("win" if target == "windows" else "linux")
    link_cmd = [
        gcc,
        str(obj_path),
        "-o",
        str(exe_path),
        f"-L{lib_dir}",
        f"-l{short}",
    ]
    if target == "linux":
        link_cmd += ["-Wl,-rpath,$ORIGIN/lib"]
    _run(link_cmd, extra_path_dirs=[gcc_dir] if gcc_dir else None)

    # On Windows the loader searches the same directory as the .exe, so we
    # also drop a copy of the .dll next to it. (Keeping the one in lib/ too
    # is intentional — that's the canonical location for any future per-
    # module dlls; the side-by-side copy is just so the existing search
    # rules find the runtime without LD_LIBRARY_PATH analogues.)
    if target == "windows":
        side_by_side = bundle_dir / shared_path.name
        if side_by_side.exists():
            side_by_side.unlink()
        side_by_side.write_bytes(shared_path.read_bytes())

    return exe_path


def detect_default_target() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    raise RuntimeError(f"unsupported host platform: {sys.platform}")
