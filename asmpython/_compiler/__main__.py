"""CLI entry point: `python -m asmpython <command> ...` or `asmpython.bat ...`.

Subcommands:

  asmpython build <source.py | project.json> [options]
  asmpython package install|uninstall <name | project.json> [options]
  asmpython pypi install|uninstall|list <name | project.json> [options]
  asmpython project new [name] [options]

For backward compatibility, a bare `asmpython <source.py> [options]` (no
subcommand) is shorthand for `asmpython build <source.py> [options]` — see
`_preprocess_argv`.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

from . import native_libraries as _native_libraries
from .driver import compile_source, compile_targets, detect_default_target
from .errors import CompileError, MultiSemaError, explain as _explain_code
from .packages import (
    PackageError,
    find_project_type,
    install_package,
    run_project_init_script,
    uninstall_package,
)
from .pypi import (
    PypiError,
    install_pypi_package,
    list_pypi_packages,
    uninstall_pypi_package,
)
from .project import (
    ProjectConfig,
    ProjectError,
    find_default_project,
    init_project,
    load_project,
)
from .. import __version__


# ── ANSI color helpers ─────────────────────────────────────────────────────────


def _want_color() -> bool:
    if os.environ.get("NO_COLOR") or os.environ.get("ASMPYTHON_NO_COLOR"):
        return False
    if True:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleMode(  # type: ignore[attr-defined]
                ctypes.windll.kernel32.GetStdHandle(-11), 7
            )  # type: ignore[attr-defined]
        except Exception:
            return False
    return True


_R = "\x1b[0m"  # reset
_BD = "\x1b[1m"  # bold
_DM = "\x1b[2m"  # dim
_GN = "\x1b[92m"  # bright green  — option flags
_YL = "\x1b[93m"  # bright yellow — metavars / choices
_CY = "\x1b[96m"  # bright cyan   — example shell commands
_BL = "\x1b[94m"  # bright blue   — section group headers
_MG = "\x1b[95m"  # bright magenta — usage label


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
        if re.match(r"^[a-z][a-z 0-9/_-]+:$", s):
            lines.append(f"\n{_BD}{_BL}{raw}{_R}")
            continue

        # "usage:" label
        if s.startswith("usage:"):
            raw = re.sub(r"^(\s*usage:)", f"{_MG}\\1{_R}", raw)

        # Option flags: -x and --xxx
        raw = re.sub(r"(?<!\w)(--?[a-zA-Z][\w-]*)", f"{_GN}\\1{_R}", raw)

        # Metavars: ALL_CAPS (≥2 chars) and {choice,sets}
        raw = re.sub(r"\b([A-Z][A-Z0-9_]{1,})\b", f"{_YL}\\1{_R}", raw)
        raw = re.sub(r"(\{[a-z][a-z0-9,]+\})", f"{_YL}\\1{_R}", raw)

        lines.append(raw)

    return "\n".join(lines)


# ── Shared parser plumbing ──────────────────────────────────────────────────────

_VALID_TARGETS = {"linux", "windows", "freestanding", "freestanding16"}


def _parse_targets(val: str) -> list[str]:
    targets = [t.strip() for t in val.replace(",", " ").split()]
    for t in targets:
        if t not in _VALID_TARGETS:
            raise ValueError(
                f"invalid target {t!r}; choose from {', '.join(sorted(_VALID_TARGETS))}"
            )
    if not targets:
        raise ValueError("--target requires at least one target")
    return targets


def _target_out(stem: Path, target: str, output_type: str) -> Path:
    """Derive output path for *target* from a base *stem* (no extension)."""
    if output_type == "library":
        return stem.with_suffix(".dll" if target == "windows" else ".so")
    if target == "freestanding":
        return stem.with_suffix(".bin")
    if target == "freestanding16":
        return stem.with_suffix(".img")
    if target == "windows":
        return stem.with_suffix(".exe")
    return stem  # linux: no extension


class _AsmPythonHelp(argparse.RawDescriptionHelpFormatter):
    """Wider columns so multi-line `help=` text reads naturally."""

    def __init__(self, prog: str) -> None:
        super().__init__(prog, max_help_position=28, width=88)


class _ColorParser(argparse.ArgumentParser):
    """ArgumentParser that colorizes --help output when stdout is a TTY."""

    def print_help(self) -> None:  # type: ignore[override]
        text = self.format_help()
        if _want_color():
            text = _colorize_help(text)
        print(text)


# ── Top-level parser + subcommands ──────────────────────────────────────────────

_TOP_DESCRIPTION = """\
Compile Python source to a native executable — no VM, no interpreter, no
runtime dependencies.

  asmpython <source.py>                       shorthand for `build <source.py>`
  asmpython build <source.py | project.json>  compile a file or a whole project
  asmpython package install|uninstall <name | project.json>
                                               manage native runtime libraries (e.g. sdl2)
  asmpython project new [name]                scaffold a new project.json + entry file
"""

_TOP_EPILOG = """\
Examples:

  Compile a single file for the host platform (default):
      asmpython hello.py

  Same thing, spelled out:
      asmpython build hello.py --target linux -o hello

  Build a whole project from its manifest:
      asmpython build mygame/project.json

  Install SDL2 (vendored offline, or from the registry) into ./libs/:
      asmpython package install sdl2

  Install every package a project depends on:
      asmpython package install mygame/project.json

  Scaffold a new project:
      asmpython project new mygame

Run `asmpython <command> --help` for that command's full option list.
"""


def _build_top_parser() -> argparse.ArgumentParser:
    ap = _ColorParser(
        prog="asmpython",
        usage="asmpython <build|package|pypi|project> ... (or just `asmpython <source.py>`)",
        description=_TOP_DESCRIPTION,
        epilog=_TOP_EPILOG,
        formatter_class=_AsmPythonHelp,
        add_help=False,
    )

    meta_grp = ap.add_argument_group("information")
    meta_grp.add_argument("-h", "--help", action="help", help="show this help message and exit")
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

    subparsers = ap.add_subparsers(dest="command")
    _add_build_subparser(subparsers)
    _add_package_subparser(subparsers)
    _add_pypi_subparser(subparsers)
    _add_pyinbin_subparser(subparsers)
    _add_project_subparser(subparsers)
    return ap


# ── `build` subcommand ──────────────────────────────────────────────────────────

_BUILD_DESCRIPTION = """\
Compile a Python source file (or a project.json manifest) to a native executable.

  source.py  -->  lex/parse/sema  -->  NASM  -->  .obj / .o  -->  linker  -->  binary

The supported language subset tracks "what 80% of small Python programs look
like": ints, floats, strings (with concat / index / slice / methods), lists,
dicts, classes with single inheritance, exceptions, f-strings, and FFI through
the curated `asmpython/_stdlib/` registry.

When given a project.json (see `asmpython project new`), every option below
falls back to that file's matching field if the flag itself isn't passed —
the CLI flag always wins when given.
"""

_BUILD_EPILOG = """\
Examples:

  Compile for the host platform (default):
      asmpython build hello.py

  Choose output name:
      asmpython build hello.py -o build/hello.exe

  Cross-compile to Linux ELF64:
      asmpython build hello.py --target linux -o hello

  Build a whole project from its manifest:
      asmpython build mygame/project.json

  Inspect the generated assembly without linking:
      asmpython build hello.py --emit-asm

  Ship as a folder with the runtime as a shared library:
      asmpython build hello.py --onedir

  Emit a shared library instead of an executable:
      asmpython build mod.py --type library -o mod.dll

  Keep intermediate .o / .obj for inspection:
      asmpython build hello.py --keep

  Give the .exe a custom icon (.ico or .png):
      asmpython build hello.py --target windows --icon app.ico
      asmpython build hello.py --target windows --icon icon.png

