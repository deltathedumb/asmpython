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
    # Assembly packages requested via include(...), in source order. Populated
    # by sema from the loaded `.asmpkg` manifests; codegen emits their NASM and
    # treats their exports as callable symbols.
    asm_packages: list = field(default_factory=list)  # list[pkgformat.AsmPackage]


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
    # Parallel to `params`: param_types[i] is the normalized annotation
    # descriptor (base, el) for params[i], or None if unannotated. Filled in
    # by the parser; sema turns it into a static type for the param.
    param_types: list = field(default_factory=list)
    # Normalized return annotation descriptor (base, el), or None. Lets sema
    # type call sites: `s.upper()` returns str, so `print(f())` prints a str.
    ret_type: object = None
    # Name of the `*args` parameter, or None. The vararg is also appended to
    # `params` as a trailing list-typed slot; call sites pack their surplus
    # positional arguments into a list and pass it there, so the callee and the
    # register-spill prologue treat it as an ordinary (list) parameter.
    vararg: "Optional[str]" = None
    # Set when the function was marked `@assembly_func`: `asm_body` is the raw
    # NASM lifted from the docstring (emitted verbatim as the body) and
    # `asm_symbol` is the label to define (defaults to `name`). When `asm_body`
    # is a non-empty string, codegen emits it verbatim instead of generating a
    # body from `body`, and sema skips analysing `body`.
    asm_body: "Optional[str]" = None
    asm_symbol: "Optional[str]" = None
    # True for nested functions lifted to module level by the parser. Sema
    # skips undefined-variable errors in their bodies (closure vars).
    is_lifted: bool = False
    # Decorator identities preceding the def (leading dotted names), e.g.
    # ["staticmethod"] / ["classmethod"]. Used to relax the method `self` rule.
    decorators: list = field(default_factory=list)


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
    # Class-body variable declarations: parallel list of (name, annot, value)
    # where annot is a parser annotation descriptor or None and value is the
    # initializer Expr or None. Used by sema to type class attributes (e.g. a
    # set/dict constant referenced as `self.NAME`).
    class_vars: list = field(default_factory=list)
    # Set when the class carries a @dataclass (or @dataclass(...)) decorator.
    # Sema synthesises __init__ from class_vars when True and no explicit
    # __init__ is defined.
    is_dataclass: bool = False
    # Decorator names collected by the parser (e.g. ["dataclass", "frozen"]).
    decorators: list = field(default_factory=list)


# ---- Statements -------------------------------------------------------------


@dataclass
class Assign:
    target: str
    value: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    # Parser annotation descriptor (base, el) from `name: T = value`, or None.
    # Lets sema type the target from the declaration — e.g. `xs: list[str] = []`
    # pins the element kind even though the initializer is an empty/opaque list.
    annot: object = None


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
class MultiAssign:
    """a = b = c = value — evaluate value once, assign to all targets."""

    targets: list[str]
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
    # `else` clause: runs when the loop condition becomes False without a break.
    orelse: list["Stmt"] = field(default_factory=list)


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
    # For `for a, b in <iter>:` the unpack targets land here (len >= 2). When
    # empty, the loop is single-target and `var` holds the one name. Each entry
    # is normally a name (str); for nested unpacking like
    # `for i, (a, b) in enumerate(zip(...))` an entry may itself be a list[str].
    targets: list = field(default_factory=list)
    # Per-target element kinds for tuple-unpack loops (`for a, b in xs`), filled
    # by sema from the iterable's element tuple slots so codegen types each bound
    # name. Empty -> targets are opaque ("any").
    target_types: list = field(default_factory=list)
    # `else` clause: runs when the iterator is exhausted without a break.
    orelse: list["Stmt"] = field(default_factory=list)


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
class Include:
    """include("name") — pull in an assembly package (`<name>.asmpkg`).

    Built from a top-level `include(...)` call (the function imported from
    `asmpython.assembly`). The package's exported symbols become callable; its
    NASM is concatenated into the program's output. `name` is the literal
    package name. Resolution + loading happen in sema/codegen via
    `asmpython.assembly.pkgformat`.
    """

    name: str
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
    # The *original* exported names, parallel to `names`. For `from m import a
    # as b`, `names == ["b"]` (the local binding) and `orig_names == ["a"]`
    # (what `m` calls it). Equal to `names` when no `as` alias is used. The
    # whole-program loader uses this to map a local alias back to the global it
    # refers to in the source module.
    orig_names: list[str] = field(default_factory=list)


