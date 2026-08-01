"""The IR data structures: Instr, Block, Func, Module, and a Builder.

THE ONE RULE THAT MAKES BACKENDS EASY
-------------------------------------
Every value operand is a virtual register id -- an `int`. Never a literal,
never a nested expression, never a label masquerading as a value. A backend
looking at `instr.args` knows without checking that each entry names a register
whose type it can look up in `func.regs`.

Literals reach the IR only through `Op.CONST`, which puts them in `instr.imm`.
That costs an instruction per constant and buys the invariant that operand
handling is one code path instead of three. A backend that wants to fold
constants can, but nothing forces it to before it works at all.

REGISTERS ARE MUTABLE
---------------------
A register may be assigned any number of times. There are no phi nodes: where
SSA would need one, a frontend assigns the same register on both incoming
paths. `func.regs` maps every register to its type, declared once -- a register
does not change type between assignments, and the verifier enforces it.

STRUCTURE
---------
    Module
      globals : name -> Global (bytes or a zero-filled size)
      funcs   : name -> Func
    Func
      params  : registers pre-loaded with the arguments on entry
      regs    : every register's type, including params and locals
      blocks  : first is the entry; each ends in exactly one terminator
    Block
      label   : unique within the function
      instrs  : the last, and only the last, is a terminator
"""
from __future__ import annotations

from dataclasses import dataclass, field

from . import ops, types as T
from .ops import Op


@dataclass(slots=True)
class Instr:
    """One instruction.

    `ty` is the width the opcode operates at -- the result type for most
    opcodes, and for a comparison the type of its OPERANDS (the result of a
    comparison is always i1).
    """

    op: Op
    ty: T.Type
    #: Register this defines, or None for an instruction that defines nothing.
    dst: int | None = None
    #: Register ids of the value operands.
    args: list[int] = field(default_factory=list)
    #: Literal payload: the value for CONST, the byte count for ALLOCA.
    imm: int | float | None = None
    #: Symbol name for CALL / GLOBAL_ADDR / FUNC_ADDR.
    sym: str | None = None
    #: Branch destinations, by block label.
    labels: list[str] = field(default_factory=list)
    #: SWITCH only: (value, label) pairs. `labels[0]` is the default.
    cases: list[tuple[int, str]] = field(default_factory=list)
    #: Free-form source position for diagnostics, e.g. "app.py:12". Backends
    #: may ignore it; nothing in the IR's meaning depends on it.
    loc: str | None = None

    @property
    def is_terminator(self) -> bool:
        return self.op in ops.TERMINATORS


@dataclass(slots=True)
class Block:
    label: str
    instrs: list[Instr] = field(default_factory=list)

    @property
    def terminator(self) -> Instr | None:
        """The block's terminator, or None if it is unterminated (invalid)."""
        if self.instrs and self.instrs[-1].is_terminator:
            return self.instrs[-1]
        return None

    @property
    def successors(self) -> list[str]:
        """Labels this block can transfer to. Empty for `ret`/`unreachable`."""
        t = self.terminator
        if t is None:
            return []
        out = list(t.labels)
        out.extend(lbl for _, lbl in t.cases)
        return out


@dataclass(slots=True)
class Func:
    name: str
    ret: T.Type
    #: Registers holding the arguments on entry, in order.
    params: list[int] = field(default_factory=list)
    #: Every register's type. Assigning a register a value of another type is
    #: an error the verifier catches.
    regs: dict[int, T.Type] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)
    #: True if this is declared but not defined here (an external symbol).
    external: bool = False
    #: Publish this symbol from the produced artifact.
    exported: bool = False

    @property
    def entry(self) -> Block | None:
        return self.blocks[0] if self.blocks else None

    def block(self, label: str) -> Block | None:
        for b in self.blocks:
            if b.label == label:
                return b
        return None

    def reg_type(self, reg: int) -> T.Type:
        try:
            return self.regs[reg]
        except KeyError:
            raise KeyError(
                f"{self.name}: register %{reg} was used but never declared"
            ) from None


@dataclass(slots=True)
class Global:
    """A module-level datum.

    Either initialised `data` bytes or a zero-filled region of `size`. Keeping
    it to bytes means no backend has to interpret a frontend's notion of an
    initialiser -- the frontend has already laid it out.
    """

    name: str
    size: int
    data: bytes | None = None
    readonly: bool = False
    exported: bool = False


