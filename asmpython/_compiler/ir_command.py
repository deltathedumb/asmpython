"""Public ``asmpython ir`` command and ``build --ir-only`` handling."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .driver import _run_backend, detect_default_target
from .irfreeze import freeze_source, inspect_ir, load_ir


class IRCommandError(RuntimeError):
    pass


def resolve_ir_path(value: str | Path) -> Path:
    requested = Path(value)
    candidates = [requested]
    if not requested.suffix:
        candidates.extend((
            requested.with_suffix(".apir"),
            Path("build") / requested.with_suffix(".apir"),
            Path("build") / f"{requested.name}.apir.json",
        ))
    elif requested.suffix == ".apir":
        candidates.append(Path("build") / requested.name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    raise IRCommandError(f"frozen IR not found: {value}")


def _parse_targets(value: str | None) -> list[str]:
    if value is None:
        return [detect_default_target()]
    targets = [item.strip() for item in value.replace(",", " ").split() if item.strip()]
    if not targets:
        raise IRCommandError("--target requires at least one target")
    return targets


def _default_backend(targets: list[str]) -> str:
    if any(target in {"freestanding", "freestanding16"} for target in targets):
        return "legacy"
    return "x86-64"


def _output_for(base: Path, target: str, output_type: str) -> Path:
    if output_type == "library":
        return base.with_suffix(".dll" if target == "windows" else ".so")
    if target == "windows":
        return base.with_suffix(".exe")
    if target == "freestanding":
        return base.with_suffix(".bin")
    if target == "freestanding16":
        return base.with_suffix(".img")
    return base


def command_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="asmpython ir",
        description="Inspect or compile a frozen ASMPython IR file.",
    )
    parser.add_argument("irname", help="IR path or name (searches the current directory and build/)")
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--target", default=None, help="target or comma-separated targets")
    parser.add_argument("--backend", default=None)
    parser.add_argument("--linker", default=None)
    parser.add_argument("--type", dest="output_type", choices=["executable", "library"], default="executable")
    parser.add_argument("--info", action="store_true", help="show metadata without compiling")
    parser.add_argument("--json", action="store_true", help="emit metadata/results as JSON")
    parser.add_argument("--keep", action="store_true")
    parser.add_argument("--keep-assembly", action="store_true")
    parser.add_argument("--use-runtime-lib", action="store_true")
    parser.add_argument("--onefile", dest="bundle_mode", action="store_const", const="onefile", default="onefile")
    parser.add_argument("--onedir", dest="bundle_mode", action="store_const", const="onedir")
    parser.add_argument("--nasm", type=Path, default=None)
    parser.add_argument("--gcc", type=Path, default=None)
    args = parser.parse_args(argv)

    try:
        path = resolve_ir_path(args.irname)
        summary = inspect_ir(path)
        if args.info:
            if args.json:
                print(json.dumps(summary, indent=2, sort_keys=True))
            else:
                metadata = summary.get("metadata", {})
                print(f"IR: {path}")
                print(f"encoding: {summary.get('encoding')}")
                print(f"format version: {metadata.get('format_version', '?')}")
                print(f"compiler: {metadata.get('compiler_version', '?')}")
                print(f"stage: {metadata.get('stage', '?')}")
                print(f"source: {metadata.get('source_path', '?')}")
                print(f"components: {len(metadata.get('components', {}))}")
                print(f"payload: {summary.get('payload_bytes', 0)} bytes")
            return 0

        frozen = load_ir(path)
        targets = _parse_targets(args.target)
        backend = args.backend or _default_backend(targets)
        stem_name = path.name[:-10] if path.name.endswith(".apir.json") else path.stem
        base = args.output.with_suffix("") if args.output is not None else Path("build") / stem_name
        base.parent.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, str]] = []
        for target in targets:
            output = args.output if args.output is not None and len(targets) == 1 else _output_for(base, target, args.output_type)
            result = _run_backend(
                frozen.module,
                target,
                output,
                keep_intermediates=args.keep,
                keep_assembly=args.keep_assembly,
                use_runtime_lib=args.use_runtime_lib,
                nasm_path=args.nasm,
                gcc_path=args.gcc,
                bundle_mode=args.bundle_mode,
                output_type=args.output_type,
                backend=backend,
                linker=args.linker,
                _asm_stem_suffix=f"-{target}" if len(targets) > 1 else "",
            )
            final = result.exe_path or result.obj_path or result.asm_path
            results.append({"target": target, "backend": backend, "output": str(final)})
        if args.json:
            print(json.dumps(results, indent=2, sort_keys=True))
        return 0
    except (OSError, ValueError, TypeError, IRCommandError, NotImplementedError) as exc:
        print(f"asmpython: ir: {exc}", file=sys.stderr)
        return 1


def freeze_build_main(argv: list[str]) -> int:
    """Handle ``asmpython build SOURCE --ir-only`` before the legacy parser."""
    forwarded = list(argv)
    if forwarded and forwarded[0] == "build":
        forwarded = forwarded[1:]
    parser = argparse.ArgumentParser(prog="asmpython build --ir-only")
    parser.add_argument("source", type=Path)
    parser.add_argument("--ir-only", action="store_true", required=True)
    parser.add_argument("--ir-stage", choices=["parsed", "typed", "optimized"], default="optimized")
    parser.add_argument("--ir-output", choices=["bin", "json"], default="bin")
    parser.add_argument("-o", "--output", type=Path, default=None)
    parser.add_argument("--one-error", action="store_true")
    args = parser.parse_args(forwarded)
    if not args.source.is_file():
        print(f"asmpython: source file not found: {args.source}", file=sys.stderr)
        return 1
    suffix = ".apir.json" if args.ir_output == "json" else ".apir"
    output = args.output or Path("build") / f"{args.source.stem}{suffix}"
    try:
        source = args.source.read_text(encoding="utf-8")
        frozen = freeze_source(
            source,
            args.source,
            output,
            stage=args.ir_stage,
            output=args.ir_output,
            all_errors=not args.one_error,
        )
    except Exception as exc:
        print(f"asmpython: IR freeze failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"wrote {output} ({args.ir_stage} IR, {args.ir_output}, "
        f"{len(frozen.metadata.get('components', {}))} components)"
    )
    return 0
