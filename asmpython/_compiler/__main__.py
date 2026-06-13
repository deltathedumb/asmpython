"""CLI entry point: `python -m asmpython source.py [options]` or `asmpython.bat ...`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .driver import compile_source, detect_default_target
from .errors import CompileError
from .. import __version__


_USAGE = "asmpython <source.py> [-o <output>] [--target win|linux] [options]"

_DESCRIPTION = """\
Compile a Python source file to a native executable.

  source.py  -->  lex/parse/sema  -->  NASM  -->  .obj / .o  -->  linker  -->  binary

The supported language subset tracks "what 80% of small Python programs look
like": ints, floats, strings (with concat / index / slice / methods), lists,
dicts, classes with single inheritance, exceptions, f-strings, and FFI through
the curated `asmpython/_stdlib/` registry.
"""

_EPILOG = """\
Examples:

  Compile for the host platform (default):
      asmpython hello.py

  Choose output name:
      asmpython hello.py -o build/hello.exe

  Cross-compile to Linux ELF64:
      asmpython hello.py --target linux -o hello

  Inspect the generated assembly without linking:
      asmpython hello.py --emit-asm

  Ship as a folder with the runtime as a shared library:
      asmpython hello.py --onedir

  Emit a shared library instead of an executable:
      asmpython mod.py --type library -o mod.dll

  Keep intermediate .o / .obj for inspection:
      asmpython hello.py --keep

Toolchain auto-discovery searches, in order:
    1.  --nasm / --gcc CLI flags
    2.  $ASMPYTHON_NASM / $ASMPYTHON_GCC env vars
    3.  ./bin/<name>
    4.  ./tools/nasm/nasm and ./tools/mingw64/bin/gcc (dev layout)
    5.  $PATH

If a tool is missing, run `_download-deps.bat --nasm --gcc --python` to
fetch a portable copy of the toolchain (Windows only).
"""


class _AsmPythonHelp(argparse.RawDescriptionHelpFormatter):
    """Wider columns so multi-line `help=` text reads naturally."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=28, width=88)


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="asmpython",
        usage=_USAGE,
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=_AsmPythonHelp,
        add_help=False,
    )

    # Input / output ---------------------------------------------------------
    io_grp = ap.add_argument_group("input / output")
    io_grp.add_argument(
        "source",
        type=Path,
        nargs="?",
        help="Python source file to compile (.py)",
    )
    io_grp.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        type=Path,
        default=None,
        help="output executable path (default: <source stem>[.exe])",
    )

    # Target / output mode ---------------------------------------------------
    build_grp = ap.add_argument_group("target / build mode")
    build_grp.add_argument(
        "--target",
        choices=["linux", "windows", "freestanding", "freestanding16"],
        metavar="{linux,windows,freestanding,freestanding16}",
        default=None,
        help="binary target platform / architecture (default: host platform). "
        "'freestanding' = bare-metal Multiboot1 kernel (.bin), boot with "
        "qemu-system-x86_64 -kernel <out.bin>; 'freestanding16' = BIOS-bootable "
        "disk image (.img) with a 16-bit boot sector, boot with "
        "qemu-system-x86_64 -drive format=raw,file=<out.img>",
    )
    build_grp.add_argument(
        "--type",
        choices=["executable", "library"],
        metavar="{executable,library}",
        default="executable",
        dest="output_type",
        help="output binary kind for the given target: 'executable' (default) "
        "or 'library' (a shared library: .dll on Windows, .so on Linux)",
    )
    build_grp.add_argument(
        "--emit-asm",
        action="store_true",
        help="stop after writing the NASM .asm file (no assemble / link)",
    )
    build_grp.add_argument(
        "--check",
        action="store_true",
        help="only run the front-end (lex / parse / sema) and report the first "
        "diagnostic; no codegen, no toolchain needed. Editor-friendly.",
    )
    build_grp.add_argument(
        "--json",
        action="store_true",
        help="with --check, print diagnostics as a JSON array on stdout "
        "(machine-readable, for editor integration)",
    )
    build_grp.add_argument(
        "--keep",
        action="store_true",
        help="keep the intermediate .o / .obj file after linking",
    )
    build_grp.add_argument(
        "--keep-assembly",
        action="store_true",
        dest="keep_assembly",
        help="keep the intermediate .asm file after assembling",
    )

    # Bundling
    bundle = build_grp.add_mutually_exclusive_group()
    bundle.add_argument(
        "-of",
        "--onefile",
        dest="bundle_mode",
        action="store_const",
        const="onefile",
        help="single statically-linked executable (default)",
    )
    bundle.add_argument(
        "-od",
        "--onedir",
        dest="bundle_mode",
        action="store_const",
        const="onedir",
        help="bundle directory: the exe (.exe/.elf) plus a resources/ "
        "(.resources/ on Linux) folder holding the runtime .dll / .so",
    )
    ap.set_defaults(bundle_mode="onefile")

    build_grp.add_argument(
        "--use-runtime-lib",
        action="store_true",
        help="link the pre-built libasmpython_rt archive instead of inlining "
        "the runtime helpers (smaller .asm; archive built on demand)",
    )

    # Toolchain --------------------------------------------------------------
    tc_grp = ap.add_argument_group("toolchain overrides")
    tc_grp.add_argument(
        "--nasm",
        metavar="PATH",
        type=Path,
        default=None,
        help="explicit path to nasm[.exe] (env: ASMPYTHON_NASM)",
    )
    tc_grp.add_argument(
        "--gcc",
        metavar="PATH",
        type=Path,
        default=None,
        help="explicit path to gcc[.exe] (env: ASMPYTHON_GCC)",
    )

    # Meta -------------------------------------------------------------------
    meta_grp = ap.add_argument_group("information")
    meta_grp.add_argument(
        "-h",
        "--help",
        action="help",
        help="show this help message and exit",
    )
    meta_grp.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"asmpython {__version__}",
        help="show version and exit",
    )
    return ap