Toolchain auto-discovery searches, in order:
    1.  --nasm / --gcc CLI flags
    2.  $ASMPYTHON_NASM / $ASMPYTHON_GCC env vars
    3.  ./bin/<name>
    4.  ./tools/nasm/nasm and ./tools/mingw64/bin/gcc (dev layout)
    5.  $PATH

If a tool is missing, run `_download-deps.bat --nasm --gcc --python` to
fetch a portable copy of the toolchain (Windows only).
"""


def _add_build_subparser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    ap = subparsers.add_parser(
        "build",
        usage="asmpython build <source.py|project.json> [-o <output>] [--target win|linux[,...]] [options]",
        description=_BUILD_DESCRIPTION,
        epilog=_BUILD_EPILOG,
        formatter_class=_AsmPythonHelp,
        add_help=False,
    )

    # Input / output ---------------------------------------------------------
    io_grp = ap.add_argument_group("input / output")
    io_grp.add_argument(
        "source",
        type=Path,
        nargs="?",
        help="Python source file (.py) or project manifest (project.json) to compile",
    )
    io_grp.add_argument(
        "-o",
        "--output",
        metavar="PATH",
        type=Path,
        default=None,
        help="output executable path (default: <source stem>[.exe], or the "
        "project's own `output` field for a project.json source)",
    )

    # Target / output mode ---------------------------------------------------
    build_grp = ap.add_argument_group("target / build mode")
    build_grp.add_argument(
        "--target",
        metavar="{linux,windows,freestanding,freestanding16}[,...]",
        default=None,
        help="target platform(s), comma-separated for multi-target builds "
        "(e.g. --target windows,linux). Default: the project's `target` "
        "field, else the host platform. Multi-target shares lex/parse/sema "
        "and runs a separate codegen+link per target. 'freestanding' = "
        "bare-metal Multiboot1 kernel (.bin); 'freestanding16' = "
        "BIOS-bootable disk image (.img).",
    )
    build_grp.add_argument(
        "--type",
        choices=["executable", "library"],
        metavar="{executable,library}",
        default=None,
        dest="output_type",
        help="output binary kind: 'executable' (default) or 'library' (a "
        "shared library: .dll on Windows, .so on Linux). Falls back to the "
        "project's `output_type` field, then 'executable'.",
    )
    # The JVM backend also lists these in its own `requested_args`. That list
    # is not wired into this parser, so an option declared only there is
    # silently rejected -- which is what happened to --jvm-class. Both lists
    # have to be kept in step until argument negotiation reads the backend's.
    build_grp.add_argument(
        "--jvm-runtime",
        default="",
        metavar="CLASS",
        help="JVM backend: internal name of the class providing runtime and "
        "host functions (default asmpython/jvm/Runtime). Extend that class to "
        "add your own host API.",
    )
    build_grp.add_argument(
        "--jvm-class",
        default="",
        metavar="CLASS",
        help="JVM backend: fully qualified name of the generated class "
        "(default asmpython.jvm.Program).",
    )
    build_grp.add_argument(
        "--jvm-javac",
        default="",
        metavar="PATH",
        help="JVM backend: javac used to build the runtime support classes "
        "(default: javac on PATH).",
    )
    build_grp.add_argument(
        "--jvm-annotation",
        action="append",
        default=[],
        metavar="ANNOTATION",
        help="JVM backend: add a runtime-visible class annotation to the "
        "generated class, e.g. com.example.Plugin(value=demo). Repeatable. "
        "Lets a framework that discovers classes by annotation find one "
        "asmpython generated.",
    )
    build_grp.add_argument(
        "--jvm-resource",
        action="append",
        default=[],
        metavar="PATH=FILE",
        help="JVM backend: add a file to the output jar at PATH. Repeatable. "
        "For the descriptor or metadata a host expects alongside the classes.",
    )
    build_grp.add_argument(
        "--jvm-instantiate",
        nargs="?",
        const="",
        default=None,
        metavar="TYPES",
        help="JVM backend: also emit a public constructor that runs the module "
        "body, for frameworks that load a class by constructing it. An "
        "optional comma-separated list of parameter types (e.g. "
        "'com.example.Context') declares what the framework passes; those "
        "arrive at an exported `on_construct` as handles.",
    )
    build_grp.add_argument(
        "--bindings",
        action="append",
        default=[],
        metavar="MODULE",
        help="load a Python file declaring BINDINGS and register it as an "
        "importable FFI module (NAME=FILE, or FILE to use its stem). "
        "Repeatable. For a host exposing its own API to compiled code, so "
        "such a module does not have to live in asmpython's stdlib.",
    )
    build_grp.add_argument(
        "--jvm-runtime-package",
        default="",
        metavar="PACKAGE",
        help="JVM backend: package to compile the bundled runtime into "
        "(default asmpython.jvm). Two jars carrying it under the same package "
        "are a split package the module system rejects, so relocate it when a "
        "host may load more than one compiled jar.",
    )
    build_grp.add_argument(
        "--class-version",
        default="",
        metavar="N",
        help="JVM backend: class-file major version to emit (45-69; 52 = Java 8, "
        "65 = Java 21). Overrides --java-version.",
    )
    build_grp.add_argument(
        "--java-version",
        default="",
        metavar="RELEASE",
        help="JVM backend: target Java release (e.g. 8, 17, 21) - emits the "
        "highest class-file version that release produces.",
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
        "a built-in minimal ICO wrapper; uses windres from the gcc "
        "toolchain). Falls back to the project's `icon` field.",
    )

    # Bundling
    bundle = build_grp.add_mutually_exclusive_group()
    bundle.add_argument(
        "-of",
        "--onefile",
        dest="bundle_mode",
        action="store_const",
        const="onefile",
        default=None,
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

    build_grp.add_argument(
        "--use-runtime-lib",
        action="store_true",
        help="link the pre-built libasmpython_rt archive instead of inlining "
        "the runtime helpers (smaller .asm; archive built on demand)",
    )
    build_grp.add_argument(
        "--passes",
        metavar="LIST",
        default=None,
        help="comma-separated IR optimization passes to run, in order: "
        "'mem2reg' (promote stack slots to SSA + phi), 'constfold', 'dce', "
        "a preset ('o1', 'o2'), a path to a .py plugin registering one via "
        "asmpython.compiler_pass.CompilerPass(...), or 'help' to list all "
        "registered passes. Requires an IR backend (not --backend legacy). "
        "Default: no passes.",
    )
    build_grp.add_argument(
        "--frontend",
        metavar="NAME",
        default=None,
        help="source-language frontend: 'python' (default; the built-in "
        "lexer/parser/sema), any planned language scaffold (lua, javascript, "
        "typescript, c, go -- discoverable but not yet implemented), or any "
        "frontend registered via asmpython.frontend.Frontend(...). May also be "
        "a path to a .py plugin that registers one.",
    )
    build_grp.add_argument(
        "--backend",
        metavar="NAME",
        default=None,
        help="codegen backend: 'legacy' (NASM-text codegen.py, all targets), "
        "'x86-64' (built-in direct-to-object SSA IR backend), 'ternary' "
        "(uASM-related), or any backend registered via "
        "asmpython.backend.Backend(...). Default: 'x86-64' for "
        "--target windows/linux, 'legacy' for --target "
        "freestanding/freestanding16 (x86-64 doesn't support those targets "
        "yet) -- chosen automatically per --target unless this flag is "
        "given explicitly.",
    )
    build_grp.add_argument(
        "--link-library",
        metavar="NAME[=PATH|:SYM,...]",
        action="append",
        default=None,
        help="link against an external native library, repeatable. NAME is "
        "the load name ('SDL2.dll', 'libopenblas.so.0'); its exported symbols "
        "are read from PATH when given, else from a file named NAME, else "
        "listed explicitly after ':'. Replaces editing the linkers' built-in "
        "symbol tables. To CALL into a library, declare it under "
        "`native_libraries` in project.json instead, which also carries the "
        "function signatures.",
    )
    build_grp.add_argument(
        "--linker",
        metavar="NAME",
        default=None,
        help="linker to use: 'gcc', 'builtin' (asmpython's own, no gcc/ld "
        "involved), or any linker registered via asmpython.linker.Linker(...). "
        "Default: whichever the selected --backend prefers (legacy -> gcc, "
        "x86-64 -> builtin)",
    )
    build_grp.add_argument(
        "--no-pyinbin-fallback",
        action="store_true",
        help="report native compiler rejection as a failure instead of executing "
        "the source through pyinbin. Useful for differential compiler testing.",
    )
    build_grp.add_argument(
        "--apm",
        metavar="PATH",
        action="append",
        default=None,
        help="load a .apm package (bare .py or a zip): may register a "
        "custom backend/linker and/or mlang Config instances from one "
        "file, plus run an on_load(asmpython) behavior-modification hook. "
        "Repeatable. (Compiler-syntax extensions are no longer "
        "supported -- a .apm registering one will fail to load.)",
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

    meta_grp = ap.add_argument_group("information")
    meta_grp.add_argument("-h", "--help", action="help", help="show this help message and exit")
    return ap


def _run_check(
    src: str,
    source_path,
    *,
    source_dir,
    as_json: bool,
    all_errors: bool = False,
    active_extensions: "frozenset[str] | None" = None,
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
        module = Parser(tokens, active_extensions).parse()
        sema_analyze(
            module,
            source_dir=source_dir,
            collect_errors=all_errors,
            active_extensions=active_extensions,
        )
    except MultiSemaError as me:
        # me itself is a real MultiSemaError instance under a Python-hosted
        # compiler, but when self-compiled, asmpython's native exception
        # model can't carry a real object through `raise MultiSemaError(...)`
        # at all -- `me` is just the generic message string ("N semantic
        # error(s)") that MultiSemaError.__init__ passed to its own
        # super().__init__(...), with no `.errors` list, no `.format_all()`,
        # nothing else available. Print that directly rather than trying to
        # access fields/methods that don't exist on a plain string.
        if isinstance(me, str):
            print(me, file=sys.stderr)
            return 1
        # Each entry in me.errors is a real SemaError instance under a
        # Python-hosted compiler. Guard every `.phase`/`.message`/`.pos`/
        # `.code` access so a selfhosted compiler's diagnostics degrade to
        # bare messages instead of crashing.
        if as_json:
            from .errors import _code_label

            diags = []
            for e in me.errors:
                if isinstance(e, str):
                    diags.append({
                        "phase": None,
                        "message": e,
                        "line": 1,
                        "col": 1,
                        "code": None,
                    })
                else:
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

            if isinstance(e, str):
                diag = {"phase": None, "message": e, "line": 1, "col": 1, "code": None}
            else:
                diag = {
                    "phase": e.phase,
                    "message": e.message,
                    "line": e.pos.line if e.pos else 1,
                    "col": e.pos.col if e.pos else 1,
                    "code": _code_label(e.code) if e.code is not None else None,
                }
            print(json.dumps([diag]))
        else:
            if isinstance(e, str):
                print(e, file=sys.stderr)
            else:
                print(e.format(src, str(source_path)), file=sys.stderr)
        return 1
    if as_json:
        print("[]")
    return 0


def _load_backend_plugin(path: Path) -> str:
    """Exec a plugin file (bare .py or a .apb package zip) and return the
    name of the backend it registered. Mirrors `_load_ext_plugin` exactly,
    diffing `asmpython._backends._REGISTRY` instead of the extension
    registry, since a Backend has no id-based activation step -- its
    registered name IS what `--backend` selects."""
    from . import apkg
    from asmpython import _backends

    before = set(_backends._REGISTRY.keys())
    try:
        src, display_name, kind = apkg.read_entry_source(path)
        ns: dict = {"__name__": f"asmpython_backend_plugin_{path.stem}", "__file__": str(path)}
        exec(compile(src, display_name, "exec"), ns)
    except apkg.ApkgError as e:
        raise RuntimeError(f"failed to load backend plugin {path}: {e}") from e
    except Exception as e:
        raise RuntimeError(f"failed to load backend plugin {path}: {e}") from e
    after = set(_backends._REGISTRY.keys())
    new_names = after - before
    if len(new_names) == 0:
        hint = f" (its manifest declares kind={kind!r})" if kind and kind != "backend" else ""
        raise RuntimeError(
            f"backend plugin {path} did not register any asmpython.backend.Backend(...)"
            f"{hint}"
        )
    if len(new_names) > 1:
        raise RuntimeError(
            f"backend plugin {path} registered multiple backends "
            f"({', '.join(sorted(new_names))}) -- a .apb package should register exactly one"
        )
    return next(iter(new_names))


def _load_linker_plugin(path: Path) -> str:
    """Exec a plugin file (bare .py or a .apl package zip) and return the
    name of the linker it registered. Mirrors `_load_backend_plugin`."""
    from . import apkg
    from asmpython import _linkers

    before = set(_linkers._REGISTRY.keys())
    try:
        src, display_name, kind = apkg.read_entry_source(path)
        ns: dict = {"__name__": f"asmpython_linker_plugin_{path.stem}", "__file__": str(path)}
        exec(compile(src, display_name, "exec"), ns)
    except apkg.ApkgError as e:
        raise RuntimeError(f"failed to load linker plugin {path}: {e}") from e
    except Exception as e:
        raise RuntimeError(f"failed to load linker plugin {path}: {e}") from e
    after = set(_linkers._REGISTRY.keys())
    new_names = after - before
    if len(new_names) == 0:
        hint = f" (its manifest declares kind={kind!r})" if kind and kind != "linker" else ""
        raise RuntimeError(
            f"linker plugin {path} did not register any asmpython.linker.Linker(...)"
            f"{hint}"
        )
    if len(new_names) > 1:
        raise RuntimeError(
            f"linker plugin {path} registered multiple linkers "
            f"({', '.join(sorted(new_names))}) -- a .apl package should register exactly one"
        )
    return next(iter(new_names))


def _load_pass_plugin(path: Path) -> str:
    """Exec a plugin .py file and return the name of the pass it registered.
    Mirrors `_load_frontend_plugin`, diffing `asmpython._passes._REGISTRY`."""
    from asmpython import _passes

    before = set(_passes._REGISTRY.keys())
    try:
        src = path.read_text(encoding="utf-8")
        ns: dict = {"__name__": f"asmpython_pass_plugin_{path.stem}", "__file__": str(path)}
        exec(compile(src, str(path), "exec"), ns)
    except Exception as e:
        raise RuntimeError(f"failed to load pass plugin {path}: {e}") from e
    new_names = set(_passes._REGISTRY.keys()) - before
    if len(new_names) == 0:
        raise RuntimeError(
            f"pass plugin {path} did not register any "
            f"asmpython.compiler_pass.CompilerPass(...)"
        )
    if len(new_names) > 1:
        raise RuntimeError(
            f"pass plugin {path} registered multiple passes "
            f"({', '.join(sorted(new_names))}) -- register exactly one"
        )
    return next(iter(new_names))


def _resolve_passes_flag(value: "str | None") -> "str | None":
    """Resolve --passes: each comma-separated entry is a registered pass name,
    a preset, or a path to a .py plugin (loaded here, replaced by its
    registered name). Mirrors `_resolve_backend_flag`, but list-valued."""
    if value is None:
        return None
    resolved: list[str] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        p = Path(part)
        resolved.append(_load_pass_plugin(p) if p.is_file() else part)
    return ",".join(resolved)


def _print_registered_passes() -> int:
    from asmpython import _passes

    print("available compiler passes (--passes):\n")
    for name, description in _passes.describe():
        print(f"  {name:<14} {description}")
    aliases = _passes.registered_aliases()
    if aliases:
        print("\naliases:")
        for alias, canonical in sorted(aliases.items()):
            print(f"  {alias:<14} -> {canonical}")
    print("\npresets:")
    for preset, names in _passes.PIPELINES.items():
        print(f"  {preset:<14} {','.join(names)}")
    return 0


def _load_frontend_plugin(path: Path) -> str:
    """Exec a plugin .py file and return the name of the frontend it
    registered. Mirrors `_load_backend_plugin`, diffing
    `asmpython._frontends._REGISTRY` -- a Frontend's registered name IS what
    `--frontend` selects."""
    from asmpython import _frontends

    before = set(_frontends._REGISTRY.keys())
    try:
        src = path.read_text(encoding="utf-8")
        ns: dict = {"__name__": f"asmpython_frontend_plugin_{path.stem}", "__file__": str(path)}
        exec(compile(src, str(path), "exec"), ns)
    except Exception as e:
        raise RuntimeError(f"failed to load frontend plugin {path}: {e}") from e
    after = set(_frontends._REGISTRY.keys())
    new_names = after - before
    if len(new_names) == 0:
        raise RuntimeError(
            f"frontend plugin {path} did not register any "
            f"asmpython.frontend.Frontend(...)"
        )
    if len(new_names) > 1:
        raise RuntimeError(
            f"frontend plugin {path} registered multiple frontends "
            f"({', '.join(sorted(new_names))}) -- register exactly one"
        )
    return next(iter(new_names))


def _resolve_frontend_flag(value: "str | None") -> "str | None":
    """Resolve --frontend's value: a bare registered name passes through
    unchanged; a filesystem path (a .py plugin) is loaded first and its
    registered name is used instead. Mirrors `_resolve_backend_flag`."""
    if value is None:
        return None
    p = Path(value)
    if p.is_file():
        return _load_frontend_plugin(p)
    return value


def _resolve_backend_flag(value: "str | None") -> "str | None":
    """Resolve --backend's value: a bare registered name passes through
    unchanged; a filesystem path (bare .py or .apb package) is loaded first
    and its registered name is used instead. Mirrors `_resolve_ext_flags`'s
    single-value shape."""
    if value is None:
        return None
    p = Path(value)
    if p.is_file():
        return _load_backend_plugin(p)
    return value


def _resolve_linker_flag(value: "str | None") -> "str | None":
    """Resolve --linker's value. Mirrors `_resolve_backend_flag`."""
    if value is None:
        return None
    p = Path(value)
    if p.is_file():
        return _load_linker_plugin(p)
    return value


