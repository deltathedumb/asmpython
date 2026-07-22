"""Public ASMPython CLI facade and management-command router."""
from __future__ import annotations

import os
import sys

from asmpython._backends import host_cli as _host_cli
from .build_options import extract_speedy_lossy, speedy_lossy_mode
from .management_commands import (
    MANAGEMENT_COMMANDS,
    REMOVED_COMMANDS,
    apply_build_profiles,
    dispatch,
)
from .profiles import ProfileError


_call_legacy_with_static_project_policy = (
    _host_cli._call_legacy_with_static_project_policy
)
_legacy_cli = _host_cli._legacy_cli
prepare_argv = _host_cli.prepare_argv
source_tree_uses_dynamic_import = _host_cli.source_tree_uses_dynamic_import
source_uses_dynamic_import = _host_cli.source_uses_dynamic_import


def _print_help() -> None:
    print("Compile Python source and manage the ASMPython toolchain.")
    print()
    print("usage: asmpython <command> ...")
    print("       asmpython <source.py> [build options]")
    print()
    print("Core commands:")
    print("  build       compile source or a project")
    print("  test        compare CPython, native, PyinBin, and hybrid execution")
    print("  backends    list backends or show one backend's metadata")
    print("  ir          inspect or compile frozen IR")
    print("  cache       inspect, verify, prune, or clear compiler caches")
    print("  profile     create, modify, show, or delete scoped build profiles")
    print("  package     manage native runtime-library packages")
    print("  pyinbin     package or run interpreted source")
    print("  project     scaffold projects")
    print()
    print("Shared build options:")
    print("  --speedy-lossy  compile faster while permitting lower-performance output")
    print()
    print("Examples:")
    print("  asmpython build app.py --profile release")
    print("  asmpython build app.py --speedy-lossy")
    print("  asmpython backends list")
    print("  asmpython backends jvm")
    print("  asmpython ir build/app.apir --target linux --speedy-lossy")
    print("  asmpython cache clear")
    print('  asmpython profile create release --scope user --set backend="x86-64"')
    print("  asmpython test tests --engine all")


def _main_with_options(raw: list[str], *, speedy_lossy: bool) -> int:
    if raw in (["-h"], ["--help"]):
        _print_help()
        return 0
    if raw and raw[0] in REMOVED_COMMANDS:
        replacement = REMOVED_COMMANDS[raw[0]]
        print(
            f"asmpython: command {raw[0]!r} was removed; use `{replacement}`",
            file=sys.stderr,
        )
        return 2

    if speedy_lossy:
        print(
            "asmpython: speedy-lossy mode enabled; backends and linkers may "
            "trade generated-program performance for faster builds",
            file=sys.stderr,
        )

    handled = dispatch(raw)
    if handled is not None:
        return handled

    try:
        prepared = apply_build_profiles(raw)
    except ProfileError as exc:
        print(f"asmpython: profile: {exc}", file=sys.stderr)
        return 2

    # ``build --ir-only`` stops before target selection/linking and therefore
    # bypasses the historical build parser, which deliberately knows nothing
    # about the frozen-IR feature.
    if "--ir-only" in prepared:
        from .ir_command import freeze_build_main
        return freeze_build_main(prepared)

    previous_traceback_mode = os.environ.get("ASMPYTHON_CLI_MIXED_TRACEBACK")
    os.environ["ASMPYTHON_CLI_MIXED_TRACEBACK"] = "1"
    try:
        return _host_cli.main(prepared, prepare=prepare_argv)
    except BaseException as exc:
        try:
            from asmpython._runtime.mixed_traceback import MixedTracebackError
            if isinstance(exc, MixedTracebackError):
                print(str(exc), file=sys.stderr)
                return 1
        except Exception:
            pass
        raise
    finally:
        if previous_traceback_mode is None:
            os.environ.pop("ASMPYTHON_CLI_MIXED_TRACEBACK", None)
        else:
            os.environ["ASMPYTHON_CLI_MIXED_TRACEBACK"] = previous_traceback_mode


def main(argv: list[str] | None = None) -> int:
    """Run management commands or delegate ordinary builds to the host policy."""

    raw = list(sys.argv[1:] if argv is None else argv)
    try:
        raw, speedy_lossy = extract_speedy_lossy(raw)
    except ValueError as exc:
        print(f"asmpython: {exc}", file=sys.stderr)
        return 2

    # The context wraps both management-command dispatch (notably ``ir``) and
    # the ordinary source build path. ModuleBackend and public plugin adapters
    # inject the value into every backend compile/link and linker link call.
    with speedy_lossy_mode(speedy_lossy):
        return _main_with_options(raw, speedy_lossy=speedy_lossy)


__all__ = [
    "MANAGEMENT_COMMANDS",
    "main",
    "prepare_argv",
    "source_tree_uses_dynamic_import",
    "source_uses_dynamic_import",
]
