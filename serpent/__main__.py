"""CLI: python -m serpent source.py [options]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .driver import compile_source, detect_default_target
from .errors import CompileError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="serpent")
    ap.add_argument("source", type=Path, help="input .py file")
    ap.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="output executable path (default: <source-stem>[.exe])",
    )
    ap.add_argument(
        "--target",
        choices=["linux", "windows"],
        default=None,
        help="binary target (default: host platform)",
    )
    ap.add_argument(
        "--emit-asm", action="store_true", help="stop after writing the .asm file"
    )
    ap.add_argument(
        "--keep", action="store_true", help="keep intermediate .o/.obj files"
    )
    ap.add_argument(
        "--use-runtime-lib",
        action="store_true",
        help="link the pre-built libserpent_rt archive instead of "
        "inlining the runtime helpers into the program "
        "(smaller .asm; builds the archive on demand)",
    )
    # Bundling mode. Mutually exclusive: --onefile is the default monolithic
    # build; --onedir produces an executable plus a sibling library folder
    # with the runtime (and, later, every user-imported module) shipped as
    # shared libraries (.dll on Windows, .so on Linux).
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument(
        "-of",
        "--onefile",
        dest="bundle_mode",
        action="store_const",
        const="onefile",
        help="ship a single executable with the runtime statically linked "
        "(default)",
    )
    mode.add_argument(
        "-od",
        "--onedir",
        dest="bundle_mode",
        action="store_const",
        const="onedir",
        help="ship the executable plus a sibling lib/ folder of shared "
        "libraries (the serpent runtime as .dll/.so today; per-module .dll/.so "
        "for user imports once cross-file compilation lands)",
    )
    ap.set_defaults(bundle_mode="onefile")
    # Hidden tool-override flags. Useful for the portable archive where the
    # bundled toolchain lives next to serpent.bat; not something most users
    # need to touch, so they're suppressed from --help.
    ap.add_argument("--nasm", type=Path, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--gcc", type=Path, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    target = args.target or detect_default_target()

    if args.output is None:
        stem = args.source.with_suffix("")
        args.output = stem.with_suffix(".exe") if target == "windows" else stem

    # --onedir implies --use-runtime-lib (shared library), but as a separate
    # shared-build path. Force the flag on so the codegen emits `extern`
    # references rather than inlining runtime helpers.
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
