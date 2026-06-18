"""Ergonomic construction helpers for ir.py's data model.

`ir.py` is deliberately a bare data model (see its module docstring) — this
module is the convenience layer that ssa_build.py (and tests, and anything
else assembling IR by hand) actually uses, so call sites read as
`b.add(x, y)` instead of manually building `Instr(op=Op.ADD, ...)` and
appending it to a block's instruction list.
"""

from __future__ import annotations

from . import ir
from .ir import Block, Function, Instr, Kind, Op, Predicate, Value


class BlockBuilder:
    """Wraps one `Block` plus the owning `Function` (for fresh value IDs),
    exposing one method per IR op. Every method appends the instruction
    to the wrapped block and returns the result `Value` (or None for
    instructions with no result)."""

    def __init__(self, func: Function, block: Block) -> None:
        self.func = func
        self.block = block

    def _emit(self, op: Op, args: list[Value], kind: Kind, **kwargs) -> Value | None:
        result = self.func.new_value(kind) if kind != Kind.NONE else None
        self.block.append(Instr(op=op, args=args, result=result, **kwargs))
        return result

    # ---- integer arithmetic ----
    def add(self, a: Value, b: Value) -> Value:
        return self._emit(Op.ADD, [a, b], Kind.INT)

    def sub(self, a: Value, b: Value) -> Value:
        return self._emit(Op.SUB, [a, b], Kind.INT)

    def mul(self, a: Value, b: Value) -> Value:
        return self._emit(Op.MUL, [a, b], Kind.INT)

    def sdiv(self, a: Value, b: Value) -> Value:
        return self._emit(Op.SDIV, [a, b], Kind.INT)

    def srem(self, a: Value, b: Value) -> Value:
        return self._emit(Op.SREM, [a, b], Kind.INT)

    def and_(self, a: Value, b: Value) -> Value:
        return self._emit(Op.AND, [a, b], Kind.INT)

    def or_(self, a: Value, b: Value) -> Value:
        return self._emit(Op.OR, [a, b], Kind.INT)

    def xor(self, a: Value, b: Value) -> Value:
        return self._emit(Op.XOR, [a, b], Kind.INT)

    def shl(self, a: Value, b: Value) -> Value:
        return self._emit(Op.SHL, [a, b], Kind.INT)

    def shr(self, a: Value, b: Value) -> Value:
        return self._emit(Op.SHR, [a, b], Kind.INT)

    def sar(self, a: Value, b: Value) -> Value:
        return self._emit(Op.SAR, [a, b], Kind.INT)

    def neg(self, a: Value) -> Value:
        return self._emit(Op.NEG, [a], Kind.INT)

    def not_(self, a: Value) -> Value:
        return self._emit(Op.NOT, [a], Kind.INT)

    # ---- float arithmetic ----
    def fadd(self, a: Value, b: Value) -> Value:
        return self._emit(Op.FADD, [a, b], Kind.FLOAT)

    def fsub(self, a: Value, b: Value) -> Value:
        return self._emit(Op.FSUB, [a, b], Kind.FLOAT)

    def fmul(self, a: Value, b: Value) -> Value:
        return self._emit(Op.FMUL, [a, b], Kind.FLOAT)

    def fdiv(self, a: Value, b: Value) -> Value:
        return self._emit(Op.FDIV, [a, b], Kind.FLOAT)

    def fneg(self, a: Value) -> Value:
        return self._emit(Op.FNEG, [a], Kind.FLOAT)

    # ---- comparison ----
    def icmp(self, pred: Predicate, a: Value, b: Value) -> Value:
        return self._emit(Op.ICMP, [a, b], Kind.INT, predicate=pred)

    def fcmp(self, pred: Predicate, a: Value, b: Value) -> Value:
        return self._emit(Op.FCMP, [a, b], Kind.INT, predicate=pred)

    # ---- conversion ----
    def sitofp(self, a: Value) -> Value:
        return self._emit(Op.SITOFP, [a], Kind.FLOAT)

    def fptosi(self, a: Value) -> Value:
        return self._emit(Op.FPTOSI, [a], Kind.INT)

    # ---- memory ----
    def load(self, kind: Kind, ptr: Value, offset: int = 0) -> Value:
        return self._emit(Op.LOAD, [ptr], kind, offset=offset)

    def store(self, ptr: Value, value: Value, offset: int = 0) -> None:
        self._emit(Op.STORE, [ptr, value], Kind.NONE, offset=offset)

    # ---- control flow ----
    def br(self, target: Block) -> None:
        self._emit(Op.BR, [], Kind.NONE, target=target)

    def condbr(self, cond: Value, true_target: Block, false_target: Block) -> None:
        self._emit(
            Op.CONDBR, [cond], Kind.NONE,
            true_target=true_target, false_target=false_target,
        )

    def ret(self, value: Value | None = None) -> None:
        self._emit(Op.RET, [value] if value is not None else [], Kind.NONE)

    def phi(self, kind: Kind) -> Value:
        """Create an (initially empty) phi; fill in `incoming` to match
        the block's `preds` order via `add_incoming` once predecessors
        are known (ssa_build.py typically back-patches phis once a
        block's predecessor list is finalized, since blocks are often
        wired up out of definition order)."""
        result = self.func.new_value(kind)
        self.block.instrs.insert(0, Instr(op=Op.PHI, result=result))
        return result

    def add_incoming(self, phi_value: Value, value: Value) -> None:
        for instr in self.block.instrs:
            if instr.op == Op.PHI and instr.result is phi_value:
                instr.incoming.append(value)
                return
        raise ValueError("add_incoming: phi_value is not a PHI in this block")

    # ---- calls ----
    def call(
        self, kind: Kind, callee: str | Value, args: list[Value], *, indirect: bool = False
    ) -> Value | None:
        if indirect:
            assert isinstance(callee, Value)
            call_args = [callee] + args
            return self._emit(Op.CALL, call_args, kind, is_indirect=True)
        assert isinstance(callee, str)
        return self._emit(Op.CALL, args, kind, callee=callee, is_indirect=False)

    # ---- escape hatch ----
    def raw_asm(self, kind: Kind, args: list[Value], target_text: dict[str, str]) -> Value | None:
        return self._emit(Op.RAW_ASM, args, kind, target_text=dict(target_text))


def new_function(name: str) -> tuple[Function, BlockBuilder]:
    """Convenience for the common case: a fresh Function with one entry
    block, ready to build into. Returns (function, builder-for-entry)."""
    func = Function(name=name)
    entry = Block(name="entry")
    func.blocks.append(entry)
    return func, BlockBuilder(func, entry)


def new_block(func: Function, name: str) -> tuple[Block, BlockBuilder]:
    """Append a fresh block to `func` and return (block, builder)."""
    block = Block(name=name)
    func.blocks.append(block)
    return block, BlockBuilder(func, block)
