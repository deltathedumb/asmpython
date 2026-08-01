"""The IR's data structures.

    Module
      globals   : Global   -- initialised bytes or a zero-filled region
      functions : Function -- definitions and external declarations
    Function
      params    : registers holding the arguments on entry
      registers : every register's type, declared once
      blocks    : first is the entry; each ends in exactly one terminator
    Block
      label     : unique within its function
      instructions : the last, and only the last, is a terminator

TWO INVARIANTS DO MOST OF THE WORK

Every value operand is a REGISTER ID -- never a literal, never a nested
expression. A backend reading `instruction.args` knows each entry names a
register whose type it can look up. Literals enter only through `Op.CONST`,
which costs an instruction per constant and buys one code path for operand
handling instead of three.

Registers are MUTABLE and there are no phi nodes. Where SSA needs a phi, a
frontend assigns the same register on both incoming paths. A register keeps one
type for its whole life, which the verifier enforces -- mutability is about
when a value is written, never about what shape it has.

EVERY INSTRUCTION CARRIES A SPAN. It is the reason an optimiser can report
"this comparison is always true" against the user's source rather than against
IR they have never seen. Passes that synthesise instructions inherit the span
of whatever they were derived from; a span is cheap to copy and expensive to
reconstruct after the fact.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Iterator

from ..diagnostics import NO_SPAN, Span
from . import types as T
from .opcodes import Op, TERMINATORS

#: A virtual register. A plain int -- the type lives in `Function.registers`,
#: so an operand is 8 bytes and comparing two is an integer compare.
Register = int


class Linkage(enum.Enum):
    """How a symbol is visible outside the module."""

    INTERNAL = "internal"   #: not visible; may be renamed or deleted
    EXPORT = "export"       #: published from the artifact
    IMPORT = "import"       #: declared here, defined elsewhere


@dataclass(slots=True)
class Instruction:
    """One instruction.

    `ty` is the width the opcode operates at: the result type for most
    opcodes, and for a comparison the type of its OPERANDS, since a comparison
    always produces i1.
    """

    op: Op
    ty: T.Type
    dst: Register | None = None
    args: list[Register] = field(default_factory=list)
    #: Literal payload: the value for CONST, the byte count for ALLOCA.
    imm: int | float | None = None
    #: Symbol referenced by CALL / GLOBAL_ADDR / FUNC_ADDR.
    sym: str | None = None
    #: Branch destinations, by block label.
    labels: list[str] = field(default_factory=list)
    #: SWITCH only: (value, label). `labels[0]` is the default.
    cases: list[tuple[int, str]] = field(default_factory=list)
    #: Where this came from in the user's source.
    span: Span = NO_SPAN

    @property
    def is_terminator(self) -> bool:
        return self.op in TERMINATORS

    @property
    def has_side_effects(self) -> bool:
        """True if removing this instruction would change behaviour.

        Dead-code elimination asks this. A call is assumed to have effects: the
        IR has no purity annotation, and treating an unknown call as pure would
        delete the one that prints.
        """
        return self.op in _EFFECTFUL or self.is_terminator

    def uses(self) -> Iterator[Register]:
        yield from self.args

    def replace_uses(self, mapping: dict[Register, Register]) -> bool:
        """Rewrite operands through `mapping`. Returns True if anything changed."""
        changed = False
        for i, a in enumerate(self.args):
            if a in mapping and mapping[a] != a:
                self.args[i] = mapping[a]
                changed = True
        return changed

    def clone(self) -> Instruction:
        return Instruction(
            self.op, self.ty, self.dst, list(self.args), self.imm, self.sym,
            list(self.labels), list(self.cases), self.span,
        )


_EFFECTFUL = frozenset({Op.STORE, Op.CALL, Op.CALL_PTR, Op.ALLOCA})


@dataclass(slots=True)
class Block:
    label: str
    instructions: list[Instruction] = field(default_factory=list)

    @property
    def terminator(self) -> Instruction | None:
        if self.instructions and self.instructions[-1].is_terminator:
            return self.instructions[-1]
        return None

    @property
    def successors(self) -> list[str]:
        t = self.terminator
        if t is None:
            return []
        out = list(t.labels)
        out.extend(label for _, label in t.cases)
        return out

    def __len__(self) -> int:
        return len(self.instructions)

    def __iter__(self) -> Iterator[Instruction]:
        return iter(self.instructions)


@dataclass(slots=True)
class Function:
    name: str
    ret: T.Type
    params: list[Register] = field(default_factory=list)
    registers: dict[Register, T.Type] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)
    linkage: Linkage = Linkage.INTERNAL
    #: True when declared but not defined here.
    external: bool = False
    span: Span = NO_SPAN

    @property
    def entry(self) -> Block | None:
        return self.blocks[0] if self.blocks else None

    def block(self, label: str) -> Block | None:
        for b in self.blocks:
            if b.label == label:
                return b
        return None

    def block_index(self) -> dict[str, int]:
        """Label -> position. Built fresh; block lists are mutated by passes."""
        return {b.label: i for i, b in enumerate(self.blocks)}

    def register_type(self, reg: Register) -> T.Type:
        try:
            return self.registers[reg]
        except KeyError:
            raise KeyError(
                f"{self.name}: register %{reg} used but never declared"
            ) from None

    def instructions(self) -> Iterator[tuple[Block, Instruction]]:
        for b in self.blocks:
            for ins in b.instructions:
                yield b, ins

    def new_register(self, ty: T.Type) -> Register:
        reg = max(self.registers, default=-1) + 1
        self.registers[reg] = ty
        return reg

    @property
    def signature(self) -> str:
        params = ", ".join(str(self.registers.get(p, T.VOID)) for p in self.params)
        return f"{self.name}({params}) -> {self.ret}"


@dataclass(slots=True)
class Global:
    """A module-level datum: initialised bytes, or a zero-filled region.

    Bytes rather than a structured initialiser, so no backend has to interpret
    a frontend's notion of one. Layout is the frontend's job; `support.layout`
    provides the C rules for a frontend that wants them.
    """

    name: str
    size: int
    data: bytes | None = None
    readonly: bool = False
    linkage: Linkage = Linkage.INTERNAL
    #: Required alignment in bytes; 0 means natural for the size.
    align: int = 0
    span: Span = NO_SPAN

    @property
    def is_zero_filled(self) -> bool:
        return self.data is None


@dataclass(slots=True)
class Module:
    name: str = "module"
    functions: list[Function] = field(default_factory=list)
    globals: list[Global] = field(default_factory=list)
    #: Free-form provenance, e.g. {"frontend": "python", "source": "prog.py"}.
    metadata: dict[str, str] = field(default_factory=dict)

    def function(self, name: str) -> Function | None:
        for f in self.functions:
            if f.name == name:
                return f
        return None

    def global_(self, name: str) -> Global | None:
        for g in self.globals:
            if g.name == name:
                return g
        return None

    def defined_functions(self) -> Iterator[Function]:
        for f in self.functions:
            if not f.external:
                yield f

    def statistics(self) -> dict[str, int]:
        blocks = sum(len(f.blocks) for f in self.defined_functions())
        instrs = sum(len(b.instructions)
                     for f in self.defined_functions() for b in f.blocks)
        return {
            "functions": sum(1 for _ in self.defined_functions()),
            "externals": sum(1 for f in self.functions if f.external),
            "globals": len(self.globals),
            "blocks": blocks,
            "instructions": instrs,
        }
