"""Name resolution and type checking for the Python subset.

Analysis is one traversal producing two products: a symbol table saying what
each name refers to, and a type for every expression. They are computed
together because for an annotated subset they are mutually dependent -- the
type of `x` is the type of the symbol `x` resolves to -- and separating them
would mean two traversals passing a partially-filled table between them.

That is a real design choice with a real cost. A dynamically-typed frontend
could NOT do this: it would need resolution complete before inference starts.
The staging here is `analysis -> lowering`, and lowering may assume every
expression already has a type.

POISONING. When analysis cannot determine a type it yields `ERROR` and reports
once. Every operation on `ERROR` produces `ERROR` silently, so a single unknown
name generates one diagnostic rather than one per use. Lowering is never
reached if any error was reported, so it may assume no `ERROR` remains.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .modules import importable, member, resolve

from ...diagnostics import DiagnosticSink, SourceFile, Span, error
from ...ir import types as T
MODULE_DUNDERS = {"__name__": "__main__", "__doc__": None, "__package__": ""}

#: `object.<dunder>` -- the DEFAULT implementations, and the runtime call each
#: one is. A class that overrides a dunder reaches its default this way, which
#: is the only way out of the recursion `__getattribute__` would otherwise be:
#:
#:     def __getattribute__(self, name):
#:         log.append(name)
#:         return object.__getattribute__(self, name)
#:
#: `object` is not a value here -- there is no type object to hold these -- so
#: the attribute is resolved at the call site, by name.
OBJECT_DEFAULTS = {
    "__getattribute__": ("apy_default_getattr", 2),
    "__setattr__": ("apy_default_setattr", 3),
    "__delattr__": ("apy_default_delattr", 2),
    "__repr__": ("apy_default_repr", 1),
    "__str__": ("apy_default_repr", 1),
    "__eq__": ("apy_default_eq", 2),
    "__ne__": ("apy_default_eq", 2),
    "__hash__": ("apy_default_hash", 1),
    "__init__": ("apy_default_init", 1),
}

#: Names that are VALUES without being assigned. `Ellipsis` is the spelling
#: `...` also has, and both are the one singleton cell.
_SINGLETON_NAMES = frozenset({"Ellipsis"})

#: The frontend's own view of a type. Distinct from `ir.types` on purpose:
#: `bool` and `int` are different here and both lower to integers, and ERROR
#: has no IR counterpart at all.
@dataclass(frozen=True, slots=True)
class SemType:
    name: str

    def __str__(self) -> str:
        return self.name

    @property
    def is_numeric(self) -> bool:
        return self.name in ("int", "float", "bool")

    @property
    def is_error(self) -> bool:
        return self.name == "<error>"


INT = SemType("int")
FLOAT = SemType("float")
BOOL = SemType("bool")
NONE = SemType("None")
ERROR = SemType("<error>")

#: The type of a value whose type is not known until it runs -- which in
#: Python is most of them. A DYNAMIC function (see `FunctionInfo.dynamic`)
#: gives every expression this type and every operation becomes a call into
#: the object runtime, where the value carries its own kind.
#:
#: This is not "unknown, guess later". It is a real type with a real
#: representation, and that distinction is the point: the compiler this
#: replaces used its `int` as a stand-in for "no idea", so a value's
#: representation followed the slot it was stored in rather than the value,
#: and one root cause surfaced as a dozen unrelated-looking bugs.
OBJ = SemType("object")

BY_NAME = {"int": INT, "float": FLOAT, "bool": BOOL, "None": NONE,
           "object": OBJ}

#: What the module's top-level statements are called internally. Not a legal
#: Python identifier, so it can never collide with a user function -- including
#: one actually named `main`, which stays an ordinary function that the entry
#: may call. Lowering renames it to `main`, which is what the backends and the
#: C runtime agree the entry point is called.
ENTRY_NAME = "<module>"


#: Builtins a dynamic function may call, and their arity (None = any). Small
#: on purpose: every entry is a thing the object runtime actually implements,
#: and a name accepted here that lowering cannot emit is a crash rather than a
#: diagnostic.
_DYN_BUILTINS = {
    "print": None, "int": None, "float": 1, "bool": 1, "str": 1, "repr": 1,
    "len": 1, "type": 1, "list": 1, "tuple": 1,
    "sorted": 1, "min": None, "max": None, "sum": None, "reversed": 1,
    "enumerate": None, "zip": None, "range": None, "abs": 1, "round": None,
    "isinstance": 2, "set": None, "frozenset": None, "complex": None,
    "ord": 1, "chr": 1, "ascii": 1, "bin": 1, "hex": 1, "oct": 1, "hash": 1,
    "callable": 1, "all": 1, "any": 1, "divmod": 2, "pow": None,
    "hasattr": 2, "getattr": None, "iter": None, "next": None,
    "dict": None, "bytes": None,
    "issubclass": 2, "vars": 1, "setattr": 3, "delattr": 2,
    "map": 2, "filter": 2, "format": None,
}

#: The keyword arguments each builtin accepts. A builtin not listed here takes
#: none, and naming one it does not have is reported where CPython reports it
#: -- at the call, by name, rather than as a wrong argument count.
_BUILTIN_KEYWORDS = {
    "sorted": ("key", "reverse"),
    "min": ("key", "default"),
    "max": ("key", "default"),
    "zip": ("strict",),
    "enumerate": ("start",),
    "print": ("sep", "end", "file", "flush"),
}

#: Builtin exception names. Calling one CONSTRUCTS an exception value, and
#: naming one in an `except` clause matches against the hierarchy in
#: `link/objects.py`. Kept in the frontend as a plain set because the frontend
#: only has to recognise the name -- the runtime owns what it means.
#: Builtins that may be used as a VALUE, not only called. Each becomes a
#: one-argument function that calls it -- `sorted(xs, key=len)` needs `len` to
#: BE something, not merely to be callable.
#:
#: Exactly one argument. A variadic builtin has no single thunk shape, and a
#: two-argument one would need its arity carried alongside; neither is needed
#: by anything that passes a builtin as a value, which is nearly always a key
#: function.
#: NOT `type`. `type(x)` currently yields the type's NAME rather than a type
#: object, and the frontend hides that by special-casing `type(x).__name__`.
#: A thunk has no such special case, so `apply(type, 1).__name__` would fail
#: on a str -- refusing is better than being wrong in a new place, and the
#: entry goes back the moment `type` returns a real object.
_VALUE_BUILTINS = frozenset({
    "repr", "str", "len", "int", "float", "bool", "abs", "hash",
    "list", "tuple", "set", "frozenset", "sorted", "reversed", "sum", "min",
    "max", "ascii", "ord", "chr", "bin", "hex", "oct",
    # These three take any number of arguments. Their thunk declares `*rest`
    # and hands the tuple to one runtime call, which is why they can be
    # values even though the one-argument thunk shape does not fit them.
    "print", "dict", "bytes",
})

#: Module attributes every Python file has without writing them, and what
#: they hold here. A compiled program IS the script being run, so `__name__`
#: is `"__main__"` -- which is what makes the `if __name__ == "__main__":`
#: guard at the bottom of a script take its branch, and that guard is in
#: enough real programs that not having it meant refusing them.
MODULE_DUNDERS = {"__name__": "__main__", "__doc__": None, "__package__": ""}
_MODULE_DUNDERS = frozenset(MODULE_DUNDERS)

#: Builtin type names, and the constructors reachable through them --
#: `dict.fromkeys`, `int.from_bytes`. Not unbound methods: there is no
#: receiver of that type to be the first argument.
_BUILTIN_TYPE_NAMES = frozenset({"dict", "int", "bytes", "str", "list",
                                 "tuple", "set", "frozenset", "float"})
_TYPE_STATIC_NAMES = frozenset({"fromkeys", "from_bytes", "fromhex"})

_EXC_NAMES = frozenset({
    "BaseException", "Exception", "SystemExit", "KeyboardInterrupt",
    "GeneratorExit", "ArithmeticError", "ZeroDivisionError", "OverflowError",
    "FloatingPointError", "LookupError", "IndexError", "KeyError",
    "NameError", "UnboundLocalError", "AttributeError", "TypeError",
    "ValueError", "UnicodeError", "RuntimeError", "NotImplementedError",
    "RecursionError", "AssertionError", "ImportError", "ModuleNotFoundError",
    "OSError", "FileNotFoundError", "StopIteration", "StopAsyncIteration",
    "MemoryError", "EOFError",
})


def _import_names(node) -> list:
    """The names an `import` statement binds.

    `import a.b` binds `a`; `import a.b as c` binds `c`; `from m import x as y`
    binds `y`. Written out because the three forms bind different things and
    reading `alias.name` for all of them binds the wrong one twice.
    """
    out = []
    for alias in node.names:
        if isinstance(node, ast.ImportFrom):
            out.append(alias.asname or alias.name)
        else:
            out.append(alias.asname or alias.name.split(".")[0])
    return out


def _has_yield(node) -> bool:
    """Does this `def`'s OWN body contain a `yield`?

    Not a nested one's: a `def` inside a generator is an ordinary function
    unless it yields itself, and walking into it would make every enclosing
    function a generator. A lambda cannot contain `yield` at all, so it is
    skipped for free by the same rule.
    """
    return any(_owns_yield(stmt) for stmt in node.body)


def _owns_yield(node) -> bool:
    """`yield` anywhere under `node`, not entering a nested function."""
    if isinstance(node, (ast.Yield, ast.YieldFrom)):
        return True
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        return False
    return any(_owns_yield(child) for child in ast.iter_child_nodes(node))


def _target_names(node) -> list:
    """Every plain name in an assignment or loop target.

    Nested targets (`for a, (b, c) in ...`) flatten here and are rejected by
    lowering, which knows the arity it is unpacking into; a name list is all
    analysis needs to declare them.
    """
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Starred):
        # `a, *rest = xs`. The star is punctuation on the target, not a target
        # of its own: `rest` is an ordinary name that happens to be bound to a
        # list. Missing it here left `rest` undeclared and reported the
        # program's own variable as an undefined name.
        return _target_names(node.value)
    if isinstance(node, (ast.Tuple, ast.List)):
        out = []
        for e in node.elts:
            out.extend(_target_names(e))
        return out
    return []


def _declared_global(body: list) -> set:
    """Names a function body declares `global`.

    Walks the whole body including nested statements, because `global` is
    function-wide wherever it appears -- Python even allows it after the first
    use, which is a wart but a real one.
    """
    found: set = set()
    for node in body:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Global):
                found.update(sub.names)
    return found


def _handler_names(node) -> list:
    """The exception names an `except` clause lists. `except (A, B):` is a
    tuple; `except A:` is one name."""
    if isinstance(node, ast.Tuple):
        return [getattr(e, "id", "") for e in node.elts]
    return [getattr(node, "id", "")]


#: The literal types the object runtime has a kind for. `Ellipsis` is not one
#: and neither is `bytes` or `complex`; a literal of any other type is refused
#: rather than passed to a lowering that has no case for it.
_CONSTANT_KINDS = frozenset({bool, int, float, complex, str, bytes,
                             type(None)})


def _is_docstring(node) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) \
        and isinstance(node.value.value, str)

#: How each maps into the IR. `bool` becomes i1 so a comparison result and a
#: `bool` variable are the same thing at the machine level.
TO_IR = {INT: T.I64, FLOAT: T.F64, BOOL: T.I1, NONE: T.VOID, OBJ: T.PTR}


#: Where a name's value actually lives, which is not the same question as what
#: it is called. Three answers, and the difference between them is the whole
#: of closure support:
#:
#:   LOCAL  a register. What every name was before nested `def` existed.
#:   CELL   a register holding a runtime CELL, because some inner function
#:          captures this name. Reads and writes go through the box.
#:   FREE   a cell that arrived through the closure environment.
#:
#: A captured variable has to be a box rather than a copy because
#: `functions/closure-cell-is-shared` requires two closures over one name to
#: see each other's writes. Copying the value at capture time passes every
#: case with a single closure and fails that one, which is the worst possible
#: split: the feature looks finished.
LOCAL, CELL, FREE = "local", "cell", "free"


@dataclass(slots=True)
class Symbol:
    name: str
    type: SemType
    span: Span
    #: Set once lowering allocates a register for it.
    register: int | None = None
    is_param: bool = False
    used: bool = False
    storage: str = LOCAL
    #: Position in the owning function's cell list (CELL) or in the
    #: environment a closure was built with (FREE). The two orders must agree:
    #: the maker writes cell `i` and the callee reads env slot `i`.
    index: int = -1


@dataclass(slots=True)
class FunctionInfo:
    """Everything lowering needs about one function, after analysis."""

    #: The `def` this came from -- or, for the module entry, the `ast.Module`
    #: holding the top-level statements. Both answer to `.body`, which is all
    #: analysis and lowering ever ask of it.
    node: ast.FunctionDef | ast.Module
    name: str
    params: list[Symbol]
    ret: SemType
    locals: dict[str, Symbol] = field(default_factory=dict)
    #: Expression node id -> its type. Keyed by id() because ast nodes are not
    #: hashable in a way that survives equality, and analysis and lowering walk
    #: the same tree objects.
    expr_types: dict[int, SemType] = field(default_factory=dict)
    #: True when this function's values are runtime objects rather than
    #: machine words. Set for the module's top-level statements, which have no
    #: annotations at all, and for any function with an unannotated parameter
    #: -- ordinary Python, in other words. A fully annotated function keeps the
    #: static path, which is what every already-written program uses.
    dynamic: bool = False
    #: Names this function reads from MODULE scope rather than from its own
    #: frame. Python's rule: a name assigned anywhere in a function body is
    #: local for the whole body, and every other name falls through to the
    #: module. Recorded per function because the answer depends on the body,
    #: not on the name.
    module_reads: set = field(default_factory=set)
    #: Names this function ASSIGNS at module scope -- those it declared
    #: `global`. Without the declaration an assignment makes a local, even if a
    #: module-level name of the same spelling exists.
    module_writes: set = field(default_factory=set)
    #: Default-value expressions, for the LAST `len(defaults)` parameters.
    #: Evaluated ONCE, where the `def` runs, not at each call -- which is
    #: observable: `def f(xs=[])` shares one list across calls, and the suite
    #: checks exactly that.
    defaults: list = field(default_factory=list)
    #: The `*rest` parameter's name, or None. It is not in `params`: it takes
    #: no argument position and the arity check must not count it.
    vararg: str | None = None
    #: True when the body contains a `yield`. Such a function does not run
    #: when called -- it builds a generator -- so it is lowered as TWO IR
    #: functions and every local lives in the generator object. See
    #: `_dyn_generator`.
    is_generator: bool = False
    #: Local name -> its slot in the generator's frame. Empty for an ordinary
    #: function, whose locals are registers.
    slots: dict = field(default_factory=dict)
    #: Locals whose assignment could not be PROVED to happen before every
    #: read. On the dynamic path they are checked at run time and raise
    #: `UnboundLocalError`, exactly as CPython does; the static path rejects
    #: them outright, having no representation for "unset".
    maybe_unbound: set = field(default_factory=set)
    #: How many LEADING parameters are positional-only -- the ones before a
    #: `/` in the signature. They take an argument position like any other and
    #: differ in one way: a keyword cannot reach them, so their names are not
    #: recorded on the function value and `f(a=1)` against `def f(a, /)` is an
    #: unexpected keyword rather than a second value for `a`.
    posonly: int = 0
    #: How many TRAILING parameters are keyword-only -- the ones after a `*`.
    #: The mirror image: a position cannot reach them, so positional filling
    #: stops short of them and only a name arrives.
    kwonly: int = 0
    #: The `**kw` parameter's name, or None. Like `vararg` it is not in
    #: `params`: it takes no argument position, and it is bound to a dict of
    #: whatever keywords the call could not place -- empty when there were
    #: none, because `def f(**kw)` called as `f()` binds `{}` and not nothing.
    kwarg: str | None = None
    #: AugAssign node id -> the equivalent BinOp, built once by analysis and
    #: reused by lowering. `x += 1` is `x = x + 1` and both passes need that
    #: tree; building it twice meant only one of them checked it, and
    #: `x **= n` type-checked as ordinary arithmetic before hitting a
    #: lowering table that requires a literal exponent.
    aug_nodes: dict[int, ast.BinOp] = field(default_factory=dict)

    #: The key of the function this one is written inside, or None at module
    #: level. A METHOD's parent is the function containing its `class`, not the
    #: class -- a class body is not in the lookup chain, so a method cannot see
    #: names the class body bound. Getting that wrong makes a method resolve a
    #: sibling method's name as a free variable, which then captures nothing.
    parent: str | None = None
    #: Dotted name for diagnostics: `outer.<locals>.inner`, `Point.move`.
    qualname: str = ""
    #: Locals some inner function captures, in CELL ORDER. Each lives in a
    #: runtime cell instead of a plain register.
    cellvars: list = field(default_factory=list)
    #: Names captured FROM an enclosing function, in ENV ORDER. The maker
    #: writes cell `i` and this function reads env slot `i`; the two orders are
    #: one agreement and both sides read this list.
    freevars: list = field(default_factory=list)
    #: The class this is a method of, or None. What `super()` needs: the class
    #: the method was DEFINED in, which is not `type(self)`.
    owner: str | None = None
    #: True for a function that is a VALUE bound where its `def` runs -- every
    #: nested `def` and every method. A module-level `def` is one too, but it
    #: also keeps its direct-call path.
    is_value: bool = False

    @property
    def signature(self) -> tuple[list[SemType], SemType]:
        return [p.type for p in self.params], self.ret

    @property
    def takes_env(self) -> bool:
        """Whether the compiled function's first parameter is the closure env.

        EVERY dynamic function has one, including those that capture nothing,
        and the module entry has none. Uniformity rather than a per-function
        flag: `apy_call` reaches a function it cannot see the definition of, so
        a convention that varied would have to be recorded in the value and
        checked on every call. An unused first parameter costs one register.
        """
        return self.dynamic and self.name != ENTRY_NAME


@dataclass(slots=True)
class ClassInfo:
    """One `class` statement.

    Not a scope in the closure sense -- see `FunctionInfo.parent`. A class body
    is executed where it is written and its bindings go into the type object,
    so what analysis needs from it is an ordered list of what to bind.
    """

    node: ast.ClassDef
    name: str
    qualname: str
    #: The base's name as written, or None. SINGLE INHERITANCE: a second base
    #: is refused rather than linearised, because a wrong MRO surfaces as one
    #: method resolving to the wrong body and nothing looks broken.
    base: str | None = None
    #: Keys into `Analyzer.functions`, in definition order.
    methods: list = field(default_factory=list)
    #: (name, value expression) per class-level assignment, in order.
    attrs: list = field(default_factory=list)
    #: The key of the function whose body this `class` statement runs in.
    scope: str = ENTRY_NAME
    #: True for `class MyError(ValueError):` -- a name in the exception
    #: hierarchy rather than a type object. See `_class_statement`.
    is_exception: bool = False


def int_literal(node) -> int | None:
    """The value of `node` if it is an integer literal, else None.

    `-1` is not `Constant(-1)` -- Python parses it as `UnaryOp(USub,
    Constant(1))`, and code that only checks for Constant silently treats a
    negative literal as "not a literal". That mistake made `range(5, 0, -1)`
    compile to an ascending loop that ran zero times.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, int) \
            and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.operand, ast.Constant) \
            and isinstance(node.operand.value, int) \
            and not isinstance(node.operand.value, bool):
        if isinstance(node.op, ast.USub):
            return -node.operand.value
        if isinstance(node.op, ast.UAdd):
            return node.operand.value
    return None


