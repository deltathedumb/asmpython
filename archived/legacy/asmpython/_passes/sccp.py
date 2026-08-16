"""Sparse conditional constant propagation, and comparison simplification.

``sccp``     constant propagation that also tracks block reachability
``cmpfold``  simplify comparisons whose answer is implied by a constant bound
``brfold``   turn ``br.t`` on a value known to be boolean-constant into ``br``

SCCP is stronger than running ``constfold`` and ``simplifycfg`` separately: it
propagates constants and reachability *at the same time*, so a value that is
only non-constant along a path that can never execute is still treated as
constant. Classic case::

    if 0:
        x = 1
    else:
        x = 2
    use(x)          # SCCP: x is 2; plain constfold: x is unknown

This is the lattice-based formulation (undefined -> constant -> overdefined)
restricted to integer values, with the phi rule that only considers operands
arriving on reachable edges.
"""

from __future__ import annotations

from .._compiler.ssa.ir import IRInstr, IRModule, IRPass, IRValue

_TOP = "top"          # not yet known (undefined)
_BOTTOM = "bottom"    # overdefined / not constant

_FOLDABLE = {
    "iadd": lambda a, b: a + b,
    "isub": lambda a, b: a - b,
    "imul": lambda a, b: a * b,
    "iand": lambda a, b: a & b,
    "ior": lambda a, b: a | b,
    "ixor": lambda a, b: a ^ b,
    "icmp.eq": lambda a, b: int(a == b),
    "icmp.ne": lambda a, b: int(a != b),
    "icmp.lt": lambda a, b: int(a < b),
    "icmp.le": lambda a, b: int(a <= b),
    "icmp.gt": lambda a, b: int(a > b),
    "icmp.ge": lambda a, b: int(a >= b),
}


def _wrap(raw: int, value) -> "int | None":
    try:
        ty = value.type
        if ty.kind != "int":
            return None
        bits = ty.bits
    except Exception:  # noqa: BLE001
        return None
    raw &= (1 << bits) - 1
    if ty.signed and raw >= (1 << (bits - 1)):
        raw -= 1 << bits
    return raw


