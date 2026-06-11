"""Recursive-descent parser. Every node it constructs gets a real SourcePos."""

from __future__ import annotations

from .lexer import Token, Lexer
from .errors import ParseError
from . import ast_nodes as A


AUG_OPS = {
    "+=": "+",
    "-=": "-",
    "*=": "*",
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
    def __init__(self, tokens: list[Token]) -> None:
        self.toks = tokens
        self.i = 0

    # ---- helpers -----------------------------------------------------------

    def _peek(self, off: int = 0) -> Token:
        return self.toks[self.i + off]

    def _eat(self) -> Token:
        t = self.toks[self.i]
        self.i += 1
        return t

    def _expect(self, kind: str, value: object = None) -> Token:
        t = self.toks[self.i]
        if t.kind != kind or (value is not None and t.value != value):
            want = f"{kind} {value!r}" if value is not None else kind
            raise ParseError(f"expected {want}, got {t.kind} {t.value!r}", t.pos)
        self.i += 1
        return t

    def _check(self, kind: str, value: object = None) -> bool:
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

    # ---- top level ---------------------------------------------------------

    def parse(self) -> A.Module:
        funcs: list[A.FuncDef] = []
        classes: list[A.ClassDef] = []
        body: list = []
        self._skip_newlines()
        while not self._check("EOF"):
            # Decorators are accepted; most are dropped (their semantics, e.g.
            # `@dataclass` synthesising __init__, aren't modelled). The one we
            # act on is `@assembly_func`, which marks a raw-NASM function body.
            decorators = self._eat_decorators()
            if self._check("KEYWORD", "def"):
                funcs.append(self._parse_funcdef(decorators=decorators))
            elif self._check("KEYWORD", "class"):
                classes.append(self._parse_classdef())
            else:
                body.append(self._parse_stmt())
            self._skip_newlines()
        return A.Module(funcs=funcs, body=body, classes=classes)

    # Decorator names the parser treats specially.
    _ASM_DECORATOR = "assembly_func"

    def _eat_decorators(self) -> list[str]:
        """Consume zero or more `@expr` lines preceding a def/class.

        Returns the leading dotted name of each decorator (e.g. `assembly_func`
        for `@assembly_func` or `@assembly_func(symbol="x")`). Callers inspect
        the list for decorators that change codegen; the rest are informational.
        """
        names: list[str] = []
        while self._check("OP", "@"):
            self._eat()
            # First NAME (optionally dotted) is the decorator's identity.
            name = None
            if self._check("NAME"):
                name = self._peek().value
            # Eat the rest of the line as a free-form decorator expression.
            # We don't model the call so we just skip until NEWLINE, balancing
            # any `(` `[` `{` along the way.
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
            if name is not None:
                names.append(name)  # type: ignore
            self._skip_newlines()
        return names

    def _parse_classdef(self) -> A.ClassDef:
        start = self._expect("KEYWORD", "class").pos
        name = self._expect("NAME").value
        parent = None
        if self._check("OP", "("):
            self._eat()
            if not self._check("OP", ")"):
                # First base class, possibly dotted (`module.Base`).
                parent = self._expect("NAME").value
                while self._check("OP", "."):
                    self._eat()
                    parent = f"{parent}.{self._expect('NAME').value}"
                # Extra bases / keyword bases (multiple inheritance, metaclass=)
                # aren't modelled — single inheritance only. Skip the rest so
                # the source still parses.
                while self._check("OP", ","):
                    self._eat()
                    self._parse_expr()
            self._expect("OP", ")")
        self._expect("OP", ":")
        self._expect("NEWLINE")
        self._skip_newlines()
        self._expect("INDENT")
        methods: list[A.FuncDef] = []
        class_vars: list = []
        while not self._check("DEDENT"):
            self._skip_newlines()
            if self._check("DEDENT"):
                break
            if self._check("KEYWORD", "pass"):
                self._eat()
                self._expect("NEWLINE")
                continue
            # Decorators on methods: mostly dropped; @assembly_func is honored.
            decorators = self._eat_decorators()
            if self._check("KEYWORD", "def"):
                methods.append(self._parse_funcdef(decorators=decorators))
            elif self._check("STRING"):
                # Class-body string literal (docstring) — drop the line.
                self._eat()
                self._expect("NEWLINE")
            elif self._check("NAME"):
                # Class-body variable: `name [: type] [= value]`. Captured so
                # sema can type `self.NAME` reads (e.g. a set/dict constant used
                # for membership). @dataclass-style annotation-only fields are
                # captured too (value None).
                class_vars.append(self._parse_class_var())
            else:
                raise ParseError(
                    "class bodies may only contain 'def' methods, field "
                    "declarations, docstrings, or 'pass'",
                    self._peek().pos,
                )
            self._skip_newlines()
        self._expect("DEDENT")
        return A.ClassDef(  # type: ignore
            name=name,  # type: ignore
            parent=parent,  # type: ignore
            methods=methods,
            pos=start,  # type: ignore
            class_vars=class_vars,
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
                self._skip_to_line_end()  # dataclass field(...) — type via annot
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
        self._expect("KEYWORD", "def")
        name = self._expect("NAME").value
        self._expect("OP", "(")
        params: list[str] = []
        defaults: list = []  # parallel to params; None means required
        param_types: list = []  # parallel to params; None means unannotated
        vararg: str | None = None
        first = True
        while not self._check("OP", ")"):
            if not first:
                self._expect("OP", ",")
                if self._check("OP", ")"):
                    break  # trailing comma
            first = False
            # `**kwargs`: accepted so source parses; asmpython doesn't model
            # keyword-collection, so the name is discarded.
            if self._check("OP", "**"):
                self._eat()
                self._expect("NAME")
                continue
            if self._check("OP", "*"):
                self._eat()
                if self._check("NAME"):
                    # `*args`: a single list-typed parameter that absorbs the
                    # caller's surplus positional arguments.
                    vararg = self._expect("NAME").value  # type: ignore
                    el = None
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
        body = self._parse_block()
        asm_body = None
        asm_symbol = None
        if decorators and self._ASM_DECORATOR in decorators:
            asm_body, asm_symbol = self._extract_asm_body(name, body, start)  # type: ignore[arg-type]
        return A.FuncDef(
            name=name,  # type: ignore
            params=params,
            body=body,
            pos=start,
            defaults=defaults,
            param_types=param_types,
            ret_type=ret_type,
            vararg=vararg,
            asm_body=asm_body,
            asm_symbol=asm_symbol,
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
        eq = self._eat()
        t = self._peek()
        # Only literal defaults are allowed (MVP). Negation prefix permitted
        # so `def f(x=-1)` works.
        neg = False
        if t.kind == "OP" and t.value == "-":
            self._eat()
            neg = True
            t = self._peek()
        if t.kind == "INT":
            self._eat()
            return A.IntLit(value=-t.value if neg else t.value, pos=t.pos)  # type: ignore
        if t.kind == "FLOAT":
            # Float defaults need extra plumbing (xmm0 vs rax dispatch through
            # the call site). Not supported in the MVP; punt for now with a
            # clear error so the user knows the gap.
            raise ParseError(
                "float default arguments aren't supported yet; "
                "use an int default and convert inside the body",
                t.pos,
            )
        if neg:
            raise ParseError("unary '-' only allowed before numeric default", eq.pos)
        if t.kind == "STRING":
            self._eat()
            return A.StrLit(value=t.value, pos=t.pos)  # type: ignore
        if t.kind == "KEYWORD" and t.value in ("True", "False", "None"):
            self._eat()
            v = 1 if t.value == "True" else 0
            return A.IntLit(value=v, pos=t.pos)
        raise ParseError(
            f"default argument must be a literal (int/float/str/True/False/None), got {t.kind} {t.value!r}",
            t.pos,
        )

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
        # A quoted forward reference like `"Expr"` — re-lex isn't worth it;
        # treat as unconstrained.
        if t.kind == "STRING":
            self._eat()
            return ("any", None)
        if t.kind != "NAME":
            # Unexpected shape (e.g. the `[int]` inside `Callable[[int], str]`).
            # Skip a balanced atom so we don't misparse the enclosing list.
            return self._skip_annot_atom()
        name = self._eat().value
        while self._check("OP", "."):
            self._eat()
            name = f"{name}.{self._expect('NAME').value}"
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
        if name in ("int", "bool"):
            return ("int", None)
        if name in ("str",):
            return ("str", None)
        if name == "float":
            return ("float", None)
        if name in ("None", "none"):
            return ("none", None)
        if name in ("object", "Any", "bytes"):
            return ("any", None)
        if name in ("Optional",):
            return inner[0] if inner else ("any", None)
        if name in ("Union",):
            return ("any", None)
        if name in self._LIST_ANNOTS:
            el = inner[0][0] if inner else None
            return ("list", el)
        if name in self._DICT_ANNOTS:
            val = inner[1][0] if len(inner) >= 2 else None
            return ("dict", val)
        if name in ("tuple", "Tuple"):
            return ("tuple", None)
        if name in ("set", "Set", "frozenset"):
            return ("set", None)
        # Anything else (CamelCase class, dotted module.Class) is a class-ish
        # name; sema decides whether it's a known class -> instance:<name>.
        return (name, None)

    def _parse_block(self) -> list:
        self._expect("NEWLINE")
        self._skip_newlines()
        self._expect("INDENT")
        stmts: list = []
        while not self._check("DEDENT"):
            self._skip_newlines()
            if self._check("DEDENT"):
                break
            stmts.append(self._parse_stmt())
            self._skip_newlines()
        self._expect("DEDENT")
        return stmts

    # ---- statements --------------------------------------------------------

    def _parse_stmt(self):
        t = self._peek()
        if t.kind == "KEYWORD":
            if t.value == "return":
                return self._parse_return()
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
            if t.value == "import":
                return self._parse_import()
            if t.value == "from":
                return self._parse_from_import()
            if t.value == "try":
                return self._parse_try()
            if t.value == "raise":
                return self._parse_raise()
            if t.value == "assert":
                return self._parse_assert()

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

        # Save state so we can detect "lhs[i] = rhs" -> IndexAssign and
        # "lhs.name = rhs" -> AttrAssign.
        pos = t.pos
        expr = self._parse_expr()
        if isinstance(expr, A.Subscript) and self._check("OP", "="):
            self._eat()
            value = self._parse_expr()
            self._expect("NEWLINE")
            return A.IndexAssign(target=expr, value=value, pos=pos)
        if isinstance(expr, A.Attr) and self._check("OP", "="):
            self._eat()
            value = self._parse_expr()
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

    def _parse_try(self) -> A.Try:
        kw = self._expect("KEYWORD", "try")
        self._expect("OP", ":")
        body = self._parse_block()
        self._skip_newlines()
        # One or more `except` clauses, each with an optional exception type
        # and optional `as name` binding. The type expression is parsed (so
        # `except (A, B) as e:` is accepted) but discarded — asmpython has no
        # exception-class RTTI, so handlers catch everything.
        handlers: list = []  # list[(bind_name, body)]
        while self._check("KEYWORD", "except"):
            self._eat()
            if not self._check("OP", ":") and not self._check("KEYWORD", "as"):
                self._parse_expr()  # exception type — ignored
            bind_name = None
            if self._check("KEYWORD", "as"):
                self._eat()
                bind_name = self._expect("NAME").value
            self._expect("OP", ":")
            hbody = self._parse_block()
            handlers.append((bind_name, hbody))
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
        first_bind, first_body = handlers[0] if handlers else (None, [])
        return A.Try(
            body=body,
            handler=first_body,
            bind_name=first_bind,  # type: ignore[arg-type]
            extra_handlers=handlers[1:],
            else_body=else_body,
            finally_body=finally_body,
            pos=kw.pos,
        )

    def _parse_raise(self) -> A.Raise:
        kw = self._expect("KEYWORD", "raise")
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

    def _parse_import(self) -> A.Import:
        kw = self._expect("KEYWORD", "import")
        name = self._expect("NAME").value
        # Dotted module path: `import os.path`. Joined into one flat string.
        while self._check("OP", "."):
            self._eat()
            name = f"{name}.{self._expect('NAME').value}"
        # Optional `as` alias — accepted but the alias name replaces the
        # module name so subsequent `name.x` lookups go through it.
        if self._check("KEYWORD", "as"):
            self._eat()
            name = self._expect("NAME").value  # type: ignore[assignment]
        self._expect("NEWLINE")
        return A.Import(module=name, pos=kw.pos)  # type: ignore

    def _parse_from_import(self) -> A.FromImport:
        kw = self._expect("KEYWORD", "from")
        # Leading dots: `from .x import y`, `from .. import z`, etc.
        level = 0
        while self._check("OP", "."):
            self._eat()
            level += 1
        module = ""
        if self._check("NAME"):
            module = self._expect("NAME").value  # type: ignore[assignment]
            # Dotted module path: `from a.b.c import x`. Eat the rest as one
            # flat string so sema sees `a.b.c`.
            while self._check("OP", "."):
                self._eat()
                module = f"{module}.{self._expect('NAME').value}"
        elif level == 0:
            # `from import ...` with no module name is invalid.
            raise ParseError("expected module name after 'from'", self._peek().pos)
        self._expect("KEYWORD", "import")
        names: list[str] = [self._expect("NAME").value]  # type: ignore
        # Optional `as` alias is eaten but its alias name is what we bind.
        if self._check("KEYWORD", "as"):
            self._eat()
            names[-1] = self._expect("NAME").value  # type: ignore[assignment]
        while self._check("OP", ","):
            self._eat()
            names.append(self._expect("NAME").value)  # type: ignore
            if self._check("KEYWORD", "as"):
                self._eat()
                names[-1] = self._expect("NAME").value  # type: ignore[assignment]
        self._expect("NEWLINE")
        return A.FromImport(module=module, names=names, pos=kw.pos, level=level)  # type: ignore

    def _parse_assign(self) -> A.Assign:
        name_tok = self._expect("NAME")
        self._expect("OP", "=")
        value = self._parse_tuple_rhs()
        self._expect("NEWLINE")
        return A.Assign(target=name_tok.value, value=value, pos=name_tok.pos)  # type: ignore

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
        """Peek ahead to see if we're at `NAME ( , NAME )+ =`. Doesn't consume."""
        k = self.i
        if self.toks[k].kind != "NAME":
            return False
        k += 1
        while k < len(self.toks):
            t = self.toks[k]
            if t.kind != "OP" or t.value != ",":
                break
            k += 1
            if k >= len(self.toks) or self.toks[k].kind != "NAME":
                return False
            k += 1
        return (
            k < len(self.toks)
            and self.toks[k].kind == "OP"
            and self.toks[k].value == "="
        )

    def _parse_tuple_assign(self) -> A.TupleAssign:
        first = self._expect("NAME")
        targets = [first.value]
        while self._check("OP", ","):
            self._eat()
            targets.append(self._expect("NAME").value)
        self._expect("OP", "=")
        values = [self._parse_expr()]
        while self._check("OP", ","):
            self._eat()
            values.append(self._parse_expr())
        self._expect("NEWLINE")
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
        return A.While(test=test, body=body, pos=kw.pos)

    def _parse_for_target(self) -> "str | list":
        """A single for-loop target: a NAME, or a parenthesized group of NAMEs
        for nested unpacking (`for i, (a, b) in ...`). A plain name is returned
        as a str; a group is returned as a list[str]."""
        if self._check("OP", "("):
            self._eat()
            names: list = [self._expect("NAME").value]
            while self._check("OP", ","):
                self._eat()
                if self._check("OP", ")"):
                    break  # trailing comma
                names.append(self._expect("NAME").value)
            self._expect("OP", ")")
            return names
        return self._expect("NAME").value  # type: ignore

    def _parse_for(self) -> A.For:
        kw = self._expect("KEYWORD", "for")
        # One or more loop targets: `for x in ...` or `for k, v in ...`.
        targets = [self._parse_for_target()]
        while self._check("OP", ","):
            self._eat()
            if self._check("KEYWORD", "in"):
                break  # trailing comma before `in`
            targets.append(self._parse_for_target())
        # A single bare name keeps the simple `var` path; any unpacking
        # (multiple targets, or a parenthesized group) goes through `targets`.
        single = len(targets) == 1 and isinstance(targets[0], str)
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
                    args.append(self._parse_expr())
            self._expect("OP", ")")
            if not (1 <= len(args) <= 3):
                raise ParseError(
                    f"range() takes 1-3 arguments, got {len(args)}",
                    kw.pos,
                )
            self._expect("OP", ":")
            body = self._parse_block()
            return A.For(var=var, range_args=args, body=body, pos=kw.pos, targets=multi)  # type: ignore
        # Any other expression: treat as iterable.
        iter_expr = self._parse_expr()
        self._expect("OP", ":")
        body = self._parse_block()
        return A.For(
            var=var,  # type: ignore
            range_args=[],
            body=body,
            pos=kw.pos,
            iter=iter_expr,
            targets=multi,
        )

    # ---- expressions -------------------------------------------------------
    # Precedence (low -> high):
    #   ternary, or, and, not, comparisons, |, ^, &, << >>, + -, * / // %, unary, primary
    def _parse_expr(self) -> "A.Expr":
        return self._parse_ternary()

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
        if self._check_any_op("-"):
            pos = self._eat().pos
            return A.UnaryOp(op="-", operand=self._parse_unary(), pos=pos)
        if self._check_any_op("+"):
            self._eat()
            return self._parse_unary()
        if self._check_any_op("~"):
            pos = self._eat().pos
            return A.UnaryOp(op="~", operand=self._parse_unary(), pos=pos)
        return self._parse_primary()

    def _parse_primary(self) -> "A.Expr":
        t = self._peek()
        if t.kind == "INT":
            self._eat()
            atom = A.IntLit(value=t.value, pos=t.pos)  # type: ignore
        elif t.kind == "FLOAT":
            self._eat()
            atom = A.FloatLit(value=t.value, pos=t.pos)  # type: ignore
        elif t.kind == "STRING":
            self._eat()
            atom = self._absorb_string_concat(A.StrLit(value=t.value, pos=t.pos))  # type: ignore
        elif t.kind == "FSTRING":
            atom = self._absorb_string_concat(self._parse_fstring())
        elif t.kind == "KEYWORD" and t.value in ("True", "False"):
            self._eat()
            atom = A.IntLit(value=1 if t.value == "True" else 0, pos=t.pos)
        elif t.kind == "KEYWORD" and t.value == "None":
            self._eat()
            atom = A.IntLit(value=0, pos=t.pos)
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
            raise ParseError(f"unexpected token {t.kind} {t.value!r}", t.pos)

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
        first = self._parse_expr()
        if self._check("KEYWORD", "for"):
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
            elems.append(self._parse_expr())
        self._expect("OP", ")")
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
            else:
                if kwargs:
                    raise ParseError(
                        "positional argument follows keyword argument",
                        self._peek().pos,
                    )
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

    def _absorb_string_concat(self, atom: "A.Expr") -> "A.Expr":
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
            text = "".join(s.value for s in segments)
            return A.StrLit(value=text, pos=atom.pos)
        return A.FString(segments=segments, pos=atom.pos)

    def _parse_fstring(self) -> A.FString:
        tok = self._eat()
        segments: list = []
        for kind, text in tok.value:  # type: ignore
            if kind == "str":
                segments.append(A.StrLit(value=text, pos=tok.pos))
            else:
                # Re-lex the expression text and parse it as an expression.
                inner_toks = Lexer(text).tokenize()
                inner_parser = Parser(inner_toks)
                expr = inner_parser._parse_expr()
                # Trailing tokens after the expression are an error.
                while inner_parser._check("NEWLINE"):
                    inner_parser._eat()
                if not inner_parser._check("EOF"):
                    raise ParseError(
                        f"unexpected tokens in f-string expression: {text!r}",
                        tok.pos,
                    )
                segments.append(expr)
        return A.FString(segments=segments, pos=tok.pos)

    def _parse_brace(self):
        """Parse a `{...}` literal — either a dict or a set.

        `{}` is the empty dict (matching Python). Otherwise the first element
        decides: a `key: value` colon makes it a dict, anything else a set.
        """
        start = self._expect("OP", "{").pos
        if self._check("OP", "}"):
            self._eat()
            return A.DictLit(keys=[], values=[], pos=start)
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
            keys: list = [first]
            values: list = [first_value]
            while self._check("OP", ","):
                self._eat()
                if self._check("OP", "}"):
                    break  # trailing comma
                keys.append(self._parse_expr())
                self._expect("OP", ":")
                values.append(self._parse_expr())
            self._expect("OP", "}")
            return A.DictLit(keys=keys, values=values, pos=start)
        # Set literal.
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
            first = self._parse_expr()
            if self._check("KEYWORD", "for"):
                comp = self._parse_comprehension_tail(first, start)
                self._expect("OP", "]")
                return comp
            elems.append(first)
            while self._check("OP", ","):
                self._eat()
                if self._check("OP", "]"):
                    break  # trailing comma
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
        var = self._expect("NAME").value
        # Tuple targets (`for a, b in ...`) parse but only the first name is
        # bound today; the rest are accepted so source still parses.
        while self._check("OP", ","):
            self._eat()
            self._expect("NAME")
        self._expect("KEYWORD", "in")
        iter_expr = self._parse_or()
        cond = None
        if self._check("KEYWORD", "if"):
            self._eat()
            cond = self._parse_or()
        return A.Comprehension(elt=elt, var=var, iter=iter_expr, cond=cond, pos=pos)  # type: ignore

    def _parse_dict_comprehension_tail(self, key, value, pos):
        """Parse `for <var> in <iter> [if <cond>]` after `key: value`, returning
        a DictComprehension. Same `for`/`in`/`if` grammar as the list form."""
        self._expect("KEYWORD", "for")
        var = self._expect("NAME").value
        # Tuple targets parse but only the first name is bound (same limit as
        # the list comprehension).
        while self._check("OP", ","):
            self._eat()
            self._expect("NAME")
        self._expect("KEYWORD", "in")
        iter_expr = self._parse_or()
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
        )