def span_of(source: SourceFile, node) -> Span:
    """The source range an AST node occupies.

    Shared by analysis and lowering so that a diagnostic and the instruction it
    describes point at exactly the same bytes -- if they computed it
    separately they would drift, and an optimiser's message would land one
    column off the error the type checker reported for the same expression.
    """
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return source.span(0, 0)
    starts = source.line_starts
    begin = starts[lineno - 1] + getattr(node, "col_offset", 0)
    end_line = getattr(node, "end_lineno", lineno)
    end_col = getattr(node, "end_col_offset", None)
    if end_col is None or end_line > len(starts):
        return source.span(begin, begin + 1)
    return source.span(begin, starts[end_line - 1] + end_col)


# ── scopes ──────────────────────────────────────────────────────────────────
# Everything down to `Analyzer` answers ONE question: for each name a function
# mentions, where does its storage live. Before nested `def` there was nothing
# to compute -- every name was a register in the current frame, or a module
# global. A closure makes it three answers and makes them depend on the whole
# nesting, so it becomes a pass of its own that runs before any body is
# checked. It has to run first because a closure may capture a name assigned
# LATER in the enclosing function (`functions/inner-function-sees-later-
# binding`), so the decision cannot be made as the body is walked.


class _Scope:
    """One binding region: the module, a function body, or a class body."""

    __slots__ = ("key", "kind", "parent", "bound", "globals", "nonlocals",
                 "reads", "cells", "frees", "node", "lambdas")

    def __init__(self, key: str, kind: str, parent, node=None) -> None:
        self.key = key
        self.kind = kind                # "module" | "function" | "class"
        self.parent = parent
        self.node = node
        self.bound: set = set()
        self.globals: set = set()
        self.nonlocals: set = set()
        self.reads: set = set()
        #: Resolved by `_resolve_closures`. ORDERED, because the function that
        #: builds a closure writes cell `i` and the closure reads env slot `i`;
        #: the two sides index into these lists and nothing else keeps them in
        #: step.
        self.cells: list = []
        self.frees: list = []
        #: Lambdas written in this scope's expressions, awaiting a scope of
        #: their own. Recorded rather than registered on the spot because
        #: `_expr_names` is a free function with no analyzer to register with,
        #: and it is called from a dozen places -- putting the hook in one of
        #: them and not the others is how a lambda in a `while` test would
        #: silently get no scope.
        self.lambdas: list = []

    @property
    def locals(self) -> set:
        """Names whose storage is this scope's own frame."""
        return self.bound - self.globals - self.nonlocals

    def enclosing_function(self):
        """The nearest enclosing FUNCTION scope, skipping class bodies.

        A class body is NOT in the lookup chain. A method that reads `n` does
        not see `n = 1` from its own class body -- it sees the enclosing
        function's `n`, or the module's. Walking the parent chain without this
        skip makes a method capture a class attribute CPython never gives it,
        and the symptom is a method that reads a stale value rather than one
        that fails.
        """
        s = self.parent
        while s is not None and s.kind == "class":
            s = s.parent
        return s


def lambda_def(node: ast.Lambda) -> ast.FunctionDef:
    """The `def` a lambda is, built once and cached on the node.

    Cached because analysis and lowering must see the SAME synthetic node:
    scope registration keys off its identity, and building a second one would
    leave lowering looking for a function nobody registered.
    """
    made = getattr(node, "_asmpython_def", None)
    if made is not None:
        return made
    made = ast.FunctionDef(
        name="<lambda>", args=node.args,
        body=[ast.Return(value=node.body)], decorator_list=[], returns=None,
        type_params=[])
    ast.copy_location(made, node)
    ast.copy_location(made.body[0], node)
    node._asmpython_def = made
    return made


def _lambdas_in(node):
    """Every lambda in an expression, outermost first, without descending into
    one that is already nested inside another -- that inner one belongs to the
    outer one's scope and is collected when the outer body is."""
    found = []

    def walk(sub):
        if sub is None:
            return
        for child in ast.iter_child_nodes(sub):
            if isinstance(child, ast.Lambda):
                found.append(child)
            else:
                walk(child)

    if isinstance(node, ast.Lambda):
        return [node]
    walk(node)
    return found


def _inside_lambda(target, root) -> bool:
    """Whether `target` sits inside a lambda somewhere under `root`."""
    for lam in _lambdas_in(root):
        if target is lam:
            return True
        for sub in ast.walk(lam):
            if sub is target:
                return True
    return False


