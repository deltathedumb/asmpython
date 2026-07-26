"""Peephole / algebraic simplification (LLVM's InstCombine, in miniature).

Rewrites individual instructions into cheaper equivalents using algebraic
identities that hold for the IR's integer semantics:

    x + 0  -> x          x * 1  -> x          x & -1 -> x
    x - 0  -> x          x * 0  -> 0          x | 0  -> x
    x ^ 0  -> x          x & 0  -> 0          x << 0 -> x
    x - x  -> 0          x ^ x  -> 0          x & x  -> x
    x * 2^k -> x << k                         x | x  -> x
    x == x -> 1          x < x  -> 0          (integers only)

Deliberately absent: anything relying on float algebra (``x + 0.0`` is NOT ``x``
for -0.0, ``x * 1.0`` is not a no-op for NaN payloads), and signed division by a
power of two (``x / 2`` truncates toward zero, ``x >> 1`` floors -- they differ
for negatives, which is exactly the class of bug this compiler already fights).

Where an instruction reduces to an existing value, the result is replaced at
every use (LLVM's replaceAllUsesWith) rather than emitting a copy, so no
``mov`` chains accumulate. Requires no SSA property: it only ever rewrites a
value into one that already dominates every use, because the replacement is an
*operand of the instruction being removed*.
"""

from __future__ import annotations

from .._compiler.ir import IRInstr, IRModule, IRPass, IRValue

#: Ops whose result is a plain integer computation this pass may rewrite.
_INT_BINOPS = frozenset({
    "iadd", "isub", "imul", "iand", "ior", "ixor", "shl", "shr", "sar",
})
_INT_CMPS = frozenset({
    "icmp.eq", "icmp.ne", "icmp.lt", "icmp.le", "icmp.gt", "icmp.ge",
    "icmp.ult", "icmp.ule", "icmp.ugt", "icmp.uge",
})

#: Comparison of a value with itself, for integers.
_SELF_CMP = {
    "icmp.eq": 1, "icmp.ne": 0,
    "icmp.lt": 0, "icmp.le": 1, "icmp.gt": 0, "icmp.ge": 1,
    "icmp.ult": 0, "icmp.ule": 1, "icmp.ugt": 0, "icmp.uge": 1,
}


def _is_int_type(value) -> bool:
    ty = getattr(value, "type", None)
    try:
        return ty is not None and ty.kind == "int"
    except Exception:  # noqa: BLE001 -- unknown type: refuse to rewrite
        return False


class PeepholePass(IRPass):
    name = "peephole"
    description = "algebraic simplification + strength reduction (instcombine-lite)"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            if self._run_func(func):
                changed = True
        return changed

    def _run_func(self, func) -> bool:
        # name -> constant int, for `const` instructions defining an integer.
        consts: dict[str, int] = {}
        for block in func.blocks:
            for instr in block.instrs:
                if (instr.op == "const" and instr.result is not None
                        and instr.operands
                        and isinstance(instr.operands[0], int)
                        and not isinstance(instr.operands[0], bool)):
                    consts[instr.result.name] = instr.operands[0]

        def const_of(operand):
            if isinstance(operand, bool):
                return None
            if isinstance(operand, int):
                return operand
            if isinstance(operand, IRValue):
                return consts.get(operand.name)
            return None

        replacement: dict[str, object] = {}
        changed = False

        for block in func.blocks:
            out: list[IRInstr] = []
            for instr in block.instrs:
                new_instrs = self._simplify(instr, const_of, replacement)
                if new_instrs is None:
                    out.append(instr)
                else:
                    changed = True
                    out.extend(new_instrs)
            block.instrs = out

        if replacement:
            def resolve(value):
                seen = 0
                while isinstance(value, IRValue) and value.name in replacement:
                    value = replacement[value.name]
                    seen += 1
                    if seen > 10000:      # pathological chain guard
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
        return changed

    def _simplify(self, instr, const_of, replacement) -> "list[IRInstr] | None":
        """Return replacement instructions, or None to keep `instr` as-is."""
        result = instr.result
        op = instr.op
        ops = instr.operands or []
        if result is None or len(ops) != 2:
            return None
        if op not in _INT_BINOPS and op not in _INT_CMPS:
            return None
        if not _is_int_type(result):
            return None

        a, b = ops
        ca, cb = const_of(a), const_of(b)

        # x OP x
        same = (isinstance(a, IRValue) and isinstance(b, IRValue)
                and a.name == b.name)
        if same:
            if op in _SELF_CMP:
                return [IRInstr("const", result, [_SELF_CMP[op]])]
            if op in ("isub", "ixor"):
                return [IRInstr("const", result, [0])]
            if op in ("iand", "ior"):
                replacement[result.name] = a
                return []

        # Identities with a constant operand. Only fold when the *other* side
        # is a value -- two constants are constfold's job, not this pass's.
        def to_value(v):
            replacement[result.name] = v
            return []

        if cb is not None and ca is None:
            if op in ("iadd", "isub", "ior", "ixor", "shl", "shr", "sar") and cb == 0:
                return to_value(a)
            if op == "imul":
                if cb == 1:
                    return to_value(a)
                if cb == 0:
                    return [IRInstr("const", result, [0])]
                if cb > 0 and (cb & (cb - 1)) == 0:
                    # x * 2^k -> x << k  (exact for wrapping integer multiply).
                    # The count MUST be a raw int, not a `const`-defined IRValue:
                    # codegen's `_shift` only takes the safe immediate encoding
                    # (`shl reg, imm`) when the operand is an int. Any other
                    # operand is treated as a variable-count shift, which does an
                    # unconditional `mov rcx, cnt` with no save/restore and
                    # clobbers whatever was in RCX. Emitting an IRValue here
                    # miscompiled base64 as soon as `cse` deduped the constant.
                    shift = cb.bit_length() - 1
                    if shift > 0:
                        return [IRInstr("shl", result, [a, shift])]
                    return to_value(a)
            if op == "iand":
                if cb == 0:
                    return [IRInstr("const", result, [0])]
                if cb == -1:
                    return to_value(a)

        if ca is not None and cb is None:
            if op in ("iadd", "ior", "ixor") and ca == 0:
                return to_value(b)
            if op == "imul":
                if ca == 1:
                    return to_value(b)
                if ca == 0:
                    return [IRInstr("const", result, [0])]
                if ca > 0 and (ca & (ca - 1)) == 0:
                    # Raw int count -- see the mirrored branch above for why.
                    shift = ca.bit_length() - 1
                    if shift > 0:
                        return [IRInstr("shl", result, [b, shift])]
                    return to_value(b)
            if op == "iand":
                if ca == 0:
                    return [IRInstr("const", result, [0])]
                if ca == -1:
                    return to_value(b)
        return None


__all__ = ["PeepholePass"]
