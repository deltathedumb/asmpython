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

from .linux_link import (
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
    runtime_source_path,
    start_source,
)
from .module_codegen import (
    SUPPORTED_OPS,
    compile_functions,
    compile_ir_module,
    run_backend_codegen,
    validate_module,
)

__all__ = [
    "Arm64LinkError",
    "Arm64ToolchainError",
    "LinuxArm64Toolchain",
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
    "run_backend_codegen",
    "runtime_source_path",
    "start_source",
    "validate_module",
]
