"""The opcode table.

This is the whole instruction set, and it is the single source of truth for
four consumers that would otherwise drift apart:

    verify.py   checks operand counts and types against these specs
    text.py     prints and parses using these names
    interp.py   executes them
    docs        `irc ops` prints this table; no separate reference to maintain

A backend author reads this file and nothing else. That is the design target:
if implementing a backend requires understanding a second document, the second
document is the bug.

WHY THE TYPE IS A FIELD, NOT PART OF THE NAME
---------------------------------------------
An instruction is `Instr(op, ty, dst, args)`. The opcode says WHAT, the `ty`
field says AT WHAT WIDTH:

    %3 = i64.add %1, %2        Instr(Op.ADD, I64, dst=3, args=[1, 2])

The alternative -- baking the width into the name, as WebAssembly does -- means
`i8.add i16.add i32.add i64.add u8.add ...` and an opcode table that multiplies
out to several hundred entries. Keeping the type in a field holds the table at
~40 opcodes while giving a backend exactly the same information in exactly the
same place: on the instruction it is currently looking at. It never has to
consult a value-type table or infer anything.

WHY SIGNEDNESS IS ON THE TYPE, NOT THE OPCODE
---------------------------------------------
There is one `DIV`, not `idiv`/`udiv`/`fdiv`. Its meaning is read from `ty`:
signed on `i*`, unsigned on `u*`, floating on `f*`. The old IR carried
signedness in BOTH the opcode and the operand type, which meant the two could
disagree and nothing checked that they didn't.

WHY THERE ARE NO PHI NODES
--------------------------
Values are mutable virtual registers: a register may be assigned many times.
Where SSA would place a phi at a join, a frontend simply assigns the same
register on both incoming paths. This removes the single hardest concept from a
backend author's path, at the cost of making some optimisations rebuild SSA
themselves if they want it (`passes/mem2reg.py` does exactly that, locally).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass

from . import types as T


class Op(enum.Enum):
    """Every instruction. The value is the mnemonic used in the text format."""

    # ── constants and addresses ─────────────────────────────────────────────
    CONST = "const"
    GLOBAL_ADDR = "global_addr"
    FUNC_ADDR = "func_addr"

    # ── data movement ───────────────────────────────────────────────────────
    COPY = "copy"

    # ── integer / float arithmetic (meaning follows `ty`) ────────────────────
    ADD = "add"
    SUB = "sub"
    MUL = "mul"
    DIV = "div"
    REM = "rem"
    NEG = "neg"

    # ── bitwise (integers only) ─────────────────────────────────────────────
    AND = "and"
    OR = "or"
    XOR = "xor"
    NOT = "not"
    SHL = "shl"
    SHR = "shr"

    # ── comparison (result is always i1) ────────────────────────────────────
    EQ = "eq"
    NE = "ne"
    LT = "lt"
    LE = "le"
    GT = "gt"
    GE = "ge"

    # ── conversion ──────────────────────────────────────────────────────────
    TRUNC = "trunc"
    EXTEND = "extend"
    FTOI = "ftoi"
    ITOF = "itof"
    FTOF = "ftof"
    BITCAST = "bitcast"

    # ── memory ──────────────────────────────────────────────────────────────
    ALLOCA = "alloca"
    LOAD = "load"
    STORE = "store"
    OFFSET = "offset"

    # ── calls ───────────────────────────────────────────────────────────────
    CALL = "call"
    CALL_PTR = "call_ptr"

    # ── terminators ─────────────────────────────────────────────────────────
    JUMP = "jump"
    BRANCH = "branch"
    SWITCH = "switch"
    RET = "ret"
    UNREACHABLE = "unreachable"


#: How an instruction's operand types are constrained. The verifier reads this;
#: it is not a general type language, just the handful of shapes that occur.
class Sig(enum.Enum):
    NONE = "none"                 #: takes no value operands
    SAME = "same"                 #: every operand has the instruction's `ty`
    SAME_ONE = "same_one"         #: exactly one operand, of the instruction's `ty`
    CMP = "cmp"                   #: two operands sharing one type; result i1
    CONV_ONE = "conv_one"         #: one operand whose type differs from `ty`
    PTR_ONE = "ptr_one"           #: one ptr operand
    PTR_INT = "ptr_int"           #: a ptr and an integer
    STORE = "store"               #: a value of `ty`, then a ptr
    ANY = "any"                   #: unconstrained (calls, switch)


@dataclass(frozen=True, slots=True)
class Spec:
    """What one opcode requires and produces."""

    op: Op
    sig: Sig
    #: Number of value operands, or None if variadic.
    arity: int | None
    #: What the instruction defines: "ty" (a value of `ty`), "i1", or "none".
    result: str
    #: True if this must be the last instruction of its block.
    terminator: bool
    #: Which types `ty` may be. Empty means any non-void type.
    allowed: tuple[T.Type, ...]
    doc: str

    @property
    def defines_value(self) -> bool:
        return self.result != "none"


def _s(op, sig, arity, result, doc, *, terminator=False, allowed=()):
    return Spec(op, sig, arity, result, terminator, tuple(allowed), doc)


_NUM = T.INTS + T.FLOATS + (T.PTR,)
_ARITH = T.INTS + T.FLOATS
_BITS = T.INTS

#: The table. Order is the order `irc ops` prints, so it is grouped for reading.
SPECS: dict[Op, Spec] = {s.op: s for s in (
    _s(Op.CONST, Sig.NONE, 0, "ty",
       "Materialise a literal. The value lives in `Instr.imm`.",
       allowed=_NUM),
    _s(Op.GLOBAL_ADDR, Sig.NONE, 0, "ty",
       "Address of a module global named by `Instr.sym`.", allowed=(T.PTR,)),
    _s(Op.FUNC_ADDR, Sig.NONE, 0, "ty",
       "Address of a function named by `Instr.sym`, for indirect calls.",
       allowed=(T.PTR,)),

    _s(Op.COPY, Sig.SAME_ONE, 1, "ty",
       "Move a value into another register. Without SSA this is how a frontend "
       "joins two paths: assign the same register on both."),

    _s(Op.ADD, Sig.SAME, 2, "ty", "Add. Wraps on overflow for integers.",
       allowed=_ARITH),
    _s(Op.SUB, Sig.SAME, 2, "ty", "Subtract. Wraps on overflow for integers.",
       allowed=_ARITH),
    _s(Op.MUL, Sig.SAME, 2, "ty", "Multiply. Wraps on overflow for integers.",
       allowed=_ARITH),
    _s(Op.DIV, Sig.SAME, 2, "ty",
       "Divide: signed on i*, unsigned on u*, IEEE on f*. Integer division by "
       "zero is undefined -- a frontend that needs a check must emit one.",
       allowed=_ARITH),
    _s(Op.REM, Sig.SAME, 2, "ty",
       "Remainder, with the sign rules of the operand type. NOT Python's "
       "floor-modulo: a Python frontend lowers `%` to more than this opcode.",
       allowed=_ARITH),
    _s(Op.NEG, Sig.SAME_ONE, 1, "ty", "Arithmetic negation.", allowed=_ARITH),

    _s(Op.AND, Sig.SAME, 2, "ty", "Bitwise and.", allowed=_BITS),
    _s(Op.OR, Sig.SAME, 2, "ty", "Bitwise or.", allowed=_BITS),
    _s(Op.XOR, Sig.SAME, 2, "ty", "Bitwise exclusive or.", allowed=_BITS),
    _s(Op.NOT, Sig.SAME_ONE, 1, "ty", "Bitwise complement.", allowed=_BITS),
    _s(Op.SHL, Sig.SAME, 2, "ty",
       "Shift left. Shifting by >= the width is undefined.", allowed=_BITS),
    _s(Op.SHR, Sig.SAME, 2, "ty",
       "Shift right: arithmetic on i*, logical on u*.", allowed=_BITS),

    _s(Op.EQ, Sig.CMP, 2, "i1", "Equal. `ty` is the type of the OPERANDS.",
       allowed=_NUM),
    _s(Op.NE, Sig.CMP, 2, "i1", "Not equal.", allowed=_NUM),
    _s(Op.LT, Sig.CMP, 2, "i1",
       "Less than: signed, unsigned or ordered-float per `ty`.", allowed=_NUM),
    _s(Op.LE, Sig.CMP, 2, "i1", "Less or equal.", allowed=_NUM),
    _s(Op.GT, Sig.CMP, 2, "i1", "Greater than.", allowed=_NUM),
    _s(Op.GE, Sig.CMP, 2, "i1", "Greater or equal.", allowed=_NUM),

    _s(Op.TRUNC, Sig.CONV_ONE, 1, "ty",
       "Narrow an integer, discarding high bits.", allowed=_BITS),
    _s(Op.EXTEND, Sig.CONV_ONE, 1, "ty",
       "Widen an integer. Sign-extends if the SOURCE type is signed, "
       "zero-extends otherwise -- so the source, not the destination, decides.",
       allowed=_BITS),
    _s(Op.FTOI, Sig.CONV_ONE, 1, "ty",
       "Float to integer, truncating toward zero.", allowed=_BITS),
    _s(Op.ITOF, Sig.CONV_ONE, 1, "ty",
       "Integer to float, respecting the source's signedness.",
       allowed=T.FLOATS),
    _s(Op.FTOF, Sig.CONV_ONE, 1, "ty", "Convert between float widths.",
       allowed=T.FLOATS),
    _s(Op.BITCAST, Sig.CONV_ONE, 1, "ty",
       "Reinterpret the bits, same width. f64 <-> i64, ptr <-> u64.",
       allowed=_NUM),

    _s(Op.ALLOCA, Sig.NONE, 0, "ty",
       "Reserve `Instr.imm` bytes of frame storage; yields its address. "
       "Freed when the function returns.", allowed=(T.PTR,)),
    _s(Op.LOAD, Sig.PTR_ONE, 1, "ty",
       "Read a value of `ty` from an address.", allowed=_NUM),
    _s(Op.STORE, Sig.STORE, 2, "none",
       "Write a value of `ty` to an address. Operands are (value, address).",
       allowed=_NUM),
    _s(Op.OFFSET, Sig.PTR_INT, 2, "ty",
       "Add a BYTE offset to a pointer. Field access and array indexing are "
       "both this -- the frontend computes the byte count, so the IR needs no "
       "notion of element type or struct layout.", allowed=(T.PTR,)),

    _s(Op.CALL, Sig.ANY, None, "ty",
       "Call the function named by `Instr.sym`. `ty` is the return type, or "
       "void. Operands are the arguments."),
    _s(Op.CALL_PTR, Sig.ANY, None, "ty",
       "Call through a function pointer. The first operand is the address; "
       "the rest are arguments."),

    _s(Op.JUMP, Sig.NONE, 0, "none",
       "Unconditional branch to `Instr.labels[0]`.",
       terminator=True, allowed=(T.VOID,)),
    _s(Op.BRANCH, Sig.SAME_ONE, 1, "none",
       "Branch on an i1: `labels[0]` if non-zero, else `labels[1]`.",
       terminator=True, allowed=(T.I1,)),
    _s(Op.SWITCH, Sig.ANY, 1, "none",
       "Multi-way branch on an integer. `Instr.cases` pairs values with "
       "labels; `labels[0]` is the default.",
       terminator=True, allowed=_BITS),
    _s(Op.RET, Sig.ANY, None, "none",
       "Return. One operand of `ty`, or none when `ty` is void.",
       terminator=True),
    _s(Op.UNREACHABLE, Sig.NONE, 0, "none",
       "Control never arrives here. A backend may emit a trap.",
       terminator=True, allowed=(T.VOID,)),
)}

#: Mnemonic -> Op, for the text parser.
BY_NAME: dict[str, Op] = {op.value: op for op in Op}

TERMINATORS = frozenset(op for op, s in SPECS.items() if s.terminator)


def spec(op: Op) -> Spec:
    return SPECS[op]


def by_name(name: str) -> Op:
    try:
        return BY_NAME[name]
    except KeyError:
        raise ValueError(f"unknown opcode {name!r}") from None


assert set(SPECS) == set(Op), (
    "every Op needs a Spec: missing " + ", ".join(
        o.name for o in Op if o not in SPECS)
)
