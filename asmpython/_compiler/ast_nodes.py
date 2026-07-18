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
    enums: list["EnumDecl"] = field(default_factory=list)
    interfaces: list["InterfaceDecl"] = field(default_factory=list)
    # Populated by sema after analyze().
    imported_modules: dict = field(default_factory=dict)
    ffi_funcs: dict = field(default_factory=dict)
    ffi_consts: dict = field(default_factory=dict)
    classes_sig: dict = field(default_factory=dict)  # name -> sema.ClassSig
    # `from module import orig as local` aliases for bundled-source stdlib funcs.
    # Maps local_name -> original_name so codegen can resolve the real symbol.
    # Populated by sema during FromImport analysis.
    func_aliases: dict = field(default_factory=dict)  # local -> original
    # `overload` extension: True if this module has any @overload-dispatched
    # call sites. Only supported on --backend x86-64 today -- see driver.py.
    uses_overload: bool = False


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
    # Name of the `**kwargs` parameter, or None. Call sites pack excess keyword
    # arguments into a DictLit passed as a trailing dict-typed slot.
    kwarg: "Optional[str]" = None
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
    # True for a top-level function OR class method merged in from an
    # asmpython stdlib module (set by program.py's load_program, never by the
    # parser). Two independent things key off this:
    #  1. A stdlib module's own top-level function names are real Python
    #     stdlib API names meant to be called in a qualified way
    #     (`tarfile.open(...)`), not bare globals — see sema.py's "cannot
    #     redefine builtin" check, which would otherwise reject e.g.
    #     tarfile.py's `def open(...)` purely because it shares a name with
    #     the `open` builtin, even though nothing about it is actually a
    #     user mistake.
    #  2. Whole-program compilation merges EVERY function/method from every
    #     imported stdlib module unconditionally, whether or not the program
    #     actually calls it. sema.py's body-check loops tolerate (silently
    #     discard, rather than hard-fail the whole compile over) a semantic
    #     error inside a body that is both is_stdlib AND unreachable from the
    #     program's real entry point (see `_syntactic_reachable_names` /
    #     `_try_check_block`'s `tolerate` param) — e.g. collections.py's
    #     `namedtuple()` uses dynamic `type()`/`property()` the native
    #     compiler can't check, but that must not block a program that only
    #     does `from collections import deque` and never calls `namedtuple`.
    is_stdlib: bool = False
    # Decorator identities preceding the def (leading dotted names), e.g.
    # ["staticmethod"] / ["classmethod"]. Used to relax the method `self` rule.
    decorators: list = field(default_factory=list)
    # `readonly_params` extension: parameter names named in a preceding
    # `@readonly(name, ...)` decorator, locked against reassignment for the
    # duration of this function's body (see sema.py's per-function
    # `_locked_params` set).
    readonly_params: list = field(default_factory=list)


@dataclass
class ClassDef:
    """Class with optional single-parent inheritance.

    Methods are stored as FuncDef nodes whose first parameter is conventionally
    named `self`. Each method's compiled symbol is `ClassName__methodname`.
    """

    name: str
    parent: Optional[str]
    methods: list[FuncDef]
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
    # Per-field decorator names (e.g. {"balance": ["private"]}) for class-body
    # variables carrying their own decorator line, used by the `access` and
    # `immutable` compiler extensions. A SEPARATE dict rather than widening
    # `class_vars`'s tuple shape: `class_vars` is unpacked as a bare 3-tuple
    # at ~8 call sites across parser/sema/codegen/ir_lower (e.g. `for fname,
    # _fannot, fvalue in c.class_vars`), and most fields never carry a
    # decorator at all, so a parallel sparse dict is both lower-risk (zero
    # existing unpacking sites touched) and avoids padding every ordinary
    # field with an always-empty 4th tuple element.
    field_decorators: dict = field(default_factory=dict)
    # `final` extension: set when the class was declared `final class Name:`
    # (a soft-keyword statement prefix, not a decorator -- unlike @final on
    # a method, a class-level "no subclasses at all" restriction reads more
    # naturally attached to the class statement itself).
    is_final: bool = False
    # `sealed` extension: set when declared `sealed class Name(permits=A, B):`.
    # `sealed_permits` holds the leaf names of the permitted subclasses.
    is_sealed: bool = False
    sealed_permits: list = field(default_factory=list)
    # `interface` extension: set when declared `class X(interface=Name):`.
    implements_interface: "str | None" = None


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
class ConstDecl:
    """`const NAME [: annotation] = value` -- only recognised when the
    `constants` compiler extension is active (see `_compiler/extensions.py`).
    Mirrors `Assign` exactly (same field shapes, renamed target -> name) so
    sema/ir_lower/codegen can reuse `Assign`'s handling for it near-verbatim
    once the const-only-once check has run; the only extra semantics are (a)
    an initializer is mandatory (no bare `const NAME` form) and (b) the name
    is permanently locked against every future rebinding form."""

    name: str
    value: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    annotation: object = None


