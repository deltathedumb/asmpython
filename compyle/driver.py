"""End-to-end driver: source.py -> .asm -> .o -> executable."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import CompileError
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


def _run(cmd: list[str]) -> None:
    print("$", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
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
) -> BuildResult:
    tokens = Lexer(src).tokenize()
    module = Parser(tokens).parse()
    sema_analyze(module)

    if target == "linux":
        gen = LinuxCodegen(module)
        nasm_fmt = "elf64"
        obj_suffix = ".o"
    elif target == "windows":
        gen = WindowsCodegen(module)
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

    nasm = _which_required("nasm")
    _run([nasm, "-f", nasm_fmt, str(asm_path), "-o", str(obj_path)])

    # Both targets now use gcc as the linker driver so the C runtime
    # (msvcrt on Windows, libc on Linux) is linked in transparently.
    gcc = _which_required("gcc")
    _run([gcc, str(obj_path), "-o", str(exe_path)])

    if not keep_intermediates:
        try:
            obj_path.unlink()
        except OSError:
            pass

    print(f"wrote {exe_path}")
    return BuildResult(asm_path=asm_path, obj_path=obj_path, exe_path=exe_path)


def detect_default_target() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "win32":
        return "windows"
    raise RuntimeError(f"unsupported host platform: {sys.platform}")
