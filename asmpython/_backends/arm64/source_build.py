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
from asmpython._compiler.ir import F64, IRInstr, IRValue
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


def _f2i_return_symbols() -> frozenset[str]:
    """Return FFI symbols whose C ABI result is double but Python result is int."""
    symbols: set[str] = set()
    for bindings in STDLIB_BINDINGS.values():
        for binding in bindings.values():
            if getattr(binding, "ret_conv", None) != "f2i":
                continue
            c_name = getattr(binding, "c_name", None)
            c_name_windows = getattr(binding, "c_name_windows", None)
            if c_name:
                symbols.add(c_name)
            if c_name_windows:
                symbols.add(c_name_windows)
    return frozenset(symbols)


_F2I_RETURN_SYMBOLS = _f2i_return_symbols()


def _normalize_ffi_return_conversions(module: Any) -> None:
    """Materialize ``Func(ret_conv='f2i')`` as an explicit IR conversion.

    Shared IR lowering types those calls by their Python-visible ``ret_type``
    (I64), while the C ABI callee returns a double in D0. AArch64 therefore
    captures the call into an F64 temporary and converts it with ``fptosi``.
    """
    for func in getattr(module, "funcs", ()):
        used_names = {
            instr.result.name
            for block in getattr(func, "blocks", ())
            for instr in getattr(block, "instrs", ())
            if instr.result is not None
        }
        serial = 0
        for block in getattr(func, "blocks", ()):
            rewritten: list[IRInstr] = []
            for instr in getattr(block, "instrs", ()):
                target = (
                    instr.operands[0]
                    if instr.op == "call" and instr.operands
                    else None
                )
                if (
                    instr.op == "call"
                    and instr.result is not None
                    and instr.result.type.name == "i64"
                    and isinstance(target, str)
                    and target in _F2I_RETURN_SYMBOLS
                ):
                    original_result = instr.result
                    while True:
                        serial += 1
                        raw_name = f"{original_result.name}__ffi_f2i_{serial}"
                        if raw_name not in used_names:
                            break
                    used_names.add(raw_name)
                    raw_result = IRValue(raw_name, F64)
                    rewritten.append(
                        IRInstr("call", raw_result, list(instr.operands))
                    )
                    rewritten.append(
                        IRInstr("fptosi", original_result, [raw_result])
                    )
                else:
                    rewritten.append(instr)
            block.instrs = rewritten


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
    lowered = ir_lower.lower_module(module)
    _normalize_ffi_return_conversions(lowered)
    return lowered


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
