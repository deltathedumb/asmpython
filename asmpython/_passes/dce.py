"""Dead code elimination over the neutral IR.

Removes instructions whose result is never used and that have no side effects.
Deliberately conservative: side-effect freedom is a **whitelist**, so any op the
pass doesn't recognize (including every future/target-specific op) is kept.
Iterates to a fixpoint, since removing one instruction can kill its operands.

Not removed: ``call`` (may do anything), ``store``, terminators, and unknown
ops. Traps are preserved -- ``idiv``/``irem``/``udiv``/``urem`` stay even when
dead, because a division by zero is an observable fault.
"""

from __future__ import annotations

from .._compiler.ir import IRModule, IRPass, IRValue

#: Ops with no side effects: safe to delete when their result is unused.
PURE_OPS = frozenset({
    "const", "mov", "phi",
    "iadd", "isub", "imul", "ineg", "inot",
    "iand", "ior", "ixor", "shl", "shr", "sar",
    "icmp.eq", "icmp.ne", "icmp.lt", "icmp.le", "icmp.gt", "icmp.ge",
    "icmp.ult", "icmp.ule", "icmp.ugt", "icmp.uge",
    "fadd", "fsub", "fmul", "fdiv", "fneg",
    "fcmp.eq", "fcmp.ne", "fcmp.lt", "fcmp.le", "fcmp.gt", "fcmp.ge",
    "sext", "zext", "trunc", "sitofp", "fptosi", "fpext", "fptrunc",
    "bitcast_i2f", "bitcast_f2i",
    "gep", "global_addr", "load", "alloca",
})


class DCEPass(IRPass):
    name = "dce"
    description = "delete side-effect-free instructions whose result is unused"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed_any = False
        for func in module.funcs:
            while self._once(func):
                changed_any = True
        return changed_any

    def _once(self, func) -> bool:
        used: set[str] = set()
        for block in func.blocks:
            for instr in block.instrs:
                for operand in instr.operands or []:
                    if isinstance(operand, IRValue):
                        used.add(operand.name)

        changed = False
        for block in func.blocks:
            kept = []
            for instr in block.instrs:
                if (instr.result is not None
                        and instr.op in PURE_OPS
                        and instr.result.name not in used):
                    changed = True
                    continue
                kept.append(instr)
            block.instrs = kept
        return changed


__all__ = ["DCEPass", "PURE_OPS"]