def _load_binding_modules(specs: "list[str]") -> None:
    """Register host-supplied FFI modules named by `--bindings`.

    A host that exposes an API to compiled code needs a module describing it,
    and that module is the HOST's -- it belongs beside the host, not in
    asmpython's stdlib. Loading one here is what lets that be true; without it
    the only place such a module can live is inside this package, which is how
    a Minecraft binding once ended up shipped with the compiler.
    """
    import importlib.util

    from ..stdlib import register_bindings

    for spec in specs or []:
        text = str(spec).strip()
        if not text:
            continue
        name, sep, source = text.partition("=")
        if not sep:
            source, name = text, Path(text).stem
        path = Path(source).expanduser()
        if not path.is_file():
            raise SystemExit(f"asmpython: --bindings: no such file: {path}")

        spec_obj = importlib.util.spec_from_file_location(f"_asmpy_bindings_{name}", path)
        if spec_obj is None or spec_obj.loader is None:
            raise SystemExit(f"asmpython: --bindings: cannot load {path}")
        module = importlib.util.module_from_spec(spec_obj)
        spec_obj.loader.exec_module(module)

        bindings = getattr(module, "BINDINGS", None)
        if not isinstance(bindings, dict):
            raise SystemExit(
                f"asmpython: --bindings: {path} defines no BINDINGS dict"
            )
        register_bindings(name.strip(), bindings, replace=True)


