"""Global value numbering (LLVM's GVN, dominator-scoped).

Where ``cse`` can only reuse a computation earlier in the *same* block, this
reuses one from any block that **dominates** the current one -- which is exactly
the condition making the earlier value guaranteed to be available:

    entry:   %a = imul %x, %x
             br.t %c, L1, L2
    L1:      %b = imul %x, %x      <- redundant, entry dominates L1
    L2:      %d = imul %x, %x      <- redundant, entry dominates L2

Implemented as a walk of the dominator tree carrying a scoped table of available
expressions, so an expression is visible exactly to the subtree it dominates and
is popped on the way back out. That scoping is what makes it sound; a flat
function-wide table would happily reuse a value from a sibling branch that never
executed.

EXPERIMENTAL -- not in any preset. Known defect: phi operands
==============================================================
This pass rewrites operands through its replacement map without special-casing
``phi``, and a phi operand does not follow the ordinary dominance rule: it must
dominate the **predecessor block its edge comes from**, not the block holding
the phi. Substituting a value that merely dominates the phi is therefore
invalid, and the resulting program reads a value along an edge where it was
never computed. Observed on ``tests/cases/153_functools_module.py`` (output
truncated) and ten other cases in the differential sweep.

Fixing it means handling phi operands per-edge -- checking each replacement
against the dominance of that operand's own predecessor -- rather than skipping
phis, which would silently forgo the redundancy they expose.

Restricted to **pure arithmetic**. ``load``/``gep`` are excluded on purpose:
proving a load still valid across blocks needs alias analysis to rule out an
intervening store or call, and this IR carries boxed heap objects whose aliasing
cannot be reasoned about here. ``cse`` already catches the within-block cases
where invalidation is tractable.
"""

from __future__ import annotations

from .._compiler.cfg import dominators
from .._compiler.ir import IRModule, IRPass, IRValue

#: Expressions whose value depends only on their operands.
_PURE = frozenset({
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

#: Commutative ops -- operands are sorted so `a+b` and `b+a` number alike.
_COMMUTATIVE = frozenset({
    "iadd", "imul", "iand", "ior", "ixor", "fadd", "fmul",
    "icmp.eq", "icmp.ne", "fcmp.eq", "fcmp.ne",
})


def _key(instr, replacement) -> "tuple | None":
    parts = []
    for operand in instr.operands or []:
        if isinstance(operand, IRValue):
            resolved = replacement.get(operand.name, operand)
            name = resolved.name if isinstance(resolved, IRValue) else resolved
            parts.append(("v", name))
        elif isinstance(operand, (int, float, str, bool)):
            parts.append(("c", type(operand).__name__, operand))
        else:
            return None
    if instr.op in _COMMUTATIVE and len(parts) == 2:
        parts = sorted(parts, key=repr)
    try:
        result_type = instr.result.type.name
    except Exception:  # noqa: BLE001
        return None
    return (instr.op, result_type, tuple(parts))


class GVNPass(IRPass):
    name = "gvn"
    description = "global value numbering over the dominator tree"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            if len(func.blocks) < 2:
                continue
            if self._run_func(func):
                changed = True
        return changed

    def _run_func(self, func) -> bool:
        idom = dominators(func)
        if not idom:
            return False

        children: dict[int, list[int]] = {}
        for node, parent in idom.items():
            if parent != node:
                children.setdefault(parent, []).append(node)

        available: dict[tuple, IRValue] = {}
        replacement: dict[str, IRValue] = {}
        changed = False

        # Dominator-tree preorder with an explicit undo log, so leaving a
        # subtree removes exactly the expressions it introduced.
        work: list[tuple[str, int]] = [("enter", 0)]
        scopes: dict[int, list[tuple]] = {}
        while work:
            action, node = work.pop()
            if action == "exit":
                for key in scopes.pop(node, []):
                    available.pop(key, None)
                continue

            introduced: list[tuple] = []
            block = func.blocks[node]
            kept = []
            for instr in block.instrs:
                if instr.operands:
                    instr.operands = [
                        replacement.get(op.name, op) if isinstance(op, IRValue) else op
                        for op in instr.operands
                    ]
                if instr.op not in _PURE or instr.result is None:
                    kept.append(instr)
                    continue
                key = _key(instr, replacement)
                if key is None:
                    kept.append(instr)
                    continue
                prior = available.get(key)
                if prior is not None:
                    replacement[instr.result.name] = prior
                    changed = True
                    continue           # dominating computation already has it
                available[key] = instr.result
                introduced.append(key)
                kept.append(instr)
            block.instrs = kept

            scopes[node] = introduced
            work.append(("exit", node))
            for child in children.get(node, ()):
                work.append(("enter", child))

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
                for instr in block.instrs:
                    if not instr.operands:
                        continue
                    instr.operands = [
                        resolve(op) if isinstance(op, IRValue) else op
                        for op in instr.operands
                    ]
        return changed


__all__ = ["GVNPass"]
