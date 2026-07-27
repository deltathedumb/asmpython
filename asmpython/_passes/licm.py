"""Loop-invariant code motion, and deletion of loops that compute nothing.

``licm``         hoist loop-invariant computations out of the loop
``loopdelete``   remove a loop whose body has no effect and no live result

Both are built on the natural-loop analysis in ``_compiler/cfg.py``; neither was
expressible while loops were approximated by block-index ranges.

Preheaders
----------
LICM needs somewhere outside the loop to hoist to. It hoists only when the
header has exactly one predecessor from outside the loop and that block's only
successor is the header -- i.e. a preheader already exists. Creating one when it
does not is now unblocked (``try_regions`` is label-based, so inserting a block
no longer shifts it onto the wrong code) and is the next step for this pass.

Safety
------
Only instructions that are pure, and whose operands are available AT the
preheader -- defined outside the loop AND in a block dominating it -- are
hoisted. "Outside the loop" alone is not enough: a value from a sibling branch
is outside the loop but does not reach the preheader. Nothing that can trap or observe memory moves: division is
excluded (a divide-by-zero must stay under its original guard), and so are
``load``/``call``/``store``, which a later iteration could otherwise observe
differently. Hoisting a pure computation is safe even if the loop runs zero
times, because it has no effect beyond producing its value.
"""

from __future__ import annotations

from .._compiler.cfg import (
    dominates, dominators, natural_loops, predecessor_indices,
    successor_indices,
)
from .._compiler.ir import IRModule, IRPass, IRValue

#: Pure and trap-free: safe to execute unconditionally.
_HOISTABLE = frozenset({
    "iadd", "isub", "imul", "ineg", "inot",
    "iand", "ior", "ixor", "shl", "shr", "sar",
    "icmp.eq", "icmp.ne", "icmp.lt", "icmp.le", "icmp.gt", "icmp.ge",
    "icmp.ult", "icmp.ule", "icmp.ugt", "icmp.uge",
    "fadd", "fsub", "fmul", "fneg",
    "fcmp.eq", "fcmp.ne", "fcmp.lt", "fcmp.le", "fcmp.gt", "fcmp.ge",
    "sext", "zext", "trunc", "sitofp", "fptosi", "fpext", "fptrunc",
    "bitcast_i2f", "bitcast_f2i",
    "const", "global_addr", "gep",
})
# NOTE: idiv/irem/udiv/urem are deliberately absent -- they can trap.


def _find_preheader(header: int, body, preds, succs) -> "int | None":
    outside = [p for p in preds[header] if p not in body]
    if len(outside) != 1:
        return None
    candidate = outside[0]
    if succs[candidate] != [header]:
        return None            # not a dedicated preheader
    return candidate


class LICMPass(IRPass):
    name = "licm"
    description = "hoist loop-invariant computations into the preheader"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            if self._run_func(func):
                changed = True
        return changed

    def _run_func(self, func) -> bool:
        loops = natural_loops(func)
        if not loops:
            return False
        succs = successor_indices(func)
        preds = predecessor_indices(func, succs)

        changed = False
        # Innermost first: hoisting out of an inner loop can expose values that
        # are then invariant for the enclosing one.
        for header, body in sorted(loops, key=lambda hb: len(hb[1])):
            preheader = _find_preheader(header, body, preds, succs)
            if preheader is None:
                continue

            defined_in_loop = {
                instr.result.name
                for bi in body
                for instr in func.blocks[bi].instrs
                if instr.result is not None
            }

            # "Not defined in the loop" is NOT sufficient to hoist. The operand
            # must also be available AT the preheader -- its defining block must
            # dominate the preheader -- or a value computed in a sibling branch,
            # which merely happens to sit outside the loop, is read above its own
            # definition. At runtime that is a wild read of whatever the register
            # last held; the verifier reports it as a use its definition does not
            # dominate.
            idom = dominators(func)
            params = {p.name for p in func.params}
            def_block: dict[str, int] = {}
            for dbi, dblock in enumerate(func.blocks):
                for dinstr in dblock.instrs:
                    if dinstr.result is not None:
                        def_block.setdefault(dinstr.result.name, dbi)

            def _available_at_preheader(name: str) -> bool:
                if name in params:
                    return True                  # live for the whole function
                where = def_block.get(name)
                if where is None:
                    return False                 # unknown provenance: refuse
                return dominates(idom, where, preheader)

            # Repeat: each hoist can make another instruction invariant.
            moved_any = True
            while moved_any:
                moved_any = False
                for bi in sorted(body):
                    block = func.blocks[bi]
                    hoist = []
                    for instr in block.instrs:
                        if instr.op not in _HOISTABLE or instr.result is None:
                            continue
                        if any(isinstance(op, IRValue)
                               and (op.name in defined_in_loop
                                    or not _available_at_preheader(op.name))
                               for op in (instr.operands or [])):
                            continue
                        hoist.append(instr)
                    if not hoist:
                        continue
                    moving = {id(i) for i in hoist}
                    block.instrs = [i for i in block.instrs if id(i) not in moving]
                    target = func.blocks[preheader]
                    at = len(target.instrs)
                    if at and target.instrs[-1].op in ("br", "br.t", "ret"):
                        at -= 1                     # stay before the terminator
                    for offset, instr in enumerate(hoist):
                        target.instrs.insert(at + offset, instr)
                        defined_in_loop.discard(instr.result.name)
                        def_block[instr.result.name] = preheader
                    moved_any = changed = True
        return changed


class LoopDeletePass(IRPass):
    """Delete a loop that cannot affect the program.

    A loop qualifies when every instruction in its body is pure and no value it
    defines is read outside the loop. The header's exit edge replaces the loop
    entirely.

    Deliberately conservative about termination: only loops whose body is a
    single block reachable from the header are considered, so the loop being
    removed is one whose trip count is governed by that block alone.
    """

    name = "loopdelete"
    description = "remove loops with no side effects and no live result"
    preserves = frozenset({"ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            if self._run_func(func):
                changed = True
        return changed

    def _run_func(self, func) -> bool:
        loops = natural_loops(func)
        if not loops:
            return False
        succs = successor_indices(func)
        changed = False

        for header, body in loops:
            if len(body) != 2:
                continue                       # header + one body block only
            used_outside: set[str] = set()
            for bi, block in enumerate(func.blocks):
                if bi in body:
                    continue
                for instr in block.instrs:
                    for op in instr.operands or []:
                        if isinstance(op, IRValue):
                            used_outside.add(op.name)

            pure = True
            defines: set[str] = set()
            for bi in body:
                for instr in func.blocks[bi].instrs:
                    if instr.op in ("br", "br.t", "ret"):
                        continue
                    if instr.op not in _HOISTABLE and instr.op != "phi":
                        pure = False
                        break
                    if instr.result is not None:
                        defines.add(instr.result.name)
                if not pure:
                    break
            if not pure or (defines & used_outside):
                continue

            exits = [s for s in succs[header] if s not in body]
            if len(exits) != 1:
                continue
            # Replace the header's terminator with a jump straight to the exit.
            exit_label = func.blocks[exits[0]].label
            from .._compiler.ir import IRInstr

            func.blocks[header].instrs = [IRInstr("br", None, [exit_label])]
            changed = True
        return changed


__all__ = ["LICMPass", "LoopDeletePass"]
