"""
asmpython's ARM64 (AArch64) backend -- Stage 1, in progress.

Implemented so far: instruction encoding, AAPCS64 register allocation, current
IR instruction selection, and ELF64 relocatable-object emission. The public
functions below compile an ``IRModule`` into ``output.o`` for verification and
future driver wiring.

The package deliberately does NOT define ``__module_backend__`` yet. Advertising
a normal driver backend before the AArch64 runtime/ABI objects exist would let
users select a target that can produce an object but cannot link ordinary
asmpython programs correctly.
"""
from __future__ import annotations

from .module_codegen import (
    SUPPORTED_OPS,
    compile_functions,
    compile_ir_module,
    run_backend_codegen,
    validate_module,
)

__all__ = [
    "SUPPORTED_OPS",
    "compile_functions",
    "compile_ir_module",
    "run_backend_codegen",
    "validate_module",
]
