"""Whole-module and mark-sweep elimination passes.

``globaldce``   drop globals nothing references
``adce``        aggressive DCE: mark from side-effecting roots, sweep the rest
``sink``        move a pure instruction down to the block that uses it

``globaldce`` reads ``IRModule.data`` and respects ``IRModule.exports``, since an
exported symbol is reachable from outside the module even when nothing inside
references it.

NOT IMPLEMENTED: constant merging (LLVM's ConstantMerge)
-------------------------------------------------------
Deduplicating globals with identical contents is unsound on this IR and was
removed after it silently corrupted output. ``IRGlobal`` carries no immutability
flag, so value equality cannot distinguish a read-only literal from mutable
storage that merely starts at the same value. Merging two *mutable* globals
aliases them, and a write through one corrupts the other -- observed as wrong
list/float/iterator results, not a crash.

LLVM only merges globals explicitly marked constant. Implementing this properly
requires an ``is_constant`` flag on ``IRGlobal`` that the frontend sets; until
that exists, do not re-add this pass.
"""

from __future__ import annotations

from .._compiler.ir import IRInstr, IRModule, IRPass, IRValue

#: Ops with no side effects (superset used for marking, mirrors dce.PURE_OPS).
_PURE = frozenset({
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


def _referenced_symbols(module: IRModule) -> set[str]:
    """Every string operand anywhere -- global names and call targets alike."""
    names: set[str] = set()
    for func in module.funcs:
        for block in func.blocks:
            for instr in block.instrs:
                for operand in instr.operands or []:
                    if isinstance(operand, str):
                        names.add(operand)
    return names


class GlobalDCEPass(IRPass):
    """Remove globals no instruction names and nothing exports."""

    name = "globaldce"
    description = "drop globals nothing references"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        data = getattr(module, "data", None)
        if not data:
            return False
        live = _referenced_symbols(module) | set(getattr(module, "exports", ()) or ())
        kept = [g for g in data if g.name in live]
        if len(kept) == len(data):
            return False
        module.data = kept
        return True


class ADCEPass(IRPass):
    """Aggressive DCE: keep only what a side-effecting instruction needs.

    Ordinary ``dce`` deletes an instruction whose result is unused, then repeats.
    This instead marks from the roots -- terminators, stores, calls -- and sweeps
    everything unmarked in one pass, which also removes whole chains of pure
    work that only ever fed each other.
    """

    name = "adce"
    description = "mark-sweep dead code elimination from side-effecting roots"
    preserves = frozenset({"cfg", "ssa"})

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            defs: dict[str, IRInstr] = {}
            for block in func.blocks:
                for instr in block.instrs:
                    if instr.result is not None:
                        defs[instr.result.name] = instr

            live: set[int] = set()
            worklist: list[IRInstr] = []
            for block in func.blocks:
                for instr in block.instrs:
                    if instr.op not in _PURE:      # terminator, store, call, ...
                        live.add(id(instr))
                        worklist.append(instr)

            while worklist:
                instr = worklist.pop()
                for operand in instr.operands or []:
                    if not isinstance(operand, IRValue):
                        continue
                    producer = defs.get(operand.name)
                    if producer is not None and id(producer) not in live:
                        live.add(id(producer))
                        worklist.append(producer)

            for block in func.blocks:
                kept = [i for i in block.instrs if id(i) in live]
                if len(kept) != len(block.instrs):
                    block.instrs = kept
                    changed = True
        return changed


class SinkPass(IRPass):
    """Move a pure instruction into the single block that uses its result.

    Shortens live ranges, which directly eases the register pressure this
    backend is sensitive to. Only sinks when every use is in one other block,
    that block is not the definition's own, and the instruction reads nothing
    defined in between -- so the move can't cross a redefinition.

    ``load`` is excluded: sinking it past a store or call would change what it
    reads.
    """

    name = "sink"
    description = "sink pure instructions into their single use block"
    preserves = frozenset({"cfg", "ssa"})

    _SINKABLE = _PURE - {"load", "alloca", "phi", "mov"}

    def run(self, module: IRModule) -> bool:
        changed = False
        for func in module.funcs:
            if len(func.blocks) < 2:
                continue
            if self._run_func(func):
                changed = True
        return changed

    def _run_func(self, func) -> bool:
        from .._compiler.cfg import loop_membership

        # Never sink INTO a loop the instruction is not already in.
        #
        # Two reasons, and the first alone settles it: moving a computation
        # from outside a loop to inside makes it run once per iteration
        # instead of once, which is precisely what LICM just finished
        # undoing. `licm,sink` would hand work back and forth.
        #
        # It is also unsound in this backend today. The register allocator
        # decides whether a value must stay live across a back edge by asking
        # where it is DEFINED relative to the loop (see regalloc._last_uses),
        # so relocating a definition across a loop boundary changes that
        # answer underneath it. Observed as a wrong result -- not a crash --
        # from `--passes licm,sink`, with the IR verifying clean either way.
        membership = loop_membership(func)
        index_of = {b.label: i for i, b in enumerate(func.blocks)}

        # Which blocks use each value; phi operands pin a value to the
        # predecessor edge, so treat any phi use as "don't move".
        use_blocks: dict[str, set[str]] = {}
        phi_used: set[str] = set()
        for block in func.blocks:
            for instr in block.instrs:
                for operand in instr.operands or []:
                    if isinstance(operand, IRValue):
                        use_blocks.setdefault(operand.name, set()).add(block.label)
                        if instr.op == "phi":
                            phi_used.add(operand.name)

        changed = False
        for block in func.blocks:
            movable: list[IRInstr] = []
            defined_here = {
                i.result.name for i in block.instrs if i.result is not None
            }
            for instr in block.instrs:
                if (instr.op not in self._SINKABLE or instr.result is None
                        or instr.result.name in phi_used):
                    continue
                targets = use_blocks.get(instr.result.name, set())
                if len(targets) != 1:
                    continue
                target = next(iter(targets))
                if target == block.label:
                    continue
                src_loops = membership.get(index_of[block.label], frozenset())
                dst_loops = membership.get(index_of[target], frozenset())
                if not dst_loops <= src_loops:
                    continue
                # Operands must not be defined in this block, otherwise moving
                # the instruction away would leave them behind out of order.
                if any(isinstance(o, IRValue) and o.name in defined_here
                       for o in (instr.operands or [])):
                    continue
                movable.append((instr, target))

            if not movable:
                continue
            moving = {id(i) for i, _ in movable}
            block.instrs = [i for i in block.instrs if id(i) not in moving]
            by_label = {b.label: b for b in func.blocks}
            for instr, target in movable:
                dest = by_label[target]
                at = 0
                while at < len(dest.instrs) and dest.instrs[at].op == "phi":
                    at += 1               # never precede a phi
                dest.instrs.insert(at, instr)
            changed = True
        return changed


__all__ = ["GlobalDCEPass", "ADCEPass", "SinkPass"]
