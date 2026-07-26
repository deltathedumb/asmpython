"""Constant folding + constant propagation over the neutral IR.

Folds integer arithmetic/bitwise/comparison instructions whose operands are all
compile-time constants into a single ``const``. Runs to a fixpoint so chains
(``a=1+2; b=a*3``) collapse fully.

Results are wrapped to the *result type's* width and signedness, so folding
matches what the machine would have computed (an i32 add that overflows folds to
the same wrapped value the hardware produces) -- constant folding must never
change program behavior.
"""

from __future__ import annotations

from .._compiler.ir import IRInstr, IRModule, IRPass, IRValue


def _wrap(value: int, ty) -> int | None:
    """Wrap ``value`` into ``ty``'s range, or None if not an integer type."""
    try:
        if ty is None or ty.kind != "int":
            return None
        bits = ty.bits
    except Exception:  # noqa: BLE001 -- malformed/unknown type: don't fold
        return None
    if bits <= 0:
        return None
    value &= (1 << bits) - 1
    if ty.signed and value >= (1 << (bits - 1)):
        value -= 1 << bits
    return value


def _fold(op: str, a: int, b: int | None) -> int | None:
    if b is None:
        if op == "ineg":
            return -a
        if op == "inot":
            return ~a
        return None
    if op == "iadd":
        return a + b
    if op == "isub":
        return a - b
    if op == "imul":
        return a * b
    if op == "iand":
        return a & b
    if op == "ior":
        return a | b
    if op == "ixor":
        return a ^ b
    if op == "shl":
        return a << b if 0 <= b < 64 else None
    if op in ("idiv", "irem", "udiv", "urem"):
        if b == 0:
            return None  # never fold a trap away
        # C/LLVM semantics: truncate toward zero (Python's // floors).
        q = abs(a) // abs(b)
        if op in ("idiv", "udiv"):
            return -q if (a < 0) != (b < 0) else q
        return a - b * (-q if (a < 0) != (b < 0) else q)
    if op in ("icmp.eq", "icmp.ne", "icmp.lt", "icmp.le", "icmp.gt", "icmp.ge"):
        table = {
            "icmp.eq": a == b, "icmp.ne": a != b, "icmp.lt": a < b,
            "icmp.le": a <= b, "icmp.gt": a > b, "icmp.ge": a >= b,
        }
        return 1 if table[op] else 0
    return None


class ConstFoldPass(IRPass):
    name = "constfold"
    description = "fold constant integer arithmetic/compares into const"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed_any = False
        for func in module.funcs:
            while self._once(func):
                changed_any = True
        return changed_any

    def _once(self, func) -> bool:
        # name -> constant int, for every `const` defining an integer.
        consts: dict[str, int] = {}
        for block in func.blocks:
            for instr in block.instrs:
                if (instr.op == "const" and instr.result is not None
                        and instr.operands and isinstance(instr.operands[0], int)
                        and not isinstance(instr.operands[0], bool)):
                    consts[instr.result.name] = instr.operands[0]

        def as_const(operand) -> int | None:
            if isinstance(operand, bool):
                return None
            if isinstance(operand, int):
                return operand
            if isinstance(operand, IRValue):
                return consts.get(operand.name)
            return None

        changed = False
        for block in func.blocks:
            for i, instr in enumerate(block.instrs):
                if instr.op == "const" or instr.result is None:
                    continue
                if instr.result.name in consts:
                    continue
                ops = instr.operands or []
                if not (1 <= len(ops) <= 2):
                    continue
                a = as_const(ops[0])
                if a is None:
                    continue
                b = as_const(ops[1]) if len(ops) == 2 else None
                if len(ops) == 2 and b is None:
                    continue
                raw = _fold(instr.op, a, b)
                if raw is None:
                    continue
                folded = _wrap(raw, instr.result.type)
                if folded is None:
                    continue
                block.instrs[i] = IRInstr("const", instr.result, [folded])
                consts[instr.result.name] = folded
                changed = True
        return changed


__all__ = ["ConstFoldPass"]
