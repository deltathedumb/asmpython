"""Build serpent's runtime archive for one or both targets.

Usage (from repo root):

    python -m serpent.runtime.build               # build for the host
    python -m serpent.runtime.build --all         # both linux + windows

Produces:

    serpent/runtime/_build/libserpent_rt_linux.a
    serpent/runtime/_build/libserpent_rt_win.a

The archive contains every `_runtime_*` symbol plus the scratch buffers
`itoa_str_buf` and `input_buf`. User programs link against it via
`gcc <prog>.obj -L<runtime/_build> -lserpent_rt_<target>`.
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
    "linux":   (LinuxCodegen,   "elf64", "libserpent_rt_linux.a"),
    "windows": (WindowsCodegen, "win64", "libserpent_rt_win.a"),
}

# Shared-library outputs, keyed by the same target name. The build_shared
# path uses these instead of the static archive for --onedir bundles.
_SHARED_TARGETS = {
    "linux":   "libserpent_rt_linux.so",
    "windows": "libserpent_rt_win.dll",
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


def build_runtime_shared(target: str, *, force: bool = False) -> Path:
    """Build the runtime as a shared library (.dll/.so) for --onedir bundles.

    Reuses the same NASM .asm output as the static archive but invokes gcc
    with `-shared` to produce a load-at-runtime artifact. Returns the
    resulting library path.
    """
    if target not in _TARGETS:
        raise ValueError(f"unknown target {target!r}")
    cls, nasm_fmt, archive_name = _TARGETS[target]
    out_dir = _build_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    shared_name = _SHARED_TARGETS[target]
    shared_path = out_dir / shared_name
    asm_path = out_dir / shared_name.replace(".dll", ".asm").replace(".so", ".asm")
    obj_suffix = ".obj" if target == "windows" else ".o"
    obj_path = out_dir / (
        shared_name.replace(".dll", obj_suffix).replace(".so", obj_suffix)
    )

    if shared_path.exists() and not force:
        # Same staleness check as the static archive: rebuild if any source
        # file is newer.
        newest_src = max(
            (Path(__file__).resolve().parent.parent / f).stat().st_mtime
            for f in ("codegen.py", f"target_{target}.py", "runtime/build.py")
        )
        if shared_path.stat().st_mtime >= newest_src:
            return shared_path

    # Codegen the runtime .asm. We pass `use_runtime_lib=False` so the
    # codegen emits every helper inline -- that's exactly what we want here,
    # since this .asm is the body of the shared library.
    gen = cls(_empty_module(), use_runtime_lib=False)
    asm_path.write_text(gen.generate_runtime_only(), encoding="utf-8")
    print(f"wrote {asm_path}")

    # Assemble with PIC enabled where applicable. NASM doesn't have a
    # `-fPIC` flag; the emitted code uses `rel`-form addressing for rodata
    # references which is already position-independent on x86-64.
    nasm = _which("nasm")
    _run([nasm, "-f", nasm_fmt, "-w-label-redef-late",
          str(asm_path), "-o", str(obj_path)])

    # Link as a shared library with gcc. On Windows the resulting .dll has
    # to export every `_runtime_*` symbol; for now we rely on
    # `-Wl,--export-all-symbols` which exports everything (small surface,
    # acceptable for the runtime).
    gcc = _which("gcc")
    link = [gcc, "-shared", str(obj_path), "-o", str(shared_path)]
    if target == "windows":
        link += ["-Wl,--export-all-symbols"]
    _run(link)
    print(f"wrote {shared_path}")
    return shared_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="serpent.runtime.build")
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
