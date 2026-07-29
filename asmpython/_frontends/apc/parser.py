"""APC parser: tokens -> :mod:`ast_nodes`.

Recursive descent for declarations and statements, precedence climbing for
expressions. Newlines separate statements; the lexer already suppressed the
ones inside ``(``/``[``, so an expression simply stops when it sees one.
"""

from __future__ import annotations

from . import ast_nodes as A
from .errors import APCError
from .lexer import Token, tokenize

# Binary operator precedence, loosest first. `is` is equality.
_PRECEDENCE: dict[str, int] = {
    "is": 1, "==": 1, "!=": 1,
    "<": 2, "<=": 2, ">": 2, ">=": 2,
    "|": 3,
    "^": 4,
    "&": 5,
    "<<": 6, ">>": 6,
    "+": 7, "-": 7,
    "*": 8, "/": 8, "%": 8,
}

_ASSIGN_OPS = ("=",)


class Parser:
    def __init__(self, src: str) -> None:
        self.src = src
        self.toks = tokenize(src)
        self.pos = 0

    # ── token helpers ────────────────────────────────────────────────────
    @property
    def cur(self) -> Token:
        return self.toks[self.pos]

    def peek(self, ahead: int = 1) -> Token:
        idx = min(self.pos + ahead, len(self.toks) - 1)
        return self.toks[idx]

    def advance(self) -> Token:
        tok = self.toks[self.pos]
        if tok.kind != "eof":
            self.pos += 1
        return tok

    def error(self, msg: str, tok: Token | None = None) -> APCError:
        t = tok or self.cur
        return APCError(msg, t.line, t.col, self.src)

    def expect_op(self, op: str) -> Token:
        if not self.cur.is_op(op):
            raise self.error(f"expected {op!r}, found {self.cur.value!r}")
        return self.advance()

    def expect_ident(self, what: str = "identifier") -> Token:
        if self.cur.kind != "ident":
            raise self.error(f"expected {what}, found {self.cur.value!r}")
        return self.advance()

    def skip_newlines(self) -> None:
        while self.cur.kind == "nl":
            self.advance()

    def at_eof(self) -> bool:
        return self.cur.kind == "eof"

    # ── entry ────────────────────────────────────────────────────────────
    def parse_module(self) -> A.Module:
        mod = A.Module(1, 1)
        self.skip_newlines()
        while not self.at_eof():
            mod.decls.append(self.parse_declaration())
            self.skip_newlines()
        return mod

    # ── declarations ─────────────────────────────────────────────────────
    def parse_declaration(self):
        tok = self.cur
        if tok.is_kw("import"):
            return self.parse_import()
        if tok.is_kw("export"):
            return self.parse_export()
        if tok.is_kw("extern") or tok.is_kw("func"):
            return self.parse_func()
        if tok.is_kw("layout"):
            return self.parse_layout()
        if tok.is_kw("enum"):
            return self.parse_enum()
        if tok.is_kw("type"):
            return self.parse_type_decl()
        if tok.is_kw("name"):
            return self.parse_name_decl()
        if tok.is_kw("const") or tok.is_kw("let"):
            return self.parse_var_decl()
        raise self.error(f"unexpected {tok.value!r} at top level")

    def parse_import(self) -> A.ImportDecl:
        tok = self.advance()                     # `import`
        node = A.ImportDecl(tok.line, tok.col)
        parts = [self.expect_ident("module name").value]
        while self.cur.is_op("::"):
            self.advance()
            if self.cur.is_op("("):              # import std::(a, b, c)
                self.advance()
                while not self.cur.is_op(")"):
                    node.names.append(self.expect_ident().value)
                    if self.cur.is_op(","):
                        self.advance()
                self.expect_op(")")
                break
            parts.append(self.expect_ident().value)
        node.module = "::".join(parts)
        return node

    def parse_export(self):
        """``export f`` / ``export a::b::f`` / ``export a::b::(f, g)``

        Also ``export(Source) name``, which renames on the way out -- the
        parens hold where it comes from, matching `const(T) x`.
        """
        tok = self.advance()                     # `export`
        node = A.ExportDecl(tok.line, tok.col)

        if self.cur.is_op("("):                  # export(Crc32::of) crc32_compute
            self.advance()
            source = self._qualified_name()
            self.expect_op(")")
            alias = self.expect_ident("exported name").value
            node.names.append(source)
            setattr(node, "alias", alias)
            return node

        parts = [self.expect_ident("exported name").value]
        while self.cur.is_op("::"):
            self.advance()
            if self.cur.is_op("("):              # a::b::(f, g)
                self.advance()
                prefix = "::".join(parts)
                while not self.cur.is_op(")"):
                    node.names.append(
                        f"{prefix}::{self.expect_ident('exported name').value}")
                    if self.cur.is_op(","):
                        self.advance()
                self.expect_op(")")
                return node
            parts.append(self.expect_ident("exported name").value)
        node.names.append("::".join(parts))
        return node

    def _qualified_name(self) -> str:
        parts = [self.expect_ident("name").value]
        while self.cur.is_op("::"):
            self.advance()
            parts.append(self.expect_ident("name").value)
        return "::".join(parts)

    def parse_func(self) -> A.FuncDecl:
        tok = self.cur
        node = A.FuncDecl(tok.line, tok.col)
        if self.cur.is_kw("extern"):
            self.advance()
            node.is_extern = True
        if not self.cur.is_kw("func"):
            raise self.error("expected 'func'")
        self.advance()
        if self.cur.is_kw("plain"):
            self.advance()
            node.is_plain = True
        node.name = self.expect_ident("function name").value

        self.expect_op("(")
        while not self.cur.is_op(")"):
            node.params.append(self.parse_param())
            if self.cur.is_op(","):
                self.advance()
        self.expect_op(")")

        if node.is_extern:
            # No body: the return type follows the parameter list directly.
            if self.cur.is_op(":"):
                self.advance()
                node.ret_type = self.parse_type_name()
            return node

        node.body = self.parse_block()
        # Trailing return type: `}: int`
        if self.cur.is_op(":"):
            self.advance()
            node.ret_type = self.parse_type_name()
        return node

    def parse_param(self) -> A.Param:
        tok = self.cur
        name = self.expect_ident("parameter name").value
        p = A.Param(tok.line, tok.col)
        p.name = name
        if self.cur.is_op(":"):
            self.advance()
            p.type_name = self.parse_type_name()
        if self.cur.is_op("="):
            self.advance()
            p.default = self.parse_expr()
        return p

    def parse_type_name(self) -> str:
        """A type: ``i64``, ``Header``, ``bytes[4]``, ``framebuf::FrameBuffer``."""
        tok = self.expect_ident("type name")
        name = tok.value
        while self.cur.is_op("::"):
            self.advance()
            name = f"{name}::{self.expect_ident('type name').value}"
        if self.cur.is_op("["):
            self.advance()
            inner: list[str] = []
            while not self.cur.is_op("]"):
                inner.append(self.advance().value)
            self.expect_op("]")
            return f"{name}[{''.join(inner)}]"
        return name

    def parse_layout(self) -> A.LayoutDecl:
        tok = self.advance()                     # `layout`
        node = A.LayoutDecl(tok.line, tok.col)
        node.name = self.expect_ident("layout name").value
        self.expect_op("{")
        self.skip_newlines()
        while not self.cur.is_op("}"):
            ftok = self.cur
            fname = self.expect_ident("field name").value
            self.expect_op(":")
            type_name = self.parse_type_name()
            size = self._layout_field_size(type_name, ftok)
            field = A.LayoutField(ftok.line, ftok.col)
            field.name = fname
            field.size = size
            if self.cur.is_op("="):
                self.advance()
                off = self.parse_expr()
                if not isinstance(off, A.Literal) or off.kind != "int":
                    raise self.error("layout offset must be an integer literal", ftok)
                field.offset = int(off.value)
            node.fields.append(field)
            self.skip_newlines()
        self.expect_op("}")
        return node

    def _layout_field_size(self, type_name: str, tok: Token) -> int:
        from .types import layout_field_size

        size = layout_field_size(type_name)
        if size is None:
            raise self.error(
                f"layout field needs a byte size, got {type_name!r} "
                "(use bytes[N] or a fixed-width scalar)",
                tok,
            )
        return size

    def parse_enum(self) -> A.EnumDecl:
        tok = self.advance()                     # `enum`
        node = A.EnumDecl(tok.line, tok.col)
        node.name = self.expect_ident("enum name").value
        if self.cur.is_op("["):                  # enum Status[u8]
            self.advance()
            node.repr_type = self.expect_ident("representation type").value
            self.expect_op("]")
        self.expect_op("{")
        self.skip_newlines()
        while not self.cur.is_op("}"):
            mtok = self.cur
            member = A.EnumMember(mtok.line, mtok.col)
            member.name = self.expect_ident("enum member").value
            if self.cur.is_op("="):
                self.advance()
                member.value = self.parse_expr()
            node.members.append(member)
            if self.cur.is_op(","):
                self.advance()
            self.skip_newlines()
        self.expect_op("}")
        return node

    def parse_name_decl(self) -> A.NameDecl:
        """``name lllib::bits`` / ``name lllib::bits(Parent) { ... }``"""
        tok = self.advance()                     # `name`
        node = A.NameDecl(tok.line, tok.col)
        parts = [self.expect_ident("namespace name").value]
        while self.cur.is_op("::"):
            self.advance()
            parts.append(self.expect_ident("namespace name").value)
        node.name = "::".join(parts)
        if self.cur.is_op("("):
            self.advance()
            node.parent = self.expect_ident("parent namespace").value
            self.expect_op(")")
        if self.cur.is_op("{"):
            node.body = []
            self.advance()
            self.skip_newlines()
            while not self.cur.is_op("}"):
                if self.at_eof():
                    raise self.error(f"unterminated 'name {node.name}'")
                node.body.append(self.parse_declaration())
                self.skip_newlines()
            self.expect_op("}")
        # No block: `body` stays None and the emitter applies the namespace to
        # every declaration after this one in the file.
        return node

    def parse_type_decl(self) -> A.TypeDecl:
        """``type X(Parent) { func constructor(...) {...}: none  ... }``"""
        tok = self.advance()                     # `type`
        node = A.TypeDecl(tok.line, tok.col)
        node.name = self.expect_ident("type name").value
        if self.cur.is_op("("):
            self.advance()
            node.parent = self.expect_ident("parent type").value
            self.expect_op(")")
        self.expect_op("{")
        self.skip_newlines()
        while not self.cur.is_op("}"):
            if self.at_eof():
                raise self.error(f"unterminated 'type {node.name}'")
            if not (self.cur.is_kw("func") or self.cur.is_kw("extern")):
                raise self.error(
                    f"only 'func' declarations belong in a type body, "
                    f"found {self.cur.value!r}")
            node.methods.append(self.parse_func())
            self.skip_newlines()
        self.expect_op("}")
        return node

    # ── statements ───────────────────────────────────────────────────────
    def parse_block(self) -> list:
        self.expect_op("{")
        body: list = []
        self.skip_newlines()
        while not self.cur.is_op("}"):
            if self.at_eof():
                raise self.error("unterminated block")
            body.append(self.parse_statement())
            self.skip_newlines()
        self.expect_op("}")
        return body

    def parse_statement(self):
        tok = self.cur
        if tok.is_kw("pub"):
            return self.parse_field_decl()
        if tok.is_kw("const") or tok.is_kw("let"):
            if self.peek().is_kw("Parent") and self.peek(2).is_op("."):
                return self.parse_field_decl()
            return self.parse_var_decl()
        if tok.is_kw("if"):
            return self.parse_if()
        if tok.is_kw("while"):
            return self.parse_while()
        if tok.is_kw("for"):
            return self.parse_for()
        if tok.is_kw("ret"):
            self.advance()
            node = A.Return(tok.line, tok.col)
            if self.cur.kind != "nl" and not self.cur.is_op("}"):
                node.value = self.parse_expr()
            return node
        if tok.is_kw("break"):
            self.advance()
            return A.Break(tok.line, tok.col)
        if tok.is_kw("continue"):
            self.advance()
            return A.Continue(tok.line, tok.col)

        expr = self.parse_expr()
        if self.cur.is_op(*_ASSIGN_OPS):
            self.advance()
            node = A.Assign(tok.line, tok.col)
            node.target = expr
            node.value = self.parse_expr()
            return node
        stmt = A.ExprStmt(tok.line, tok.col)
        stmt.expr = expr
        return stmt

    def parse_field_decl(self) -> A.FieldDecl:
        """``pub const Parent.Red: int = red`` inside a constructor."""
        tok = self.cur
        node = A.FieldDecl(tok.line, tok.col)
        if self.cur.is_kw("pub"):
            self.advance()
            node.public = True
        if not (self.cur.is_kw("const") or self.cur.is_kw("let")):
            raise self.error("expected 'const' or 'let' after 'pub'")
        node.mutable = self.advance().value == "let"
        holder = self.expect_ident("'Parent'").value
        if holder not in ("Parent", "Self"):
            raise self.error(
                f"a field is declared on 'Parent', not {holder!r}", tok)
        self.expect_op(".")
        node.name = self.expect_ident("field name").value
        if self.cur.is_op(":"):
            self.advance()
            node.type_name = self.parse_type_name()
        if self.cur.is_op("="):
            self.advance()
            node.value = self.parse_expr()
        return node

    def parse_var_decl(self) -> A.VarDecl:
        tok = self.advance()                     # `const` | `let`
        node = A.VarDecl(tok.line, tok.col)
        node.mutable = tok.value == "let"
        node.name = self.expect_ident("variable name").value
        if self.cur.is_op(":"):
            self.advance()
            node.type_name = self.parse_type_name()
        if self.cur.is_op("="):
            self.advance()
            node.value = self.parse_expr()
        return node

    def parse_if(self) -> A.If:
        tok = self.advance()                     # `if`
        node = A.If(tok.line, tok.col)
        self.expect_op("(")
        node.cond = self.parse_expr()
        self.expect_op(")")
        node.then_body = self.parse_block()
        # `else` may sit on the next line
        save = self.pos
        self.skip_newlines()
        if self.cur.is_kw("else"):
            self.advance()
            if self.cur.is_kw("if"):
                node.else_body = [self.parse_if()]
            else:
                node.else_body = self.parse_block()
        else:
            self.pos = save
        return node

    def parse_while(self) -> A.While:
        tok = self.advance()
        node = A.While(tok.line, tok.col)
        self.expect_op("(")
        node.cond = self.parse_expr()
        self.expect_op(")")
        node.body = self.parse_block()
        return node

    def parse_for(self) -> A.For:
        tok = self.advance()                     # `for`
        node = A.For(tok.line, tok.col)
        self.expect_op("(")
        node.var = self.expect_ident("loop variable").value
        self.expect_op("=")
        node.start = self.parse_expr()
        self.expect_op("..")
        node.end = self.parse_expr()
        self.expect_op(")")
        node.body = self.parse_block()
        return node

    # ── expressions ──────────────────────────────────────────────────────
    def parse_expr(self, min_prec: int = 0):
        left = self.parse_cast()
        while True:
            tok = self.cur
            op = tok.value
            if tok.kind == "ident" and op == "is":
                pass
            elif tok.kind != "op" or op not in _PRECEDENCE:
                break
            prec = _PRECEDENCE.get(op)
            if prec is None or prec < min_prec:
                break
            self.advance()
            right = self.parse_expr(prec + 1)
            node = A.Binary(tok.line, tok.col)
            node.op = op
            node.lhs = left
            node.rhs = right
            left = node
        return left

    def parse_cast(self):
        """``as`` sits above unary, so ``&b as i64`` is ``(&b) as i64``.

        Handling it as a postfix of the *operand* instead would bind it to
        ``b``, silently taking the address of a cast rather than casting an
        address.
        """
        expr = self.parse_unary()
        while self.cur.is_kw("as"):
            tok = self.advance()
            cast = A.Cast(tok.line, tok.col)
            cast.expr = expr
            cast.type_name = self.parse_type_name()
            expr = cast
        return expr

    def parse_unary(self):
        tok = self.cur
        if tok.is_op("-", "~", "!", "&", "*"):
            self.advance()
            node = A.Unary(tok.line, tok.col)
            node.op = tok.value
            node.operand = self.parse_unary()
            return node
        return self.parse_postfix()

    def parse_postfix(self):
        expr = self.parse_primary()
        while True:
            tok = self.cur
            if tok.is_op("("):
                self.advance()
                call = A.Call(tok.line, tok.col)
                call.callee = expr
                while not self.cur.is_op(")"):
                    # Named argument: `strict=false`
                    if (self.cur.kind == "ident" and self.peek().is_op("=")
                            and not self.peek(2).is_op("=")):
                        key = self.advance().value
                        self.advance()
                        call.args.append((key, self.parse_expr()))
                    else:
                        call.args.append(self.parse_expr())
                    if self.cur.is_op(","):
                        self.advance()
                self.expect_op(")")
                expr = call
                continue
            if tok.is_op("["):
                self.advance()
                idx = A.Index(tok.line, tok.col)
                idx.obj = expr
                idx.index = self.parse_expr()
                self.expect_op("]")
                expr = idx
                continue
            if tok.is_op("."):
                self.advance()
                mem = A.Member(tok.line, tok.col)
                mem.obj = expr
                mem.field = self.expect_ident("field name").value
                expr = mem
                continue
            if tok.is_op("::"):
                self.advance()
                member = self.expect_ident("member name").value
                if isinstance(expr, A.Name):
                    ns = A.NsAccess(tok.line, tok.col)
                    ns.namespace = expr.ident
                    ns.member = member
                    expr = ns
                elif isinstance(expr, A.NsAccess):
                    # `lllib::bits::popcount`: absorb the previous member into
                    # the namespace rather than treating it as a field access.
                    # A namespace has arbitrary depth; only the final component
                    # is ever the member.
                    deeper = A.NsAccess(tok.line, tok.col)
                    deeper.namespace = f"{expr.namespace}::{expr.member}"
                    deeper.member = member
                    expr = deeper
                else:
                    mem = A.Member(tok.line, tok.col)
                    mem.obj = expr
                    mem.field = member
                    expr = mem
                continue
            break
        return expr

    def parse_primary(self):
        tok = self.cur
        if tok.kind == "int":
            self.advance()
            node = A.Literal(tok.line, tok.col)
            node.value = int(tok.value, 0)
            node.kind = "int"
            return node
        if tok.kind == "float":
            self.advance()
            node = A.Literal(tok.line, tok.col)
            node.value = float(tok.value)
            node.kind = "float"
            return node
        if tok.kind == "str":
            self.advance()
            node = A.Literal(tok.line, tok.col)
            node.value = tok.value
            node.kind = "str"
            return node
        if tok.is_kw("true", "false"):
            self.advance()
            node = A.Literal(tok.line, tok.col)
            node.value = tok.value == "true"
            node.kind = "bool"
            return node
        if tok.is_kw("none", "null"):
            self.advance()
            node = A.Literal(tok.line, tok.col)
            node.value = None
            node.kind = "none"
            return node
        if tok.is_kw("sizeof"):
            self.advance()
            self.expect_op("(")
            node = A.SizeOf(tok.line, tok.col)
            node.type_name = self.parse_type_name()
            self.expect_op(")")
            return node
        if tok.is_op("("):
            self.advance()
            inner = self.parse_expr()
            self.expect_op(")")
            return inner
        if tok.kind == "ident":
            self.advance()
            node = A.Name(tok.line, tok.col)
            node.ident = tok.value
            return node
        raise self.error(f"unexpected {tok.value!r} in expression")


def parse(src: str) -> A.Module:
    return Parser(src).parse_module()


__all__ = ["Parser", "parse"]
