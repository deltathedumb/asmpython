"""Control-flow graph simplification (LLVM's SimplifyCFG, conservative subset).

Three transforms, in order:

1. **Constant branch folding** -- ``br.t`` on a compile-time-constant condition
   becomes an unconditional ``br`` to the taken side. Also folds ``br.t`` whose
   two targets are the same block.
2. **Unreachable block elimination** -- blocks no longer reachable from the
   entry block are deleted (folding a branch usually orphans one).
3. **Phi repair** -- incoming pairs naming a predecessor that no longer branches
   here are dropped, and a phi left with exactly one incoming value is replaced
   by that value at every use.

Step 3 is what makes 1 and 2 safe: removing a CFG edge without pruning the
corresponding phi operand would leave a phi claiming a value arrives along an
edge that no longer exists.

Pairs naturally with ``constfold``, which is what turns a runtime condition into
the constant this pass folds on -- run ``constfold`` first.

Functions with ``try_regions``
------------------------------
Handled like any other. ``IRFunc.try_regions`` names blocks by LABEL, so
deleting a block cannot silently repoint a region at different code the way
positional indices did. A region whose blocks this pass proves UNREACHABLE is
dropped by the consumers, which is correct: code that cannot execute imposes no
liveness requirement on the register allocator.
"""

from __future__ import annotations

from .._compiler.ir import IRInstr, IRModule, IRPass, IRValue


class SimplifyCFGPass(IRPass):
    name = "simplifycfg"
    description = "fold constant branches, drop unreachable blocks, repair phis"
    preserves = frozenset({"ssa"})       # rewrites the CFG by design

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            if self._run_func(func):
                changed = True
        return changed

    def _run_func(self, func) -> bool:
        if not func.blocks:
            return False
        changed = False

        # ── 1. constant branch folding ───────────────────────────────────────
        consts: dict[str, int] = {}
        for block in func.blocks:
            for instr in block.instrs:
                if (instr.op == "const" and instr.result is not None
                        and instr.operands
                        and isinstance(instr.operands[0], int)
                        and not isinstance(instr.operands[0], bool)):
                    consts[instr.result.name] = instr.operands[0]

        for block in func.blocks:
            if not block.instrs:
                continue
            term = block.instrs[-1]
            if term.op != "br.t" or len(term.operands or []) < 3:
                continue
            cond, t_label, f_label = term.operands[0], term.operands[1], term.operands[2]
            taken: str | None = None
            if isinstance(t_label, str) and isinstance(f_label, str) and t_label == f_label:
                taken = t_label
            else:
                value = None
                if isinstance(cond, IRValue):
                    value = consts.get(cond.name)
                elif isinstance(cond, int) and not isinstance(cond, bool):
                    value = cond
                if value is not None:
                    taken = str(t_label) if value != 0 else str(f_label)
            if taken is not None:
                block.instrs[-1] = IRInstr("br", None, [taken])
                changed = True

        # ── 2. unreachable block elimination ─────────────────────────────────
        by_label = {b.label: b for b in func.blocks}

        def succs(block) -> list[str]:
            if not block.instrs:
                return []
            term = block.instrs[-1]
            if term.op == "br":
                return [str(term.operands[0])] if term.operands else []
            if term.op == "br.t":
                return [str(t) for t in term.operands[1:3] if isinstance(t, str)]
            return []

        reachable: set[str] = set()
        stack = [func.blocks[0].label]
        while stack:
            label = stack.pop()
            if label in reachable or label not in by_label:
                continue
            reachable.add(label)
            stack.extend(succs(by_label[label]))

        if len(reachable) != len(func.blocks):
            func.blocks = [b for b in func.blocks if b.label in reachable]
            changed = True

        # ── 3. phi repair ────────────────────────────────────────────────────
        preds: dict[str, set[str]] = {b.label: set() for b in func.blocks}
        for block in func.blocks:
            for s in succs(block):
                if s in preds:
                    preds[s].add(block.label)

        replacement: dict[str, object] = {}
        for block in func.blocks:
            valid = preds.get(block.label, set())
            for instr in block.instrs:
                if instr.op != "phi":
                    continue
                ops = instr.operands or []
                kept: list = []
                for i in range(0, len(ops) - 1, 2):
                    value, label = ops[i], str(ops[i + 1])
                    if label in valid:
                        kept.extend([value, ops[i + 1]])
                if len(kept) != len(ops):
                    instr.operands = kept
                    changed = True
                if len(kept) == 2 and instr.result is not None:
                    # Exactly one incoming edge: the phi *is* that value.
                    replacement[instr.result.name] = kept[0]

        if replacement:
            def resolve(value):
                seen = 0
                while isinstance(value, IRValue) and value.name in replacement:
                    value = replacement[value.name]
                    seen += 1
                    if seen > 10000:
                        break
                return value

            for block in func.blocks:
                out = []
                for instr in block.instrs:
                    if (instr.op == "phi" and instr.result is not None
                            and instr.result.name in replacement):
                        changed = True
                        continue          # drop the now-degenerate phi
                    if instr.operands:
                        instr.operands = [
                            resolve(op) if isinstance(op, IRValue) else op
                            for op in instr.operands
                        ]
                    out.append(instr)
                block.instrs = out

        return changed


__all__ = ["SimplifyCFGPass"]
