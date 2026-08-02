"""The starting set of transforms.

Four passes, chosen because between them they clean up everything a
straightforward frontend emits, and because each demonstrates one shape a pass
can take:

    constfold    peephole, instruction-local
    copyprop     dataflow-free rewriting plus a use-count
    dce          backward liveness over the whole function
    simplifycfg  changes the block graph, so it invalidates `cfg`

None of them knows anything about a source language. `constfold` folds
`i64.add 2, 3` because the IR says ADD on i64 wraps at 64 bits -- not because
Python integers do anything in particular.

A NOTE ON WRAPPING. Folding must produce exactly what the target would. The
interpreter's `_wrap` is the authority for that, so the folder calls it rather
than reimplementing the rule; two implementations of overflow that agree today
will not agree after the first edit to either.
"""
from __future__ import annotations

from ..ir import Module, types as T
from ..ir.cfg import ControlFlowGraph
from ..ir.interpreter import _arith, _wrap
from ..ir.module import Block, Function, Instruction, Register
from ..ir.opcodes import Op
from .manager import Pass, register

_FOLDABLE = frozenset({
    Op.ADD, Op.SUB, Op.MUL, Op.DIV, Op.REM, Op.AND, Op.OR, Op.XOR,
})
_COMPARISONS = frozenset({Op.EQ, Op.NE, Op.LT, Op.LE, Op.GT, Op.GE})


class ConstantFolding(Pass):
    name = "constfold"
    description = "evaluate operations whose operands are known constants"
    requires = frozenset({"verified"})
    provides = frozenset({"constants"})
    invalidates = frozenset()          # instruction-local; the CFG is untouched

    def run(self, module: Module) -> bool:
        changed = False
        for fn in module.defined_functions():
            changed |= self._function(fn)
        return changed

    def _function(self, fn: Function) -> bool:
        changed = False
        for block in fn.blocks:
            # Constants are tracked per BLOCK, not across the function: a
            # register may be reassigned on another path, and a value that is
            # constant here need not be constant where control merges.
            known: dict[Register, int | float] = {}
            for ins in block.instructions:
                if ins.op is Op.CONST and ins.dst is not None:
                    known[ins.dst] = ins.imm
                    continue
                if self._fold(ins, known, fn):
                    known[ins.dst] = ins.imm
                    changed = True
                elif ins.dst is not None:
                    known.pop(ins.dst, None)
        return changed

    def _fold(self, ins: Instruction, known: dict, fn: Function) -> bool:
        if ins.dst is None or len(ins.args) != 2:
            return False
        if ins.op not in _FOLDABLE and ins.op not in _COMPARISONS:
            return False
        if any(a not in known for a in ins.args):
            return False
        x, y = known[ins.args[0]], known[ins.args[1]]

        if ins.op in _COMPARISONS:
            from ..ir.interpreter import _compare
            value = 1 if _compare(ins.op, ins.ty, x, y) else 0
            result_ty = T.I1
        else:
            # Division by zero is undefined, not zero. Folding it would bake a
            # value into the program that the target would have trapped on, so
            # it is left alone and the trap stays where the user can see it.
            if ins.op in (Op.DIV, Op.REM) and y == 0:
                return False
            try:
                value = _arith(ins.op, ins.ty, x, y)
            except Exception:
                return False
            result_ty = ins.ty

        ins.op = Op.CONST
        ins.ty = result_ty
        ins.imm = value
        ins.args = []
        return True


class CopyPropagation(Pass):
    name = "copyprop"
    description = "forward `copy` chains and drop the copies that become dead"
    requires = frozenset({"verified"})
    invalidates = frozenset()

    def run(self, module: Module) -> bool:
        changed = False
        for fn in module.defined_functions():
            changed |= self._function(fn)
        return changed

    def _function(self, fn: Function) -> bool:
        """Forward copies WITHIN a block only.

        Crossing a block boundary would need to prove the source is not
        reassigned on any other path reaching here -- which is dominance plus
        reaching definitions. Within a block the question is trivial, and the
        frontend emits most of its copies locally anyway (a `copy` joining two
        arms is exactly the case that must NOT be forwarded).
        """
        changed = False
        for block in fn.blocks:
            alias: dict[Register, Register] = {}
            for ins in block.instructions:
                if alias and ins.replace_uses(alias):
                    changed = True
                if ins.dst is not None:
                    # Anything aliasing the register just written is stale.
                    for k in [k for k, v in alias.items()
                              if v == ins.dst or k == ins.dst]:
                        del alias[k]
                if (ins.op is Op.COPY and ins.dst is not None
                        and fn.register_type(ins.dst)
                        == fn.register_type(ins.args[0])):
                    alias[ins.dst] = ins.args[0]
        return changed


class DeadCodeElimination(Pass):
    name = "dce"
    description = "remove instructions whose results are never used"
    requires = frozenset({"verified"})
    provides = frozenset({"no-dead-code"})
    invalidates = frozenset()

    def run(self, module: Module) -> bool:
        changed = False
        for fn in module.defined_functions():
            changed |= self._function(fn)
        return changed

    def _function(self, fn: Function) -> bool:
        """Iterate: removing one instruction can make its operands dead too."""
        changed = False
        while True:
            used: set[Register] = set()
            for _, ins in fn.instructions():
                used.update(ins.args)

            removed = False
            for block in fn.blocks:
                keep = []
                for ins in block.instructions:
                    dead = (ins.dst is not None
                            and ins.dst not in used
                            and not ins.has_side_effects
                            and ins.dst not in fn.params)
                    if dead:
                        removed = True
                    else:
                        keep.append(ins)
                block.instructions = keep
            if not removed:
                return changed
            changed = True


class SimplifyCFG(Pass):
    name = "simplifycfg"
    description = "drop unreachable blocks and fold constant branches"
    requires = frozenset({"verified"})
    invalidates = frozenset({"cfg"})

    def run(self, module: Module) -> bool:
        changed = False
        for fn in module.defined_functions():
            changed |= self._fold_branches(fn)
            changed |= self._drop_unreachable(fn)
        return changed

    def _fold_branches(self, fn: Function) -> bool:
        """`branch` on a constant becomes `jump`."""
        changed = False
        for block in fn.blocks:
            term = block.terminator
            if term is None or term.op is not Op.BRANCH:
                continue
            const = self._defining_const(block, term.args[0])
            if const is None:
                continue
            taken = term.labels[0] if const else term.labels[1]
            term.op = Op.JUMP
            term.ty = T.VOID
            term.args = []
            term.labels = [taken]
            changed = True
        return changed

    @staticmethod
    def _defining_const(block: Block, reg: Register) -> int | None:
        """The constant `reg` holds at the end of `block`, if it is one.

        Scans backwards for the LAST assignment, because a register may be
        written several times in one block and only the most recent one is the
        value the terminator sees.
        """
        for ins in reversed(block.instructions):
            if ins.dst == reg:
                return int(ins.imm) if ins.op is Op.CONST else None
        return None

    def _drop_unreachable(self, fn: Function) -> bool:
        cfg = ControlFlowGraph.build(fn)
        dead = set(cfg.unreachable)
        if not dead:
            return False
        # The entry block is never dropped even if something has made it look
        # unreachable -- removing it would leave a function with no entry.
        dead.discard(0)
        if not dead:
            return False
        fn.blocks = [b for i, b in enumerate(fn.blocks) if i not in dead]
        return True


for _p in (ConstantFolding(), CopyPropagation(), DeadCodeElimination(),
           SimplifyCFG()):
    register(_p)
