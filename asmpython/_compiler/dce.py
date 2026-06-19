"""dce.py — Dead-code elimination on the SSA IR.

Three passes that compose and iterate to fixpoint:

  1. _elim_unreachable_blocks — remove blocks unreachable from the entry;
                                 update predecessor lists and PHI incoming.
  2. _fold_const_branches     — CONDBR(constant) → unconditional BR; this
                                 creates new unreachable blocks for pass 1.
  3. _elim_dead_instrs        — mark-and-sweep: seed from side-effecting
                                 instructions (STORE, CALL, BR, CONDBR, RET,
                                 RAW_ASM), propagate liveness backward through
                                 def-use chains, drop everything else.

`run_dce(func)` runs all three in a loop until no pass reports a change.
PHI nodes are dead if their result is never consumed by any live instruction;
the sweep handles this without special cases since PHIs are not in
`_SIDE_EFFECT_OPS` and only become live when a live instruction uses their
result.
"""

from __future__ import annotations

from .ir import Block, Function, Instr, Op


# Instructions kept regardless of whether their result is used.
# LOAD is intentionally absent: a LOAD whose result is never read is dead
# (the only observable effect would be a fault on a bad pointer, which is
# already undefined behaviour in asmpython's memory model).
_SIDE_EFFECT_OPS: frozenset[Op] = frozenset({
    Op.STORE,
    Op.CALL,
    Op.RAW_ASM,
    Op.BR,
    Op.CONDBR,
    Op.RET,
})


# ---------------------------------------------------------------------------
# Pass 1: unreachable block elimination
# ---------------------------------------------------------------------------

def _reachable_ids(func: Function) -> set[int]:
    """Return id(Block) for every block reachable from the entry via CFG."""
    visited: set[int] = set()
    worklist: list[Block] = [func.entry()]
    while worklist:
        blk = worklist.pop()
        bid = id(blk)
        if bid in visited:
            continue
        visited.add(bid)
        term = blk.terminator()
        if term is None:
            continue
        if term.op == Op.BR and term.target is not None:
            worklist.append(term.target)
        elif term.op == Op.CONDBR:
            if term.true_target is not None:
                worklist.append(term.true_target)
            if term.false_target is not None:
                worklist.append(term.false_target)
    return visited


def _elim_unreachable_blocks(func: Function) -> bool:
    """Remove blocks unreachable from the entry block. Returns True if any removed."""
    reachable = _reachable_ids(func)
    before = len(func.blocks)
    func.blocks = [b for b in func.blocks if id(b) in reachable]
    if len(func.blocks) == before:
        return False

    # Fix predecessor lists and PHI incoming values in all surviving blocks.
    for blk in func.blocks:
        old_preds = blk.preds
        removed_indices = {
            i for i, p in enumerate(old_preds) if id(p) not in reachable
        }
        if not removed_indices:
            continue
        blk.preds = [p for p in old_preds if id(p) in reachable]
        for instr in blk.instrs:
            if instr.op == Op.PHI:
                instr.incoming = [
                    v for i, v in enumerate(instr.incoming)
                    if i not in removed_indices
                ]
    return True


# ---------------------------------------------------------------------------
# Pass 2: constant-branch folding
# ---------------------------------------------------------------------------

def _fold_const_branches(func: Function) -> bool:
    """Replace CONDBR(constant) with an unconditional BR to the taken target.

    Updates the not-taken block's predecessor list and its PHI incoming
    values to stay consistent. Returns True if any branch was folded.
    """
    changed = False
    for blk in func.blocks:
        term = blk.terminator()
        if term is None or term.op != Op.CONDBR:
            continue
        cond = term.args[0]
        if cond.def_ is None or cond.def_.op != Op.CONST:
            continue
        const_val = cond.def_.const_value
        if not isinstance(const_val, (int, float)):
            continue

        taken = term.true_target if const_val else term.false_target
        not_taken = term.false_target if const_val else term.true_target
        if taken is None:
            continue

        # Rewrite the block's terminator to an unconditional branch.
        blk.instrs[-1] = Instr(op=Op.BR, target=taken)

        # Remove blk from not_taken's predecessor list and fix its PHI nodes.
        # Skip if both arms lead to the same block: blk stays a predecessor
        # of that block, just via an unconditional branch now.
        if not_taken is not None and not_taken is not taken:
            try:
                idx = next(i for i, p in enumerate(not_taken.preds) if p is blk)
            except StopIteration:
                pass
            else:
                not_taken.preds.pop(idx)
                for instr in not_taken.instrs:
                    if instr.op == Op.PHI and idx < len(instr.incoming):
                        instr.incoming.pop(idx)

        changed = True
    return changed


# ---------------------------------------------------------------------------
# Pass 3: dead instruction elimination
# ---------------------------------------------------------------------------

def _elim_dead_instrs(func: Function) -> bool:
    """Mark-and-sweep: seed live set from side-effecting instructions,
    propagate backwards through def-use edges, drop everything unmarked.

    Returns True if any instructions were removed.
    """
    live: set[int] = set()  # id(Instr) for each live instruction
    worklist: list[Instr] = []

    # Seed: every instruction with observable side effects is always live.
    for blk in func.blocks:
        for instr in blk.instrs:
            if instr.op in _SIDE_EFFECT_OPS:
                iid = id(instr)
                if iid not in live:
                    live.add(iid)
                    worklist.append(instr)

    # Propagate: for each live instruction, mark the instructions that
    # produce its arguments (and PHI incoming values) as live.
    while worklist:
        instr = worklist.pop()
        for v in instr.args:
            if v.def_ is not None:
                iid = id(v.def_)
                if iid not in live:
                    live.add(iid)
                    worklist.append(v.def_)
        # PHI nodes don't use args[] — they use incoming[].
        if instr.op == Op.PHI:
            for v in instr.incoming:
                if v.def_ is not None:
                    iid = id(v.def_)
                    if iid not in live:
                        live.add(iid)
                        worklist.append(v.def_)

    changed = False
    for blk in func.blocks:
        before = len(blk.instrs)
        blk.instrs = [i for i in blk.instrs if id(i) in live]
        if len(blk.instrs) < before:
            changed = True
    return changed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_dce(func: Function) -> None:
    """Run all DCE passes on *func* in-place, iterating to fixpoint.

    The order — unreachable-block removal, branch folding, dead-instruction
    sweep — is chosen so each pass feeds the next: folding a constant branch
    makes the not-taken arm unreachable, and removing that arm may leave its
    argument-producing instructions without any live consumers.
    """
    while True:
        c1 = _elim_unreachable_blocks(func)
        c2 = _fold_const_branches(func)
        c3 = _elim_dead_instrs(func)
        if not (c1 or c2 or c3):
            break