def _run_check(src: str, source_path, *, source_dir, as_json: bool) -> int:
    """Front-end-only check (lex / parse / sema). Returns 0 if clean, 1 if a
    diagnostic was found. With `as_json`, prints a JSON array of diagnostics on
    stdout; otherwise prints the human-readable formatted error to stderr.

    The JSON shape (one object per diagnostic — currently at most one, since the
    front-end stops at the first error) is what the VS Code extension consumes:
        [{"phase": "...", "message": "...", "line": N, "col": N}]
    A clean file prints `[]`.
    """
    import json

    from .lexer import Lexer
    from .parser import Parser
    from .sema import analyze as sema_analyze

    try:
        tokens = Lexer(src).tokenize()
        module = Parser(tokens).parse()
        sema_analyze(module, source_dir=source_dir)
    except CompileError as e:
        if as_json:
            diag = {
                "phase": e.phase,
                "message": e.message,
                "line": e.pos.line if e.pos else 1,
                "col": e.pos.col if e.pos else 1,
            }
            print(json.dumps([diag]))
        else:
            print(e.format(src, str(source_path)), file=sys.stderr)
        return 1
    if as_json:
        print("[]")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)

    if args.source is None:
        ap.print_usage(sys.stderr)
        print("asmpython: error: no source file given (try `asmpython --help`)", file=sys.stderr)
        return 2

    if not args.source.exists():
        print(f"asmpython: source file not found: {args.source}", file=sys.stderr)
        return 1

    target = args.target or detect_default_target()

    if args.output is None:
        stem = args.source.with_suffix("")
        if args.output_type == "library":
            args.output = stem.with_suffix(".dll" if target == "windows" else ".so")
        elif target == "freestanding":
            args.output = stem.with_suffix(".bin")
        elif target == "freestanding16":
            args.output = stem.with_suffix(".img")
        else:
            args.output = stem.with_suffix(".exe") if target == "windows" else stem

    src = args.source.read_text(encoding="utf-8")

    # --check: front-end only (lex / parse / sema). Fast, no toolchain.
    # Surfaces the first diagnostic — as JSON with --json, else human-readable.
    if args.check:
        return _run_check(src, args.source, source_dir=args.source.resolve().parent,
                          as_json=args.json)

    # --onedir implies --use-runtime-lib so the codegen emits `extern`
    # references that resolve against the shared runtime library.
    use_runtime_lib = args.use_runtime_lib or args.bundle_mode == "onedir"

    try:
        compile_source(
            src,
            target,
            args.output,
            emit_asm_only=args.emit_asm,
            keep_intermediates=args.keep,
            keep_assembly=args.keep_assembly,
            use_runtime_lib=use_runtime_lib,
            nasm_path=args.nasm,
            gcc_path=args.gcc,
            bundle_mode=args.bundle_mode,
            source_dir=args.source.resolve().parent,
            entry_path=args.source.resolve(),
            output_type=args.output_type,
        )
    except CompileError as e:
        print(e.format(src, str(args.source)), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"asmpython: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
