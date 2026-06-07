"""Build mamba's runtime archive for one or both targets.

Usage (from repo root):

    python -m mamba.runtime.build               # build for the host
    python -m mamba.runtime.build --all         # both linux + windows

Produces:

    mamba/runtime/_build/libmamba_rt_linux.a
    mamba/runtime/_build/libmamba_rt_win.a

The archive contains every `_runtime_*` symbol plus the scratch buffers
`itoa_str_buf` and `input_buf`. User programs link against it via
`gcc <prog>.obj -L<runtime/_build> -lmamba_rt_<target>`.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from .. import ast_nodes as A
from ..target_linux import LinuxCodegen
from ..target_windows import WindowsCodegen


_TARGETS = {
    "linux":   (LinuxCodegen,   "elf64", "libmamba_rt_linux.a"),
    "windows": (WindowsCodegen, "win64", "libmamba_rt_win.a"),
}


def _build_dir() -> Path:
    return Path(__file__).resolve().parent / "_build"


def _empty_module() -> A.Module:
    """A Module with no user code so codegen emits only runtime helpers."""
    return A.Module(funcs=[], body=[], classes=[])


def _which(name: str) -> str:
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


def build_runtime(target: str, *, force: bool = False) -> Path:
    """Build the runtime archive for one target. Returns the .a/.lib path."""
    if target not in _TARGETS:
        raise ValueError(f"unknown target {target!r}")
    cls, nasm_fmt, archive_name = _TARGETS[target]
    out_dir = _build_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / archive_name
    asm_path = out_dir / archive_name.replace(".a", ".asm")
    obj_suffix = ".obj" if target == "windows" else ".o"
    obj_path = out_dir / archive_name.replace(".a", obj_suffix)

    if archive_path.exists() and not force:
        # Check timestamps: rebuild if any source file is newer.
        newest_src = max(
            (Path(__file__).resolve().parent.parent / f).stat().st_mtime
            for f in ("codegen.py", f"target_{target}.py", "runtime/build.py")
        )
        if archive_path.stat().st_mtime >= newest_src:
            return archive_path

    # Codegen the runtime .asm
    gen = cls(_empty_module(), use_runtime_lib=False)
    asm_path.write_text(gen.generate_runtime_only(), encoding="utf-8")
    print(f"wrote {asm_path}")

    # Assemble
    nasm = _which("nasm")
    _run([nasm, "-f", nasm_fmt, "-w-label-redef-late",
          str(asm_path), "-o", str(obj_path)])

    # Archive with ar.
    if archive_path.exists():
        archive_path.unlink()
    ar = _which("ar")
    _run([ar, "rcs", str(archive_path), str(obj_path)])
    print(f"wrote {archive_path}")
    return archive_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mamba.runtime.build")
    ap.add_argument("--target", choices=sorted(_TARGETS), default=None,
                    help="target (default: host)")
    ap.add_argument("--all", action="store_true",
                    help="build all targets (linux + windows)")
    ap.add_argument("--force", action="store_true",
                    help="rebuild even if archive is up to date")
    args = ap.parse_args(argv)

    if args.all:
        targets = sorted(_TARGETS)
    elif args.target is not None:
        targets = [args.target]
    else:
        targets = ["windows" if sys.platform == "win32" else "linux"]

    for t in targets:
        build_runtime(t, force=args.force)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
