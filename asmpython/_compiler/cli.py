"""Public ASMPython CLI facade and management-command router."""
from __future__ import annotations

import os
import sys

from asmpython._backends import host_cli as _host_cli
from .build_options import (
    SharedBuildOptions,
    extract_shared_build_options,
    shared_build_options,
)
from .build_report import event, report_session
from .management_commands import (
    MANAGEMENT_COMMANDS,
    REMOVED_COMMANDS,
    apply_build_profiles,
    dispatch,
)
from .profiles import ProfileError
from .toolchain_policy import warn_selected_nonproduction


_call_legacy_with_static_project_policy = (
    _host_cli._call_legacy_with_static_project_policy
)
_legacy_cli = _host_cli._legacy_cli
prepare_argv = _host_cli.prepare_argv
source_tree_uses_dynamic_import = _host_cli.source_tree_uses_dynamic_import
source_uses_dynamic_import = _host_cli.source_uses_dynamic_import

_NON_BUILD_COMMANDS = MANAGEMENT_COMMANDS | {
    "extension", "package", "pypi", "pyinbin", "project",
}


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
    print("  extension   package, install, download, list, or remove .apext extensions")
    print("  ir          inspect or compile frozen IR")
    print("  cache       inspect, verify, prune, or clear compiler caches")
    print("  profile     create, modify, show, or delete scoped build profiles")
    print("  package     manage native runtime-library packages")
    print("  pyinbin     package or run interpreted source")
    print("  project     scaffold projects")
    print()
    print("Shared build options:")
    print("  --speedy-lossy       compile faster while permitting lower-performance output")
    print("  --bleach             enable the strong default sanitizer set")
    print("  --sanitize NAME      enable a specific sanitizer; repeatable")
    print("  --report PATH        write a machine-readable JSON build report")
    print()
    print("Examples:")
    print("  asmpython build app.py --profile release")
    print("  asmpython build app.py --speedy-lossy --report build-report.json")
    print("  asmpython build app.py --bleach")
    print("  asmpython build app.py --sanitize address --sanitize undefined")
    print("  asmpython extension package main:extension")
    print("  asmpython extension install my_extension.apext --user")
    print("  asmpython extension get https://example.com/my_extension.apext")
    print("  asmpython backends list")
    print("  asmpython backends jvm")
    print("  asmpython ir build/app.apir --target linux --speedy-lossy")
    print("  asmpython cache clear")
    print('  asmpython profile create release --scope user --set backend="x86-64"')
    print("  asmpython test tests --engine all")


def _is_build_invocation(raw: list[str]) -> bool:
    if not raw:
        return False
    first = raw[0]
    return first == "build" or first not in _NON_BUILD_COMMANDS


def _merge_options(
    profile_options: SharedBuildOptions,
    explicit_options: SharedBuildOptions,
) -> SharedBuildOptions:
    sanitizers = tuple(sorted(set(profile_options.sanitizers) | set(explicit_options.sanitizers)))
    selected = set(sanitizers)
    if "thread" in selected and selected.intersection({"address", "leak", "memory"}):
        raise ValueError(
            "the thread sanitizer cannot be combined with address, leak, or memory sanitizers"
        )
    if "memory" in selected and selected.intersection({"address", "leak"}):
        raise ValueError(
            "the memory sanitizer cannot be combined with address or leak sanitizers"
        )
    return SharedBuildOptions(
        speedy_lossy=profile_options.speedy_lossy or explicit_options.speedy_lossy,
        bleach=profile_options.bleach or explicit_options.bleach,
        sanitizers=sanitizers,
        report_path=explicit_options.report_path or profile_options.report_path,
    )


def _load_extensions(report) -> bool:
    from .extension_packages import ExtensionPackageError, load_installed_extensions

    try:
        loaded = load_installed_extensions()
    except ExtensionPackageError as exc:
        print(f"asmpython: extension: {exc}", file=sys.stderr)
        return False
    if report is not None:
        for item in loaded:
            report.add_extension(
                id=item.id,
                version=item.version,
                scope=item.scope,
                path=item.path,
                production_suitable=item.production_suitable,
            )
    return True


def _extensions_needed(raw: list[str], *, is_build: bool) -> bool:
    if is_build:
        return True
    return bool(raw and raw[0] in {"backends", "ir", "test"})


def _option_value(argv: list[str], flag: str) -> str | None:
    value: str | None = None
    for index, token in enumerate(argv):
        if token == flag and index + 1 < len(argv):
            value = argv[index + 1]
        elif token.startswith(flag + "="):
            value = token.split("=", 1)[1]
    return value


def _main_with_options(
    raw: list[str],
    *,
    options: SharedBuildOptions,
    report,
    is_build: bool,
) -> int:
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

    if options.speedy_lossy:
        print(
            "asmpython: speedy-lossy mode enabled; backends and linkers may "
            "trade generated-program speed and code quality for faster builds",
            file=sys.stderr,
        )
    if options.bleach:
        print(
            "asmpython: bleach mode enabled; strong runtime/compiler checks may "
            "substantially increase compile time, program size, and runtime cost",
            file=sys.stderr,
        )
    elif options.sanitizers:
        print(
            "asmpython: sanitizers enabled: " + ", ".join(options.sanitizers),
            file=sys.stderr,
        )

    if _extensions_needed(raw, is_build=is_build) and not _load_extensions(report):
        return 1

    handled = dispatch(raw)
    if handled is not None:
        return handled

    if is_build:
        warn_selected_nonproduction(raw)
        event(
            "build.configuration",
            backend=_option_value(raw, "--backend"),
            linker=_option_value(raw, "--linker"),
            target=_option_value(raw, "--target"),
            speedy_lossy=options.speedy_lossy,
            bleach=options.bleach,
            sanitizers=options.sanitizers,
        )

    if "--ir-only" in raw:
        from .ir_command import freeze_build_main
        return freeze_build_main(raw)

    previous_traceback_mode = os.environ.get("ASMPYTHON_CLI_MIXED_TRACEBACK")
    os.environ["ASMPYTHON_CLI_MIXED_TRACEBACK"] = "1"
    try:
        return _host_cli.main(raw, prepare=prepare_argv)
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

    original = list(sys.argv[1:] if argv is None else argv)

    # Extension management has its own parser and does not consume build flags.
    if original and original[0] == "extension":
        from .extension_command import command_main
        return command_main(original[1:])

    try:
        raw, explicit_options = extract_shared_build_options(original)
        is_build = _is_build_invocation(raw)
        if is_build:
            prepared = apply_build_profiles(raw)
            prepared, profile_options = extract_shared_build_options(prepared)
            options = _merge_options(profile_options, explicit_options)
        else:
            prepared = raw
            options = explicit_options
    except (ProfileError, ValueError) as exc:
        print(f"asmpython: {exc}", file=sys.stderr)
        return 2

    option_record = {
        "speedy_lossy": options.speedy_lossy,
        "bleach": options.bleach,
        "sanitizers": list(options.sanitizers),
    }
    with shared_build_options(options):
        with report_session(options.report_path, original, option_record) as report:
            try:
                result = _main_with_options(
                    prepared,
                    options=options,
                    report=report,
                    is_build=is_build,
                )
            except BaseException as exc:
                if report is not None:
                    report.write(
                        exit_code=1,
                        error=f"{type(exc).__name__}: {exc}",
                    )
                raise
            if report is not None:
                report.write(exit_code=result)
            return result


__all__ = [
    "MANAGEMENT_COMMANDS",
    "main",
    "prepare_argv",
    "source_tree_uses_dynamic_import",
    "source_uses_dynamic_import",
]
