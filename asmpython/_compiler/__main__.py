"""CLI entry point: `python -m asmpython <command> ...` or `asmpython.bat ...`.

Three subcommands:

  asmpython build <source.py | project.json> [options]
  asmpython package install|uninstall <name | project.json> [options]
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

from .driver import compile_source, compile_targets, detect_default_target
from .errors import CompileError, MultiSemaError, explain as _explain_code
from .packages import (
    PackageError,
    find_project_type,
    install_package,
    run_project_init_script,
    uninstall_package,
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
    if not hasattr(sys.stdout, "isatty") or not sys.stdout.isatty():
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
        usage="asmpython <build|package|project> ... (or just `asmpython <source.py>`)",
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
        "--backend",
        choices=("legacy", "x86-64"),
        default="legacy",
        help="codegen backend: 'legacy' (NASM-text codegen.py, all targets) "
        "or 'x86-64' (built-in direct-to-object SSA IR backend, windows "
        "only for now, experimental). Default: legacy",
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


def cmd_build(args: argparse.Namespace) -> int:
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
                backend=args.backend,
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
                backend=args.backend,
            )
    except MultiSemaError as me:
        # me is just the generic "N semantic error(s)" message string when
        # self-compiled (see the matching guard in _run_check above for why).
        if isinstance(me, str):
            print(me, file=sys.stderr)
        else:
            print(me.format_all(src, str(source_path)), file=sys.stderr)
        return 1
    except CompileError as e:
        # e is a real CompileError instance under a Python-hosted compiler,
        # but just a plain message string when self-compiled (see
        # MultiSemaError's docstring in errors.py for why).
        if isinstance(e, str):
            print(e, file=sys.stderr)
        else:
            print(e.format(src, str(source_path)), file=sys.stderr)
        return 1
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

_SUBCOMMANDS = {"build", "package", "project"}
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
