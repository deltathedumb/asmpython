"""Local common-subexpression elimination (LLVM's EarlyCSE, block-local).

Within a single basic block, a pure computation repeated on identical operands
is computed once and reused::

    %a = gep  %p, 8          %a = gep  %p, 8
    %b = load %a             %b = load %a
    %c = gep  %p, 8     ->   (removed; %c replaced by %a)
    %d = imul %b, %b         %d = imul %b, %b

Deliberately **block-local**: a cross-block CSE would have to prove the earlier
definition dominates every use, which needs dominator info and is unsound on
this IR's memory-SSA form where a value's meaning can change between blocks.
Staying inside one block makes availability trivially true -- straight-line code
with no intervening control flow.

Invalidation: any ``store`` or ``call`` in the block kills every memoized
``load``/``gep``-of-loaded-memory entry, since either may write through a
pointer this block later reads. Pure arithmetic entries survive -- they depend
only on their operand values, which are single-assignment temporaries.
"""

from __future__ import annotations

from .._compiler.ssa.ir import IRModule, IRPass, IRValue

#: Pure arithmetic/compare/convert ops: value depends only on the operands.
_PURE_ARITH = frozenset({
    "iadd", "isub", "imul", "ineg", "inot",
    "iand", "ior", "ixor", "shl", "shr", "sar",
    "icmp.eq", "icmp.ne", "icmp.lt", "icmp.le", "icmp.gt", "icmp.ge",
    "icmp.ult", "icmp.ule", "icmp.ugt", "icmp.uge",
    "fadd", "fsub", "fmul", "fdiv", "fneg",
    "fcmp.eq", "fcmp.ne", "fcmp.lt", "fcmp.le", "fcmp.gt", "fcmp.ge",
    "sext", "zext", "trunc", "sitofp", "fptosi", "fpext", "fptrunc",
    "bitcast_i2f", "bitcast_f2i",
    "const", "global_addr",
})

#: Memory-dependent ops: CSE-able, but invalidated by any store/call.
_MEMORY_OPS = frozenset({"load", "gep"})


def _key(instr) -> "tuple | None":
    """Hashable identity of a computation, or None if it can't be keyed."""
    parts: list = [instr.op]
    for operand in instr.operands or []:
        if isinstance(operand, IRValue):
            parts.append(("v", operand.name))
        elif isinstance(operand, (int, str, float, bool)):
            parts.append(("c", type(operand).__name__, operand))
        else:
            return None
    if instr.result is not None:
        # Two identical computations must also agree on result width/kind --
        # a trunc to i32 and a trunc to i8 key the same otherwise.
        try:
            parts.append(("t", instr.result.type.name))
        except Exception:  # noqa: BLE001
            return None
    return tuple(parts)


class CSEPass(IRPass):
    name = "cse"
    description = "block-local common-subexpression elimination"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            replacement: dict[str, IRValue] = {}
            for block in func.blocks:
                if self._run_block(block, replacement):
                    changed = True
            if replacement:
                self._rewrite(func, replacement)
        return changed

    def _run_block(self, block, replacement: dict) -> bool:
        available: dict[tuple, IRValue] = {}
        out = []
        changed = False
        for instr in block.instrs:
            if instr.op in ("store", "call"):
                # May write through any pointer: drop memoized memory reads.
                available = {
                    k: v for k, v in available.items() if k[0] not in _MEMORY_OPS
                }
                out.append(instr)
                continue

            if (instr.result is None
                    or (instr.op not in _PURE_ARITH and instr.op not in _MEMORY_OPS)):
                out.append(instr)
                continue

            # Operands may already point at an earlier CSE'd value.
            operands = [
                replacement.get(o.name, o) if isinstance(o, IRValue) else o
                for o in (instr.operands or [])
            ]
            instr.operands = operands

            key = _key(instr)
            if key is None:
                out.append(instr)
                continue

            prior = available.get(key)
            if prior is not None:
                replacement[instr.result.name] = prior
                changed = True
                continue          # drop the redundant computation

            available[key] = instr.result
            out.append(instr)
        block.instrs = out
        return changed

    @staticmethod
    def _rewrite(func, replacement: dict) -> None:
        def resolve(value):
            seen = 0
            while isinstance(value, IRValue) and value.name in replacement:
                value = replacement[value.name]
                seen += 1
                if seen > 10000:
                    break
            return value

        for block in func.blocks:
            for instr in block.instrs:
                if not instr.operands:
                    continue
                instr.operands = [
                    resolve(op) if isinstance(op, IRValue) else op
                    for op in instr.operands
                ]


__all__ = ["CSEPass"]
