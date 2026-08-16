"""`eval()` and `exec()` inside a produced binary.

WHAT THEY COST, and why the compiler says so at the call site: everything
reached through here is INTERPRETED. The binary around it is native code that
was compiled ahead of time; source handed to it at run time cannot be, so it
is walked instead -- and a walk is orders of magnitude slower than the code
that called it. `W0091` is that sentence, reported where it is written.

WHY A TREE WALK. The tree already exists: `_pycompile` built it to answer
whether the source was valid Python, and walking it is the shortest honest
distance from there to a value. A bytecode VM would be the other shape --
portapy is one, at 2,700 lines plus a frontend that would have to be rewritten
against this tree -- and every program calling `eval` would carry it. The
ENTRY POINTS here are what a program sees, so that engine can replace this one
without anything above noticing.

WHAT IS NOT HERE. `import` inside evaluated source, `global`/`nonlocal`, `del`,
`with`, `match`, generators, decorators and comprehension scopes beyond one
level. Each raises rather than doing something approximate: a wrong answer
from an interpreter is worse than a refusal, because nothing above it can tell.
"""

from _pycompile import code, compile as _compile

# A BUILTIN THAT HAS NO VALUE FORM gets a wrapper.
#
# Most builtins here are ordinary values -- `len` and `int` can be passed to
# `map` and so can be put in a table. Some cannot: they are lowered as a
# call shape rather than as an object, so naming one without calling it is
# refused (E0056). A one-line `def` around each is the whole fix, and it is
# also where the argument counts get written down.


def _all(xs):
    return all(xs)


def _any(xs):
    return any(xs)


def _divmod(a, b):
    return divmod(a, b)


def _enumerate(xs, start=0):
    return enumerate(xs, start)


def _format(value, spec=""):
    return format(value, spec)


def _getattr(obj, name):
    return getattr(obj, name)


def _hasattr(obj, name):
    return hasattr(obj, name)


def _isinstance(obj, kinds):
    return isinstance(obj, kinds)


def _issubclass(a, b):
    return issubclass(a, b)


def _iter(xs):
    return iter(xs)


def _map(fn, xs):
    return map(fn, xs)


def _next(it):
    return next(it)


def _max(a, b=None):
    # `max` AS A VALUE SCANS AN ITERABLE -- it is a one-argument thunk, so
    # `max(3, 9)` through it handed the scan an int. Both spellings mean the
    # largest of what was given, and this is where the two are told apart.
    if b is None:
        return max(a)
    return max(a, b)


def _min(a, b=None):
    if b is None:
        return min(a)
    return min(a, b)


def _pow(a, b):
    return a ** b


def _range(a, b=None, c=None):
    if b is None:
        return range(a)
    if c is None:
        return range(a, b)
    return range(a, b, c)


def _round(x, digits=None):
    if digits is None:
        return round(x)
    return round(x, digits)


def _type(x):
    return type(x)


def _zip(a, b):
    return zip(a, b)


#: The builtins evaluated source can reach. Written out rather than taken from
#: a namespace, because a produced binary has no `builtins` module to ask --
#: what a name means was decided when the program was compiled.
_BUILTINS = {
    "abs": abs, "all": _all, "any": _any, "ascii": ascii, "bin": bin,
    "bool": bool, "bytes": bytes, "chr": chr, "complex": complex,
    "dict": dict, "divmod": _divmod, "enumerate": _enumerate, "float": float,
    "format": _format, "frozenset": frozenset, "getattr": _getattr,
    "hasattr": _hasattr, "hash": hash, "hex": hex, "id": id, "int": int,
    "isinstance": _isinstance, "issubclass": _issubclass, "iter": _iter,
    "len": len, "list": list, "map": _map, "max": _max, "min": _min,
    "next": _next, "oct": oct, "ord": ord, "pow": _pow, "print": print,
    "range": _range, "repr": repr, "reversed": reversed, "round": _round,
    "set": set, "sorted": sorted, "str": str, "sum": sum, "tuple": tuple,
    "type": _type, "zip": _zip,
    "True": True, "False": False, "None": None,
}


class _Jump(Exception):
    """A `break` or a `continue`, carried out of the statement that wrote it."""

    def __init__(self, kind):
        super().__init__(kind)
        self.kind = kind


class _Returned(Exception):
    """A `return`, carried out of the function body being walked."""

    def __init__(self, value):
        super().__init__("return")
        self.value = value


class _Function:
    """A `def` or a `lambda` that evaluated source wrote.

    It closes over the namespaces it was defined in, which is what makes a
    function defined by `exec` able to see the names around it.
    """

    def __init__(self, node, glob, loc, walker):
        self.node = node
        self.glob = glob
        self.loc = loc
        self.walker = walker
        self.__name__ = node.get("name") or "<lambda>"

    def __call__(self, *args):
        params = self.node.args.every_name()
        inner = {}
        for key in self.loc:
            inner[key] = self.loc[key]
        i = 0
        for name in params:
            if i < len(args):
                inner[name] = args[i]
            i = i + 1
        defaults = self.node.args.defaults
        if defaults:
            first = len(params) - len(defaults)
            j = 0
            for one in defaults:
                if first + j >= len(args):
                    inner[params[first + j]] = self.walker.eval(
                        one, self.glob, self.loc)
                j = j + 1
        body = self.node.get("body")
        if not isinstance(body, list):
            return self.walker.eval(body, self.glob, inner)   # a lambda
        try:
            self.walker.run(body, self.glob, inner)
        except _Returned as got:
            return got.value
        return None


