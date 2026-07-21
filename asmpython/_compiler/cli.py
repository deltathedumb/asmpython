"""Extended ASMPython CLI with build subcommands, frozen IR, and fastcomp."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .__main__ import _build_parser, _run_check, _target_out
from .driver import (
    _compile_program,
    _run_backend,
    compile_source,
    compile_targets,
    detect_default_target,
)
from .errors import CompileError, MultiSemaError, explain as _explain_code
from .fastcomp import (
    default_cache_dir,
    fast_compile_module,
    invalidate_cache,
    load_cached_frontend,
    store_cached_frontend,
)
from .irfreeze import freeze_source, load_ir


def _build_group(parser: argparse.ArgumentParser) -> argparse._ArgumentGroup:
    for group in parser._action_groups:
        if group.title == "target / build mode":
            return group
    return parser.add_argument_group("target / build mode")


def _extended_parser() -> argparse.ArgumentParser:
    parser = _build_parser()
    group = _build_group(parser)
    group.add_argument(
        "--ir-only",
        action="store_true",
        help="stop after target-independent IR and write a frozen IR file",
    )
    group.add_argument(
        "--ir-stage",
        choices=["parsed", "typed", "optimized"],
        default="optimized",
        metavar="{parsed,typed,optimized}",
        help="IR stage to freeze with --ir-only (default: optimized)",
    )
    group.add_argument(
        "--ir-output",
        choices=["bin", "json"],
        default="bin",
        metavar="{bin,json}",
        help="frozen IR encoding: fast custom binary cache format or structured JSON",
    )
    group.add_argument(
        "--fastcomp",
        action="store_true",
        help="incrementally assemble changed functions and stitch cached object fragments",
    )
    group.add_argument(
        "--cache-dir",
        type=Path,
        default=None,
        metavar="PATH",
        help="override the fastcomp cache directory (env: ASMPYTHON_CACHE_DIR)",
    )
    return parser


def _normalize_argv(argv: list[str]) -> tuple[str, list[str]]:
    if argv and argv[0] == "build":
        return "build", argv[1:]
    if argv and argv[0] == "invalidate":
        return "invalidate", argv[1:]
    return "build", argv


def _invalidate_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="asmpython invalidate")
    parser.add_argument(
        "source",
        type=Path,
        nargs="?",
        help="only invalidate cache entries for this source file; omit to clear all",
    )
    parser.add_argument("--cache-dir", type=Path, default=None, metavar="PATH")
    args = parser.parse_args(argv)
    root = args.cache_dir or default_cache_dir()
    removed = invalidate_cache(cache_dir=root, source=args.source)
    scope = str(args.source.resolve()) if args.source is not None else "all projects"
    suffix = "entry" if removed == 1 else "entries"
    print(f"asmpython: invalidated {removed} cache {suffix} for {scope}")
    return 0


def _ir_output_path(source: Path, requested: Path | None, output_format: str) -> Path:
    if requested is not None:
        return requested
    build = Path("build")
    build.mkdir(parents=True, exist_ok=True)
    if output_format == "json":
        return build / f"{source.stem}.apir.json"
    return build / f"{source.stem}.apir"


def _validate_modes(
    parser: argparse.ArgumentParser, args: argparse.Namespace, raw_args: list[str]
) -> None:
    if args.ir_only and args.fastcomp:
        parser.error("--ir-only and --fastcomp are mutually exclusive")
    if args.ir_only and args.check:
        parser.error("--ir-only and --check are mutually exclusive")
    if args.ir_only and args.emit_asm:
        parser.error("--ir-only and --emit-asm are mutually exclusive")
    if args.fastcomp and args.emit_asm:
        parser.error("--fastcomp and --emit-asm are mutually exclusive")
    explicit_ir_option = any(
        value == "--ir-stage"
        or value.startswith("--ir-stage=")
        or value == "--ir-output"
        or value.startswith("--ir-output=")
        for value in raw_args
    )
    if not args.ir_only and explicit_ir_option:
        parser.error("--ir-stage and --ir-output require --ir-only")


def _compile_frozen(
    module: object,
    targets: list[str],
    out_paths: list[Path],
    args: argparse.Namespace,
) -> None:
    for target, out_path in zip(targets, out_paths):
        _run_backend(
            module,
            target,
            out_path,
            emit_asm_only=args.emit_asm,
            keep_intermediates=args.keep,
            keep_assembly=args.keep_assembly,
            use_runtime_lib=args.use_runtime_lib or args.bundle_mode == "onedir",
            nasm_path=args.nasm,
            gcc_path=args.gcc,
            bundle_mode=args.bundle_mode,
            output_type=args.output_type,
            icon_path=args.icon,
            _asm_stem_suffix=f"-{target}" if len(targets) > 1 else "",
        )


def main(argv: list[str] | None = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    command, command_argv = _normalize_argv(raw_argv)
    if command == "invalidate":
        return _invalidate_main(command_argv)

    parser = _extended_parser()
    args = parser.parse_args(command_argv)
    _validate_modes(parser, args, command_argv)

    if args.explain is not None:
        desc = _explain_code(args.explain)
        if desc:
            print(desc)
            return 0
        print(f"asmpython: unknown error code {args.explain!r}", file=sys.stderr)
        return 1
    if args.source is None:
        parser.print_usage(sys.stderr)
        print("asmpython: error: no source file given (try `asmpython --help`)", file=sys.stderr)
        return 2
    if not args.source.exists():
        print(f"asmpython: source file not found: {args.source}", file=sys.stderr)
        return 1

    all_errors = not args.one_error
    source_path = args.source.resolve()
    frozen_input = source_path.suffix == ".apir" or source_path.name.endswith(".apir.json")

    try:
        if args.ir_only:
            if frozen_input:
                parser.error("--ir-only expects Python source, not an existing frozen IR file")
            source = source_path.read_text(encoding="utf-8")
            output_path = _ir_output_path(source_path, args.output, args.ir_output)
            frozen = freeze_source(
                source,
                source_path,
                output_path,
                stage=args.ir_stage,
                output=args.ir_output,
                all_errors=all_errors,
            )
            print(
                f"wrote {output_path} ({args.ir_stage} IR, {args.ir_output}, "
                f"{len(frozen.metadata.get('components', {}))} components)"
            )
            return 0

        if frozen_input:
            frozen = load_ir(source_path)
            module = frozen.module
            source = ""
        else:
            source = source_path.read_text(encoding="utf-8")
            if args.check:
                return _run_check(
                    source,
                    source_path,
                    source_dir=source_path.parent,
                    as_json=args.json,
                    all_errors=all_errors,
                )
            module = None

        targets: list[str] = args.target or [detect_default_target()]
        base_stem = Path("build") / source_path.with_suffix("").name
        single = len(targets) == 1
        if args.output is None:
            base_stem.parent.mkdir(parents=True, exist_ok=True)
            out_paths = [
                _target_out(base_stem, target, args.output_type) for target in targets
            ]
        else:
            stem = args.output.with_suffix("")
            if single:
                out_paths = [args.output]
            else:
                out_paths = [
                    _target_out(stem, target, args.output_type) for target in targets
                ]
            for path in out_paths:
                path.parent.mkdir(parents=True, exist_ok=True)

        use_runtime_lib = args.use_runtime_lib or args.bundle_mode == "onedir"
        if frozen_input and args.fastcomp:
            for target, out_path in zip(targets, out_paths):
                fast_compile_module(
                    module,
                    target,
                    out_path,
                    source_path=source_path,
                    cache_dir=args.cache_dir,
                    keep_assembly=args.keep_assembly,
                    use_runtime_lib=use_runtime_lib,
                    nasm_path=args.nasm,
                    gcc_path=args.gcc,
                    bundle_mode=args.bundle_mode,
                    output_type=args.output_type,
                    icon_path=args.icon,
                )
        elif frozen_input:
            _compile_frozen(module, targets, out_paths, args)
        elif args.fastcomp:
            module = load_cached_frontend(source_path, cache_dir=args.cache_dir)
            if module is None:
                module = _compile_program(
                    source,
                    source_dir=source_path.parent,
                    entry_path=source_path,
                    whole_program=True,
                    all_errors=all_errors,
                )
                store_cached_frontend(module, source_path, cache_dir=args.cache_dir)
            else:
                print("fastcomp: reused cached optimized IR")
            for target, out_path in zip(targets, out_paths):
                fast_compile_module(
                    module,
                    target,
                    out_path,
                    source_path=source_path,
                    cache_dir=args.cache_dir,
                    keep_assembly=args.keep_assembly,
                    use_runtime_lib=use_runtime_lib,
                    nasm_path=args.nasm,
                    gcc_path=args.gcc,
                    bundle_mode=args.bundle_mode,
                    output_type=args.output_type,
                    icon_path=args.icon,
                )
        elif single:
            compile_source(
                source,
                targets[0],
                out_paths[0],
                emit_asm_only=args.emit_asm,
                keep_intermediates=args.keep,
                keep_assembly=args.keep_assembly,
                use_runtime_lib=use_runtime_lib,
                nasm_path=args.nasm,
                gcc_path=args.gcc,
                bundle_mode=args.bundle_mode,
                source_dir=source_path.parent,
                entry_path=source_path,
                output_type=args.output_type,
                icon_path=args.icon,
                all_errors=all_errors,
            )
        else:
            compile_targets(
                source,
                targets,
                out_paths,
                emit_asm_only=args.emit_asm,
                keep_intermediates=args.keep,
                keep_assembly=args.keep_assembly,
                use_runtime_lib=use_runtime_lib,
                nasm_path=args.nasm,
                gcc_path=args.gcc,
                bundle_mode=args.bundle_mode,
                source_dir=source_path.parent,
                entry_path=source_path,
                output_type=args.output_type,
                icon_path=args.icon,
                all_errors=all_errors,
            )
    except MultiSemaError as error:
        text = "" if frozen_input else source
        print(error.format_all(text, str(source_path)), file=sys.stderr)
        return 1
    except CompileError as error:
        text = "" if frozen_input else source
        print(error.format(text, str(source_path)), file=sys.stderr)
        return 1
    except Exception as error:
        print(f"asmpython: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