def _backend_args(args: argparse.Namespace, backend_name: str) -> dict:
    """The backend-specific options to hand the selected backend.

    Driven by the backend's own `requested_args` rather than a list kept here.
    A hardcoded list is silently lossy: an option can be declared by the
    backend, accepted by the parser, and still never arrive -- which is exactly
    what happened to --jvm-class, where the generated class name was ignored
    with no error at all.

    The three names below are always forwarded so a backend that declares
    nothing still sees the shared JVM/codegen options.
    """
    collected = {
        "class_version": getattr(args, "class_version", ""),
        "java_version": getattr(args, "java_version", ""),
        "jvm_runtime": getattr(args, "jvm_runtime", ""),
    }
    try:
        from .._backends import get_backend

        backend = get_backend(backend_name)
    except Exception:
        return collected

    for request in getattr(backend, "requested_args", None) or []:
        name = request.get("name") if isinstance(request, dict) else None
        if not name:
            continue
        dest = name.lstrip("-").replace("-", "_")
        if hasattr(args, dest):
            collected[dest] = getattr(args, dest)
    return collected


def cmd_build(args: argparse.Namespace) -> int:
    # Before anything resolves imports: a host-supplied module has to be
    # registered for `import <name>` in the source to find it.
    _load_binding_modules(getattr(args, "bindings", []))

    # Informational: `--passes help` lists the registry and exits, so it works
    # without a source file.
    if args.passes and args.passes.strip() == "help":
        return _print_registered_passes()
    if args.source is None:
        print(
            "asmpython: error: no source file given (try `asmpython build --help`)",
            file=sys.stderr,
        )
        return 2
    if not args.source.exists():
        print(f"asmpython: source file not found: {args.source}", file=sys.stderr)
        return 1

    cfg: ProjectConfig | None = None
    project_dir: Path | None = None
    if args.source.suffix.lower() == ".json":
        try:
            cfg = load_project(args.source)
        except ProjectError as e:
            print(f"asmpython: {args.source}: {e}", file=sys.stderr)
            return 1
        project_dir = args.source.resolve().parent
        assert project_dir is not None
        source_path = (project_dir / cfg.entry).resolve()
        if not source_path.is_file():
            print(
                f"asmpython: {args.source}: entry file not found: {source_path}",
                file=sys.stderr,
            )
            return 1
    else:
        source_path = args.source

    src = source_path.read_text(encoding="utf-8")
    all_errors = not args.one_error

    if args.check:
        return _run_check(
            src,
            source_path,
            source_dir=source_path.resolve().parent,
            as_json=args.json,
            all_errors=all_errors,
        )

    # Resolve effective settings: CLI flag wins when given, else the
    # project.json's matching field, else the built-in default.
    cli_targets = None
    if args.target:
        try:
            cli_targets = _parse_targets(args.target)
        except ValueError as e:
            print(f"asmpython: error: argument --target: {e}", file=sys.stderr)
            return 2
    targets: list[str] = cli_targets or (cfg.target if cfg and cfg.target else None) or [
        detect_default_target()
    ]
    # External native libraries: project.json's `native_libraries`, then any
    # --link-library flags. Populating the registry here (before sema, which
    # needs the FFI bindings, and before the driver, which needs the symbol
    # map at link time) is what lets a build reference a library the linkers'
    # builtin tables have never heard of.
    try:
        registry = _native_libraries.NativeLibraryRegistry(
            search_dirs=_native_libraries.default_search_dirs(
                project_dir, cfg.library_dirs if cfg is not None else None
            )
        )
        if cfg is not None:
            for entry in cfg.native_libraries:
                registry.declare(_native_libraries.from_mapping(entry))
        for raw_decl in args.link_library or ():
            registry.declare(_native_libraries.parse_declaration(raw_decl))
        _native_libraries.set_active_registry(registry)
        registry.install_bindings(targets)
    except _native_libraries.NativeLibraryError as e:
        print(f"asmpython: native library: {e}", file=sys.stderr)
        return 1

    output_type = args.output_type or (cfg.output_type if cfg else None) or "executable"
    bundle_mode = args.bundle_mode or (cfg.bundle_mode if cfg else None) or "onefile"
    icon_path = args.icon
    if icon_path is None and cfg is not None and cfg.icon:
        assert project_dir is not None
        icon_path = project_dir / cfg.icon
    use_runtime_lib = (
        args.use_runtime_lib
        or (cfg.use_runtime_lib if cfg is not None else False)
        or bundle_mode == "onedir"
    )

    single = len(targets) == 1

    # --backend default is target-dependent: the x86-64 IR backend has no
    # freestanding/freestanding16 support yet (see RESUME.md's "x86-64
    # backend: native freestanding/freestanding16 targets" pending item),
    # so those targets still need `legacy`; windows/linux default to the
    # newer x86-64 backend now that it has strong parity (confirmed via
    # this session's full-corpus sweep). A mixed target set (e.g.
    # `--target windows,freestanding` in one invocation) can't have two
    # different auto-picked backends applied to it -- fall back to
    # `legacy` for the whole build in that rare case rather than picking
    # one target's default arbitrarily; pass `--backend` explicitly to
    # override.
    try:
        effective_backend = _resolve_backend_flag(args.backend)
        effective_frontend = _resolve_frontend_flag(args.frontend) or "python"
        effective_passes = _resolve_passes_flag(args.passes)
        args.linker = _resolve_linker_flag(args.linker)
    except RuntimeError as e:
        print(f"asmpython: error: {e}", file=sys.stderr)
        return 1
    if effective_backend is None:
        _freestanding_targets = {"freestanding", "freestanding16"}
        if any(t in _freestanding_targets for t in targets):
            effective_backend = "legacy"
        else:
            effective_backend = "x86-64"

    effective_output = args.output
    if effective_output is None and cfg is not None and cfg.output:
        assert project_dir is not None
        effective_output = project_dir / cfg.output

    if effective_output is None:
        if cfg is not None:
            assert project_dir is not None
            base_stem = project_dir / "build" / source_path.with_suffix("").name
        else:
            base_stem = Path("build") / source_path.with_suffix("").name
        base_stem.parent.mkdir(parents=True, exist_ok=True)
        out_paths = [_target_out(base_stem, t, output_type) for t in targets]
    else:
        stem = effective_output.with_suffix("")
        if single:
            out_paths = [effective_output]
        else:
            out_paths = [_target_out(stem, t, output_type) for t in targets]
        for p in out_paths:
            p.parent.mkdir(parents=True, exist_ok=True)

    _source_dir = source_path.resolve().parent
    _entry_path = source_path.resolve()

    def pyinbin_fallback(native_error: Exception) -> int:
        """Try target-neutral execution after native codegen rejects source."""
        from asmpython.pyinbin import run_source

        bundle: Path | None = None
        if cfg is not None and cfg.pyinbin_imports:
            assert project_dir is not None
            from .pyinbin_package import PyinbinPackageError, build_source_bundle

            bundle = project_dir / "build" / "pyinbin"
            try:
                build_source_bundle(project_dir, cfg.pyinbin_imports, bundle)
            except PyinbinPackageError as package_error:
                print(
                    f"asmpython: native compilation failed: {native_error}\n"
                    f"asmpython: pyinbin packaging failed: {package_error}",
                    file=sys.stderr,
                )
                return 1

        pypi_roots: list[Path] = []
        if cfg is not None and cfg.pypi_packages:
            assert project_dir is not None
            pypi_roots.append(project_dir / cfg.pypi_dir)

        try:
            run_source(source_path, bundle=bundle, import_roots=pypi_roots or None)
        except Exception as fallback_error:
            print(
                f"asmpython: native compilation failed: {native_error}\n"
                f"asmpython: pyinbin fallback failed: {fallback_error}",
                file=sys.stderr,
            )
            return 1
        print(
            "asmpython: native backend rejected this source; "
            "pyinbin fallback executed successfully (no native artifact produced)",
            file=sys.stderr,
        )
        return 0

    def native_rejection(native_error: Exception) -> int:
        """Report a native-only failure or use the normal pyinbin fallback."""
        if not args.no_pyinbin_fallback:
            return pyinbin_fallback(native_error)
        if isinstance(native_error, MultiSemaError):
            print(native_error.format_all(src, str(source_path)), file=sys.stderr)
        elif isinstance(native_error, CompileError):
            print(native_error.format(src, str(source_path)), file=sys.stderr)
        else:
            print(f"asmpython: native compilation failed: {native_error}", file=sys.stderr)
        return 1

    if cfg is not None and cfg.pyinbin_imports:
        return native_rejection(RuntimeError("project declares pyinbin_imports"))

    from . import apkg

    try:
        active_extensions: frozenset = frozenset()
        for apm_path in (args.apm or []):
            apkg.load_module_package(Path(apm_path))
    except (RuntimeError, apkg.ApkgError) as e:
        print(f"asmpython: error: {e}", file=sys.stderr)
        return 1
    try:
        if single:
            compile_source(
                src,
                targets[0],
                out_paths[0],
                emit_asm_only=args.emit_asm,
                keep_intermediates=args.keep,
                keep_assembly=args.keep_assembly,
                use_runtime_lib=use_runtime_lib,
                nasm_path=args.nasm,
                gcc_path=args.gcc,
                bundle_mode=bundle_mode,
                source_dir=_source_dir,
                entry_path=_entry_path,
                output_type=output_type,
                icon_path=icon_path,
                all_errors=all_errors,
                backend=effective_backend,
                backend_args=_backend_args(args, effective_backend),
                linker=args.linker,
                active_extensions=active_extensions,
                frontend=effective_frontend,
                passes=effective_passes,
            )
        else:
            compile_targets(
                src,
                targets,
                out_paths,
                emit_asm_only=args.emit_asm,
                keep_intermediates=args.keep,
                keep_assembly=args.keep_assembly,
                use_runtime_lib=use_runtime_lib,
                nasm_path=args.nasm,
                gcc_path=args.gcc,
                bundle_mode=bundle_mode,
                source_dir=_source_dir,
                entry_path=_entry_path,
                output_type=output_type,
                icon_path=icon_path,
                all_errors=all_errors,
                backend=effective_backend,
                linker=args.linker,
                active_extensions=active_extensions,
                frontend=effective_frontend,
                passes=effective_passes,
            )
    except MultiSemaError as me:
        # Give the target-neutral interpreter a chance before reporting a
        # native-only language limitation. This is what makes dynamic imports
        # and other interpreter-capable constructs usable from the CLI.
        return native_rejection(me)
    except CompileError as e:
        return native_rejection(e)
    except NotImplementedError as e:
        return native_rejection(e)
    except Exception as e:
        print(f"asmpython: {e}", file=sys.stderr)
        return 1
    return 0


