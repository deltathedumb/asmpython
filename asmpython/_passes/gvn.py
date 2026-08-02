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

EXPERIMENTAL -- not in any preset.
=================================
The original "phi operand" diagnosis for this pass was wrong. Those divergences
came from the IR not satisfying SSA dominance at all (lowering emitted allocas
into whichever block first needed them); once allocas moved to the entry block,
they went away without any change here.

Loop-varying reuse was the next layer, and is fixed in ``_run_func``: an
instruction inside a loop has one SSA name but a different value per iteration.
Full corpus went 803 identical / 10 different -> 812 / 8.

ONE of the remaining eight is a real regression -- the other seven are programs
already wrong without this pass, where it merely rearranges wrong output (two of
them into a crash), and one, 472_generator_method_and_class_factory, this pass
FIXES. The real one, reduced:

    _A = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    ... n_groups picked by an if/elif chain over `rem`, then
    while j >= 0:
        if j > 7 - n_groups: out.append(ord(_A[(chunk >> (j*5)) & 0x1F]))
        else:               out.append(ord("="))

    no passes -> MZXW6===        --passes gvn -> M=======

Only one data group is emitted instead of five, i.e. the loop bound derived from
`n_groups` collapses. `n_groups` reaches that comparison through a `load` from a
stack slot, so every expression over it carries a single SSA name while the
value differs -- the same shape as the fix above, but reached by a path the
cycle test does not cover. Find it before promoting this pass.

Restricted to **pure arithmetic**. ``load``/``gep`` are excluded on purpose:
proving a load still valid across blocks needs alias analysis to rule out an
intervening store or call, and this IR carries boxed heap objects whose aliasing
cannot be reasoned about here. ``cse`` already catches the within-block cases
where invalidation is tractable.
"""

from __future__ import annotations

from .._compiler.ssa.cfg import cycle_membership, dominators
from .._compiler.ssa.ir import IRModule, IRPass, IRValue

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

        # An instruction inside a loop has ONE SSA name but a DIFFERENT value on
        # each iteration. Dominance alone does not make it available: the
        # defining block dominates later blocks in the same loop, but by the
        # time control reaches them again the operands have changed.
        #
        # Reusing it anyway freezes a loop-varying value at its first iteration.
        # Observed as a comprehension over two variables producing
        # [(0,0),(0,0),(1,0),(1,0)] instead of [(0,0),(0,1),(1,0),(1,1)], and as
        # urlencode truncating 'hello%20world' to 'hello%20'.
        #
        # Real SSA does not have this problem -- a loop-varying value arrives
        # through a phi, so its operands differ per iteration and number
        # differently. This IR is memory-SSA until mem2reg runs, so the loop
        # variable is a `load` and every expression over it keeps one name.
        # Hence: an expression defined inside a cycle is never reused at another
        # block inside that same cycle. Outside it, the value is the final
        # iteration's and dominance is sufficient.
        cycles_of = cycle_membership(func)

        children: dict[int, list[int]] = {}
        for node, parent in idom.items():
            if parent != node:
                children.setdefault(parent, []).append(node)

        available: dict[tuple, tuple[IRValue, int]] = {}
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
                    prior_value, prior_block = prior
                    here = cycles_of.get(node)
                    there = cycles_of.get(prior_block)
                    same_cycle = (here is not None and there is not None
                                  and (here & there))
                    if not same_cycle:
                        replacement[instr.result.name] = prior_value
                        changed = True
                        continue       # dominating computation already has it
                    kept.append(instr)
                    continue           # loop-varying: recompute it
                available[key] = (instr.result, node)
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
