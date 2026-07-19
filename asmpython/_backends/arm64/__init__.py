"""
asmpython's ARM64 (AArch64) backend -- Stage 1, in progress.

Implemented so far: instruction encoding, AAPCS64 register allocation, current
IR instruction selection, ELF64 relocatable-object emission, and an explicit
Linux executable builder for the currently supported freestanding runtime
slice.

The package deliberately does NOT define ``__module_backend__`` yet. The
functions exported here are experimental APIs for verification and future
driver wiring; advertising a normal backend before broader runtime coverage and
regression gates are complete would overstate compatibility.
"""
from __future__ import annotations

from .elf_inspect import Arm64ElfFormatError, undefined_symbols
from .linux_link import (
    RUNTIME_EXPORTS,
    Arm64LinkError,
    Arm64ToolchainError,
    LinuxArm64Toolchain,
    assemble_file,
    assemble_text,
    build_executable_from_object,
    build_ir_executable,
    build_runtime_object,
    build_start_object,
    discover_toolchain,
    link_objects,
    required_external_symbols,
    runtime_source_path,
    start_source,
    validate_runtime_requirements,
)
from .module_codegen import (
    SUPPORTED_OPS,
    compile_functions,
    compile_ir_module,
    run_backend_codegen,
    validate_module,
)

__all__ = [
    "Arm64ElfFormatError",
    "Arm64LinkError",
    "Arm64ToolchainError",
    "LinuxArm64Toolchain",
    "RUNTIME_EXPORTS",
    "SUPPORTED_OPS",
    "assemble_file",
    "assemble_text",
    "build_executable_from_object",
    "build_ir_executable",
    "build_runtime_object",
    "build_start_object",
    "compile_functions",
    "compile_ir_module",
    "discover_toolchain",
    "link_objects",
    "required_external_symbols",
    "run_backend_codegen",
    "runtime_source_path",
    "start_source",
    "undefined_symbols",
    "validate_module",
    "validate_runtime_requirements",
]
