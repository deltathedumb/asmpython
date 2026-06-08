"""CLI entry point: `python -m serpent source.py [options]` or `serpent.bat ...`."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .driver import compile_source, detect_default_target
from .errors import CompileError


__version__ = "0.3.0"


_USAGE = "serpent <source.py> [-o <output>] [--target win|linux] [options]"

_DESCRIPTION = """\
Compile a Python source file to a native executable.

  source.py  -->  lex/parse/sema  -->  NASM  -->  .obj / .o  -->  linker  -->  binary

The supported language subset tracks "what 80% of small Python programs look
like": ints, floats, strings (with concat / index / slice / methods), lists,
dicts, classes with single inheritance, exceptions, f-strings, and FFI through
the curated `serpent/stdlib/` registry.
"""

_EPILOG = """\
Examples:

  Compile for the host platform (default):
      serpent hello.py

  Choose output name:
      serpent hello.py -o build/hello.exe

  Cross-compile to Linux ELF64:
      serpent hello.py --target linux -o hello

  Inspect the generated assembly without linking:
      serpent hello.py --emit-asm

  Ship as a folder with the runtime as a shared library:
      serpent hello.py --onedir

  Keep intermediate .o / .obj for inspection:
      serpent hello.py --keep

Toolchain auto-discovery searches, in order:
    1.  --nasm / --gcc CLI flags
    2.  $SERPENT_NASM / $SERPENT_GCC env vars
    3.  ./bin/<name>
    4.  ./tools/nasm/nasm and ./tools/mingw64/bin/gcc (dev layout)
    5.  $PATH

If a tool is missing, run `_download-deps.bat --nasm --gcc --python` to
fetch a portable copy of the toolchain (Windows only).
"""


class _SerpentHelp(argparse.RawDescriptionHelpFormatter):
    """Wider columns so multi-line `help=` text reads naturally."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=28, width=88)


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="serpent",
        usage=_USAGE,
        description=_DESCRIPTION,
        epilog=_EPILOG,
        formatter_class=_SerpentHelp,
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
        choices=["linux", "windows"],
        metavar="{linux,windows}",
        default=None,
        help="binary target platform (default: host platform)",
    )
    build_grp.add_argument(
        "--emit-asm",
        action="store_true",
        help="stop after writing the NASM .asm file (no assemble / link)",
    )
    build_grp.add_argument(
        "--keep",
        action="store_true",
        help="keep the intermediate .o / .obj file after linking",
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
        help="executable plus sibling lib/ with the runtime as .dll / .so",
    )
    ap.set_defaults(bundle_mode="onefile")

    build_grp.add_argument(
        "--use-runtime-lib",
        action="store_true",
        help="link the pre-built libserpent_rt archive instead of inlining "
        "the runtime helpers (smaller .asm; archive built on demand)",
    )

    # Toolchain --------------------------------------------------------------
    tc_grp = ap.add_argument_group("toolchain overrides")
    tc_grp.add_argument(
        "--nasm",
        metavar="PATH",
        type=Path,
        default=None,
        help="explicit path to nasm[.exe] (env: SERPENT_NASM)",
    )
    tc_grp.add_argument(
        "--gcc",
        metavar="PATH",
        type=Path,
        default=None,
        help="explicit path to gcc[.exe] (env: SERPENT_GCC)",
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
        version=f"serpent {__version__}",
        help="show version and exit",
    )
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = _build_parser()
    args = ap.parse_args(argv)

    if args.source is None:
        ap.print_usage(sys.stderr)
        print("serpent: error: no source file given (try `serpent --help`)", file=sys.stderr)
        return 2

    if not args.source.exists():
        print(f"serpent: source file not found: {args.source}", file=sys.stderr)
        return 1

    target = args.target or detect_default_target()

    if args.output is None:
        stem = args.source.with_suffix("")
        args.output = stem.with_suffix(".exe") if target == "windows" else stem

    # --onedir implies --use-runtime-lib so the codegen emits `extern`
    # references that resolve against the shared runtime library.
    use_runtime_lib = args.use_runtime_lib or args.bundle_mode == "onedir"

    src = args.source.read_text(encoding="utf-8")
    try:
        compile_source(
            src,
            target,
            args.output,
            emit_asm_only=args.emit_asm,
            keep_intermediates=args.keep,
            use_runtime_lib=use_runtime_lib,
            nasm_path=args.nasm,
            gcc_path=args.gcc,
            bundle_mode=args.bundle_mode,
        )
    except CompileError as e:
        print(e.format(src, str(args.source)), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"serpent: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