@dataclass(slots=True)
class Module:
    name: str = "module"
    funcs: list[Func] = field(default_factory=list)
    globals: list[Global] = field(default_factory=list)

    def func(self, name: str) -> Func | None:
        for f in self.funcs:
            if f.name == name:
                return f
        return None

    def glob(self, name: str) -> Global | None:
        for g in self.globals:
            if g.name == name:
                return g
        return None


class Builder:
    """Convenience layer for emitting a function. Frontends use this.

    Nothing here is privileged -- it only constructs the dataclasses above --
    but it removes the two mistakes that dominate hand-built IR: forgetting to
    declare a register's type, and appending to a block that is already
    terminated.
    """

    def __init__(self, func: Func) -> None:
        self.func = func
        self._cur: Block | None = func.blocks[0] if func.blocks else None
        self._next_reg = max(func.regs, default=-1) + 1
        self._next_label = 0

    # ── registers and blocks ────────────────────────────────────────────────
    def reg(self, ty: T.Type) -> int:
        """Allocate a fresh virtual register of `ty`."""
        r = self._next_reg
        self._next_reg += 1
        self.func.regs[r] = ty
        return r

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
        blk.instrs.append(instr)
        return instr.dst

    def _binop(self, op: Op, ty: T.Type, a: int, b: int) -> int:
        d = self.reg(ty)
        self.emit(Instr(op, ty, dst=d, args=[a, b]))
        return d

    def const(self, ty: T.Type, value: int | float) -> int:
        d = self.reg(ty)
        self.emit(Instr(Op.CONST, ty, dst=d, imm=value))
        return d

    def copy(self, dst: int, src: int) -> int:
        """Assign an EXISTING register from another. The join mechanism."""
        self.emit(Instr(Op.COPY, self.func.reg_type(dst), dst=dst, args=[src]))
        return dst

    def add(self, ty, a, b): return self._binop(Op.ADD, ty, a, b)
    def sub(self, ty, a, b): return self._binop(Op.SUB, ty, a, b)
    def mul(self, ty, a, b): return self._binop(Op.MUL, ty, a, b)
    def div(self, ty, a, b): return self._binop(Op.DIV, ty, a, b)
    def rem(self, ty, a, b): return self._binop(Op.REM, ty, a, b)

    def cmp(self, op: Op, ty: T.Type, a: int, b: int) -> int:
        """A comparison. `ty` is the OPERAND type; the result is i1."""
        d = self.reg(T.I1)
        self.emit(Instr(op, ty, dst=d, args=[a, b]))
        return d

    def load(self, ty: T.Type, addr: int) -> int:
        d = self.reg(ty)
        self.emit(Instr(Op.LOAD, ty, dst=d, args=[addr]))
        return d

    def store(self, ty: T.Type, value: int, addr: int) -> None:
        self.emit(Instr(Op.STORE, ty, args=[value, addr]))

    def alloca(self, nbytes: int) -> int:
        d = self.reg(T.PTR)
        self.emit(Instr(Op.ALLOCA, T.PTR, dst=d, imm=nbytes))
        return d

    def offset(self, base: int, off: int) -> int:
        d = self.reg(T.PTR)
        self.emit(Instr(Op.OFFSET, T.PTR, dst=d, args=[base, off]))
        return d

    def call(self, ret: T.Type, sym: str, args: list[int]) -> int | None:
        d = None if ret.is_void else self.reg(ret)
        self.emit(Instr(Op.CALL, ret, dst=d, args=list(args), sym=sym))
        return d

    # ── terminators ─────────────────────────────────────────────────────────
    def jump(self, target: Block | str) -> None:
        lbl = target if isinstance(target, str) else target.label
        self.emit(Instr(Op.JUMP, T.VOID, labels=[lbl]))

    def branch(self, cond: int, then: Block | str, els: Block | str) -> None:
        t = then if isinstance(then, str) else then.label
        e = els if isinstance(els, str) else els.label
        self.emit(Instr(Op.BRANCH, T.I1, args=[cond], labels=[t, e]))

    def ret(self, value: int | None = None) -> None:
        ty = self.func.ret
        self.emit(Instr(Op.RET, ty, args=[] if value is None else [value]))