def _expr_names(node, scope: _Scope) -> None:
    """Record what an expression reads and what it binds.

    A Store-context name inside an EXPRESSION is a comprehension target or a
    walrus. Comprehensions do not get a scope of their own here -- a stated
    divergence, `[i for i in xs]` leaves `i` behind -- so their targets bind in
    the enclosing scope, which is exactly what recording them here does.
    """
    if node is None:
        return
    for lam in _lambdas_in(node):
        scope.lambdas.append(lam)
    for sub in ast.walk(node):
        if _inside_lambda(sub, node):
            # A lambda's PARAMETERS and body are its own, not the enclosing
            # scope's. Walking into one bound them here, so `lambda x: x` made
            # `x` a local of whatever contained it.
            continue
        if isinstance(sub, ast.Name):
            if isinstance(sub.ctx, ast.Load):
                scope.reads.add(sub.id)
            else:
                scope.bound.add(sub.id)


class Analyzer:
    """Resolves names and assigns a type to every expression."""

    def __init__(self, source: SourceFile, sink: DiagnosticSink) -> None:
        self.source = source
        self.sink = sink
        self.functions: dict[str, FunctionInfo] = {}
        self.current: FunctionInfo | None = None
        #: `break`/`continue` are only meaningful inside a loop, and Python
        #: makes that a syntax error. ast.parse does NOT -- it happily parses
        #: a bare `break` in a function body -- so it is checked here.
        self.loop_depth = 0
        #: Every name the module's top level binds. Set by `run`.
        self.module_names: set = set()
        #: One `ClassInfo` per `class` statement, keyed the way `functions` is.
        self.classes: dict = {}
        #: User exception class name -> its base's name. Separate from
        #: `classes` because these are NOT type objects: `raise MyError(x)`
        #: builds an exception cell, exactly as `raise ValueError(x)` does.
        self.exc_classes: dict = {}
        #: The scope tree, flat, plus an index from a function's key to its
        #: scope. Built by `_collect_scopes` before any body is analysed.
        self.scopes: list = []
        self.scope_of: dict = {}
        #: ClassDef node id -> its key in `classes`. Keyed by id() for the same
        #: reason `expr_types` is: analysis and lowering walk the same objects.
        self.class_of_node: dict = {}
        #: FunctionDef node id -> its key. A nested `def` is a STATEMENT that
        #: builds a value, so lowering needs to get from the statement back to
        #: the function analysis registered for it.
        self.def_of_node: dict = {}
        #: Keys of every `def` below module level, in registration order --
        #: which is source order, so a method is checked after the class that
        #: owns it exists.
        self._nested_keys: list = []
        #: The names local to the function being checked, or None outside one
        #: and for the module entry (whose names ARE the globals).
        self._function_locals: set | None = None

    # ── entry point ─────────────────────────────────────────────────────────
    def run(self, tree: ast.Module) -> dict[str, FunctionInfo]:
        defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        body = [n for n in tree.body
                if not isinstance(n, ast.FunctionDef) and not _is_docstring(n)]

        # The module's own names, before anything else: a function body may
        # read one, and which names exist at module scope is the question that
        # decides whether an unresolved name is a global or a mistake.
        self.module_names = self._module_names(
            [n for n in tree.body if not isinstance(n, ast.FunctionDef)])

        # Signatures first, so a function may call one defined later.
        for node in defs:
            if node.name in self.functions:
                self._error("E0002", f"function {node.name!r} is defined twice",
                            node)
            self.functions[node.name] = self._signature(node)

        # A module-level `def` or `class` BINDS A MODULE NAME, and now that
        # both are values something may read it rather than call it. Added
        # only when there is an entry to initialise the storage: a module of
        # pure definitions runs `main` directly and never executes a `def`
        # statement, so a global there would be permanently unset.
        if any(not isinstance(n, ast.FunctionDef) and not _is_docstring(n)
               for n in tree.body):
            self.module_names |= {
                n.name for n in tree.body
                if isinstance(n, ast.ClassDef)
                or (isinstance(n, ast.FunctionDef)
                    and self.functions[n.name].dynamic)}

        # Scopes next, and before any body: a closure may capture a name the
        # enclosing function assigns LATER than the `def`, so where a name
        # lives cannot be decided while walking the body that mentions it.
        self._collect_scopes(tree, defs)
        self._resolve_closures()

        entry = self._entry(body)
        for node in defs:
            self._body(self.functions[node.name])
        for key in self._nested_keys:
            self._body(self.functions[key])
        if entry is not None:
            self._body(entry)
            # A module-level `def`'s DECORATORS run at module level, not in
            # the function they decorate, so they are checked here -- inside
            # the entry's scope and after its body, which is what makes an
            # unknown one a NAME ERROR rather than an unbound global that
            # reaches lowering and fails the IR verifier.
            self.current = entry
            self.dynamic = True
            for node in defs:
                for deco in node.decorator_list:
                    self._expr(deco)
        return self.functions

    # ── scope collection ────────────────────────────────────────────────────
    def _collect_scopes(self, tree: ast.Module, defs: list) -> None:
        """Build the scope tree and register every nested `def` and `class`.

        Registration happens HERE rather than as each body is walked, so that a
        name can be resolved against the whole nesting before any of it is
        checked. The module scope is created even though module names are
        handled by `module_names` -- it terminates the walk upward, and a
        function scope whose search reaches it has found a global.
        """
        module = self._new_scope(ENTRY_NAME, "module", None, tree)
        module.bound |= set(self.module_names)
        for node in tree.body:
            self._collect(node, module,
                          qual="" if not isinstance(node, ast.FunctionDef)
                          else node.name)
        # Lambdas last, and by INDEX over a list that grows: registering one
        # collects its body, which may contain more. Draining after the whole
        # statement walk rather than at each `_expr_names` call site is what
        # makes a lambda in a `while` test or an `except` clause get a scope
        # like any other -- there are a dozen such sites and hooking some of
        # them is how one silently would not.
        i = 0
        while i < len(self.scopes):
            scope = self.scopes[i]
            i += 1
            j = 0
            while j < len(scope.lambdas):
                self._register_lambda(scope.lambdas[j], scope)
                j += 1

    def _new_scope(self, key: str, kind: str, parent, node=None) -> _Scope:
        scope = _Scope(key, kind, parent, node)
        self.scopes.append(scope)
        self.scope_of[key] = scope
        return scope

    def _register_lambda(self, lam, scope: _Scope) -> None:
        """Give one lambda a scope of its own.

        A lambda is a nested function that happens to be written inline, so it
        gets exactly what a nested `def` gets -- and by going through the same
        `_register_nested`, it gets closures and cells for free rather than a
        second mechanism that would have to grow them separately.
        """
        made = lambda_def(lam)
        key = self._register_nested(made, scope, "<lambda>")
        self.def_of_node[id(lam)] = key
        child = self._new_scope(key, "function", scope, made)
        for a in made.args.args:
            child.bound.add(a.arg)
        if made.args.vararg:
            child.bound.add(made.args.vararg.arg)
        for stmt in made.body:
            self._collect(stmt, child)

    def _collect(self, node, scope: _Scope, qual: str = "") -> None:
        """Record one statement's bindings and reads into `scope`.

        A `def` or `class` gets a child scope and is NOT descended into here;
        everything else contributes to this one. Written out per statement kind
        rather than over `ast.iter_fields`, because the two questions -- which
        sub-node is a nested SCOPE and which is an expression evaluated HERE --
        have different answers per statement and a generic walk gets the
        `def`'s default arguments wrong: they are evaluated where the `def` is
        written, not where the function runs.
        """
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope.bound.add(node.name)
            for d in node.args.defaults:
                _expr_names(d, scope)
            for dec in node.decorator_list:
                _expr_names(dec, scope)
            key = self._register_nested(node, scope, qual or node.name)
            child = self._new_scope(key, "function", scope, node)
            for a in node.args.args:
                child.bound.add(a.arg)
            if node.args.vararg:
                child.bound.add(node.args.vararg.arg)
            if node.args.kwarg:
                child.bound.add(node.args.kwarg.arg)
            for s in node.body:
                self._collect(s, child)
            return
        if isinstance(node, ast.ClassDef):
            scope.bound.add(node.name)
            for b in node.bases:
                _expr_names(b, scope)
            key = self._register_class(node, scope, qual or node.name)
            child = self._new_scope(key, "class", scope, node)
            for s in node.body:
                self._collect(s, child)
            return

        match node:
            case ast.Global(names=names):
                scope.globals.update(names)
            case ast.Nonlocal(names=names):
                scope.nonlocals.update(names)
            # A TARGET goes through `_expr_names` as well as `_target_names`,
            # and the pair is not redundant. In `x.attr = v` the base `x` is a
            # LOAD inside a Store-context Attribute, so it is a read of x and
            # binds nothing; `x[i] = v` reads both x and i. `_target_names`
            # answers the plain-name half and `_expr_names` the rest, and
            # neither alone covers `a, b[i] = ...`.
            case ast.Assign(targets=targets, value=value):
                _expr_names(value, scope)
                for t in targets:
                    scope.bound.update(_target_names(t))
                    _expr_names(t, scope)
            case ast.AnnAssign(target=target, value=value):
                _expr_names(value, scope)
                scope.bound.update(_target_names(target))
                _expr_names(target, scope)
            case ast.AugAssign(target=target, value=value):
                _expr_names(value, scope)
                # `x += 1` READS x as well as binding it.
                scope.reads.update(_target_names(target))
                scope.bound.update(_target_names(target))
                _expr_names(target, scope)
            case ast.For(target=target, iter=it, body=b, orelse=o):
                _expr_names(it, scope)
                scope.bound.update(_target_names(target))
                _expr_names(target, scope)
                for s in list(b) + list(o):
                    self._collect(s, scope)
            case ast.While(test=test, body=b, orelse=o) \
                    | ast.If(test=test, body=b, orelse=o):
                _expr_names(test, scope)
                for s in list(b) + list(o):
                    self._collect(s, scope)
            case ast.Try():
                for s in list(node.body) + list(node.orelse) \
                        + list(node.finalbody):
                    self._collect(s, scope)
                for h in node.handlers:
                    _expr_names(h.type, scope)
                    if h.name:
                        scope.bound.add(h.name)
                    for s in h.body:
                        self._collect(s, scope)
            case _:
                for sub in ast.iter_child_nodes(node):
                    if isinstance(sub, ast.expr):
                        _expr_names(sub, scope)
                    elif isinstance(sub, ast.stmt):
                        self._collect(sub, scope)

    def _register_nested(self, node, scope: _Scope, qual: str) -> str:
        """Give a `def` below module level its own FunctionInfo."""
        if scope.kind == "module" and node.name in self.functions \
                and self.functions[node.name].node is node:
            # A module-level `def`, already registered by `run`. Its key is its
            # bare name, which is what every existing call site looks up.
            info = self.functions[node.name]
            info.qualname = node.name
            info.is_value = info.dynamic
            self.def_of_node[id(node)] = node.name
            return node.name

        owner = scope.key if scope.kind == "class" else None
        parent = scope
        while parent is not None and parent.kind == "class":
            parent = parent.parent
        qualname = (f"{scope.key}.{node.name}" if scope.kind == "class"
                    else f"{scope.key}.<locals>.{node.name}")
        key = qualname
        n = 1
        while key in self.functions:
            n += 1
            key = f"{qualname}#{n}"      # two `def f` in one body: both exist
        info = self._signature(node)
        info.name = key
        info.qualname = qualname
        info.parent = parent.key if parent is not None else None
        info.owner = owner
        info.is_value = True
        if not info.dynamic:
            # A nested function is a VALUE, and a value is an `apy_value`.
            # There is no way to hand a machine-word function to `apy_call`,
            # so annotations do not buy the static path here.
            info.dynamic = True
            info.ret = OBJ
            for p in info.params:
                p.type = OBJ
        self.functions[key] = info
        self.def_of_node[id(node)] = key
        self._nested_keys.append(key)
        if owner is not None:
            self.classes[owner].methods.append(key)
        return key

    def _register_class(self, node: ast.ClassDef, scope: _Scope,
                        qual: str) -> str:
        qualname = (node.name if scope.kind == "module"
                    else f"{scope.key}.{node.name}")
        key = qualname
        n = 1
        while key in self.classes:
            n += 1
            key = f"{qualname}#{n}"
        base = None
        if len(node.bases) > 1:
            self._error("E0071", "only single inheritance is supported", node)
        elif node.bases:
            if isinstance(node.bases[0], ast.Name):
                # `class C(object):` is `class C:` -- every class already has
                # `object` at the root of its chain, so naming it explicitly
                # says nothing this runtime does not already do. Writing it is
                # a Python 2 habit that plenty of code still carries, and
                # refusing it rejected programs over punctuation.
                if node.bases[0].id != "object":
                    base = node.bases[0].id
            else:
                self._error("E0072", "a base class must be a plain name", node)
        if node.keywords:
            self._error("E0073", "a metaclass or class keyword is not "
                                 "supported", node)
        info = ClassInfo(node, node.name, qualname, base, scope=scope.key)
        # WHETHER THIS IS AN EXCEPTION CLASS is decided HERE, in the scope
        # pass, and not where the `class` statement is checked. Function
        # bodies are analysed before the module's, so a `def` that says
        # `except MyError:` is checked before the `class MyError` statement
        # is reached -- and reported the name as unknown.
        if base is not None and (base in _EXC_NAMES or base in self.exc_classes):
            info.is_exception = True
            self.exc_classes[node.name] = base
        self.classes[key] = info
        self.class_of_node[id(node)] = key
        return key

    def _resolve_closures(self) -> None:
        """Decide, for every mentioned name, whether it is captured.

        The rule is Python's: a name not bound in this function, but bound in
        some enclosing FUNCTION, is free here and a cell there. Every function
        BETWEEN the two gets it as free as well, because the closure is built
        one level at a time -- an inner function two levels down receives the
        box from its immediate parent, which must therefore have it to give.
        """
        for scope in self.scopes:
            if scope.kind != "function":
                continue
            wanted = (scope.reads | scope.nonlocals) - scope.globals
            for name in sorted(wanted):
                if name in scope.locals:
                    continue
                chain: list = []
                binder = scope.enclosing_function()
                while binder is not None and binder.kind == "function":
                    if name in binder.locals:
                        break
                    chain.append(binder)
                    binder = binder.enclosing_function()
                if binder is None or binder.kind != "function":
                    # Nothing encloses it: a module global or a builtin, both
                    # of which the existing `module_names` path already
                    # answers. `nonlocal` with no binder is the one error.
                    if name in scope.nonlocals:
                        self._error(
                            "E0067",
                            f"no binding for nonlocal {name!r} was found in "
                            f"an enclosing function", scope.node)
                    continue
                if name not in binder.cells:
                    binder.cells.append(name)
                for mid in chain + [scope]:
                    if name not in mid.frees:
                        mid.frees.append(name)

        for scope in self.scopes:
            info = self.functions.get(scope.key)
            if info is not None and scope.kind == "function":
                info.cellvars = list(scope.cells)
                info.freevars = list(scope.frees)

    def _module_names(self, body: list) -> set:
        """Every name the module's top level binds.

        Collected BEFORE any function body is checked, because a function may
        refer to a global defined after it -- the lookup happens when the
        function runs, not where it is written, and rejecting that would refuse
        a shape most Python files have.
        """
        found: set = set()
        for node in body:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign):
                    for t in sub.targets:
                        found.update(_target_names(t))
                elif isinstance(sub, (ast.AnnAssign, ast.AugAssign)):
                    found.update(_target_names(sub.target))
                elif isinstance(sub, ast.For):
                    found.update(_target_names(sub.target))
                elif isinstance(sub, ast.ExceptHandler) and sub.name:
                    found.add(sub.name)
                elif isinstance(sub, ast.With):
                    # `with a as x:` binds x. A `withitem` is not a statement
                    # or an expression, so neither of the branches above sees
                    # it and the name had no module storage.
                    for item in sub.items:
                        if isinstance(item.optional_vars, ast.Name):
                            found.add(item.optional_vars.id)
                elif isinstance(sub, (ast.Import, ast.ImportFrom)):
                    # An import BINDS A MODULE NAME, so a function below can
                    # read it -- which is the whole shape of a script that
                    # imports at the top and uses it further down.
                    found.update(_import_names(sub))
        return found

    def _entry(self, body: list) -> FunctionInfo | None:
        """Make the module's top-level statements the program.

        Running a Python file runs its top-level statements; a `def main` in it
        is an ordinary name that happens to be spelled `main`. This frontend
        began the other way round -- a module was a bag of definitions and
        `main` was the entry point by convention -- which meant no program that
        looks like a Python script would compile, and every conformance case is
        a script.

        Both shapes work now, under one rule: the module body is the entry, and
        a module with no top-level statements falls back to the old convention
        of `main` being it. That fallback is what keeps every already-written
        `def main() -> int:` program compiling and doing what it did.

        The synthesised entry is registered under a name no Python identifier
        can collide with, and lowering gives it the IR name `main` that the
        backends and the C runtime agree on. A user function actually spelled
        `main` in a module that HAS top-level statements is then an ordinary
        function that the entry may call, and lowering renames it out of the
        way -- two definitions of the entry symbol is a C compiler error
        naming a function the user never wrote.
        """
        info = self.functions.get("main")
        if body:
            node = ast.Module(body=body, type_ignores=[])
            node.lineno, node.col_offset = getattr(body[0], "lineno", 1), 0
            entry = FunctionInfo(node, ENTRY_NAME, [], INT, dynamic=True)
            self.functions[ENTRY_NAME] = entry
            return entry

        if info is None:
            # Only definitions, and no `main` to call: nothing would run. That
            # is a legal (empty) Python program, but it is far more often a
            # mistake than an intention, and the diagnostic costs nothing --
            # the moment the module grows one executable statement it stops
            # applying, because that statement IS the program.
            self.sink.report(error("E0003", "nothing to run")
                             .note("this module is only definitions, and "
                                   "defines no `main` to call")
                             .help("add top-level statements, or a "
                                   "`def main() -> int:`"))
            return None
        if info.params:
            self.sink.report(
                error("E0008", "`main` takes no parameters")
                .at(self._span(info.node))
                .note("with no top-level statements it is the program's entry "
                      "point, and there is nothing to pass it")
                .help("read arguments from a runtime function instead"))
        if info.ret is not INT and not info.ret.is_error:
            self.sink.report(
                error("E0009", f"`main` must return int, not {info.ret}")
                .at(self._span(info.node))
                .note("the return value becomes the process exit code"))
        # No top-level statements: `main` IS the entry, exactly as before. Not
        # a synthesised `return main()` wrapper -- that would work, but it puts
        # a trampoline where every reader (and every test) expects the IR
        # function called `main` to be the one the user wrote.
        return None

    # ── signatures ──────────────────────────────────────────────────────────
    def _signature(self, node: ast.FunctionDef) -> FunctionInfo:
        params: list[Symbol] = []
        seen: set[str] = set()
        # One unannotated parameter makes the WHOLE function dynamic, not just
        # that parameter. A function with a machine-word `int` in one slot and
        # a runtime object in another would need a conversion at every use of
        # either, and deciding per-parameter is how a compiler ends up with a
        # value whose representation depends on where it came from.
        # An `object` annotation is as dynamic as a missing one: the value's
        # kind is decided at run time either way, and `def f(v: object)` is how
        # a program says so explicitly. Read from the SOURCE rather than from
        # the resolved types, because resolving a parameter's annotation is
        # what this loop is about to do.
        # ANY annotation the static path cannot represent is dynamic, not an
        # error. `int`, `float`, `bool` and `None` are the machine words it
        # knows; `str`, `list[int]`, `Optional[int]`, a forward reference in
        # quotes and every typing construct are runtime objects, and saying so
        # is exactly what the dynamic path is for. Refusing them instead meant
        # a program that merely ANNOTATES did not compile at all -- and an
        # annotation is documentation, so a compiler that cannot use one
        # should ignore it rather than reject the program.
        def _is_dynamic_annotation(a) -> bool:
            if a is None:
                return True
            name = getattr(a, "id", None)
            if name is None and isinstance(a, ast.Constant):
                name = str(a.value)
            return name not in BY_NAME or name == "object"
        # A MISSING RETURN ANNOTATION is dynamic too, and this is not a
        # detail: `def f():` has no parameters, so "every parameter is
        # annotated" is vacuously true and a zero-argument function in an
        # ordinary script was taking the static path -- where module-level
        # names do not exist, so `def f(): return top` could not see `top`.
        # POSITIONAL-ONLY FIRST, then ordinary, then keyword-only: that is
        # the order they occupy argument positions in, and every count below
        # -- defaults, arity, the slot map -- is stated against it.
        declared = (list(node.args.posonlyargs) + list(node.args.args)
                    + list(node.args.kwonlyargs))
        dynamic = (any(_is_dynamic_annotation(a.annotation)
                       for a in declared)
                   or _is_dynamic_annotation(node.returns))
        for arg in declared:
            if arg.arg in seen:
                self._error("E0004",
                            f"duplicate parameter {arg.arg!r}", arg)
            seen.add(arg.arg)
            ty = (OBJ if dynamic
                  else self._annotation(arg.annotation, arg,
                                        f"parameter {arg.arg!r}"))
            params.append(Symbol(arg.arg, ty, self._span(arg), is_param=True))
        if not dynamic and (node.args.posonlyargs or node.args.kwonlyargs):
            # The static path has no call-site name matching to restrict: its
            # arguments are machine words in registers, filled by position and
            # nothing else.
            self._error("E0005", "positional-only and keyword-only parameters "
                                 "are not supported here", node)
        if node.args.kwarg and not dynamic:
            # The static path has nowhere to put one: its parameters are
            # machine words and a `**kw` is a dict.
            self._error("E0005", "**kwargs are not supported here", node)
        if node.args.vararg and not dynamic:
            self._error("E0005", "*args are not supported here", node)
        if node.args.defaults and not dynamic:
            # The static path has no place to keep a default: its parameters
            # are machine words in registers and there is no per-function
            # storage to evaluate one into.
            self._error("E0006", "default arguments are not supported here",
                        node)
        if node.decorator_list and not dynamic:
            # The static path has no value to hand a decorator: its functions
            # are machine-word signatures, not objects, so there is nothing
            # for `f = deco(f)` to rebind.
            self._error("E0007", "decorators are not supported here", node)

        if dynamic:
            ret = OBJ
        else:
            ret = (self._annotation(node.returns, node, "the return type")
                   if node.returns else NONE)
        info = FunctionInfo(node, node.name, params, ret, dynamic=dynamic)
        # A `yield` ANYWHERE in the body makes this a generator, whatever else
        # the body does -- so `def g(): yield 1` and `def g(): ... yield ...`
        # deep inside a loop are the same kind of function. It is decided here
        # rather than while checking the body, because a call to it has to
        # know before the body is reached.
        info.is_generator = dynamic and _has_yield(node)
        if info.is_generator:
            info.ret = OBJ
        if dynamic:
            info.defaults = list(node.args.defaults)
            info.vararg = node.args.vararg.arg if node.args.vararg else None
            info.kwarg = node.args.kwarg.arg if node.args.kwarg else None
            info.posonly = len(node.args.posonlyargs)
            info.kwonly = len(node.args.kwonlyargs)
            # A keyword-only parameter's default lives in its own list, and a
            # None there means REQUIRED rather than "defaults to None". Both
            # kinds end up as trailing defaults, which is the layout the
            # function value already has.
            info.defaults = list(node.args.defaults) + [
                d for d in node.args.kw_defaults if d is not None]
            if info.vararg:
                # `*rest` is an ordinary local holding a tuple. Declared as a
                # parameter would make the arity checks count it.
                pass
        return info

    def _annotation(self, ann, at, what: str) -> SemType:
        if ann is None:
            self.sink.report(
                error("E0010", f"{what} needs a type annotation")
                .at(self._span(at))
                .help("annotate with int, float, bool or None"))
            return ERROR
        name = getattr(ann, "id", None)
        if name is None and isinstance(ann, ast.Constant):
            name = str(ann.value)
        if name not in BY_NAME:
            self.sink.report(
                error("E0011", f"unsupported type {ast.unparse(ann)!r} for {what}")
                .at(self._span(ann))
                .note("this frontend understands: " + ", ".join(BY_NAME)))
            return ERROR
        return BY_NAME[name]

    # ── bodies ──────────────────────────────────────────────────────────────
    @property
    def class_names(self) -> set:
        """Bare class names, for resolving `C()` and `isinstance(x, C)`."""
        return {c.name for c in self.classes.values()}

    def _body(self, info: FunctionInfo) -> None:
        self.current = info
        self.dynamic = info.dynamic
        # A FREE variable is already bound -- the enclosing frame made the box
        # before this function existed -- so it is declared here and marked
        # assigned. Without that, reading it looks like use-before-assignment,
        # which is exactly the shape `inner-function-sees-later-binding` has:
        # the enclosing function assigns AFTER the `def`.
        for i, name in enumerate(info.freevars):
            info.locals[name] = Symbol(name, OBJ, self._span(info.node),
                                       storage=FREE, index=i)
        if info.kwarg:
            info.locals[info.kwarg] = Symbol(info.kwarg, OBJ,
                                             info.params[0].span
                                             if info.params else None,
                                             is_param=True)
        if info.vararg:
            info.locals[info.vararg] = Symbol(info.vararg, OBJ,
                                              self._span(info.node))
        scope = self.scope_of.get(info.name)
        if info.dynamic and info.name != ENTRY_NAME:
            declared = (set(scope.globals) if scope is not None
                        else _declared_global(info.node.body))
            info.module_writes = declared & self.module_names
            #: Local for the whole body if bound anywhere in it, minus what
            #: `global` hands back to the module, plus what a closure brought
            #: in -- a free variable is not this frame's storage but it is
            #: NOT the module's either, and leaving it out would make an
            #: enclosing function's `n` read the global of the same name.
            #:
            #: Taken from the SCOPE rather than by walking the body: the walk
            #: descends into nested `def`s, so an inner function's locals
            #: would count as this one's and shadow a global it really reads.
            self._function_locals = (
                (scope.locals if scope is not None
                 else self._module_names([info.node]) - declared)
                | {p.name for p in info.params} | set(info.freevars))
        else:
            self._function_locals = None
        for p in info.params:
            info.locals[p.name] = p
        if info.dynamic and self._function_locals is not None:
            # AN ASSIGNMENT ANYWHERE MAKES THE NAME LOCAL FOR THE WHOLE BODY.
            # That is Python's rule, and it is what makes reading a name
            # before its assignment an `UnboundLocalError` rather than finding
            # the global of the same spelling:
            #
            #     n = 10                  # module level
            #     def f():
            #         print(n)            # UnboundLocalError, not 10
            #         n = 5
            #
            # Declared up front so the read below finds the symbol. A name
            # nothing assigns is NOT here, so a misspelling is still an
            # undefined name reported at compile time.
            for name in sorted(self._function_locals):
                if name not in info.locals:
                    info.locals[name] = Symbol(name, OBJ, self._span(info.node))
        #: Names definitely assigned at the current point. Parameters always
        #: are; everything else has to be reached by an assignment on EVERY
        #: path, which is what makes the intersection at a join the whole
        #: analysis.
        self.assigned = {p.name for p in info.params} | set(info.freevars)
        if info.vararg:
            self.assigned.add(info.vararg)
        if info.kwarg:
            self.assigned.add(info.kwarg)
        self._block(info.node.body)
        # Which locals are BOXED, decided now that every name is declared. A
        # parameter can be one too: `def outer(n)` with an inner function that
        # reads `n` boxes the parameter on entry.
        for i, name in enumerate(info.cellvars):
            sym = info.locals.get(name)
            if sym is not None:
                sym.storage, sym.index = CELL, i
        if info.is_generator:
            # EVERY LOCAL GETS A SLOT, now that every name is declared. A
            # register does not survive the return a `yield` compiles to, so a
            # generator's frame is the object itself -- see `_dyn_generator`.
            # Numbered here rather than during lowering because both halves of
            # the pair, the constructor and the step, must agree on the map.
            info.slots = {name: i for i, name in enumerate(info.locals)}
        self.current = None

    def _block(self, body: list) -> bool:
        """Analyse a list of statements. Returns whether control falls through.

        Falling through matters for the join: a branch that always returns
        contributes nothing to what is assigned afterwards, and treating it
        as a normal path would reject

            if c:
                x: int = 1
            else:
                return 0
            print(x)

        which is correct code and a common shape.
        """
        for stmt in body:
            if not self._stmt(stmt):
                return False
        return True

    def _stmt(self, node) -> bool:
        """Analyse one statement; returns whether control falls through."""
        info = self.current
        assert info is not None
        self.dynamic = info.dynamic
        match node:
            case ast.Expr():
                self._expr(node.value)
            case ast.Assign(targets=[ast.Name(id=name)]):
                self._bind(name, self._expr(node.value), node)
                self.assigned.add(name)
            case ast.Assign(targets=[(ast.Tuple() | ast.List()) as target]) \
                    if self.dynamic:
                self._expr(node.value)
                for name in _target_names(target):
                    self._bind(name, OBJ, node)
                    self.assigned.add(name)
            case ast.Assign(targets=[ast.Subscript() as target]) if self.dynamic:
                self._expr(target.value)
                self._expr(target.slice)
                self._expr(node.value)
            case ast.Assign(targets=[ast.Attribute() as target]) if self.dynamic:
                # `x.attr = v`. Nothing static to check -- whether `x` has that
                # attribute, and whether it accepts one, are both runtime
                # questions, and the runtime answers them where CPython does.
                self._expr(target.value)
                self._expr(node.value)
            case ast.Assign():
                self._error("E0020", "only simple `name = value` assignment "
                                     "is supported", node)
            case ast.AnnAssign(target=ast.Name(id=name)) if self.dynamic:
                # `x: int = 1` inside a dynamic function. The annotation is
                # not enforced: the value is an object either way, and
                # honouring it would mean one slot in the function held a
                # machine word while its neighbours held objects.
                if node.value is not None:
                    self._expr(node.value)
                    self._bind(name, OBJ, node)
                    self.assigned.add(name)
                else:
                    self._declare(name, OBJ, node)
            case ast.AnnAssign(target=ast.Name(id=name)):
                declared = self._annotation(node.annotation, node, f"{name!r}")
                if node.value is not None:
                    actual = self._expr(node.value)
                    self._check_assignable(actual, declared, node)
                self._declare(name, declared, node)
                # `x: int` with no value declares a type and assigns nothing,
                # exactly as in Python.
                if node.value is not None:
                    self.assigned.add(name)
            case ast.AugAssign(target=(ast.Subscript() | ast.Attribute())) \
                    if self.dynamic:
                # `xs[i] += v`. The TARGET EXPRESSION IS EVALUATED ONCE --
                # `xs[idx()] += 5` calls `idx` a single time -- which is why
                # this is a statement of its own rather than a rewrite to
                # `xs[i] = xs[i] + v`.
                if isinstance(node.target, ast.Subscript):
                    self._expr(node.target.value)
                    self._expr(node.target.slice)
                else:
                    self._expr(node.target.value)
                self._expr(node.value)
            case ast.AugAssign(target=ast.Name(id=name)):
                # `x += 1` READS x first, so an unassigned x is an error here
                # for the same reason it is in an expression.
                have = self._lookup(name, node)
                # Analysed as the binary operation it is, through the same
                # path a written-out `x = x + 1` takes. Checking only the
                # right-hand side let `x **= n` and `x @= y` past the operator
                # rules and into lowering, which raised at the user.
                synthetic = ast.BinOp(
                    left=ast.Name(id=name, ctx=ast.Load()),
                    op=node.op, right=node.value)
                ast.copy_location(synthetic, node)
                ast.copy_location(synthetic.left, node)
                info.aug_nodes[id(node)] = synthetic
                result = self._expr(synthetic)
                self._check_assignable(result, have, node,
                                       what=f"assignment to {name!r}")
                self.assigned.add(name)
            case ast.Return():
                got = self._expr(node.value) if node.value else NONE
                # In a GENERATOR a `return` does not produce the call's value
                # -- it ends the iteration -- so there is nothing to check it
                # against. `return` and `return v` are both legal there, and
                # `v` becomes StopIteration's `value` rather than the result.
                if not info.is_generator:
                    self._check_assignable(got, info.ret, node,
                                           what="return value")
                return False
            case ast.If():
                self._expr(node.test)
                before = set(self.assigned)
                then_falls = self._block(node.body)
                then_assigned = set(self.assigned)
                self.assigned = before
                else_falls = self._block(node.orelse)
                else_assigned = set(self.assigned)
                # A name is assigned after the `if` only if every path that
                # reaches here assigned it.
                if then_falls and else_falls:
                    self.assigned = then_assigned & else_assigned
                elif then_falls:
                    self.assigned = then_assigned
                elif else_falls:
                    self.assigned = else_assigned
                else:
                    self.assigned = then_assigned | else_assigned
                return then_falls or else_falls
            case ast.While():
                self._expr(node.test)
                # The body may run zero times, so nothing it assigns is
                # definitely assigned afterwards.
                before = set(self.assigned)
                self.loop_depth += 1
                self._block(node.body)
                self.loop_depth -= 1
                self.assigned = before
                if node.orelse and not self.dynamic:
                    # The static path's lowering has no `else` on a loop, so
                    # accepting one here would drop it in silence. Rejecting is
                    # a limit; dropping is a wrong answer.
                    self._error("E0026", "`while ... else` is not supported "
                                         "in an annotated function", node)
                self._block(node.orelse)
                self.assigned = before
            case ast.For(target=(ast.Tuple() | ast.List())) if self.dynamic:
                self._for_unpack(node)
            case ast.For(target=ast.Name(id=name)):
                self._for_range(node, name)
            case ast.For():
                self._error("E0021", "loop target must be a plain name", node)
            case ast.Break() | ast.Continue():
                if self.loop_depth == 0:
                    self._error(
                        "E0027",
                        f"`{type(node).__name__.lower()}` outside a loop", node)
                return False
            case ast.Global() if self.dynamic:
                for name in node.names:
                    if name not in self.module_names:
                        self._error("E0066",
                                    f"`global {name}` names nothing at module "
                                    f"scope", node)
            case ast.Nonlocal() if self.dynamic:
                # The binding was resolved in `_resolve_closures`, which is
                # also where a nonlocal with nothing to bind to is reported.
                # Nothing left to check here, and nothing to emit: the
                # declaration only changes which storage the assignments use.
                pass
            case ast.Nonlocal():
                self._error("E0067", "`nonlocal` needs a dynamic function",
                            node)
            case ast.FunctionDef() if self.dynamic:
                # A nested `def`. Its body was registered and checked on its
                # own; what happens HERE is the binding of its name to a
                # function value, which is an ordinary assignment.
                self._bind(node.name, OBJ, node)
                self.assigned.add(node.name)
            case ast.ClassDef() if self.dynamic:
                self._class_statement(node)
            case ast.FunctionDef() | ast.ClassDef():
                self._error("E0074",
                            f"a nested {'class' if isinstance(node, ast.ClassDef) else 'def'}"
                            f" needs a dynamic function; this one is "
                            f"statically typed", node)
            case ast.Raise() if self.dynamic:
                if node.exc is not None:
                    self._expr(node.exc)
                if node.cause is not None:
                    self._expr(node.cause)
                return False
            case ast.Delete() if self.dynamic:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        self._lookup(target.id, target)
                        # `del x` UNBINDS. For a LOCAL that is a compile-time
                        # fact, so dropping it from `assigned` turns a later
                        # read into the error CPython raises as
                        # UnboundLocalError, reported earlier.
                        #
                        # For a MODULE name it is not: the cell goes back to
                        # zero and every read of one is already guarded, so
                        # the NameError happens where CPython's does -- at run
                        # time, where `try: print(x) except NameError:` can
                        # catch it. Rejecting that at compile time would make
                        # a program that HANDLES the error impossible to run,
                        # which is a larger divergence than the one the
                        # diagnostic was protecting against.
                        if target.id not in self.module_names:
                            self.assigned.discard(target.id)
                    elif isinstance(target, ast.Subscript):
                        self._expr(target.value)
                        self._expr(target.slice)
                    else:
                        self._error("E0081",
                                    f"`del` of a "
                                    f"{type(target).__name__} is not "
                                    f"supported", target)
            case ast.NamedExpr():
                pass        # only ever reached as an expression
            case ast.Assert() if self.dynamic:
                self._expr(node.test)
                if node.msg is not None:
                    self._expr(node.msg)
            case ast.Import() if self.dynamic:
                for alias in node.names:
                    if resolve(alias.name) is None:
                        self._error("E0083",
                                    f"no module named {alias.name!r} is "
                                    f"available; there is no import path",
                                    node, importable())
                        continue
                    # `import a.b` BINDS `a`, as Python does: the head is a
                    # package holding the module, so `c.math.sqrt` reads two
                    # attributes off one name. `import a.b as n` binds `n` to
                    # the module itself instead, which is the other half of
                    # the same rule.
                    bound = alias.asname or alias.name.split(".")[0]
                    self._bind(bound, OBJ, node)
                    self.assigned.add(bound)
            case ast.ImportFrom() if self.dynamic:
                if node.module is None or resolve(node.module) is None:
                    self._error("E0083",
                                f"no module named {node.module!r} is "
                                f"available; there is no import path",
                                node, importable())
                else:
                    for alias in node.names:
                        if member(node.module, alias.name) is None:
                            self._error("E0084",
                                        f"module {node.module!r} has no member "
                                        f"{alias.name!r}", node)
                            continue
                        bound = alias.asname or alias.name
                        self._bind(bound, OBJ, node)
                        self.assigned.add(bound)
            case ast.With() if self.dynamic:
                for item in node.items:
                    self._expr(item.context_expr)
                    if item.optional_vars is not None:
                        if not isinstance(item.optional_vars, ast.Name):
                            self._error("E0082",
                                        "`with ... as` needs a plain name",
                                        node)
                        else:
                            self._bind(item.optional_vars.id, OBJ, node)
                            self.assigned.add(item.optional_vars.id)
                return self._block(node.body)
            case ast.Try() if self.dynamic:
                return self._dyn_try(node)
            case ast.Pass():
                pass
            case _:
                self._error("E0022",
                            f"unsupported statement: {type(node).__name__}", node)
        return True

    def _class_statement(self, node: ast.ClassDef) -> None:
        """Check a `class` statement where it is WRITTEN.

        The methods were registered and are checked as functions of their own.
        What is left is the class body's other bindings -- which are class
        attributes, shared by every instance -- and the name the statement
        binds in the enclosing scope.
        """
        info = self.classes[self.class_of_node[id(node)]]
        if info.base is not None:
            if info.is_exception:
                # A user EXCEPTION class. It becomes a name in the runtime's
                # exception hierarchy rather than a type object -- see
                # `apy_exc_register` in link/objects.py -- so `except
                # ValueError:` catches it through the same walk that makes
                # `except LookupError:` catch a KeyError.
                #
                # THE BODY MUST BE EMPTY, because a name in a hierarchy has no
                # methods and no attributes to put them in. Accepting one and
                # dropping it silently is the bad half of this trade: the
                # program would compile and quietly do less than it says.
                real = [st for st in node.body
                        if not isinstance(st, ast.Pass)
                        and not _is_docstring(st)]
                if real:
                    self._error("E0075",
                                f"an exception class may only have an empty "
                                f"body; {node.name!r} defines "
                                f"{type(real[0]).__name__}", real[0])
                self._bind(node.name, OBJ, node)
                self.assigned.add(node.name)
                return
            elif not any(c.name == info.base for c in self.classes.values()):
                self._error("E0076",
                            f"base class {info.base!r} is not a class defined "
                            f"in this module", node)
        for stmt in node.body:
            match stmt:
                case ast.FunctionDef():
                    # The body was registered and is checked on its own; the
                    # DECORATORS are not part of it -- they run where the
                    # class body runs, so an unknown one has to be reported
                    # here or it reaches lowering as an unbound global.
                    for deco in stmt.decorator_list:
                        self._expr(deco)
                case ast.Assign(targets=[ast.Name(id=name)], value=value):
                    self._expr(value)
                    info.attrs.append((name, value))
                case ast.AnnAssign(target=ast.Name(id=name), value=value) \
                        if value is not None:
                    self._expr(value)
                    info.attrs.append((name, value))
                case ast.Pass():
                    pass
                case _ if _is_docstring(stmt):
                    pass
                case _:
                    self._error("E0077",
                                f"a class body may only contain methods and "
                                f"attribute assignments, not "
                                f"{type(stmt).__name__}", stmt)
        self._bind(node.name, OBJ, node)
        self.assigned.add(node.name)

    def _for_unpack(self, node: ast.For) -> None:
        """`for a, b in pairs:` -- the target is a tuple of names."""
        self._expr(node.iter)
        names = _target_names(node.target)
        before = set(self.assigned)
        for name in names:
            self._declare(name, OBJ, node)
            self.assigned.add(name)
        self.loop_depth += 1
        self._block(node.body)
        self.loop_depth -= 1
        self.assigned = before
        self._block(node.orelse)
        self.assigned = before

    def _dyn_try(self, node: ast.Try) -> bool:
        """Analyse a try statement. Falls through unless every path returns.

        The definite-assignment set is intersected the way `if` does it, and
        for the same reason -- except that the BODY may have stopped anywhere,
        so nothing it assigned is definitely assigned in a handler.
        """
        before = set(self.assigned)
        body_falls = self._block(node.body)
        after_body = set(self.assigned)

        # THE PATHS THAT REACH PAST THE STATEMENT, each with what it assigned.
        # Intersected at the end, exactly as `if`/`else` does it -- and for
        # the same reason a branch that returns does not dilute an `if`, a
        # HANDLER THAT CANNOT FALL THROUGH does not dilute this:
        #
        #     try:
        #         result = run()
        #     except Error as exc:
        #         raise Wrapped from exc     # never reaches the line below
        #     use(result)                    # `result` IS assigned here
        #
        # Resetting to `before` unconditionally -- which is what this did --
        # rejected that shape, and it is the ordinary way to wrap an
        # exception. A handler ending in `continue`, `break` or `return` is
        # the same case.
        exits: list[set] = []
        if body_falls:
            self.assigned = after_body
            # `else` runs only when the body finished without raising, so it
            # extends that path rather than being one of its own.
            if not node.orelse or self._block(node.orelse):
                exits.append(set(self.assigned))
        for handler in node.handlers:
            # A HANDLER STARTS FROM `before`: the body may have stopped
            # anywhere, so nothing it assigned is definitely assigned here.
            self.assigned = set(before)
            if handler.type is not None:
                for name in _handler_names(handler.type):
                    if name not in _EXC_NAMES and name not in self.exc_classes:
                        self._error("E0062",
                                    f"unknown exception type {name!r}",
                                    handler)
            if handler.name:
                self._declare(handler.name, OBJ, handler)
                self.assigned.add(handler.name)
            if self._block(handler.body):
                exits.append(set(self.assigned))

        # `finally` runs on EVERY path out, so what it assigns is assigned
        # afterwards whichever way control got there.
        after_final: set = set()
        if node.finalbody:
            self.assigned = set(before)
            self._block(node.finalbody)
            after_final = set(self.assigned)

        if exits:
            out = set(exits[0])
            for reached in exits[1:]:
                out &= reached
            self.assigned = out | after_final
            return True
        # Nothing falls through: every path returned, raised or jumped.
        self.assigned = before | after_final
        return False

    def _for_range(self, node: ast.For, name: str) -> None:
        call = node.iter
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "range"):
            if self.dynamic:
                # Any sequence, walked by index. Not an iterator protocol --
                # there is no generator yet, and a `for` over a list does not
                # need one.
                self._expr(node.iter)
                self._declare(name, OBJ, node)
                before = set(self.assigned)
                self.assigned.add(name)
                self.loop_depth += 1
                self._block(node.body)
                self.loop_depth -= 1
                self.assigned = before
                self._block(node.orelse)
                self.assigned = before
                return
            self._error("E0023", "only `for ... in range(...)` is supported",
                        node.iter)
            return
        if not 1 <= len(call.args) <= 3:
            self._error("E0024", "range() takes 1 to 3 arguments", call)
        for a in call.args:
            got = self._expr(a)
            if not self.dynamic:
                self._check_assignable(got, INT, a, what="range() argument")
            # In a dynamic function every argument is an object, and whether
            # it holds an int is a runtime question. Lowering unwraps it; a
            # value that is not an int fails there, where CPython fails too.
        if len(call.args) == 3:
            # The step's SIGN decides whether the loop test is `<` or `>`.
            # A runtime step would need both tests and a branch on the sign;
            # accepting one and emitting `<` would make every descending loop
            # run zero times and report success.
            step = int_literal(call.args[2])
            if step is None:
                self._error("E0028", "range() step must be a literal",
                            call.args[2])
            elif step == 0:
                self._error("E0029", "range() step must not be zero",
                            call.args[2])
        self._declare(name, OBJ if self.dynamic else INT, node)
        # `range(0)` runs zero times, so neither the loop variable nor
        # anything the body assigns is definitely assigned afterwards --
        # `for i in range(0): pass` then `print(i)` is an UnboundLocalError
        # in Python too.
        before = set(self.assigned)
        self.assigned.add(name)
        self.loop_depth += 1
        self._block(node.body)
        self.loop_depth -= 1
        self.assigned = before
        # `for ... else` runs the else clause when the loop finished WITHOUT a
        # break -- so it is reachable, and nothing it assigns is definitely
        # assigned afterwards either. Only on the dynamic path: the static
        # lowering has no `else` on a loop and would drop it in silence.
        if node.orelse and not self.dynamic:
            self._error("E0025", "`for ... else` is not supported in an "
                                 "annotated function", node)
        self._block(node.orelse)
        self.assigned = before

    # ── symbols ─────────────────────────────────────────────────────────────
    def _declare(self, name: str, ty: SemType, at) -> Symbol:
        info = self.current
        assert info is not None
        existing = info.locals.get(name)
        if existing is not None:
            if not ty.is_error and not existing.type.is_error and existing.type != ty:
                self.sink.report(
                    error("E0030", f"{name!r} was declared {existing.type} "
                                   f"and is being redeclared {ty}")
                    .at(self._span(at))
                    .also(existing.span, "first declared here")
                    .note("a variable keeps one type for its whole life"))
            return existing
        sym = Symbol(name, ty, self._span(at))
        info.locals[name] = sym
        return sym

    def _bind(self, name: str, ty: SemType, at) -> None:
        info = self.current
        assert info is not None
        if name in info.module_writes:
            return          # the module owns the storage; nothing local to bind
        existing = info.locals.get(name)
        if existing is None:
            self._declare(name, ty, at)
            return
        self._check_assignable(ty, existing.type, at,
                               what=f"assignment to {name!r}")

    def _lookup(self, name: str, at) -> SemType:
        info = self.current
        assert info is not None
        sym = info.locals.get(name)
        if sym is not None and name not in self.assigned and info.dynamic:
            # NOT PROVED, so checked at run time -- see `maybe_unbound`. Some
            # correct programs cannot be proved: an interpreter's dispatch
            # loop assigns under one condition and reads under its complement,
            # and nothing short of a solver relates the two.
            info.maybe_unbound.add(name)
        if sym is None:
            self.sink.report(
                error("E0031", f"undefined name {name!r}")
                .at(self._span(at))
                .help("assign it before use, or annotate it"))
            return ERROR
        if name not in self.assigned and not info.dynamic:
            # Python raises UnboundLocalError for this at runtime; saying it
            # at compile time is strictly better. Reporting it here also
            # keeps it out of the IR, where the verifier catches the same
            # thing as "reads %1 before any path writes it" and the driver
            # correctly but unhelpfully calls it an internal compiler error.
            self.sink.report(
                error("E0032", f"{name!r} may be used before it is assigned")
                .at(self._span(at))
                .also(sym.span, "assigned here, but not on every path")
                .note("Python raises UnboundLocalError for this at runtime")
                .help("give it a value before the branch, or assign it in "
                      "every branch"))
            # Treated as assigned from here on, so one mistake yields one
            # diagnostic rather than one per use.
            self.assigned.add(name)
        sym.used = True
        return sym.type

    # ── expressions ─────────────────────────────────────────────────────────
    def _expr(self, node) -> SemType:
        ty = self._expr_inner(node)
        info = self.current
        if info is not None:
            info.expr_types[id(node)] = ty
        return ty

    def _expr_inner(self, node) -> SemType:
        if getattr(self, "dynamic", False):
            return self._dyn_expr(node)
        match node:
            case ast.Constant(value=bool()):
                return BOOL
            case ast.Constant(value=int()):
                return INT
            case ast.Constant(value=float()):
                return FLOAT
            case ast.Constant(value=None):
                return NONE
            case ast.Name(id=name):
                return self._lookup(name, node)
            case ast.UnaryOp(op=ast.Not()):
                self._expr(node.operand)
                return BOOL
            case ast.UnaryOp():
                return self._expr(node.operand)
            case ast.BinOp():
                return self._binop(node)
            case ast.BoolOp():
                types = [self._expr(v) for v in node.values]
                return self._unify_all(types, node)
            case ast.Compare():
                self._expr(node.left)
                for c in node.comparators:
                    self._expr(c)
                return BOOL
            case ast.Call():
                return self._call(node)
            case ast.IfExp():
                self._expr(node.test)
                return self._unify_all(
                    [self._expr(node.body), self._expr(node.orelse)], node)
        self._error("E0040",
                    f"unsupported expression: {type(node).__name__}", node)
        return ERROR

    def _dyn_expr(self, node) -> SemType:
        """Check an expression in a dynamic function. Every result is OBJ.

        There is nothing to infer -- the value's kind is decided at run time --
        so this walks for the questions that still have static answers: an
        undefined name, a call to something that does not exist, a call with
        the wrong number of arguments. Everything else the object runtime
        answers, including the type errors, which it raises where CPython does.
        """
        match node:
            case ast.Lambda():
                # Its body was checked with its own scope during collection;
                # the expression itself is a function value.
                return OBJ
            case ast.Attribute(value=ast.Name(id=base)) if (
                    base in _DYN_BUILTINS and base not in self.current.locals):
                # `str.lower` -- an unbound method of a builtin type. Lowering
                # makes it a one-argument callable; there is nothing to check
                # here, because whether the method exists is the receiver's
                # business at run time.
                return OBJ
            case ast.NamedExpr(target=ast.Name(id=name)):
                # PEP 572: binds `name` and evaluates to the same value. The
                # binding is in the enclosing scope, not a scope of its own,
                # which is what makes `while (n := next_one()) > 0:` work.
                self._expr(node.value)
                self._bind(name, OBJ, node)
                self.assigned.add(name)
                return OBJ
            case ast.Constant(value=v) if type(v) in _CONSTANT_KINDS:
                return OBJ
            case ast.Constant(value=v):
                # A literal whose type the runtime has no kind for. Accepting
                # it here and letting lowering assert breaks the project's
                # stated invariant -- a result or a diagnostic, never a
                # traceback -- and it did, for every `b'..'` in the corpus.
                # Whether the kind is missing is decided HERE, where there is
                # a span to point at and a name to say.
                if v is Ellipsis:
                    return OBJ
                self._error("E0080",
                            f"a {type(v).__name__} literal is not supported "
                            f"yet", node)
                return ERROR
            case ast.Yield() | ast.YieldFrom() if self.current.is_generator:
                if node.value is not None:
                    self._expr(node.value)
                return OBJ
            case ast.Name(id=name) if name in _SINGLETON_NAMES:
                return OBJ
            case ast.Attribute(value=ast.Name(id="object"), attr=attr) \
                    if attr in OBJECT_DEFAULTS \
                    and "object" not in self.current.locals:
                # `object.__getattribute__` and friends -- see
                # `OBJECT_DEFAULTS`. Shadowed by a local named `object`, which
                # is why the table is consulted only when nothing else claims
                # the name.
                return OBJ
            case ast.Name(id=name) if (name in _MODULE_DUNDERS
                                       and name not in self.module_names
                                       and name not in self.current.locals):
                # `__name__` and friends. Not assigned by the program, so no
                # storage exists for them; lowering answers each with its
                # constant. Shadowed by a real binding of the same spelling,
                # which is why both tables are checked first.
                return OBJ
            case ast.Name(id=name):
                if (self._function_locals is not None
                        and name not in self._function_locals
                        and name in self.module_names):
                    # A module-level name, read from inside a function.
                    self.current.module_reads.add(name)
                    return OBJ
                if name not in self.current.locals:
                    if name in _EXC_NAMES or name in self.exc_classes:
                        return OBJ
                    if name in self.functions or name in self.class_names:
                        # A `def` or a `class` NAMED but not called. Both are
                        # values now -- a function object and a type object --
                        # so this is an ordinary read, not the refusal it used
                        # to be.
                        #
                        # A STATICALLY TYPED `def` is the exception: its
                        # parameters are machine words and there is no object
                        # to hand out, so it has no value form and no module
                        # storage. Saying so here is what stops lowering
                        # emitting a read of a global that was never made --
                        # which reached the IR verifier as an internal error
                        # on a program the user wrote.
                        if (name in self.functions
                                and not self.functions[name].dynamic):
                            self._error(
                                "E0085",
                                f"{name!r} is a statically typed function and "
                                f"has no value form", node)
                            return ERROR
                        return OBJ
                    if name in _DYN_BUILTINS:
                        # A builtin used as a VALUE -- `key=repr`,
                        # `map(str, xs)`. Lowering synthesises a one-argument
                        # function that calls it, so it becomes a value like
                        # any other. Only the one-argument builtins qualify:
                        # the thunk's SHAPE is what makes it callable, and a
                        # variadic like `print` has no single shape.
                        if name in _VALUE_BUILTINS:
                            return OBJ
                        self._error("E0056",
                                    f"{name!r} is a builtin that cannot be "
                                    f"used as a value", node)
                        return ERROR
                self._lookup(name, node)
                return OBJ
            case ast.Attribute():
                # ANY attribute, on any value. This used to accept only
                # `type(x).__name__` because neither `type(x)` nor a bound
                # method was a value; both are now, so the general form is
                # what the runtime answers and a missing attribute is an
                # AttributeError where CPython raises one rather than a
                # compile error where CPython has none.
                self._expr(node.value)
                return OBJ
            case ast.Set(elts=elts):
                for e in elts:
                    self._arg_expr(e)
                return OBJ
            case ast.JoinedStr(values=parts):
                for part in parts:
                    if isinstance(part, ast.Constant):
                        continue
                    if part.format_spec is not None:
                        # A spec is itself an f-string -- `{x:{w}}` nests --
                        # so it is checked as one rather than as text.
                        self._expr(part.format_spec)
                    self._expr(part.value)
                return OBJ
            case ast.List(elts=elts) | ast.Tuple(elts=elts):
                # `_arg_expr`, not `_expr`: a display may contain `*xs`, and
                # the star itself has no type -- what it spreads does.
                for e in elts:
                    self._arg_expr(e)
                return OBJ
            case ast.ListComp() | ast.SetComp() | ast.GeneratorExp():
                return self._dyn_comprehension(node, [node.elt])
            case ast.DictComp():
                return self._dyn_comprehension(node, [node.key, node.value])
            case ast.Dict(keys=keys, values=values):
                for k, v in zip(keys, values):
                    if k is None:
                        self._error("E0059", "`**` in a dict display is not "
                                             "supported yet", node)
                        continue
                    self._expr(k)
                    self._expr(v)
                return OBJ
            case ast.Subscript():
                self._expr(node.value)
                if isinstance(node.slice, ast.Slice):
                    for part in (node.slice.lower, node.slice.upper,
                                 node.slice.step):
                        if part is not None:
                            self._expr(part)
                    return OBJ
                self._expr(node.slice)
                return OBJ
            case ast.Call():
                return self._dyn_call(node)
            case ast.BinOp():
                self._expr(node.left); self._expr(node.right)
                if not isinstance(node.op, self._LOWERABLE_BINOPS):
                    self.sink.report(
                        error("E0045",
                              f"operator {_op_symbol(node.op)} is not supported")
                        .at(self._span(node)))
                return OBJ
            case ast.UnaryOp():
                self._expr(node.operand)
                return OBJ
            case ast.BoolOp():
                for v in node.values:
                    self._expr(v)
                return OBJ
            case ast.Compare():
                self._expr(node.left)
                for c in node.comparators:
                    self._expr(c)
                return OBJ
            case ast.IfExp():
                self._expr(node.test); self._expr(node.body)
                self._expr(node.orelse)
                return OBJ
        self._error("E0040",
                    f"unsupported expression: {type(node).__name__}", node)
        return ERROR

    def _dyn_comprehension(self, node, results: list) -> SemType:
        """A comprehension binds its loop variables in its OWN scope.

        Python gives a comprehension a scope of its own, so `[i for i in xs]`
        does not leave `i` behind. This frontend has one flat scope per
        function, so the names ARE visible afterwards -- a stated divergence.
        What it must not do is reject the comprehension for using a name that
        the enclosing code never assigned, which is why the targets are
        declared here before the element expression is checked.
        """
        for gen in node.generators:
            if gen.is_async:
                self._error("E0064", "an async comprehension is not supported",
                            node)
            self._expr(gen.iter)
            for name in _target_names(gen.target):
                self._declare(name, OBJ, node)
                self.assigned.add(name)
            for cond in gen.ifs:
                self._expr(cond)
        for r in results:
            self._expr(r)
        return OBJ

    def _arg_expr(self, arg) -> SemType:
        """Type one call argument, seeing through `*xs`.

        The star itself has no type; what it spreads does, and typing that is
        what catches `f(*7)`. Every place a call's arguments are walked goes
        through here, because a missed one reaches lowering as a bare
        `ast.Starred` and asserts.
        """
        if isinstance(arg, ast.Starred):
            return self._expr(arg.value)
        return self._expr(arg)

    def _dyn_call(self, node: ast.Call) -> SemType:
        for kw in node.keywords:
            self._expr(kw.value)
        if isinstance(node.func, ast.Attribute)                 and isinstance(node.func.value, ast.Name)                 and node.func.value.id in _BUILTIN_TYPE_NAMES                 and node.func.attr in _TYPE_STATIC_NAMES                 and node.func.value.id not in self.current.locals:
            # `dict.fromkeys(...)` -- a constructor on the TYPE. The receiver
            # is not typed because there is none; see `_TYPE_STATICS`.
            for a in node.args:
                self._arg_expr(a)
            return OBJ
        if isinstance(node.func, ast.Attribute)                 and isinstance(node.func.value, ast.Name)                 and node.func.value.id == "object"                 and node.func.attr in OBJECT_DEFAULTS                 and "object" not in self.current.locals:
            # `object.__getattribute__(self, name)` -- a DEFAULT, resolved by
            # name. The receiver is not typed because there is no `object`
            # value to type; see `OBJECT_DEFAULTS`.
            for a in node.args:
                self._arg_expr(a)
            return OBJ
        if isinstance(node.func, ast.Attribute):
            # A method call. A name in the table lowers to the runtime
            # operation it names; anything else is looked up on the receiver
            # and called, which is what a user class needs and what makes
            # `"abc".nosuch()` an AttributeError at run time -- where CPython
            # raises it -- instead of a compile error CPython does not have.
            self._expr(node.func.value)
            for a in node.args:
                self._arg_expr(a)
            return OBJ
        if not isinstance(node.func, ast.Name):
            # A call on something computed: `fs[0](x)`, `obj.f()(y)`. The
            # callee is a value, so this is `apy_call` like any other.
            self._expr(node.func)
            for a in node.args:
                self._arg_expr(a)
            return OBJ
        name = node.func.id
        if name == "super":
            if node.args:
                self._error("E0078", "only the no-argument form of `super()` "
                                     "is supported", node)
            elif self.current.owner is None:
                self._error("E0079", "`super()` outside a method", node)
            return OBJ
        for a in node.args[:1] if name == "isinstance" else node.args:
            self._arg_expr(a)
        if name in _EXC_NAMES or name in self.exc_classes:
            if len(node.args) > 1:
                self._error("E0061", "an exception takes at most one "
                                     "argument here", node)
            return OBJ
        if name == "isinstance":
            # THE SECOND ARGUMENT IS AN EXPRESSION, and usually a dotted one:
            # `isinstance(node, ast.Name)` is what every AST walk is made of.
            # A tuple of them is the other legal form and means "any of
            # these".
            #
            # Only a BARE BUILTIN NAME is special. There is no `int` value to
            # compare against -- the builtin types are kinds, not objects --
            # so that one name travels as text and everything else travels as
            # the class it evaluates to. See `_dyn_type_name`.
            second = node.args[1] if len(node.args) == 2 else None
            for element in (second.elts
                            if isinstance(second, (ast.Tuple, ast.List))
                            else [second] if second is not None else []):
                if isinstance(element, ast.Name)                         and element.id not in self.current.locals                         and element.id not in self.class_names:
                    continue      # a builtin or exception name: travels as text
                self._expr(element)
            return OBJ
        if name in _DYN_BUILTINS:
            want = _DYN_BUILTINS[name]
            starred = any(isinstance(a, ast.Starred) for a in node.args)
            allowed = _BUILTIN_KEYWORDS.get(name)
            if allowed is not None:
                # These are keywords, not positional, so the positional count
                # is unaffected by them.
                for kw in node.keywords:
                    if kw.arg not in allowed:
                        self._error("E0068",
                                    f"{name}() got an unexpected keyword "
                                    f"argument {kw.arg!r}", node)
            if want is not None and not starred and len(node.args) != want:
                self._error("E0054",
                            f"{name}() takes exactly {want} argument(s), "
                            f"got {len(node.args)}", node)
            return OBJ
        info = self.functions.get(name)
        if info is None:
            # Not a module-level `def`. It may still be a callable VALUE -- a
            # class, a nested function, a parameter holding one -- in which
            # case this is `apy_call` and the arity is the callee's business
            # at run time. Only a name that resolves to nothing is an error.
            if name in self.class_names or name in self.current.locals \
                    or (self._function_locals is not None
                        and name not in self._function_locals
                        and name in self.module_names):
                self._expr(node.func)
                return OBJ
            self.sink.report(
                error("E0052", f"call to unknown function {name!r}")
                .at(self._span(node.func))
                .note("known functions: "
                      + (", ".join(sorted(n for n in self.functions
                                          if n != ENTRY_NAME)) or "(none)")))
            return ERROR
        self._check_arity(node, name, info)
        return OBJ

    def _check_arity(self, node: ast.Call, name: str,
                     info: FunctionInfo) -> None:
        """Positional count, keyword names, and what the defaults cover.

        A STARRED argument makes the count unknowable here -- it is whatever
        the spread sequence turns out to hold -- so every count check is
        skipped and the runtime reports a mismatch instead. Skipping is not
        accepting: `f(*xs)` against a two-parameter `f` is still an error,
        just one nobody can see until `xs` exists.
        """
        if any(isinstance(a, ast.Starred) for a in node.args):
            return
        if info.kwarg is not None or any(kw.arg is None
                                         for kw in node.keywords):
            # `**kw` accepts any keyword and `f(**d)` supplies names that do
            # not exist yet, so neither the count nor the names can be checked
            # here. The runtime reports a real mismatch, which is where
            # CPython reports this shape too.
            return
        if getattr(info.node, "decorator_list", ()):
            # A DECORATED function's signature is the WRAPPER's, and that is
            # decided at run time -- `@tag('hi') def g(x)` is called as `g(3)`
            # through a `wrapper(*a)` that takes anything. Checking the `def`'s
            # own parameters here rejected calls the program makes happily.
            return
        params = [p.name for p in info.params]
        required = len(params) - len(info.defaults)
        given = list(node.args)
        by_name = {kw.arg: kw for kw in node.keywords if kw.arg}
        for kw in by_name:
            if kw not in params:
                self.sink.report(
                    error("E0068", f"{name}() got an unexpected keyword "
                                   f"argument {kw!r}")
                    .at(self._span(node))
                    .also(self._span_of_def(info), f"{name} is defined here"))
                return
            if params.index(kw) < len(given):
                self.sink.report(
                    error("E0069", f"{name}() got multiple values for "
                                   f"argument {kw!r}")
                    .at(self._span(node)))
                return
        if info.vararg is None and len(given) > len(params):
            self.sink.report(
                error("E0053", f"{name}() takes at most {len(params)} "
                               f"positional argument(s), got {len(given)}")
                .at(self._span(node))
                .also(self._span_of_def(info), f"{name} is defined here"))
            return
        filled = set(params[:len(given)]) | set(by_name)
        missing = [p for p in params[:required] if p not in filled]
        if missing:
            self.sink.report(
                error("E0053", f"{name}() missing {len(missing)} required "
                               f"argument(s): "
                               + ", ".join(repr(m) for m in missing))
                .at(self._span(node))
                .also(self._span_of_def(info), f"{name} is defined here"))

    #: Every binary operator this frontend lowers. Checked explicitly, because
    #: the failure mode of NOT checking is a traceback rather than an error:
    #: `@` and `**` both type-checked as ordinary arithmetic here and then hit
    #: a lowering table that had never heard of them. An operator missing from
    #: this set is refused by the type checker, which is where a user can see
    #: it.
    _LOWERABLE_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv,
                         ast.Mod, ast.Pow, ast.BitAnd, ast.BitOr, ast.BitXor,
                         ast.LShift, ast.RShift)

    def _binop(self, node: ast.BinOp) -> SemType:
        left, right = self._expr(node.left), self._expr(node.right)
        if not isinstance(node.op, self._LOWERABLE_BINOPS):
            self.sink.report(
                error("E0045",
                      f"operator {_op_symbol(node.op)} is not supported")
                .at(self._span(node)))
            return ERROR
        if left.is_error or right.is_error:
            return ERROR
        if not (left.is_numeric and right.is_numeric):
            self.sink.report(
                error("E0041", f"cannot apply {_op_symbol(node.op)} to "
                               f"{left} and {right}")
                .at(self._span(node.op) if hasattr(node.op, "lineno")
                    else self._span(node), _op_symbol(node.op))
                .also(self._span(node.left), str(left))
                .also(self._span(node.right), str(right)))
            return ERROR
        if isinstance(node.op, ast.Div):
            return FLOAT          # Python's `/` is always float
        if isinstance(node.op, ast.Pow):
            return self._pow(node, left, right)
        if isinstance(node.op, (ast.BitAnd, ast.BitOr, ast.BitXor,
                                ast.LShift, ast.RShift)):
            if FLOAT in (left, right):
                self.sink.report(
                    error("E0042", f"{_op_symbol(node.op)} requires integers")
                    .at(self._span(node))
                    .note("floats have no bitwise representation here"))
                return ERROR
            return INT
        return self._unify_all([left, right], node)

    def _pow(self, node: ast.BinOp, left: SemType, right: SemType) -> SemType:
        """`**`, restricted to a non-negative integer literal exponent.

        Python's `**` is not one operation. `2 ** 10` is an int, `2 ** -1` is
        the float 0.5, and `2.0 ** x` is a libm call. A statically typed
        subset cannot give one expression two types, so accepting a runtime
        exponent would mean picking one and being silently wrong about the
        other -- `2 ** n` yielding 0 for negative n is exactly the kind of
        plausible wrong answer that never gets reported as a bug.

        A literal exponent has none of that ambiguity: the result type is the
        base's, and lowering expands it to multiplications with no loop and no
        runtime at all. Anything else is refused with the expression that does
        work.
        """
        exponent = node.right
        value = int_literal(exponent)
        if value is None:
            self.sink.report(
                error("E0043", "`**` needs a literal integer exponent")
                .at(self._span(node.right), "not a literal")
                .note("`x ** n` for a runtime n is float-valued in Python "
                      "when n may be negative, and this subset gives every "
                      "expression one static type")
                .help("for a square write `x * x`; for a float power call "
                      "into a runtime yourself"))
            return ERROR
        if value < 0:
            self.sink.report(
                error("E0044", "`**` with a negative exponent is float-valued")
                .at(self._span(node.right), f"{value}")
                .help(f"write `1.0 / ({ast.unparse(node.left)} "
                      f"** {-value})`"))
            return ERROR
        return FLOAT if left is FLOAT else INT

    #: Type conversions. Not "functions" -- they are the only calls whose
    #: result type depends on which one you named rather than on a signature.
    _CONVERSIONS = {"int": INT, "float": FLOAT, "bool": BOOL}

    def _call(self, node: ast.Call) -> SemType:
        if not isinstance(node.func, ast.Name):
            self._error("E0050", "only direct calls by name are supported", node)
            return ERROR
        name = node.func.id
        if node.keywords:
            self._error("E0051", "keyword arguments are not supported", node)
        if name == "print":
            for a in node.args:
                self._arg_expr(a)
            return NONE
        if name in self._CONVERSIONS and name not in self.functions:
            want = self._CONVERSIONS[name]
            if len(node.args) != 1:
                self._error("E0054",
                            f"{name}() takes exactly one argument, "
                            f"got {len(node.args)}", node)
                for a in node.args:
                    self._expr(a)
                return want
            got = self._expr(node.args[0])
            if not (got.is_numeric or got.is_error):
                self.sink.report(
                    error("E0055", f"cannot convert {got} to {want}")
                    .at(self._span(node.args[0]), str(got)))
                return ERROR
            return want
        info = self.functions.get(name)
        if info is None:
            self.sink.report(
                error("E0052", f"call to unknown function {name!r}")
                .at(self._span(node.func))
                .note("known functions: "
                      + ", ".join(sorted(self.functions)) or "(none)"))
            for a in node.args:
                self._arg_expr(a)
            return ERROR
        params, ret = info.signature
        if len(node.args) != len(params):
            self.sink.report(
                error("E0053", f"{name}() takes {len(params)} argument(s), "
                               f"got {len(node.args)}")
                .at(self._span(node))
                .also(self._span_of_def(info), f"{name} is defined here"))
        for arg, want in zip(node.args, params):
            got = self._expr(arg)
            self._check_assignable(got, want, arg, what=f"argument to {name}()")
        for extra in node.args[len(params):]:
            self._expr(extra)
        return ret

    # ── type rules ──────────────────────────────────────────────────────────
    def _unify_all(self, types: list[SemType], at) -> SemType:
        real = [t for t in types if not t.is_error]
        if not real:
            return ERROR
        if FLOAT in real:
            return FLOAT
        if INT in real:
            return INT
        return real[0]

    def _check_assignable(self, got: SemType, want: SemType, at,
                          what: str = "value") -> None:
        if got.is_error or want.is_error or got == want:
            return
        # bool widens to int and int to float, as Python's own numeric tower
        # does. Nothing narrows implicitly: losing precision silently is how a
        # program computes the wrong answer without ever failing.
        if (want, got) in {(INT, BOOL), (FLOAT, INT), (FLOAT, BOOL)}:
            return
        d = error("E0060", f"{what} has type {got}, expected {want}") \
            .at(self._span(at), str(got))
        if (want, got) == (INT, FLOAT):
            d.help("convert explicitly with int(...) -- narrowing is never implicit")
        self.sink.report(d)

    # ── positions ───────────────────────────────────────────────────────────
    def _span(self, node) -> Span:
        return span_of(self.source, node)

    def _span_of_def(self, info: FunctionInfo) -> Span:
        return self._span(info.node)

    def _error(self, code: str, message: str, node, choices=None) -> None:
        """One diagnostic. `choices` lists what WOULD have worked, which for
        an import is the whole answer -- the set is small and knowing it is
        the difference between "no" and "here is what there is"."""
        report = error(code, message).at(self._span(node))
        if choices:
            report = report.help("available: " + ", ".join(choices))
        self.sink.report(report)


_OP_SYMBOLS = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
    ast.FloorDiv: "//", ast.Mod: "%", ast.Pow: "**",
    ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^",
    ast.LShift: "<<", ast.RShift: ">>", ast.MatMult: "@",
}


def _op_symbol(op) -> str:
    return _OP_SYMBOLS.get(type(op), type(op).__name__)