@dataclass
class EnumDecl:
    """`enum NAME:` block of tagged compile-time int constants -- only
    recognised when the `enum` compiler extension is active. Each member is
    `(member_name, value)`; the parser resolves auto-increment (a member
    with no explicit `= N` takes the previous member's value + 1, starting
    at 0, exactly like CPython's own `enum.IntEnum` / `auto()` default
    numbering) so every member here already carries a concrete int, not a
    deferred expression. `Color.RED`-style reads fold to a plain IntLit at
    sema time carrying a synthetic "enum:<NAME>" type marker (for cross-
    enum type-mismatch checking) -- like ConstDecl, this produces zero new
    runtime representation, so codegen/ir_lower need no EnumDecl handling
    at all."""

    name: str
    members: list  # list[tuple[str, int]]
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class InterfaceDecl:
    """`interface NAME:` block of method-signature stubs -- only recognised
    when the `interface` compiler extension is active. Each stub is a real
    `FuncDef` (reusing the exact same node method bodies use), but its body
    must be exactly `pass` -- a signature-only declaration, never real code.
    `class X(interface=NAME):` (parsed in _parse_classdef, mirroring the
    `sealed` extension's `permits=` keyword-arg-in-base-list pattern) must
    implement every stub with a matching arity/return type or the class
    fails to compile. Purely a compile-time structural contract -- no
    runtime representation, no vtable; codegen/ir_lower need no
    InterfaceDecl handling at all."""

    name: str
    methods: list  # list[FuncDef], each body == [Pass(...)]
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class AugAssign:
    target: str
    op: str  # "+", "-", "*", "//", "%", "&", "|", "^", "<<", ">>"
    value: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class StarTarget:
    """`*name` as one target of a TupleAssign (PEP 3132 starred assignment),
    e.g. `a, *rest = xs` or `first, *mid, last = xs`. At most one per
    TupleAssign, and only valid when the single right-hand value is a
    `list`-typed expression -- `rest`/`mid`/etc. is bound to a fresh list
    holding the "leftover" middle elements (same element kind as the
    right-hand list)."""

    name: str
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class TupleAssign:
    """a, b, c = e1, e2, e3 -- evaluates every rhs first (into temporaries),
    then performs each store, so a, b = b, a works.

    Targets are `Name`, `Subscript`, `Attr`, or (single-iterable unpack form
    only) `StarTarget` expressions (e.g. `xs[0], xs[1] = xs[1], xs[0]`,
    `self.x, self.y = self.y, self.x`, or `a, *rest = xs`). Subscript/Attr
    targets are only allowed in the parallel form (one value per target) --
    the single-iterable unpack form requires plain names (and at most one
    `*rest`, no nested unpacking)."""

    targets: list["Expr"] = field(default_factory=list)
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
class ClosureBind:
    """Bind a name to a closure object wrapping a lifted inner function.

    Emitted by the parser when a nested `def` is found; replaces the old
    `Pass` placeholder.  `func_name` is the inner function's name; `free_vars`
    is the list of outer-scope variable names that the body references.
    `nonlocal_vars` is the subset of free_vars declared `nonlocal` in the inner
    body (these are captured by box/reference so the inner function can mutate them).
    Codegen allocates a list [CLOSURE_MAGIC, fn_ptr, var1, var2, ...].
    """

    func_name: str
    free_vars: list
    nonlocal_vars: list = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class Import:
    """import math  (the module name remains visible as a prefix).

    `import a.b.c as d` keeps the full dotted path in `module` (needed to
    resolve which real file/package this import points at) and the local
    name in `alias` (the name later `alias.x` lookups bind through); `alias`
    is None when there's no `as` clause, in which case lookups use `module`
    directly (or its first dotted segment).
    """

    module: str
    alias: "str | None" = None
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

    Each `except` clause may name one or more exception types (`except E:` or
    `except (E1, E2):`); an empty `types` list means a bare `except:` that
    matches anything. At runtime, the raised exception carries a type id and
    `_gen_try` checks each handler's declared types (and their builtin/user
    class ancestry) in source order, running the first one that matches. If
    none match, any `finally` runs and the exception propagates.

    The first handler stays in `handler` / `handler_types` / `bind_name` for
    back-compat with the single-handler codegen path. Any additional `except`
    clauses land in `extra_handlers` as `(types, bind_name, body)` tuples;
    `else_body` / `finally_body` hold the optional trailing clauses.
    """

    body: list["Stmt"]
    handler: list["Stmt"]
    bind_name: Optional[str] = None
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    handler_types: list[str] = field(default_factory=list)
    extra_handlers: list = field(
        default_factory=list
    )  # list[(types: list[str], bind_name, body)]
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


@dataclass
class YieldStmt:
    """`yield expr` — suspends a generator function and produces one value."""

    value: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


# ---- match/case patterns -----------------------------------------------------


@dataclass
class MatchValue:
    """A literal pattern: `case 1:`, `case "x":`, `case 3.0:`, `case None:`,
    `case True:`, `case -1:`. Matches when the subject equals `value`."""

    value: "Expr"
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class MatchCapture:
    """A capture pattern (`case x:`, binds the subject to `x`) or the
    wildcard pattern (`case _:`, when `name == "_"`, matches anything and
    binds nothing)."""

    name: str
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class MatchOr:
    """`case p1 | p2 | ...:` — matches if any alternative matches.

    Only literal alternatives (MatchValue, or nested MatchOr of literals) are
    supported: capture patterns inside an `or` pattern are rejected, since
    asmpython doesn't support the cross-alternative binding-consistency rules
    CPython enforces.
    """

    patterns: list["Pattern"] = field(default_factory=list)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class MatchSequence:
    """`case [p0, p1, ...]:` / `case (p0, p1, ...):` — matches a list/tuple of
    the right shape. At most one element may be a starred capture (`*rest` /
    `*_`); `star_index` is its position in `patterns`, or None if there's no
    star. A starred wildcard (`*_`) still occupies a slot in `patterns`
    (as `MatchCapture("_")`) but binds nothing."""

    patterns: list["Pattern"] = field(default_factory=list)
    star_index: Optional[int] = None
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class MatchClass:
    """`case ClassName(p0, p1, kw=pk, ...):` — matches an instance of
    `cls_name` (via isinstance/RTTI) whose `__match_args__`-named attributes
    (for positional sub-patterns) and keyword-named attributes match."""

    cls_name: str
    positional: list["Pattern"] = field(default_factory=list)
    kwargs: list = field(default_factory=list)  # list[(str, Pattern)]
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class MatchAs:
    """`case pattern as name:` — matches if `pattern` matches, and also binds
    the whole subject to `name`. `pattern` is None for a bare capture (`case
    name:`) handled instead by MatchCapture; MatchAs is only built for the
    explicit `as` form."""

    pattern: "Optional[Pattern]"
    name: str
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class MatchMapping:
    """`case {"key1": p1, "key2": p2, ...}:` — matches a dict with at least
    the listed keys present, checking each sub-pattern against the value.
    Only string literal keys are supported."""

    keys: list  # list[str]
    patterns: list  # list[Pattern]
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


Pattern = MatchValue | MatchCapture | MatchOr | MatchSequence | MatchClass | MatchAs | MatchMapping


@dataclass
class Match:
    """`match subject: case pattern [if guard]: body ...`.

    Sema rewrites this *in place* into an `If`/`elif`/.../`else` chain (see
    `Analyzer._check_stmt`), evaluating `subject` once into a synthetic temp
    so patterns/guards can reference it without re-evaluating side effects.
    """

    subject: "Expr"
    cases: list = field(default_factory=list)  # list[(Pattern, Optional[Expr], list[Stmt])]
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
    | ClosureBind
    | Import
    | FromImport
    | AttrAssign
    | Try
    | With
    | Raise
    | Global
    | Nonlocal
    | Del
    | Match
    | YieldStmt
)
# IndexAssign is also a Stmt but forward-referenced because Subscript is defined below.


# ---- Expressions ------------------------------------------------------------


@dataclass
class IntLit:
    value: int
    pos: SourcePos = field(default_factory=lambda: _NO_POS)
    # True for the literals `True`/`False` (both parse to IntLit(1)/IntLit(0)).
    # Lets print()/str()/f-strings render "True"/"False" instead of "1"/"0"
    # without a separate AST node or a distinct static type (bool is still
    # "int" everywhere else: arithmetic, comparisons, etc.).
    is_bool: bool = False
    # True for the literal `None` (parses to IntLit(0)). Lets print()/str()/
    # f-strings render "None" instead of "0"; `None` is still "int" (0)
    # everywhere else (comparisons, truthiness, etc.).
    is_none: bool = False


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
    # Filled in by sema when inferred_type == "int" and the name was last
    # assigned a bool-valued expression (see is_bool_expr). Lets print()/
    # str()/f-strings render "True"/"False" for `b = x > 1; print(b)`.
    is_bool: bool = False
    # Filled in by sema when inferred_type == "int" and the name was last
    # assigned `None` (see is_none_expr). Lets print()/str()/f-strings render
    # "None" for `x = None; print(x)`.
    is_none: bool = False


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
class NamedExpr:
    """`target := value` — assignment expression (the "walrus operator").

    Evaluates `value`, assigns it to `target` (same scope as a plain
    `Assign` to that name would use), and the whole expression evaluates to
    that value. `inferred_type` / `list_el_type` mirror `value`'s type and
    are filled in by sema.
    """

    target: str
    value: "Expr"
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
    # `**expr` unpacking (e.g. `Config(**data)`), extracted out of `args` by
    # sema before arity checks. Only resolvable against a callee with a
    # statically known parameter list (function/constructor/`__call__`) --
    # see DoubleStarred's docstring.
    dstar: "Expr | None" = None
    # `overload` extension: the mangled symbol name of the specific
    # @overload signature sema resolved this call to, or None for an
    # ordinary (non-overloaded) call. Set by SemaAnalyzer._resolve_overload,
    # consumed by codegen/ir_lower's call-emission sites so a resolved
    # overload call jumps to the right symbol instead of the bare (and
    # therefore ambiguous) function name.
    resolved_overload_symbol: "str | None" = None


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
    # Additional `for` clauses: `[x for a in A for b in B]`.
    # Parallel lists: extra_for_vars[i] is str (single name) or "" (multi-target),
    # extra_for_targets[i] is list of names when multi-unpack, else [],
    # extra_for_iters[i] is the iterable expr,
    # extra_for_conds[i] is the filter expr or None.
    extra_for_vars: list = field(default_factory=list)
    extra_for_targets: list = field(default_factory=list)
    extra_for_iters: list = field(default_factory=list)
    extra_for_conds: list = field(default_factory=list)


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
    # `overload` extension: mirrors A.Call.resolved_overload_symbol -- the
    # mangled symbol a resolved @overload method call dispatches to, or
    # None for an ordinary (non-overloaded) method call.
    resolved_overload_symbol: "str | None" = None


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
    codegen / iteration recover the right per-element kind.

    PEP 448 dict unpacking (`{**other, "k": v}`) is represented by a `None`
    entry in `keys` paired with the spread expression (which must be
    dict-typed) at the same index in `values`; codegen merges that dict's
    entries in via `_runtime_dict_update` in source order, so later entries
    (whether spreads or explicit keys) win on key conflicts."""

    keys: list["Expr | None"] = field(default_factory=list)
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

    value: "Expr" = field(default_factory=lambda: _NO_EXPR)
    pos: SourcePos = field(default_factory=lambda: _NO_POS)


