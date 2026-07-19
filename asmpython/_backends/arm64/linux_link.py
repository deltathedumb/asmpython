"""Experimental Linux AArch64 object assembly and executable linking.

This module is the reusable bridge between the object-only backend and the
future compiler-driver integration. It deliberately remains an explicit API:
``asmpython._backends.arm64`` still does not advertise ``__module_backend__``
because the freestanding runtime covers only a small source subset.

The builder has no host-architecture assumption. On an AArch64 Linux host it
uses native ``as``/``ld``; elsewhere it uses GNU cross-binutils. Tool paths can
be overridden for hermetic builds or alternate toolchains.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .elf_inspect import (
    Arm64ElfFormatError,
    defined_global_symbols,
    undefined_symbols,
)
from .module_codegen import compile_ir_module
from .runtime_manifest import RUNTIME_EXPORTS, RUNTIME_SOURCE_NAMES

# Kept as a private alias for callers/tests written before runtime_manifest.py
# became the single source of truth.
_RUNTIME_SOURCE_NAMES = RUNTIME_SOURCE_NAMES


@dataclass(frozen=True)
class LinuxArm64Toolchain:
    assembler: str
    linker: str
    native: bool


class Arm64ToolchainError(RuntimeError):
    pass


class Arm64LinkError(RuntimeError):
    pass


def _resolve_tool(explicit: str | None, env_name: str, default: str) -> str:
    candidate = explicit or os.environ.get(env_name) or default
    resolved = shutil.which(candidate)
    if resolved is None:
        raise Arm64ToolchainError(
            f"required ARM64 tool {candidate!r} was not found on PATH; "
            f"set {env_name} to an explicit executable path"
        )
    return resolved


def discover_toolchain(
    mode: str = "auto",
    *,
    assembler: str | None = None,
    linker: str | None = None,
) -> LinuxArm64Toolchain:
    """Resolve native or GNU-cross AArch64 assembler/linker executables."""
    if mode not in {"auto", "native", "cross"}:
        raise ValueError(
            f"ARM64 toolchain mode must be auto/native/cross, got {mode!r}"
        )

    machine = platform.machine().lower()
    host_is_arm64 = machine in {"aarch64", "arm64"}
    use_native = host_is_arm64 if mode == "auto" else mode == "native"
    if use_native and not host_is_arm64:
        raise Arm64ToolchainError(
            f"native ARM64 toolchain requested on non-AArch64 host {machine!r}"
        )

    if use_native:
        return LinuxArm64Toolchain(
            assembler=_resolve_tool(assembler, "ASMPYTHON_ARM64_AS", "as"),
            linker=_resolve_tool(linker, "ASMPYTHON_ARM64_LD", "ld"),
            native=True,
        )
    return LinuxArm64Toolchain(
        assembler=_resolve_tool(
            assembler,
            "ASMPYTHON_ARM64_AS",
            "aarch64-linux-gnu-as",
        ),
        linker=_resolve_tool(
            linker,
            "ASMPYTHON_ARM64_LD",
            "aarch64-linux-gnu-ld",
        ),
        native=False,
    )


def _run(command: list[str], *, stage: str) -> None:
    completed = subprocess.run(
        command,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        detail = (
            completed.stderr.strip()
            or completed.stdout.strip()
            or "no diagnostic"
        )
        raise Arm64LinkError(
            f"ARM64 {stage} failed with exit code {completed.returncode}: {detail}"
        )


def assemble_text(
    source: str,
    *,
    toolchain: LinuxArm64Toolchain,
    filename: str = "input.S",
) -> bytes:
    """Assemble one GNU-syntax AArch64 source string into an ELF object."""
    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-as-") as tmp:
        root = Path(tmp)
        source_path = root / filename
        object_path = root / "output.o"
        source_path.write_text(source, encoding="utf-8")
        _run(
            [toolchain.assembler, "-o", str(object_path), str(source_path)],
            stage=f"assembly of {filename}",
        )
        return object_path.read_bytes()


def assemble_file(
    source_path: str | Path,
    *,
    toolchain: LinuxArm64Toolchain,
) -> bytes:
    source = Path(source_path)
    if not source.is_file():
        raise FileNotFoundError(source)
    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-as-") as tmp:
        object_path = Path(tmp) / "output.o"
        _run(
            [toolchain.assembler, "-o", str(object_path), str(source)],
            stage=f"assembly of {source}",
        )
        return object_path.read_bytes()


def _runtime_directory() -> Path:
    return Path(__file__).resolve().parents[2] / "_runtime"


def runtime_source_paths() -> tuple[Path, ...]:
    return tuple(_runtime_directory() / name for name in RUNTIME_SOURCE_NAMES)


def runtime_source_path() -> Path:
    """Backward-compatible path to the original/core runtime source."""
    return runtime_source_paths()[0]


def validate_runtime_object(runtime_object: bytes) -> frozenset[str]:
    """Verify that the merged runtime matches its compatibility declaration."""
    try:
        exports = defined_global_symbols(runtime_object)
        unresolved = undefined_symbols(runtime_object)
    except Arm64ElfFormatError as exc:
        raise Arm64LinkError(
            f"invalid assembled ARM64 runtime object: {exc}"
        ) from exc

    missing = RUNTIME_EXPORTS - exports
    if missing:
        raise Arm64LinkError(
            "assembled ARM64 runtime is missing declared export(s): "
            + ", ".join(sorted(missing))
        )
    if unresolved:
        raise Arm64LinkError(
            "freestanding ARM64 runtime unexpectedly requires external symbol(s): "
            + ", ".join(sorted(unresolved))
        )
    return exports


def _write_object_inputs(root: Path, objects: Iterable[bytes]) -> list[Path]:
    paths: list[Path] = []
    for index, payload in enumerate(objects):
        path = root / f"input-{index}.o"
        path.write_bytes(payload)
        paths.append(path)
    return paths


def link_relocatable(
    objects: Iterable[bytes],
    *,
    toolchain: LinuxArm64Toolchain,
) -> bytes:
    """Merge runtime slices into one still-relocatable AArch64 ELF object."""
    object_list = list(objects)
    if not object_list:
        raise ValueError("at least one ARM64 object is required for relocatable link")
    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-ld-r-") as tmp:
        root = Path(tmp)
        paths = _write_object_inputs(root, object_list)
        output_path = root / "output.o"
        _run(
            [
                toolchain.linker,
                "-r",
                "-o",
                str(output_path),
                *(str(path) for path in paths),
            ],
            stage="relocatable runtime link",
        )
        return output_path.read_bytes()


def build_runtime_object(*, toolchain: LinuxArm64Toolchain) -> bytes:
    """Assemble, merge, and validate all freestanding ARM64 runtime slices."""
    source_objects = [
        assemble_file(path, toolchain=toolchain)
        for path in runtime_source_paths()
    ]
    runtime_object = link_relocatable(source_objects, toolchain=toolchain)
    validate_runtime_object(runtime_object)
    return runtime_object


def start_source(entry_symbol: str = "main") -> str:
    if not entry_symbol or not all(
        char.isalnum() or char in "_.$" for char in entry_symbol
    ):
        raise ValueError(f"invalid AArch64 entry symbol {entry_symbol!r}")
    return (
        ".text\n"
        ".global _start\n"
        ".type _start, %function\n"
        "_start:\n"
        f"    bl {entry_symbol}\n"
        "    mov x8, #93\n"
        "    svc #0\n"
        ".size _start, .-_start\n"
        '.section .note.GNU-stack,"",%progbits\n'
    )


def build_start_object(
    entry_symbol: str = "main",
    *,
    toolchain: LinuxArm64Toolchain,
) -> bytes:
    return assemble_text(
        start_source(entry_symbol),
        toolchain=toolchain,
        filename="start.S",
    )


def required_external_symbols(program_object: bytes) -> frozenset[str]:
    """Return the program object's unresolved symbol requirements."""
    try:
        return undefined_symbols(program_object)
    except Arm64ElfFormatError as exc:
        raise Arm64LinkError(f"invalid ARM64 program object: {exc}") from exc


