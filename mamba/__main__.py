"""CLI: python -m mamba source.py [options]"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .driver import compile_source, detect_default_target
from .errors import CompileError


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mamba")
    ap.add_argument("source", type=Path, help="input .py file")
    ap.add_argument("-o", "--output", type=Path, default=None,
                    help="output executable path (default: <source-stem>[.exe])")
    ap.add_argument("--target", choices=["linux", "windows"], default=None,
                    help="binary target (default: host platform)")
    ap.add_argument("--emit-asm", action="store_true",
                    help="stop after writing the .asm file")
    ap.add_argument("--keep", action="store_true",
                    help="keep intermediate .o/.obj files")
    ap.add_argument("--use-runtime-lib", action="store_true",
                    help="link the pre-built libmamba_rt archive instead of "
                         "inlining the runtime helpers into the program "
                         "(smaller .asm; builds the archive on demand)")
    ap.add_argument("--nasm", type=Path, default=None,
                    help="path to the nasm executable (overrides PATH lookup "
                         "and the MAMBA_NASM env var)")
    ap.add_argument("--gcc", type=Path, default=None,
                    help="path to the gcc executable (overrides PATH lookup "
                         "and the MAMBA_GCC env var)")
    args = ap.parse_args(argv)

    target = args.target or detect_default_target()

    if args.output is None:
        stem = args.source.with_suffix("")
        args.output = stem.with_suffix(".exe") if target == "windows" else stem

    src = args.source.read_text(encoding="utf-8")
    try:
        compile_source(
            src, target, args.output,
            emit_asm_only=args.emit_asm,
            keep_intermediates=args.keep,
            use_runtime_lib=args.use_runtime_lib,
        )
    except CompileError as e:
        print(e.format(src, str(args.source)), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"mamba: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
