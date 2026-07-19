"""Compile a complete target-neutral IR module to an AArch64 ELF object.

This is intentionally object-only. It is used by focused verification and is
the future driver entry point, but the ARM64 package does not expose a
``__module_backend__`` yet because the runtime/ABI support required to link a
normal asmpython program has not been ported.

The per-instruction code generator currently preserves byte offsets for unknown
operations by emitting NOP. That behaviour is useful while developing an
individual dispatch table but must never reach a user-visible object: silently
replacing an instruction is a miscompile. This module therefore validates the
entire IR before register allocation and rejects any unsupported operation with
function and block context.
"""
from __future__ import annotations

from typing import Any

from .codegen import FuncCode, compile_func
from .elf import build_elf
from .regalloc import allocate


SUPPORTED_OPS = frozenset(
    {
        "alloca",
        "bitcast_f2i",
        "bitcast_i2f",
        "br",
        "br.t",
        "call",
        "const",
        "debug_loc",
        "fadd",
        "fcmp.eq",
        "fcmp.ge",
        "fcmp.gt",
        "fcmp.le",
        "fcmp.lt",
        "fcmp.ne",
        "fdiv",
        "fmul",
        "fneg",
        "fptosi",
        "fsub",
        "gep",
        "global_addr",
        "iadd",
        "iand",
        "icmp.eq",
        "icmp.ge",
        "icmp.gt",
        "icmp.le",
        "icmp.lt",
        "icmp.ne",
        "icmp.uge",
        "icmp.ugt",
        "icmp.ule",
        "icmp.ult",
        "idiv",
        "imul",
        "ineg",
        "inot",
        "ior",
        "irem",
        "isub",
        "ixor",
        "load",
        "mov",
        "ret",
        "sar",
        "sext",
        "shl",
        "shr",
        "sitofp",
        "store",
        "str_global",
        "trunc",
        "udiv",
        "urem",
        "zext",
    }
)


def validate_module(ir: Any) -> None:
    """Reject IR operations the current AArch64 dispatch cannot lower."""
    for func in getattr(ir, "funcs", ()):
        for block in getattr(func, "blocks", ()):
            for index, instr in enumerate(getattr(block, "instrs", ())):
                if instr.op not in SUPPORTED_OPS:
                    raise ValueError(
                        "ARM64 backend does not support IR operation "
                        f"{instr.op!r} in function {func.name!r}, "
                        f"block {block.label!r}, instruction {index}"
                    )


def compile_functions(funcs: list[Any], abi: str = "aapcs64") -> list[FuncCode]:
    if abi != "aapcs64":
        raise ValueError(f"ARM64 backend only supports ABI 'aapcs64', got {abi!r}")
    output: list[FuncCode] = []
    for func in funcs:
        allocation = allocate(func, abi)
        output.append(compile_func(func, allocation, abi))
    return output


def compile_ir_module(ir: Any, abi: str = "aapcs64") -> bytes:
    """Return one AArch64 ELF64 relocatable object for ``ir``."""
    validate_module(ir)
    functions = list(getattr(ir, "funcs", ()) or ())
    globals_ = list(getattr(ir, "data", ()) or ())
    return build_elf(compile_functions(functions, abi), globals_)


def run_backend_codegen(ir: Any, args: dict[str, Any] | None = None) -> dict[str, bytes]:
    """Future backend-plugin-shaped object-generation entry point.

    Deliberately not wrapped in ``ModuleBackend`` yet; linking normal compiled
    programs requires the AArch64 runtime port first.
    """
    args = args or {}
    target_os = str(args.get("target_os", "linux")).lower()
    if target_os == "auto":
        target_os = "linux"
    if target_os != "linux":
        raise ValueError(
            "ARM64 object generation currently supports target_os='linux' only, "
            f"got {target_os!r}"
        )
    abi = str(args.get("abi", "aapcs64")).lower()
    return {"output.o": compile_ir_module(ir, abi)}