class SCCPPass(IRPass):
    name = "sccp"
    description = "sparse conditional constant propagation (constants + reachability)"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            if self._run_func(func):
                changed = True
        return changed

    def _run_func(self, func) -> bool:
        if not func.blocks:
            return False
        by_label = {b.label: b for b in func.blocks}
        values: dict[str, object] = {}          # name -> int | _TOP | _BOTTOM

        # Function parameters are supplied by the caller, so they are
        # OVERDEFINED from the start -- never _TOP. Leaving them undefined is
        # not a missed optimization, it is unsound: `_TOP` propagates through
        # the loop-guard comparison, a `_TOP` branch condition marks NEITHER
        # successor reachable, so the latch never becomes reachable, and the
        # loop header's phi is then evaluated against its entry operand alone
        # and materialized as a constant. That folds a loop counter to its
        # initial value and produces a program that never terminates.
        for param in func.params:
            values[param.name] = _BOTTOM

        def lattice(operand):
            if isinstance(operand, bool):
                return _BOTTOM
            if isinstance(operand, int):
                return operand
            if isinstance(operand, IRValue):
                return values.get(operand.name, _TOP)
            return _BOTTOM

        def meet(a, b):
            if a is _TOP:
                return b
            if b is _TOP:
                return a
            if a is _BOTTOM or b is _BOTTOM or a != b:
                return _BOTTOM
            return a

        # Iterate to a fixpoint. Reachability is MONOTONIC -- it accumulates
        # across rounds and is never rebuilt. That is not an optimization, it
        # is what makes the analysis sound on loops: a loop header's phi is
        # first evaluated before the back edge has been proven reachable, so
        # only the entry operand is visible and the phi looks constant. If the
        # reachable set were rebuilt each round, that premature conclusion
        # would be re-derived forever and materialized -- folding a loop
        # counter to its initial value and producing a program that never
        # terminates. Because the set only grows, a later round sees the back
        # edge, meets in the second operand, and the value correctly drops to
        # overdefined.
        reachable = {func.blocks[0].label}
        for _ in range(256):
            progressed = False
            work = [func.blocks[0].label]
            visited: set[str] = set()
            while work:
                label = work.pop()
                if label in visited:
                    continue
                visited.add(label)
                block = by_label.get(label)
                if block is None or not block.instrs:
                    continue
                for instr in block.instrs:
                    if instr.result is None:
                        continue
                    name = instr.result.name
                    before = values.get(name, _TOP)
                    after = self._eval(instr, lattice, meet, reachable)
                    if after != before:
                        values[name] = after
                        progressed = True

                term = block.instrs[-1]
                targets: list[str] = []
                if term.op == "br" and term.operands:
                    targets = [str(term.operands[0])]
                elif term.op == "br.t" and len(term.operands or []) >= 3:
                    cond = lattice(term.operands[0])
                    t, f = str(term.operands[1]), str(term.operands[2])
                    if isinstance(cond, int):
                        targets = [t if cond != 0 else f]
                    elif cond is _TOP:
                        targets = []          # undefined so far: assume no edge
                    else:
                        targets = [t, f]
                for tgt in targets:
                    if tgt in by_label:
                        if tgt not in reachable:
                            reachable.add(tgt)
                            progressed = True
                        if tgt not in visited:
                            work.append(tgt)
            if not progressed:
                break

        # Materialize: every value proven constant becomes a `const`.
        changed = False
        for block in func.blocks:
            if block.label not in reachable:
                continue
            for i, instr in enumerate(block.instrs):
                if instr.result is None or instr.op == "const":
                    continue
                value = values.get(instr.result.name, _TOP)
                if isinstance(value, int) and not isinstance(value, bool):
                    block.instrs[i] = IRInstr("const", instr.result, [value])
                    changed = True
        return changed

    @staticmethod
    def _eval(instr, lattice, meet, reachable):
        op = instr.op
        ops = instr.operands or []

        if op == "const":
            if ops and isinstance(ops[0], int) and not isinstance(ops[0], bool):
                return ops[0]
            return _BOTTOM

        if op == "phi":
            acc = _TOP
            for i in range(0, len(ops) - 1, 2):
                label = str(ops[i + 1])
                if label not in reachable:
                    continue                  # edge can't execute: ignore it
                acc = meet(acc, lattice(ops[i]))
                if acc is _BOTTOM:
                    break
            return acc

        fold = _FOLDABLE.get(op)
        if fold is None or len(ops) != 2:
            return _BOTTOM
        a, b = lattice(ops[0]), lattice(ops[1])
        if a is _TOP or b is _TOP:
            return _TOP
        if a is _BOTTOM or b is _BOTTOM:
            return _BOTTOM
        try:
            raw = fold(a, b)
        except Exception:  # noqa: BLE001
            return _BOTTOM
        wrapped = _wrap(raw, instr.result)
        return _BOTTOM if wrapped is None else wrapped


class CmpFoldPass(IRPass):
    """Fold comparisons whose result is implied regardless of the operand.

    Unsigned values are never negative, so ``x <u 0`` is always false and
    ``x >=u 0`` always true -- independent of what ``x`` holds.
    """

    name = "cmpfold"
    description = "fold comparisons with a constant-implied answer"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            consts = {}
            for block in func.blocks:
                for instr in block.instrs:
                    if (instr.op == "const" and instr.result is not None
                            and instr.operands and isinstance(instr.operands[0], int)
                            and not isinstance(instr.operands[0], bool)):
                        consts[instr.result.name] = instr.operands[0]

            for block in func.blocks:
                for i, instr in enumerate(block.instrs):
                    if instr.result is None or instr.op not in ("icmp.ult", "icmp.uge"):
                        continue
                    ops = instr.operands or []
                    if len(ops) != 2:
                        continue
                    rhs = ops[1]
                    value = (consts.get(rhs.name) if isinstance(rhs, IRValue)
                             else rhs if isinstance(rhs, int) else None)
                    if value != 0:
                        continue
                    answer = 0 if instr.op == "icmp.ult" else 1
                    block.instrs[i] = IRInstr("const", instr.result, [answer])
                    changed = True
        return changed


__all__ = ["SCCPPass", "CmpFoldPass"]