# ── `package` subcommand ────────────────────────────────────────────────────────

_PACKAGE_DESCRIPTION = """\
Install or remove native runtime-library dependencies (DLLs / shared objects
/ import libraries) that asmpython programs link or load at runtime but that
aren't asmpython/Python source themselves — SDL2 and friends.

A <name> resolves first against asmpython's bundled `_vendor/` directory
(instant, offline), then against a remote package registry (a JSON index;
see package-repository.json at the asmpython repo root for the format).

Pass a project.json instead of a package name to install/uninstall every
package listed in that project's `packages` field at once, into its
`library_dirs[0]`.
"""

_PACKAGE_EPILOG = """\
Examples:

  Install SDL2 into ./libs/ (or the cwd project's library_dirs[0]):
      asmpython package install sdl2

  Pin a specific version:
      asmpython package install sdl2 --version 2.32.10

  Install into an explicit directory:
      asmpython package install sdl2 --dir vendor/

  Install everything a project depends on:
      asmpython package install mygame/project.json

  Remove a package:
      asmpython package uninstall sdl2
"""


def _add_package_subparser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    ap = subparsers.add_parser(
        "package",
        usage="asmpython package <install|uninstall> <name|project.json> [options]",
        description=_PACKAGE_DESCRIPTION,
        epilog=_PACKAGE_EPILOG,
        formatter_class=_AsmPythonHelp,
        add_help=False,
    )
    meta_grp = ap.add_argument_group("information")
    meta_grp.add_argument("-h", "--help", action="help", help="show this help message and exit")

    pkg_sub = ap.add_subparsers(dest="package_action")

    install_p = pkg_sub.add_parser(
        "install",
        formatter_class=_AsmPythonHelp,
        help="install a package, or every package listed in a project.json",
    )
    install_p.add_argument("target", help="package name, or path to a project JSON file")
    install_p.add_argument(
        "--version",
        default=None,
        help="pin to a specific version (default: the registry's 'latest')",
    )
    install_p.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="install destination (default: a project.json in the cwd's "
        "library_dirs[0], else ./libs/)",
    )
    install_p.add_argument(
        "--registry",
        default=None,
        help="override the package registry URL or local path "
        "(default: $ASMPYTHON_PACKAGE_REGISTRY, else the asmpython repo's "
        "package-repository.json)",
    )

    uninstall_p = pkg_sub.add_parser(
        "uninstall",
        formatter_class=_AsmPythonHelp,
        help="remove a previously-installed package, or every package listed in a project.json",
    )
    uninstall_p.add_argument("target", help="package name, or path to a project JSON file")
    uninstall_p.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="install directory to remove from (default: a project.json in "
        "the cwd's library_dirs[0], else ./libs/)",
    )

    return ap