class _Walker:
    """The interpreter. One method for expressions, one for statements."""

    def unsupported(self, node):
        raise NotImplementedError(
            node.kind + " is not supported in evaluated source")

    def lookup(self, name, glob, loc):
        if name in loc:
            return loc[name]
        if name in glob:
            return glob[name]
        if name in _BUILTINS:
            return _BUILTINS[name]
        raise NameError("name '" + name + "' is not defined")

    # -- expressions -----------------------------------------------------
    def eval(self, node, glob, loc):
        kind = node.kind
        if kind == "Constant":
            return node.value
        if kind == "Name":
            return self.lookup(node.id, glob, loc)
        if kind == "BinOp":
            return self.binary(node.op, self.eval(node.left, glob, loc),
                               self.eval(node.right, glob, loc))
        if kind == "UnaryOp":
            value = self.eval(node.operand, glob, loc)
            if node.op == "-":
                return -value
            if node.op == "+":
                return +value
            if node.op == "~":
                return ~value
            return not value
        if kind == "BoolOp":
            out = None
            for one in node.values:
                out = self.eval(one, glob, loc)
                if node.op == "and" and not out:
                    return out
                if node.op == "or" and out:
                    return out
            return out
        if kind == "Compare":
            left = self.eval(node.left, glob, loc)
            i = 0
            for op in node.ops:
                right = self.eval(node.comparators[i], glob, loc)
                if not self.compare(op, left, right):
                    return False
                left = right
                i = i + 1
            return True
        if kind == "IfExp":
            if self.eval(node.test, glob, loc):
                return self.eval(node.body, glob, loc)
            return self.eval(node.orelse, glob, loc)
        if kind == "Call":
            return self.call(node, glob, loc)
        if kind == "Subscript":
            return self.eval(node.value, glob, loc)[
                self.eval(node.slice, glob, loc)]
        if kind == "Slice":
            return slice(
                self.eval(node.lower, glob, loc) if node.get("lower") else None,
                self.eval(node.upper, glob, loc) if node.get("upper") else None,
                self.eval(node.step, glob, loc) if node.get("step") else None)
        if kind == "Attribute":
            return getattr(self.eval(node.value, glob, loc), node.attr)
        if kind == "Tuple":
            return tuple(self.elements(node.elts, glob, loc))
        if kind == "List":
            return self.elements(node.elts, glob, loc)
        if kind == "Set":
            return set(self.elements(node.elts, glob, loc))
        if kind == "Dict":
            out = {}
            i = 0
            for key in node.keys:
                value = self.eval(node.values[i], glob, loc)
                if key is None:
                    for k in value:
                        out[k] = value[k]
                else:
                    out[self.eval(key, glob, loc)] = value
                i = i + 1
            return out
        if kind == "JoinedStr":
            out = ""
            for part in node.values:
                if part.kind == "Constant":
                    out = out + part.value
                else:
                    value = self.eval(part.value, glob, loc)
                    if part.get("conversion") == 114:
                        value = repr(value)
                    spec = ""
                    if part.get("format_spec") is not None:
                        spec = self.eval(part.format_spec, glob, loc)
                    out = out + format(value, spec)
            return out
        if kind == "Lambda":
            return _Function(node, glob, loc, self)
        if kind == "NamedExpr":
            value = self.eval(node.value, glob, loc)
            loc[node.target.id] = value
            return value
        if kind == "Starred":
            return self.eval(node.value, glob, loc)
        self.unsupported(node)

    def elements(self, elts, glob, loc):
        out = []
        for one in elts:
            if one.kind == "Starred":
                for item in self.eval(one.value, glob, loc):
                    out.append(item)
            else:
                out.append(self.eval(one, glob, loc))
        return out

    def call(self, node, glob, loc):
        fn = self.eval(node.func, glob, loc)
        args = self.elements(node.args, glob, loc)
        named = {}
        for kw in node.get("keywords") or []:
            value = self.eval(kw.value, glob, loc)
            if kw.get("arg") is None:
                for key in value:
                    named[key] = value[key]
            else:
                named[kw.arg] = value
        return fn(*args, **named)

    def binary(self, op, a, b):
        if op == "+":
            return a + b
        if op == "-":
            return a - b
        if op == "*":
            return a * b
        if op == "/":
            return a / b
        if op == "//":
            return a // b
        if op == "%":
            return a % b
        if op == "**":
            return a ** b
        if op == "|":
            return a | b
        if op == "&":
            return a & b
        if op == "^":
            return a ^ b
        if op == "<<":
            return a << b
        if op == ">>":
            return a >> b
        if op == "@":
            return a @ b
        raise NotImplementedError("operator " + str(op))

    def compare(self, op, a, b):
        if op == "==":
            return a == b
        if op == "!=":
            return a != b
        if op == "<":
            return a < b
        if op == "<=":
            return a <= b
        if op == ">":
            return a > b
        if op == ">=":
            return a >= b
        if op == "is":
            return a is b
        if op == "is not":
            return a is not b
        if op == "in":
            return a in b
        if op == "not in":
            return a not in b
        raise NotImplementedError("comparison " + str(op))

    # -- statements ------------------------------------------------------
    def run(self, body, glob, loc):
        for stmt in body:
            self.statement(stmt, glob, loc)

    def assign(self, target, value, glob, loc):
        kind = target.kind
        if kind == "Name":
            loc[target.id] = value
            return
        if kind in ("Tuple", "List"):
            items = list(value)
            i = 0
            for one in target.elts:
                self.assign(one, items[i], glob, loc)
                i = i + 1
            return
        if kind == "Subscript":
            holder = self.eval(target.value, glob, loc)
            holder[self.eval(target.slice, glob, loc)] = value
            return
        if kind == "Attribute":
            setattr(self.eval(target.value, glob, loc), target.attr, value)
            return
        self.unsupported(target)

    def statement(self, node, glob, loc):
        kind = node.kind
        if kind == "Expr":
            self.eval(node.value, glob, loc)
            return
        if kind == "Assign":
            value = self.eval(node.value, glob, loc)
            for target in node.targets:
                self.assign(target, value, glob, loc)
            return
        if kind == "AugAssign":
            current = self.eval(node.target, glob, loc)
            value = self.binary(node.op[:-1] if node.op.endswith("=")
                                else node.op,
                                current, self.eval(node.value, glob, loc))
            self.assign(node.target, value, glob, loc)
            return
        if kind == "AnnAssign":
            if node.get("value") is not None:
                self.assign(node.target,
                            self.eval(node.value, glob, loc), glob, loc)
            return
        if kind == "If":
            if self.eval(node.test, glob, loc):
                self.run(node.body, glob, loc)
            else:
                self.run(node.get("orelse") or [], glob, loc)
            return
        if kind == "While":
            while self.eval(node.test, glob, loc):
                try:
                    self.run(node.body, glob, loc)
                except _Jump as jump:
                    if jump.kind == "break":
                        return
            return
        if kind == "For":
            for item in self.eval(node.iter, glob, loc):
                self.assign(node.target, item, glob, loc)
                try:
                    self.run(node.body, glob, loc)
                except _Jump as jump:
                    if jump.kind == "break":
                        return
            self.run(node.get("orelse") or [], glob, loc)
            return
        if kind == "FunctionDef":
            loc[node.name] = _Function(node, glob, loc, self)
            return
        if kind == "Return":
            raise _Returned(self.eval(node.value, glob, loc)
                            if node.get("value") is not None else None)
        if kind == "Break":
            raise _Jump("break")
        if kind == "Continue":
            raise _Jump("continue")
        if kind == "Pass":
            return
        if kind == "Raise":
            if node.get("exc") is None:
                raise RuntimeError("No active exception to re-raise")
            raise self.eval(node.exc, glob, loc)
        if kind == "Assert":
            if not self.eval(node.test, glob, loc):
                raise AssertionError(
                    self.eval(node.msg, glob, loc)
                    if node.get("msg") is not None else "")
            return
        if kind in ("Try", "TryStar"):
            try:
                self.run(node.body, glob, loc)
            except _Jump:
                raise
            except _Returned:
                raise
            except Exception as caught:
                for handler in node.handlers:
                    want = (self.eval(handler.type, glob, loc)
                            if handler.get("type") is not None else Exception)
                    if isinstance(caught, want):
                        if handler.get("name"):
                            loc[handler.name] = caught
                        self.run(handler.body, glob, loc)
                        break
                else:
                    self.run(node.get("finalbody") or [], glob, loc)
                    raise
            else:
                self.run(node.get("orelse") or [], glob, loc)
            self.run(node.get("finalbody") or [], glob, loc)
            return
        self.unsupported(node)


def _prepared(source, filename, mode, glob, loc):
    """The tree, the globals and the locals, however the caller spelled them."""
    made = source if isinstance(source, code) else _compile(
        source, filename, mode)
    if glob is None:
        glob = {}
    if loc is None:
        loc = glob
    return made, glob, loc


def eval(source, globals=None, locals=None):
    """Evaluate ONE EXPRESSION and answer what it came to.

    The argument names are CPython's, shadowing two builtins, because a
    program writes `eval(src, ns)` and may write `eval(src, globals=ns)`.
    """
    made, glob, loc = _prepared(source, "<string>", "eval", globals, locals)
    body = made.tree.get("body")
    return _Walker().eval(body, glob, loc)


def exec(source, globals=None, locals=None):
    """Run STATEMENTS, and answer None -- which is the whole difference."""
    made, glob, loc = _prepared(source, "<string>", "exec", globals, locals)
    body = made.tree.get("body")
    if not isinstance(body, list):
        body = [body]
    _Walker().run(body, glob, loc)
    return None
