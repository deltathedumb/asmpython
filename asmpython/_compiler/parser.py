"""Recursive-descent parser. Every node it constructs gets a real SourcePos."""

from __future__ import annotations

from .lexer import Token, Lexer
from .errors import ErrorCode, ParseError
from . import ast_nodes as A
from . import extensions


AUG_OPS = {
    "+=": "+",
    "-=": "-",
    "*=": "*",
    "**=": "**",
    "/=": "//",
    "//=": "//",
    "%=": "%",
    "&=": "&",
    "|=": "|",
    "^=": "^",
    "<<=": "<<",
    ">>=": ">>",
}


class Parser:
    def __init__(self, tokens: list[Token], active_extensions: "frozenset[str] | None" = None) -> None:
        self.toks = tokens
        self.i = 0
        # Nested function definitions are lifted to module level here so sema
        # can resolve calls to them (closures aren't compiled, but parsing
        # must succeed so the self-hosting gauntlet can proceed).
        self._nested_funcs: list = []
        # Stack of the PARAMETER names of each function currently being parsed,
        # outermost first. A nested `def` consults it to tell an enclosing
        # scope's variable from a module-level function when it sees a name in
        # CALL position -- see `_find_free_vars`.
        self._enclosing_params: list = []
        # Nested class definitions are lifted the same way. This keeps
        # function-local helper classes (notably ctypes.Structure declarations)
        # visible to sema/codegen without modeling Python's local class scope.
        self._nested_classes: list = []
        # Every name `import X [as Y]` / `from X import ... [as Y]` binds at
        # module scope, accumulated as imports are parsed (which precedes any
        # nested def referencing them, the normal case). _find_free_vars
        # subtracts this set too: `A.IntLit` inside a nested function
        # references the *module alias* `A`, a compile-time namespace lookup
        # with no runtime storage location, not a value to capture into a
        # closure -- without this, _var_mem crashes with "undefined variable
        # A" trying to find A's (nonexistent) memory slot when building the
        # closure's captured-value array. Confirmed via the self-host
        # codegen probe: every file that does `from . import ast_nodes as A`
        # and then has a nested def referencing `A.*` hit this.
        self._import_bound_names: set = set()
        # Names bound by `import x` / `from x import y` seen so far. Used by
        # `_eat_decorators` to tell stdlib decorators (e.g. `@lru_cache(...)`,
        # `@unique`, currently informational no-ops) apart from decorators on
        # user-defined values (e.g. `@iset.instruction("ADD")`), which get
        # desugared into a real call instead.
        self._imported_names: "set[str]" = set()
        # Desugared general-decorator application exprs awaiting the next
        # top-level def to attach to. Populated by `_eat_decorators`, drained
        # by `parse()` right after the decorated def is parsed.
        self._pending_decorator_exprs: list = []
        self._deco_tmp_counter = 0
        # Synthetic-temp counter for desugaring a chained assignment led by
        # an attribute/subscript target (`self.a = self.b = value`) -- see
        # `_desugar_chained_assign`. A separate counter from
        # `_deco_tmp_counter` above (different desugaring, own naming
        # convention: `__chain_tmp_N`) even though both exist to guarantee
        # a shared RHS is evaluated exactly once across several generated
        # statements.
        self._chain_tmp_counter = 0
        # `@readonly(name, ...)`'s captured param names, populated by
        # `_eat_decorators` and read by the immediately-following
        # `_parse_funcdef` call (every caller does exactly this sequence).
        self._pending_readonly_params: list = []
        # `@access(PolicyName)`'s captured bare-name argument (e.g. "Public"),
        # populated by `_eat_decorators` and read by the immediately-
        # following `_parse_funcdef` call, same sequencing as
        # `_pending_readonly_params` above. None when no `@access(...)`
        # decorator preceded the def.
        self._pending_access_policy: "str | None" = None
        # `@abi(ABIName)`'s captured bare-name argument (e.g. "C"), same
        # sequencing/lifetime as `_pending_access_policy` above. None when no
        # explicit `@abi(...)` decorator preceded the def.
        self._pending_abi_name: "str | None" = None
        # Per-Parser compiler-extension state (const and whatever future
        # extensions register contextual keywords). Fresh per instance --
        # see extensions.py's module docstring for the isolation guarantees
        # this gives for free. Extensions are activated for the *whole*
        # compile run via the `--ext` CLI flag (never by in-source
        # directives -- a compiled program's grammar never changes without
        # the invoker's explicit opt-in), so activation happens once, right
        # here, before any token is parsed. `_suite_depth` counts how many
        # nested suites (function/class/if/loop/try/with bodies) we're
        # currently inside, so `const` (while `constants` is active) can be
        # rejected uniformly outside module scope without checking at each
        # of `_parse_block`'s many call sites individually.
        self.ext_ctx = extensions.ExtensionContext()
        for _ext_name in active_extensions or ():
            self.ext_ctx.activate(_ext_name)
        self._suite_depth = 0

    # ---- helpers -----------------------------------------------------------

    def _peek(self, off: int = 0) -> Token:
        return self.toks[self.i + off]

    def _eat(self) -> Token:
        t = self.toks[self.i]
        self.i += 1
        return t

    def _expect(self, kind: str, value: str = None) -> Token:
        t = self.toks[self.i]
        if t.kind != kind or (value is not None and t.value != value):
            want = f"{kind} {value!r}" if value is not None else kind
            raise ParseError(f"expected {want}, got {t.kind} {t.value!r}", t.pos, ErrorCode.P_EXPECTED_TOKEN)
        self.i += 1
        return t

    def _check(self, kind: str, value: str = None) -> bool:
        t = self._peek()
        if t.kind != kind:
            return False
        return value is None or t.value == value

    def _check_any_op(self, *values: str) -> bool:
        t = self._peek()
        return t.kind == "OP" and t.value in values

    def _skip_newlines(self) -> None:
        while self._check("NEWLINE"):
            self._eat()

    # ---- generic AST walk ---------------------------------------------------
    # `_find_free_vars` and `_propagate_transitive_free_vars` below each need
    # to "visit every expression/statement reachable from a function body,
    # doing X at each one" for two different X's (collecting Name references
    # for free-variable analysis; collecting Call.func names for transitive
    # free-var propagation between mutually-calling lifted siblings) --
    # previously two structurally-identical pairs of hand-written dispatch
    # chains (~35-38 of this compiler's ~64 AST node types each), differing
    # only in what each did at a visited node, not in which nodes it visited.
    # `_walk_expr`/`_walk_stmt` are the single shared recursive descent
    # behind both: every expression/statement node this compiler's AST can
    # produce that has a child expression or sub-statement list, in the same
    # traversal order the original hand-written pairs used (load-bearing for
    # `_find_free_vars`'s comprehension-scoping stack, which pushes/pops
    # bound names around a specific sub-visit and depends on visiting `iter`
    # before `elt`/`cond`, matching real Python's own scoping rule that a
    # comprehension's outermost `for x in <iter>` runs in the *enclosing*
    # scope). `on_expr` is called on every expression node visited (before
    # recursing into its children, unless it returns truthy -- see
    # `_walk_expr`'s own docstring); callers needing scoping/suppression
    # logic (only `_find_free_vars` does) implement it inside their own
    # callback instead of `_walk_expr` knowing anything about scoping rules
    # itself -- keeping "what the tree looks like" (here, shared) separate
    # from "what a caller does at each node" (caller-specific).
    def _walk_expr(self, node, on_expr) -> None:
        """Visit `node` and every expression reachable from it, calling
        `on_expr(node)` for each -- true expression nodes ONLY (Name,
        BinOp, Call, ListLit, ...), never a statement. `_walk_stmt` below
        is what routes a statement's own expression-shaped fields (an
        Assign's `.value`, a TupleAssign's `.targets`/`.values`, ...) into
        this function one field at a time; a statement itself is never
        passed in here, so `on_expr` never fires on one by accident (the
        original hand-written pair of expression-walking functions never
        called their own equivalent of `on_expr` for a bare statement node
        either -- only real expression shapes).

        If `on_expr(node)` returns a truthy value, `_walk_expr` stops --
        it does NOT also recurse into `node`'s own children afterward. This
        is for callers whose per-node logic isn't a plain "visit every
        child the same way" walk (only `_find_free_vars`'s comprehension-
        scoping needs this: it must push bound names, walk `elt`/`cond` FOR
        ITSELF in a specific order, then pop -- if `_walk_expr` also walked
        those same children afterward via its own generic Comprehension
        case, `elt`'s references would be visited a second time, outside
        the scoping push/pop, wrongly counting a comprehension's own loop
        variable as a free-variable reference). Every other caller's
        callback returns a falsy value (or nothing) and gets the default
        full recursion, same as if this parameter didn't exist.
        """
        if node is None:
            return
        if on_expr(node):
            return
        if isinstance(node, A.BinOp):
            self._walk_expr(node.left, on_expr)
            self._walk_expr(node.right, on_expr)
        elif isinstance(node, A.UnaryOp):
            self._walk_expr(node.operand, on_expr)
        elif isinstance(node, A.Call):
            for a in node.args:
                self._walk_expr(a, on_expr)
            for _kw_name, kw_val in (node.kwargs or []):
                self._walk_expr(kw_val, on_expr)
        elif isinstance(node, A.MethodCall):
            self._walk_expr(node.obj, on_expr)
            for a in node.args:
                self._walk_expr(a, on_expr)
            for _kw_name, kw_val in (node.kwargs or []):
                self._walk_expr(kw_val, on_expr)
        elif isinstance(node, A.Attr):
            self._walk_expr(node.obj, on_expr)
        elif isinstance(node, A.Subscript):
            self._walk_expr(node.obj, on_expr)
            self._walk_expr(node.index, on_expr)
        elif isinstance(node, A.Slice):
            self._walk_expr(node.start, on_expr)
            self._walk_expr(node.stop, on_expr)
            self._walk_expr(node.step, on_expr)
        elif isinstance(node, A.IfExp):
            self._walk_expr(node.test, on_expr)
            self._walk_expr(node.body, on_expr)
            self._walk_expr(node.orelse, on_expr)
        elif isinstance(node, A.NamedExpr):
            self._walk_expr(node.value, on_expr)
        elif isinstance(node, A.BoolOp):
            self._walk_expr(node.left, on_expr)
            self._walk_expr(node.right, on_expr)
        elif isinstance(node, A.Compare):
            for op in node.operands:
                self._walk_expr(op, on_expr)
        elif isinstance(node, (A.ListLit, A.TupleLit, A.SetLit)):
            for e in node.elems:
                self._walk_expr(e, on_expr)
        elif isinstance(node, A.DictLit):
            for k in node.keys:
                self._walk_expr(k, on_expr)
            for v in node.values:
                self._walk_expr(v, on_expr)
        elif isinstance(node, A.FString):
            for seg in node.segments:
                self._walk_expr(seg, on_expr)
        elif isinstance(node, A.Starred):
            self._walk_expr(node.value, on_expr)
        elif isinstance(node, A.Lambda):
            self._walk_expr(node.body, on_expr)
        elif isinstance(node, A.Comprehension):
            # `iter` (the outermost `for x in <iter>`) runs in the
            # *enclosing* scope in real Python -- visited first, same order
            # the original hand-written walk used, so a caller's own
            # comprehension-scoping callback (only `_find_free_vars` has
            # one) can rely on this ordering.
            self._walk_expr(node.iter, on_expr)
            self._walk_expr(node.elt, on_expr)
            self._walk_expr(node.cond, on_expr)
            for it in node.extra_for_iters:
                self._walk_expr(it, on_expr)
            for c in node.extra_for_conds:
                self._walk_expr(c, on_expr)
        elif isinstance(node, A.DictComprehension):
            self._walk_expr(node.iter, on_expr)
            self._walk_expr(node.key, on_expr)
            self._walk_expr(node.value, on_expr)
            self._walk_expr(node.cond, on_expr)

    def _walk_stmt(self, stmts: list, on_expr) -> None:
        """Visit every expression reachable from a statement LIST (an `If`'s
        `then`/`orelse`, a function body, ...): recurse into nested
        statement lists (loop/branch/try/match bodies), and for every other
        statement type, hand each of its own expression-shaped fields to
        `_walk_expr` one at a time (an `Assign`'s `.value`, a `TupleAssign`'s
        per-target Subscript/Attr expressions and `.values`, ...) -- the
        statement-level half of the shared walk `_walk_expr`'s own docstring
        describes. `on_expr` is the same per-expression-node callback
        `_walk_expr` takes; passed straight through so a caller implements
        its logic once, regardless of whether the node it fires on came
        from a statement-level or expression-level field.
        """
        for s in stmts:
            if isinstance(s, A.If):
                self._walk_expr(s.test, on_expr)
                self._walk_stmt(s.then, on_expr)
                self._walk_stmt(s.orelse or [], on_expr)
            elif isinstance(s, A.While):
                self._walk_expr(s.test, on_expr)
                self._walk_stmt(s.body, on_expr)
                self._walk_stmt(s.orelse or [], on_expr)
            elif isinstance(s, A.For):
                for ra in s.range_args:
                    self._walk_expr(ra, on_expr)
                self._walk_expr(s.iter, on_expr)
                self._walk_stmt(s.body, on_expr)
                self._walk_stmt(s.orelse or [], on_expr)
            elif isinstance(s, A.With):
                self._walk_expr(s.expr, on_expr)
                self._walk_stmt(s.body, on_expr)
            elif isinstance(s, A.Try):
                self._walk_stmt(s.body, on_expr)
                self._walk_stmt(s.handler, on_expr)
                for _types, _bind, hbody in (getattr(s, "extra_handlers", None) or []):
                    self._walk_stmt(hbody, on_expr)
                self._walk_stmt(getattr(s, "else_body", None) or [], on_expr)
                self._walk_stmt(getattr(s, "finally_body", None) or [], on_expr)
            elif isinstance(s, A.Match):
                self._walk_expr(s.subject, on_expr)
                for _pattern, guard, body in s.cases:
                    self._walk_expr(guard, on_expr)
                    self._walk_stmt(body, on_expr)
            elif isinstance(s, A.Assign):
                self._walk_expr(s.value, on_expr)
            elif isinstance(s, A.AugAssign):
                self._walk_expr(s.value, on_expr)
            elif isinstance(s, A.MultiAssign):
                self._walk_expr(s.value, on_expr)
            elif isinstance(s, A.TupleAssign):
                for t in s.targets:
                    self._walk_expr(t, on_expr)
                for v in s.values:
                    self._walk_expr(v, on_expr)
            elif isinstance(s, A.AttrAssign):
                self._walk_expr(s.obj, on_expr)
                self._walk_expr(s.value, on_expr)
            elif isinstance(s, A.IndexAssign):
                self._walk_expr(s.target, on_expr)
                self._walk_expr(s.value, on_expr)
            elif isinstance(s, A.Del):
                self._walk_expr(s.target, on_expr)
            elif isinstance(s, A.Return):
                self._walk_expr(s.value, on_expr)
            elif isinstance(s, A.Raise):
                self._walk_expr(s.value, on_expr)
            elif isinstance(s, A.YieldStmt):
                self._walk_expr(s.value, on_expr)
            elif isinstance(s, A.ExprStmt):
                self._walk_expr(s.expr, on_expr)
            # Every other statement type (Pass, Break, Continue, Global,
            # Nonlocal, Import, FromImport, ...) has neither a nested
            # statement list nor an expression field relevant here -- no
            # on_expr calls, same as they contributed nothing to either
            # original hand-written walk.

    def _find_free_vars(self, fdef: A.FuncDef) -> tuple:
        """Return (free_vars, nonlocal_vars) for fdef.

        free_vars: outer-scope names referenced in fdef's body that are not
        locally bound (params / non-nonlocal assigns).
        nonlocal_vars: subset of free_vars declared `nonlocal` in the body;
        these must be captured by reference (boxed cell) so mutations are shared."""
        # Explicit `: list` read, not `set(fdef.params)` directly: fdef is
        # this function's own A.FuncDef parameter, an external/opaque type to
        # sema, so fdef.params reads as "any" -- set()'s codegen treats an
        # "any"-typed argument as already dict-shaped (sets are dict-backed)
        # and hands it straight back instead of iterating it as the list it
        # actually is here, same bug class fixed elsewhere this session for
        # nonlocal_vars/kwargs. Confirmed via a selfhost rebuild: this made
        # local_names not actually exclude a lifted nested function's own
        # parameters from its free_vars, corrupting the closure-lifting
        # param-prepend in sema and shifting every real parameter out of
        # position ("undefined variable" for an in-scope parameter name).
        params_list: list = fdef.params
        local_names: set = set(params_list)
        if fdef.vararg:
            local_names.add(fdef.vararg)
        if fdef.kwarg:
            local_names.add(fdef.kwarg)
        # Collect names declared `nonlocal` — exclude them from local_names so
        # they appear in free_vars even when assigned inside the body.
        nonlocal_names: set = set()
        def _collect_nonlocal(stmts: list) -> None:
            for s in stmts:
                if isinstance(s, A.Nonlocal):
                    nonlocal_names.update(s.names)
        _collect_nonlocal(fdef.body)
        # Collect names that are assigned (bound) inside the body (skip
        # nonlocal). NOT built on the shared _walk_stmt walker: this needs
        # STATEMENT-shape recursion tuned to "which statement types create
        # an ordinary local-variable binding" (Assign/AugAssign/For/If/
        # MultiAssign/TupleAssign/While/Try), a different, narrower
        # question than "every expression reachable from here" -- and
        # deliberately does NOT recurse into A.Match (match-pattern
        # captures are bound through sema's own separate pattern-
        # compilation pass, not this local_names set; verified this
        # doesn't create the opposite bug -- a pattern-captured name
        # wrongly excluded from a later free-var reference -- via a real
        # nested-function repro before leaving this as-is).
        def _collect_assigned(stmts: list) -> None:
            for s in stmts:
                if isinstance(s, A.Assign):
                    if s.target not in nonlocal_names:
                        local_names.add(s.target)
                elif isinstance(s, A.AugAssign):
                    if s.target not in nonlocal_names:
                        local_names.add(s.target)
                elif isinstance(s, A.For):
                    if isinstance(s.var, str) and s.var not in nonlocal_names:
                        local_names.add(s.var)
                    for _t in (s.targets or []):
                        if isinstance(_t, str) and _t not in nonlocal_names:
                            local_names.add(_t)
                        elif isinstance(_t, list):
                            for _nm in _t:
                                if isinstance(_nm, str) and _nm not in nonlocal_names:
                                    local_names.add(_nm)
                    _collect_assigned(s.body)
                elif isinstance(s, A.If):
                    _collect_assigned(s.then)
                    _collect_assigned(s.orelse)
                elif isinstance(s, A.MultiAssign):
                    for _nm in s.targets:
                        if isinstance(_nm, str) and _nm not in nonlocal_names:
                            local_names.add(_nm)
                elif isinstance(s, A.TupleAssign):
                    for _t in s.targets:
                        if isinstance(_t, A.Name):
                            if _t.name not in nonlocal_names:
                                local_names.add(_t.name)
                        elif isinstance(_t, A.StarTarget):
                            if _t.name not in nonlocal_names:
                                local_names.add(_t.name)
                elif isinstance(s, A.While):
                    _collect_assigned(s.body)
                elif isinstance(s, A.Try):
                    _collect_assigned(s.body)
                    _collect_assigned(s.handler)
                    for _et, _eb, _eh in (getattr(s, "extra_handlers", None) or []):
                        _collect_assigned(_eh)
                    _collect_assigned(getattr(s, "else_body", None) or [])
                    _collect_assigned(getattr(s, "finally_body", None) or [])
                    if getattr(s, "bind_name", None) and s.bind_name not in nonlocal_names:
                        local_names.add(s.bind_name)
        _collect_assigned(fdef.body)
        # Collect all Name references in the body -- built on the shared
        # _walk_stmt/_walk_expr walker (see their own docstrings), unlike
        # _collect_assigned above: this question really is "every
        # expression reachable from here," including inside a `match`
        # statement's subject/guards/case bodies (previously NOT walked at
        # all -- a real, live bug this fix corrects: a nested function
        # referencing an outer variable only through a `match` subject
        # never saw it as a free variable, so closure-lifting never passed
        # it in, and the compiled function silently read garbage for it.
        # Confirmed via a real repro compiled and run: `match outer:
        # case 1: return 100 / case _: return 200` inside a nested function
        # printed 200 for outer=1 instead of CPython's 100, before this
        # fix).
        referenced: set = set()
        # Names currently bound by an enclosing comprehension's own `for`
        # clause(s) — Python scopes these to the comprehension itself, not
        # the surrounding function, so a `Name` read while this is non-empty
        # must not be recorded as a free-var reference. A list-as-stack
        # (rather than reassigning a set) so nested comprehensions compose
        # without needing `nonlocal`. Comprehension/DictComprehension
        # return True from on_expr to suppress _walk_expr's own generic
        # recursion into their children (see _walk_expr's docstring) --
        # this callback recurses into them itself, in the specific order
        # (iter, then push scope, then elt/cond/extras, then pop) real
        # Python's own comprehension scoping needs.
        comp_suppressed: list = []
        # Parameters of every function enclosing this one. A name in CALL
        # position is a bare string on A.Call, not an A.Name, so the walk below
        # never saw it -- which meant a nested function that only ever CALLS a
        # captured callable had NO free variables recorded:
        #
        #     def deco(f):
        #         def wrap(x):
        #             return f(x)      # `f` was invisible here
        #         return wrap
        #
        # so `f` was neither captured nor passed in, and sema reported
        # "undefined function 'f'" -- the whole decorator idiom. Recording
        # EVERY callee would wrongly capture module-level function names, so
        # only a name that is a PARAMETER of an enclosing function counts:
        # that is exactly the set that can only have come from an outer scope.
        _outer_params: set = set()
        for _frame in self._enclosing_params:
            _outer_params.update(_frame)

        def on_expr(node) -> bool:
            if isinstance(node, A.Name):
                if node.name not in comp_suppressed:
                    referenced.add(node.name)
                return False
            if isinstance(node, A.Call):
                _cf = node.func
                if (
                    isinstance(_cf, str)
                    and _cf in _outer_params
                    and _cf not in comp_suppressed
                ):
                    referenced.add(_cf)
            if isinstance(node, A.Comprehension):
                self._walk_expr(node.iter, on_expr)
                _comp_vars: list = []
                if node.var:
                    _comp_vars.append(node.var)
                for _t in (node.targets or []):
                    if isinstance(_t, str):
                        _comp_vars.append(_t)
                for _ev in (node.extra_for_vars or []):
                    if _ev:
                        _comp_vars.append(_ev)
                for _etl in (node.extra_for_targets or []):
                    for _t in _etl:
                        if isinstance(_t, str):
                            _comp_vars.append(_t)
                for _cv in _comp_vars:
                    comp_suppressed.append(_cv)
                self._walk_expr(node.elt, on_expr)
                self._walk_expr(node.cond, on_expr)
                for it in node.extra_for_iters:
                    self._walk_expr(it, on_expr)
                for c in node.extra_for_conds:
                    self._walk_expr(c, on_expr)
                for _cv in _comp_vars:
                    comp_suppressed.remove(_cv)
                return True
            if isinstance(node, A.DictComprehension):
                self._walk_expr(node.iter, on_expr)
                _comp_vars = []
                if node.var:
                    _comp_vars.append(node.var)
                for _t in (node.targets or []):
                    if isinstance(_t, str):
                        _comp_vars.append(_t)
                for _cv in _comp_vars:
                    comp_suppressed.append(_cv)
                self._walk_expr(node.key, on_expr)
                self._walk_expr(node.value, on_expr)
                self._walk_expr(node.cond, on_expr)
                for _cv in _comp_vars:
                    comp_suppressed.remove(_cv)
                return True
            return False
        self._walk_stmt(fdef.body, on_expr)

        # `x += 1` READS x before writing it, but an AugAssign's target is a
        # bare string on the node, not an A.Name, so the expression walk above
        # never sees it -- the same shape as A.Call.func, fixed just above for
        # exactly the same reason.
        #
        # Restricted to names declared `nonlocal`, which is the only case that
        # needs it: _collect_assigned already excludes those from local_names,
        # so without this the name is neither local NOR free and sema reports
        # "augmented assignment to undefined variable". A plain `x = x + 1`
        # worked because its right-hand `x` IS an A.Name and got recorded.
        #
        # Recording EVERY AugAssign target instead looks safe -- a real local
        # is in local_names and subtracts out -- but is not, and cost 11 cases
        # against 1 fixed. A GLOBAL augmented inside a lifted function
        # (`count += 1` with no `global` declaration) is never plainly assigned
        # there, so it is not in local_names either: it became a free variable,
        # got prepended as a parameter, and the enclosing scope had nothing to
        # pass. Broke str concat/repeat, deque, gc, atexit, signal, subprocess
        # and import_binary.
        # Recursion mirrors _collect_assigned above statement for statement --
        # same node shapes, same attribute names (A.If is .then/.orelse, not
        # .body). A generic getattr walk silently missed `if`, which is where
        # a conditional `x += 1` lives.
        def _collect_augassign_targets(stmts: list) -> None:
            for s in stmts:
                if isinstance(s, A.AugAssign):
                    if isinstance(s.target, str) and s.target in nonlocal_names:
                        referenced.add(s.target)
                elif isinstance(s, A.For):
                    _collect_augassign_targets(s.body)
                elif isinstance(s, A.If):
                    _collect_augassign_targets(s.then)
                    _collect_augassign_targets(s.orelse)
                elif isinstance(s, A.While):
                    _collect_augassign_targets(s.body)
                elif isinstance(s, A.Try):
                    _collect_augassign_targets(s.body)
                    for _eh in (s.handler or []):
                        if isinstance(_eh, list):
                            _collect_augassign_targets(_eh)
                    _collect_augassign_targets(getattr(s, "else_body", None) or [])
                    _collect_augassign_targets(getattr(s, "finally_body", None) or [])

        _collect_augassign_targets(fdef.body)

        # Free vars = referenced names that are not locally bound.
        BUILTINS = {
            "print", "len", "range", "str", "int", "float", "bool", "list",
            "dict", "tuple", "set", "abs", "min", "max", "sum", "sorted",
            "isinstance", "type", "enumerate", "zip", "map", "filter",
            "hasattr", "getattr", "setattr", "repr", "hash", "id",
            "StopIteration", "ValueError", "TypeError", "KeyError",
            "IndexError", "AttributeError", "RuntimeError", "Exception",
            "ZeroDivisionError", "NotImplementedError", "OverflowError",
            "True", "False", "None",
        }
        free = [
            n for n in sorted(referenced - local_names)
            if n not in BUILTINS and n not in self._import_bound_names
        ]
        nl = [n for n in free if n in nonlocal_names]
        return free, nl

    def parse(self) -> A.Module:
        funcs: list[A.FuncDef] = []
        classes: list[A.ClassDef] = []
        enums: list[A.EnumDecl] = []
        interfaces: list[A.InterfaceDecl] = []
        body: list = []
        self._skip_newlines()
        while not self._check("EOF"):
            # `assign_decorators` extension: `@decorator` above an
            # assignment. Must be detected BEFORE `_eat_decorators()` runs
            # below -- that call unconditionally consumes any leading `@`
            # line, and a bare (non-informational, non-imported) decorator
            # name would fall into its "general decorator expression"
            # branch, which only knows how to apply to a following `def`.
            # `_looks_like_assign_decorator` peeks (not `_eat_decorators`'
            # own always-consuming loop), so a plain `@decorator` above
            # `def`/`class` is completely unaffected -- this only fires
            # when the extension is active at all.
            if self.ext_ctx.is_active("assign_decorators") and self._looks_like_assign_decorator():
                body.extend(self._parse_assign_decorator_stmt())
                self._skip_newlines()
                continue
            # Decorators are accepted; most are dropped (their semantics, e.g.
            # `@dataclass` synthesising __init__, aren't modelled). The one we
            # act on is `@assembly_func`, which marks a raw-NASM function body.
            decorators = self._eat_decorators()
            pending = self._pending_decorator_exprs
            self._pending_decorator_exprs = []
            # `async def` at MODULE level. `_parse_funcdef` already consumes a
            # leading `async` and records `is_async`, and `_parse_stmt` relies on
            # exactly that -- it leaves BOTH tokens in place for its `def` arm.
            # This loop only ever checked for `def`, so a module-level
            # `async def f(): ...` was "[P001] unexpected token KEYWORD 'async'"
            # while the identical definition nested inside a function parsed
            # fine (tests/cases/async_def_parse.py).
            if self._check("KEYWORD", "def") or (
                self._check("KEYWORD", "async")
                and self._peek(1).kind == "KEYWORD"
                and self._peek(1).value == "def"
            ):
                fdef = self._parse_funcdef(decorators=decorators)
                funcs.append(fdef)
                body.extend(
                    self._desugar_function_decorators(fdef, pending)
                )
            elif self._check("KEYWORD", "class"):
                cdef = self._parse_classdef(decorators=decorators)
                classes.append(cdef)
                # Apply a general class decorator (`@register_object_class(
                # "somnia.Game") class Game: ...`) at module init, exactly like
                # the def case above: emit `__deco_tmp = register_object_class(
                # "somnia.Game")` then `__deco_tmp(Game)` for its side effect.
                # The class is passed by name -- a bare class name is a `type`
                # value (its RTTI id), so a registration decorator that stores
                # `cls`/sets `cls.__attr__` and returns it works unchanged.
                # Previously these exprs were parsed and stashed but never
                # applied for a class, so registration-pattern decorators (a
                # whole object-type registry) silently did nothing.
                body.extend(self._desugar_decorator_exprs(pending, cdef.name, cdef.pos))
            elif self._check("NAME", "final") and self._looks_like_final_class():
                classes.append(self._dispatch_final_class())
            elif self._check("NAME", "sealed") and self._looks_like_sealed_class():
                classes.append(self._dispatch_sealed_class())
            elif self._check("NAME", "enum") and self._looks_like_enum_decl():
                enums.append(self._dispatch_enum_decl())
            elif self._check("NAME", "interface") and self._looks_like_interface_decl():
                interfaces.append(self._dispatch_interface_decl())
            else:
                # A statement may expand to SEVERAL -- `import a, b` becomes one
                # A.Import each, and a chained assignment led by an attribute or
                # subscript target desugars the same way. _parse_block already
                # flattens for a nested suite; this loop did not, so a
                # list-valued statement reached sema as a nested list and became
                # "[E145] internal: unhandled stmt list". Same dispatch-parity
                # shape as the decorator and `async def` gaps: handled in one of
                # the two statement collectors and not the other.
                _stmt = self._parse_stmt()
                if isinstance(_stmt, list):
                    body.extend(_stmt)
                else:
                    body.append(_stmt)
            self._skip_newlines()
        # Add any nested functions parsed inside function bodies (lifted to
        # module level so calls to them resolve in sema).
        Parser._propagate_transitive_free_vars(self._nested_funcs)
        funcs.extend(self._nested_funcs)
        classes.extend(self._nested_classes)
        return A.Module(funcs=funcs, body=body, classes=classes, enums=enums, interfaces=interfaces)

    @staticmethod
    def _collect_called_names(stmts: list, out: set) -> None:
        """Collect every `A.Call.func` name reachable inside a statement
        list -- built on the shared `_walk_stmt`/`_walk_expr` walker (see
        their own docstrings) rather than a second hand-written dispatch
        chain structurally identical to `_find_free_vars`'s own expression
        walk, just for a different purpose (here: propagating free
        variables between mutually-calling lifted sibling functions, see
        `_propagate_transitive_free_vars`). No comprehension-scoping
        concern here (unlike `_find_free_vars`'s `on_expr`) -- a `Call`
        found anywhere, including inside a comprehension, is a real call
        this function's own body makes, regardless of which names are
        comprehension-scoped.

        A throwaway `Parser([])` instance is used only because `_walk_stmt`/
        `_walk_expr` are ordinary (non-static) methods -- neither reads any
        instance state, so an empty token list is fine; this method itself
        stays `@staticmethod` since every real caller already has no `Parser`
        instance of its own handy (see `_propagate_transitive_free_vars`,
        also `@staticmethod`).
        """
        def on_expr(node) -> bool:
            if isinstance(node, A.Call):
                out.add(node.func)
            return False
        Parser([])._walk_stmt(stmts, on_expr)

    @staticmethod
    def _propagate_transitive_free_vars(nested_funcs: list) -> None:
        """`_find_free_vars` (used for every nested `def`) only sees variables
        a function references *directly* — it has no notion that calling a
        sibling nested function (also lifted to module scope) might require
        forwarding free vars *that sibling* needs but the caller never
        touches itself. E.g. a `walk(stmts)` helper that does nothing but
        `for s in stmts: walk_expr(s)` has no free vars of its own, but
        `walk_expr` might close over `found`/`results` from their shared
        enclosing scope — `walk` must still receive and forward them, since
        sema prepends free vars as the lifted function's leading params and
        codegen passes a direct-by-name call's free vars from the *caller's*
        own scope (see codegen.py's `_gen_call`).

        Fixed-point over the whole nested-function batch: repeatedly union in
        any callee's free vars (minus names the caller already locally binds,
        i.e. params/vararg/kwarg) until nothing changes. Bounded by the
        number of nested functions, so this always terminates."""
        # Explicit `dict` annotation: an unannotated `{}` literal's value
        # kind defaults to "int" (sema can't see f's real type -- A.FuncDef
        # is an external/opaque type), so `by_name.get(callee_name)`'s
        # result read back as "int"-typed instead of opaque/instance. That
        # made every `callee.free_vars` / `callee.nonlocal_vars` attribute
        # read below dispatch through codegen's int-receiver path instead
        # of the real instance-attribute (dict_get_default) path, reading
        # garbage. Confirmed via gdb: crashed inside this exact function
        # for any two nested functions that call each other.
        by_name: dict = {}
        for f in nested_funcs:
            by_name[f.name] = f
        changed = True
        while changed:
            changed = False
            for f in nested_funcs:
                called: set = set()
                Parser._collect_called_names(f.body, called)
                # Same fix as _find_free_vars: explicit `: list` read, not
                # set(f.params) directly (f.params reads "any"-typed here).
                f_params: list = f.params
                own_locals: set = set(f_params)
                if f.vararg:
                    own_locals.add(f.vararg)
                if f.kwarg:
                    own_locals.add(f.kwarg)
                # Same fix again: explicit `: list` reads for f's own
                # free_vars/nonlocal_vars, not direct attribute access --
                # f.free_vars / f.nonlocal_vars read as "any" (f: A.FuncDef,
                # an opaque external type), which makes `fv in f.free_vars`
                # compile as an int-comparison membership test (the el_t
                # inference for a plain A.Attr falls to its "int" default)
                # instead of a real string comparison, so it never actually
                # matched and every call kept re-appending the same free var.
                f_free_vars: list = f.free_vars
                f_nonlocal_vars: list = f.nonlocal_vars
                for callee_name in called:
                    callee = by_name.get(callee_name)
                    if callee is None or callee is f:
                        continue
                    callee_free_vars: list = callee.free_vars
                    callee_nonlocal_vars: list = callee.nonlocal_vars
                    for fv in callee_free_vars:
                        if fv in own_locals or fv in f_free_vars:
                            continue
                        f_free_vars.append(fv)
                        if fv in callee_nonlocal_vars and fv not in f_nonlocal_vars:
                            f_nonlocal_vars.append(fv)
                        changed = True

    # Decorator names the parser treats specially.
    _ASM_DECORATOR = "assembly_func"
    # Bare leading names that stay informational (parsed-and-discarded) even
    # though they're not one of the dotted-suffix forms below. These are
    # decorators on values the compiler doesn't model as real callables
    # (dataclass/property machinery, stdlib decorators with no codegen
    # backing them yet) — actually calling them would either be meaningless
    # (`dataclass`, `staticmethod`, `classmethod`, `property` already get
    # bespoke handling elsewhere) or fail to type-check (`lru_cache`/`unique`
    # style stdlib decorators that exist as importable names but aren't
    # modeled as real higher-order functions).
    _INFORMATIONAL_DECORATORS = {
        "dataclass", "staticmethod", "classmethod", "property",
        "abstractmethod", "assembly_func",
        # Wave-1 compiler-extension decorators (access/immutable/final).
        # Parsed unconditionally like every other informational decorator --
        # whether they're *meaningful* (i.e. the owning extension is active)
        # is a semantic-phase question (SemaAnalyzer._ext_active), not a
        # parse-phase one, so a stray `@private` with `--ext access` absent
        # parses fine and is caught later with a precise E087 diagnostic.
        "private", "protected", "public", "immutable", "final",
        # Wave-2 compiler-extension decorators.
        "readonly", "mutable_params", "must_use", "overload",
    }

    def _eat_decorators(self) -> list[str]:
        """Consume zero or more `@expr` lines preceding a def/class.

        Returns the leading dotted name of each decorator (e.g. `assembly_func`
        for `@assembly_func` or `@assembly_func(symbol="x")`). Callers inspect
        the list for decorators that change codegen; the rest are informational.

        A decorator whose leading name isn't one of the known-informational
        ones above, isn't a dotted property-accessor form, and isn't a name
        bound by `import`/`from ... import` is treated as a *general*
        decorator on a user-defined value (e.g. `@iset.instruction("ADD")`,
        where `iset` is a plain module-level variable). For those, the full
        expression is parsed for real and stashed in
        `self._pending_decorator_exprs` for `parse()` to desugar into an
        actual call once the decorated def is known.
        """
        names: list[str] = []
        self._pending_readonly_params: list = []
        self._pending_access_policy: "str | None" = None
        self._pending_abi_name: "str | None" = None
        while self._check("OP", "@"):
            self._eat()
            # First NAME (optionally dotted) is the decorator's identity.
            name = None
            dotted_suffix = False
            if self._check("NAME"):
                name = self._peek().value
                # `@<prop>.setter` / `.getter` / `.deleter`: capture the
                # dotted accessor form (e.g. "x.setter") so sema can match
                # it against the property getter method of the same name.
                # `@<handle>.imported` (import_binary dynamic-loading
                # handles) needs the same dotted capture, but `<handle>` is
                # an arbitrary variable name rather than a fixed property
                # name — sema resolves which import_binary() call `name`
                # came from separately; the parser only needs to keep the
                # ".imported" suffix instead of discarding it.
                if (
                    self._peek(1).kind == "OP"
                    and self._peek(1).value == "."
                    and self._peek(2).kind == "NAME"
                    and self._peek(2).value in ("setter", "getter", "deleter", "imported")
                ):
                    name = f"{name}.{self._peek(2).value}"
                    dotted_suffix = True
            # `@readonly(param_name, ...)`: unlike every other informational
            # decorator, its parenthesized args are real data (the parameter
            # names to lock), not just skipped syntax -- capture them into
            # self._pending_readonly_params instead of falling through to
            # the generic "skip the rest of the line" branch below.
            if (
                name == "readonly"
                and not dotted_suffix
                and self._peek(1).kind == "OP"
                and self._peek(1).value == "("
            ):
                self._eat()  # 'readonly'
                self._eat()  # '('
                while not self._check("OP", ")"):
                    self._pending_readonly_params.append(self._expect("NAME").value)
                    if self._check("OP", ","):
                        self._eat()
                self._eat()  # ')'
                self._expect("NEWLINE")
                names.append(name)
                self._skip_newlines()
                continue
            # `@access(PolicyName)` / `@abi(ABIName)`, or through a dotted
            # receiver (`@asmpython.access(asmpython.Public)`,
            # `@asmpython.abi(asmpython.C)`): capture the bare argument name
            # (e.g. "Public" / "C") into self._pending_access_policy /
            # self._pending_abi_name instead of letting it fall through to
            # the generic "skip the rest of the line" branch below, which
            # would otherwise discard it same as `readonly`'s args would
            # without its own special-case above. `access`/`abi` reached via
            # any binding (`from asmpython import access`, `from
            # asmpython.annotations import access`, a dotted receiver ending
            # in `.access`/`.abi`) are all treated the same way -- only the
            # trailing decorator segment and the trailing argument segment
            # matter. The compiler never evaluates
            # asmpython.annotations.AccessObject/ABIObject -- it only
            # recognizes the well-known preset identifiers sema.py/ir_lower.py
            # check against.
            deco_call_kind = None  # "access" | "abi" | None
            deco_call_start = None  # 0 = bare name, 1 = one dotted segment to skip
            if (
                name in ("access", "abi")
                and not dotted_suffix
                and self._peek(1).kind == "OP"
                and self._peek(1).value == "("
            ):
                deco_call_kind = name
                deco_call_start = 0
            elif (
                name is not None
                and not dotted_suffix
                and self._peek(1).kind == "OP"
                and self._peek(1).value == "."
                and self._peek(2).kind == "NAME"
                and self._peek(2).value in ("access", "abi")
                and self._peek(3).kind == "OP"
                and self._peek(3).value == "("
            ):
                deco_call_kind = self._peek(2).value
                deco_call_start = 1  # one extra ".access"/".abi" segment to skip
            if deco_call_kind is not None:
                self._eat()  # decorator receiver name
                if deco_call_start == 1:
                    self._eat()  # '.'
                    self._eat()  # 'access' / 'abi'
                name = deco_call_kind
                self._eat()  # '('
                argument = None
                if self._check("NAME"):
                    argument = self._peek().value
                    self._eat()
                    if self._check("OP", "."):
                        # Dotted argument (e.g. `asmpython.Public`): only the
                        # trailing segment is the actual preset name.
                        self._eat()  # '.'
                        argument = self._expect("NAME").value
                if deco_call_kind == "access":
                    self._pending_access_policy = argument
                else:
                    self._pending_abi_name = argument
                # Skip anything else inside the parens (extra kwargs like
                # `broaden=True`) without modeling them -- unrecognized here,
                # same as every other decorator's discarded call arguments.
                depth = 0
                while not (self._check("OP", ")") and depth == 0):
                    t = self._peek()
                    if t.kind == "EOF":
                        break
                    if t.kind == "OP" and t.value in ("(", "[", "{"):
                        depth += 1
                    elif t.kind == "OP" and t.value in (")", "]", "}"):
                        depth -= 1
                    self._eat()
                self._eat()  # ')'
                self._expect("NEWLINE")
                names.append(name)
                self._skip_newlines()
                continue
            is_informational = (
                dotted_suffix
                or name in self._INFORMATIONAL_DECORATORS
                or (name is not None and name in self._imported_names)
            )
            if is_informational or name is None:
                # Eat the rest of the line as a free-form decorator
                # expression. We don't model the call so we just skip until
                # NEWLINE, balancing any `(` `[` `{` along the way.
                depth = 0
                while True:
                    t = self._peek()
                    if t.kind == "NEWLINE" and depth == 0:
                        self._eat()
                        break
                    if t.kind == "EOF":
                        break
                    if t.kind == "OP" and t.value in ("(", "[", "{"):
                        depth += 1
                    elif t.kind == "OP" and t.value in (")", "]", "}"):
                        depth -= 1
                    self._eat()
            else:
                # General decorator on a user-defined value: parse the real
                # expression (e.g. `ternary_1.instruction("000000")`) so it
                # can be applied for its side effect against the decorated
                # function once parsed.
                expr = self._parse_expr()
                self._expect("NEWLINE")
                self._pending_decorator_exprs.append(expr)
            if name is not None:
                names.append(name)  # type: ignore
            self._skip_newlines()
        return names

    def _desugar_function_decorators(self, fdef, pending: list) -> list:
        """Apply general decorators to a top-level `def`, REBINDING the name.

        `@deco def g(...)` means `g = deco(g)` in Python -- the decorator's
        result replaces the function. The existing desugaring called the
        decorator for its side effect and DISCARDED the result, which models
        only the registration pattern (a decorator that returns its argument
        unchanged); a decorator that returns a wrapper had that wrapper thrown
        away, so `@deco def g(x)` still called the undecorated `g`. The whole
        decorator idiom silently did nothing.

        To make the rebinding actually take effect, the function is RENAMED:
        `g(5)` resolves a top-level function name before a variable, so leaving
        the original name on the FuncDef would keep every call going to the
        undecorated version. The original name becomes a module-level variable
        holding the decorated value, which is the ordinary
        callable-value-in-a-variable shape (`add5 = partial(...)`; `add5(3)`)
        that already works. Recursive self-calls inside the body then go through
        the decorated name, matching CPython.

        Decorators apply bottom-up, i.e. the one nearest the `def` first.
        """
        if not pending:
            return []
        _orig_name = fdef.name
        self._deco_tmp_counter += 1
        _inner_name = f"__undecorated_{self._deco_tmp_counter}_{_orig_name}"
        fdef.name = _inner_name
        stmts: list = []
        # Built as ONE nested expression -- `g = deco2(deco1(orig))` -- rather
        # than a chain of intermediate assignments. Routing each step through a
        # temp loses the closure type on the way back out (`t = deco(orig)`
        # types t "closure", but `g = t` then re-derives g's type from a plain
        # Name read), and the call site needs that type to dispatch a closure
        # VALUE rather than a direct symbol call.
        _cur = A.Name(name=_inner_name, pos=fdef.pos)
        for expr in reversed(pending):
            if isinstance(expr, A.Name):
                _cur = A.Call(func=expr.name, args=[_cur], pos=fdef.pos)
            else:
                # `@factory(args)`: the factory expression is evaluated once
                # into a temp, because a call on an arbitrary expression's
                # result needs a name to hang off here.
                self._deco_tmp_counter += 1
                _tmp = f"__deco_tmp_{self._deco_tmp_counter}"
                stmts.append(A.Assign(target=_tmp, value=expr, pos=fdef.pos))
                _cur = A.Call(func=_tmp, args=[_cur], pos=fdef.pos)
        stmts.append(A.Assign(target=_orig_name, value=_cur, pos=fdef.pos))
        return stmts

    def _desugar_decorator_exprs(self, exprs: list, func_name: str, pos) -> list:
        """Turn deferred general-decorator exprs into real application stmts.

        `@factory(args)` on `def f(...): ...` is modeled as `f` unchanged
        (registered as a normal top-level function) plus, for each deferred
        decorator expr, two synthesized module-body statements:
        `__deco_tmp_N = factory(args)` then a bare `__deco_tmp_N(f)` call.
        This matches the identity-decorator shape (`return func` after a
        side effect) used by registration-pattern decorators like
        `InstructionSet.instruction`, and sidesteps the parser's lack of
        support for calling an expression's result directly (`expr(...)(...)`
        doesn't parse) by routing the intermediate closure through a name.
        """
        stmts: list = []
        for expr in exprs:
            if isinstance(expr, A.Name):
                # A bare-name decorator (`@deco`): apply it as a DIRECT call
                # `deco(target)`, not through an intermediate `__deco_tmp =
                # deco; __deco_tmp(target)`. The direct form lets sema's
                # call-site parameter inference (which matches by callee name)
                # see the class/function argument and type `deco`'s parameter
                # accordingly -- essential for a class decorator whose body
                # does `cls.attr = ...` on the passed class value, which needs
                # the parameter typed `type` to dispatch to the class object
                # rather than faulting on its raw RTTI id.
                call = A.Call(func=expr.name, args=[A.Name(name=func_name, pos=pos)], pos=pos)
                stmts.append(A.ExprStmt(expr=call, pos=pos))
                continue
            self._deco_tmp_counter += 1
            tmp_name = f"__deco_tmp_{self._deco_tmp_counter}"
            stmts.append(A.Assign(target=tmp_name, value=expr, pos=pos))
            call = A.Call(func=tmp_name, args=[A.Name(name=func_name, pos=pos)], pos=pos)
            stmts.append(A.ExprStmt(expr=call, pos=pos))
        return stmts

    def _parse_classdef(self, decorators: "list[str] | None" = None) -> A.ClassDef:
        class_decorators = list(decorators) if decorators else []
        is_dc = "dataclass" in class_decorators
        # Snapshot the CLASS's own `@access(...)`/`@abi(...)` state before
        # parsing the class body -- each method inside resets
        # self._pending_access_policy/_pending_abi_name via its own
        # `_eat_decorators()` call, so reading them after the body loop below
        # would reflect the last method's decorators instead of the class's.
        class_access_policy = self._pending_access_policy
        class_abi_name = self._pending_abi_name
        start = self._expect("KEYWORD", "class").pos
        name = self._expect("NAME").value
        parent = None
        extra_bases: list = []
        parent_qualifier = None
        sealed_permits: list = []
        implements_interface: "str | None" = None
        metaclass_name: "str | None" = None
        if self._check("OP", "("):
            self._eat()
            if not self._check("OP", ")"):
                # `sealed class X(permits=A, B):` -- the `sealed` extension's
                # explicit subclass permit-list. Recognized as a keyword-arg
                # shape (bare NAME immediately followed by `=`) so it can't
                # be confused with a genuine base class name (which is never
                # followed by `=` here). Captures the comma-separated NAME
                # list that follows into `sealed_permits`; parsed regardless
                # of whether `sealed` is actually active for this class (the
                # sema-side is_sealed check is what's gated on activation --
                # a `permits=` clause on an otherwise-ordinary class with no
                # `sealed` prefix is simply never consulted).
                if (
                    self._check("NAME", "permits")
                    and self._peek(1).kind == "OP"
                    and self._peek(1).value == "="
                ):
                    self._eat()  # 'permits'
                    self._eat()  # '='
                    sealed_permits.append(self._expect("NAME").value)
                    while self._check("OP", ","):
                        self._eat()
                        sealed_permits.append(self._expect("NAME").value)
                elif (
                    self._check("NAME", "interface")
                    and self._peek(1).kind == "OP"
                    and self._peek(1).value == "="
                ):
                    # `class X(interface=Name):` -- the `interface`
                    # extension's structural-conformance declaration. Same
                    # keyword-arg-shape recognition as `permits=`, but
                    # captures a single NAME (not a comma-list).
                    self._eat()  # 'interface'
                    self._eat()  # '='
                    implements_interface = self._expect("NAME").value
                elif (
                    self._check("NAME", "metaclass")
                    and self._peek(1).kind == "OP"
                    and self._peek(1).value == "="
                ):
                    self._eat()
                    self._eat()
                    metaclass_name = ".".join(self._parse_dotted_name_parts())
                else:
                    # First base class, possibly dotted (`module.Base`) and
                    # possibly a subscripted generic (`Protocol[_T_co]`,
                    # `Generic[T]`) -- real Python allows a base class
                    # expression to be any of these, e.g. typeshed's own
                    # `class SupportsRead(Protocol[_T_co]):`. Every consumer
                    # (sema's class table, codegen's chain walker) keys by
                    # bare class name only, so `parent` keeps the leaf and a
                    # subscript's contents are parsed (for balanced-bracket
                    # correctness) and discarded, same as the "extra bases
                    # aren't modelled" skip a few lines below already does
                    # for anything else in this parameter list. Preserve the
                    # dotted qualifier separately so whole-program merging
                    # can resolve same-named classes from different modules.
                    # Confirmed via a real repro: PIL's own bundled typeshed
                    # stub `PIL/_typing.py`'s `class SupportsRead(Protocol
                    # [_T_co]):` failed with "expected OP ')', got OP '['"
                    # before this fix -- the subscript was never consumed at
                    # all, so parsing fell straight through to this
                    # parenthesized-base-list's own closing `)` check.
                    parent_parts = self._parse_dotted_name_parts()
                    parent = parent_parts[-1]
                    if len(parent_parts) > 1:
                        parent_qualifier = ".".join(parent_parts[:-1])
                    if self._check("OP", "["):
                        self._eat()
                        if not self._check("OP", "]"):
                            self._parse_expr()
                            while self._check("OP", ","):
                                self._eat()
                                if self._check("OP", "]"):
                                    break
                                self._parse_expr()
                        self._expect("OP", "]")
                # Extra bases are CAPTURED now (see ClassDef.extra_bases) so a
                # mixin's methods resolve; keyword bases (metaclass=, permits=,
                # interface=) are still handled separately below.
                while self._check("OP", ","):
                    self._eat()
                    if (
                        self._check("NAME", "permits")
                        and self._peek(1).kind == "OP"
                        and self._peek(1).value == "="
                    ):
                        self._eat()  # 'permits'
                        self._eat()  # '='
                        sealed_permits.append(self._expect("NAME").value)
                        while self._check("OP", ","):
                            self._eat()
                            sealed_permits.append(self._expect("NAME").value)
                        break
                    if (
                        self._check("NAME", "interface")
                        and self._peek(1).kind == "OP"
                        and self._peek(1).value == "="
                    ):
                        self._eat()  # 'interface'
                        self._eat()  # '='
                        implements_interface = self._expect("NAME").value
                        break
                    if (
                        self._check("NAME", "metaclass")
                        and self._peek(1).kind == "OP"
                        and self._peek(1).value == "="
                    ):
                        self._eat()
                        self._eat()
                        metaclass_name = ".".join(self._parse_dotted_name_parts())
                        continue
                    _extra = self._parse_expr()
                    # A plain (possibly dotted) base name is a real base class.
                    # Anything else (a subscripted generic, a call) is not
                    # something this compiler models, and is dropped as before.
                    if isinstance(_extra, A.Name):
                        extra_bases.append(_extra.name)
                    elif isinstance(_extra, A.Attr):
                        extra_bases.append(_extra.name)
            self._expect("OP", ")")
        self._expect("OP", ":")
        # Single-line class body (`class C: pass` / `class C: ...`) -- same
        # inline-body allowance _parse_block() has for def/if/while/etc.
        # (see its own comment). Only `pass`/`...` make sense on this line
        # (a class body can't usefully hold a single inline method/field
        # declaration the way a function body can hold a single inline
        # statement), so this stays a narrow, explicit check rather than
        # delegating to the general statement dispatcher.
        if not self._check("NEWLINE"):
            if self._check("KEYWORD", "pass") or self._check("OP", "..."):
                self._eat()
                self._expect("NEWLINE")
                return A.ClassDef(  # type: ignore
                    name=name,  # type: ignore
                    parent=parent,  # type: ignore
                    extra_bases=list(extra_bases),
                    methods=[],
                    pos=start,  # type: ignore
                    parent_qualifier=parent_qualifier,
                    class_vars=[],
                    is_dataclass=is_dc,
                    decorators=class_decorators,
                    field_decorators={},
                    sealed_permits=sealed_permits,
                    implements_interface=implements_interface,
                    metaclass=metaclass_name,
                    access_policy=class_access_policy,
                    abi_name=class_abi_name or "AutoABI",
                    is_public_export=class_access_policy == "Public" or class_abi_name is not None,
                )
            raise ParseError(
                "class bodies may only contain 'def' methods, field "
                "declarations, docstrings, or 'pass'",
                self._peek().pos,
            )
        self._expect("NEWLINE")
        self._skip_newlines()
        self._expect("INDENT")
        methods: list[A.FuncDef] = []
        class_vars: list = []
        field_decorators: dict = {}
        conditional_body_blocks: list = []
        side_effect_stmts: list = []
        while not self._check("DEDENT"):
            self._skip_newlines()
            if self._check("DEDENT"):
                break
            if self._check("KEYWORD", "pass"):
                self._eat()
                self._expect("NEWLINE")
                continue
            if self._check("KEYWORD", "if"):
                self._parse_class_body_if(
                    methods, class_vars, field_decorators,
                    conditional_body_blocks, side_effect_stmts,
                )
                self._skip_newlines()
                continue
            # Decorators on methods: mostly dropped; @assembly_func is honored.
            decorators = self._eat_decorators()
            if self._check("KEYWORD", "def"):
                methods.append(self._parse_funcdef(decorators=decorators))
            elif self._check("STRING"):
                # Class-body string literal (docstring) — drop the line.
                self._eat()
                self._expect("NEWLINE")
            elif self._check("NAME") and self._peek(1).kind == "OP" and self._peek(1).value in (
                "=", ":",
            ):
                # Class-body variable: `name [: type] [= value]` -- only
                # these two lookahead shapes are genuinely a class-var
                # DECLARATION (a single name immediately followed by `:` or
                # `=`); anything else starting with a bare NAME (a call,
                # `x.y`, `x[i]`, a tuple-assign `a, b = ...`, ...) is a real
                # statement, handled by the general fallback below instead
                # of being misparsed as a field name. Captured so sema can
                # type `self.NAME` reads (e.g. a set/dict constant used for
                # membership). @dataclass-style annotation-only fields are
                # captured too (value None).
                cv = self._parse_class_var()
                class_vars.append(cv)
                if decorators:
                    field_decorators[cv[0]] = decorators
            else:
                # A general class-body statement -- real Python allows ANY
                # statement in a class body (it's executed code, same as a
                # function body): a side-effecting call made purely for
                # what it registers elsewhere (e.g. Pillow's own `list(map(
                # _register_basic, [...]))`, populating external format-
                # registry dicts this compiler's static class model has no
                # equivalent slot for), or a bare tuple-assignment (`a, b =
                # 1, 2`, never actually reachable before this fix --
                # _parse_class_var only ever handled a single name). Reuses
                # the SAME general statement dispatcher every other scope
                # already uses (_parse_stmt), rather than a second, narrower
                # copy of assignment-shape detection -- safe here because
                # `def` (the one construct _parse_stmt treats differently
                # inside a class body, lifting it as a closure-capturing
                # nested function rather than an ordinary method) is
                # already dispatched separately above, so _parse_stmt is
                # never reached for it from this fallback. Recorded on
                # ClassDef.side_effect_stmts for visibility rather than
                # folded into class_vars/methods -- see that field's own
                # docstring for why: asmpython's class system has no "run
                # this code during class construction" phase at all,
                # matching the same architectural gap conditional_body_
                # blocks already documents for `if`.
                side_effect_stmts.append(self._parse_stmt())
            self._skip_newlines()
        self._expect("DEDENT")
        # A method default referencing a bare name that's actually one of
        # this class's own class-vars (`DEFAULT_X = ...` above, then later
        # `def __init__(self, x=DEFAULT_X)`) is valid Python: a class body
        # executes top-to-bottom, so unqualified names in a default
        # expression resolve against earlier class-body bindings, not
        # module globals. Defaults are spliced verbatim into call sites
        # missing the arg (see codegen's "splice in the literals"), where
        # the bare name wouldn't resolve at all -- rewrite to `ClassName.X`
        # so it's a real, globally-resolvable attribute access everywhere.
        class_var_names = {cv[0] for cv in class_vars}
        if class_var_names:
            for m in methods:
                for i, d in enumerate(m.defaults):
                    if isinstance(d, A.Name) and d.name in class_var_names:
                        m.defaults[i] = A.Attr(
                            obj=A.Name(name=name, pos=d.pos), name=d.name, pos=d.pos
                        )
        return A.ClassDef(  # type: ignore
            name=name,  # type: ignore
            parent=parent,  # type: ignore
            extra_bases=list(extra_bases),
            methods=methods,
            pos=start,  # type: ignore
            parent_qualifier=parent_qualifier,
            class_vars=class_vars,
            is_dataclass=is_dc,
            decorators=class_decorators,
            field_decorators=field_decorators,
            sealed_permits=sealed_permits,
            implements_interface=implements_interface,
            metaclass=metaclass_name,
            access_policy=class_access_policy,
            abi_name=class_abi_name or "AutoABI",
            is_public_export=class_access_policy == "Public" or class_abi_name is not None,
            conditional_body_blocks=conditional_body_blocks,
            side_effect_stmts=side_effect_stmts,
        )

    def _parse_class_body_if(
        self,
        methods: list,
        class_vars: list,
        field_decorators: dict,
        conditional_body_blocks: list,
        side_effect_stmts: list,
    ) -> None:
        """`if <cond>: <methods/field declarations/expression statements>`
        inside a class body -- a real, common idiom for version/capability-
        gated member definition (e.g. Pillow's own `if hasattr(Fraction,
        "__int__"): __int__ = _delegate("__int__")`). See ClassDef.
        conditional_body_blocks' docstring for why this unconditionally
        folds the guarded block's contents into the caller's own lists
        (same as if the `if` wasn't there) rather than modeling real
        runtime branching: asmpython has no conditional-class-body-
        execution semantics at all, and the conditions this idiom actually
        guards on (hasattr against real, unmodeled external types) have no
        compile-time value to branch on in the first place.

        `elif`/`else` are deliberately NOT supported: unlike a bare `if`
        (safe to always-include, since real Python's version/capability
        checks are usually gating an ADDITIVE feature), an `elif`/`else`
        chain implies genuinely DIFFERENT, mutually exclusive definitions
        for what may be the same name -- always-including every branch
        would silently let a later branch's definition win by simple list
        order rather than reflecting any real, meaningful choice. Rejected
        explicitly with a precise diagnostic rather than silently picking
        one branch or the other.
        """
        kw = self._expect("KEYWORD", "if")
        cond = self._parse_expr()
        self._expect("OP", ":")
        self._expect("NEWLINE")
        self._skip_newlines()
        self._expect("INDENT")
        guarded_names: list[str] = []
        while not self._check("DEDENT"):
            self._skip_newlines()
            if self._check("DEDENT"):
                break
            if self._check("KEYWORD", "pass"):
                self._eat()
                self._expect("NEWLINE")
                continue
            if self._check("KEYWORD", "if"):
                # Nested `if` inside a guarded block -- same unconditional-
                # inclusion rule applies recursively.
                self._parse_class_body_if(
                    methods, class_vars, field_decorators,
                    conditional_body_blocks, side_effect_stmts,
                )
                self._skip_newlines()
                continue
            decorators = self._eat_decorators()
            if self._check("KEYWORD", "def"):
                fdef = self._parse_funcdef(decorators=decorators)
                methods.append(fdef)
                guarded_names.append(fdef.name)
            elif self._check("STRING"):
                self._eat()
                self._expect("NEWLINE")
            elif self._check("NAME") and self._peek(1).kind == "OP" and self._peek(1).value in (
                "=", ":",
            ):
                cv = self._parse_class_var()
                class_vars.append(cv)
                guarded_names.append(cv[0])
                if decorators:
                    field_decorators[cv[0]] = decorators
            else:
                # Same general statement fallback as the outer class-body
                # loop -- see its own comment for why _parse_stmt is safe
                # to reuse here.
                side_effect_stmts.append(self._parse_stmt())
            self._skip_newlines()
        self._expect("DEDENT")
        conditional_body_blocks.append((cond, guarded_names))
        if self._check("KEYWORD", "elif") or self._check("KEYWORD", "else"):
            raise ParseError(
                "'elif'/'else' after a class-body 'if' is not supported -- "
                "asmpython has no conditional-class-body-execution model, "
                "so an 'if' block's methods/fields are always included "
                "(matching version/capability-gated member definitions "
                "like Pillow's own hasattr(...) idiom), but a genuine "
                "elif/else branch would imply mutually exclusive "
                "definitions this compiler cannot meaningfully choose "
                "between",
                self._peek().pos,
                ErrorCode.P_UNEXPECTED_TOKEN,
            )

    def _parse_class_var(self):
        """Parse a class-body variable line: `name [: type] [= value]`.
        Returns (name, annot, value_expr_or_None).

        When the line carries a type annotation (the @dataclass field style,
        `pos: SourcePos = field(...)`), the type comes from the annotation and
        the value is skipped — its initializer may use constructs asmpython can't
        parse (`field(default_factory=lambda: ...)`). An unannotated assignment
        (`KEYWORDS = {...}`) is a real constant, so its value is parsed so sema
        can type the attribute."""
        name = self._expect("NAME").value
        annot = None
        if self._check("OP", ":"):
            self._eat()
            annot = self._parse_type_annotation()
        value = None
        if self._check("OP", "="):
            self._eat()
            if annot is not None:
                # Dataclass-style field initializer: TRY to parse it — codegen
                # synthesizes the field store from it (`field(default_factory=
                # dict)` allocates an empty dict). Genuinely unparseable
                # initializers (`field(default_factory=lambda: ...)`) fall back
                # to a token skip and the field stays unset.
                saved = self.i
                try:
                    value = self._parse_expr()
                except ParseError:
                    self.i = saved
                    self._skip_to_line_end()
            else:
                value = self._parse_expr()
        self._expect("NEWLINE")
        return (name, annot, value)

    def _skip_to_line_end(self) -> None:
        """Consume tokens up to the line's terminating NEWLINE, balancing any
        bracketing along the way. Used to drop a class-var initializer we don't
        want to build an AST for."""
        depth = 0
        while True:
            t = self._peek()
            if t.kind == "EOF":
                break
            if t.kind == "NEWLINE" and depth == 0:
                break
            if t.kind == "OP" and t.value in ("(", "[", "{"):
                depth += 1
            elif t.kind == "OP" and t.value in (")", "]", "}"):
                depth -= 1
            self._eat()

    def _parse_funcdef(self, decorators: "list[str] | None" = None) -> A.FuncDef:
        start = self._peek().pos
        is_async = False
        if self._check("KEYWORD", "async"):
            self._eat()
            is_async = True
        self._expect("KEYWORD", "def")
        name = self._expect("NAME").value
        self._expect("OP", "(")
        params: list[str] = []
        defaults: list = []  # parallel to params; None means required
        param_types: list = []  # parallel to params; None means unannotated
        vararg: str | None = None
        kwarg: str | None = None
        first = True
        while not self._check("OP", ")"):
            if not first:
                self._expect("OP", ",")
                if self._check("OP", ")"):
                    break  # trailing comma
            first = False
            if self._check("OP", "**"):
                self._eat()
                kwarg = self._expect("NAME").value
                # `**kw: T` annotates each VALUE as type T (the dict is
                # always `dict[str, T]`). Carry that value kind into the
                # dict's value type so `kw["x"]` reads recover it. With no
                # annotation -- or an explicit `object`/opaque one -- the
                # values are genuinely heterogeneous, so the value type is
                # "any" (NOT the int unknown-sentinel, which would format a
                # string/float value's pointer as an integer -- the concrete
                # bug this fixes). Previously the annotation was parsed and
                # thrown away and the value type hardcoded to None (== int),
                # silently miscompiling every `**kwargs` value read of a
                # non-int value.
                value_kind = "any"
                if self._check("OP", ":"):
                    self._eat()
                    inner = self._parse_type_annotation()  # dict[str, T] value type
                    if inner and inner[0] not in (None, "object", "any"):
                        value_kind = inner[0]
                params.append(kwarg)
                param_types.append(("dict", value_kind))
                defaults.append(None)
                continue
            if self._check("OP", "/"):
                # Positional-only marker (PEP 570): `def f(a, b, /, c):` --
                # everything before it may only be passed positionally by a
                # real caller. Like the bare-`*` keyword-only marker just
                # below, asmpython treats every parameter positionally
                # regardless (callers may still pass any of them by
                # keyword; sema binds keyword args onto positions), so this
                # carries no actual parameter and is simply consumed and
                # discarded, exactly the same way. Found via a real repro:
                # PIL's own bundled typeshed stub, PIL/_typing.py's
                # `def read(self, length: int = ..., /) -> _T_co: ...`.
                self._eat()
                continue
            if self._check("OP", "*"):
                self._eat()
                if self._check("NAME"):
                    # `*args`: a single list-typed parameter that absorbs the
                    # caller's surplus positional arguments.
                    vararg = self._expect("NAME").value  # type: ignore
                    # Heterogeneous unless annotated: *args holds whatever the
                    # caller passed. An annotation below overrides this.
                    el = "any"
                    if self._check("OP", ":"):
                        self._eat()
                        inner = self._parse_type_annotation()
                        el = inner[0] if inner else None
                    params.append(vararg)  # type: ignore
                    param_types.append(("list", el))
                    defaults.append(None)
                else:
                    # A bare `*` is the keyword-only marker. asmpython treats the
                    # following params positionally; callers may still pass them
                    # by keyword (sema binds keyword args onto positions).
                    pass
                continue
            self._parse_param(params, defaults, param_types)
        self._expect("OP", ")")
        ret_type = None
        if self._check("OP", "->"):
            self._eat()
            ret_type = self._parse_type_annotation()
        self._expect("OP", ":")
        # Publish this function's parameters while its body (and therefore any
        # nested `def` inside it) is parsed.
        _encl_frame: set = set(params)
        if vararg:
            _encl_frame.add(vararg)
        if kwarg:
            _encl_frame.add(kwarg)
        self._enclosing_params.append(_encl_frame)
        try:
            body = self._parse_block()
        finally:
            self._enclosing_params.pop()
        asm_body = None
        asm_symbol = None
        if decorators and self._ASM_DECORATOR in decorators:
            asm_body, asm_symbol = self._extract_asm_body(name, body, start)  # type: ignore[arg-type]
        access_policy = self._pending_access_policy
        abi_name = self._pending_abi_name
        return A.FuncDef(
            name=name,  # type: ignore
            params=params,
            body=body,
            pos=start,
            defaults=defaults,
            param_types=param_types,
            ret_type=ret_type,
            vararg=vararg,
            kwarg=kwarg,
            asm_body=asm_body,
            asm_symbol=asm_symbol,
            decorators=list(decorators) if decorators else [],
            readonly_params=list(self._pending_readonly_params),
            access_policy=access_policy,
            abi_name=abi_name or "AutoABI",
            is_public_export=access_policy == "Public" or abi_name is not None,
            is_async=is_async,
        )

    def _extract_asm_body(self, name: str, body: list, pos) -> "tuple[str, str]":
        """Lift the raw-NASM body of an `@assembly_func` out of its docstring.

        The function body must be exactly one string literal (the NASM), shaped
        by the parser as a single `ExprStmt(StrLit)`. Returns (nasm_text,
        symbol). The symbol defaults to the function name; callers may later
        override it from a `@assembly_func(symbol=...)` decorator argument (not
        modelled in the parser, which only sees the bare decorator name).
        """
        non_pass = [s for s in body if not isinstance(s, A.Pass)]
        if (
            len(non_pass) == 1
            and isinstance(non_pass[0], A.ExprStmt)
            and isinstance(non_pass[0].expr, A.StrLit)
        ):
            return non_pass[0].expr.value, name  # type: ignore[return-value]
        raise ParseError(
            f"@assembly_func {name!r} must have a body of exactly one "
            f"triple-quoted string containing its NASM instructions",
            pos,
        )

    def _parse_param(self, params: list, defaults: list, param_types: list) -> None:
        """Parse one positional parameter: NAME [: type] [= default]."""
        params.append(self._expect("NAME").value)
        annot = None
        if self._check("OP", ":"):
            self._eat()
            annot = self._parse_type_annotation()
        param_types.append(annot)
        d = self._parse_optional_default()
        if d is None and defaults and defaults[-1] is not None:
            raise ParseError(
                "non-default argument follows default argument",
                self._peek().pos,
            )
        defaults.append(d)

    def _parse_optional_default(self):
        """If the next token is '=', consume it and the literal that follows.
        Returns the literal AST node, or None if no default was present."""
        if not self._check("OP", "="):
            return None
        self._eat()
        return self._parse_default_literal()

    def _is_safe_default_value(self, e) -> bool:
        """True if `e` is safe to splice, verbatim, into every call site that
        omits this parameter (see `_parse_default_literal`'s docstring for
        why that's the actual constraint a default value's shape must
        satisfy -- this is the recursive shape-check side of it, mirroring
        `_is_assign_target`'s role for assignment targets).

        Accepts exactly the shapes `_parse_default_literal` accepted before
        it started parsing through the general expression grammar: numeric/
        string/bool/None/Ellipsis literals, a (possibly negated) numeric
        literal, a dotted Name/Attr chain (a compile-time-constant reference,
        e.g. `Trit.MID`), and a list/tuple built purely from safe elements
        (recursively) -- plus the empty dict, the one dict shape the
        original grammar ever allowed (asmpython dict literals need str
        keys typed from real elements, which an empty dict has none of to
        type from, so a non-empty dict was never actually reachable there
        either). Everything else the general expression grammar can
        produce -- calls, subscripts, attribute access beyond a bare dotted
        reference, f-strings, comprehensions, sets, non-empty dicts,
        binary/boolean/comparison ops, lambdas -- is rejected, since none of
        them are safe to silently re-evaluate at every omitted-argument call
        site the way a real one-time def-level default should be.
        """
        if isinstance(e, (A.IntLit, A.FloatLit, A.StrLit)):
            return True
        if isinstance(e, A.UnaryOp) and e.op == "-":
            return isinstance(e.operand, (A.IntLit, A.FloatLit))
        if isinstance(e, A.Name):
            return True
        if isinstance(e, A.Attr):
            return self._is_safe_default_value(e.obj)
        if isinstance(e, (A.ListLit, A.TupleLit)):
            return all(self._is_safe_default_value(el) for el in e.elems)
        if isinstance(e, A.DictLit):
            return not e.keys and not e.values
        if isinstance(e, A.Lambda):
            # A lambda literal is safe to splice for the very reason this check
            # exists: it is immutable and its evaluation has no side effects, so
            # re-evaluating it per call site yields an equivalent function. It
            # is strictly safer than the ListLit accepted just above, which is
            # the classic mutable default where per-site splicing genuinely does
            # diverge from CPython's evaluate-once semantics.
            return True
        return False

    def _fold_default_negation(self, e):
        """`_is_safe_default_value` accepts `UnaryOp(op="-", operand=IntLit|
        FloatLit)`, but every existing consumer of a default value's AST
        (sema's direct-splice call-site binding, anything pattern-matching
        on `isinstance(default, A.IntLit)`) expects the sign already folded
        into the literal itself -- the same pre-folded shape
        `_parse_default_literal` always produced, before it started parsing
        through the general expression grammar (which builds a real
        `UnaryOp` node for `-x`, unlike a literal's own lexer-level sign).
        Folds it back to that shape so this stays byte-for-byte compatible
        with every downstream reader, instead of teaching each of them to
        also unwrap a UnaryOp wrapper around a default they never had to
        handle before."""
        if isinstance(e, A.UnaryOp) and e.op == "-":
            operand = e.operand
            if isinstance(operand, A.IntLit):
                return A.IntLit(value=-operand.value, pos=e.pos)
            if isinstance(operand, A.FloatLit):
                return A.FloatLit(value=-operand.value, pos=e.pos)
        return e

    def _parse_default_literal(self):
        """Parse one default-argument value: anything `_is_safe_default_value`
        accepts (see its docstring for the exact set and why it's
        restricted) -- int/float/str/bool/None/Ellipsis, a (possibly
        negated) number, a dotted Name/Attr reference, or a list/tuple of
        such, recursively. The empty dict is the one dict shape accepted;
        asmpython's dict literals need str keys typed from real elements,
        which an empty dict never has any of to type from.

        Parses through the same general atom/trailer grammar every other
        expression uses (so e.g. `[1, [2, 3]]` or `(a.b.c, 1)` compose for
        free, the same way nested nested nested tuples/lists already do in
        any ordinary expression) rather than a separate, narrower copy of
        int/float/str/list/tuple/dict/dotted-name parsing -- then rejects
        (via `_is_safe_default_value`) whatever shape the general grammar
        can produce that this restriction doesn't allow (calls, subscripts,
        f-strings, comprehensions, sets, non-empty dicts, binary ops, ...).

        Only a leading `-` is special-cased here, not the full `_parse_unary`
        (which would also silently accept and discard a leading `+`, or wrap
        `~x` in a UnaryOp for `_is_safe_default_value` to reject after the
        fact) -- the original grammar's negation handling only ever
        recognized `-`, so a bare `def f(x=+1)` always raised
        P_INVALID_DEFAULT before; matching that exactly means never letting
        the parse succeed for `+`/`~` in the first place, not rejecting them
        one level later once they've already been silently consumed.
        """
        if self._check("KEYWORD", "lambda"):
            # `_parse_primary` sits above lambda in the precedence chain and so
            # can never reach it. A lambda default (`def f(x, key=lambda v: v)`)
            # therefore failed in the parser, before _is_safe_default_value --
            # which does accept it -- ever got a say.
            expr = self._parse_lambda()
        elif self._check_any_op("-"):
            minus_pos = self._eat().pos
            expr = A.UnaryOp(op="-", operand=self._parse_primary(), pos=minus_pos)
        else:
            expr = self._parse_primary()
        if not self._is_safe_default_value(expr):
            raise ParseError(
                "default argument must be a literal "
                "(int/float/str/True/False/None/list/dict), got "
                f"{expr.__class__.__name__}",
                expr.pos,
            )
        return self._fold_default_negation(expr)

    def _parse_type_annotation(self) -> tuple:
        """Parse a type annotation, returning a normalized descriptor.

        The descriptor is a ``(base, el)`` tuple:
          - ``base`` is a primitive (``"int"``/``"str"``/``"float"``/``"list"``/
            ``"dict"``/``"tuple"``/``"set"``/``"none"``/``"any"``) or a bare/
            dotted class-ish name (e.g. ``"Token"``, ``"A.IntLit"``).
          - ``el`` carries the inner element/value base for ``list[T]`` (the T)
            and ``dict[K, V]`` (the V), else ``None``.

        sema turns this into a concrete static type (resolving class names to
        ``instance:<Class>``). Genuinely-ambiguous shapes (multi-type unions,
        unrecognised constructs) collapse to ``("any", None)`` so they simply
        don't constrain inference.
        """
        return self._parse_annot_union()

    def _parse_annot_union(self) -> tuple:
        first = self._parse_annot_unit()
        non_none = [] if first[0] == "none" else [first]
        while self._check("OP", "|"):
            self._eat()
            nxt = self._parse_annot_unit()
            if nxt[0] != "none":
                non_none.append(nxt)
        if len(non_none) == 1:
            return non_none[0]  # `T | None` / `Optional[T]` -> T
        if not non_none:
            return ("none", None)
        return ("any", None)  # genuine multi-type union: don't constrain

    def _parse_annot_unit(self) -> tuple:
        t = self._peek()
        if t.kind == "KEYWORD" and t.value == "None":
            self._eat()
            return ("none", None)
        # A quoted forward reference like `"Expr"` (PEP 484): re-lex the
        # string's contents as a type expression, e.g. `"Path"` -> `Path`,
        # `"list[Foo]"` -> `list[Foo]`. Falls back to unconstrained `any` if
        # the contents aren't a parseable annotation.
        if t.kind == "STRING":
            self._eat()
            return self._parse_annot_from_string(t.value)
        if t.kind != "NAME":
            # Unexpected shape (e.g. the `[int]` inside `Callable[[int], str]`).
            # Skip a balanced atom so we don't misparse the enclosing list.
            return self._skip_annot_atom()
        name = ".".join(self._parse_dotted_name_parts())
        inner: list = []
        if self._check("OP", "["):
            self._eat()
            if not self._check("OP", "]"):
                inner.append(self._parse_annot_union())
                while self._check("OP", ","):
                    self._eat()
                    if self._check("OP", "]"):
                        break
                    inner.append(self._parse_annot_union())
            self._expect("OP", "]")
        return self._normalize_annot(name, inner)  # type: ignore

    def _parse_annot_from_string(self, src: str) -> tuple:
        """Re-lex and parse a quoted forward-reference annotation's contents
        (e.g. the `Path` in `-> "Path"`) as a normal type expression, by
        running it through a fresh `Lexer`/`Parser` pair. Any lex/parse
        failure (the string isn't actually a type expression) falls back to
        unconstrained `any`, matching the previous behaviour."""
        try:
            inner_p: Parser = Parser(Lexer(src).tokenize())
            return inner_p._parse_annot_union()
        except Exception:
            return ("any", None)

    def _skip_annot_atom(self) -> tuple:
        """Consume one balanced annotation atom (balancing []/()/{}), stopping
        at a depth-0 separator. Used for annotation shapes we don't model."""
        depth = 0
        while True:
            t = self._peek()
            if t.kind in ("NEWLINE", "EOF"):
                break
            if (
                depth == 0
                and t.kind == "OP"
                and t.value in (",", "]", ")", "=", ":", "|")
            ):
                break
            if t.kind == "OP" and t.value in ("[", "(", "{"):
                depth += 1
            elif t.kind == "OP" and t.value in ("]", ")", "}"):
                if depth == 0:
                    break
                depth -= 1
            self._eat()
        return ("any", None)

    # Names that mean "a list at runtime" / "a dict at runtime".
    _LIST_ANNOTS = {"list", "List", "Iterable", "Iterator", "Sequence", "tuple_unused"}
    _DICT_ANNOTS = {"dict", "Dict", "Mapping", "MutableMapping"}

    def _normalize_annot(self, name: str, inner: list) -> tuple:
        if name == "bool":
            return ("bool", None)
        if name == "int":
            return ("int", None)
        if name == "int8":
            # Only meaningful as outparam[int8]/inparam[int8]'s pointee kind
            # (a byte-granularity C ABI buffer, e.g. `uint8_t *buffer` --
            # see PortaPy's portapy_dict_key_copy_utf8) -- deliberately a
            # distinct name from `bytes`/`bytearray` (which normalize to
            # `list[int]`, an ordinary asmpython list, not a raw pointer).
            return ("int8", None)
        if name in ("str",):
            return ("str", None)
        if name == "float":
            return ("float", None)
        if name in ("None", "none"):
            return ("none", None)
        if name in ("object", "Any"):
            return ("any", None)
        if name in ("bytes", "bytearray"):
            # Treat bytes/bytearray as list[int] — same memory layout, int elements.
            return ("list", "int")
        if name in ("Optional",):
            return inner[0] if inner else ("any", None)
        if name in ("Union",):
            return ("any", None)
        if name in self._LIST_ANNOTS:
            if inner and inner[0][0] in ("tuple", "Tuple") and inner[0][1]:
                # list[tuple[T1, T2, ...]]: keep the per-slot base kinds so
                # `for a, b in <list[tuple[T1,T2]]>` can type each target
                # (instead of collapsing to the bare "tuple" element kind).
                slot_bases = [s[0] for s in inner[0][1]]
                return ("list", ("tuple", slot_bases))
            if inner and inner[0][0] in self._LIST_ANNOTS:
                # list[list[T]]: preserve the inner element kind so
                # `for row in matrix: row[i]` knows the leaf type.
                # inner[0] is already the normalized ("list", el_kind) tuple.
                inner_el = inner[0][1]
                return ("list", ("list", inner_el))
            if inner and inner[0][0] in self._DICT_ANNOTS:
                # list[dict[K,V]]: preserve the value kind so
                # `for d in dicts: d[key]` knows the value type.
                # inner[0] is already the normalized ("dict", val_kind) tuple
                # produced by the recursive _normalize_annot call for the dict.
                val_el = inner[0][1]
                return ("list", ("dict", val_el))
            el = inner[0][0] if inner else None
            return ("list", el)
        if name in self._DICT_ANNOTS:
            if len(inner) >= 2 and inner[1][0] in ("tuple", "Tuple") and inner[1][1]:
                # dict[K, tuple[T1, T2, ...]]: preserve the per-slot value
                # kinds so lookups can unpack the returned tuple precisely.
                slot_bases = [s[0] for s in inner[1][1]]
                return ("dict", ("tuple", slot_bases))
            if len(inner) >= 2 and inner[1][0] in self._LIST_ANNOTS:
                # dict[K, list[T]]: preserve the inner element kind so a
                # lookup's returned list carries its element type.
                return ("dict", ("list", inner[1][1]))
            if len(inner) >= 2 and inner[1][0] in self._DICT_ANNOTS:
                # dict[K, dict[K2, V]]: preserve the nested dict's value kind.
                return ("dict", ("dict", inner[1][1]))
            val = inner[1][0] if len(inner) >= 2 else None
            return ("dict", val)
        if name in ("tuple", "Tuple"):
            return ("tuple", inner)
        if name in ("set", "Set", "frozenset"):
            return ("set", None)
        if name == "outparam":
            # `outparam[int]`/`outparam[float]`: a parameter that's a raw
            # pointer the CALLER owns (only meaningful on an exported
            # `@access(Public)`/`@abi(...)` function's own parameter list --
            # sema.py rejects it everywhere else). The pointee kind (`el`)
            # decides the store width -- see sema's outparam-store handling
            # and ir_lower.py's IRType selection for this parameter.
            el = inner[0][0] if inner else "int"
            return ("outparam", el)
        if name == "inparam":
            # `inparam[int]`/`inparam[float]`: a raw, read-only, caller-
            # owned ARRAY pointer -- outparam's read-side counterpart, same
            # export-only enforcement (see sema.py). Element kind decides
            # the load width and the per-element pointer arithmetic
            # (ir_lower.py's Subscript-read lowering).
            el = inner[0][0] if inner else "int"
            return ("inparam", el)
        # Anything else (CamelCase class, dotted module.Class) is a class-ish
        # name; sema decides whether it's a known class -> instance:<name>.
        return (name, None)

    def _parse_block(self) -> list:
        # Single-line compound-statement body (`def f(): ...`, `if x: y = 1`,
        # `class C: pass`) -- real Python allows exactly one simple statement
        # right after the colon on the same line, with no INDENT/DEDENT at
        # all, instead of the usual indented block. Every _parse_block
        # caller already consumed the colon, so a non-NEWLINE token here
        # means this is the inline form: parse exactly one statement via the
        # normal dispatcher (which already expects to consume the statement's
        # own trailing NEWLINE, same as when it's the first line of an
        # indented block) and return it as a one-element body. Confirmed via
        # a real repro: PIL.Image.py's `Protocol` stub methods are written
        # exactly as `def getdata(self) -> tuple[...]: ...` on one line.
        if not self._check("NEWLINE"):
            result = self._parse_stmt()
            # _parse_stmt() normally returns exactly one Stmt, but a chained
            # assignment led by an attribute/subscript target (`self.a =
            # self.b = value`) desugars to MULTIPLE statements and returns a
            # real list instead -- see `_desugar_chained_assign`'s own
            # comment for why. Flatten rather than nest so a single-line
            # inline body (`if x: self.a = self.b = 1`) still yields a flat
            # statement list, same shape as the ordinary indented-block
            # path below produces.
            return result if isinstance(result, list) else [result]
        self._expect("NEWLINE")
        self._skip_newlines()
        self._expect("INDENT")
        self._suite_depth += 1
        try:
            stmts: list = []
            while not self._check("DEDENT"):
                self._skip_newlines()
                if self._check("DEDENT"):
                    break
                # `assign_decorators` extension: see the matching check in
                # `parse()`'s module-level loop for why this must happen
                # before any decorator-consuming call, and why a plain
                # decorator above a nested `def`/`class` is unaffected.
                if self.ext_ctx.is_active("assign_decorators") and self._looks_like_assign_decorator():
                    stmts.extend(self._parse_assign_decorator_stmt())
                    self._skip_newlines()
                    continue
                result = self._parse_stmt()
                # Same list-vs-single-node tolerance as the inline-body
                # case just above -- see its comment for why.
                if isinstance(result, list):
                    stmts.extend(result)
                else:
                    stmts.append(result)
                self._skip_newlines()
            self._expect("DEDENT")
            return stmts
        finally:
            self._suite_depth -= 1

    # ---- statements --------------------------------------------------------

    def _parse_dotted_name_parts(self) -> list[str]:
        """Consume `NAME (. NAME)*` and return its parts as a flat list, e.g.
        `os.path.join` -> `["os", "path", "join"]`. The single shared
        primitive behind every dotted-name walk in this parser -- module
        paths (`import a.b.c`), class bases (`class X(module.Base):`),
        metaclass targets, except-clause types, default-value attribute
        chains, and quoted forward-reference/subscript annotation names all
        parse the exact same "one or more dot-separated identifiers" shape,
        previously via ~9 separate hand-written `while self._check("OP",
        "."): ...` loops scattered across this file that only differed in
        what shape they immediately built from the parts (a joined string, a
        list of A.Attr nodes, a bare leaf-name). Callers derive whichever of
        those shapes they need from the returned parts list (e.g.
        `".".join(parts)` for a flat string, or fold with A.Attr for an
        attribute-chain expression) instead of each re-implementing the walk
        itself -- so a future fix to how dotted names are consumed (e.g.
        stricter validation, a different soft-keyword interaction) only
        needs to change here, not at every call site again.
        """
        parts = [self._expect("NAME").value]
        while self._check("OP", "."):
            self._eat()
            parts.append(self._expect("NAME").value)
        return parts

    def _is_assign_target(self, expr) -> bool:
        """True if `expr` is a valid assignment-target SHAPE: a bare name, a
        subscript, an attribute access, or a (possibly nested) tuple/list of
        such -- real Python's own assignment-target grammar, which allows
        arbitrary nesting (`(a, [b, c.d]) = x` is legal CPython). Recursing
        into TupleLit/ListLit here means every assignment-parsing call site
        below gets nested-unpack support for free, instead of each needing
        its own repeat of this shape check (previously duplicated ~6 times,
        each only checking Name/Subscript/Attr and never recursing into a
        nested tuple/list target -- e.g. `(a, (b, c)) = x` worked at the top
        level but `((a, b), c) = x` did not, an arbitrary inconsistency of
        no benefit to anyone, just a byproduct of how many places happened
        to repeat the check).

        A single valid identifier (`A.Name`) is always assignable regardless
        of the surrounding shape (`(a, *[b, c]) = x` mixes it with a nested
        list-target). Note this deliberately does NOT accept `A.Starred`
        itself -- callers that allow a starred sub-target (tuple/list
        unpacking) check for it explicitly beside this predicate, since a
        bare `*x = v` at the top level is invalid Python (`_parse_tuple_
        assign` raises P_INVALID_ASSIGN_TARGET for exactly that shape) and a
        starred target is only ever valid *inside* a tuple/list target, not
        as a target in its own right.
        """
        if isinstance(expr, (A.Name, A.Subscript, A.Attr)):
            return True
        if isinstance(expr, (A.TupleLit, A.ListLit)):
            return bool(expr.elems) and all(
                self._is_assign_target(el) for el in expr.elems
            )
        return False

    def _parse_decorated_stmt(self):
        """A decorated `def`/`class` in statement position (inside a suite).

        Mirrors the module-level path in `parse()`: consume the decorator
        lines, take the general decorator EXPRESSIONS that `_eat_decorators`
        stashes, then apply them with the same `_desugar_function_decorators`
        rebinding used at module level -- so `@deco def inner(...)` really means
        `inner = deco(inner)` here too, rather than calling the decorator for
        its side effect and discarding the wrapper.

        Returns a LIST, which `_parse_block`/`parse` already flatten: a nested
        def lifts to module level and leaves behind at most a ClosureBind, and
        the decorator rebinding is one or more assignments after it. The
        ClosureBind must come FIRST -- it binds the captured values the lifted
        function needs, and the decorator call happens after that.
        """
        decorators = self._eat_decorators()
        pending = self._pending_decorator_exprs
        self._pending_decorator_exprs = []

        if self._check("KEYWORD", "class"):
            cdef = self._parse_classdef(decorators=decorators)
            self._nested_classes.append(cdef)
            out: list = [A.Pass(pos=cdef.pos)]
            out.extend(self._desugar_decorator_exprs(pending, cdef.name, cdef.pos))
            return out

        if not (
            self._check("KEYWORD", "def")
            or (
                self._check("KEYWORD", "async")
                and self._peek(1).kind == "KEYWORD"
                and self._peek(1).value == "def"
            )
        ):
            raise ParseError(
                "a decorator must be followed by a function or class definition",
                self._peek().pos,
                ErrorCode.P_EXPECTED_TOKEN,
            )

        fdef = self._parse_funcdef(decorators=decorators)
        fdef.is_lifted = True
        free_vars, nonlocal_vars = self._find_free_vars(fdef)
        fdef.free_vars = free_vars
        fdef.nonlocal_vars = nonlocal_vars
        self._nested_funcs.append(fdef)
        stmts: list = []
        if free_vars:
            stmts.append(
                A.ClosureBind(
                    func_name=fdef.name,
                    free_vars=free_vars,
                    nonlocal_vars=nonlocal_vars,
                    pos=fdef.pos,
                )
            )
        deco_stmts = self._desugar_function_decorators(fdef, pending)
        if not stmts and not deco_stmts:
            return [A.Pass(pos=fdef.pos)]
        stmts.extend(deco_stmts)
        return stmts

    def _parse_stmt(self):
        # A statement that BEGINS with `@` -- a decorated def/class inside any
        # suite, not just at module level.
        #
        # The nested-def arm below calls `_eat_decorators()`, but only after
        # dispatching on the `def` KEYWORD, so it could never see a leading
        # `@`: the current token was OP '@' and dispatch fell through to
        # "[P001] unexpected token OP '@'". Every decorated function inside
        # another function was a parse error, which is the Flask app-factory
        # shape (`def create_app(): @bp.route(...) def view(): ...`) and the
        # first thing that stopped a real project compiling.
        if self._check("OP", "@") and not (
            self.ext_ctx.is_active("assign_decorators")
            and self._looks_like_assign_decorator()
        ):
            return self._parse_decorated_stmt()
        t = self._peek()
        if t.kind == "KEYWORD":
            if t.value == "return":
                return self._parse_return()
            if t.value == "async":
                # `async` introduces def/for/with and nothing else. For
                # `async for` / `async with`, consume it and let the ordinary
                # statement parser take over -- the awaiting lives in the
                # desugaring (__anext__, __aenter__/__aexit__), not in the
                # syntax. For `async def`, leave BOTH tokens in place:
                # `_parse_funcdef` skips a leading `async` itself, so the `def`
                # arm further down handles it unchanged.
                #
                # This has to sit ahead of the for/with arms: eating `async`
                # advances past them, and control never comes back.
                nxt = self._peek(1)
                if nxt.kind == "KEYWORD" and nxt.value in ("for", "with"):
                    self._eat()
                    t = self._peek()
            if t.value == "if":
                return self._parse_if()
            if t.value == "while":
                return self._parse_while()
            if t.value == "for":
                return self._parse_for()
            if t.value == "pass":
                pos = self._eat().pos
                self._expect("NEWLINE")
                return A.Pass(pos=pos)
            if t.value == "break":
                pos = self._eat().pos
                self._expect("NEWLINE")
                return A.Break(pos=pos)
            if t.value == "continue":
                pos = self._eat().pos
                self._expect("NEWLINE")
                return A.Continue(pos=pos)
            if t.value == "def" or (
                t.value == "async"
                and self._peek(1).kind == "KEYWORD"
                and self._peek(1).value == "def"
            ):
                # Nested function definition: lift to module level. Detect
                # free variables (names referenced in the body but not in the
                # inner params or locally assigned) so codegen can build a
                # closure object bundling the captured values.
                decorators = self._eat_decorators()
                fdef = self._parse_funcdef(decorators=decorators)
                fdef.is_lifted = True
                free_vars, nonlocal_vars = self._find_free_vars(fdef)
                fdef.free_vars = free_vars
                fdef.nonlocal_vars = nonlocal_vars
                self._nested_funcs.append(fdef)
                if free_vars:
                    return A.ClosureBind(
                        func_name=fdef.name,
                        free_vars=free_vars,
                        nonlocal_vars=nonlocal_vars,
                        pos=fdef.pos,
                    )
                return A.Pass(pos=fdef.pos)
            if t.value == "class":
                cdef = self._parse_classdef(decorators=None)
                self._nested_classes.append(cdef)
                return A.Pass(pos=cdef.pos)
            if t.value == "import":
                return self._parse_import()
            if t.value == "from":
                return self._parse_from_import()
            if t.value == "try":
                return self._parse_try()
            if t.value == "with":
                return self._parse_with()
            if t.value == "raise":
                return self._parse_raise()
            if t.value == "assert":
                return self._parse_assert()
            if t.value == "global":
                return self._parse_global()
            if t.value == "nonlocal":
                return self._parse_nonlocal()
            if t.value == "del":
                return self._parse_del()
            if t.value == "yield":
                pos = self._eat().pos
                if self._check("NEWLINE"):
                    val: A.Expr = A.IntLit(value=0, pos=pos)  # bare yield
                else:
                    val = self._parse_expr()
                self._expect("NEWLINE")
                return A.YieldStmt(value=val, pos=pos)

        # `match` is a soft keyword (an ordinary NAME token): only treat it as
        # a match statement when the lookahead confirms `match <expr>:` shape,
        # so `match = 5` / `match(x)` / `match.foo` keep working as before.
        if t.kind == "NAME" and t.value == "match" and self._looks_like_match_stmt():
            return self._parse_match()

        # `final class` / `sealed class` nested inside a function body:
        # lifted to module level exactly like an ordinary nested `class`
        # (see the KEYWORD "class" branch above) -- append to
        # _nested_classes and leave a A.Pass placeholder in the enclosing
        # body.
        if t.kind == "NAME" and t.value == "final" and self._looks_like_final_class():
            cdef = self._dispatch_final_class()
            self._nested_classes.append(cdef)
            return A.Pass(pos=cdef.pos)
        if t.kind == "NAME" and t.value == "sealed" and self._looks_like_sealed_class():
            cdef = self._dispatch_sealed_class()
            self._nested_classes.append(cdef)
            return A.Pass(pos=cdef.pos)

        # `enum` nested inside a function body: unlike final/sealed class,
        # enum is module-scope-only (mirrors const's own restriction, see
        # `_parse_enum_decl`'s `_suite_depth != 0` check) -- so this
        # detection exists purely to route into that precise
        # P_EXTENSION_SCOPE diagnostic instead of falling through to
        # ordinary statement parsing, which would choke on the unconsumed
        # NAME/':' with a confusing generic error.
        if t.kind == "NAME" and t.value == "enum" and self._looks_like_enum_decl():
            return self._dispatch_enum_decl()  # raises P_EXTENSION_SCOPE

        # `interface` nested inside a function body: same module-scope-only
        # restriction and rationale as `enum` above.
        if t.kind == "NAME" and t.value == "interface" and self._looks_like_interface_decl():
            return self._dispatch_interface_decl()  # raises P_EXTENSION_SCOPE

        # Third-party extension statement handlers (asmpython.extend.Extension(...),
        # registered via --ext). Unlike `const`/`match`, a plugin-registered
        # keyword has no built-in shape lookahead the parser can check ahead
        # of time -- the whole point is the plugin decides its own grammar.
        # So once active, the keyword is unconditionally a statement prefix:
        # a real, documented trade-off (it can no longer double as a plain
        # variable name for the rest of this compile), acceptable because it
        # only ever applies when the invoker explicitly opted in via --ext.
        if t.kind == "NAME":
            handler = self.ext_ctx.handler_for(t.value)
            if handler is not None:
                _ext_name, callback = handler
                self._eat()  # the keyword itself
                return callback(self, t.pos)

        # Assignment / aug-assignment vs expression statement.
        if t.kind == "NAME":
            nxt = self._peek(1)
            if nxt.kind == "OP" and nxt.value == "=":
                return self._parse_assign()
            if nxt.kind == "OP" and nxt.value in AUG_OPS:
                return self._parse_aug_assign()
            # Annotated assignment / declaration: `name: type [= value]`.
            if nxt.kind == "OP" and nxt.value == ":":
                return self._parse_annotated_assign()
            # Tuple assignment: `a, b[, c]* = e1, e2[, e3]*` at statement
            # position. Only when the LHS is purely NAME,NAME,... NAME = .
            if nxt.kind == "OP" and nxt.value == ",":
                if self._looks_like_tuple_assign():
                    return self._parse_tuple_assign()

        # Starred unpack target: `a, *rest = xs` or `*init, last = xs`. The
        # `*name` form can also lead the LHS, so it's not caught by the
        # NAME-led check above.
        if t.kind == "OP" and t.value == "*" and self._looks_like_tuple_assign():
            return self._parse_tuple_assign()

        # Save state so we can detect "lhs[i] = rhs" -> IndexAssign and
        # "lhs.name = rhs" -> AttrAssign.
        pos = t.pos
        expr = self._parse_expr()
        # Parenthesized tuple-unpacking assignment: `(a, b, c) = e1, e2, e3`,
        # and its list-target sibling `[a, b, c] = e1, e2, e3` (real Python
        # allows both interchangeably). A leading `(` or `[` never reaches
        # the bare-NAME lookahead above (that only fires when the statement
        # starts with a NAME token), so both were always parsed here as a
        # plain TupleLit/ListLit expression first -- unwrap its elements
        # into targets when they're all valid assignment targets and `=`
        # follows, exactly like the bare-NAME tuple-unpack case just above
        # handles `a, b, c = ...` (real Python treats `(a, b) = x` and
        # `a, b = x` as equivalent). Confirmed via a real repro: PIL.Image.
        # py's `(a, b, c, d, e, f) = matrix`. Uses the general
        # `_is_assign_target` predicate (see its docstring) instead of an
        # inline Name/Subscript/Attr check, so a nested unpack target like
        # `(a, (b, c)) = x` -- or, now, `[a, [b, c]] = x` -- is accepted
        # here too, for free.
        if isinstance(expr, (A.TupleLit, A.ListLit)) and expr.elems and self._check("OP", "=") and all(
            self._is_assign_target(el) for el in expr.elems
        ):
            self._eat()  # '='
            values = [self._parse_expr()]
            while self._check("OP", ","):
                self._eat()
                values.append(self._parse_expr())
            self._expect("NEWLINE")
            return A.TupleAssign(targets=expr.elems, values=values, pos=pos)
        # Tuple assignment with at least one subscript/attribute target, e.g.
        # `xs[0], xs[1] = xs[1], xs[0]` or `a, self.x = self.x, a`. Pure
        # NAME-only sequences are handled above by `_parse_tuple_assign`.
        if self._is_assign_target(expr) and self._check("OP", ","):
            targets = [expr]
            while self._check("OP", ","):
                self._eat()
                tgt = self._parse_expr()
                if not self._is_assign_target(tgt):
                    raise ParseError("cannot assign to this expression", tgt.pos, ErrorCode.P_INVALID_ASSIGN_TARGET)
                targets.append(tgt)
            self._expect("OP", "=")
            values = [self._parse_expr()]
            while self._check("OP", ","):
                self._eat()
                values.append(self._parse_expr())
            self._expect("NEWLINE")
            # `a, *rest = 1, 2, 3, 4` -- a starred target with a bare tuple
            # RHS. CPython binds `rest` to a LIST either way, so this is
            # exactly `a, *rest = [1, 2, 3, 4]`; rewriting it here routes the
            # statement through the single-list unpack path that sema and
            # ir_lower already implement and test, instead of adding a second
            # lowering for a form that means the same thing.
            #
            # Only when a star is present: a plain `a, b = 1, 2` is a PARALLEL
            # assignment, not an unpack, and must keep its separate values.
            if len(values) > 1 and any(isinstance(t, A.StarTarget) for t in targets):
                values = [A.ListLit(elems=values, pos=pos)]
            return A.TupleAssign(targets=targets, values=values, pos=pos)
        if isinstance(expr, A.Subscript) and self._check("OP", "="):
            self._eat()
            # Bare (unparenthesised) tuple RHS: `d[k] = a, b`, same as plain
            # assignment's `x = a, b` -- _parse_expr() alone only ever
            # consumed the first element and left a stray comma for the
            # NEWLINE check to choke on. Confirmed via a real repro:
            # PIL.Image.py's `OPEN[id] = factory, accept`.
            #
            # A CHAINED assignment led by a subscript (`a[0] = a[1] = 9`) is
            # collected the same way the attribute-led branch below does it --
            # that path already existed, this one did not, so the second `=`
            # was left for the NEWLINE check and reported as
            # "[P002] expected NEWLINE, got OP '='". tests/cases/algo_prime_sieve.py
            # is exactly this shape (`is_prime[0] = is_prime[1] = False`).
            #
            # Speculative parse with rewind, for the same reason as the
            # attribute branch: lookahead alone cannot tell "another chained
            # target" from "the RHS value", since both start with an arbitrary
            # expression. Only a following `=` commits it as a target.
            chain_targets = [expr]
            while True:
                save = self.i
                nxt = self._parse_expr()
                if self._is_assign_target(nxt) and self._check("OP", "="):
                    self._eat()
                    chain_targets.append(nxt)
                    continue
                self.i = save
                break
            value = self._parse_tuple_rhs()
            self._expect("NEWLINE")
            if len(chain_targets) > 1:
                return self._desugar_chained_assign(chain_targets, value, pos)
            return A.IndexAssign(target=expr, value=value, pos=pos)
        if isinstance(expr, A.Subscript) and self._peek().kind == "OP" and self._peek().value in AUG_OPS:
            op_tok = self._eat()
            rhs = self._parse_expr()
            self._expect("NEWLINE")
            combined = A.BinOp(op=AUG_OPS[op_tok.value], left=expr, right=rhs, pos=op_tok.pos)
            return A.IndexAssign(target=expr, value=combined, pos=pos)
        if isinstance(expr, A.Attr) and self._check("OP", "="):
            self._eat()
            # Chained assignment led by an attribute target (`self.a =
            # self.b = value`) -- _parse_assign's own multi-target chain
            # only ever collects bare NAME tokens, since this statement
            # never even reaches it (a leading `self.x` is parsed as a
            # general expression here, not the NAME-led fast path
            # _parse_assign is only invoked from). Desugars using the SAME
            # "assign the real value to the LAST target, then have every
            # earlier target read the value back from it" trick
            # _parse_assign already uses for pure-name chains (safe here
            # too: reading an attribute immediately after storing to it
            # returns exactly what was stored, no descriptor/property
            # machinery modeled in between) -- returns a real list of
            # statements (one assignment per target) instead of inventing
            # a new "block of heterogeneous assignment types" AST node;
            # `_parse_block`'s own loop is the one caller updated to
            # extend rather than append when this returns a list (see its
            # own comment). Confirmed via a real repro: Pillow's own
            # TiffImagePlugin.py's `self.__first = self.__next =
            # self.tag_v2.next`.
            more_targets = [expr]
            while True:
                # Lookahead alone can't distinguish "another chained target
                # follows" from "the RHS value expression follows" (both
                # start with an arbitrary expression) -- so this speculatively
                # parses the next expression and only commits to treating it
                # as another target if a further `=` immediately follows;
                # otherwise rewind and let the normal RHS parse below run.
                save = self.i
                nxt = self._parse_expr()
                if self._is_assign_target(nxt) and self._check("OP", "="):
                    self._eat()
                    more_targets.append(nxt)
                    continue
                self.i = save
                break
            if len(more_targets) > 1:
                value = self._parse_tuple_rhs()
                self._expect("NEWLINE")
                return self._desugar_chained_assign(more_targets, value, pos)
            # Same bare-tuple-RHS allowance as the Subscript case just above.
            value = self._parse_tuple_rhs()
            self._expect("NEWLINE")
            return A.AttrAssign(obj=expr.obj, name=expr.name, value=value, pos=pos)
        if isinstance(expr, A.Attr) and self._check("OP", ":"):
            # `self.x: type [= value]`. Capture the annotation (sema types the
            # field from it) and lower to either `self.x = value` or a no-op
            # `self.x = 0` if no initializer is given.
            self._eat()
            annot = self._parse_type_annotation()
            if self._check("OP", "="):
                self._eat()
                value = self._parse_expr()
                self._expect("NEWLINE")
                return A.AttrAssign(
                    obj=expr.obj, name=expr.name, value=value, pos=pos, annot=annot
                )
            self._expect("NEWLINE")
            return A.AttrAssign(
                obj=expr.obj,
                name=expr.name,
                value=A.IntLit(value=0, pos=pos),
                annot=annot,
                pos=pos,
            )
        if isinstance(expr, A.Attr):
            # `self.x += rhs` form. Lowered to `self.x = self.x + rhs` so we
            # don't need a new IR node.
            t = self._peek()
            if t.kind == "OP" and t.value in AUG_OPS:
                self._eat()
                rhs = self._parse_expr()
                self._expect("NEWLINE")
                op = AUG_OPS[t.value]  # type: ignore[index]
                combined = A.BinOp(op=op, left=expr, right=rhs, pos=t.pos)
                return A.AttrAssign(
                    obj=expr.obj, name=expr.name, value=combined, pos=pos
                )
        self._expect("NEWLINE")
        return A.ExprStmt(expr=expr, pos=pos)

    def _exc_type_name(self, e) -> str | None:
        """The exception class name for an `except` clause's type expression:
        a bare name (`ValueError`) or a dotted attribute (`subprocess.
        CalledProcessError`, `pkg.mod.MyError`) -- in both cases asmpython's
        flat whole-program class namespace only cares about the final
        component. Returns `None` for any other expression shape."""
        if isinstance(e, A.Name):
            return e.name
        if isinstance(e, A.Attr):
            return e.name
        return None

    def _parse_try(self) -> A.Try:
        kw = self._expect("KEYWORD", "try")
        self._expect("OP", ":")
        body = self._parse_block()
        self._skip_newlines()
        # One or more `except` clauses, each with an optional exception type
        # (or tuple of types) and optional `as name` binding.
        handlers: list = []  # list[(types, bind_name, body)]
        while self._check("KEYWORD", "except"):
            tok = self._eat()
            types: list[str] = []
            if not self._check("OP", ":") and not self._check("KEYWORD", "as"):
                type_expr = self._parse_expr()
                name = self._exc_type_name(type_expr)
                if name is not None:
                    types = [name]
                elif isinstance(type_expr, A.TupleLit) and all(
                    self._exc_type_name(e) is not None for e in type_expr.elems
                ):
                    types = [self._exc_type_name(e) for e in type_expr.elems]  # type: ignore[misc]
                else:
                    raise ParseError(
                        "'except' type must be a name, a dotted name, or a "
                        "tuple of names",
                        tok.pos,
                    )
            bind_name = None
            if self._check("KEYWORD", "as"):
                self._eat()
                bind_name = self._expect("NAME").value
            self._expect("OP", ":")
            hbody = self._parse_block()
            handlers.append((types, bind_name, hbody))
            self._skip_newlines()
        # Optional `else:` (runs when no exception fired) and `finally:`.
        else_body: list = []
        if self._check("KEYWORD", "else"):
            self._eat()
            self._expect("OP", ":")
            else_body = self._parse_block()
            self._skip_newlines()
        finally_body: list = []
        if self._check("NAME", "finally") or self._check("KEYWORD", "finally"):
            self._eat()
            self._expect("OP", ":")
            finally_body = self._parse_block()
        if not handlers and not finally_body:
            raise ParseError(
                "'try' must be followed by 'except' or 'finally'", self._peek().pos
            )
        first_types, first_bind, first_body = (
            handlers[0] if handlers else ([], None, [])
        )
        return A.Try(
            body=body,
            handler=first_body,
            bind_name=first_bind,  # type: ignore[arg-type]
            handler_types=first_types,
            extra_handlers=handlers[1:],
            else_body=else_body,
            finally_body=finally_body,
            pos=kw.pos,
        )

    def _parse_with(self) -> "A.With":
        kw = self._expect("KEYWORD", "with")
        items: list[tuple] = []
        while True:
            expr = self._parse_expr()
            name: str | None = None
            if self._check("KEYWORD", "as"):
                self._eat()
                name = self._expect("NAME").value
            items.append((expr, name))
            if self._check("OP", ","):
                self._eat()
                continue
            break
        self._expect("OP", ":")
        body = self._parse_block()
        # Desugar `with a as x, b as y: body` into nested
        # `with a as x: with b as y: body` -- sema's A.With -> A.Try rewrite
        # then handles each level (and each level's __enter__/__exit__) the
        # same as a single `with`, innermost first.
        with_stmt: A.With
        for expr, name in reversed(items):
            with_stmt = A.With(expr=expr, name=name, body=body, pos=kw.pos)
            body = [with_stmt]
        return with_stmt

    def _looks_like_final_class(self) -> bool:
        """`final` is a soft keyword: only `final class ...` is the class-
        finality shape (an ordinary `final = 5` or `final.method()` keeps
        working). Mirrors `_looks_like_const_decl`'s save/rewind idiom."""
        save = self.i
        self._eat()  # 'final'
        ok = self._check("KEYWORD", "class")
        self.i = save
        return ok

    def _parse_final_class(self) -> A.ClassDef:
        kw = self._expect("NAME", "final")
        cdef = self._parse_classdef(decorators=None)
        cdef.is_final = True
        cdef.pos = kw.pos
        return cdef

    def _dispatch_final_class(self) -> A.ClassDef:
        """Shared by both the module-level and nested-statement dispatch
        sites: parse `final class ...` if the extension is active, else
        raise the precise "shape matched but not activated" diagnostic
        (mirrors `const`'s own dispatch, see _parse_stmt)."""
        if self.ext_ctx.is_active("final"):
            return self._parse_final_class()
        raise ParseError(
            "'final class' is not supported -- asmpython's compiler-"
            "extension system was withdrawn (see archived/extensions/)",
            self._peek().pos,
            ErrorCode.P_FINAL_WITHOUT_EXTENSION,
        )

    def _looks_like_sealed_class(self) -> bool:
        """`sealed` is a soft keyword, same shape-check as `final`."""
        save = self.i
        self._eat()  # 'sealed'
        ok = self._check("KEYWORD", "class")
        self.i = save
        return ok

    def _parse_sealed_class(self) -> A.ClassDef:
        kw = self._expect("NAME", "sealed")
        cdef = self._parse_classdef(decorators=None)
        cdef.is_sealed = True
        cdef.pos = kw.pos
        return cdef

    def _dispatch_sealed_class(self) -> A.ClassDef:
        """Shared dispatch helper -- see `_dispatch_final_class`."""
        if self.ext_ctx.is_active("sealed"):
            return self._parse_sealed_class()
        raise ParseError(
            "'sealed class' is not supported -- asmpython's compiler-"
            "extension system was withdrawn (see archived/extensions/)",
            self._peek().pos,
            ErrorCode.P_SEALED_WITHOUT_EXTENSION,
        )

    def _looks_like_enum_decl(self) -> bool:
        """`enum` is a soft keyword: only `enum NAME:` followed by NEWLINE
        (then an indented block of member lines) is the enum-declaration
        shape -- an ordinary `enum = 5` or `enum.method()` keeps working.
        Mirrors `_looks_like_match_stmt`'s save/rewind idiom (both need to
        look past the trailing `:` + NEWLINE, not just the leading tokens
        const's own lookahead stops at)."""
        save = self.i
        self._eat()  # 'enum'
        ok = False
        if self._check("NAME"):
            self._eat()
            ok = self._check("OP", ":")
        self.i = save
        return ok

    def _parse_enum_decl(self) -> A.EnumDecl:
        kw = self._expect("NAME", "enum")
        if self._suite_depth != 0:
            raise ParseError(
                "'enum' may only appear at module scope",
                kw.pos,
                ErrorCode.P_EXTENSION_SCOPE,
            )
        name_tok = self._expect("NAME")
        self._expect("OP", ":")
        self._expect("NEWLINE")
        self._skip_newlines()
        self._expect("INDENT")
        members: list = []
        next_auto = 0
        while not self._check("DEDENT"):
            self._skip_newlines()
            if self._check("DEDENT"):
                break
            m_tok = self._expect("NAME")
            value = None
            if self._check("OP", "="):
                self._eat()
                sign = 1
                if self._check("OP", "-"):
                    self._eat()
                    sign = -1
                v_tok = self._expect("INT")
                value = sign * int(v_tok.value)
            self._expect("NEWLINE")
            resolved_value = value if value is not None else next_auto
            members.append((m_tok.value, resolved_value))
            next_auto = resolved_value + 1
            self._skip_newlines()
        self._expect("DEDENT")
        if not members:
            raise ParseError(
                "'enum' declaration has no members", kw.pos,
            )
        return A.EnumDecl(name=name_tok.value, members=members, pos=kw.pos)

    def _dispatch_enum_decl(self) -> A.EnumDecl:
        """Shared dispatch helper -- see `_dispatch_final_class`."""
        if self.ext_ctx.is_active("enum"):
            return self._parse_enum_decl()
        raise ParseError(
            "'enum' declarations are not supported -- asmpython's "
            "compiler-extension system was withdrawn (see "
            "archived/extensions/)",
            self._peek().pos,
            ErrorCode.P_ENUM_WITHOUT_EXTENSION,
        )

    def _looks_like_interface_decl(self) -> bool:
        """`interface` is a soft keyword, same shape-check as `enum`:
        only `interface NAME:` followed by NEWLINE is the declaration
        shape."""
        save = self.i
        self._eat()  # 'interface'
        ok = False
        if self._check("NAME"):
            self._eat()
            ok = self._check("OP", ":")
        self.i = save
        return ok

    def _parse_interface_decl(self) -> A.InterfaceDecl:
        kw = self._expect("NAME", "interface")
        if self._suite_depth != 0:
            raise ParseError(
                "'interface' may only appear at module scope",
                kw.pos,
                ErrorCode.P_EXTENSION_SCOPE,
            )
        name_tok = self._expect("NAME")
        self._expect("OP", ":")
        self._expect("NEWLINE")
        self._skip_newlines()
        self._expect("INDENT")
        methods: list = []
        while not self._check("DEDENT"):
            self._skip_newlines()
            if self._check("DEDENT"):
                break
            if self._check("KEYWORD", "pass"):
                self._eat()
                self._expect("NEWLINE")
                self._skip_newlines()
                continue
            if not self._check("KEYWORD", "def"):
                raise ParseError(
                    "'interface' bodies may only contain method stubs "
                    "('def name(...): pass') or 'pass'",
                    self._peek().pos,
                )
            stub = self._parse_funcdef()
            # A stub is a signature-only declaration: its body must be
            # exactly `pass`, never real code -- no precedent to reuse here
            # (this is the first construct in this compiler requiring a
            # constrained/empty body shape), so this checks the parsed
            # body directly rather than special-casing the tokenizer.
            if not (len(stub.body) == 1 and isinstance(stub.body[0], A.Pass)):
                raise ParseError(
                    f"interface method {stub.name!r}'s body must be "
                    f"exactly 'pass' -- interface stubs declare a "
                    f"signature only, never real code",
                    kw.pos,
                    ErrorCode.P_INTERFACE_STUB_BODY,
                )
            methods.append(stub)
            self._skip_newlines()
        self._expect("DEDENT")
        if not methods:
            raise ParseError("'interface' declaration has no methods", kw.pos)
        return A.InterfaceDecl(name=name_tok.value, methods=methods, pos=kw.pos)

    def _dispatch_interface_decl(self) -> A.InterfaceDecl:
        """Shared dispatch helper -- see `_dispatch_final_class`."""
        if self.ext_ctx.is_active("interface"):
            return self._parse_interface_decl()
        raise ParseError(
            "'interface' declarations are not supported -- asmpython's "
            "compiler-extension system was withdrawn (see "
            "archived/extensions/)",
            self._peek().pos,
            ErrorCode.P_INTERFACE_WITHOUT_EXTENSION,
        )

    def _looks_like_assign_decorator(self) -> bool:
        """`assign_decorators` extension: only `@NAME` followed by NEWLINE
        then a statement that ISN'T `def`/`class` (or another `@`-prefixed
        decorator line -- multiple stacked decorators only make sense for
        the def/class case, not this one) is this shape. A plain `@deco`
        above `def`/`class` must keep going through the existing decorator
        machinery completely untouched -- checked via save/rewind, the
        same idiom every other soft-keyword lookahead in this parser uses."""
        save = self.i
        ok = False
        if self._check("OP", "@"):
            self._eat()
            if self._check("NAME"):
                self._eat()
                # Dotted (`@mod.deco`) or called (`@deco(...)`) forms are
                # handled (and rejected with a precise error) inside
                # _parse_assign_decorator_stmt itself -- here, just confirm
                # the line ends in NEWLINE eventually and doesn't lead into
                # `def`/`class`/another `@`.
                while self._check("OP", "."):
                    self._eat()
                    if not self._check("NAME"):
                        break
                    self._eat()
                if self._check("OP", "("):
                    depth = 0
                    while True:
                        tk = self._peek()
                        if tk.kind == "EOF":
                            break
                        if tk.kind == "OP" and tk.value == "(":
                            depth += 1
                        elif tk.kind == "OP" and tk.value == ")":
                            depth -= 1
                            if depth == 0:
                                self._eat()
                                break
                        self._eat()
                if self._check("NEWLINE"):
                    self._eat()
                    while self._check("NEWLINE"):
                        self._eat()
                    ok = not (
                        self._check("KEYWORD", "def")
                        or self._check("KEYWORD", "class")
                        or self._check("OP", "@")
                    )
        self.i = save
        return ok

    def _parse_assign_decorator_stmt(self) -> list:
        """`assign_decorators` extension: `@decorator` above an assignment
        statement. Returns a LIST of statements (unlike every other
        `_parse_*` statement helper, which returns exactly one) since the
        desugaring is inherently multi-statement -- callers (`parse()`'s
        module-level loop and `_parse_block`) extend their statement list
        with the result instead of appending a single node.

        Confirmed exact semantics: `@decorator` above `x = 5` desugars to
        `x = 5; decorator(x, 5)` -- the assignment stands unchanged, then a
        synthetic call passes the bound name's value and the initializer
        value as two positional args, purely for side effects (return
        value discarded, never feeds back into the binding). For a
        single-call tuple-unpack target, `@decorator` above `(a, b) = f()`
        desugars to `__deco_tmp_N = f(); a, b = __deco_tmp_N;
        decorator((a, b), __deco_tmp_N)` -- a synthetic temp guarantees
        `f()` evaluates exactly once (reused for both the unpack and the
        second decorator arg), reusing the existing
        `_deco_tmp_counter`/`__deco_tmp_N` naming convention
        `_desugar_decorator_exprs` already established for the unrelated
        def-decorator-factory case.

        Unlike every other decorator use in this grammar (always `def`/
        `class`), this is a genuinely new decorator-application TARGET, so
        it's handled as its own small parser-level desugaring rather than
        reusing `_eat_decorators`/`_desugar_decorator_exprs` (those exist
        to solve a different problem: applying a factory's *return value*
        as a wrapper, whereas this calls `decorator` itself directly, one
        call, no wrapper indirection needed -- so no `expr(...)(...)`
        parsing limitation to route around here).
        """
        self._eat()  # '@'
        deco_pos = self._peek().pos
        deco_name_tok = self._expect("NAME")
        deco_call_name = deco_name_tok.value
        while self._check("OP", "."):
            # A dotted decorator target (`@mod.decorator`) has no direct
            # call-by-name representation (A.Call.func is a bare str, the
            # callee must be a plain top-level function name) -- reject
            # explicitly rather than silently miscompiling to the wrong
            # callee.
            raise ParseError(
                "'@decorator' above an assignment only supports a plain "
                "function name, not a dotted attribute",
                deco_pos,
                ErrorCode.P_ASSIGN_DECORATOR_UNSUPPORTED_TARGET,
            )
        self._expect("NEWLINE")
        self._skip_newlines()
        stmt = self._parse_stmt()
        if isinstance(stmt, A.Assign):
            # Both decorator args reference the bound NAME (not `stmt.value`
            # -- reusing that raw RHS expression node directly here would
            # make codegen re-evaluate/re-execute it a second time wherever
            # it's lowered, silently double-running any side effect the
            # initializer has; confirmed via a real test with a
            # side-effecting call as the RHS producing the wrong count
            # before this fix). Reading the name twice after a real
            # assignment is always safe and side-effect-free.
            call = A.Call(
                func=deco_call_name,
                args=[
                    A.Name(name=stmt.target, pos=deco_pos),
                    A.Name(name=stmt.target, pos=deco_pos),
                ],
                pos=deco_pos,
            )
            return [stmt, A.ExprStmt(expr=call, pos=deco_pos)]
        if isinstance(stmt, A.TupleAssign) and len(stmt.values) == 1:
            target_names = [t for t in stmt.targets if isinstance(t, A.Name)]
            if len(target_names) != len(stmt.targets):
                raise ParseError(
                    "'@decorator' above an assignment only supports a "
                    "single-target or single-call tuple-unpack assignment",
                    deco_pos,
                    ErrorCode.P_ASSIGN_DECORATOR_UNSUPPORTED_TARGET,
                )
            self._deco_tmp_counter += 1
            tmp_name = f"__deco_tmp_{self._deco_tmp_counter}"
            tmp_assign = A.Assign(target=tmp_name, value=stmt.values[0], pos=deco_pos)
            stmt.values = [A.Name(name=tmp_name, pos=deco_pos)]
            call = A.Call(
                func=deco_call_name,
                args=[
                    A.TupleLit(
                        elems=[A.Name(name=t.name, pos=deco_pos) for t in target_names],
                        pos=deco_pos,
                    ),
                    A.Name(name=tmp_name, pos=deco_pos),
                ],
                pos=deco_pos,
            )
            return [tmp_assign, stmt, A.ExprStmt(expr=call, pos=deco_pos)]
        raise ParseError(
            "'@decorator' above an assignment only supports a "
            "single-target or single-call tuple-unpack assignment",
            deco_pos,
            ErrorCode.P_ASSIGN_DECORATOR_UNSUPPORTED_TARGET,
        )

    def _looks_like_match_stmt(self) -> bool:
        """`match` is a soft keyword: only `match <expr>:` followed by
        NEWLINE (then an indented block of `case` clauses) is a match
        statement. Anything else (`match = 5`, `match(x)`, `match.attr`,
        `match[i] = v`, ...) is a normal NAME-led statement, so this
        speculatively parses the subject expression and rewinds regardless of
        the outcome."""
        save = self.i
        self._eat()  # 'match'
        ok = False
        try:
            self._parse_expr()
            ok = self._check("OP", ":") and self._peek(1).kind == "NEWLINE"
        except ParseError:
            ok = False
        self.i = save
        return ok

    def _parse_match(self) -> A.Match:
        kw = self._eat()  # 'match'
        subject = self._parse_expr()
        self._expect("OP", ":")
        self._expect("NEWLINE")
        self._skip_newlines()
        self._expect("INDENT")
        cases: list = []
        while not self._check("DEDENT"):
            self._skip_newlines()
            if self._check("DEDENT"):
                break
            cases.append(self._parse_case())
            self._skip_newlines()
        self._expect("DEDENT")
        return A.Match(subject=subject, cases=cases, pos=kw.pos)

    def _parse_case(self) -> tuple:
        self._expect("NAME", "case")  # soft keyword, like 'match'
        pattern = self._parse_case_pattern()
        guard = None
        if self._check("KEYWORD", "if"):
            self._eat()
            guard = self._parse_expr()
        self._expect("OP", ":")
        body = self._parse_block()
        return (pattern, guard, body)

    def _parse_case_pattern(self) -> "A.Pattern":
        """Top-level pattern of a `case` clause: a single pattern, or an
        unparenthesized sequence pattern (`case a, b:`, `case a, *rest:`)."""
        star_index: "int | None"
        if self._check("OP", "*"):
            star_index = 0
            patterns = [self._parse_star_pattern()]
        else:
            first = self._parse_as_pattern()
            if not self._check("OP", ","):
                return first
            patterns = [first]
            star_index = None
        pos = patterns[0].pos
        while self._check("OP", ","):
            self._eat()
            if self._check("OP", ":") or self._check("KEYWORD", "if"):
                break  # trailing comma
            if self._check("OP", "*"):
                if star_index is not None:
                    raise ParseError(
                        "multiple starred names in sequence pattern",
                        self._peek().pos,
                    )
                star_index = len(patterns)
                patterns.append(self._parse_star_pattern())
            else:
                patterns.append(self._parse_as_pattern())
        return A.MatchSequence(patterns=patterns, star_index=star_index, pos=pos)

    def _parse_star_pattern(self) -> "A.Pattern":
        """`*name` / `*_` inside a sequence pattern."""
        pos = self._expect("OP", "*").pos
        name = self._expect("NAME").value
        return A.MatchCapture(name=name, pos=pos)

    def _parse_as_pattern(self) -> "A.Pattern":
        pat = self._parse_or_pattern()
        if self._check("KEYWORD", "as"):
            pos = self._eat().pos
            name = self._expect("NAME").value
            return A.MatchAs(pattern=pat, name=name, pos=pos)
        return pat

    def _parse_or_pattern(self) -> "A.Pattern":
        first = self._parse_closed_pattern()
        if not self._check("OP", "|"):
            return first
        alts = [first]
        while self._check("OP", "|"):
            self._eat()
            alts.append(self._parse_closed_pattern())
        return A.MatchOr(patterns=alts, pos=first.pos)

    def _parse_sequence_items(self, close: str) -> tuple:
        """Comma-separated pattern items up to (not including) the `close`
        OP token. Returns (patterns, star_index, saw_trailing_comma)."""
        patterns: list = []
        star_index: "int | None" = None
        saw_comma = False
        while not self._check("OP", close):
            if self._check("OP", "*"):
                if star_index is not None:
                    raise ParseError(
                        "multiple starred names in sequence pattern",
                        self._peek().pos,
                    )
                star_index = len(patterns)
                patterns.append(self._parse_star_pattern())
            else:
                patterns.append(self._parse_as_pattern())
            if self._check("OP", ","):
                self._eat()
                saw_comma = True
                continue
            break
        return patterns, star_index, saw_comma

    def _parse_closed_pattern(self) -> "A.Pattern":
        t = self._peek()
        if t.kind == "OP" and t.value == "(":
            pos = self._eat().pos
            patterns, star_index, saw_comma = self._parse_sequence_items(")")
            self._expect("OP", ")")
            if len(patterns) == 1 and star_index is None and not saw_comma:
                return patterns[0]  # `(pattern)` is a grouping, not a 1-sequence
            return A.MatchSequence(patterns=patterns, star_index=star_index, pos=pos)
        if t.kind == "OP" and t.value == "[":
            pos = self._eat().pos
            patterns, star_index, _ = self._parse_sequence_items("]")
            self._expect("OP", "]")
            return A.MatchSequence(patterns=patterns, star_index=star_index, pos=pos)
        if t.kind == "OP" and t.value == "{":
            pos = self._eat().pos
            keys: list = []
            patterns_map: list = []
            if not (self._peek().kind == "OP" and self._peek().value == "}"):
                while True:
                    key_tok = self._peek()
                    if key_tok.kind != "STRING":
                        raise ParseError(
                            "mapping pattern keys must be string literals", key_tok.pos
                        )
                    self._eat()
                    keys.append(key_tok.value)
                    self._expect("OP", ":")
                    patterns_map.append(self._parse_or_pattern())
                    if not (self._peek().kind == "OP" and self._peek().value == ","):
                        break
                    self._eat()
                    if self._peek().kind == "OP" and self._peek().value == "}":
                        break
            self._expect("OP", "}")
            return A.MatchMapping(keys=keys, patterns=patterns_map, pos=pos)
        if t.kind == "OP" and t.value == "-":
            self._eat()
            n = self._peek()
            if n.kind == "INT":
                self._eat()
                return A.MatchValue(
                    value=A.IntLit(value=-n.value, pos=t.pos), pos=t.pos  # type: ignore
                )
            if n.kind == "FLOAT":
                self._eat()
                return A.MatchValue(
                    value=A.FloatLit(value=-n.value, pos=t.pos), pos=t.pos  # type: ignore
                )
            raise ParseError("expected a number after '-' in pattern", n.pos)
        if t.kind == "INT":
            self._eat()
            return A.MatchValue(value=A.IntLit(value=t.value, pos=t.pos), pos=t.pos)  # type: ignore
        if t.kind == "FLOAT":
            self._eat()
            return A.MatchValue(value=A.FloatLit(value=t.value, pos=t.pos), pos=t.pos)  # type: ignore
        if t.kind == "STRING":
            self._eat()
            return A.MatchValue(value=A.StrLit(value=t.value, pos=t.pos), pos=t.pos)  # type: ignore
        if t.kind == "KEYWORD" and t.value in ("True", "False"):
            self._eat()
            val = A.IntLit(value=1 if t.value == "True" else 0, pos=t.pos, is_bool=True)
            return A.MatchValue(value=val, pos=t.pos)
        if t.kind == "KEYWORD" and t.value == "None":
            self._eat()
            return A.MatchValue(
                value=A.IntLit(value=0, pos=t.pos, is_none=True), pos=t.pos
            )
        if t.kind == "NAME":
            if t.value == "_":
                self._eat()
                return A.MatchCapture(name="_", pos=t.pos)
            name_tok = self._eat()
            if self._check("OP", "."):
                # Dotted value pattern (`case Color.RED:`) or a dotted class
                # pattern (`case mod.ClassName(...):`, last segment is the
                # class). Resolve once the chain ends.
                value: A.Expr = A.Name(name=name_tok.value, pos=name_tok.pos)
                last_name = name_tok.value
                while self._check("OP", "."):
                    self._eat()
                    last_name = self._expect("NAME").value
                    value = A.Attr(obj=value, name=last_name, pos=name_tok.pos)
                if self._check("OP", "("):
                    return self._parse_class_pattern(last_name, name_tok.pos)
                return A.MatchValue(value=value, pos=name_tok.pos)
            if self._check("OP", "("):
                return self._parse_class_pattern(name_tok.value, name_tok.pos)
            return A.MatchCapture(name=name_tok.value, pos=name_tok.pos)
        raise ParseError(f"invalid pattern: unexpected {t.kind} {t.value!r}", t.pos)

    def _parse_class_pattern(self, cls_name: str, pos) -> "A.MatchClass":
        self._expect("OP", "(")
        positional: list = []
        kwargs: list = []
        while not self._check("OP", ")"):
            if (
                self._check("NAME")
                and self._peek(1).kind == "OP"
                and self._peek(1).value == "="
            ):
                kwname = self._eat().value
                self._eat()  # '='
                kwargs.append((kwname, self._parse_as_pattern()))
            else:
                if kwargs:
                    raise ParseError(
                        "positional pattern follows keyword pattern",
                        self._peek().pos,
                    )
                positional.append(self._parse_as_pattern())
            if self._check("OP", ","):
                self._eat()
                continue
            break
        self._expect("OP", ")")
        return A.MatchClass(
            cls_name=cls_name, positional=positional, kwargs=kwargs, pos=pos
        )

    def _parse_raise(self) -> A.Raise:
        kw = self._expect("KEYWORD", "raise")
        # Bare `raise` (no expression) re-raises the currently-active
        # exception; only valid inside an `except` handler.
        if self._check("NEWLINE"):
            self._eat()
            return A.Raise(value=None, pos=kw.pos)
        value = self._parse_expr()
        # `raise X from Y` — exception chaining. asmpython doesn't model the
        # __cause__ link, so the cause expression is parsed and discarded.
        if self._check("KEYWORD", "from"):
            self._eat()
            self._parse_expr()
        self._expect("NEWLINE")
        return A.Raise(value=value, pos=kw.pos)

    def _parse_assert(self) -> A.If:
        """`assert cond[, msg]` desugars to `if not cond: raise <msg>`.

        With no message we synthesize the string "AssertionError" so the
        runtime print has something useful. The msg expression (when given)
        must evaluate to str — sema enforces that on the lowered Raise.
        """
        kw = self._expect("KEYWORD", "assert")
        cond = self._parse_expr()
        if self._check("OP", ","):
            self._eat()
            msg = self._parse_expr()
        else:
            msg = A.StrLit(value="AssertionError", pos=kw.pos)
        self._expect("NEWLINE")
        negated = A.UnaryOp(op="not", operand=cond, pos=kw.pos)
        raise_stmt = A.Raise(value=msg, pos=kw.pos)
        return A.If(test=negated, then=[raise_stmt], orelse=[], pos=kw.pos)

    def _parse_global(self) -> "A.Global":
        kw = self._expect("KEYWORD", "global")
        names = [self._expect("NAME").value]
        while self._check("OP", ","):
            self._eat()
            names.append(self._expect("NAME").value)
        self._expect("NEWLINE")
        return A.Global(names=names, pos=kw.pos)

    def _parse_nonlocal(self) -> "A.Nonlocal":
        kw = self._expect("KEYWORD", "nonlocal")
        names = [self._expect("NAME").value]
        while self._check("OP", ","):
            self._eat()
            names.append(self._expect("NAME").value)
        self._expect("NEWLINE")
        return A.Nonlocal(names=names, pos=kw.pos)

    def _parse_del(self) -> "A.Del":
        kw = self._expect("KEYWORD", "del")
        target = self._parse_expr()
        # Multi-target del (`del a, b, c`) -- each target is a fully
        # independent deletion (no shared semantics between them, unlike
        # e.g. a tuple-assignment's shared RHS), so `del a, b, c` and
        # `del a; del b; del c` behave identically. Wraps the targets in
        # an A.TupleLit (an existing, general "list of expressions" AST
        # shape already used elsewhere) rather than widening A.Del's own
        # `target: Expr` field to a list -- every one of A.Del's 7 call
        # sites across ir_lower/codegen/sema/program checks
        # `isinstance(target, A.TupleLit)` and iterates `.elems` when
        # present, needing no change to A.Del's shape or its single-target
        # callers. Confirmed via a real repro: Pillow's own
        # TiffImagePlugin.py's `del _load_dispatch, _write_dispatch, idx,
        # name` module-level cleanup line.
        if self._check("OP", ","):
            elems = [target]
            while self._check("OP", ","):
                self._eat()
                if self._check("NEWLINE"):
                    break  # trailing comma
                elems.append(self._parse_expr())
            target = A.TupleLit(elems=elems, pos=kw.pos)
        self._expect("NEWLINE")
        return A.Del(target=target, pos=kw.pos)

    def _parse_import(self):
        """`import a` / `import a.b as c` / `import a, b as c, d`

        Returns a single A.Import for the one-module form and a LIST when the
        line names several. Python allows a comma-separated list here and each
        entry may carry its own `as` alias; only the first was parsed, so
        `import csv, io` stopped at the comma and raised
        "[P002] expected NEWLINE, got OP ','". The `from x import a, b` form
        already accepted a list, so this was a gap in one of the two import
        statements rather than a missing concept.

        A list is safe to return: parse() and _parse_block already flatten a
        statement that expands to several (chained assignment does the same),
        and each entry gets its own A.Import so alias handling, bound-name
        recording and module resolution stay exactly as they are for a
        single-module line.
        """
        kw = self._expect("KEYWORD", "import")
        first = self._parse_one_import_clause(kw)
        if not self._check("OP", ","):
            self._expect("NEWLINE")
            return first
        out: list = [first]
        while self._check("OP", ","):
            self._eat()
            if self._check("NEWLINE"):
                break  # tolerate a trailing comma
            out.append(self._parse_one_import_clause(kw))
        self._expect("NEWLINE")
        return out

    def _parse_one_import_clause(self, kw) -> A.Import:
        """One `<dotted name> [as <alias>]` entry of an import statement.

        Consumes no NEWLINE and no comma, so the caller decides whether more
        entries follow.
        """
        # Dotted module path: `import os.path`. Joined into one flat string.
        name = ".".join(self._parse_dotted_name_parts())
        # Optional `as` alias: keep the full dotted path in `module` (needed
        # to resolve the real file -- a prior version of this collapsed
        # `module` to just the alias, e.g. `import lumen.audio as audio`
        # became module="audio", an import_program-merge could never find,
        # so the whole submodule silently never got pulled in and every
        # `audio.x` call/attribute on it silently fell through to an
        # opaque-int default instead of erroring or working) and the local
        # bound name in `alias` separately.
        alias: "str | None" = None
        if self._check("KEYWORD", "as"):
            self._eat()
            alias = self._expect("NAME").value  # type: ignore[assignment]
        self._import_bound_names.add(alias if alias else name.split(".")[0])
        self._imported_names.add(alias or name.split(".")[0])
        return A.Import(module=name, alias=alias, pos=kw.pos)  # type: ignore

    def _parse_from_import(self) -> A.FromImport:
        kw = self._expect("KEYWORD", "from")
        # Leading dots: `from .x import y`, `from .. import z`, etc.
        level = 0
        while self._check("OP", "."):
            self._eat()
            level += 1
        module = ""
        if self._check("NAME"):
            # Dotted module path: `from a.b.c import x`. Joined into one
            # flat string so sema sees `a.b.c`.
            module = ".".join(self._parse_dotted_name_parts())
        elif level == 0:
            # `from import ...` with no module name is invalid.
            raise ParseError("expected module name after 'from'", self._peek().pos, ErrorCode.P_MISSING_MODULE)
        self._expect("KEYWORD", "import")
        # `from x import (a, b, ...)`: the lexer already suppresses NEWLINE
        # tokens while paren_depth > 0 (same mechanism that lets a call's
        # arguments span lines), so this only needs to consume the optional
        # parens around the name list.
        parenthesized = self._check("OP", "(")
        if parenthesized:
            self._eat()
        # `names` holds the locally-bound name (the alias when `as` is present);
        # `orig_names` holds the exported name as the source module spells it.
        first: str = self._expect("NAME").value  # type: ignore[assignment]
        names: list[str] = [first]
        orig_names: list[str] = [first]
        if self._check("KEYWORD", "as"):
            self._eat()
            names[-1] = self._expect("NAME").value  # type: ignore[assignment]
        while self._check("OP", ","):
            self._eat()
            # Allow a trailing comma before the closing paren: `(a, b,)`.
            if parenthesized and self._check("OP", ")"):
                break
            nm: str = self._expect("NAME").value  # type: ignore[assignment]
            names.append(nm)
            orig_names.append(nm)
            if self._check("KEYWORD", "as"):
                self._eat()
                names[-1] = self._expect("NAME").value  # type: ignore[assignment]
        if parenthesized:
            self._expect("OP", ")")
        self._expect("NEWLINE")
        self._import_bound_names.update(names)
        self._imported_names.update(names)
        return A.FromImport(  # type: ignore
            module=module, names=names, orig_names=orig_names, pos=kw.pos, level=level
        )

    def _parse_assign(self) -> A.Assign:
        name_tok = self._expect("NAME")
        self._expect("OP", "=")
        # Collect chained targets: `a = b = c = 0` — all but last are targets.
        targets = [name_tok]
        while (
            self._check("NAME")
            and self._peek(1).kind == "OP"
            and self._peek(1).value == "="
        ):
            targets.append(self._eat())
            self._eat()  # consume `=`
        value = self._parse_tuple_rhs()
        self._expect("NEWLINE")
        # Emit a sequence: assign value to last target, then copy to all earlier.
        # Simple approach: for N>1 targets, evaluate value into the last target
        # then assign each earlier target the same value expression.
        # Since multiple assignment is `a = b = expr`, and expr is evaluated once,
        # we return the first assignment; the extra ones are emitted by returning
        # a MultiAssign node or by chaining. For now, emit only the rightmost
        # assignment so expression is only evaluated once; then prefix copies.
        # Actually just return assignments for each target sharing the same expr.
        # The rightmost target gets the real expr; earlier ones get the name ref.
        last = targets[-1]
        stmts: list = [
            A.Assign(target=last.value, value=value, pos=last.pos)
        ]
        for t in reversed(targets[:-1]):
            stmts.append(
                A.Assign(
                    target=t.value,
                    value=A.Name(name=last.value, pos=t.pos),
                    pos=t.pos,
                )
            )
        if len(stmts) == 1:
            return stmts[0]
        # Wrap in a block via MultiAssign (use Pass as sentinel with a body list).
        # Use the first target's position.
        return A.MultiAssign(targets=[t.value for t in targets], value=value, pos=name_tok.pos)  # type: ignore

    def _desugar_chained_assign(self, targets: list, value, pos) -> list:
        """`targets[0] = targets[1] = ... = value`, where at least one
        target is an Attr/Subscript (a pure-NAME chain never reaches this --
        see `_parse_assign`'s own version of this trick). Returns a list of
        statements: the LAST target gets the real value; every earlier
        target (in source order) is assigned a fresh READ of the last
        target's own location. Mirrors `_parse_assign`'s identical
        last-target-then-read-back approach exactly, generalized to
        Name/Attr/Subscript targets via `_is_assign_target`'s same shape
        rules instead of bare NAME tokens only.
        """
        last = targets[-1]
        read_last = self._read_expr_for_target(last)
        stmts: list = [self._build_assign_for_target(last, value, pos)]
        for tgt in targets[:-1]:
            stmts.append(self._build_assign_for_target(tgt, read_last, tgt.pos))
        return stmts

    def _build_assign_for_target(self, target, value, pos):
        """Build the right assignment-statement AST node for `target`'s
        shape (Name -> Assign, Attr -> AttrAssign, Subscript -> IndexAssign)
        with `value` as its RHS -- the construction half of
        `_desugar_chained_assign`'s per-target loop."""
        if isinstance(target, A.Name):
            return A.Assign(target=target.name, value=value, pos=pos)
        if isinstance(target, A.Attr):
            return A.AttrAssign(obj=target.obj, name=target.name, value=value, pos=pos)
        if isinstance(target, A.Subscript):
            return A.IndexAssign(target=target, value=value, pos=pos)
        raise ParseError("cannot assign to this expression", pos, ErrorCode.P_INVALID_ASSIGN_TARGET)

    def _read_expr_for_target(self, target):
        """The expression that READS `target`'s own storage location right
        after it was just assigned to -- e.g. for `self.a = self.b = v`,
        this builds the `self.b` read-back expression assigned to `self.a`.
        A fresh node (never the same object `target` itself), since AST
        nodes elsewhere in this compiler are assumed not to be shared
        across multiple positions in the tree."""
        if isinstance(target, A.Name):
            return A.Name(name=target.name, pos=target.pos)
        if isinstance(target, A.Attr):
            return A.Attr(obj=target.obj, name=target.name, pos=target.pos)
        if isinstance(target, A.Subscript):
            return A.Subscript(obj=target.obj, index=target.index, pos=target.pos)
        raise ParseError("cannot assign to this expression", target.pos, ErrorCode.P_INVALID_ASSIGN_TARGET)

    def _parse_annotated_assign(self):
        """`name: type [= value]` at statement position.

        The annotation is captured onto the Assign so sema can type the target
        from the declaration (e.g. `xs: list[str] = []` pins the element kind
        even when the initializer is an empty/opaque list). If a value follows,
        returns an Assign; a bare `x: T` lowers to `x = 0` carrying the
        annotation so the variable at least exists and is typed.
        """
        name_tok = self._expect("NAME")
        self._expect("OP", ":")
        annot = self._parse_type_annotation()
        if self._check("OP", "="):
            self._eat()
            value = self._parse_tuple_rhs()
            self._expect("NEWLINE")
            return A.Assign(
                target=name_tok.value,  # type: ignore
                value=value,
                pos=name_tok.pos,
                annot=annot,
            )  # type: ignore[arg-type]
        # Bare `x: int` (no initializer). Lower to `x = 0` so the variable
        # at least exists; if the source never assigns, the body still
        # reads zero, which matches CPython's behaviour for un-annotated
        # uninitialised globals (NameError) only loosely — but it's a safer
        # default than refusing to compile.
        self._expect("NEWLINE")
        return A.Assign(
            target=name_tok.value,  # type: ignore[arg-type]
            value=A.IntLit(value=0, pos=name_tok.pos),
            pos=name_tok.pos,
            annot=annot,
        )

    def _looks_like_tuple_assign(self) -> bool:
        """Peek ahead to see if we're at `(NAME|*NAME) ( , (NAME|*NAME) )* =`.
        Doesn't consume."""

        def _item(k: int) -> int | None:
            if self.toks[k].kind == "OP" and self.toks[k].value == "*":
                k += 1
                if k >= len(self.toks) or self.toks[k].kind != "NAME":
                    return None
                return k + 1
            if self.toks[k].kind == "NAME":
                return k + 1
            return None

        k = _item(self.i)
        if k is None:
            return False
        while k < len(self.toks):
            t = self.toks[k]
            if t.kind != "OP" or t.value != ",":
                break
            k2 = _item(k + 1)
            if k2 is None:
                return False
            k = k2
        return (
            k < len(self.toks)
            and self.toks[k].kind == "OP"
            and self.toks[k].value == "="
        )

    def _parse_tuple_assign(self) -> A.TupleAssign:
        def _parse_target():
            if self._check("OP", "*"):
                star_tok = self._eat()
                name_tok = self._expect("NAME")
                return A.StarTarget(name=name_tok.value, pos=star_tok.pos)
            tok = self._expect("NAME")
            return A.Name(name=tok.value, pos=tok.pos)

        first = _parse_target()
        targets: list = [first]
        while self._check("OP", ","):
            self._eat()
            targets.append(_parse_target())
        if len(targets) == 1 and isinstance(targets[0], A.StarTarget):
            raise ParseError(
                "starred assignment target must be in a list or tuple",
                targets[0].pos,
            )
        n_star = sum(1 for t in targets if isinstance(t, A.StarTarget))
        if n_star > 1:
            raise ParseError(
                "multiple starred expressions in assignment", first.pos
            )
        self._expect("OP", "=")
        values = [self._parse_expr()]
        while self._check("OP", ","):
            self._eat()
            values.append(self._parse_expr())
        self._expect("NEWLINE")
        # See the identical rewrite in _parse_stmt: a starred target with a
        # bare tuple RHS (`a, *rest = 1, 2, 3, 4`) means the same as
        # `a, *rest = [1, 2, 3, 4]`, because CPython binds the star to a list
        # either way. Rewriting here reuses the single-list unpack path rather
        # than adding a second lowering. NAME-only sequences reach this site,
        # which is where `a, *rest` actually lands.
        if len(values) > 1 and any(isinstance(t, A.StarTarget) for t in targets):
            values = [A.ListLit(elems=values, pos=first.pos)]
        return A.TupleAssign(targets=targets, values=values, pos=first.pos)  # type: ignore

    def _parse_aug_assign(self) -> A.AugAssign:
        name_tok = self._expect("NAME")
        op_tok = self._eat()
        op = AUG_OPS[op_tok.value]  # type: ignore
        value = self._parse_expr()
        self._expect("NEWLINE")
        return A.AugAssign(target=name_tok.value, op=op, value=value, pos=name_tok.pos)  # type: ignore

    def _parse_return(self) -> A.Return:
        kw = self._expect("KEYWORD", "return")
        value = None
        if not self._check("NEWLINE"):
            value = self._parse_tuple_rhs()
        self._expect("NEWLINE")
        return A.Return(value=value, pos=kw.pos)

    def _parse_if(self) -> A.If:
        kw = self._expect("KEYWORD", "if")
        test = self._parse_expr()
        self._expect("OP", ":")
        then = self._parse_block()
        orelse: list = []
        self._skip_newlines()
        if self._check("KEYWORD", "elif"):
            orelse = [self._parse_elif()]
        elif self._check("KEYWORD", "else"):
            self._eat()
            self._expect("OP", ":")
            orelse = self._parse_block()
        return A.If(test=test, then=then, orelse=orelse, pos=kw.pos)

    def _parse_elif(self) -> A.If:
        kw = self._expect("KEYWORD", "elif")
        test = self._parse_expr()
        self._expect("OP", ":")
        then = self._parse_block()
        orelse: list = []
        self._skip_newlines()
        if self._check("KEYWORD", "elif"):
            orelse = [self._parse_elif()]
        elif self._check("KEYWORD", "else"):
            self._eat()
            self._expect("OP", ":")
            orelse = self._parse_block()
        return A.If(test=test, then=then, orelse=orelse, pos=kw.pos)

    def _parse_while(self) -> A.While:
        kw = self._expect("KEYWORD", "while")
        test = self._parse_expr()
        self._expect("OP", ":")
        body = self._parse_block()
        orelse: list = []
        self._skip_newlines()
        if self._check("KEYWORD", "else"):
            self._eat()
            self._expect("OP", ":")
            orelse = self._parse_block()
        return A.While(test=test, body=body, pos=kw.pos, orelse=orelse)

    def _parse_for_target_group(self) -> tuple[list, bool]:
        """One for-loop target, as a flat list of names, plus whether it was
        PARENTHESIZED.

        A plain name gives a one-element list; a parenthesized group gives all
        its names flat, so callers can use len(targets)==1 to detect the
        single-variable case without isinstance checks (which compile to a
        static False in gen1 when the list element has inferred type 'int').

        The caller needs that second answer to tell `for (k, v) in ...` from
        `for a, (b, c) in ...`; the flat name list alone cannot -- see
        `_parse_for_target_list`.
        """
        if self._check("OP", "("):
            self._eat()
            names: list = [self._expect("NAME").value]
            while self._check("OP", ","):
                self._eat()
                if self._check("OP", ")"):
                    break  # trailing comma
                names.append(self._expect("NAME").value)
            self._expect("OP", ")")
            return names, True
        result: list = []
        result.append(self._expect("NAME").value)
        return result, False

    def _parse_for_target_list(self) -> tuple[list, bool]:
        """Every target of one `for` clause, flat, plus whether any group was
        NESTED -- i.e. parenthesized while not spanning the whole list.

        Flattening a parenthesized group is free when that group IS the whole
        target list: `for (k, v) in items` means exactly `for k, v in items`,
        so the parens carry no information.

        A group that is one element among several is different in kind:
        `for a, (b, c) in ...` flattens to `['a', 'b', 'c']`, an AST
        byte-identical to `for a, b, c in ...`. Whether that is CORRECT then
        depends entirely on the ITERABLE, which is parsed after the targets --
        so this only reports the shape, and `_reject_nested_for_target` decides
        once the iterable is known.
        """
        first = self._peek()
        targets, grouped = self._parse_for_target_group()
        groups = 1
        while self._check("OP", ","):
            self._eat()
            if self._check("KEYWORD", "in"):
                break  # trailing comma before `in`
            names, was_grouped = self._parse_for_target_group()
            grouped = grouped or was_grouped
            groups += 1
            targets.extend(names)
        self._for_target_pos = first.pos
        return targets, (grouped and groups > 1)

    # Iterables whose lowering already yields a FLAT tuple, making a flattened
    # nested target the correct reading rather than a wrong one.
    #
    # `enumerate(x)` yields (index, item), and the lowering spreads `item`, so
    # `for i, (a, b) in enumerate(pairs)` genuinely wants ['i', 'a', 'b'].
    # Three corpus cases depend on this and pass because of it:
    # 74_zip.py and 388_zip_three.py (`enumerate(zip(...))`) and
    # sim_leaderboard.py (`enumerate(ranked)`).
    _FLATTENING_ITERABLES = ("enumerate",)

    def _reject_nested_for_target(self, nested: bool, iter_expr) -> None:
        """Refuse a nested loop target the lowering cannot honour.

        The flattening above is not merely a bug to be removed -- it is
        LOAD-BEARING for `enumerate(...)`, where the flat reading is the right
        one. Refusing every nested target broke those three passing cases to
        convert one already-documented failure, a net regression caught by
        screening the loop/comprehension cases before committing.

        For any other iterable the nesting is real and unimplemented, and
        flattening it silently binds the inner tuple's ADDRESS: for
        `for a, (b, c) in [(1, (2, 3))]` the corpus recorded
        `1 8885232 0` where CPython prints `1 2 3`
        (tests/cases/nested_unpacking_for.py). Refusing raises ParseError,
        which is a CompileError, so the driver hands the program to the
        interpreter fallback -- the same route the equivalent ASSIGNMENT
        `a, (b, c) = ...` already takes -- and it runs correctly.
        """
        if not nested:
            return
        if isinstance(iter_expr, A.Call) and iter_expr.func in self._FLATTENING_ITERABLES:
            return
        raise ParseError(
            "nested unpacking in a loop target is not supported natively "
            "-- bind the item and unpack it inside the loop body",
            self._for_target_pos,
            ErrorCode.P_FOR_TARGET_NESTED,
        )

    def _parse_for(self) -> A.For:
        kw = self._expect("KEYWORD", "for")
        # One or more loop targets: `for x in ...` or `for k, v in ...`.
        targets, _nested = self._parse_for_target_list()
        # A single bare name keeps the simple `var` path; any unpacking
        # (multiple targets, or a parenthesized group) goes through `targets`.
        single = len(targets) == 1
        var = targets[0] if single else ""
        multi = [] if single else targets
        self._expect("KEYWORD", "in")
        # Two iterable shapes:
        #   for x in range(...):  -> .range_args is set, .iter is None
        #   for x in <expr>:      -> .iter is set, .range_args is empty
        if (
            self._check("NAME")
            and self._peek().value == "range"
            and self._peek(1).kind == "OP"
            and self._peek(1).value == "("
        ):
            self._eat()  # 'range'
            self._expect("OP", "(")
            args: list = []
            if not self._check("OP", ")"):
                args.append(self._parse_expr())
                while self._check("OP", ","):
                    self._eat()
                    if self._check("OP", ")"):
                        break  # trailing comma
                    args.append(self._parse_expr())
            self._expect("OP", ")")
            if not (1 <= len(args) <= 3):
                raise ParseError(
                    f"range() takes 1-3 arguments, got {len(args)}",
                    kw.pos,
                )
            # `range()` yields plain ints, so a nested target can never be the
            # flat reading -- refuse it whatever the arguments are.
            self._reject_nested_for_target(_nested, None)
            self._expect("OP", ":")
            body = self._parse_block()
            orelse_f: list = []
            self._skip_newlines()
            if self._check("KEYWORD", "else"):
                self._eat()
                self._expect("OP", ":")
                orelse_f = self._parse_block()
            return A.For(var=var, range_args=args, body=body, pos=kw.pos, targets=multi, orelse=orelse_f)  # type: ignore
        # Any other expression: treat as iterable.
        iter_expr = self._parse_expr()
        self._reject_nested_for_target(_nested, iter_expr)
        self._expect("OP", ":")
        body = self._parse_block()
        orelse_f2: list = []
        self._skip_newlines()
        if self._check("KEYWORD", "else"):
            self._eat()
            self._expect("OP", ":")
            orelse_f2 = self._parse_block()
        return A.For(
            var=var,  # type: ignore
            range_args=[],
            body=body,
            pos=kw.pos,
            iter=iter_expr,
            targets=multi,
            orelse=orelse_f2,
        )

    # ---- expressions -------------------------------------------------------
    # Precedence (low -> high):
    #   lambda, ternary, or, and, not, comparisons, |, ^, &, << >>, + -, * / // %, unary, primary
    def _parse_expr(self) -> "A.Expr":
        if self._check("KEYWORD", "lambda"):
            return self._parse_lambda()
        if (
            self._check("NAME")
            and self._peek(1).kind == "OP"
            and self._peek(1).value == ":="
        ):
            name_tok = self._eat()
            pos = self._eat().pos  # ':='
            value = self._parse_expr()
            return A.NamedExpr(target=name_tok.value, value=value, pos=pos)
        return self._parse_ternary()

    def _parse_lambda(self) -> "A.Lambda":
        pos = self._expect("KEYWORD", "lambda").pos
        params: list[str] = []
        vararg: "str | None" = None

        def _param_item() -> bool:
            """One lambda parameter item. Returns False for a bare `*name`
            (vararg -- captured into the enclosing `vararg`, not `params`;
            real Python allows at most one, always last), True otherwise
            (an ordinary name, appended to `params`, so the caller's own
            comma-loop keeps going as before)."""
            nonlocal vararg
            if self._check("OP", "*"):
                # `lambda *_: expr` / `lambda *args: expr` -- a catch-all
                # for surplus positional args, same shape as `_parse_funcdef`'s
                # own `*name` vararg param (see its comment). Unlike a def's
                # vararg, this has no type annotation position -- a lambda
                # parameter is never annotated at all, matching real Python's
                # own lambda grammar (annotations are illegal in a lambda's
                # parameter list, def-only).
                self._eat()
                vararg = self._expect("NAME").value
                return False
            params.append(self._expect("NAME").value)
            if self._check("OP", "="):
                self._eat()
                self._parse_expr()
            return True

        if not self._check("OP", ":"):
            more = _param_item()
            while more and self._check("OP", ","):
                self._eat()
                if self._check("OP", ":"):
                    break
                more = _param_item()
        self._expect("OP", ":")
        # The body is a full expression, not just a ternary: CPython's grammar
        # is `lambdef: 'lambda' [varargslist] ':' test` and `test` includes
        # `lambdef` itself. Parsing at the ternary level -- one rung BELOW
        # lambda in the precedence chain -- made a curried
        # `lambda x: lambda y: x + y` unparseable, since the inner lambda sat
        # at a level the body could never reach.
        body = self._parse_expr()
        return A.Lambda(params=params, vararg=vararg, body=body, pos=pos)

    def _parse_ternary(self) -> "A.Expr":
        """Conditional expression: `body if test else orelse`.

        Sits at the lowest precedence (above assignment, which isn't an
        expression here). The `if`/`else` keywords can only appear at this
        position as a ternary — statement-level `if` is dispatched earlier in
        `_parse_stmt`, so there's no ambiguity. Right-associative: the else
        arm recurses so `a if p else b if q else c` nests as expected.
        """
        body = self._parse_or()
        if self._check("KEYWORD", "if"):
            pos = self._eat().pos
            test = self._parse_or()
            self._expect("KEYWORD", "else")
            orelse = self._parse_ternary()
            return A.IfExp(test=test, body=body, orelse=orelse, pos=pos)
        return body

    def _parse_or(self) -> "A.Expr":
        left = self._parse_and()
        while self._check("KEYWORD", "or"):
            pos = self._eat().pos
            right = self._parse_and()
            left = A.BoolOp(op="or", left=left, right=right, pos=pos)
        return left

    def _parse_and(self) -> "A.Expr":
        left = self._parse_not()
        while self._check("KEYWORD", "and"):
            pos = self._eat().pos
            right = self._parse_not()
            left = A.BoolOp(op="and", left=left, right=right, pos=pos)
        return left

    def _parse_not(self) -> "A.Expr":
        if self._check("KEYWORD", "not"):
            pos = self._eat().pos
            return A.UnaryOp(op="not", operand=self._parse_not(), pos=pos)
        return self._parse_cmp()

    def _at_membership(self) -> "str | None":
        """Detect `in`, `not in`, `is`, `is not` at the current position.
        Returns the normalised op string (or None). Does NOT consume tokens.

        Hoisted from a nested helper in `_parse_cmp` (it only ever needed
        `self`) so the parser stays within asmpython's own compilable subset —
        no closures."""
        if self._check("KEYWORD", "in"):
            return "in"
        if self._check("KEYWORD", "is"):
            if self.i + 1 < len(self.toks):
                nxt = self.toks[self.i + 1]
                if nxt.kind == "KEYWORD" and nxt.value == "not":
                    return "is not"
            return "is"
        if self._check("KEYWORD", "not"):
            # peek-ahead: only `not in` here, not unary `not` (which is
            # parsed lower down).
            if self.i + 1 < len(self.toks):
                nxt = self.toks[self.i + 1]
                if nxt.kind == "KEYWORD" and nxt.value == "in":
                    return "not in"
        return None

    def _at_cmp_op(self) -> bool:
        """True when positioned on a relational comparison operator. Spelled out
        (rather than splatting a tuple into `_check_any_op`) because asmpython
        doesn't support call-site argument unpacking — keeps the parser self-
        compilable."""
        return self._check_any_op("==", "!=", "<", "<=", ">", ">=")

    def _parse_cmp(self) -> "A.Expr":
        """Chained comparisons: a < b < c becomes Compare([<, <], [a, b, c]).

        Also folds `in` / `not in` into the Compare chain so codegen sees a
        uniform shape.
        """
        left = self._parse_bit_or()

        if not self._at_cmp_op() and self._at_membership() is None:
            return left
        operands = [left]
        ops: list[str] = []
        first_pos = self._peek().pos
        while True:
            if self._at_cmp_op():
                ops.append(self._eat().value)  # type: ignore
            else:
                m = self._at_membership()
                if m is None:
                    break
                if m == "in":
                    self._eat()
                elif m == "not in":
                    self._eat()
                    self._eat()
                elif m == "is":
                    self._eat()
                elif m == "is not":
                    self._eat()
                    self._eat()
                ops.append(m)
            operands.append(self._parse_bit_or())
        return A.Compare(ops=ops, operands=operands, pos=first_pos)

    def _parse_bit_or(self) -> "A.Expr":
        left = self._parse_bit_xor()
        while self._check_any_op("|"):
            pos = self._eat().pos
            right = self._parse_bit_xor()
            left = A.BinOp(op="|", left=left, right=right, pos=pos)
        return left

    def _parse_bit_xor(self) -> "A.Expr":
        left = self._parse_bit_and()
        while self._check_any_op("^"):
            pos = self._eat().pos
            right = self._parse_bit_and()
            left = A.BinOp(op="^", left=left, right=right, pos=pos)
        return left

    def _parse_bit_and(self) -> "A.Expr":
        left = self._parse_shift()
        while self._check_any_op("&"):
            pos = self._eat().pos
            right = self._parse_shift()
            left = A.BinOp(op="&", left=left, right=right, pos=pos)
        return left

    def _parse_shift(self) -> "A.Expr":
        left = self._parse_add()
        while self._check_any_op("<<", ">>"):
            tok = self._eat()
            right = self._parse_add()
            left = A.BinOp(op=tok.value, left=left, right=right, pos=tok.pos)  # type: ignore
        return left

    def _parse_add(self) -> "A.Expr":
        left = self._parse_mul()
        while self._check_any_op("+", "-"):
            tok = self._eat()
            right = self._parse_mul()
            left = A.BinOp(op=tok.value, left=left, right=right, pos=tok.pos)  # type: ignore
        return left

    def _parse_mul(self):
        left = self._parse_unary()
        while self._check_any_op("*", "/", "//", "%"):
            tok = self._eat()
            right = self._parse_unary()
            # Keep '/' distinct from '//'. expr_type / codegen decide whether
            # this is int-int (where '/' acts like '//' since we have no
            # implicit float promotion for int/int) or float (true division).
            left = A.BinOp(op=tok.value, left=left, right=right, pos=tok.pos)  # type: ignore
        return left

    def _parse_unary(self) -> "A.Expr":
        if self._check("KEYWORD", "await"):
            # CPython's grammar puts `await` in the power tier
            # (`await_primary: AWAIT primary`), so it binds TIGHTER than every
            # arithmetic operator: `await a() + await b()` is
            # `(await a()) + (await b())`, a BinOp of two awaits. Verified
            # against CPython's own ast. Parsing it at the `not` tier instead
            # would have made it swallow the whole `a() + await b()`.
            pos = self._eat().pos
            return A.Await(value=self._parse_unary(), pos=pos)
        if self._check_any_op("-"):
            pos = self._eat().pos
            return A.UnaryOp(op="-", operand=self._parse_unary(), pos=pos)
        if self._check_any_op("+"):
            self._eat()
            return self._parse_unary()
        if self._check_any_op("~"):
            pos = self._eat().pos
            return A.UnaryOp(op="~", operand=self._parse_unary(), pos=pos)
        return self._parse_power()

    def _parse_power(self) -> "A.Expr":
        base = self._parse_primary()
        if self._check("OP", "**"):
            tok = self._eat()
            exp = self._parse_unary()  # right-associative
            return A.BinOp(op="**", left=base, right=exp, pos=tok.pos)
        return base

    def _parse_primary(self) -> "A.Expr":
        t = self._peek()
        if t.kind == "INT":
            self._eat()
            atom = A.IntLit(value=t.value, pos=t.pos)  # type: ignore
        elif t.kind == "FLOAT":
            self._eat()
            atom = A.FloatLit(value=t.value, pos=t.pos)  # type: ignore
        elif t.kind == "BYTES":
            # b"..." literal: expand to list[int] of character codes.
            self._eat()
            # Explicit `: str` annotation: t.value reads as opaque "object"
            # (Token.value's declared dataclass type), so an unannotated
            # `for c in t.value` iterates via the wrong (opaque) protocol.
            bval: str = t.value  # type: ignore
            byte_elems: list = [
                A.IntLit(value=ord(c), pos=t.pos)
                for c in bval
            ]
            atom = A.ListLit(elems=byte_elems, pos=t.pos, el_type="int")
        elif t.kind == "STRING":
            self._eat()
            atom = self._absorb_string_concat(A.StrLit(value=t.value, pos=t.pos))  # type: ignore
        elif t.kind == "FSTRING":
            atom = self._absorb_string_concat(self._parse_fstring())
        elif t.kind == "KEYWORD" and t.value in ("True", "False"):
            self._eat()
            atom = A.IntLit(value=1 if t.value == "True" else 0, pos=t.pos, is_bool=True)
        elif t.kind == "KEYWORD" and t.value == "None":
            self._eat()
            atom = A.IntLit(value=0, pos=t.pos, is_none=True)
        elif t.kind == "OP" and t.value == "...":
            self._eat()
            atom = A.IntLit(value=0, pos=t.pos, is_ellipsis=True)
        elif t.kind == "OP" and t.value == "(":
            atom = self._parse_paren_or_tuple()
        elif t.kind == "OP" and t.value == "[":
            atom = self._parse_list_lit()
        elif t.kind == "OP" and t.value == "{":
            atom = self._parse_brace()
        elif t.kind == "NAME":
            self._eat()
            if self._check("OP", "("):
                self._eat()
                args, kwargs = self._parse_call_args()
                self._expect("OP", ")")
                atom = A.Call(func=t.value, args=args, kwargs=kwargs, pos=t.pos)  # type: ignore
            else:
                atom = A.Name(name=t.value, pos=t.pos)  # type: ignore
        else:
            raise ParseError(f"unexpected token {t.kind} {t.value!r}", t.pos, ErrorCode.P_UNEXPECTED_TOKEN)

        # Trailing subscripts and method calls chain off any primary.
        return self._parse_trailers(atom)

    def _parse_trailers(self, atom):
        while True:
            if self._check("OP", "["):
                lbr = self._eat()
                # Slice form: [start:stop[:step]]; any part may be omitted.
                start = None
                if not self._check("OP", ":"):
                    start = self._parse_expr()
                if self._check("OP", ":"):
                    self._eat()
                    stop = None
                    step = None
                    if not self._check("OP", ":") and not self._check("OP", "]"):
                        stop = self._parse_expr()
                    if self._check("OP", ":"):
                        self._eat()
                        if not self._check("OP", "]"):
                            step = self._parse_expr()
                    self._expect("OP", "]")
                    idx = A.Slice(start=start, stop=stop, step=step, pos=lbr.pos)
                elif self._check("OP", ","):
                    # Comma-separated subscript (`x[a, b]`) desugars to a
                    # single tuple index (`x[(a, b)]`), same as real Python's
                    # own grammar -- most common in generic-type expressions
                    # used as real runtime values (`tuple[int, ...]`,
                    # `Callable[[int], str]` passed to `typing.cast(...)`,
                    # etc.), which this parser otherwise has no annotation
                    # context to special-case. A trailing comma before `]`
                    # (`x[a,]`) is valid Python too and still yields a
                    # 1-tuple index, matching `(a,)`'s own trailing-comma rule.
                    elems = [start]
                    while self._check("OP", ","):
                        self._eat()
                        if self._check("OP", "]"):
                            break
                        elems.append(self._parse_expr())
                    self._expect("OP", "]")
                    idx = A.TupleLit(elems=elems, pos=lbr.pos)  # type: ignore
                else:
                    self._expect("OP", "]")
                    idx = start
                atom = A.Subscript(obj=atom, index=idx, pos=lbr.pos)  # type: ignore
            elif self._check("OP", "."):
                dot = self._eat()
                name = self._expect("NAME").value
                if self._check("OP", "("):
                    # obj.name(...) — method call
                    self._eat()
                    args, kwargs = self._parse_call_args()  # type: ignore
                    self._expect("OP", ")")
                    atom = A.MethodCall(
                        obj=atom,
                        method=name,  # type: ignore
                        args=args,
                        kwargs=kwargs,
                        pos=dot.pos,  # type: ignore
                    )
                else:
                    # obj.name — attribute access (e.g. math.pi)
                    atom = A.Attr(obj=atom, name=name, pos=dot.pos)  # type: ignore
            elif self._check("OP", "("):
                # A CALL on an arbitrary callee expression. A plain `name(...)`
                # never reaches here (the atom parser consumes its own arg
                # list), so this only fires where the callee is a real
                # expression: `handlers['add'](3, 4)`, `(lambda x: x+1)(5)`,
                # `compose(f, g)(5)`, `registry[k].fn(v)`. Python's grammar
                # allows a call trailer after any primary, and a newline
                # token separates statements, so no valid non-call `(` can
                # follow an atom on the same logical line.
                lp = self._eat()
                args, kwargs = self._parse_call_args()  # type: ignore
                self._expect("OP", ")")
                atom = A.Call(
                    func=A.CALLABLE_EXPR_SENTINEL,
                    args=args,
                    kwargs=kwargs,
                    func_expr=atom,  # type: ignore
                    pos=lp.pos,
                )
            else:
                return atom

    def _parse_paren_or_tuple(self):
        """After a '(': parse either a parenthesised expression or a tuple.

        A comma is what makes it a tuple:
            ()        -> empty tuple
            (a)       -> just `a` (grouping, not a tuple)
            (a,)      -> 1-tuple
            (a, b, c) -> 3-tuple   (trailing comma allowed)
        """
        lpar = self._expect("OP", "(")
        if self._check("OP", ")"):
            self._eat()
            return A.TupleLit(elems=[], pos=lpar.pos)
        tuple_has_star = False
        if self._check("OP", "*"):
            star_pos = self._eat().pos
            first = A.Starred(value=self._parse_expr(), pos=star_pos)
            tuple_has_star = True
        else:
            first = self._parse_expr()
        if self._check("KEYWORD", "for"):
            if isinstance(first, A.Starred):
                raise ParseError(
                    "generator expression element cannot be starred",
                    first.pos,
                )
            comp = self._parse_comprehension_tail(first, lpar.pos)
            self._expect("OP", ")")
            return comp
        if not self._check("OP", ","):
            self._expect("OP", ")")
            return first
        elems = [first]
        while self._check("OP", ","):
            self._eat()
            if self._check("OP", ")"):
                break  # trailing comma
            if self._check("OP", "*"):
                star_pos2 = self._eat().pos
                elems.append(A.Starred(value=self._parse_expr(), pos=star_pos2))
                tuple_has_star = True
            else:
                elems.append(self._parse_expr())
        self._expect("OP", ")")
        if tuple_has_star:
            return A.ListLit(elems=elems, pos=lpar.pos)
        return A.TupleLit(elems=elems, pos=lpar.pos)

    def _parse_call_args(self):
        """Parse a call's argument list after '(' is consumed, up to ')'.

        Returns (args, kwargs); kwargs is a list of (name, expr). A trailing
        comma is allowed. Keyword arguments (`name=expr`) must follow all
        positional arguments, matching Python.
        """
        args: list = []
        kwargs: list = []
        if self._check("OP", ")"):
            return args, kwargs
        while True:
            if (
                self._check("NAME")
                and self._peek(1).kind == "OP"
                and self._peek(1).value == "="
            ):
                name = self._eat().value
                self._eat()  # '='
                kwargs.append((name, self._parse_expr()))
            elif self._check("OP", "**"):
                # `**expr` may legally follow keyword arguments (it isn't a
                # positional argument) — Python allows `f(a=1, **kw)`.
                star_pos = self._eat().pos
                args.append(A.DoubleStarred(value=self._parse_expr(), pos=star_pos))
            else:
                if kwargs:
                    raise ParseError(
                        "positional argument follows keyword argument",
                        self._peek().pos,
                    )
                if self._check("OP", "*"):
                    star_pos = self._eat().pos
                    args.append(A.Starred(value=self._parse_expr(), pos=star_pos))
                else:
                    arg = self._parse_expr()
                    # A bare generator expression as the sole argument:
                    # `sum(x for x in xs)`.
                    if self._check("KEYWORD", "for"):
                        arg = self._parse_comprehension_tail(arg, arg.pos)
                    args.append(arg)
            if not self._check("OP", ","):
                break
            self._eat()
            if self._check("OP", ")"):
                break  # trailing comma
        return args, kwargs

    def _parse_tuple_rhs(self):
        """Parse a right-hand-side that may be a bare (unparenthesised) tuple.

        Used where Python allows `x = 1, 2` and `return a, b`: parse one
        expression, then — if a comma follows — collect the rest into a
        TupleLit. A trailing comma before the end of the line is allowed,
        so `x = 1,` is a 1-tuple.
        """
        first = self._parse_expr()
        if not self._check("OP", ","):
            return first
        pos = getattr(first, "pos", self._peek().pos)
        elems = [first]
        while self._check("OP", ","):
            self._eat()
            if self._check("NEWLINE") or self._check("EOF"):
                break  # trailing comma
            elems.append(self._parse_expr())
        return A.TupleLit(elems=elems, pos=pos)

    def _absorb_string_concat(self, atom: A.Expr) -> A.Expr:
        """Implicit concatenation of adjacent string/f-string literals:
        `"a" "b"`, `f"a" "b"`, `"a" f"b"` all merge into one literal. Inside
        parens, newlines are suppressed, so this also joins literals split
        across lines (common in multi-line error messages)."""
        if not (self._check("STRING") or self._check("FSTRING")):
            return atom
        segments: list = list(atom.segments) if isinstance(atom, A.FString) else [atom]
        has_fstring = isinstance(atom, A.FString)
        while self._check("STRING") or self._check("FSTRING"):
            if self._check("STRING"):
                tok = self._eat()
                segments.append(A.StrLit(value=tok.value, pos=tok.pos))  # type: ignore
            else:
                fs = self._parse_fstring()
                segments.extend(fs.segments)
                has_fstring = True
        if not has_fstring:
            text = "".join([s.value for s in segments])
            return A.StrLit(value=text, pos=atom.pos)
        return A.FString(segments=segments, pos=atom.pos)

    def _parse_fstring(self) -> A.FString:
        tok = self._eat()
        segments: list = []
        for seg in tok.value:  # type: ignore
            kind = seg[0]
            text = seg[1]
            spec = seg[2] if len(seg) > 2 else ""
            conv = seg[3] if len(seg) > 3 else ""
            if kind == "str":
                segments.append(A.StrLit(value=text, pos=tok.pos))
            else:
                # Re-lex the expression text and parse it as an expression.
                inner_toks = Lexer(text).tokenize()
                # Explicit `: Parser` annotation: gen1's sema types the
                # constructor call return as "instance:Parser" (via the
                # f"instance:{e.func}" path), but without the annotation
                # the scope-add falls back to the default "int" because
                # the Call node's inferred_type is read as "any"-typed
                # (opaque attribute of the external A.Expr parameter),
                # leaving inner_parser typed "int" and every subsequent
                # ._check()/_eat() call producing "int has no method" errors.
                inner_parser: Parser = Parser(inner_toks)
                expr = inner_parser._parse_expr()
                # Trailing tokens after the expression are an error.
                while inner_parser._check("NEWLINE"):
                    inner_parser._eat()
                if not inner_parser._check("EOF"):
                    raise ParseError(
                        f"unexpected tokens in f-string expression: {text!r}",
                        tok.pos,
                    )
                # Carry a `:format-spec` (e.g. `.2f`) for codegen to honour.
                if spec:
                    expr.fmt_spec = spec  # type: ignore[attr-defined]
                # Carry a `!r`/`!s`/`!a` conversion for codegen to honour.
                if conv:
                    expr.conv_flag = conv  # type: ignore[attr-defined]
                segments.append(expr)
        return A.FString(segments=segments, pos=tok.pos)

    def _parse_brace(self):
        """Parse a `{...}` literal — either a dict or a set.

        `{}` is the empty dict (matching Python). A leading `**` (PEP 448
        dict unpacking) is unambiguously a dict literal, since sets can't
        contain `**expr`. Otherwise the first element decides: a `key: value`
        colon makes it a dict, anything else a set.
        """
        start = self._expect("OP", "{").pos
        if self._check("OP", "}"):
            self._eat()
            return A.DictLit(keys=[], values=[], pos=start)
        if self._check("OP", "**"):
            # `{**d1, "k": v, **d2, ...}`.
            keys: list = []
            values: list = []
            while True:
                if self._check("OP", "**"):
                    op = self._eat()
                    keys.append(A.Name(name="**", pos=op.pos))
                    values.append(self._parse_expr())
                else:
                    k = self._parse_expr()
                    self._expect("OP", ":")
                    keys.append(k)
                    values.append(self._parse_expr())
                if not self._check("OP", ","):
                    break
                self._eat()
                if self._check("OP", "}"):
                    break  # trailing comma
            self._expect("OP", "}")
            return A.DictLit(keys=keys, values=values, pos=start)
        first = self._parse_expr()
        if self._check("OP", ":"):
            # Dict literal or dict comprehension. Both open `key: value`.
            self._eat()
            first_value = self._parse_expr()
            if self._check("KEYWORD", "for"):
                # `{k: v for var in iter [if cond]}`.
                comp = self._parse_dict_comprehension_tail(first, first_value, start)
                self._expect("OP", "}")
                return comp
            keys: list = []
            keys.extend([first])
            values = [first_value]
            while self._check("OP", ","):
                self._eat()
                if self._check("OP", "}"):
                    break  # trailing comma
                if self._check("OP", "**"):
                    # `{"k": v, **other, ...}` — merge another dict in here.
                    op = self._eat()
                    keys.append(A.Name(name="**", pos=op.pos))
                    values.append(self._parse_expr())
                    continue
                keys.append(self._parse_expr())
                self._expect("OP", ":")
                values.append(self._parse_expr())
            self._expect("OP", "}")
            return A.DictLit(keys=keys, values=values, pos=start)
        # Set literal or set comprehension.
        if self._check("KEYWORD", "for"):
            # `{expr for var in iter [if cond]}` — set comprehension.
            # Lower to set(list_comprehension) so codegen can use the
            # existing list comprehension + set constructor machinery.
            comp = self._parse_comprehension_tail(first, start)
            self._expect("OP", "}")
            return A.Call(func="set", args=[comp], kwargs=[], pos=start)  # type: ignore
        elems: list = [first]
        while self._check("OP", ","):
            self._eat()
            if self._check("OP", "}"):
                break  # trailing comma
            elems.append(self._parse_expr())
        self._expect("OP", "}")
        return A.SetLit(elems=elems, pos=start)

    def _parse_list_lit(self):
        start = self._expect("OP", "[").pos
        elems: list = []
        if not self._check("OP", "]"):
            if self._check("OP", "*"):
                star_pos = self._eat().pos
                first = A.Starred(value=self._parse_expr(), pos=star_pos)
            else:
                first = self._parse_expr()
            if self._check("KEYWORD", "for"):
                if isinstance(first, A.Starred):
                    raise ParseError(
                        "list comprehension element cannot be starred",
                        first.pos,
                    )
                comp = self._parse_comprehension_tail(first, start)
                self._expect("OP", "]")
                return comp
            elems.append(first)
            while self._check("OP", ","):
                self._eat()
                if self._check("OP", "]"):
                    break  # trailing comma
                if self._check("OP", "*"):
                    star_pos2 = self._eat().pos
                    elems.append(A.Starred(value=self._parse_expr(), pos=star_pos2))
                else:
                    elems.append(self._parse_expr())
        self._expect("OP", "]")
        return A.ListLit(elems=elems, pos=start)

    def _parse_comprehension_tail(self, elt, pos):
        """Parse `for <var> in <iter> [if <cond>]` after the element expression,
        returning a Comprehension. Used by list comprehensions and generator
        expressions. The iterable and filter are parsed below ternary
        precedence so a trailing `if` reads as the comprehension filter rather
        than a conditional expression."""
        self._expect("KEYWORD", "for")
        # One or more loop targets: `for x in ...` or `for k, v in ...`
        # (mirrors `_parse_for`).
        targets, _nested = self._parse_for_target_list()
        single = len(targets) == 1
        var = targets[0] if single else ""
        multi = [] if single else targets
        self._expect("KEYWORD", "in")
        iter_expr = self._parse_or()
        self._reject_nested_for_target(_nested, iter_expr)
        cond = None
        if self._check("KEYWORD", "if"):
            self._eat()
            cond = self._parse_or()
        ef_vars: list = []
        ef_targets: list = []
        ef_iters: list = []
        ef_conds: list = []
        while self._check("KEYWORD", "for"):
            self._eat()
            etargets, _enested = self._parse_for_target_list()
            evar2: str = etargets[0] if len(etargets) == 1 else ""
            emulti2: list = [] if len(etargets) == 1 else etargets
            self._expect("KEYWORD", "in")
            eiter2 = self._parse_or()
            self._reject_nested_for_target(_enested, eiter2)
            econd2 = None
            if self._check("KEYWORD", "if"):
                self._eat()
                econd2 = self._parse_or()
            ef_vars.append(evar2)
            ef_targets.append(emulti2)
            ef_iters.append(eiter2)
            ef_conds.append(econd2)
        return A.Comprehension(elt=elt, var=var, iter=iter_expr, cond=cond, pos=pos, targets=multi, extra_for_vars=ef_vars, extra_for_targets=ef_targets, extra_for_iters=ef_iters, extra_for_conds=ef_conds)  # type: ignore

    def _parse_dict_comprehension_tail(self, key, value, pos):
        """Parse `for <var> in <iter> [if <cond>]` after `key: value`, returning
        a DictComprehension. Same `for`/`in`/`if` grammar as the list form,
        including multi-target unpacking (`for k, v in ...`)."""
        self._expect("KEYWORD", "for")
        targets, _nested = self._parse_for_target_list()
        single = len(targets) == 1
        var = targets[0] if single else ""
        multi = [] if single else targets
        self._expect("KEYWORD", "in")
        iter_expr = self._parse_or()
        self._reject_nested_for_target(_nested, iter_expr)
        cond = None
        if self._check("KEYWORD", "if"):
            self._eat()
            cond = self._parse_or()
        return A.DictComprehension(
            key=key,
            value=value,
            var=var,  # type: ignore
            iter=iter_expr,
            cond=cond,
            pos=pos,  # type: ignore
            targets=multi,
        )
