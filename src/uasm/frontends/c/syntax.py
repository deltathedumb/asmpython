"""The C syntax tree, already typed.

ONE TREE, NOT TWO. A C compiler cannot parse before it types: `(a)*b` is a
multiplication or a cast depending on whether `a` is a typedef name, and
knowing that needs a scope, which needs declarations to have been processed.
So the parser resolves names and computes types as it goes, and what comes out
the far side already carries them. A second "analysis pass" over an untyped
tree would have to redo the scope tracking the parser already did in order to
answer the question the parser already answered.

EVERY IMPLICIT CONVERSION IS A NODE. `Conv` is inserted by the parser wherever
C says a conversion happens -- array-to-pointer decay, the integer promotions,
the usual arithmetic conversions, the conversion of an argument to its
parameter's type, the conversion of a returned value. Lowering then emits code
for a tree in which nothing is implicit, and the question "where did this
sign-extension come from" has an answer that is a node with a span.

That is worth the extra nodes. The alternative is lowering rediscovering the
conversion rules at every operator, which is how the two halves of a compiler
come to disagree about what `(char)x + 1u` means.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ...diagnostics import Span
from .ctype import CType, Member


@dataclass(slots=True)
class Node:
    span: Span


# ── expressions ─────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Expr(Node):
    """Every expression carries its type and whether it is an lvalue.

    `lvalue` is not "assignable": an array, a function designator and a `const`
    object are all lvalues, and none may be assigned. The two questions are
    asked separately because their answers differ, and conflating them is how
    `arr = x` gets the error message for a const violation.
    """

    type: CType
    lvalue: bool = False


@dataclass(slots=True)
class Ident(Expr):
    """A reference to a declared object, function or enumeration constant."""

    name: str = ""
    #: The `Symbol` it resolved to. Lowering reads `sym.storage` to decide
    #: between a frame slot, a global's address and a function's address.
    sym: Any = None


@dataclass(slots=True)
class IntLit(Expr):
    value: int = 0


@dataclass(slots=True)
class FloatLit(Expr):
    value: float = 0.0


@dataclass(slots=True)
class StringLit(Expr):
    """A string literal, already concatenated with its neighbours and decoded.

    `data` is the bytes it occupies including the terminating zero, so that a
    global initialised with it is a memcpy of exactly these bytes and nothing
    downstream has to know the encoding again.
    """

    prefix: str = ""
    data: bytes = b""
    #: Assigned by lowering: the name of the global holding these bytes.
    symbol: str | None = None


@dataclass(slots=True)
class Unary(Expr):
    op: str = ""                #: one of + - ~ ! & * ++ --
    operand: Expr | None = None
    #: True for a postfix ++/--, which yields the OLD value.
    postfix: bool = False
    #: `++`/`--` only: 1 for an arithmetic operand, the element size for a
    #: pointer. Lowering adds this rather than recomputing a layout.
    scale: Any = 1


@dataclass(slots=True)
class Binary(Expr):
    op: str = ""
    left: Expr | None = None
    right: Expr | None = None
    #: Pointer arithmetic: the size of the element, so lowering multiplies by
    #: a number rather than recomputing a layout it has no business knowing.
    #: AN `int`, OR AN EXPRESSION when the element is a variable-length array
    #: -- `int (*p)[n]` has an element whose size is not known until `n` is.
    scale: Any = 1


@dataclass(slots=True)
class Logical(Expr):
    """`&&` and `||`. A separate node because they do not evaluate both sides,
    and folding them into `Binary` invites a lowering that does."""

    op: str = ""
    left: Expr | None = None
    right: Expr | None = None


@dataclass(slots=True)
class Assign(Expr):
    """`a = b`, and every compound assignment.

    A COMPOUND ASSIGNMENT KEEPS ITS OPERATOR rather than being expanded into
    `a = a + b` at parse time, because `*p++ += 1` must evaluate `p++` once.
    Lowering computes the address once and reads through it.
    """

    op: str = "="
    target: Expr | None = None
    value: Expr | None = None
    #: For `+=` on a pointer, the element size; see `Binary.scale`.
    scale: Any = 1
    #: The type the operation is performed IN, which is not the target's type:
    #: `char c; c += 1` computes in `int` and converts back.
    compute: CType | None = None


@dataclass(slots=True)
class Conditional(Expr):
    cond: Expr | None = None
    then: Expr | None = None
    otherwise: Expr | None = None


@dataclass(slots=True)
class Comma(Expr):
    left: Expr | None = None
    right: Expr | None = None


@dataclass(slots=True)
class Cast(Expr):
    """An explicit cast. `Conv` is the implicit one; they lower identically and
    are distinct so a diagnostic can say which the programmer wrote."""

    operand: Expr | None = None


@dataclass(slots=True)
class Conv(Expr):
    """An implicit conversion inserted by the parser. See the module docstring."""

    operand: Expr | None = None
    #: Why, for diagnostics: "argument", "return", "usual arithmetic", ...
    reason: str = ""


@dataclass(slots=True)
class Call(Expr):
    func: Expr | None = None
    args: list[Expr] = field(default_factory=list)
    #: Arguments past the last named parameter of a variadic function, already
    #: default-argument-promoted. Lowering builds the argument area from these.
    varargs: list[Expr] = field(default_factory=list)


@dataclass(slots=True)
class MemberAccess(Expr):
    base: Expr | None = None
    name: str = ""
    #: The whole path, through anonymous members. See `ctype.find_member`.
    path: list[Member] = field(default_factory=list)
    #: True for `->`; the base is then a pointer that must be loaded through.
    arrow: bool = False


@dataclass(slots=True)
class Index(Expr):
    """`a[i]`, already normalised so `base` is the pointer side. C defines it
    as `*(a + i)` and permits `4[a]`; normalising here means lowering never
    meets the backwards spelling."""

    base: Expr | None = None
    index: Expr | None = None
    scale: Any = 1


@dataclass(slots=True)
class SizeofType(Expr):
    """`sizeof(T)` and `_Alignof(T)`, already folded to a constant unless the
    operand is a variable-length array."""

    operand_type: CType | None = None
    #: For a VLA, the expression computing the size at run time.
    dynamic: Expr | None = None
    #: True for `_Alignof`, which asks a different question of the same type.
    alignment: bool = False


@dataclass(slots=True)
class CompoundLiteral(Expr):
    """`(T){...}`. An unnamed object with the lifetime of its scope -- a frame
    slot inside a function, a global at file scope."""

    init: Any = None
    #: Set by lowering for the file-scope case.
    symbol: str | None = None
    static: bool = False


@dataclass(slots=True)
class VaArg(Expr):
    """`va_arg(ap, T)`. A node rather than a call because its second operand is
    a TYPE, which no C function can take."""

    ap: Expr | None = None


@dataclass(slots=True)
class BuiltinCall(Expr):
    """A `__builtin_*` that lowering implements directly."""

    name: str = ""
    args: list[Expr] = field(default_factory=list)
    #: Type operands, for the builtins that take one.
    types: list[CType] = field(default_factory=list)


@dataclass(slots=True)
class StmtExpr(Expr):
    """GNU's `({ ... })`. Its value is that of the last expression statement.

    Supported because `<assert.h>`-style macros and a great deal of real code
    use it to evaluate a macro argument exactly once, and the alternative --
    refusing it -- turns a working header into a compiler error with no
    workaround the user can apply from their side.
    """

    body: Any = None


# ── initialisers ────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Init(Node):
    """A resolved initialiser.

    FLAT, AND THAT IS THE POINT. `{1, 2, {3}}`, `{[2] = 7}` and `"abc"` are
    three spellings that all end as "these bytes at these offsets"; resolving
    the braces, the designators and the brace elision in `initializer.py`
    means lowering sees a list of (offset, type, expression) and nothing else.
    """

    #: (byte offset, type, value). For a bit-field, `bits` and `bit_offset`.
    entries: list[InitEntry] = field(default_factory=list)
    #: The total size initialised, which fixes `int a[] = {1,2,3}`'s bound.
    size: int = 0


@dataclass(slots=True)
class InitEntry:
    offset: int
    type: CType
    value: Expr | None
    bits: int | None = None
    bit_offset: int = 0
    #: For a string initialising a char array: the raw bytes, padded.
    data: bytes | None = None


# ── statements ──────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Stmt(Node):
    pass


@dataclass(slots=True)
class Compound(Stmt):
    items: list[Any] = field(default_factory=list)
    #: True when the block declares a VLA, so lowering brackets it with a
    #: save and restore of the variable-length storage mark.
    has_vla: bool = False


@dataclass(slots=True)
class ExprStmt(Stmt):
    expr: Expr | None = None


@dataclass(slots=True)
class If(Stmt):
    cond: Expr | None = None
    then: Stmt | None = None
    otherwise: Stmt | None = None


@dataclass(slots=True)
class While(Stmt):
    cond: Expr | None = None
    body: Stmt | None = None


@dataclass(slots=True)
class DoWhile(Stmt):
    body: Stmt | None = None
    cond: Expr | None = None


@dataclass(slots=True)
class For(Stmt):
    init: Any = None            #: an Expr, a list of Decl, or None
    cond: Expr | None = None
    step: Expr | None = None
    body: Stmt | None = None


@dataclass(slots=True)
class Switch(Stmt):
    expr: Expr | None = None
    body: Stmt | None = None
    #: (value, label) filled in by the parser as `case`s are met, so lowering
    #: emits one `Op.SWITCH` rather than a chain of comparisons.
    cases: list[tuple[int, str]] = field(default_factory=list)
    default_label: str | None = None


@dataclass(slots=True)
class Case(Stmt):
    value: int = 0
    body: Stmt | None = None
    label: str = ""
    #: A GNU case range, `case 1 ... 5:`. `value` is the low end.
    high: int | None = None


@dataclass(slots=True)
class Default(Stmt):
    body: Stmt | None = None
    label: str = ""


@dataclass(slots=True)
class Label(Stmt):
    name: str = ""
    body: Stmt | None = None


@dataclass(slots=True)
class Goto(Stmt):
    name: str = ""
    #: A GNU computed goto, `goto *p`.
    target: Expr | None = None


@dataclass(slots=True)
class Break(Stmt):
    pass


@dataclass(slots=True)
class Continue(Stmt):
    pass


@dataclass(slots=True)
class Return(Stmt):
    value: Expr | None = None


@dataclass(slots=True)
class Empty(Stmt):
    pass


# ── declarations ────────────────────────────────────────────────────────────
@dataclass(slots=True)
class Decl(Node):
    """One declarator of one declaration. `int a, *b;` is two of these."""

    name: str = ""
    type: CType | None = None
    sym: Any = None
    init: Init | None = None
    #: The VLA's element-count expression chain, innermost first, for a local
    #: whose size is not known until it is reached.
    vla_size: Expr | None = None


@dataclass(slots=True)
class FunctionDef(Node):
    name: str = ""
    type: CType | None = None
    sym: Any = None
    params: list[Any] = field(default_factory=list)
    body: Compound | None = None
    #: Every `Symbol` with automatic storage in the function, in declaration
    #: order, so lowering can lay out the frame in one pass.
    locals: list[Any] = field(default_factory=list)
    #: Labels used by `goto`, so lowering can create the blocks up front.
    labels: dict[str, str] = field(default_factory=dict)
    #: True if the function is variadic; lowering gives it the extra argument
    #: area parameter described in `lower.py`.
    variadic: bool = False


@dataclass(slots=True)
class Unit(Node):
    """A translation unit: what the frontend hands to lowering."""

    decls: list[Any] = field(default_factory=list)
