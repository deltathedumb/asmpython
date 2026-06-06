"""Recursive-descent parser. Every node it constructs gets a real SourcePos."""
from __future__ import annotations

from .lexer import Token, Lexer
from .errors import ParseError, SourcePos
from . import ast_nodes as A


AUG_OPS = {
    "+=": "+", "-=": "-", "*=": "*", "/=": "//", "//=": "//", "%=": "%",
    "&=": "&", "|=": "|", "^=": "^", "<<=": "<<", ">>=": ">>",
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
        body: list = []
        self._skip_newlines()
        while not self._check("EOF"):
            if self._check("KEYWORD", "def"):
                funcs.append(self._parse_funcdef())
            else:
                body.append(self._parse_stmt())
            self._skip_newlines()
        return A.Module(funcs=funcs, body=body)

    def _parse_funcdef(self) -> A.FuncDef:
        start = self._peek().pos
        self._expect("KEYWORD", "def")
        name = self._expect("NAME").value
        self._expect("OP", "(")
        params: list[str] = []
        if not self._check("OP", ")"):
            params.append(self._expect("NAME").value)
            while self._check("OP", ","):
                self._eat()
                params.append(self._expect("NAME").value)
        self._expect("OP", ")")
        if self._check("OP", "->"):
            self._eat()
            self._expect("NAME")
        self._expect("OP", ":")
        body = self._parse_block()
        return A.FuncDef(name=name, params=params, body=body, pos=start)

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

        # Assignment / aug-assignment vs expression statement.
        if t.kind == "NAME":
            nxt = self._peek(1)
            if nxt.kind == "OP" and nxt.value == "=":
                return self._parse_assign()
            if nxt.kind == "OP" and nxt.value in AUG_OPS:
                return self._parse_aug_assign()

        # Save state so we can detect "lhs[i] = rhs" and re-emit as IndexAssign.
        pos = t.pos
        saved = self.i
        expr = self._parse_expr()
        if isinstance(expr, A.Subscript) and self._check("OP", "="):
            self._eat()
            value = self._parse_expr()
            self._expect("NEWLINE")
            return A.IndexAssign(target=expr, value=value, pos=pos)
        self._expect("NEWLINE")
        return A.ExprStmt(expr=expr, pos=pos)

    def _parse_import(self) -> A.Import:
        kw = self._expect("KEYWORD", "import")
        name = self._expect("NAME").value
        self._expect("NEWLINE")
        return A.Import(module=name, pos=kw.pos)

    def _parse_from_import(self) -> A.FromImport:
        kw = self._expect("KEYWORD", "from")
        module = self._expect("NAME").value
        self._expect("KEYWORD", "import")
        names: list[str] = [self._expect("NAME").value]
        while self._check("OP", ","):
            self._eat()
            names.append(self._expect("NAME").value)
        self._expect("NEWLINE")
        return A.FromImport(module=module, names=names, pos=kw.pos)

    def _parse_assign(self) -> A.Assign:
        name_tok = self._expect("NAME")
        self._expect("OP", "=")
        value = self._parse_expr()
        self._expect("NEWLINE")
        return A.Assign(target=name_tok.value, value=value, pos=name_tok.pos)

    def _parse_aug_assign(self) -> A.AugAssign:
        name_tok = self._expect("NAME")
        op_tok = self._eat()
        op = AUG_OPS[op_tok.value]
        value = self._parse_expr()
        self._expect("NEWLINE")
        return A.AugAssign(target=name_tok.value, op=op, value=value, pos=name_tok.pos)

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
        if self._check("NAME") and self._peek().value == "range" and \
           self._peek(1).kind == "OP" and self._peek(1).value == "(":
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
            return A.For(var=var, range_args=args, body=body, pos=kw.pos)
        # Any other expression: treat as iterable.
        iter_expr = self._parse_expr()
        self._expect("OP", ":")
        body = self._parse_block()
        return A.For(var=var, range_args=[], body=body, pos=kw.pos, iter=iter_expr)

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
        """Chained comparisons: a < b < c becomes Compare([<, <], [a, b, c])."""
        left = self._parse_bit_or()
        cmp_ops = ("==", "!=", "<", "<=", ">", ">=")
        if not self._check_any_op(*cmp_ops):
            return left
        operands = [left]
        ops: list[str] = []
        first_pos = self._peek().pos
        while self._check_any_op(*cmp_ops):
            ops.append(self._eat().value)
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
            left = A.BinOp(op=tok.value, left=left, right=right, pos=tok.pos)
        return left

    def _parse_add(self):
        left = self._parse_mul()
        while self._check_any_op("+", "-"):
            tok = self._eat()
            right = self._parse_mul()
            left = A.BinOp(op=tok.value, left=left, right=right, pos=tok.pos)
        return left

    def _parse_mul(self):
        left = self._parse_unary()
        while self._check_any_op("*", "/", "//", "%"):
            tok = self._eat()
            right = self._parse_unary()
            # Keep '/' distinct from '//'. expr_type / codegen decide whether
            # this is int-int (where '/' acts like '//' since we have no
            # implicit float promotion for int/int) or float (true division).
            left = A.BinOp(op=tok.value, left=left, right=right, pos=tok.pos)
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
            atom = A.IntLit(value=t.value, pos=t.pos)
        elif t.kind == "FLOAT":
            self._eat()
            atom = A.FloatLit(value=t.value, pos=t.pos)
        elif t.kind == "STRING":
            self._eat()
            atom = A.StrLit(value=t.value, pos=t.pos)
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
                atom = A.Call(func=t.value, args=args, pos=t.pos)
            else:
                atom = A.Name(name=t.value, pos=t.pos)
        else:
            raise ParseError(f"unexpected token {t.kind} {t.value!r}", t.pos)

        # Trailing subscripts and method calls chain off any primary.
        return self._parse_trailers(atom)

    def _parse_trailers(self, atom):
        while True:
            if self._check("OP", "["):
                lbr = self._eat()
                idx = self._parse_expr()
                self._expect("OP", "]")
                atom = A.Subscript(obj=atom, index=idx, pos=lbr.pos)
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
                    atom = A.MethodCall(obj=atom, method=name, args=args, pos=dot.pos)
                else:
                    # obj.name — attribute access (e.g. math.pi)
                    atom = A.Attr(obj=atom, name=name, pos=dot.pos)
            else:
                return atom

    def _parse_fstring(self) -> A.FString:
        tok = self._eat()
        segments: list = []
        for kind, text in tok.value:
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