def _resolve_default_library_dir(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    proj = find_default_project(Path.cwd())
    if proj is not None:
        try:
            cfg = load_project(proj)
            return proj.parent / cfg.library_dirs[0]
        except ProjectError:
            pass
    return Path("libs")


def _install_one(name: str, dest_dir: Path, *, version: str | None, registry_url: str | None) -> bool:
    try:
        resolved_version, installed = install_package(
            name, dest_dir, version=version, registry_url=registry_url
        )
    except PackageError as e:
        print(f"asmpython: package install {name!r} failed: {e}", file=sys.stderr)
        return False
    print(f"asmpython: installed {name} {resolved_version} -> {dest_dir}/ ({', '.join(installed)})")
    return True


def _uninstall_one(name: str, dest_dir: Path) -> bool:
    try:
        removed = uninstall_package(name, dest_dir)
    except PackageError as e:
        print(f"asmpython: package uninstall {name!r} failed: {e}", file=sys.stderr)
        return False
    print(
        f"asmpython: uninstalled {name} from {dest_dir}/ "
        f"({', '.join(removed) if removed else 'no files removed'})"
    )
    return True


def cmd_package_install(args: argparse.Namespace) -> int:
    target_path = Path(args.target)
    if target_path.suffix.lower() == ".json" and target_path.is_file():
        try:
            cfg = load_project(target_path)
        except ProjectError as e:
            print(f"asmpython: {args.target}: {e}", file=sys.stderr)
            return 1
        if not cfg.packages:
            print(f"asmpython: {args.target}: no packages listed", file=sys.stderr)
            return 0
        dest_dir = target_path.resolve().parent / cfg.library_dirs[0]
        ok = True
        for name in cfg.packages:
            ok = _install_one(name, dest_dir, version=None, registry_url=args.registry) and ok
        return 0 if ok else 1

    dest_dir = _resolve_default_library_dir(args.dir)
    return 0 if _install_one(args.target, dest_dir, version=args.version, registry_url=args.registry) else 1


def cmd_package_uninstall(args: argparse.Namespace) -> int:
    target_path = Path(args.target)
    if target_path.suffix.lower() == ".json" and target_path.is_file():
        try:
            cfg = load_project(target_path)
        except ProjectError as e:
            print(f"asmpython: {args.target}: {e}", file=sys.stderr)
            return 1
        if not cfg.packages:
            print(f"asmpython: {args.target}: no packages listed", file=sys.stderr)
            return 0
        dest_dir = target_path.resolve().parent / cfg.library_dirs[0]
        ok = True
        for name in cfg.packages:
            ok = _uninstall_one(name, dest_dir) and ok
        return 0 if ok else 1

    dest_dir = _resolve_default_library_dir(args.dir)
    return 0 if _uninstall_one(args.target, dest_dir) else 1


def cmd_package(args: argparse.Namespace) -> int:
    action = args.package_action
    if action == "install":
        return cmd_package_install(args)
    if action == "uninstall":
        return cmd_package_uninstall(args)
    print(
        "asmpython: error: `package` requires a subcommand (install/uninstall); "
        "try `asmpython package --help`",
        file=sys.stderr,
    )
    return 2


# ── `pypi` subcommand ────────────────────────────────────────────────────────

_PYPI_DESCRIPTION = """\
Install real PyPI packages for pyinbin (run-through-interpreter) imports.

v1 is deliberately narrow: only pure-Python wheels (no compiled extension
modules), no sdist builds, and no transitive dependency resolution -- every
package your program imports must be installed explicitly. Installed
packages are implicit pyinbin import roots for `asmpython build` and
`asmpython pyinbin run`; this is a separate system from `asmpython package`
(prebuilt binary deps like SDL2).
"""

_PYPI_EPILOG = """\
Examples:

  Install a package into ./pypi_libs/ (or the cwd project's pypi_dir):
      asmpython pypi install requests

  Pin a specific version:
      asmpython pypi install six --version 1.16.0

  Install into an explicit directory:
      asmpython pypi install six --dir vendor_py/

  Install everything a project depends on:
      asmpython pypi install myproj/project.json

  Remove a package:
      asmpython pypi uninstall six

  List installed packages:
      asmpython pypi list
"""


def _add_pypi_subparser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    ap = subparsers.add_parser(
        "pypi",
        usage="asmpython pypi <install|uninstall|list> <name|project.json> [options]",
        description=_PYPI_DESCRIPTION,
        epilog=_PYPI_EPILOG,
        formatter_class=_AsmPythonHelp,
        add_help=False,
    )
    meta_grp = ap.add_argument_group("information")
    meta_grp.add_argument("-h", "--help", action="help", help="show this help message and exit")

    pypi_sub = ap.add_subparsers(dest="pypi_action")

    install_p = pypi_sub.add_parser(
        "install",
        formatter_class=_AsmPythonHelp,
        help="install a PyPI package (pure-Python wheel only), or every package listed in a project.json",
    )
    install_p.add_argument("target", help="PyPI package name, or path to a project JSON file")
    install_p.add_argument(
        "--version",
        default=None,
        help="pin to a specific version (default: PyPI's latest release)",
    )
    install_p.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="install destination (default: a project.json in the cwd's "
        "pypi_dir, else ./pypi_libs/)",
    )

    uninstall_p = pypi_sub.add_parser(
        "uninstall",
        formatter_class=_AsmPythonHelp,
        help="remove a previously-installed PyPI package, or every package listed in a project.json",
    )
    uninstall_p.add_argument("target", help="PyPI package name, or path to a project JSON file")
    uninstall_p.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="install directory to remove from (default: a project.json in "
        "the cwd's pypi_dir, else ./pypi_libs/)",
    )

    list_p = pypi_sub.add_parser(
        "list",
        formatter_class=_AsmPythonHelp,
        help="list PyPI packages installed into a directory",
    )
    list_p.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="directory to list (default: a project.json in the cwd's "
        "pypi_dir, else ./pypi_libs/)",
    )

    return ap