@dataclass
class Attr:
    """obj.name access. Used for `math.sqrt(x)` style after `import math`,
    and for instance attribute access (`self.x`, `point.x`)."""

    obj: "Expr"
    name: str
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    inferred_type: str = "int"
    # When the field is a collection, sema stamps the element kind here so a
    # later `self.xs[i]` / `for x in self.xs` recovers it (str / instance / …).
    list_el_type: str = "int"
    value_type: str = "int"
    tuple_elem_types: list = field(default_factory=list)


@dataclass
class AttrAssign:
    """obj.name = value  (statement-level)."""

    obj: "Expr"
    name: str
    value: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    # Parser annotation descriptor from `self.x: T = value`, or None. Lets sema
    # type the field from the declaration even when the value is an empty/opaque
    # initializer (`self.classes: dict[str, ClassSig] = {}`).
    annot: object = None


@dataclass
class With:
    """`with expr [as name]: body` — context manager.
    Lowered as: evaluate expr, optionally bind result to name, run body.
    __enter__/__exit__ are not modelled; the expression result is the value."""

    expr: "Expr"
    name: "Optional[str]"
    body: list["Stmt"]
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Try:
    """`try: body (except [Type] [as name]: handler)+ [else: ...] [finally: ...]`.

    asmpython has no exception-class RTTI, so an `except` clause's type is parsed
    but ignored: the first handler catches anything raised. `bind_name` binds
    the exception's message string inside that first handler.

    The first handler stays in `handler` / `bind_name` for back-compat with the
    single-handler codegen path. Any additional `except` clauses land in
    `extra_handlers` as (bind_name, body) pairs; `else_body` / `finally_body`
    hold the optional trailing clauses. Codegen currently implements only the
    single-handler, no-else, no-finally shape and rejects the rest until the
    full handler machinery lands.
    """

    body: list["Stmt"]
    handler: list["Stmt"]
    bind_name: Optional[str] = None
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    extra_handlers: list = field(default_factory=list)  # list[(bind_name, body)]
    else_body: list["Stmt"] = field(default_factory=list)
    finally_body: list["Stmt"] = field(default_factory=list)


@dataclass
class Raise:
    """`raise expr` — expr must evaluate to a str. `value` is None for a
    bare `raise` (re-raise the currently-active exception)."""

    value: "Expr | None"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Global:
    """`global x, y` — declare names as module-level in this function."""

    names: list[str] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Nonlocal:
    """`nonlocal x, y` — tell sema the names are from an enclosing scope."""

    names: list[str] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Del:
    """`del target` — delete a variable, dict key, or list element.

    `target` is:
      - a Name node            → zero the local / global slot
      - a Subscript node       → dict pop or list remove-by-index
    """

    target: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


