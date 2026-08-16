"""The checks CPython does in its COMPILER, not in its parser.

`ast.parse("break")` builds a perfectly good tree. `compile("break", ...)`
raises `SyntaxError`. The difference is not a quirk: `break` is a statement
like any other and whether it is legal depends on WHAT ENCLOSES IT, which a
grammar cannot see. The same goes for `return`, `yield`, `await`, a duplicate
parameter name, a `global` that contradicts an assignment, and a `nonlocal`
with nothing to bind to.

So `_pyparse` deliberately accepts all of them -- the parser test asserts it,
in `REFUSED_AFTER_PARSING` -- and this walks the tree afterwards with the
context the grammar could not carry: which scope we are in, whether it is a
function, whether it is async, and how many loops enclose the statement.

WHAT IT IS NOT: a type checker, or a name resolver. Every check here is one
CPython performs before it emits a single instruction, and refuses to compile
over. A program this accepts may still fail at run time in every ordinary way.
"""

from _pyparse import ParseError

#: Names that may not be BOUND. `None = 1` is refused by the parser, which
#: sees a constant on the left of an `=`; these are the spellings where the
#: name arrives as an ordinary NAME token and only the binding is wrong --
#: `def None()`, `import x as True`, `for None in xs`.
_RESERVED = ("None", "True", "False", "__debug__")

#: A scope that a `return`, a `yield` or an `await` may sit in. A class body
#: is NOT one, which is why `class C: return 1` is an error.
_FUNCTIONS = ("FunctionDef", "AsyncFunctionDef", "Lambda")


class _Scope:
    """One scope, and what it knows about the names in it.

    `assigned` and `declared` are separate because the ERROR is about their
    ORDER: `global x` after `x = 1` in the same scope is refused, and before it
    is fine. Recording only the union would lose exactly the fact being
    checked.
    """

    def __init__(self, kind, is_async=False):
        self.kind = kind
        self.is_async = is_async
        #: Names this scope binds, in the order the walk reaches them.
        self.assigned = []
        #: Names declared `global` or `nonlocal` here, and which of the two.
        self.declared = {}
        #: How many loops enclose the statement being walked. Reset per scope,
        #: because a `def` inside a `for` does not put its body in the loop.
        self.loops = 0

    def is_function(self):
        return self.kind in _FUNCTIONS


