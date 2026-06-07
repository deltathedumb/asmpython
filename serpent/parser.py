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
            # Decorators are accepted but silently dropped. Their semantics
            # (e.g. `@dataclass` synthesising __init__) aren't modelled yet
            # — accepting the syntax just lets source that uses them parse.
            self._eat_decorators()
            if self._check("KEYWORD", "def"):
                funcs.append(self._parse_funcdef())
            elif self._check("KEYWORD", "class"):
                classes.append(self._parse_classdef())
            else:
                body.append(self._parse_stmt())
            self._skip_newlines()
        return A.Module(funcs=funcs, body=body, classes=classes)

    def _eat_decorators(self) -> None:
        """Consume zero or more `@expr` lines preceding a def/class."""
        while self._check("OP", "@"):
            self._eat()
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
            self._skip_newlines()

    def _parse_classdef(self) -> A.ClassDef:
        start = self._expect("KEYWORD", "class").pos
        name = self._expect("NAME").value
        parent = None
        if self._check("OP", "("):
            self._eat()
            if not self._check("OP", ")"):
                parent = self._expect("NAME").value
            self._expect("OP", ")")
        self._expect("OP", ":")
        self._expect("NEWLINE")
        self._skip_newlines()
        self._expect("INDENT")
        methods: list[A.FuncDef] = []
        while not self._check("DEDENT"):
            self._skip_newlines()
            if self._check("DEDENT"):
                break
            if self._check("KEYWORD", "pass"):
                self._eat()
                self._expect("NEWLINE")
                continue
            # Decorators on methods: accept but drop.
            self._eat_decorators()
            if self._check("KEYWORD", "def"):
                methods.append(self._parse_funcdef())
            elif self._check("STRING"):
                # Class-body string literal (docstring) — drop the line.
                self._eat()
                self._expect("NEWLINE")
            elif self._check("NAME"):
                # Class-body field declaration: `name [: type] [= default]`.
                # We don't model class-level attributes at the value layer
                # yet, but accepting the syntax lets @dataclass-style sources
                # parse. Any default is evaluated for side effects (None) but
                # otherwise discarded.
                self._eat_class_field_decl()
            else:
                raise ParseError(
                    "class bodies may only contain 'def' methods, field "
                    "declarations, docstrings, or 'pass'",
                    self._peek().pos,
                )
            self._skip_newlines()
        self._expect("DEDENT")
        return A.ClassDef(name=name, parent=parent, methods=methods, pos=start)  # type: ignore

    def _eat_class_field_decl(self) -> None:
        """Drop a class-body line like `name: type` or `name: type = default`.
        Used to swallow @dataclass field declarations without modelling them."""
        self._expect("NAME")
        if self._check("OP", ":"):
            self._eat()
            self._parse_type_annotation()
        if self._check("OP", "="):
            self._eat()
            # Default expression — discard tokens until end of line. We don't
            # call _parse_expr because that would build an AST we can't use.
            depth = 0
            while True:
                t = self._peek()
                if t.kind == "NEWLINE" and depth == 0:
                    break
                if t.kind == "EOF":
                    break
                if t.kind == "OP" and t.value in ("(", "[", "{"):
                    depth += 1
                elif t.kind == "OP" and t.value in (")", "]", "}"):
                    depth -= 1
                self._eat()
        self._expect("NEWLINE")

    def _parse_funcdef(self) -> A.FuncDef:
        start = self._peek().pos
        self._expect("KEYWORD", "def")
        name = self._expect("NAME").value
        self._expect("OP", "(")
        params: list[str] = []
        defaults: list = []  # parallel to params; None means required
        if not self._check("OP", ")"):
            self._parse_param(params, defaults)
            while self._check("OP", ","):
                self._eat()
                # A bare '*' is the keyword-only marker. We don't model
                # keyword args, but we accept the syntax so source that uses
                # it parses (subsequent params still behave positionally).
                if self._check("OP", "*"):
                    self._eat()
                    if not self._check("OP", ","):
                        # Not a marker -- it's *args; not supported yet.
                        raise ParseError(
                            "*args not supported yet",
                            self._peek().pos,
                        )
                    continue
                self._parse_param(params, defaults)
        self._expect("OP", ")")
        if self._check("OP", "->"):
            self._eat()
            self._parse_type_annotation()
        self._expect("OP", ":")
        body = self._parse_block()
        return A.FuncDef(
            name=name,  # type: ignore
            params=params,
            body=body,
            pos=start,
            defaults=defaults,
        )

    def _parse_param(self, params: list, defaults: list) -> None:
        """Parse one positional parameter: NAME [: type] [= default]."""
        params.append(self._expect("NAME").value)
        if self._check("OP", ":"):
            self._eat()
            self._parse_type_annotation()
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

    def _parse_type_annotation(self) -> None:
        """Eat a type annotation. We don't model types, so we just skip enough
        tokens to balance brackets. Supports: NAME, NAME[NAME], NAME[NAME, NAME],
        NAME | NAME, None."""
        depth = 0
        while True:
            t = self._peek()
            if depth == 0 and t.kind == "OP" and t.value in (",", ")", "=", ":"):
                break
            if depth == 0 and t.kind == "NEWLINE":
                break
            if t.kind == "OP" and t.value == "[":
                depth += 1
            elif t.kind == "OP" and t.value == "]":
                depth -= 1
                if depth < 0:
                    break
            self._eat()

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
            # `self.x: type [= value]`. Eat the annotation and lower to either
            # `self.x = value` or a no-op if no initializer is given.
            self._eat()
            self._parse_type_annotation()
            if self._check("OP", "="):
                self._eat()
                value = self._parse_expr()
                self._expect("NEWLINE")
                return A.AttrAssign(
                    obj=expr.obj, name=expr.name, value=value, pos=pos
                )
            self._expect("NEWLINE")
            return A.AttrAssign(
                obj=expr.obj,
                name=expr.name,
                value=A.IntLit(value=0, pos=pos),
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
        self._expect("KEYWORD", "except")
        bind_name = None
        if self._check("KEYWORD", "as"):
            self._eat()
            bind_name = self._expect("NAME").value
        self._expect("OP", ":")
        handler = self._parse_block()
        return A.Try(body=body, handler=handler, bind_name=bind_name, pos=kw.pos)  # type: ignore

    def _parse_raise(self) -> A.Raise:
        kw = self._expect("KEYWORD", "raise")
        value = self._parse_expr()
        self._expect("NEWLINE")
        return A.Raise(value=value, pos=kw.pos)

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
        value = self._parse_expr()
        self._expect("NEWLINE")
        return A.Assign(target=name_tok.value, value=value, pos=name_tok.pos)  # type: ignore

    def _parse_annotated_assign(self):
        """`name: type [= value]` at statement position.

        The annotation is parsed and discarded (serpent doesn't drive typing
        off annotations yet). If a value follows, returns an Assign;
        otherwise an ExprStmt of a no-op IntLit so the statement still has
        a body — the variable becomes defined in the scope of the wrapping
        block, just without a meaningful initial value.
        """
        name_tok = self._expect("NAME")
        self._expect("OP", ":")
        self._parse_type_annotation()
        if self._check("OP", "="):
            self._eat()
            value = self._parse_expr()
            self._expect("NEWLINE")
            return A.Assign(target=name_tok.value, value=value, pos=name_tok.pos)  # type: ignore[arg-type]
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
            value = self._parse_expr()
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

    def _parse_for(self) -> A.For:
        kw = self._expect("KEYWORD", "for")
        var = self._expect("NAME").value
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
            return A.For(var=var, range_args=args, body=body, pos=kw.pos)  # type: ignore
        # Any other expression: treat as iterable.
        iter_expr = self._parse_expr()
        self._expect("OP", ":")
        body = self._parse_block()
        return A.For(var=var, range_args=[], body=body, pos=kw.pos, iter=iter_expr)  # type: ignore

    # ---- expressions -------------------------------------------------------
    # Precedence (low -> high):
    #   or, and, not, comparisons, | , ^, &, << >>, + -, * / // %, unary, primary
    def _parse_expr(self):
        return self._parse_or()

    def _parse_or(self):
        left = self._parse_and()
        while self._check("KEYWORD", "or"):
            pos = self._eat().pos
            right = self._parse_and()
            left = A.BoolOp(op="or", left=left, right=right, pos=pos)
        return left

    def _parse_and(self):
        left = self._parse_not()
        while self._check("KEYWORD", "and"):
            pos = self._eat().pos
            right = self._parse_not()
            left = A.BoolOp(op="and", left=left, right=right, pos=pos)
        return left

    def _parse_not(self):
        if self._check("KEYWORD", "not"):
            pos = self._eat().pos
            return A.UnaryOp(op="not", operand=self._parse_not(), pos=pos)
        return self._parse_cmp()

    def _parse_cmp(self):
        """Chained comparisons: a < b < c becomes Compare([<, <], [a, b, c]).

        Also folds `in` / `not in` into the Compare chain so codegen sees a
        uniform shape.
        """
        left = self._parse_bit_or()
        cmp_ops = ("==", "!=", "<", "<=", ">", ">=")

        def _at_membership() -> str | None:
            """Detect `in`, `not in`, `is`, `is not`. Returns the
            normalised op string. We do NOT consume tokens here."""
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

        if not self._check_any_op(*cmp_ops) and _at_membership() is None:
            return left
        operands = [left]
        ops: list[str] = []
        first_pos = self._peek().pos
        while True:
            if self._check_any_op(*cmp_ops):
                ops.append(self._eat().value)  # type: ignore
            else:
                m = _at_membership()
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

    def _parse_bit_or(self):
        left = self._parse_bit_xor()
        while self._check_any_op("|"):
            pos = self._eat().pos
            right = self._parse_bit_xor()
            left = A.BinOp(op="|", left=left, right=right, pos=pos)
        return left

    def _parse_bit_xor(self):
        left = self._parse_bit_and()
        while self._check_any_op("^"):
            pos = self._eat().pos
            right = self._parse_bit_and()
            left = A.BinOp(op="^", left=left, right=right, pos=pos)
        return left

    def _parse_bit_and(self):
        left = self._parse_shift()
        while self._check_any_op("&"):
            pos = self._eat().pos
            right = self._parse_shift()
            left = A.BinOp(op="&", left=left, right=right, pos=pos)
        return left

    def _parse_shift(self):
        left = self._parse_add()
        while self._check_any_op("<<", ">>"):
            tok = self._eat()
            right = self._parse_add()
            left = A.BinOp(op=tok.value, left=left, right=right, pos=tok.pos)  # type: ignore
        return left

    def _parse_add(self):
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

    def _parse_unary(self):
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

    def _parse_primary(self):
        t = self._peek()
        if t.kind == "INT":
            self._eat()
            atom = A.IntLit(value=t.value, pos=t.pos)  # type: ignore
        elif t.kind == "FLOAT":
            self._eat()
            atom = A.FloatLit(value=t.value, pos=t.pos)  # type: ignore
        elif t.kind == "STRING":
            self._eat()
            atom = A.StrLit(value=t.value, pos=t.pos)  # type: ignore
        elif t.kind == "FSTRING":
            atom = self._parse_fstring()
        elif t.kind == "KEYWORD" and t.value in ("True", "False"):
            self._eat()
            atom = A.IntLit(value=1 if t.value == "True" else 0, pos=t.pos)
        elif t.kind == "KEYWORD" and t.value == "None":
            self._eat()
            atom = A.IntLit(value=0, pos=t.pos)
        elif t.kind == "OP" and t.value == "(":
            self._eat()
            atom = self._parse_expr()
            self._expect("OP", ")")
        elif t.kind == "OP" and t.value == "[":
            atom = self._parse_list_lit()
        elif t.kind == "OP" and t.value == "{":
            atom = self._parse_dict_lit()
        elif t.kind == "NAME":
            self._eat()
            if self._check("OP", "("):
                self._eat()
                args: list = []
                if not self._check("OP", ")"):
                    args.append(self._parse_expr())
                    while self._check("OP", ","):
                        self._eat()
                        args.append(self._parse_expr())
                self._expect("OP", ")")
                atom = A.Call(func=t.value, args=args, pos=t.pos)  # type: ignore
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
                # Slice form: [start:stop]; either side may be omitted.
                start = None
                if not self._check("OP", ":"):
                    start = self._parse_expr()
                if self._check("OP", ":"):
                    self._eat()
                    stop = None
                    if not self._check("OP", "]"):
                        stop = self._parse_expr()
                    self._expect("OP", "]")
                    idx = A.Slice(start=start, stop=stop, pos=lbr.pos)
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
                    args: list = []
                    if not self._check("OP", ")"):
                        args.append(self._parse_expr())
                        while self._check("OP", ","):
                            self._eat()
                            args.append(self._parse_expr())
                    self._expect("OP", ")")
                    atom = A.MethodCall(obj=atom, method=name, args=args, pos=dot.pos)  # type: ignore
                else:
                    # obj.name — attribute access (e.g. math.pi)
                    atom = A.Attr(obj=atom, name=name, pos=dot.pos)  # type: ignore
            else:
                return atom

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

    def _parse_dict_lit(self) -> A.DictLit:
        start = self._expect("OP", "{").pos
        keys: list = []
        values: list = []
        if not self._check("OP", "}"):
            keys.append(self._parse_expr())
            self._expect("OP", ":")
            values.append(self._parse_expr())
            while self._check("OP", ","):
                self._eat()
                if self._check("OP", "}"):
                    break  # trailing comma
                keys.append(self._parse_expr())
                self._expect("OP", ":")
                values.append(self._parse_expr())
        self._expect("OP", "}")
        return A.DictLit(keys=keys, values=values, pos=start)

    def _parse_list_lit(self) -> A.ListLit:
        start = self._expect("OP", "[").pos
        elems: list = []
        if not self._check("OP", "]"):
            elems.append(self._parse_expr())
            while self._check("OP", ","):
                self._eat()
                if self._check("OP", "]"):
                    break  # trailing comma
                elems.append(self._parse_expr())
        self._expect("OP", "]")
        return A.ListLit(elems=elems, pos=start)
