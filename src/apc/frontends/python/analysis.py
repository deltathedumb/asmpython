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

from ...diagnostics import DiagnosticSink, SourceFile, Span, error
from ...ir import types as T

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

BY_NAME = {"int": INT, "float": FLOAT, "bool": BOOL, "None": NONE}

#: How each maps into the IR. `bool` becomes i1 so a comparison result and a
#: `bool` variable are the same thing at the machine level.
TO_IR = {INT: T.I64, FLOAT: T.F64, BOOL: T.I1, NONE: T.VOID}


@dataclass(slots=True)
class Symbol:
    name: str
    type: SemType
    span: Span
    #: Set once lowering allocates a register for it.
    register: int | None = None
    is_param: bool = False
    used: bool = False


@dataclass(slots=True)
class FunctionInfo:
    """Everything lowering needs about one function, after analysis."""

    node: ast.FunctionDef
    name: str
    params: list[Symbol]
    ret: SemType
    locals: dict[str, Symbol] = field(default_factory=dict)
    #: Expression node id -> its type. Keyed by id() because ast nodes are not
    #: hashable in a way that survives equality, and analysis and lowering walk
    #: the same tree objects.
    expr_types: dict[int, SemType] = field(default_factory=dict)

    @property
    def signature(self) -> tuple[list[SemType], SemType]:
        return [p.type for p in self.params], self.ret


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


class Analyzer:
    """Resolves names and assigns a type to every expression."""

    def __init__(self, source: SourceFile, sink: DiagnosticSink) -> None:
        self.source = source
        self.sink = sink
        self.functions: dict[str, FunctionInfo] = {}
        self.current: FunctionInfo | None = None

    # ── entry point ─────────────────────────────────────────────────────────
    def run(self, tree: ast.Module) -> dict[str, FunctionInfo]:
        defs = [n for n in tree.body if isinstance(n, ast.FunctionDef)]

        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                continue
            if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant):
                continue          # a module docstring
            self._error("E0001",
                        "only function definitions are supported at module level",
                        node)

        # Signatures first, so a function may call one defined later.
        for node in defs:
            if node.name in self.functions:
                self._error("E0002", f"function {node.name!r} is defined twice",
                            node)
            self.functions[node.name] = self._signature(node)
        for node in defs:
            self._body(self.functions[node.name])

        if "main" not in self.functions:
            self.sink.report(error("E0003", "no `main` function")
                             .help("add `def main() -> int:`"))
        return self.functions

    # ── signatures ──────────────────────────────────────────────────────────
    def _signature(self, node: ast.FunctionDef) -> FunctionInfo:
        params: list[Symbol] = []
        seen: set[str] = set()
        for arg in node.args.args:
            if arg.arg in seen:
                self._error("E0004",
                            f"duplicate parameter {arg.arg!r}", arg)
            seen.add(arg.arg)
            params.append(Symbol(arg.arg, self._annotation(arg.annotation, arg,
                                                           f"parameter {arg.arg!r}"),
                                 self._span(arg), is_param=True))
        for unsupported, what in (
            (node.args.vararg, "*args"), (node.args.kwarg, "**kwargs"),
            (node.args.kwonlyargs, "keyword-only parameters"),
            (node.args.posonlyargs, "positional-only parameters"),
        ):
            if unsupported:
                self._error("E0005", f"{what} are not supported", node)
        if node.args.defaults:
            self._error("E0006", "default arguments are not supported", node)
        if node.decorator_list:
            self._error("E0007", "decorators are not supported", node)

        ret = (self._annotation(node.returns, node, "the return type")
               if node.returns else NONE)
        return FunctionInfo(node, node.name, params, ret)

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
    def _body(self, info: FunctionInfo) -> None:
        self.current = info
        for p in info.params:
            info.locals[p.name] = p
        for stmt in info.node.body:
            self._stmt(stmt)
        self.current = None

    def _stmt(self, node) -> None:
        info = self.current
        assert info is not None
        match node:
            case ast.Expr():
                self._expr(node.value)
            case ast.Assign(targets=[ast.Name(id=name)]):
                self._bind(name, self._expr(node.value), node)
            case ast.Assign():
                self._error("E0020", "only simple `name = value` assignment "
                                     "is supported", node)
            case ast.AnnAssign(target=ast.Name(id=name)):
                declared = self._annotation(node.annotation, node, f"{name!r}")
                if node.value is not None:
                    actual = self._expr(node.value)
                    self._check_assignable(actual, declared, node)
                self._declare(name, declared, node)
            case ast.AugAssign(target=ast.Name(id=name)):
                have = self._lookup(name, node)
                self._expr(node.value)
                self._bind(name, have, node)
            case ast.Return():
                got = self._expr(node.value) if node.value else NONE
                self._check_assignable(got, info.ret, node,
                                       what="return value")
            case ast.If() | ast.While():
                self._expr(node.test)
                for s in node.body:
                    self._stmt(s)
                for s in getattr(node, "orelse", []):
                    self._stmt(s)
            case ast.For(target=ast.Name(id=name)):
                self._for_range(node, name)
            case ast.For():
                self._error("E0021", "loop target must be a plain name", node)
            case ast.Pass():
                pass
            case _:
                self._error("E0022",
                            f"unsupported statement: {type(node).__name__}", node)

    def _for_range(self, node: ast.For, name: str) -> None:
        call = node.iter
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Name)
                and call.func.id == "range"):
            self._error("E0023", "only `for ... in range(...)` is supported",
                        node.iter)
            return
        if not 1 <= len(call.args) <= 3:
            self._error("E0024", "range() takes 1 to 3 arguments", call)
        for a in call.args:
            got = self._expr(a)
            self._check_assignable(got, INT, a, what="range() argument")
        self._declare(name, INT, node)
        for s in node.body:
            self._stmt(s)
        for s in node.orelse:
            self._error("E0025", "`for ... else` is not supported", s)

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
        if sym is None:
            self.sink.report(
                error("E0031", f"undefined name {name!r}")
                .at(self._span(at))
                .help("assign it before use, or annotate it"))
            return ERROR
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

    def _binop(self, node: ast.BinOp) -> SemType:
        left, right = self._expr(node.left), self._expr(node.right)
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

    def _call(self, node: ast.Call) -> SemType:
        if not isinstance(node.func, ast.Name):
            self._error("E0050", "only direct calls by name are supported", node)
            return ERROR
        name = node.func.id
        if node.keywords:
            self._error("E0051", "keyword arguments are not supported", node)
        if name == "print":
            for a in node.args:
                self._expr(a)
            return NONE
        info = self.functions.get(name)
        if info is None:
            self.sink.report(
                error("E0052", f"call to unknown function {name!r}")
                .at(self._span(node.func))
                .note("known functions: "
                      + ", ".join(sorted(self.functions)) or "(none)"))
            for a in node.args:
                self._expr(a)
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

    def _error(self, code: str, message: str, node) -> None:
        self.sink.report(error(code, message).at(self._span(node)))


_OP_SYMBOLS = {
    ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/",
    ast.FloorDiv: "//", ast.Mod: "%", ast.Pow: "**",
    ast.BitAnd: "&", ast.BitOr: "|", ast.BitXor: "^",
    ast.LShift: "<<", ast.RShift: ">>",
}


def _op_symbol(op) -> str:
    return _OP_SYMBOLS.get(type(op), type(op).__name__)
