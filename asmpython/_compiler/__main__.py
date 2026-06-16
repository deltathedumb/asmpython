"""CLI entry point: `python -m asmpython source.py [options]` or `asmpython.bat ...`."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from .driver import compile_source, detect_default_target
from .errors import CompileError, MultiSemaError, explain as _explain_code
from .. import __version__


# ── ANSI color helpers ─────────────────────────────────────────────────────────

def _want_color() -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("ASMPYTHON_NO_COLOR"):
        return False
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleMode(  # type: ignore[attr-defined]
                ctypes.windll.kernel32.GetStdHandle(-11), 7)  # type: ignore[attr-defined]
        except Exception:
            return False
    return True


_R  = "\x1b[0m"    # reset
_BD = "\x1b[1m"    # bold
_DM = "\x1b[2m"    # dim
_GN = "\x1b[92m"   # bright green  — option flags
_YL = "\x1b[93m"   # bright yellow — metavars / choices
_CY = "\x1b[96m"   # bright cyan   — example shell commands
_BL = "\x1b[94m"   # bright blue   — section group headers
_MG = "\x1b[95m"   # bright magenta — usage label


def _colorize_help(text: str) -> str:
    """Apply ANSI colors to argparse-formatted help text."""
    lines: list[str] = []
    in_examples = False

    for raw in text.splitlines():
        s = raw.lstrip()

        # Examples / epilog block
        if s.startswith("Examples:"):
            in_examples = True
            lines.append(f"{_BD}{raw}{_R}")
            continue

        if in_examples:
            if s.startswith("asmpython"):
                indent = raw[: len(raw) - len(s)]
                lines.append(f"{indent}{_CY}{s}{_R}")
            elif s.endswith(":") and s:
                indent = raw[: len(raw) - len(s)]
                lines.append(f"{indent}{_DM}{s}{_R}")
            else:
                lines.append(raw)
            continue

        # Argparse section group headers (e.g. "input / output:")
        if re.match(r'^[a-z][a-z 0-9/_-]+:$', s):
            lines.append(f"\n{_BD}{_BL}{raw}{_R}")
            continue

        # "usage:" label
        if s.startswith("usage:"):
            raw = re.sub(r'^(\s*usage:)', f"{_MG}\\1{_R}", raw)

        # Option flags: -x and --xxx
        raw = re.sub(r'(?<!\w)(--?[a-zA-Z][\w-]*)', f"{_GN}\\1{_R}", raw)

        # Metavars: ALL_CAPS (≥2 chars) and {choice,sets}
        raw = re.sub(r'\b([A-Z][A-Z0-9_]{1,})\b', f"{_YL}\\1{_R}", raw)
        raw = re.sub(r'(\{[a-z][a-z0-9,]+\})', f"{_YL}\\1{_R}", raw)

        lines.append(raw)

    return "\n".join(lines)


# ── Argument parser ─────────────────────────────────────────────────────────────

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

  Give the .exe a custom icon (.ico or .png):
      asmpython hello.py --target windows --icon app.ico
      asmpython hello.py --target windows --icon icon.png

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


class _ColorParser(argparse.ArgumentParser):
    """ArgumentParser that colorizes --help output when stdout is a TTY."""

    def print_help(self, file=None) -> None:  # type: ignore[override]
        if file is None:
            file = sys.stdout
        text = self.format_help()
        if _want_color():
            text = _colorize_help(text)
        file.write(text)
        if not text.endswith("\n"):
            file.write("\n")


def _build_parser() -> argparse.ArgumentParser:
    ap = _ColorParser(
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
        help="only run the front-end (lex / parse / sema) and report diagnostics; "
        "no codegen, no toolchain needed. Editor-friendly.",
    )
    build_grp.add_argument(
        "--one-error",
        action="store_true",
        dest="one_error",
        help="stop at the first sema error instead of reporting all of them. "
        "Parse errors always stop early regardless.",
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
    build_grp.add_argument(
        "--icon",
        metavar="PATH",
        type=Path,
        default=None,
        help="embed PATH (.ico or .png) as the executable's icon resource "
        "(--target windows only; PNG auto-converted to ICO via Pillow or "
        "a built-in minimal ICO wrapper; uses windres from the gcc toolchain)",
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
    meta_grp.add_argument(
        "--explain",
        metavar="CODE",
        default=None,
        help="print a detailed description of an error code (e.g. E014, L003, P002) and exit",
    )
    return ap


def _run_check(
    src: str, source_path, *, source_dir, as_json: bool, all_errors: bool = False
) -> int:
    """Front-end-only check (lex / parse / sema). Returns 0 if clean, 1 if any
    diagnostic was found. With `as_json`, prints a JSON array of diagnostics on
    stdout; otherwise prints human-readable formatted errors to stderr.

    The JSON shape is what the VS Code extension consumes:
        [{"phase": "...", "message": "...", "line": N, "col": N}]
    A clean file prints `[]`.
    """
    import json

    from .errors import MultiSemaError
    from .lexer import Lexer
    from .parser import Parser
    from .sema import analyze as sema_analyze

    try:
        tokens = Lexer(src).tokenize()
        module = Parser(tokens).parse()
        sema_analyze(module, source_dir=source_dir, collect_errors=all_errors)
    except MultiSemaError as me:
        if as_json:
            from .errors import _code_label
            diags = []
            for e in me.errors:
                diags.append({
                    "phase": e.phase,
                    "message": e.message,
                    "line": e.pos.line if e.pos else 1,
                    "col": e.pos.col if e.pos else 1,
                    "code": _code_label(e.code) if e.code is not None else None,
                })
            print(json.dumps(diags))
        else:
            print(me.format_all(src, str(source_path)), file=sys.stderr)
        return 1
    except CompileError as e:
        if as_json:
            from .errors import _code_label
            diag = {
                "phase": e.phase,
                "message": e.message,
                "line": e.pos.line if e.pos else 1,
                "col": e.pos.col if e.pos else 1,
                "code": _code_label(e.code) if e.code is not None else None,
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

    if args.explain is not None:
        desc = _explain_code(args.explain)
        if desc:
            print(desc)
            return 0
        print(f"asmpython: unknown error code {args.explain!r}", file=sys.stderr)
        return 1

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
    all_errors = not args.one_error
    if args.check:
        return _run_check(src, args.source, source_dir=args.source.resolve().parent,
                          as_json=args.json, all_errors=all_errors)

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
            icon_path=args.icon,
            all_errors=all_errors,
        )
    except MultiSemaError as me:
        print(me.format_all(src, str(args.source)), file=sys.stderr)
        return 1
    except CompileError as e:
        print(e.format(src, str(args.source)), file=sys.stderr)
        return 1
    except Exception as e:
        print(f"asmpython: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
