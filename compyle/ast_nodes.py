"""AST node definitions. Keep them dumb: just data containers.

Most nodes carry a `pos` so later phases can blame the right source location
when they reject the program. Default value is a placeholder; the parser fills
real positions in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .errors import SourcePos


_NO_POS = SourcePos(0, 0)


# ---- Module / functions -----------------------------------------------------

@dataclass
class Module:
    funcs: list["FuncDef"]
    body: list["Stmt"]
    # Populated by sema after analyze().
    imported_modules: dict = field(default_factory=dict)
    ffi_funcs: dict = field(default_factory=dict)
    ffi_consts: dict = field(default_factory=dict)


@dataclass
class FuncDef:
    name: str
    params: list[str]
    body: list["Stmt"]
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


# ---- Statements -------------------------------------------------------------

@dataclass
class Assign:
    target: str
    value: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class AugAssign:
    target: str
    op: str            # "+", "-", "*", "//", "%", "&", "|", "^", "<<", ">>"
    value: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Return:
    value: Optional["Expr"]
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class If:
    test: "Expr"
    then: list["Stmt"]
    orelse: list["Stmt"]
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class While:
    test: "Expr"
    body: list["Stmt"]
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class For:
    """`for <var> in range(...)` or `for <var> in <list-expr>`.

    Exactly one of `range_args` or `iter` is populated. `range_args` is
    1/2/3 args matching Python's range(). `iter` is any list-typed expression.
    """
    var: str
    range_args: list["Expr"]
    body: list["Stmt"]
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    iter: Optional["Expr"] = None


@dataclass
class Break:
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Continue:
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class ExprStmt:
    expr: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Pass:
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Import:
    """import math  (the module name remains visible as a prefix)."""
    module: str
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class FromImport:
    """from math import sqrt, pi   (names land in current scope unprefixed)."""
    module: str
    names: list[str] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Attr:
    """obj.name access (used for `math.sqrt(x)` style after `import math`)."""
    obj: "Expr"
    name: str
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    inferred_type: str = "int"


Stmt = Assign | AugAssign | Return | If | While | For | Break | Continue | ExprStmt | Pass | Import | FromImport
# IndexAssign is also a Stmt but forward-referenced because Subscript is defined below.


# ---- Expressions ------------------------------------------------------------

@dataclass
class IntLit:
    value: int
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class FloatLit:
    value: float
    label: str = ""    # codegen fills with the .rodata label
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class StrLit:
    value: str
    label: str = ""
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Name:
    name: str
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    inferred_type: str = "int"


@dataclass
class BinOp:
    op: str
    left: "Expr"
    right: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class UnaryOp:
    op: str
    operand: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Compare:
    """Chained comparison: ops[i] relates operands[i] and operands[i+1]."""
    ops: list[str]
    operands: list["Expr"]
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class BoolOp:
    op: str  # "and" / "or"
    left: "Expr"
    right: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Call:
    func: str
    args: list["Expr"] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    # Set by sema for builtins whose return type is known (str / int).
    inferred_type: str = "int"


@dataclass
class ListLit:
    """[a, b, c] literal. All elements assumed to be ints in our model."""
    elems: list["Expr"] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Subscript:
    """obj[index] - read or write depending on context."""
    obj: "Expr"
    index: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    inferred_type: str = "int"


@dataclass
class MethodCall:
    """obj.method(args...). Only specific known methods are supported
    (lst.append, lst.pop) so this isn't true OOP - it's syntactic sugar
    for special-cased runtime calls."""
    obj: "Expr"
    method: str
    args: list["Expr"] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    inferred_type: str = "int"


@dataclass
class IndexAssign:
    """lst[i] = value. Statement-level."""
    target: "Subscript"
    value: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class FString:
    """An f-string. `segments` alternates between StrLit and Expr nodes."""
    segments: list["Expr"] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


Expr = IntLit | FloatLit | StrLit | Name | BinOp | UnaryOp | Compare | BoolOp | Call | ListLit | Subscript | MethodCall | FString | Attr


def expr_type(e) -> str:
    """Static type of an expression: 'int', 'float', 'str', or 'list'.

    Numeric promotion: a BinOp/Compare/Unary whose operand types include
    float is itself float. Comparisons are special: they always return int
    (0 or 1) even when comparing floats.
    """
    if isinstance(e, FloatLit):
        return "float"
    if isinstance(e, StrLit):
        return "str"
    if isinstance(e, ListLit):
        return "list"
    if isinstance(e, FString):
        return "str"
    if isinstance(e, (Call, Name, MethodCall, Attr)):
        return e.inferred_type
    if isinstance(e, Subscript):
        return getattr(e, "inferred_type", "int")
    if isinstance(e, BinOp):
        lt, rt = expr_type(e.left), expr_type(e.right)
        if e.op in ("&", "|", "^", "<<", ">>"):
            return "int"   # bitwise ops only legal on ints (sema rejects floats)
        # Python's true division always produces a float, even on ints.
        if e.op == "/":
            return "float"
        if "float" in (lt, rt):
            return "float"
        return "int"
    if isinstance(e, UnaryOp):
        return expr_type(e.operand)
    if isinstance(e, Compare):
        return "int"
    if isinstance(e, BoolOp):
        # bools track the underlying op's wider type
        lt, rt = expr_type(e.left), expr_type(e.right)
        if "float" in (lt, rt):
            return "float"
        return "int"
    return "int"
