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
    classes: list["ClassDef"] = field(default_factory=list)
    # Populated by sema after analyze().
    imported_modules: dict = field(default_factory=dict)
    ffi_funcs: dict = field(default_factory=dict)
    ffi_consts: dict = field(default_factory=dict)
    classes_sig: dict = field(default_factory=dict)  # name -> sema.ClassSig


@dataclass
class FuncDef:
    name: str
    params: list[str]
    body: list["Stmt"]
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    # Parallel to `params`: defaults[i] is the default expression for
    # params[i], or None for required params. Only literal defaults
    # (IntLit, FloatLit, StrLit) are supported for now.
    defaults: list["Expr | None"] = field(default_factory=list)


@dataclass
class ClassDef:
    """Class with optional single-parent inheritance.

    Methods are stored as FuncDef nodes whose first parameter is conventionally
    named `self`. Each method's compiled symbol is `ClassName__methodname`.
    """

    name: str
    parent: Optional[str]
    methods: list["FuncDef"]
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
    op: str  # "+", "-", "*", "//", "%", "&", "|", "^", "<<", ">>"
    value: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class TupleAssign:
    """a, b, c = e1, e2, e3 -- evaluates every rhs first (into temporaries),
    then performs each store, so a, b = b, a works.

    Only simple name targets are supported (no nested unpacking, no `*rest`,
    no subscript/attr targets yet)."""

    targets: list[str] = field(default_factory=list)
    values: list["Expr"] = field(default_factory=list)
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
    """from math import sqrt, pi   (names land in current scope unprefixed).

    `level` is the number of leading dots: 0 for absolute imports, 1 for
    `from .x import y`, 2 for `from ..x import y`, etc. Relative imports
    aren't resolved against project files yet; they parse and bind their
    names to the int sentinel so source that uses them can still be checked.
    """

    module: str
    names: list[str] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    level: int = 0


@dataclass
class Attr:
    """obj.name access. Used for `math.sqrt(x)` style after `import math`,
    and for instance attribute access (`self.x`, `point.x`)."""

    obj: "Expr"
    name: str
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    inferred_type: str = "int"


@dataclass
class AttrAssign:
    """obj.name = value  (statement-level)."""

    obj: "Expr"
    name: str
    value: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Try:
    """`try: body except [as name]: handler`.

    No `finally` or `else` clauses in v1. No exception classes — `except`
    catches anything raised. If `bind_name` is set, the exception message
    string is bound to that local name inside the handler.
    """

    body: list["Stmt"]
    handler: list["Stmt"]
    bind_name: Optional[str] = None
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Raise:
    """`raise expr` — expr must evaluate to a str."""

    value: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


Stmt = (
    Assign
    | AugAssign
    | Return
    | If
    | While
    | For
    | Break
    | Continue
    | ExprStmt
    | Pass
    | Import
    | FromImport
    | AttrAssign
    | Try
    | Raise
)
# IndexAssign is also a Stmt but forward-referenced because Subscript is defined below.


# ---- Expressions ------------------------------------------------------------


@dataclass
class IntLit:
    value: int
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class FloatLit:
    value: float
    label: str = ""  # codegen fills with the .rodata label
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
    # Filled in by sema when inferred_type == "list" — element kind ("int" /
    # "str" / "float"). Lets codegen specialise iteration / indexing without
    # re-running the scope analysis.
    list_el_type: str = "int"


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
    """[a, b, c] literal.

    Elements must be homogeneous within a single list: all-int, all-str, or
    all-float. `el_type` is filled in by sema. The high-level value type
    ("list") doesn't carry the element kind so existing comparisons keep
    working; element-aware sites (append/index/iteration/print) consult
    `el_type` explicitly.
    """

    elems: list["Expr"] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    el_type: str = "int"


@dataclass
class Subscript:
    """obj[index] - read or write depending on context."""

    obj: "Expr"
    index: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    inferred_type: str = "int"


@dataclass
class Slice:
    """s[start:stop:step] inside a Subscript's index slot. Any of the three
    may be None (use the implicit endpoint / step=1)."""

    start: "Expr | None" = None
    stop: "Expr | None" = None
    step: "Expr | None" = None
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


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
    # When inferred_type == "list", element kind ("int" / "str" / "float").
    # Set by sema for methods that return lists (e.g. dict.keys()/.values()).
    list_el_type: str = "int"


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


@dataclass
class DictLit:
    """{key: value, ...} literal. Currently restricted to str-keyed, int-valued."""

    keys: list["Expr"] = field(default_factory=list)
    values: list["Expr"] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


Expr = (
    IntLit
    | FloatLit
    | StrLit
    | Name
    | BinOp
    | UnaryOp
    | Compare
    | BoolOp
    | Call
    | ListLit
    | Subscript
    | MethodCall
    | FString
    | Attr
    | DictLit
)


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
    if isinstance(e, DictLit):
        return "dict"
    if isinstance(e, FString):
        return "str"
    if isinstance(e, (Call, Name, MethodCall, Attr)):
        return e.inferred_type
    if isinstance(e, Subscript):
        return getattr(e, "inferred_type", "int")
    if isinstance(e, BinOp):
        lt, rt = expr_type(e.left), expr_type(e.right)
        if e.op in ("&", "|", "^", "<<", ">>"):
            return "int"  # bitwise ops only legal on ints (sema rejects floats)
        # String operations: + concatenates; * repeats (str * int).
        if e.op == "+" and lt == "str" and rt == "str":
            return "str"
        if e.op == "*" and (
            (lt == "str" and rt == "int") or (lt == "int" and rt == "str")
        ):
            return "str"
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