Stmt = (
    Assign
    | AugAssign
    | MultiAssign
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
    | Include
    | AttrAssign
    | Try
    | With
    | Raise
    | Global
    | Nonlocal
    | Del
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
    # Filled in by sema when inferred_type == "list" and list_el_type is
    # itself a container ("list"/"dict") — the common element/value kind one
    # level down, so repr can recurse into nested containers.
    list_el_value_type: str = "int"
    # Filled in by sema when inferred_type == "dict" — value kind ("int" /
    # "str" / "float" / "list" / "dict").
    value_type: str = "int"
    # Filled in by sema when inferred_type == "dict" and value_type is itself
    # a container ("list"/"dict") — the common element/value kind one level
    # down, so repr can recurse into nested containers.
    inner_value_type: str = "int"
    # Filled in by sema when inferred_type == "tuple": the per-position
    # element kinds (e.g. ["int", "str"]). Tuples are heterogeneous, so
    # there's one entry per slot rather than a single element type.
    tuple_elem_types: list[str] = field(default_factory=list)


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
class IfExp:
    """Conditional expression: `body if test else orelse`.

    Both arms must produce the same static type (with int/float promotion),
    so codegen knows which register class (rax vs xmm0) the result lands in.
    `inferred_type` / `list_el_type` are filled in by sema.
    """

    test: "Expr"
    body: "Expr"
    orelse: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    inferred_type: str = "int"
    list_el_type: str = "int"


@dataclass
class Call:
    func: str
    args: list["Expr"] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    # Set by sema for builtins whose return type is known (str / int).
    inferred_type: str = "int"
    # When inferred_type == "list", the element kind of the returned list.
    list_el_type: str = "int"
    # Set by sema when the callee returns a tuple: the element kinds of that
    # tuple, so `a, b = f()` knows the per-target types at the call site.
    tuple_elem_types: list[str] = field(default_factory=list)
    # When inferred_type == "dict", the value kind of the returned dict, so
    # `f()[k]` reads recover it. Set by sema from the callee's `-> dict[..]`.
    value_type: str = "int"
    # Keyword arguments: parallel list of (name, expr). Sema maps them onto
    # the callee's positional parameters.
    kwargs: list = field(default_factory=list)


@dataclass
class Comprehension:
    """`[elt for var in iter if cond]` and the generator-expression form
    `(elt for var in iter if cond)`. asmpython treats a genexp as an eagerly
    materialized list — consumers (`sum`, `sorted`, `for`) iterate it the same
    way. Single target, single `for`, optional single `if`.
    """

    elt: "Expr"
    var: str
    iter: "Expr"
    cond: "Optional[Expr]" = None
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    inferred_type: str = "list"
    # Element kind of the produced list (the static type of `elt`).
    list_el_type: str = "int"
    # For `[elt for a, b in <iter>]` the unpack targets land here (len >= 2).
    # When empty, the comprehension is single-target and `var` holds the one
    # name. Mirrors `A.For.targets`.
    targets: list = field(default_factory=list)


@dataclass
class Lambda:
    """`lambda params: expr` — an anonymous function expression.

    `params` is a list of parameter names (no defaults, no *args).
    `body` is the single expression returned. `func_name` is filled in by
    codegen with the generated label (e.g. `_lambda_42`) and used when the
    lambda value is later called indirectly.
    """

    params: list[str] = field(default_factory=list)
    body: "Optional[Expr]" = None
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    func_name: str = ""  # set by codegen


@dataclass
class DictComprehension:
    """`{key: value for var in iter if cond}` — builds a dict.

    The dict comprehension mirrors the list `Comprehension` but produces two
    expressions per iteration (a str key and a value). Like a DictLit, keys must
    be str and the values are homogeneous in kind; `value_type` is filled in by
    sema. Single target, single `for`, optional single `if`.
    """

    key: "Expr"
    value: "Expr"
    var: str
    iter: "Expr"
    cond: "Optional[Expr]" = None
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    inferred_type: str = "dict"
    # Value kind of the produced dict (the static type of `value`).
    value_type: str = "int"
    # For `{k: v for a, b in <iter>}` the unpack targets land here (len >= 2).
    # When empty, the comprehension is single-target and `var` holds the one
    # name. Mirrors `A.For.targets`.
    targets: list = field(default_factory=list)


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
    # When el_type is a container ("dict"/"list"), the common value/element kind
    # of those nested containers (one level down). "int" when unknown.
    el_value_type: str = "int"
    # When el_type == "tuple", the common per-slot element kinds of those tuple
    # elements (so `xs[i][0]` / `for a, b in xs` resolve). Empty when unknown.
    el_tuple_types: list = field(default_factory=list)


@dataclass
class Subscript:
    """obj[index] - read or write depending on context."""

    obj: "Expr"
    index: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    inferred_type: str = "int"
    # For list slices: element type of the resulting sub-list. Lets
    # `for x in xs[a:b]` and friends iterate with the right per-element kind.
    list_el_type: str = "int"
    # When this Subscript yields a tuple (reserved for future nested tuples),
    # the element kinds of that tuple. Empty otherwise.
    tuple_elem_types: list[str] = field(default_factory=list)
    # When this Subscript yields a dict (a nested container read out of an
    # outer dict/list), the value kind of that inner dict. "any" when the
    # nested value type isn't tracked. Lets `outer[k][k2]` stay lenient.
    value_type: str = "int"


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
    # When inferred_type == "dict", the value kind of the returned dict (set
    # by sema for calls whose signature declares `-> dict[..]`).
    value_type: str = "int"
    # When inferred_type == "tuple", per-slot kinds (so `x, y = obj.m()`
    # unpacks). Set by sema for methods that return a tuple.
    tuple_elem_types: list = field(default_factory=list)
    # Keyword arguments: parallel list of (name, expr).
    kwargs: list = field(default_factory=list)


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
    """{key: value, ...} literal. Keys must be str. Values may be any of int /
    str / float / instance:<Class>, but the dict is homogeneous in value kind
    (sema rejects mixed-value dicts). `value_type` is set by sema and lets
    codegen / iteration recover the right per-element kind."""

    keys: list["Expr"] = field(default_factory=list)
    values: list["Expr"] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    value_type: str = "int"
    # When `value_type` is itself a container ("dict"/"list"), the common
    # value/element kind of those nested containers, so a chained read
    # `outer[k][k2]` recovers the leaf type. "int" when unknown / not nested.
    inner_value_type: str = "int"
    # When `value_type == "tuple"`, the common per-slot element kinds of those
    # tuple values (e.g. `dict[str, tuple[str, str]]` -> ["str", "str"]), so
    # `d.values()` and `for k, v in d.items()` can type their unpack targets.
    # Empty when unknown / the value tuples don't share a shape.
    value_tuple_elem_types: list = field(default_factory=list)


@dataclass
class TupleLit:
    """(a, b, c) literal — a first-class, fixed-size, heterogeneous value.

    At runtime a tuple reuses the list layout (a 24-byte [cap, len, buf]
    header plus an 8-byte-per-slot buffer), so `len()`, indexing, and
    iteration share the list machinery. The difference is static: each slot
    may have its own type. `elem_types` is filled in by sema, one entry per
    element, and lets codegen pick `mov` vs `movsd` per slot and lets
    indexing recover the right result type.

    `()` is the empty tuple (len 0); `(a,)` is a 1-tuple. A parenthesised
    single expression `(a)` is *not* a tuple — the parser only builds a
    TupleLit when it sees a comma.
    """

    elems: list["Expr"] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    elem_types: list[str] = field(default_factory=list)


@dataclass
class SetLit:
    """{a, b, c} set literal. Distinguished from a dict literal by the absence
    of `key: value` colons. Not yet a runtime value — accepted so set-literal
    source (e.g. the lexer's KEYWORDS set) parses; sema/codegen support is
    still pending."""

    elems: list["Expr"] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Starred:
    """`*expr` used as a call argument, e.g. `f(*pieces[0])`.

    asmpython has no runtime varargs, so sema requires `value` to be a
    tuple-typed expression with statically-known `elem_types` (a Name,
    Subscript, or Attr — not a Call, to avoid re-evaluating side effects) and
    rewrites the single Starred argument into one `Subscript` per tuple slot
    (`value[0], value[1], ...`) before codegen ever sees it. codegen has no
    knowledge of this node at all.
    """

    value: "Expr"
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
    | TupleLit
    | SetLit
    | IfExp
    | Comprehension
    | DictComprehension
    | Lambda
    | Starred
)