def validate_runtime_requirements(
    program_object: bytes,
    *,
    include_runtime: bool,
) -> frozenset[str]:
    """Reject symbols not supplied by the selected experimental link set."""
    required = required_external_symbols(program_object)
    available = RUNTIME_EXPORTS if include_runtime else frozenset()
    missing = required - available
    if missing:
        rendered = ", ".join(sorted(missing))
        if include_runtime:
            raise Arm64LinkError(
                "current freestanding ARM64 runtime does not provide required "
                f"symbol(s): {rendered}"
            )
        raise Arm64LinkError(
            "runtime-free ARM64 build has unresolved symbol(s): " + rendered
        )
    return required


def link_objects(
    objects: Iterable[bytes],
    *,
    toolchain: LinuxArm64Toolchain,
    entry_symbol: str = "_start",
) -> bytes:
    """Link ELF objects into a static freestanding Linux AArch64 executable."""
    object_list = list(objects)
    if not object_list:
        raise ValueError("at least one ARM64 object is required for linking")
    with tempfile.TemporaryDirectory(prefix="asmpython-arm64-ld-") as tmp:
        root = Path(tmp)
        paths = _write_object_inputs(root, object_list)
        output_path = root / "output"
        _run(
            [
                toolchain.linker,
                "-e",
                entry_symbol,
                "-o",
                str(output_path),
                *(str(path) for path in paths),
            ],
            stage="link",
        )
        return output_path.read_bytes()


def build_executable_from_object(
    program_object: bytes,
    *,
    toolchain: LinuxArm64Toolchain,
    entry_symbol: str = "main",
    include_runtime: bool = True,
) -> bytes:
    validate_runtime_requirements(
        program_object,
        include_runtime=include_runtime,
    )
    objects = [
        build_start_object(entry_symbol, toolchain=toolchain),
        program_object,
    ]
    if include_runtime:
        objects.append(build_runtime_object(toolchain=toolchain))
    return link_objects(objects, toolchain=toolchain)


def build_ir_executable(
    ir: Any,
    *,
    toolchain: LinuxArm64Toolchain,
    entry_symbol: str = "main",
    include_runtime: bool = True,
) -> bytes:
    """Compile an IRModule and link it into a Linux AArch64 executable."""
    return build_executable_from_object(
        compile_ir_module(ir),
        toolchain=toolchain,
        entry_symbol=entry_symbol,
        include_runtime=include_runtime,
    )