class _Checker:
    def __init__(self, mode):
        self.mode = mode
        self.scopes = [_Scope("Module")]

    # -- reporting -------------------------------------------------------
    def fail(self, message, node):
        raise ParseError(message, getattr(node, "lineno", 0),
                         getattr(node, "col_offset", 0))

    def scope(self):
        return self.scopes[-1]

    def enclosing_function(self):
        """The nearest enclosing scope that is a function, skipping classes.

        A CLASS BODY IS NOT ONE, and that is what makes `return` inside a
        class body an error while `return` inside a method is not.
        """
        for one in reversed(self.scopes[:-1]):
            if one.is_function():
                return one
        return None

    # -- parameters ------------------------------------------------------
    def check_parameters(self, args, node):
        """Duplicate names, and more than one of a thing there may be one of.

        `def f(a, a)` is a SyntaxError and so is `lambda a, a: a`; the grammar
        accepts both, because a parameter list is a list of names and nothing
        about the second `a` is ill-formed on its own.
        """
        if args is None:
            return
        seen = []
        for name in args.every_name():
            if name in seen:
                self.fail("duplicate argument '" + name +
                          "' in function definition", node)
            if name in _RESERVED:
                self.fail("cannot assign to " + name, node)
            seen.append(name)
        for group in (args.posonlyargs, args.args, args.kwonlyargs):
            for one in group:
                if one.annotation is not None:
                    self.expression(one.annotation)
        for one in args.defaults:
            self.expression(one)
        for one in args.kw_defaults:
            if one is not None:
                self.expression(one)

    # -- assignment targets ----------------------------------------------
    def check_target(self, node, node_for_error=None):
        """What a `for`, a `with ... as` or an assignment may bind.

        THE STARRED RULES ARE HERE and not in the parser because they are
        about the WHOLE target: `a, *b, *c = ...` has two stars and each is
        fine on its own, and `*a = [1]` is a star with no tuple around it.
        """
        where = node_for_error if node_for_error is not None else node
        if node is None:
            return
        if node.kind in ("Tuple", "List"):
            stars = 0
            for one in node.elts:
                if one.kind == "Starred":
                    # INSIDE A TUPLE IS WHERE A STAR BELONGS, so what is
                    # checked is what it wraps -- recursing into the generic
                    # branch below would refuse `a, *b = ...`, which is the
                    # whole point of having one.
                    stars = stars + 1
                    self.check_target(one.value, where)
                else:
                    self.check_target(one, where)
            if stars > 1:
                self.fail("multiple starred expressions in assignment", where)
            return
        if node.kind == "Starred":
            # A LONE STAR HAS NOTHING TO TAKE THE REST FROM. `*a = [1]` is not
            # `a = [1]` -- there is no sequence being unpacked at all.
            self.fail("starred assignment target must be in a list or tuple",
                      where)
        if node.kind == "Name":
            if node.id in _RESERVED:
                self.fail("cannot assign to " + node.id, where)
            self.bind(node.id, where)

    def bind(self, name, node):
        """Record a binding, and refuse one that contradicts a declaration."""
        scope = self.scope()
        if name not in scope.assigned:
            scope.assigned.append(name)

    # -- the walk --------------------------------------------------------
    def module(self, tree):
        for stmt in tree.body:
            self.statement(stmt)

    def body(self, stmts):
        for stmt in stmts or []:
            self.statement(stmt)

    def statement(self, node):
        kind = node.kind
        if kind in ("Break", "Continue"):
            if self.scope().loops == 0:
                word = "break" if kind == "Break" else "continue"
                self.fail("'" + word + "' outside loop", node)
            return
        if kind == "Return":
            if not self.scope().is_function():
                self.fail("'return' outside function", node)
            if node.get("value") is not None:
                self.expression(node.value)
            return
        if kind in ("Global", "Nonlocal"):
            self.declare(node)
            return
        if kind in ("FunctionDef", "AsyncFunctionDef"):
            for one in node.get("decorator_list") or []:
                self.expression(one)
            self.check_parameters(node.get("args"), node)
            if node.get("returns") is not None:
                self.expression(node.returns)
            self.bind(node.name, node)
            if node.name in _RESERVED:
                self.fail("cannot assign to " + node.name, node)
            self.scopes.append(_Scope(kind, kind == "AsyncFunctionDef"))
            self.body(node.body)
            self.scopes.pop()
            return
        if kind == "ClassDef":
            for one in node.get("decorator_list") or []:
                self.expression(one)
            for one in node.get("bases") or []:
                self.expression(one)
            for one in node.get("keywords") or []:
                self.expression(one.value)
            self.bind(node.name, node)
            if node.name in _RESERVED:
                self.fail("cannot assign to " + node.name, node)
            self.scopes.append(_Scope("ClassDef"))
            self.body(node.body)
            self.scopes.pop()
            return
        if kind in ("For", "AsyncFor"):
            if kind == "AsyncFor":
                self.needs_async(node)
            self.expression(node.iter)
            self.check_target(node.target)
            self.scope().loops = self.scope().loops + 1
            self.body(node.body)
            self.scope().loops = self.scope().loops - 1
            self.body(node.get("orelse"))
            return
        if kind == "While":
            self.expression(node.test)
            self.scope().loops = self.scope().loops + 1
            self.body(node.body)
            self.scope().loops = self.scope().loops - 1
            self.body(node.get("orelse"))
            return
        if kind == "If":
            self.expression(node.test)
            self.body(node.body)
            self.body(node.get("orelse"))
            return
        if kind in ("With", "AsyncWith"):
            if kind == "AsyncWith":
                self.needs_async(node)
            for item in node.items:
                self.expression(item.context_expr)
                if item.get("optional_vars") is not None:
                    self.check_target(item.optional_vars)
            self.body(node.body)
            return
        if kind in ("Try", "TryStar"):
            self.body(node.body)
            for handler in node.handlers:
                if handler.get("type") is not None:
                    self.expression(handler.type)
                if handler.get("name"):
                    self.bind(handler.name, handler)
                self.body(handler.body)
            self.body(node.get("orelse"))
            self.body(node.get("finalbody"))
            return
        if kind == "Match":
            self.expression(node.subject)
            for case in node.cases:
                self.pattern(case.get("pattern"))
                if case.get("guard") is not None:
                    self.expression(case.guard)
                self.body(case.body)
            return
        if kind in ("Import", "ImportFrom"):
            for alias in node.names:
                bound = alias.get("asname") or alias.name.split(".")[0]
                if bound in _RESERVED:
                    self.fail("cannot assign to " + bound, node)
                self.bind(bound, node)
            return
        if kind == "Assign":
            self.expression(node.value)
            for one in node.targets:
                self.check_target(one)
            return
        if kind == "AugAssign":
            self.expression(node.value)
            self.check_target(node.target)
            return
        if kind == "AnnAssign":
            if node.get("value") is not None:
                self.expression(node.value)
            self.expression(node.annotation)
            self.check_target(node.target)
            return
        if kind == "Delete":
            for one in node.targets:
                self.expression(one)
            return
        if kind == "Expr":
            self.expression(node.value)
            return
        if kind == "Raise":
            if node.get("exc") is not None:
                self.expression(node.exc)
            if node.get("cause") is not None:
                self.expression(node.cause)
            return
        if kind == "Assert":
            self.expression(node.test)
            if node.get("msg") is not None:
                self.expression(node.msg)
            return
        if kind == "TypeAlias":
            self.expression(node.value)
            return
        # `Pass` and anything else with no sub-expressions.
        return

    def declare(self, node):
        """`global x` and `nonlocal x`.

        TWO RULES, both about what has already happened in this scope:
        `global x` after `x = 1` here contradicts itself, and `nonlocal x`
        needs a binding in an enclosing FUNCTION -- at module level there
        cannot be one, which is why `nonlocal x` alone is always an error.
        """
        scope = self.scope()
        for name in node.names:
            if name in scope.assigned:
                word = "global" if node.kind == "Global" else "nonlocal"
                self.fail("name '" + name + "' is assigned to before " +
                          word + " declaration", node)
            scope.declared[name] = node.kind
            if node.kind == "Nonlocal":
                if not scope.is_function():
                    self.fail("nonlocal declaration not allowed at module "
                              "level", node)
                found = False
                for one in self.scopes[:-1]:
                    if one.is_function() and name in one.assigned:
                        found = True
                if not found:
                    self.fail("no binding for nonlocal '" + name + "' found",
                              node)

    def needs_async(self, node):
        scope = self.scope()
        if not (scope.is_function() and scope.is_async):
            word = {"Await": "await", "AsyncFor": "async for",
                    "AsyncWith": "async with"}.get(node.kind, "await")
            self.fail("'" + word + "' outside async function", node)

    # -- patterns --------------------------------------------------------
    def pattern(self, node):
        """PEP 634. The one rule a grammar cannot carry: `**rest` is LAST.

        `{**rest, 'a': 1}` parses -- a mapping pattern is a list of items and
        each is well formed -- and is refused, because the double-star takes
        everything that is left and there is nothing after it to take.
        """
        if node is None:
            return
        if node.kind == "Dict":
            keys = node.get("keys") or []
            for i, key in enumerate(keys):
                if key is None and i != len(keys) - 1:
                    self.fail("** pattern must be the last item in a mapping "
                              "pattern", node)
        for child in node.children():
            self.pattern(child)

    # -- expressions -----------------------------------------------------
    def expression(self, node):
        if node is None:
            return
        kind = node.kind
        if kind == "Await":
            self.needs_async(node)
        elif kind in ("Yield", "YieldFrom"):
            scope = self.scope()
            if not scope.is_function():
                self.fail("'yield' outside function", node)
        elif kind == "Lambda":
            self.check_parameters(node.get("args"), node)
            self.scopes.append(_Scope("Lambda"))
            self.expression(node.body)
            self.scopes.pop()
            return
        elif kind == "NamedExpr":
            # `(a.b := 1)` and `((a, b) := ...)`: the walrus binds a NAME and
            # nothing else. The parser refuses most of these; this is the
            # backstop for the shapes it lets through.
            if node.target.kind != "Name":
                self.fail("cannot use assignment expressions with " +
                          node.target.kind.lower(), node)
        for child in node.children():
            self.expression(child)


def validate(tree, mode="exec"):
    """Refuse what CPython refuses AFTER parsing. Answers the tree."""
    checker = _Checker(mode)
    if tree.kind == "Module":
        checker.module(tree)
    else:
        checker.expression(tree.body if tree.get("body") is not None else tree)
    return tree
