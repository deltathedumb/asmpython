"""Experimental single-file Linux AArch64 build command.

This is intentionally separate from the normal ``asmpython --backend`` surface
until runtime coverage and regression gates are broad enough to advertise ARM64
as a general backend.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .linux_link import (
    RUNTIME_EXPORTS,
    Arm64LinkError,
    Arm64ToolchainError,
    build_executable_from_object,
    discover_toolchain,
    required_external_symbols,
    validate_runtime_requirements,
)
from .source_build import compile_source_object
from asmpython._compiler.errors import CompileError, MultiSemaError
from asmpython._compiler.ir_lower import LowerError


def _default_object_path(source: Path) -> Path:
    return source.with_name(source.stem + ".arm64.o")


def _default_executable_path(source: Path) -> Path:
    return source.with_name(source.stem + "-arm64")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)

    object_parser = subcommands.add_parser(
        "object",
        help="compile one source file to an AArch64 ELF relocatable object",
    )
    object_parser.add_argument("source", type=Path)
    object_parser.add_argument("-o", "--output", type=Path)

    requirements_parser = subcommands.add_parser(
        "requirements",
        help="show unresolved symbols and current runtime compatibility",
    )
    requirements_parser.add_argument("source", type=Path)
    requirements_parser.add_argument(
        "--runtime",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="evaluate against the current freestanding runtime (default: yes)",
    )

    build_parser = subcommands.add_parser(
        "build",
        help="build a static freestanding Linux AArch64 executable",
    )
    build_parser.add_argument("source", type=Path)
    build_parser.add_argument("-o", "--output", type=Path)
    build_parser.add_argument(
        "--mode",
        choices=("auto", "native", "cross"),
        default="auto",
        help="native or GNU cross toolchain selection",
    )
    build_parser.add_argument("--assembler", help="override assembler executable")
    build_parser.add_argument("--linker", help="override linker executable")
    build_parser.add_argument("--entry", default="main", help="source entry function")
    build_parser.add_argument(
        "--runtime",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="link the current freestanding runtime (default: yes)",
    )
    return parser


def _read_source(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"cannot read source file {path}: {exc}") from exc


def _compile(path: Path) -> tuple[str, bytes]:
    source = _read_source(path)
    return source, compile_source_object(source)


def _write(path: Path, payload: bytes, *, executable: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    if executable:
        path.chmod(path.stat().st_mode | 0o111)


def _format_failure(exc: BaseException, source: str, filename: str) -> str:
    if isinstance(exc, CompileError):
        return exc.format(source, filename)
    if isinstance(exc, MultiSemaError):
        return exc.format_all(source, filename)
    return f"{filename}: {exc}"


def _run_object(args: argparse.Namespace, source: str, object_blob: bytes) -> int:
    output = args.output or _default_object_path(args.source)
    _write(output, object_blob)
    print(f"wrote AArch64 object: {output}")
    return 0


def _run_requirements(args: argparse.Namespace, object_blob: bytes) -> int:
    required = required_external_symbols(object_blob)
    available = RUNTIME_EXPORTS if args.runtime else frozenset()
    missing = required - available

    print("required symbols:")
    if required:
        for symbol in sorted(required):
            status = "available" if symbol in available else "missing"
            print(f"  {symbol} [{status}]")
    else:
        print("  (none)")
    print(
        "compatibility: "
        + ("compatible" if not missing else "unsupported by selected runtime")
    )
    return 0 if not missing else 1


def _run_build(args: argparse.Namespace, object_blob: bytes) -> int:
    # Compatibility is checked before tool discovery so an unsupported Python
    # feature reports its precise missing _abi_* surface even on a machine with
    # no AArch64 toolchain installed.
    validate_runtime_requirements(
        object_blob,
        include_runtime=args.runtime,
    )
    toolchain = discover_toolchain(
        args.mode,
        assembler=args.assembler,
        linker=args.linker,
    )
    executable = build_executable_from_object(
        object_blob,
        toolchain=toolchain,
        entry_symbol=args.entry,
        include_runtime=args.runtime,
    )
    output = args.output or _default_executable_path(args.source)
    _write(output, executable, executable=True)
    mode = "native" if toolchain.native else "cross"
    print(f"wrote Linux AArch64 executable ({mode} toolchain): {output}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    source = ""
    try:
        source, object_blob = _compile(args.source)
        if args.command == "object":
            return _run_object(args, source, object_blob)
        if args.command == "requirements":
            return _run_requirements(args, object_blob)
        if args.command == "build":
            return _run_build(args, object_blob)
        parser.error(f"unknown command {args.command!r}")
    except (
        CompileError,
        MultiSemaError,
        LowerError,
        Arm64LinkError,
        Arm64ToolchainError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(_format_failure(exc, source, str(args.source)), file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
