"""Single-file source compilation helpers for the experimental ARM64 backend.

These functions intentionally use the same lexer/parser/sema/IR-lowering path
as the normal compiler. They do not implement project/module loading yet; that
remains part of future driver integration.

The ARM64 backend remains deliberately gated, so target-specific bindings that
are not yet part of the shared x86/legacy surface are installed only while its
source is being analysed. The checked module retains the resolved ``Func``
object after the registry is restored.
"""
from __future__ import annotations

from typing import Any

from .linux_link import LinuxArm64Toolchain, build_executable_from_object
from .module_codegen import compile_ir_module
from asmpython._compiler import ir_lower
from asmpython._compiler.lexer import Lexer
from asmpython._compiler.parser import Parser
from asmpython._compiler.sema import analyze as sema_analyze
from asmpython._compiler.unpack_normalize import normalize_typed_unpacks
from asmpython.stdlib import Func, STDLIB_BINDINGS


_ARM64_MATH_ULP = Func(
    arg_types=("float",),
    ret_type="float",
    c_name="_math_ulp",
)


def _analyze_with_arm64_bindings(module: Any) -> None:
    """Run sema with the gated ARM64-only binding overlay installed."""
    original_math = STDLIB_BINDINGS["math"]
    arm64_math = dict(original_math)
    arm64_math["ulp"] = _ARM64_MATH_ULP
    STDLIB_BINDINGS["math"] = arm64_math
    try:
        sema_analyze(module)
    finally:
        STDLIB_BINDINGS["math"] = original_math


def lower_source(source: str) -> Any:
    """Run one source string through lex, parse, sema, and IR lowering."""
    tokens = Lexer(source).tokenize()
    module = Parser(tokens, frozenset()).parse()
    _analyze_with_arm64_bindings(module)
    normalize_typed_unpacks(module)
    return ir_lower.lower_module(module)


def compile_source_object(source: str) -> bytes:
    """Compile one source string into an AArch64 ELF64 relocatable object."""
    return compile_ir_module(lower_source(source))


def build_source_executable(
    source: str,
    *,
    toolchain: LinuxArm64Toolchain,
    entry_symbol: str = "main",
    include_runtime: bool = True,
) -> bytes:
    """Compile and link one source string into a Linux AArch64 executable."""
    return build_executable_from_object(
        compile_source_object(source),
        toolchain=toolchain,
        entry_symbol=entry_symbol,
        include_runtime=include_runtime,
    )
