"""Recursive-descent parser. Every node it constructs gets a real SourcePos."""

from __future__ import annotations

from .lexer import Token, Lexer
from .errors import ErrorCode, ParseError
from . import ast_nodes as A


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
    def __init__(self, tokens: list[Token]) -> None:
        self.toks = tokens
        self.i = 0
        # Nested function definitions are lifted to module level here so sema
        # can resolve calls to them (closures aren't compiled, but parsing
        # must succeed so the self-hosting gauntlet can proceed).
        self._nested_funcs: list = []

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

    # ---- top level ---------------------------------------------------------

    def _find_free_vars(self, fdef: A.FuncDef) -> tuple:
        """Return (free_vars, nonlocal_vars) for fdef.

        free_vars: outer-scope names referenced in fdef's body that are not
        locally bound (params / non-nonlocal assigns).
        nonlocal_vars: subset of free_vars declared `nonlocal` in the body;
        these must be captured by reference (boxed cell) so mutations are shared."""
        local_names: set = set(fdef.params)
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
        # Collect names that are assigned (bound) inside the body (skip nonlocal).
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
                elif isinstance(s, A.If):
                    _collect_assigned(s.then)
                    _collect_assigned(s.orelse)
                elif isinstance(s, A.While):
                    _collect_assigned(s.body)
                elif isinstance(s, A.Try):
                    _collect_assigned(s.body)
                    _collect_assigned(s.handler)
        _collect_assigned(fdef.body)
        # Collect all Name references in the body.
        referenced: set = set()
        def _collect_refs(stmts: list) -> None:
            for s in stmts:
                _collect_refs_expr(s)
        def _collect_refs_expr(node) -> None:
            if isinstance(node, A.Name):
                referenced.add(node.name)
            elif isinstance(node, A.BinOp):
                _collect_refs_expr(node.left)
                _collect_refs_expr(node.right)
            elif isinstance(node, A.UnaryOp):
                _collect_refs_expr(node.operand)
            elif isinstance(node, A.Call):
                for a in node.args:
                    _collect_refs_expr(a)
            elif isinstance(node, A.Assign):
                _collect_refs_expr(node.value)
            elif isinstance(node, A.AugAssign):
                _collect_refs_expr(node.value)
            elif isinstance(node, A.Return):
                if node.value is not None:
                    _collect_refs_expr(node.value)
            elif isinstance(node, A.If):
                _collect_refs_expr(node.test)
                _collect_refs(node.then)
                _collect_refs(node.orelse)
            elif isinstance(node, A.While):
                _collect_refs_expr(node.test)
                _collect_refs(node.body)
            elif isinstance(node, A.For):
                if node.iter is not None:
                    _collect_refs_expr(node.iter)
                _collect_refs(node.body)
            elif isinstance(node, A.ExprStmt):
                _collect_refs_expr(node.expr)
            elif isinstance(node, A.Attr):
                _collect_refs_expr(node.obj)
            elif isinstance(node, A.Compare):
                for op in node.operands:
                    _collect_refs_expr(op)
            elif isinstance(node, A.BoolOp):
                for v in node.values:
                    _collect_refs_expr(v)
        _collect_refs(fdef.body)
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
        free = [n for n in sorted(referenced - local_names) if n not in BUILTINS]
        nl = [n for n in free if n in nonlocal_names]
        return free, nl

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
                classes.append(self._parse_classdef(decorators=decorators))
            else:
                body.append(self._parse_stmt())
            self._skip_newlines()
        # Add any nested functions parsed inside function bodies (lifted to
        # module level so calls to them resolve in sema).
        funcs.extend(self._nested_funcs)
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
                # `@<prop>.setter` / `.getter` / `.deleter`: capture the
                # dotted accessor form (e.g. "x.setter") so sema can match
                # it against the property getter method of the same name.
                if (
                    self._peek(1).kind == "OP"
                    and self._peek(1).value == "."
                    and self._peek(2).kind == "NAME"
                    and self._peek(2).value in ("setter", "getter", "deleter")
                ):
                    name = f"{name}.{self._peek(2).value}"
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

    def _parse_classdef(self, decorators: "list[str] | None" = None) -> A.ClassDef:
        is_dc = bool(decorators and "dataclass" in decorators)
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
            is_dataclass=is_dc,
            decorators=list(decorators) if decorators else [],
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
                if self._check("OP", ":"):
                    self._eat()
                    self._parse_type_annotation()  # value-type annotation; dict[str, T] either way
                params.append(kwarg)
                param_types.append(("dict", None))
                defaults.append(None)
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
            kwarg=kwarg,
            asm_body=asm_body,
            asm_symbol=asm_symbol,
            decorators=list(decorators) if decorators else [],
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

    def _parse_default_literal(self):
        """Parse one default-argument literal: int/float/str/bool/None, or a
        `[...]`/`{...}` literal of such values (e.g. `choices: list[str] = []`).
        Negation prefix permitted on numbers so `def f(x=-1)` works."""
        eq = self._peek()
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
            self._eat()
            return A.FloatLit(value=-t.value if neg else t.value, pos=t.pos)  # type: ignore
        if neg:
            raise ParseError("unary '-' only allowed before numeric default", eq.pos, ErrorCode.P_INVALID_DEFAULT)
        if t.kind == "STRING":
            self._eat()
            return A.StrLit(value=t.value, pos=t.pos)  # type: ignore
        if t.kind == "KEYWORD" and t.value in ("True", "False", "None"):
            self._eat()
            v = 1 if t.value == "True" else 0
            return A.IntLit(
                value=v,
                pos=t.pos,
                is_bool=t.value != "None",
                is_none=t.value == "None",
            )
        if t.kind == "OP" and t.value == "[":
            return self._parse_default_list(t)
        if t.kind == "OP" and t.value == "{":
            return self._parse_default_dict(t)
        raise ParseError(
            f"default argument must be a literal (int/float/str/True/False/None/list/dict), got {t.kind} {t.value!r}",
            t.pos,
        )

    def _parse_default_list(self, open_tok: Token) -> "A.ListLit":
        """`[a, b, ...]` as a default argument value. Element type follows
        the first element (homogeneous, like other asmpython list literals);
        empty (`[]`) defaults to "int" (the caller's annotation, if any,
        decides the param's actual element type)."""
        self._eat()  # '['
        elems: list = []
        while not self._check("OP", "]"):
            elems.append(self._parse_default_literal())
            if self._check("OP", ","):
                self._eat()
            else:
                break
        self._expect("OP", "]")
        el_type = "int"
        if elems:
            if isinstance(elems[0], A.StrLit):
                el_type = "str"
            elif isinstance(elems[0], A.FloatLit):
                el_type = "float"
        return A.ListLit(elems=elems, pos=open_tok.pos, el_type=el_type)

    def _parse_default_dict(self, open_tok: Token) -> "A.DictLit":
        """`{}` as a default argument value. Only the empty dict literal is
        supported (asmpython dict literals require str keys; an empty dict
        needs no key/value type info)."""
        self._eat()  # '{'
        self._expect("OP", "}")
        return A.DictLit(keys=[], values=[], pos=open_tok.pos)

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

    def _parse_annot_from_string(self, src: str) -> tuple:
        """Re-lex and parse a quoted forward-reference annotation's contents
        (e.g. the `Path` in `-> "Path"`) as a normal type expression, by
        running it through a fresh `Lexer`/`Parser` pair. Any lex/parse
        failure (the string isn't actually a type expression) falls back to
        unconstrained `any`, matching the previous behaviour."""
        try:
            sub = Parser(Lexer(src).tokenize())
            return sub._parse_annot_union()
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
            val = inner[1][0] if len(inner) >= 2 else None
            return ("dict", val)
        if name in ("tuple", "Tuple"):
            return ("tuple", inner)
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
            if t.value == "def":
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
        # Tuple assignment with at least one subscript/attribute target, e.g.
        # `xs[0], xs[1] = xs[1], xs[0]` or `a, self.x = self.x, a`. Pure
        # NAME-only sequences are handled above by `_parse_tuple_assign`.
        if isinstance(expr, (A.Name, A.Subscript, A.Attr)) and self._check("OP", ","):
            targets = [expr]
            while self._check("OP", ","):
                self._eat()
                tgt = self._parse_expr()
                if not isinstance(tgt, (A.Name, A.Subscript, A.Attr)):
                    raise ParseError("cannot assign to this expression", tgt.pos, ErrorCode.P_INVALID_ASSIGN_TARGET)
                targets.append(tgt)
            self._expect("OP", "=")
            values = [self._parse_expr()]
            while self._check("OP", ","):
                self._eat()
                values.append(self._parse_expr())
            self._expect("NEWLINE")
            return A.TupleAssign(targets=targets, values=values, pos=pos)
        if isinstance(expr, A.Subscript) and self._check("OP", "="):
            self._eat()
            value = self._parse_expr()
            self._expect("NEWLINE")
            return A.IndexAssign(target=expr, value=value, pos=pos)
        if isinstance(expr, A.Subscript) and self._peek().kind == "OP" and self._peek().value in AUG_OPS:
            op_tok = self._eat()
            rhs = self._parse_expr()
            self._expect("NEWLINE")
            combined = A.BinOp(op=AUG_OPS[op_tok.value], left=expr, right=rhs, pos=op_tok.pos)
            return A.IndexAssign(target=expr, value=combined, pos=pos)
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

    @staticmethod
    def _exc_type_name(e) -> str | None:
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
        self._expect("NEWLINE")
        return A.Del(target=target, pos=kw.pos)

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
            raise ParseError("expected module name after 'from'", self._peek().pos, ErrorCode.P_MISSING_MODULE)
        self._expect("KEYWORD", "import")
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
            nm: str = self._expect("NAME").value  # type: ignore[assignment]
            names.append(nm)
            orig_names.append(nm)
            if self._check("KEYWORD", "as"):
                self._eat()
                names[-1] = self._expect("NAME").value  # type: ignore[assignment]
        self._expect("NEWLINE")
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
            orelse_f: list = []
            self._skip_newlines()
            if self._check("KEYWORD", "else"):
                self._eat()
                self._expect("OP", ":")
                orelse_f = self._parse_block()
            return A.For(var=var, range_args=args, body=body, pos=kw.pos, targets=multi, orelse=orelse_f)  # type: ignore
        # Any other expression: treat as iterable.
        iter_expr = self._parse_expr()
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
        if not self._check("OP", ":"):
            params.append(self._expect("NAME").value)
            while self._check("OP", ","):
                self._eat()
                if self._check("OP", ":"):
                    break
                params.append(self._expect("NAME").value)
        self._expect("OP", ":")
        body = self._parse_ternary()
        return A.Lambda(params=params, body=body, pos=pos)

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
            byte_elems: list = [
                A.IntLit(value=ord(c), pos=t.pos)
                for c in t.value  # type: ignore
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
            keys = [first]
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
        # One or more loop targets: `for x in ...` or `for k, v in ...`
        # (mirrors `_parse_for`).
        targets = [self._parse_for_target()]
        while self._check("OP", ","):
            self._eat()
            if self._check("KEYWORD", "in"):
                break  # trailing comma before `in`
            targets.append(self._parse_for_target())
        single = len(targets) == 1 and isinstance(targets[0], str)
        var = targets[0] if single else ""
        multi = [] if single else targets
        self._expect("KEYWORD", "in")
        iter_expr = self._parse_or()
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
            etargets = [self._parse_for_target()]
            while self._check("OP", ","):
                self._eat()
                if self._check("KEYWORD", "in"):
                    break
                etargets.append(self._parse_for_target())
            evar2: str = etargets[0] if len(etargets) == 1 and isinstance(etargets[0], str) else ""
            emulti2: list = [] if len(etargets) == 1 and isinstance(etargets[0], str) else etargets
            self._expect("KEYWORD", "in")
            eiter2 = self._parse_or()
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
        targets = [self._parse_for_target()]
        while self._check("OP", ","):
            self._eat()
            if self._check("KEYWORD", "in"):
                break  # trailing comma before `in`
            targets.append(self._parse_for_target())
        single = len(targets) == 1 and isinstance(targets[0], str)
        var = targets[0] if single else ""
        multi = [] if single else targets
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
            targets=multi,
        )