def _resolve_default_pypi_dir(explicit: Path | None) -> Path:
    if explicit is not None:
        return explicit
    proj = find_default_project(Path.cwd())
    if proj is not None:
        try:
            cfg = load_project(proj)
            return proj.parent / cfg.pypi_dir
        except ProjectError:
            pass
    return Path("pypi_libs")


def _pypi_install_one(name: str, dest_dir: Path, *, version: str | None) -> bool:
    try:
        resolved_version, installed = install_pypi_package(name, dest_dir, version=version)
    except PypiError as e:
        print(f"asmpython: pypi install {name!r} failed: {e}", file=sys.stderr)
        return False
    print(f"asmpython: installed {name} {resolved_version} -> {dest_dir}/ ({len(installed)} file(s))")
    return True


def _pypi_uninstall_one(name: str, dest_dir: Path) -> bool:
    try:
        removed = uninstall_pypi_package(name, dest_dir)
    except PypiError as e:
        print(f"asmpython: pypi uninstall {name!r} failed: {e}", file=sys.stderr)
        return False
    print(
        f"asmpython: uninstalled {name} from {dest_dir}/ "
        f"({len(removed)} file(s) removed)" if removed else
        f"asmpython: {name} was not installed in {dest_dir}/"
    )
    return True


def cmd_pypi_install(args: argparse.Namespace) -> int:
    target_path = Path(args.target)
    if target_path.suffix.lower() == ".json" and target_path.is_file():
        try:
            cfg = load_project(target_path)
        except ProjectError as e:
            print(f"asmpython: {args.target}: {e}", file=sys.stderr)
            return 1
        if not cfg.pypi_packages:
            print(f"asmpython: {args.target}: no pypi_packages listed", file=sys.stderr)
            return 0
        dest_dir = target_path.resolve().parent / cfg.pypi_dir
        ok = True
        for name in cfg.pypi_packages:
            ok = _pypi_install_one(name, dest_dir, version=None) and ok
        return 0 if ok else 1

    dest_dir = _resolve_default_pypi_dir(args.dir)
    return 0 if _pypi_install_one(args.target, dest_dir, version=args.version) else 1


def cmd_pypi_uninstall(args: argparse.Namespace) -> int:
    target_path = Path(args.target)
    if target_path.suffix.lower() == ".json" and target_path.is_file():
        try:
            cfg = load_project(target_path)
        except ProjectError as e:
            print(f"asmpython: {args.target}: {e}", file=sys.stderr)
            return 1
        if not cfg.pypi_packages:
            print(f"asmpython: {args.target}: no pypi_packages listed", file=sys.stderr)
            return 0
        dest_dir = target_path.resolve().parent / cfg.pypi_dir
        ok = True
        for name in cfg.pypi_packages:
            ok = _pypi_uninstall_one(name, dest_dir) and ok
        return 0 if ok else 1

    dest_dir = _resolve_default_pypi_dir(args.dir)
    return 0 if _pypi_uninstall_one(args.target, dest_dir) else 1


def cmd_pypi_list(args: argparse.Namespace) -> int:
    dest_dir = _resolve_default_pypi_dir(args.dir)
    manifest = list_pypi_packages(dest_dir)
    if not manifest:
        print(f"asmpython: no PyPI packages installed in {dest_dir}/")
        return 0
    for entry in manifest.values():
        print(f"{entry['name']} {entry['version']} ({len(entry.get('files', []))} file(s))")
    return 0


def cmd_pypi(args: argparse.Namespace) -> int:
    action = args.pypi_action
    if action == "install":
        return cmd_pypi_install(args)
    if action == "uninstall":
        return cmd_pypi_uninstall(args)
    if action == "list":
        return cmd_pypi_list(args)
    print(
        "asmpython: error: `pypi` requires a subcommand (install/uninstall/list); "
        "try `asmpython pypi --help`",
        file=sys.stderr,
    )
    return 2


# ── `pyinbin` subcommand ───────────────────────────────────────────────────────

_PYINBIN_DESCRIPTION = """\
Build source bundles and run supported Python through the pyinbin interpreter. A bundle
contains the Python modules declared by a project's `pyinbin_imports` field,
their qualified import names, and SHA-256 integrity metadata.

The bootstrap runtime executes lowered bytecode and routes imports only through
its explicit source/bundle loader. Native embedding remains a separate target
backend delivery step.
"""


def _add_pyinbin_subparser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    ap = subparsers.add_parser(
        "pyinbin",
        usage="asmpython pyinbin {package,run} ...",
        description=_PYINBIN_DESCRIPTION,
        formatter_class=_AsmPythonHelp,
        add_help=False,
    )
    meta_grp = ap.add_argument_group("information")
    meta_grp.add_argument("-h", "--help", action="help", help="show this help message and exit")
    pyinbin_sub = ap.add_subparsers(dest="pyinbin_action")
    package_p = pyinbin_sub.add_parser(
        "package", formatter_class=_AsmPythonHelp, help="package declared runtime-import source roots"
    )
    package_p.add_argument("project", type=Path, help="project.json declaring pyinbin_imports")
    package_p.add_argument(
        "-o", "--output", type=Path, default=None,
        help="bundle directory (default: <project>/build/pyinbin)",
    )
    run_p = pyinbin_sub.add_parser("run", formatter_class=_AsmPythonHelp, help="run Python source through pyinbin")
    run_p.add_argument("source", type=Path, help="entry Python source file")
    run_p.add_argument("--bundle", type=Path, default=None, help="verified pyinbin source bundle for imports")
    run_p.add_argument(
        "--import-root", type=Path, action="append", default=None,
        help="additional source root for interpreted imports (repeatable)",
    )
    return ap


def cmd_pyinbin_package(args: argparse.Namespace) -> int:
    project_path = args.project
    if project_path.suffix.lower() != ".json" or not project_path.is_file():
        print("asmpython: pyinbin package requires an existing project.json", file=sys.stderr)
        return 2
    try:
        cfg = load_project(project_path)
    except ProjectError as exc:
        print(f"asmpython: {project_path}: {exc}", file=sys.stderr)
        return 1
    if not cfg.pyinbin_imports:
        print(f"asmpython: {project_path}: no pyinbin_imports declared", file=sys.stderr)
        return 1

    from .pyinbin_package import PyinbinPackageError, build_source_bundle

    root = project_path.resolve().parent
    destination = args.output or root / "build" / "pyinbin"
    try:
        modules = build_source_bundle(root, cfg.pyinbin_imports, destination)
    except PyinbinPackageError as exc:
        print(f"asmpython: pyinbin package failed: {exc}", file=sys.stderr)
        return 1
    print(f"asmpython: packaged {len(modules)} pyinbin module(s) -> {destination}")
    return 0


