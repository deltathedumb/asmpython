"""Build asmpython's runtime archive for one or both targets.

Usage (from repo root):

    python -m asmpython._runtime.build               # build for the host
    python -m asmpython._runtime.build --all         # both linux + windows

Produces:

    asmpython/_runtime/_build/libasmpython_rt_linux.a
    asmpython/_runtime/_build/libasmpython_rt_win.a

The archive contains every `_runtime_*` symbol plus the scratch buffers
`itoa_str_buf` and `input_buf`. User programs link against it via
`gcc <prog>.obj -L<_runtime/_build> -lasmpython_rt_<target>`.
"""

from __future__ import annotations

import argparse
import hashlib
import inspect
import shutil
import subprocess
import sys
from pathlib import Path

from .._compiler import ast_nodes as A
from .._targets import get_target


_TARGETS = {
    "linux": (get_target("linux"), "elf64", "libasmpython_rt_linux.a"),
    "windows": (get_target("windows"), "win64", "libasmpython_rt_win.a"),
    # x86-32 (i386) Linux -- the legacy runtime port is PARTIAL (see
    # target_x86_32_linux.py's own module docstring and its
    # _UNPORTED_RUNTIME_HELPERS list): exception handling and the dict/
    # hash/sort core are done and verified; the string runtime, general
    # list runtime, several dict/list helpers, console/print, and the
    # GUI/audio/net/threading asmlib layer are not yet ported. A program
    # that only uses what IS ported builds and runs correctly; one that
    # calls an unported primitive gets a real, clear NASM/linker
    # undefined-symbol error at build time, not a silent wrong answer.
    "x86_32_linux": (get_target("x86_32_linux"), "elf32",
                     "libasmpython_rt_x86_32_linux.a"),
}

# Shared-library outputs, keyed by the same target name. The build_shared
# path uses these instead of the static archive for --onedir bundles.
_SHARED_TARGETS = {
    "linux": "libasmpython_rt_linux.so",
    "windows": "libasmpython_rt_win.dll",
}


def _build_dir() -> Path:
    return Path(__file__).resolve().parent / "_build"


def _source_paths(target: str) -> list[Path]:
    """The files whose CONTENTS determine the runtime .asm.

    The target's own source is found from the loaded class rather than by
    guessing a path. A hard-coded path that stops existing hashes nothing and
    silently stops invalidating -- the exact stale-archive failure the digest
    below exists to prevent -- and it would have to be edited again the next
    time targets move, or be wrong for a target registered from outside.
    """
    compiler_dir = Path(__file__).resolve().parent.parent / "_compiler"
    return [
        compiler_dir / "codegen.py",
        Path(inspect.getfile(get_target(target))).resolve(),
        Path(__file__).resolve(),
    ]


def _source_digest(target: str) -> str:
    """Hash of the sources that determine the runtime .asm.

    CONTENT, not mtime. The mtime check this replaces silently served a stale
    archive whenever a source was restored with an older-or-equal timestamp --
    `git stash pop`, `git checkout`, and any edit landing inside the archive's
    mtime granularity all do that. The failure is invisible in the worst
    possible way: the build prints nothing, exits 0, and every test that
    follows measures the OLD runtime while appearing to test the new one. That
    cost a full day's verification here -- a stress run, a corpus run, and a
    set of conclusions drawn from a library that never contained the change
    under test.
    """
    h = hashlib.sha256()
    for path in _source_paths(target):
        h.update(path.read_bytes())
    return h.hexdigest()


def _stamp_path(archive_path: Path) -> Path:
    return archive_path.with_suffix(archive_path.suffix + ".srcdigest")


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


def runtime_object_path(target: str) -> Path:
    """The single relocatable object build_runtime() assembles before
    archiving it (the whole runtime is one NASM translation unit -- see
    generate_runtime_only()). Linkers that want raw object bytes directly
    (e.g. the builtin PE linker, which has no notion of .a archives) use
    this instead of the .a build_runtime() returns."""
    if target not in _TARGETS:
        raise ValueError(f"unknown target {target!r}")
    _cls, _fmt, archive_name = _TARGETS[target]
    obj_suffix = ".obj" if target == "windows" else ".o"
    return _build_dir() / archive_name.replace(".a", obj_suffix)


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

    digest = _source_digest(target)
    stamp = _stamp_path(archive_path)
    if archive_path.exists() and not force:
        # Rebuild unless the archive was built from exactly these sources.
        if stamp.exists() and stamp.read_text(encoding="utf-8").strip() == digest:
            return archive_path

    # INVALIDATE FIRST. If assembling or archiving fails below, there must be
    # no stamp left claiming the artifacts match some source state -- the
    # .asm has already been overwritten by then, so the archive and the stamp
    # would disagree with it. Worse, reverting the source afterwards makes the
    # digest match the OLD stamp again, and the stale artifact is then served
    # forever. That exact sequence happened here: a transient assembler
    # failure, a revert, and a build that silently kept the wrong runtime.
    stamp.unlink(missing_ok=True)

    # Codegen the runtime .asm
    gen = cls(_empty_module(), use_runtime_lib=False)
    asm_path.write_text(gen.generate_runtime_only(), encoding="utf-8")
    print(f"wrote {asm_path}")

    # Assemble
    nasm = _which("nasm")
    _run([
        nasm,
        "-f",
        nasm_fmt,
        "-w-label-redef-late",
        str(asm_path),
        "-o",
        str(obj_path),
    ])

    # Archive with ar.
    if archive_path.exists():
        archive_path.unlink()
    ar = _which("ar")
    _run([ar, "rcs", str(archive_path), str(obj_path)])
    # Record WHAT this archive was built from. Written only after the build
    # succeeds, so a failed build leaves the old stamp and the next run
    # rebuilds rather than trusting a half-made archive.
    stamp.write_text(digest, encoding="utf-8")
    print(f"wrote {archive_path}")
    return archive_path


