"""Building IR by hand or from a frontend.

A thin layer over the dataclasses in `module.py`. Nothing here is privileged,
but it removes the three mistakes that dominate hand-built IR: forgetting to
declare a register's type, appending after a terminator, and losing the source
span on a synthesised instruction.

SPANS ARE STICKY. `builder.span = node.span` sets the span applied to every
instruction emitted afterwards, so a frontend sets it once per AST node instead
of threading it through forty call sites -- and forgetting produces
instructions with NO_SPAN rather than a wrong one, which is the failure mode
you want.
"""
from __future__ import annotations

from ..diagnostics import NO_SPAN, Span
from . import types as T
from .module import Block, Function, Instruction, Register
from .opcodes import Op

class Builder:
    """Convenience layer for emitting a function. Frontends use this.

    Nothing here is privileged -- it only constructs the dataclasses above --
    but it removes the two mistakes that dominate hand-built IR: forgetting to
    declare a register's type, and appending to a block that is already
    terminated.
    """

    def __init__(self, func: Function) -> None:
        self.func = func
        self.span: Span = NO_SPAN
        self._cur: Block | None = func.blocks[0] if func.blocks else None
        self._next_label = 0

    # ── registers and blocks ────────────────────────────────────────────────
    def reg(self, ty: T.Type) -> int:
        """Allocate a fresh virtual register of `ty`.

        Delegates to the function so there is exactly one allocator; see
        `Function.new_register`.
        """
        return self.func.new_register(ty)

    def new_block(self, hint: str = "b") -> Block:
        """Create a block with a unique label. It is NOT made current."""
        label = f"{hint}{self._next_label}"
        self._next_label += 1
        while self.func.block(label) is not None:
            label = f"{hint}{self._next_label}"
            self._next_label += 1
        b = Block(label)
        self.func.blocks.append(b)
        return b

    def switch_to(self, block: Block) -> None:
        self._cur = block

    @property
    def current(self) -> Block:
        if self._cur is None:
            raise RuntimeError(
                f"{self.func.name}: no current block; call new_block/switch_to"
            )
        return self._cur

    # ── emission ────────────────────────────────────────────────────────────
    def emit(self, instr: Instr) -> int | None:
        """Append `instr`, returning the register it defines (or None).

        Refuses to append after a terminator. That is not pedantry: silently
        appending unreachable code produces a block whose terminator is in the
        middle, which every consumer then mis-reads -- and the resulting bug
        appears far from the frontend that caused it.
        """
        blk = self.current
        if blk.terminator is not None:
            raise RuntimeError(
                f"{self.func.name}/{blk.label}: block already ends in "
                f"{blk.terminator.op.value!r}; start a new block"
            )
        if instr.span is NO_SPAN:
            instr.span = self.span
        blk.instructions.append(instr)
        return instr.dst

    def _binop(self, op: Op, ty: T.Type, a: int, b: int) -> int:
        d = self.reg(ty)
        self.emit(Instruction(op, ty, dst=d, args=[a, b]))
        return d

    def const(self, ty: T.Type, value: int | float) -> int:
        d = self.reg(ty)
        self.emit(Instruction(Op.CONST, ty, dst=d, imm=value))
        return d

    def copy(self, dst: int, src: int) -> int:
        """Assign an EXISTING register from another. The join mechanism."""
        self.emit(Instruction(Op.COPY, self.func.register_type(dst), dst=dst, args=[src]))
        return dst

    def add(self, ty, a, b): return self._binop(Op.ADD, ty, a, b)
    def sub(self, ty, a, b): return self._binop(Op.SUB, ty, a, b)
    def mul(self, ty, a, b): return self._binop(Op.MUL, ty, a, b)
    def div(self, ty, a, b): return self._binop(Op.DIV, ty, a, b)
    def rem(self, ty, a, b): return self._binop(Op.REM, ty, a, b)

    def cmp(self, op: Op, ty: T.Type, a: int, b: int) -> int:
        """A comparison. `ty` is the OPERAND type; the result is i1."""
        d = self.reg(T.I1)
        self.emit(Instruction(op, ty, dst=d, args=[a, b]))
        return d

    def load(self, ty: T.Type, addr: int) -> int:
        d = self.reg(ty)
        self.emit(Instruction(Op.LOAD, ty, dst=d, args=[addr]))
        return d

    def store(self, ty: T.Type, value: int, addr: int) -> None:
        self.emit(Instruction(Op.STORE, ty, args=[value, addr]))

    def alloca(self, nbytes: int) -> int:
        d = self.reg(T.PTR)
        self.emit(Instruction(Op.ALLOCA, T.PTR, dst=d, imm=nbytes))
        return d

    def offset(self, base: int, off: int) -> int:
        d = self.reg(T.PTR)
        self.emit(Instruction(Op.OFFSET, T.PTR, dst=d, args=[base, off]))
        return d

    def call(self, ret: T.Type, sym: str, args: list[int]) -> int | None:
        d = None if ret.is_void else self.reg(ret)
        self.emit(Instruction(Op.CALL, ret, dst=d, args=list(args), sym=sym))
        return d

    # ── terminators ─────────────────────────────────────────────────────────
    def jump(self, target: Block | str) -> None:
        lbl = target if isinstance(target, str) else target.label
        self.emit(Instruction(Op.JUMP, T.VOID, labels=[lbl]))

    def branch(self, cond: int, then: Block | str, els: Block | str) -> None:
        t = then if isinstance(then, str) else then.label
        e = els if isinstance(els, str) else els.label
        self.emit(Instruction(Op.BRANCH, T.I1, args=[cond], labels=[t, e]))

    def ret(self, value: int | None = None) -> None:
        ty = self.func.ret
        self.emit(Instruction(Op.RET, ty, args=[] if value is None else [value]))
