"""SSA intermediate representation shared by every codegen target.

See docs/IR-DESIGN.md for the design rationale. This module defines the
IR's data structures only (values, instructions, basic blocks, functions)
— SSA construction from the AST, register allocation, and target lowering
each live in their own modules (ssa_build.py, regalloc.py, target.py) so
this file stays a pure data model with no dependency on the AST or on any
particular architecture.

Value model: every IR value is one of two kinds (`Kind.INT` for
integers/pointers, `Kind.FLOAT` for the language's one float width) plus
a `None`/control case for instructions kept only for their side effect
(stores, branches, calls whose result is discarded). asmpython doesn't
distinguish integers from pointers at the value level — both are "an
8-byte general-purpose-register-class value" — so there's no separate
pointer kind.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class Kind(Enum):
    INT = auto()    # integer or pointer, GP-register class
    FLOAT = auto()  # f64, float-register class
    NONE = auto()   # no result value (store, branch, discarded call)


@dataclass
class Value:
    """One SSA value: the result of exactly one defining instruction, or
    a function parameter. Identity matters (two `Value`s are the same SSA
    name iff they're the same object) — never compare by id_ across
    functions, since numbering restarts per function."""

    id_: int
    kind: Kind
    # Set by the instruction that defines this value (None for params,
    # which are defined by the function's parameter list instead).
    def_: Optional["Instr"] = None

    def __repr__(self) -> str:
        sigil = "%" if self.kind != Kind.NONE else "$"
        return f"{sigil}v{self.id_}"


class Op(Enum):
    # Constants. `const_value` on the Instr holds the literal (a Python
    # int for Kind.INT, a Python float for Kind.FLOAT). A dedicated op
    # rather than e.g. an ADD-with-zero encoding, since overloading an
    # arithmetic op to mean "load this immediate" would give the
    # register allocator a fake extra use of whatever the zero operand
    # was, distorting liveness for no reason.
    CONST = auto()

    # String literal address. `const_value` holds the literal TEXT (a
    # Python str), Kind.INT (it's a pointer once lowered). Deliberately
    # carries the raw text rather than a pre-assigned label/offset:
    # string interning (deduplicating identical literals into one
    # .rodata entry, assigning the actual label) is each target's
    # lowering-stage responsibility, not ssa_build.py's — this keeps
    # SSA construction free of any global/cross-function string table,
    # since the same literal text appearing in two different functions
    # should usually still dedupe to one .rodata entry program-wide
    # (exactly what Codegen.intern_string does today), and that kind of
    # whole-program bookkeeping belongs at the point where a target is
    # actually emitting a complete program, not mid-construction of one
    # function's IR.
    STRING_ADDR = auto()

    # Integer arithmetic/logic.
    ADD = auto()
    SUB = auto()
    MUL = auto()
    SDIV = auto()  # signed division
    SREM = auto()  # signed remainder (sign matches dividend, Python //'s
    #                 floor-division adjustment is handled above the IR,
    #                 in the AST-to-IR lowering, not here)
    AND = auto()
    OR = auto()
    XOR = auto()
    SHL = auto()
    SHR = auto()  # logical shift right
    SAR = auto()  # arithmetic shift right
    NEG = auto()  # unary
    NOT = auto()  # unary, bitwise complement

    # Float arithmetic.
    FADD = auto()
    FSUB = auto()
    FMUL = auto()
    FDIV = auto()
    FNEG = auto()  # unary

    # Comparison (produce an Int64 0/1, never a flags register — see
    # docs/IR-DESIGN.md "Instruction set" for why this sidesteps the
    # x86 ucomisd-unordered-NaN vs. ARM64 fcmp semantic gap entirely).
    ICMP = auto()
    FCMP = auto()

    # Conversion.
    SITOFP = auto()  # signed-int-to-float
    FPTOSI = auto()  # float-to-signed-int (truncating toward zero)

    # Memory. `offset` is a compile-time-constant byte offset (frame
    # slots and struct-field-style heap accesses are both just this).
    LOAD = auto()
    STORE = auto()

    # Control flow.
    BR = auto()       # unconditional branch
    CONDBR = auto()   # conditional branch
    RET = auto()
    PHI = auto()

    # Calls. `is_indirect` on the instruction distinguishes "call a named
    # external/global symbol" from "call through a function-pointer SSA
    # value" (closures, import_binary — see docs/IR-DESIGN.md "Calls").
    CALL = auto()

    # Escape hatch for the 73 hand-written runtime helpers during the
    # x86-64-parity migration step — see docs/IR-DESIGN.md. `target_text`
    # on the instruction holds one assembly-text blob per target name;
    # the owning target's lowering substitutes its own entry and errors
    # if its name isn't present (so a RawAsm site silently missing an
    # ARM64 entry is a hard compile error, not silently wrong codegen).
    RAW_ASM = auto()


# Comparison predicates for ICMP/FCMP.
class Predicate(Enum):
    EQ = auto()
    NE = auto()
    LT = auto()
    LE = auto()
    GT = auto()
    GE = auto()


@dataclass
class Instr:
    """One IR instruction. `result` is None for instructions with no
    value (Kind.NONE) — STORE, BR, CONDBR, RET, and a CALL whose result
    is discarded all have `result is None`."""

    op: Op
    args: list[Value] = field(default_factory=list)
    result: Optional[Value] = None

    # CONST only: a Python int (Kind.INT) or float (Kind.FLOAT) literal.
    const_value: object = None

    # ICMP/FCMP only.
    predicate: Optional[Predicate] = None

    # LOAD/STORE only: compile-time-constant byte offset from args[0].
    offset: int = 0

    # CALL only. `callee` is a bare symbol name for a direct call, or
    # None when `is_indirect` (the callee is args[0] instead, a function
    # pointer SSA value with the rest of `args` as the call's real
    # arguments — see docs/IR-DESIGN.md "Calls").
    callee: Optional[str] = None
    is_indirect: bool = False

    # BR only.
    target: Optional["Block"] = None
    # CONDBR only.
    true_target: Optional["Block"] = None
    false_target: Optional["Block"] = None

    # PHI only: parallel to a predecessor-ordered list maintained by the
    # owning Block (see Block.preds) — incoming[i] is the value flowing
    # in from preds[i].
    incoming: list[Value] = field(default_factory=list)

    # RAW_ASM only: target name -> verbatim assembly text. See Op.RAW_ASM.
    target_text: dict[str, str] = field(default_factory=dict)


@dataclass
class Block:
    """A basic block: a maximal straight-line instruction sequence with
    one entry (the start) and one exit (the last instruction, always a
    BR/CONDBR/RET — never fallthrough; see Function.validate)."""

    name: str
    instrs: list[Instr] = field(default_factory=list)
    # Maintained by whoever builds the CFG (ssa_build.py): predecessor
    # blocks in a fixed order, so each PHI's `incoming` list lines up
    # positionally with `preds`.
    preds: list["Block"] = field(default_factory=list)

    def append(self, instr: Instr) -> None:
        self.instrs.append(instr)

    def terminator(self) -> Optional[Instr]:
        if not self.instrs:
            return None
        last = self.instrs[-1]
        return last if last.op in (Op.BR, Op.CONDBR, Op.RET) else None


@dataclass
class Function:
    """One compiled function: an ordered list of basic blocks (the first
    is the entry), plus the parameter values (defined by the function
    itself, not by any instruction)."""

    name: str
    params: list[Value] = field(default_factory=list)
    blocks: list[Block] = field(default_factory=list)
    # Bumped by `new_value`; not reset between blocks, only between
    # functions (see the module docstring's identity note).
    _next_value_id: int = 0
    # Bumped by `fresh_block_name`; keeps e.g. repeated "cmp_cont" hints
    # (one per link of a chained comparison) human-distinguishable in
    # debug output even though blocks are identified by object identity
    # everywhere else in the IR, never by name.
    _next_block_id: int = 0

    def new_value(self, kind: Kind) -> Value:
        v = Value(id_=self._next_value_id, kind=kind)
        self._next_value_id += 1
        return v

    def fresh_block_name(self, hint: str) -> str:
        self._next_block_id += 1
        return f"{hint}_{self._next_block_id}"

    def entry(self) -> Block:
        return self.blocks[0]

    def validate(self) -> None:
        """Cheap structural sanity checks, meant to catch IR-construction
        bugs early rather than as a full verifier. Checked: every block
        (a) is non-empty and (b) ends in a terminator, since the lowering
        and register-allocation stages both assume no implicit
        fallthrough between blocks."""
        if not self.blocks:
            raise ValueError(f"function {self.name!r} has no blocks")
        for b in self.blocks:
            if not b.instrs:
                raise ValueError(f"block {b.name!r} in {self.name!r} is empty")
            if b.terminator() is None:
                raise ValueError(
                    f"block {b.name!r} in {self.name!r} does not end in "
                    "a terminator (BR/CONDBR/RET)"
                )