def expr_type(e: Expr) -> str:
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
    if isinstance(e, Comprehension):
        return "list"
    if isinstance(e, DictComprehension):
        return "dict"
    if isinstance(e, DictLit):
        return "dict"
    if isinstance(e, TupleLit):
        return "tuple"
    if isinstance(e, SetLit):
        return "set"
    if isinstance(e, FString):
        return "str"
    if isinstance(e, (Call, Name, MethodCall, Attr)):
        return e.inferred_type
    if isinstance(e, IfExp):
        return e.inferred_type
    if isinstance(e, Subscript):
        return getattr(e, "inferred_type", "int")
    if isinstance(e, BinOp):
        # An operator overloaded via a dunder (`Path / "sub"` -> __truediv__)
        # is typed from that method's return annotation, not arithmetic
        # promotion — honor it whatever the result type.
        if getattr(e, "dunder_owner", None) is not None:
            return e.inferred_type  # type: ignore
        # sema may stamp a BinOp with a non-arithmetic result: a union of class
        # objects (`A | B | C`) is "type"; an opaque ("any") operand makes the
        # result "any"; set union/difference/intersection (|, -, &) is "set".
        # Honor those so they chain (e.g. `(a | b) | c` for nested set unions).
        if getattr(e, "inferred_type", None) in ("type", "any", "set"):
            return e.inferred_type  # type: ignore
        lt, rt = expr_type(e.left), expr_type(e.right)
        if e.op in ("&", "|", "^", "<<", ">>"):
            return "int"  # bitwise ops only legal on ints (sema rejects floats)
        # String operations: + concatenates; * repeats (str * int).
        if e.op == "+" and lt == "str" and rt == "str":
            return "str"
        # `str + any` (an opaque value concatenated onto a string) is still a
        # string — the str operand pins it. Mirrors sema's stamp.
        if e.op == "+" and "str" in (lt, rt) and "any" in (lt, rt):
            return "str"
        if e.op == "*" and (
            (lt == "str" and rt == "int") or (lt == "int" and rt == "str")
        ):
            return "str"
        # `"...%s..." % (args)` (printf-style formatting) always yields a str.
        if e.op == "%" and lt == "str":
            return "str"
        # Python's true division always produces a float, even on ints.
        if e.op == "/":
            return "float"
        if "float" in (lt, rt):
            return "float"
        return "int"
    if isinstance(e, UnaryOp):
        # `not x` is a boolean (int 0/1) whatever x is; treating it as the
        # operand's type makes `if not xs:` read the 0/1 result as a list
        # header. `-`/`~` keep the operand's numeric type.
        if e.op == "not":
            return "int"
        return expr_type(e.operand)
    if isinstance(e, Compare):
        return "int"
    if isinstance(e, BoolOp):
        # `a and b` / `a or b` evaluate to one of the operands, so the result
        # type is their common type. An opaque operand makes the result opaque;
        # two equal types pass through (so `x or "default"` stays str).
        lt, rt = expr_type(e.left), expr_type(e.right)
        if "any" in (lt, rt):
            return "any"
        if lt == rt:
            return lt
        if "float" in (lt, rt):
            return "float"
        return "int"
    return "int"