@dataclass
class DoubleStarred:
    """`**expr` used as a call argument, e.g. `Config(**data)`.

    asmpython dicts are runtime hashtables with no statically-known key set,
    so this can't be expanded the way Starred is (indexing every tuple slot).
    Instead the parser leaves it attached to the enclosing Call as `Call.dstar`
    (see Call.dstar) rather than in `args`/`kwargs`, and sema expands it once
    it knows the callee's declared parameter names: each declared name not
    already bound positionally or by an explicit keyword becomes
    `name=expr["name"]`. Only valid where the callee has a statically known
    parameter list (a plain function, a constructor, or `__call__`) --
    unresolvable calls (e.g. through a bare function value) reject it.
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
    | DoubleStarred
    | NamedExpr
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
    if isinstance(e, Call):
        _ec: Call = e
        return _ec.inferred_type
    if isinstance(e, Name):
        _en: Name = e
        return _en.inferred_type
    if isinstance(e, MethodCall):
        _em: MethodCall = e
        return _em.inferred_type
    if isinstance(e, Attr):
        _ea: Attr = e
        return _ea.inferred_type
    if isinstance(e, IfExp):
        return e.inferred_type
    if isinstance(e, NamedExpr):
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
        # result "any"; set union/difference/intersection (|, -, &) is "set";
        # dict union (`d1 | d2`, PEP 584) is "dict". Honor those so they chain
        # (e.g. `(a | b) | c` for nested set/dict unions).
        if getattr(e, "inferred_type", None) in ("type", "any", "set", "dict", "list", "str"):
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


def is_bool_expr(e: Expr) -> bool:
    """True if `e` statically evaluates to a Python `bool` (True/False),
    even though its `expr_type` is "int" (bool is int-compatible everywhere:
    arithmetic, comparisons, indexing). Used by print()/str()/f-strings to
    render "True"/"False" instead of "1"/"0"."""
    if isinstance(e, IntLit):
        return e.is_bool
    if isinstance(e, Compare):
        return True
    if isinstance(e, UnaryOp):
        return e.op == "not"
    if isinstance(e, BoolOp):
        return is_bool_expr(e.left) and is_bool_expr(e.right)
    if isinstance(e, IfExp):
        return is_bool_expr(e.body) and is_bool_expr(e.orelse)
    if isinstance(e, Call):
        return e.func in ("bool", "isinstance", "any", "all") or getattr(e, "is_bool", False)
    if isinstance(e, MethodCall):
        return e.method in (
            "isdigit", "isalpha", "isalnum", "isspace",
            "isupper", "islower", "isdecimal", "isidentifier",
            "startswith", "endswith",
        ) or getattr(e, "is_bool", False)
    if isinstance(e, Name):
        return getattr(e, "is_bool", False)
    if isinstance(e, NamedExpr):
        return is_bool_expr(e.value)
    return False


def is_none_expr(e: Expr) -> bool:
    """True if `e` statically evaluates to `None` (parsed as IntLit(0)), even
    though its `expr_type` is "int". Used by print()/str()/f-strings to
    render "None" instead of "0"."""
    if isinstance(e, IntLit):
        return e.is_none
    if isinstance(e, Name):
        return getattr(e, "is_none", False)
    if isinstance(e, NamedExpr):
        return is_none_expr(e.value)
    return False


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


def parse_format_fields(fmt: str) -> list[tuple]:
    """Parse a str.format()-style format string into a flat list of pieces.

    Each piece is either ("lit", text, "", "") for literal text (with `{{`/
    `}}` collapsed to literal `{`/`}`), or ("arg", index_or_name, spec, conv)
    for a `{field}` replacement: `index_or_name` is an `int` for an
    auto-numbered (`{}`) or explicit-index (`{0}`) field, or a `str` for a
    named field (`{name}`); `spec` is the optional `:format-spec` (without
    the leading `:`); `conv` is the optional `!r`/`!s`/`!a` conversion
    (without the leading `!`). Auto-numbering only advances for `{}` fields,
    matching CPython. Shared by sema (validation) and codegen (lowering) so
    the two stay in sync.
    """
    pieces: list[tuple] = []
    buf = ""
    auto = 0
    i = 0
    n = len(fmt)
    while i < n:
        ch = fmt[i]
        if ch == "{":
            if i + 1 < n and fmt[i + 1] == "{":
                buf += "{"
                i += 2
                continue
            j = i + 1
            field = ""
            while j < n and fmt[j] != "}":
                field += fmt[j]
                j += 1
            name_conv, _, spec = field.partition(":")
            if "!" in name_conv:
                idx_part, conv = name_conv.split("!", 1)
            else:
                idx_part, conv = name_conv, ""
            idx_part = idx_part.strip()
            idx: object
            if idx_part == "":
                idx = auto
                auto += 1
            elif idx_part.isdigit():
                idx = int(idx_part)
            else:
                idx = idx_part
            if buf:
                pieces.append(("lit", buf, "", ""))
                buf = ""
            pieces.append(("arg", idx, spec, conv))
            i = j + 1
            continue
        if ch == "}":
            if i + 1 < n and fmt[i + 1] == "}":
                buf += "}"
                i += 2
                continue
            buf += "}"
            i += 1
            continue
        buf += ch
        i += 1
    if buf:
        pieces.append(("lit", buf, "", ""))
    return pieces
