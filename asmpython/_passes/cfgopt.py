"""Structural CFG passes beyond ``simplifycfg``.

``jumpthread``   retarget branches that aim at a block which only forwards
``blockmerge``   fuse a block into its sole predecessor
``phisimplify``  collapse a phi whose incoming values are all the same

All three SKIP functions carrying ``try_regions``: that metadata is positional
BLOCK INDICES read by the register allocator (``_in_try_region``), so removing
or reordering a block silently repoints those spans. See ``simplifycfg``.
"""

from __future__ import annotations

from .._compiler.ir import IRModule, IRPass, IRValue


def _succs(block) -> list[str]:
    if not block.instrs:
        return []
    term = block.instrs[-1]
    if term.op == "br":
        return [str(term.operands[0])] if term.operands else []
    if term.op == "br.t":
        return [str(t) for t in term.operands[1:3] if isinstance(t, str)]
    return []


def _preds(func) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {b.label: [] for b in func.blocks}
    for block in func.blocks:
        for s in _succs(block):
            if s in out:
                out[s].append(block.label)
    return out


def _has_phi(block) -> bool:
    return any(i.op == "phi" for i in block.instrs)


class JumpThreadPass(IRPass):
    """Skip over a block whose entire body is an unconditional branch.

    If ``B`` contains only ``br C``, every branch to ``B`` is retargeted to
    ``C`` and ``B`` becomes unreachable (``simplifycfg`` then deletes it).

    Refuses when ``C`` contains phis: a phi there names ``B`` as the incoming
    edge, and rerouting predecessors around ``B`` would need one phi entry per
    new predecessor. Correctness over coverage.
    """

    name = "jumpthread"
    description = "retarget branches through forwarding-only blocks"
    preserves = frozenset({"ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            if getattr(func, "try_regions", None) or not func.blocks:
                continue
            by_label = {b.label: b for b in func.blocks}
            entry = func.blocks[0].label

            forward: dict[str, str] = {}
            for block in func.blocks:
                if block.label == entry or len(block.instrs) != 1:
                    continue
                term = block.instrs[0]
                if term.op != "br" or not term.operands:
                    continue
                target = str(term.operands[0])
                dest = by_label.get(target)
                if dest is None or target == block.label or _has_phi(dest):
                    continue
                forward[block.label] = target

            if not forward:
                continue

            def final(label: str) -> str:
                seen = set()
                while label in forward and label not in seen:
                    seen.add(label)
                    label = forward[label]
                return label

            for block in func.blocks:
                if not block.instrs:
                    continue
                term = block.instrs[-1]
                if term.op == "br" and term.operands:
                    tgt = str(term.operands[0])
                    new = final(tgt)
                    if new != tgt:
                        term.operands = [new]
                        changed = True
                elif term.op == "br.t" and len(term.operands or []) >= 3:
                    ops = list(term.operands)
                    for i in (1, 2):
                        if isinstance(ops[i], str):
                            new = final(ops[i])
                            if new != ops[i]:
                                ops[i] = new
                                changed = True
                    term.operands = ops
        return changed


class BlockMergePass(IRPass):
    """Fuse ``B`` into ``A`` when A's only successor is B and B's only pred is A.

    Removes an unconditional branch and lengthens the straight-line run, which
    gives the block-local passes (``cse``, ``dse``, ``loadelim``) a bigger
    window to work in.

    Skips when B has phis -- with a single predecessor those are degenerate and
    ``phisimplify`` should collapse them first.
    """

    name = "blockmerge"
    description = "fuse a block into its sole predecessor"
    preserves = frozenset({"ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            if getattr(func, "try_regions", None) or not func.blocks:
                continue
            if self._run_func(func):
                changed = True
        return changed

    def _run_func(self, func) -> bool:
        changed = False
        merged_any = True
        while merged_any:
            merged_any = False
            preds = _preds(func)
            by_label = {b.label: b for b in func.blocks}
            entry = func.blocks[0].label

            for block in func.blocks:
                succs = _succs(block)
                if len(succs) != 1:
                    continue
                target = succs[0]
                dest = by_label.get(target)
                if (dest is None or target == entry or target == block.label
                        or len(preds.get(target, [])) != 1 or _has_phi(dest)):
                    continue
                if not block.instrs or block.instrs[-1].op != "br":
                    continue

                # Splice: drop A's terminator, append all of B.
                block.instrs = block.instrs[:-1] + dest.instrs

                # B no longer exists, so every phi in B's successors that names
                # B as an incoming edge must now name A -- control still arrives
                # along that edge, just from the fused block. Leaving the stale
                # label makes the phi describe an edge no predecessor supplies;
                # phi elimination then never emits the copy for it and the value
                # is whatever the register happened to hold (a loop accumulator
                # reading garbage, or an unterminated loop).
                for succ_label in _succs(block):
                    succ = by_label.get(succ_label)
                    if succ is None:
                        continue
                    for instr in succ.instrs:
                        if instr.op != "phi" or not instr.operands:
                            continue
                        instr.operands = [
                            block.label
                            if (i % 2 == 1 and str(op) == target) else op
                            for i, op in enumerate(instr.operands)
                        ]

                func.blocks = [b for b in func.blocks if b.label != target]
                merged_any = changed = True
                break
        return changed


class PhiSimplifyPass(IRPass):
    """Collapse a phi whose incoming values are all identical.

    ``phi [v, A, v, B]`` is just ``v`` regardless of the path taken. Also
    handles the self-referential loop form ``phi [v, A, self, B]``, where the
    only value that can ever arrive is ``v``.
    """

    name = "phisimplify"
    description = "collapse phis whose incoming values are all the same"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            replacement: dict[str, object] = {}
            for block in func.blocks:
                kept = []
                for instr in block.instrs:
                    if instr.op != "phi" or instr.result is None:
                        kept.append(instr)
                        continue
                    ops = instr.operands or []
                    incoming = [ops[i] for i in range(0, len(ops) - 1, 2)]
                    # Ignore edges that just carry the phi's own result back.
                    distinct = []
                    for value in incoming:
                        if (isinstance(value, IRValue)
                                and value.name == instr.result.name):
                            continue
                        if not any(_same(value, seen) for seen in distinct):
                            distinct.append(value)
                    if len(distinct) == 1:
                        replacement[instr.result.name] = distinct[0]
                        changed = True
                        continue
                    kept.append(instr)
                block.instrs = kept
            if replacement:
                _apply(func, replacement)
        return changed


def _same(a, b) -> bool:
    if isinstance(a, IRValue) and isinstance(b, IRValue):
        return a.name == b.name
    if isinstance(a, IRValue) or isinstance(b, IRValue):
        return False
    return type(a) is type(b) and a == b


def _apply(func, replacement: dict) -> None:
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


__all__ = ["JumpThreadPass", "BlockMergePass", "PhiSimplifyPass"]