def tuple_element_types(e: Expr) -> list[str]:
    """Per-slot element kinds for a tuple-typed expression, or [] if unknown.

    Reads `elem_types` off a literal and the `tuple_elem_types` carrier field
    that sema stamps onto Names and Calls that resolve to tuples.
    """
    if isinstance(e, TupleLit):
        return list(e.elem_types)
    return list(getattr(e, "tuple_elem_types", []))


def parse_pct_format(fmt: str) -> tuple[list[tuple], int]:
    """Parse a printf-style '%' format string into (pieces, n_conversions).

    Each piece is either ("lit", text) or ("arg", flags, width, precision,
    conv), where flags/width are the raw characters between '%' and the
    conversion character and precision includes the leading '.' (or "" if
    absent). "%%" becomes a literal "%". Raises ValueError on a malformed or
    unsupported specifier. Shared by sema (validation) and codegen (lowering)
    so the two stay in sync.
    """
    pieces: list[tuple] = []
    buf = ""
    nconv = 0
    i = 0
    n = len(fmt)
    while i < n:
        ch = fmt[i]
        if ch != "%":
            buf += ch
            i += 1
            continue
        if i + 1 < n and fmt[i + 1] == "%":
            buf += "%"
            i += 2
            continue
        j = i + 1
        flags = ""
        while j < n and fmt[j] in "-+0 #":
            flags += fmt[j]
            j += 1
        width = ""
        while j < n and fmt[j].isdigit():
            width += fmt[j]
            j += 1
        precision = ""
        if j < n and fmt[j] == ".":
            precision = "."
            j += 1
            while j < n and fmt[j].isdigit():
                precision += fmt[j]
                j += 1
        if j >= n:
            raise ValueError("incomplete format specifier")
        conv = fmt[j]
        if conv not in "rsdiouxXeEfFgG":
            raise ValueError(f"unsupported format character {conv!r}")
        if buf:
            pieces.append(("lit", buf))
            buf = ""
        pieces.append(("arg", flags, width, precision, conv))
        nconv += 1
        i = j + 1
    if buf:
        pieces.append(("lit", buf))
    return pieces, nconv