def cmd_pyinbin_run(args: argparse.Namespace) -> int:
    from asmpython.pyinbin import PyinbinImportError, PyinbinUnsupportedError, VMError, run_source

    try:
        run_source(args.source, bundle=args.bundle, import_roots=args.import_root)
    except (OSError, PyinbinImportError, PyinbinUnsupportedError, VMError) as exc:
        print(f"asmpython: pyinbin: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_pyinbin(args: argparse.Namespace) -> int:
    if args.pyinbin_action == "package":
        return cmd_pyinbin_package(args)
    if args.pyinbin_action == "run":
        return cmd_pyinbin_run(args)
    print(
        "asmpython: error: `pyinbin` requires a subcommand (package/run); "
        "try `asmpython pyinbin --help`",
        file=sys.stderr,
    )
    return 2


# ── `project` subcommand ────────────────────────────────────────────────────────

_PROJECT_DESCRIPTION = """\
Scaffold a new asmpython project: a project.json manifest plus a starter
entry file and library directory. The manifest captures everything `build`
needs — entry file, output, target(s), output type, bundle mode, icon,
library_dirs, and packages — so `asmpython build <project.json>` alone can
fully replace an equivalent CLI invocation's flags.

--type scaffolds using a custom project type instead: a package in the
registry can offer one or more project types under its `install.
project-types` entry (see package-repository.json), each naming a
platform-specific init_script that runs against the new project directory
after the usual project.json/main.py scaffold is in place.
"""

_PROJECT_EPILOG = """\
Examples:

  Scaffold ./mygame/project.json + ./mygame/main.py:
      asmpython project new mygame

  Scaffold in the current directory instead:
      asmpython project new --dir .

  Pre-set the project's default target(s):
      asmpython project new mygame --target windows,linux

  Scaffold using a package-provided custom project type:
      asmpython project new mygame --type sdl2_game

  Same, disambiguated when more than one package defines that type name:
      asmpython project new mygame --type sdl2_game --from sdl2
"""


def _add_project_subparser(subparsers: argparse._SubParsersAction) -> argparse.ArgumentParser:
    ap = subparsers.add_parser(
        "project",
        usage="asmpython project new [name] [options]",
        description=_PROJECT_DESCRIPTION,
        epilog=_PROJECT_EPILOG,
        formatter_class=_AsmPythonHelp,
        add_help=False,
    )
    meta_grp = ap.add_argument_group("information")
    meta_grp.add_argument("-h", "--help", action="help", help="show this help message and exit")

    proj_sub = ap.add_subparsers(dest="project_action")

    new_p = proj_sub.add_parser(
        "new", formatter_class=_AsmPythonHelp, help="create a new project.json + entry file"
    )
    new_p.add_argument(
        "name", nargs="?", default=None, help="project name (default: the directory name)"
    )
    new_p.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="project directory (default: ./<name>, or the cwd if no name is given)",
    )
    new_p.add_argument(
        "--target",
        metavar="{linux,windows,freestanding,freestanding16}[,...]",
        default=None,
        help="default target(s) to record in project.json (default: none, "
        "i.e. host platform at build time)",
    )
    new_p.add_argument(
        "--type",
        default=None,
        help="scaffold using a custom project type's init_script, looked up "
        "by searching every package in the registry for a matching "
        "install.project-types entry (see package-repository.json)",
    )
    new_p.add_argument(
        "--from",
        dest="from_pkg",
        default=None,
        help="restrict the --type lookup to this package (required if more "
        "than one package defines the same type name)",
    )
    new_p.add_argument(
        "--version",
        default=None,
        help="with --type: pin the owning package to a specific version "
        "(default: the registry's 'latest')",
    )
    new_p.add_argument(
        "--registry",
        default=None,
        help="with --type: override the package registry URL or local path "
        "(default: $ASMPYTHON_PACKAGE_REGISTRY, else the asmpython repo's "
        "package-repository.json)",
    )

    return ap


def cmd_project_new(args: argparse.Namespace) -> int:
    directory = args.dir
    name = args.name
    if directory is None:
        directory = Path(name) if name else Path(".")
    if name is None:
        resolved_name = directory.resolve().name
        name = resolved_name or "project"

    target = None
    if args.target:
        try:
            target = _parse_targets(args.target)
        except ValueError as e:
            print(f"asmpython: error: argument --target: {e}", file=sys.stderr)
            return 2

    try:
        proj_path, cfg = init_project(directory, name, target=target)
    except ProjectError as e:
        print(f"asmpython: {e}", file=sys.stderr)
        return 1

    if args.type:
        try:
            owner_pkg, project_type_entry = find_project_type(
                args.type, from_pkg=args.from_pkg, registry_url=args.registry
            )
            run_project_init_script(
                owner_pkg, project_type_entry, directory,
                version=args.version, registry_url=args.registry,
            )
        except PackageError as e:
            print(f"asmpython: project type {args.type!r}: {e}", file=sys.stderr)
            return 1

    print(f"asmpython: created project {cfg.name!r}")
    print(f"  {proj_path}")
    print(f"  {directory / cfg.entry}")
    print(f"  {directory / cfg.library_dirs[0]}/")
    print()
    print("Next steps:")
    print(f"  asmpython build {proj_path}")
    return 0


def cmd_project(args: argparse.Namespace) -> int:
    action = args.project_action
    if action == "new":
        return cmd_project_new(args)
    print(
        "asmpython: error: `project` requires a subcommand (new); "
        "try `asmpython project --help`",
        file=sys.stderr,
    )
    return 2


# ── argv preprocessing (backward-compat shorthand) ──────────────────────────────

_SUBCOMMANDS = {"build", "package", "pypi", "pyinbin", "project"}
_TOP_LEVEL_ONLY = {"-h", "--help", "-V", "--version"}


def _preprocess_argv(argv: list[str]) -> list[str]:
    """`asmpython <file> [flags]` (no subcommand) is shorthand for
    `asmpython build <file> [flags]`; bare `asmpython` with nothing at all
    is treated the same way so `build`'s own "no source file given" message
    fires instead of a generic "no command" one."""
    if not argv:
        return ["build"]
    if argv[0] in _TOP_LEVEL_ONLY or argv[0] == "--explain":
        return argv
    if argv[0] in _SUBCOMMANDS:
        return argv
    return ["build"] + argv


def main(argv: list[str] | None = None) -> int:
    raw_argv = sys.argv[1:] if argv is None else argv
    processed = _preprocess_argv(raw_argv)

    ap = _build_top_parser()
    args = ap.parse_args(processed)

    if args.explain is not None:
        desc = _explain_code(args.explain)
        if desc:
            print(desc)
            return 0
        print(f"asmpython: unknown error code {args.explain!r}", file=sys.stderr)
        return 1

    command = args.command
    if command == "build":
        return cmd_build(args)
    if command == "package":
        return cmd_package(args)
    if command == "pypi":
        return cmd_pypi(args)
    if command == "pyinbin":
        return cmd_pyinbin(args)
    if command == "project":
        return cmd_project(args)

    ap.print_usage(sys.stderr)
    print(
        "asmpython: error: no command given (try `asmpython --help`)",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
