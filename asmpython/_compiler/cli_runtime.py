"""Host-only orchestration for the public ASMPython CLI facade."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from asmpython._backends import host_cli as _host_cli
from .build_options import SharedBuildOptions, extract_shared_build_options, shared_build_options
from .build_report import event, report_session
from .management_commands import MANAGEMENT_COMMANDS, REMOVED_COMMANDS, apply_build_profiles, dispatch
from .profiles import ProfileError
from .toolchain_policy import warn_selected_nonproduction


_call_legacy_with_static_project_policy = _host_cli._call_legacy_with_static_project_policy
_legacy_cli = _host_cli._legacy_cli
prepare_argv = _host_cli.prepare_argv
source_tree_uses_dynamic_import = _host_cli.source_tree_uses_dynamic_import
source_uses_dynamic_import = _host_cli.source_uses_dynamic_import

_EXTRA_COMMANDS = frozenset({"linkers", "lock", "verify", "abi", "sign"})
_NON_BUILD_COMMANDS = MANAGEMENT_COMMANDS | _EXTRA_COMMANDS | {
    "extension", "package", "pypi", "pyinbin", "project",
}


def _print_help() -> None:
    print("Compile Python source and manage the ASMPython toolchain.")
    print()
    print("usage: asmpython <command> ...")
    print("       asmpython build [SOURCE] [build options]")
    print()
    print("Core commands:")
    print("  build       compile source or build.config.toml")
    print("  test        compare CPython, native, PyinBin, and hybrid execution")
    print("  backends    list backends or show one backend's metadata")
    print("  linkers     list linkers or show one linker's metadata")
    print("  extension   package, install, download, list, or remove .apext extensions")
    print("  lock        create, verify, update, or show asmpython.lock")
    print("  verify      verify executable/library/package artifacts")
    print("  abi         dump, compare, or check native ABIs")
    print("  sign        create or verify certificate-backed package signatures")
    print("  ir          inspect or compile frozen IR")
    print("  cache       inspect, verify, prune, or clear compiler caches")
    print("  profile     create, modify, show, or delete scoped build profiles")
    print()
    print("Shared build options:")
    print("  --config PATH        use a specific build.config.toml")
    print("  --target P S ABI     structured target triple, e.g. pc windows msvc")
    print("  --graphonly          print the build graph without compiling")
    print("  --fastcomp           reuse parsed modules, IR, dependency graph, and backend state")
    print("  --embed PATH         append a file to the artifact; repeatable")
    print("  --debug              emit debugger metadata")
    print("  --debug-format NAME  auto, dwarf, pdb, codeview, or sourcemap")
    print("  --speedy-lossy       compile faster while permitting lower-performance output")
    print("  --bleach             enable the strong default sanitizer set")
    print("  --sanitize NAME      enable a specific sanitizer; repeatable")
    print("  --report PATH        write a machine-readable JSON build report")
    print("  --locked             require the current asmpython.lock")
    print()
    print("Examples:")
    print("  asmpython build")
    print("  asmpython build app.py --target pc windows msvc")
    print("  asmpython build --graphonly")
    print("  asmpython build app.py --fastcomp --debug --embed LICENSE")
    print("  asmpython verify app.exe")
    print("  asmpython abi diff old.dll new.dll")


def _is_build_invocation(raw: list[str]) -> bool:
    return bool(raw) and (raw[0] == "build" or raw[0] not in _NON_BUILD_COMMANDS)


def _merge_options(profile: SharedBuildOptions, explicit: SharedBuildOptions) -> SharedBuildOptions:
    sanitizers = tuple(sorted(set(profile.sanitizers) | set(explicit.sanitizers)))
    selected = set(sanitizers)
    if "thread" in selected and selected.intersection({"address", "leak", "memory"}):
        raise ValueError(
            "the thread sanitizer cannot be combined with address, leak, or memory sanitizers"
        )
    if "memory" in selected and selected.intersection({"address", "leak"}):
        raise ValueError("the memory sanitizer cannot be combined with address or leak sanitizers")
    debug_format = explicit.debug_format
    if debug_format == "auto" and profile.debug_format != "auto":
        debug_format = profile.debug_format
    return SharedBuildOptions(
        speedy_lossy=profile.speedy_lossy or explicit.speedy_lossy,
        bleach=profile.bleach or explicit.bleach,
        sanitizers=sanitizers,
        report_path=explicit.report_path or profile.report_path,
        locked=profile.locked or explicit.locked,
        lockfile_path=(
            explicit.lockfile_path
            if explicit.lockfile_path != Path("asmpython.lock")
            else profile.lockfile_path
        ),
        fastcomp=profile.fastcomp or explicit.fastcomp,
        debug=profile.debug or explicit.debug,
        debug_format=debug_format,
        embed_paths=tuple(dict.fromkeys((*profile.embed_paths, *explicit.embed_paths))),
    )


def _dispatch_extra(raw: list[str]) -> int | None:
    if not raw:
        return None
    command, rest = raw[0], raw[1:]
    if command == "backends":
        from .component_commands import command_main
        return command_main("backend", rest)
    if command == "linkers":
        from .component_commands import command_main
        return command_main("linker", rest)
    if command == "lock":
        from .build_lock import command_main
        return command_main(rest)
    if command == "verify":
        from .artifact_verify import command_main
        return command_main(rest)
    if command == "abi":
        from .abi_tool import command_main
        return command_main(rest)
    if command == "sign":
        from .package_signing import command_main
        return command_main(rest)
    return None


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
    return is_build or bool(raw and raw[0] in {"backends", "linkers", "ir", "test", "lock"})


def _option_value(argv: list[str], flag: str) -> str | None:
    value: str | None = None
    for index, token in enumerate(argv):
        if token == flag and index + 1 < len(argv):
            value = argv[index + 1]
        elif token.startswith(flag + "="):
            value = token.split("=", 1)[1]
    return value


def _source_path(argv: list[str]) -> Path | None:
    tokens = argv[1:] if argv and argv[0] == "build" else argv
    skip = False
    value_flags = {
        "--backend", "--linker", "--target", "--type", "--output", "-o",
        "--profile", "--graph-format", "--graph-output", "--icon", "--nasm", "--gcc",
    }
    for token in tokens:
        if skip:
            skip = False
            continue
        if token in value_flags:
            skip = True
            continue
        if token.startswith("-"):
            continue
        return Path(token)
    return None


def _legacy_target_argv(argv: list[str]) -> list[str]:
    result = list(argv)
    for index, token in enumerate(result[:-1]):
        if token != "--target":
            continue
        value = result[index + 1]
        parts = value.split("-")
        if len(parts) == 3:
            os.environ["ASMPYTHON_TARGET_TRIPLE"] = value
            result[index + 1] = {"macos": "mac"}.get(parts[1], parts[1])
    return result


def _postprocess_artifact(raw: list[str], options: SharedBuildOptions) -> int:
    from .artifact_verify import verify_artifact
    from .debug_support import infer_output_path, write_debug_sidecar
    from .embedded_data import append_resources, collect_files

    artifact = infer_output_path(raw)
    if artifact is None:
        if options.embed_paths or options.debug:
            print(
                "asmpython: cannot determine output path for embedding/debug metadata; pass --output",
                file=sys.stderr,
            )
            return 1
        return 0
    if not artifact.is_file():
        if options.embed_paths or options.debug:
            print(f"asmpython: expected output artifact was not created: {artifact}", file=sys.stderr)
            return 1
        return 0
    if options.embed_paths:
        files = collect_files(options.embed_paths)
        append_resources(artifact, files)
        event(
            "artifact.embedded",
            artifact=artifact,
            files=len(files),
            bytes=sum(len(data) for data in files.values()),
            names=sorted(files),
        )
    if options.debug:
        sidecar = write_debug_sidecar(
            artifact,
            source=_source_path(raw),
            target=os.environ.get("ASMPYTHON_TARGET_TRIPLE") or _option_value(raw, "--target"),
            backend=_option_value(raw, "--backend"),
            linker=_option_value(raw, "--linker"),
            debug_format=options.debug_format,
        )
        event("artifact.debug-sidecar", artifact=artifact, sidecar=sidecar)
    verified = verify_artifact(artifact)
    event("artifact.verification", **verified.as_dict())
    if not verified.valid:
        print(f"asmpython: generated artifact failed verification: {artifact}", file=sys.stderr)
        for error in verified.errors:
            print(f"  {error}", file=sys.stderr)
        return 1
    return 0


def _main_with_options(
    raw: list[str],
    *,
    options: SharedBuildOptions,
    report,
    is_build: bool,
    config_path: Path | None,
) -> int:
    if raw in (["-h"], ["--help"]):
        _print_help()
        return 0
    if raw and raw[0] in REMOVED_COMMANDS:
        replacement = REMOVED_COMMANDS[raw[0]]
        print(f"asmpython: command {raw[0]!r} was removed; use `{replacement}`", file=sys.stderr)
        return 2

    if options.speedy_lossy:
        print(
            "asmpython: speedy-lossy mode enabled; generated code may be slower, larger, or less optimized",
            file=sys.stderr,
        )
    if options.bleach:
        print(
            "asmpython: bleach mode enabled; checks may substantially increase build and runtime cost",
            file=sys.stderr,
        )
    elif options.sanitizers:
        print("asmpython: sanitizers enabled: " + ", ".join(options.sanitizers), file=sys.stderr)

    if _extensions_needed(raw, is_build=is_build) and not _load_extensions(report):
        return 1

    handled = _dispatch_extra(raw)
    if handled is not None:
        return handled
    handled = dispatch(raw)
    if handled is not None:
        return handled
    if not is_build:
        return _host_cli.main(raw, prepare=prepare_argv)

    from .capability_negotiation import negotiate_build
    warn_selected_nonproduction(raw)
    negotiation = negotiate_build(raw)
    event(
        "build.configuration",
        config=None if config_path is None else str(config_path),
        backend=negotiation.backend.name,
        linker=None if negotiation.linker is None else negotiation.linker.name,
        target=negotiation.target,
        output_type=negotiation.output_type,
        speedy_lossy=options.speedy_lossy,
        bleach=options.bleach,
        sanitizers=options.sanitizers,
        fastcomp=options.fastcomp,
        debug=options.debug,
        debug_format=options.debug_format,
        embedded=[str(path) for path in options.embed_paths],
    )
    for warning in negotiation.warnings:
        print(f"asmpython: capability warning: {warning}", file=sys.stderr)
    if negotiation.errors:
        for error in negotiation.errors:
            print(f"asmpython: capability error: {error}", file=sys.stderr)
        return 2

    if options.locked:
        from .build_lock import BuildLockError, enforce_locked_build
        try:
            enforce_locked_build(options.lockfile_path, raw)
        except BuildLockError as exc:
            print(f"asmpython: lock: {exc}", file=sys.stderr)
            return 2
        event("build.lock", path=options.lockfile_path, valid=True)

    if "--graphonly" in raw:
        from .build_plan import BuildPlanError, graphonly_main
        try:
            return graphonly_main(raw)
        except BuildPlanError as exc:
            print(f"asmpython: graph: {exc}", file=sys.stderr)
            return 2

    if options.fastcomp:
        from .fast_state import prepare_state, state_summary
        source = _source_path(raw)
        if source is not None and source.suffix == ".py" and source.is_file():
            state = prepare_state(source, backend=negotiation.backend.name, target=negotiation.target)
            os.environ["ASMPYTHON_FAST_STATE"] = str(state.directory)
            event("fastcomp.state", **state_summary(state))
            print(
                f"asmpython: fastcomp {'hit' if state.hit else 'warmed'}: {state.directory}",
                file=sys.stderr,
            )

    if "--ir-only" in raw:
        from .ir_command import freeze_build_main
        return freeze_build_main(raw)

    previous_traceback_mode = os.environ.get("ASMPYTHON_CLI_MIXED_TRACEBACK")
    os.environ["ASMPYTHON_CLI_MIXED_TRACEBACK"] = "1"
    try:
        result = _host_cli.main(_legacy_target_argv(raw), prepare=prepare_argv)
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
    if result != 0:
        return result
    return _postprocess_artifact(raw, options)


def main(argv: list[str] | None = None) -> int:
    original = list(sys.argv[1:] if argv is None else argv)
    if original and original[0] == "extension":
        from .extension_command import command_main
        return command_main(original[1:])

    try:
        initial_build = _is_build_invocation(original)
        from .build_config import BuildConfigError, apply_build_config
        configured, config_path = apply_build_config(original, is_build=initial_build)
        from .target_triple import TargetTripleError, normalize_target_argv
        configured, triple = normalize_target_argv(configured)
        if triple is not None:
            os.environ["ASMPYTHON_TARGET_TRIPLE"] = triple.canonical
        raw, explicit_options = extract_shared_build_options(configured)
        is_build = _is_build_invocation(raw)
        if is_build:
            prepared = apply_build_profiles(raw)
            prepared, profile_options = extract_shared_build_options(prepared)
            options = _merge_options(profile_options, explicit_options)
        else:
            prepared = raw
            options = explicit_options
    except (BuildConfigError, ProfileError, TargetTripleError, ValueError) as exc:
        print(f"asmpython: {exc}", file=sys.stderr)
        return 2

    option_record = {
        "speedy_lossy": options.speedy_lossy,
        "bleach": options.bleach,
        "sanitizers": list(options.sanitizers),
        "locked": options.locked,
        "lockfile": str(options.lockfile_path),
        "fastcomp": options.fastcomp,
        "debug": options.debug,
        "debug_format": options.debug_format,
        "embed_paths": [str(path) for path in options.embed_paths],
        "config": None if config_path is None else str(config_path),
        "target_triple": None if triple is None else triple.as_dict(),
    }
    with shared_build_options(options):
        with report_session(options.report_path, original, option_record) as report:
            try:
                result = _main_with_options(
                    prepared,
                    options=options,
                    report=report,
                    is_build=is_build,
                    config_path=config_path,
                )
            except BaseException as exc:
                if report is not None:
                    report.write(exit_code=1, error=f"{type(exc).__name__}: {exc}")
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