_ABI_SHIM_TARGETS = {
    # target -> (source filename, nasm output format, object filename)
    "windows": ("abi_shims.asm", "win64", "abi_shims.obj"),
    "linux": ("abi_shims_linux_bundle.asm", "elf64", "abi_shims_linux.o"),
}


def build_abi_shims(target: str, *, force: bool = False) -> Path:
    """Assemble the ABI shim layer for the built-in x86-64 SSA pipeline.

    Linux uses ``abi_shims_linux_bundle.asm`` so target-neutral dynamic-loader
    names can be translated to ``dlopen``/``dlsym`` alongside the ordinary
    runtime ABI shims. Cached objects are rebuilt when either the bundle or
    its included base shim source changes.
    """
    if target not in _ABI_SHIM_TARGETS:
        have = ", ".join(sorted(_ABI_SHIM_TARGETS))
        raise ValueError(f"unknown target {target!r} for build_abi_shims (have: {have})")
    src_name, nasm_fmt, obj_name = _ABI_SHIM_TARGETS[target]
    src_path = Path(__file__).resolve().parent / src_name
    source_paths = [src_path]
    if target == "linux":
        source_paths.append(src_path.parent / "abi_shims_linux.asm")
    out_dir = _build_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    obj_path = out_dir / obj_name

    if obj_path.exists() and not force:
        newest_source = max(path.stat().st_mtime for path in source_paths)
        if obj_path.stat().st_mtime >= newest_source:
            return obj_path

    nasm = _which("nasm")
    include_dir = f"-I{src_path.parent}/"
    _run([
        nasm,
        include_dir,
        "-f",
        nasm_fmt,
        "-w-label-redef-late",
        str(src_path),
        "-o",
        str(obj_path),
    ])
    print(f"wrote {obj_path}")
    return obj_path


# Windows-only for now (threading_shims.asm is Win32-thread-API-based;
# a Linux/pthread equivalent hasn't been written -- 178_threading_stubs.py/
# 235_threading_module.py only ever ran on windows this session anyway).
_THREADING_SHIM_TARGETS = {
    "windows": ("threading_shims.asm", "win64", "threading_shims.obj"),
}


def build_threading_shims(target: str, *, force: bool = False) -> Path:
    """Assemble the threading ABI shim layer -- kept as a SEPARATE object
    from build_abi_shims's (always-linked) output specifically because
    _threading_trampoline references _threading_bootstrap, a real user-
    program-level function that only exists in a program that actually imports
    threading. Declaring that extern in the always-linked object broke every
    program's link step (an unresolved relocation regardless of whether the
    referencing code path runs) -- see threading_shims.asm's own header
    comment. driver.py only appends this object to the link set when the
    compiled program actually needs it."""
    if target not in _THREADING_SHIM_TARGETS:
        have = ", ".join(sorted(_THREADING_SHIM_TARGETS))
        raise ValueError(f"unknown target {target!r} for build_threading_shims (have: {have})")
    src_name, nasm_fmt, obj_name = _THREADING_SHIM_TARGETS[target]
    src_path = Path(__file__).resolve().parent / src_name
    out_dir = _build_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    obj_path = out_dir / obj_name

    if obj_path.exists() and not force:
        if obj_path.stat().st_mtime >= src_path.stat().st_mtime:
            return obj_path

    nasm = _which("nasm")
    _run([nasm, "-f", nasm_fmt, "-w-label-redef-late", str(src_path), "-o", str(obj_path)])
    print(f"wrote {obj_path}")
    return obj_path


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

    digest = _source_digest(target)
    stamp = _stamp_path(shared_path)
    if shared_path.exists() and not force:
        # Same content-hash check as the static archive.
        if stamp.exists() and stamp.read_text(encoding="utf-8").strip() == digest:
            return shared_path

    stamp.unlink(missing_ok=True)   # invalidate first; see build_runtime

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
    _run([
        nasm,
        "-f",
        nasm_fmt,
        "-w-label-redef-late",
        str(asm_path),
        "-o",
        str(obj_path),
    ])

    # Link as a shared library with gcc. On Windows the resulting .dll has
    # to export every `_runtime_*` symbol; for now we rely on
    # `-Wl,--export-all-symbols` which exports everything (small surface,
    # acceptable for the runtime).
    gcc = _which("gcc")
    link = [gcc, "-shared", str(obj_path), "-o", str(shared_path)]
    if target == "windows":
        link += ["-Wl,--export-all-symbols"]
    _run(link)
    stamp.write_text(digest, encoding="utf-8")
    print(f"wrote {shared_path}")
    return shared_path


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="asmpython._runtime.build")
    ap.add_argument(
        "--target",
        choices=sorted(_TARGETS),
        default=None,
        help="target (default: host)",
    )
    ap.add_argument(
        "--all", action="store_true", help="build all targets (linux + windows)"
    )
    ap.add_argument(
        "--force", action="store_true", help="rebuild even if archive is up to date"
    )
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
