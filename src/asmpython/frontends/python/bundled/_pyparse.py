"""Parse Python tokens into the tree `_pyast` describes.

Recursive descent, one method per precedence level, because Python's grammar
is written that way and a table-driven parser would put the precedence
somewhere a reader has to reconstruct.

WHAT THIS OWES CPYTHON is agreement about which programs are ILL-FORMED --
`compile()` exists in this runtime to answer that question. So the refusals
are written out at the point they apply, and every one of them names the
spelling it refuses.

WHAT IT DOES NOT OWE: the same error MESSAGE. A program catches `SyntaxError`
and prints the type, which is what the cases check; matching CPython's wording
exactly would be a second, much larger promise.

WHERE THE OTHER HALF LIVES: `break` outside a loop, `return` outside a
function and the `nonlocal`/`global` rules are not parse errors at all --
CPython accepts them into a tree and rejects them in the compiler. They are in
`_pyvalidate`, which runs over the finished tree.
"""

from _pylex import (END, INDENT, DEDENT, KEYWORDS, NAME, NEWLINE, NUMBER, OP,
                    STRING, LexError, tokenize)
from _pyast import Arg, Arguments, Node


class ParseError(Exception):
    """Source that tokenises but does not parse."""

    def __init__(self, message, line, col):
        # `super()`, not `Exception.__init__(self, ...)` -- see the same
        # note on `_pylex.LexError`.
        super().__init__(message)
        self.msg = message
        self.line = line
        self.col = col


class IndentParseError(ParseError):
    """A parse error that is about INDENTATION.

    Its own class because a program catches it by name: `IndentationError` is
    a subclass of `SyntaxError` in Python, and `except IndentationError:`
    around a `compile()` is exactly what a program writes. One class for both
    would leave that clause silent for the one input it exists for.
    """


#: Binary operators by precedence, LOOSEST FIRST. Each entry is the level's
#: operators; the parser walks this list and each level parses the one below
#: it, which is the whole of the precedence table.
_BINARY_LEVELS = (
    ("|",),
    ("^",),
    ("&",),
    ("<<", ">>"),
    ("+", "-"),
    ("*", "@", "/", "//", "%"),
)

_COMPARISONS = ("<", ">", "==", ">=", "<=", "!=")

_AUGMENTED = ("+=", "-=", "*=", "@=", "/=", "%=", "&=", "|=", "^=", "<<=",
              ">>=", "**=", "//=")

#: What may be assigned TO. Everything else is `cannot assign to X`, and the
#: name in the message is this table's value.
_ASSIGNABLE = frozenset({"Name", "Attribute", "Subscript", "Tuple", "List",
                         "Starred"})

#: What a bad assignment target is CALLED, for the message. Written out
#: because CPython names the FORM rather than the node -- `cannot assign to
#: function call`, not `to Call`.
_TARGET_NAMES = {
    "Call": "function call", "BinOp": "expression", "UnaryOp": "expression",
    "BoolOp": "expression", "Compare": "comparison", "Constant": "literal",
    "Dict": "dict literal", "Set": "set display", "Lambda": "lambda",
    "IfExp": "conditional expression", "JoinedStr": "f-string expression",
    "ListComp": "list comprehension", "SetComp": "set comprehension",
    "DictComp": "dict comprehension", "GeneratorExp": "generator expression",
    "Await": "await expression", "Yield": "yield expression",
    "NamedExpr": "named expression", "Slice": "subscript",
}


class Parser:
    def __init__(self, tokens, mode="exec"):
        self.toks = tokens
        self.i = 0
        self.mode = mode
        #: Whether a `case` pattern is being read. Inside one, a display's
        #: elements may carry their own `as` capture -- `case {"k": [a] as
        #: inner}` binds INSIDE the mapping -- and nowhere else may they.
        self.in_pattern = False

    # -- token access ----------------------------------------------------
    def peek(self, ahead=0):
        at = self.i + ahead
        return self.toks[at] if at < len(self.toks) else self.toks[-1]

    def at(self, kind, value=None):
        t = self.peek()
        return t.kind == kind and (value is None or t.value == value)

    def at_keyword(self, word):
        t = self.peek()
        return t.kind == NAME and t.value == word

    def next(self):
        t = self.peek()
        if self.i < len(self.toks) - 1:
            self.i = self.i + 1
        return t

    def accept(self, kind, value=None):
        if self.at(kind, value):
            return self.next()
        return None

    def expect(self, kind, value=None):
        if self.at(kind, value):
            return self.next()
        self.fail("invalid syntax")

    def expect_keyword(self, word):
        if self.at_keyword(word):
            return self.next()
        self.fail("invalid syntax")

    def fail(self, message, tok=None):
        t = tok if tok is not None else self.peek()
        raise ParseError(message, t.line, t.col)

    def fail_indent(self, message, tok=None):
        """The same, for a problem that is about indentation."""
        t = tok if tok is not None else self.peek()
        raise IndentParseError(message, t.line, t.col)

    def node(self, kind, tok, **fields):
        return Node(kind, tok.line, tok.col, **fields)

    # -- entry points ----------------------------------------------------
    def parse_module(self):
        body = []
        while not self.at(END):
            if self.at(INDENT):
                # AN INDENT WHERE NO BLOCK WAS OPENED. A line further in
                # than the one above it, with no `:` to open a suite, is
                # an INDENTATION error and a program catches it as one --
                # swallowing the token made it a statement of its own and
                # the line silently joined the block above.
                self.fail_indent("unexpected indent")
            if self.accept(NEWLINE) or self.accept(DEDENT):
                continue
            body.extend(self.statement())
        return Node("Module", 1, 0, body=body)

    def parse_expression(self):
        """`eval` mode: ONE expression and nothing after it.

        A statement here is the error `eval("x = 1")` reports, and it is
        reported as a SyntaxError rather than run -- which is the whole
        difference between `eval` and `exec`.
        """
        while self.accept(NEWLINE):
            pass
        value = self.expressions()
        while self.accept(NEWLINE) or self.accept(DEDENT):
            pass
        if not self.at(END):
            self.fail("invalid syntax")
        return Node("Expression", 1, 0, body=value)

    # -- suites ----------------------------------------------------------
    def block(self):
        """What follows a `:` -- either the rest of this line, or an
        indented suite under it."""
        if self.accept(NEWLINE):
            if not self.at(INDENT):
                # AN INDENTATION ERROR, which a program catches by that name.
                self.fail_indent("expected an indented block")
            self.next()
            body = []
            while not self.at(DEDENT) and not self.at(END):
                if self.accept(NEWLINE):
                    continue
                if self.at(INDENT):
                    # A LINE FURTHER IN THAN THE BLOCK IT IS IN, with nothing
                    # opening a suite. `if True:` then two lines at different
                    # depths is an INDENTATION error, and a program catches it
                    # as one -- reaching `statement()` with an INDENT in hand
                    # reported "invalid syntax", which is true and is not what
                    # `except IndentationError:` is written to catch.
                    self.fail_indent("unexpected indent")
                body.extend(self.statement())
            self.accept(DEDENT)
            if not body:
                self.fail_indent("expected an indented block")
            return body
        # `if x: y = 1` -- a simple statement list on the same line.
        body = self.simple_line()
        return body

    def simple_line(self):
        """One or more simple statements separated by `;`, then a NEWLINE."""
        out = self.simple_statement()
        while self.accept(OP, ";"):
            if self.at(NEWLINE) or self.at(END):
                break
            out.extend(self.simple_statement())
        if not self.accept(NEWLINE) and not self.at(END) \
                and not self.at(DEDENT):
            self.fail("invalid syntax")
        return out

    # -- statements ------------------------------------------------------
    def statement(self):
        t = self.peek()
        if t.kind == OP and t.value == "@":
            return [self.decorated()]
        if t.kind == NAME:
            word = t.value
            if word == "if":
                return [self.if_statement()]
            if word == "while":
                return [self.while_statement()]
            if word == "for":
                return [self.for_statement(False)]
            if word == "try":
                return [self.try_statement()]
            if word == "with":
                return [self.with_statement(False)]
            if word == "def":
                return [self.function(False, [])]
            if word == "class":
                return [self.class_statement([])]
            if word == "async":
                return [self.async_statement()]
            # SOFT KEYWORDS. `match` and `type` are ordinary names unless
            # they START one of their statements, which is decided by what
            # follows -- `match = 1` and `type(x)` stay assignments and calls.
            if word == "match" and self.looks_like_match():
                return [self.match_statement()]
            if word == "type" and self.looks_like_type_alias():
                return [self.type_alias()]
        return self.simple_line()

    def looks_like_match(self):
        """`match X:` starts a match statement; `match(x)` is a CALL.

        WHAT DECIDES IT IS THE END OF THE LINE. A match statement is `match`,
        a subject, and a colon -- so the logical line ends with one, and a
        call or an assignment does not. Listing the tokens that may follow
        `match` cannot work: `match(x):` IS a match statement whose subject is
        parenthesised, and `match(x)` is a call, and they differ only in what
        comes at the end.

        Scanned at BRACKET DEPTH ZERO, so a colon inside `match d["k"]:` --
        or inside a lambda or a slice -- is not mistaken for the one that ends
        the header.
        """
        if self.peek(1).kind in (NEWLINE, END):
            return False
        if self.peek(1).kind == OP and self.peek(1).value in ("=", ".", ",",
                                                              ")", "]", "==",
                                                              ";"):
            return False
        depth, i, last = 0, 1, None
        while True:
            token = self.peek(i)
            if token.kind in (NEWLINE, END):
                break
            if token.kind == OP:
                if token.value in ("(", "[", "{"):
                    depth = depth + 1
                elif token.value in (")", "]", "}"):
                    depth = depth - 1
            last = token
            i = i + 1
        return (last is not None and last.kind == OP and last.value == ":"
                and depth == 0)

    def looks_like_type_alias(self):
        """`type X = ...` and `type X[T] = ...` start an alias."""
        return (self.peek(1).kind == NAME
                and self.peek(2).kind == OP
                and self.peek(2).value in ("=", "["))

    def simple_statement(self):
        t = self.peek()
        if t.kind == NAME:
            word = t.value
            if word == "pass":
                self.next()
                return [self.node("Pass", t)]
            if word == "break":
                self.next()
                return [self.node("Break", t)]
            if word == "continue":
                self.next()
                return [self.node("Continue", t)]
            if word == "return":
                self.next()
                value = None
                if not self.ends_statement():
                    value = self.expressions()
                return [self.node("Return", t, value=value)]
            if word == "raise":
                return [self.raise_statement()]
            if word == "import":
                return [self.import_statement()]
            if word == "from":
                return [self.from_import()]
            if word in ("global", "nonlocal"):
                self.next()
                names = [self.expect(NAME).value]
                while self.accept(OP, ","):
                    names.append(self.expect(NAME).value)
                return [self.node("Global" if word == "global" else "Nonlocal",
                                  t, names=names)]
            if word == "del":
                self.next()
                targets = self.target_list()
                for one in targets:
                    self.check_target(one, "delete")
                return [self.node("Delete", t, targets=targets)]
            if word == "assert":
                self.next()
                test = self.expression()
                message = self.expression() if self.accept(OP, ",") else None
                return [self.node("Assert", t, test=test, msg=message)]
        return [self.expression_statement()]

    def ends_statement(self):
        return (self.at(NEWLINE) or self.at(END) or self.at(DEDENT)
                or self.at(OP, ";"))

    def expression_statement(self):
        t = self.peek()
        first = self.expressions(allow_star=True)
        if self.at(OP, ":") and not self.at(OP, ":="):
            # AN ANNOTATION. `x: int = 1` and `x: int` both land here, and
            # only a single target may carry one.
            self.next()
            annotation = self.expression()
            value = self.expression() if self.accept(OP, "=") else None
            self.check_target(first, "annotate")
            return self.node("AnnAssign", t, target=first,
                             annotation=annotation, value=value,
                             simple=1 if first.kind == "Name" else 0)
        for op in _AUGMENTED:
            if self.at(OP, op):
                self.next()
                value = self.expressions()
                self.check_target(first, "augmented assign")
                return self.node("AugAssign", t, target=first, op=op[:-1],
                                 value=value)
        if self.at(OP, "="):
            targets = [first]
            value = None
            while self.accept(OP, "="):
                got = self.expressions(allow_star=True)
                targets.append(got)
            value = targets[-1]
            del targets[-1]
            for one in targets:
                self.check_target(one, "assign")
            return self.node("Assign", t, targets=targets, value=value)
        if first.kind == "NamedExpr" and not first.get("parenthesised"):
            # A BARE `x := 1` is a SyntaxError and `(x := 1)` is not: the
            # parentheses are what make it an expression rather than a
            # statement, and by this point the tree cannot tell them apart --
            # so `atom` records which it was.
            raise ParseError(":= cannot be used as a statement; use = instead",
                             first.lineno, first.col_offset)
        return self.node("Expr", t, value=first)

    def check_target(self, node, what):
        """Refuse anything that cannot be assigned to.

        THE ONE CHECK THE PARSER OWES that is not about token order: `f() = 1`
        parses cleanly as an assignment and is a SyntaxError, and a parser
        that let it through would make `compile()` answer `accepted`.
        """
        if node.kind in ("Tuple", "List"):
            for item in node.get("elts", []):
                self.check_target(item, what)
            return
        if node.kind == "Starred":
            self.check_target(node.value, what)
            return
        if node.kind in _ASSIGNABLE:
            return
        named = _TARGET_NAMES.get(node.kind, node.kind)
        if node.kind == "Constant":
            value = node.get("value")
            if value is None or value is True or value is False:
                named = repr(value)
        verb = "delete" if what == "delete" else "assign to"
        raise ParseError("cannot " + verb + " " + named,
                         node.lineno, node.col_offset)

    def target_list(self):
        out = [self.expression()]
        while self.accept(OP, ","):
            if self.ends_statement():
                break
            out.append(self.expression())
        return out

    def raise_statement(self):
        t = self.next()
        exc = None
        cause = None
        if not self.ends_statement():
            exc = self.expression()
            if self.at_keyword("from"):
                self.next()
                cause = self.expression()
        return self.node("Raise", t, exc=exc, cause=cause)

    def dotted_name(self):
        out = self.expect(NAME).value
        while self.accept(OP, "."):
            out = out + "." + self.expect(NAME).value
        return out

    def import_statement(self):
        t = self.next()
        names = []
        while True:
            name = self.dotted_name()
            alias = self.expect(NAME).value if self.accept_soft("as") else None
            names.append(Node("alias", t.line, t.col, name=name, asname=alias))
            if not self.accept(OP, ","):
                break
        return self.node("Import", t, names=names)

    def accept_soft(self, word):
        if self.at_keyword(word):
            self.next()
            return True
        return False

    def from_import(self):
        t = self.next()
        level = 0
        while self.at(OP, ".") or self.at(OP, "..."):
            level = level + (3 if self.peek().value == "..." else 1)
            self.next()
        module = None
        if not self.at_keyword("import"):
            module = self.dotted_name()
        self.expect_keyword("import")
        names = []
        if self.accept(OP, "*"):
            names.append(Node("alias", t.line, t.col, name="*", asname=None))
        else:
            closed = self.accept(OP, "(") is not None
            while True:
                name = self.expect(NAME).value
                alias = self.expect(NAME).value if self.accept_soft("as") \
                    else None
                names.append(Node("alias", t.line, t.col, name=name,
                                  asname=alias))
                if not self.accept(OP, ","):
                    break
                if closed and self.at(OP, ")"):
                    break
            if closed:
                self.expect(OP, ")")
        return self.node("ImportFrom", t, module=module, names=names,
                         level=level)

    # -- compound statements ---------------------------------------------
    def if_statement(self):
        t = self.next()
        test = self.named_expression()
        self.expect(OP, ":")
        body = self.block()
        orelse = []
        if self.at_keyword("elif"):
            orelse = [self.if_statement_from_elif()]
        elif self.at_keyword("else"):
            self.next()
            self.expect(OP, ":")
            orelse = self.block()
        return self.node("If", t, test=test, body=body, orelse=orelse)

    def if_statement_from_elif(self):
        # `elif` IS AN `if` NESTED IN THE `else`, which is what CPython's tree
        # says and what makes one walk handle both.
        return self.if_statement()

    def while_statement(self):
        t = self.next()
        test = self.named_expression()
        self.expect(OP, ":")
        body = self.block()
        orelse = []
        if self.at_keyword("else"):
            self.next()
            self.expect(OP, ":")
            orelse = self.block()
        return self.node("While", t, test=test, body=body, orelse=orelse)

    def for_statement(self, is_async):
        t = self.next()
        target = self.targets_until_in()
        self.expect_keyword("in")
        iterable = self.expressions()
        self.expect(OP, ":")
        body = self.block()
        orelse = []
        if self.at_keyword("else"):
            self.next()
            self.expect(OP, ":")
            orelse = self.block()
        return self.node("AsyncFor" if is_async else "For", t, target=target,
                         iter=iterable, body=body, orelse=orelse)

    def target_item(self):
        """One assignment target: a name, an attribute, a subscript, a
        parenthesised or bracketed group, or a starred one.

        ITS OWN PRODUCTION, and this is why: a target is parsed where `in`
        follows it, so the full expression grammar reads `x in y` as a
        comparison and swallows the keyword the `for` needs. CPython's grammar
        separates them for the same reason.
        """
        if self.at(OP, "*"):
            t = self.next()
            return self.node("Starred", t, value=self.target_item())
        return self.postfix()

    def targets_until_in(self):
        first = self.target_item()
        if not self.at(OP, ","):
            self.check_target(first, "assign")
            return first
        items = [first]
        while self.accept(OP, ","):
            if self.at_keyword("in"):
                break
            items.append(self.target_item())
        made = Node("Tuple", first.lineno, first.col_offset, elts=items)
        self.check_target(made, "assign")
        return made

    def with_statement(self, is_async):
        t = self.next()
        items = []
        closed = False
        if self.at(OP, "(") and self.with_parens_are_items():
            closed = self.accept(OP, "(") is not None
        while True:
            value = self.expression()
            target = None
            if self.accept_soft("as"):
                target = self.expression()
                self.check_target(target, "assign")
            items.append(Node("withitem", t.line, t.col, context_expr=value,
                              optional_vars=target))
            if not self.accept(OP, ","):
                break
            if closed and self.at(OP, ")"):
                break
        if closed:
            self.expect(OP, ")")
        self.expect(OP, ":")
        body = self.block()
        return self.node("AsyncWith" if is_async else "With", t, items=items,
                         body=body)

    def with_parens_are_items(self):
        """`with (a, b):` is two items; `with (a + b):` is one expression.

        Decided by scanning to the matching `)` and asking whether an `as` or
        a `,` at depth one is inside it -- the parenthesised form is not
        distinguishable any earlier, which is why CPython's own grammar
        backtracks here.
        """
        depth = 0
        at = self.i
        while at < len(self.toks):
            t = self.toks[at]
            if t.kind == OP and t.value in ("(", "[", "{"):
                depth = depth + 1
            elif t.kind == OP and t.value in (")", "]", "}"):
                depth = depth - 1
                if depth == 0:
                    # What follows the group decides: `:` means it was the
                    # whole item list.
                    nxt = self.toks[at + 1] if at + 1 < len(self.toks) else t
                    return nxt.kind == OP and nxt.value == ":"
            elif depth == 1 and t.kind == NAME and t.value == "as":
                return True
            at = at + 1
        return False

    def try_statement(self):
        t = self.next()
        self.expect(OP, ":")
        body = self.block()
        handlers = []
        orelse = []
        final = []
        star = False
        while self.at_keyword("except"):
            h = self.next()
            if self.accept(OP, "*"):
                star = True
            kind = None
            name = None
            if not self.at(OP, ":"):
                kind = self.expression()
                # PEP 758: `except ValueError, TypeError:` needs no
                # parentheses in 3.14 -- but only WITHOUT `as`, because
                # `except A, B as e` would be ambiguous about what `e` binds.
                if self.at(OP, ","):
                    items = [kind]
                    while self.accept(OP, ","):
                        if self.at(OP, ":"):
                            break
                        items.append(self.expression())
                    kind = Node("Tuple", kind.lineno, kind.col_offset,
                                elts=items)
                    if self.at_keyword("as"):
                        self.fail("multiple exception types must be "
                                  "parenthesized when using 'as'")
                if self.accept_soft("as"):
                    # `except E as (a, b)` IS REFUSED: the target of an
                    # `except` is one name and nothing else.
                    if not self.at(NAME):
                        self.fail("invalid syntax")
                    name = self.next().value
            self.expect(OP, ":")
            handlers.append(Node("ExceptHandler", h.line, h.col, type=kind,
                                 name=name, body=self.block()))
        if self.at_keyword("else"):
            self.next()
            self.expect(OP, ":")
            orelse = self.block()
        if self.at_keyword("finally"):
            self.next()
            self.expect(OP, ":")
            final = self.block()
        if not handlers and not final:
            self.fail("expected 'except' or 'finally' block")
        return self.node("TryStar" if star else "Try", t, body=body,
                         handlers=handlers, orelse=orelse, finalbody=final)

    def decorated(self):
        decorators = []
        while self.at(OP, "@"):
            self.next()
            decorators.append(self.named_expression())
            if not self.accept(NEWLINE):
                self.fail("invalid syntax")
        if self.at_keyword("def"):
            return self.function(False, decorators)
        if self.at_keyword("class"):
            return self.class_statement(decorators)
        if self.at_keyword("async"):
            self.next()
            return self.function(True, decorators)
        self.fail("invalid syntax")

    def async_statement(self):
        self.next()
        if self.at_keyword("def"):
            return self.function(True, [])
        if self.at_keyword("for"):
            return self.for_statement(True)
        if self.at_keyword("with"):
            return self.with_statement(True)
        self.fail("invalid syntax")

    def type_params(self):
        """PEP 695's `[T]`, `[*Ts]`, `[**P]`, and defaults."""
        out = []
        if not self.accept(OP, "["):
            return out
        while not self.at(OP, "]"):
            kind = "TypeVar"
            if self.accept(OP, "*"):
                kind = "TypeVarTuple"
            elif self.accept(OP, "**"):
                kind = "ParamSpec"
            t = self.expect(NAME)
            bound = self.expression() if self.accept(OP, ":") else None
            default = self.expression() if self.accept(OP, "=") else None
            out.append(Node(kind, t.line, t.col, name=t.value, bound=bound,
                            default_value=default))
            if not self.accept(OP, ","):
                break
        self.expect(OP, "]")
        return out

    def function(self, is_async, decorators):
        t = self.next()
        name = self.expect(NAME).value
        params = self.type_params()
        self.expect(OP, "(")
        args = self.parameters()
        self.expect(OP, ")")
        returns = self.expression() if self.accept(OP, "->") else None
        self.expect(OP, ":")
        body = self.block()
        return self.node("AsyncFunctionDef" if is_async else "FunctionDef", t,
                         name=name, args=args, body=body,
                         decorator_list=decorators, returns=returns,
                         type_params=params)

    def parameters(self, annotated=True):
        """The five parameter groups, and the rules about their order."""
        args = Arguments()
        seen_star = False
        while not self.at(OP, ")") and not self.at(OP, ":"):
            if self.accept(OP, "/"):
                # EVERYTHING BEFORE `/` IS POSITIONAL-ONLY, retroactively.
                args.posonlyargs = args.args
                args.args = []
                if not self.accept(OP, ","):
                    break
                continue
            if self.at(OP, "*") and self.peek(1).kind == OP \
                    and self.peek(1).value == ",":
                self.next()
                seen_star = True
                self.accept(OP, ",")
                continue
            if self.at(OP, "*"):
                # ONE STAR PER LIST. `def f(*a, *b)` parses as two ordinary
                # `*` groups and there is only one place to put the second --
                # so without this the parameter silently replaced the first
                # and the function took `*b` alone.
                if seen_star:
                    self.fail("* argument may appear only once")
                self.next()
                one = self.one_parameter(annotated)
                args.vararg = one
                seen_star = True
                if not self.accept(OP, ","):
                    break
                continue
            if self.accept(OP, "**"):
                args.kwarg = self.one_parameter(annotated)
                self.accept(OP, ",")
                break
            if self.at(OP, "("):
                # PEP 3113: `def f((a, b)):` was Python 2 and is refused.
                self.fail("Function parameters cannot be parenthesized")
            one = self.one_parameter(annotated)
            default = self.expression() if self.accept(OP, "=") else None
            if seen_star:
                args.kwonlyargs.append(one)
                args.kw_defaults.append(default)
            else:
                args.args.append(one)
                if default is not None:
                    args.defaults.append(default)
                elif args.defaults:
                    # A NON-DEFAULT AFTER A DEFAULT cannot be filled: every
                    # position before it is optional, so nothing could reach
                    # it positionally.
                    self.fail("parameter without a default follows parameter "
                              "with a default")
            if not self.accept(OP, ","):
                break
        return args

    def one_parameter(self, annotated=True):
        t = self.expect(NAME)
        if t.value in KEYWORDS:
            self.fail("invalid syntax", t)
        # A LAMBDA'S `:` ENDS THE PARAMETER LIST, so there is no annotation to
        # read there -- reading one anyway ate `lambda x: x`'s body.
        annotation = None
        if annotated and self.accept(OP, ":"):
            annotation = self.expression()
        return Arg(t.value, annotation, t.line, t.col)

    def class_statement(self, decorators):
        t = self.next()
        name = self.expect(NAME).value
        params = self.type_params()
        bases = []
        keywords = []
        if self.accept(OP, "("):
            while not self.at(OP, ")"):
                if self.accept(OP, "**"):
                    keywords.append(Node("keyword", t.line, t.col, arg=None,
                                         value=self.expression()))
                elif self.at(NAME) and self.peek(1).kind == OP \
                        and self.peek(1).value == "=":
                    key = self.next().value
                    self.next()
                    keywords.append(Node("keyword", t.line, t.col, arg=key,
                                         value=self.expression()))
                elif self.at(OP, "*"):
                    # `class C(*bases)` -- the base list SPREAD, which is what
                    # a class built from a computed tuple of bases is written
                    # as. The same shape a call takes, and refused here for no
                    # reason but that nothing had asked yet.
                    star = self.next()
                    bases.append(Node("Starred", star.line, star.col,
                                      value=self.expression(), ctx="Load"))
                else:
                    bases.append(self.expression())
                if not self.accept(OP, ","):
                    break
            self.expect(OP, ")")
        self.expect(OP, ":")
        body = self.block()
        return self.node("ClassDef", t, name=name, bases=bases,
                         keywords=keywords, body=body,
                         decorator_list=decorators, type_params=params)

    def type_alias(self):
        t = self.next()
        name_tok = self.expect(NAME)
        params = self.type_params()
        self.expect(OP, "=")
        value = self.expression()
        return self.node("TypeAlias", t,
                         name=Node("Name", name_tok.line, name_tok.col,
                                   id=name_tok.value, ctx="Store"),
                         type_params=params, value=value)

    def match_statement(self):
        t = self.next()
        subject = self.expressions()
        self.expect(OP, ":")
        self.expect(NEWLINE)
        self.expect(INDENT)
        cases = []
        while self.at_keyword("case"):
            c = self.next()
            pattern = self.pattern()
            # THE GUARD IS READ HERE, after the pattern: inside a `case`
            # an `if` begins the guard and is not a conditional
            # expression, so reading it as a ternary looked for an
            # `else` that is not coming.
            guard = None
            if self.at_keyword("if"):
                self.next()
                guard = self.named_expression()
            self.expect(OP, ":")
            cases.append(Node("match_case", c.line, c.col, pattern=pattern,
                              guard=guard, body=self.block()))
        if not cases:
            self.fail("expected 'case' block")
        while self.accept(NEWLINE):
            pass
        self.accept(DEDENT)
        return self.node("Match", t, subject=subject, cases=cases)

    def pattern_item(self):
        """One element of a pattern display, which may carry its own `as`.

        `case {"k": [a, b] as inner}` captures INSIDE the mapping, so the
        capture cannot be something only the outermost pattern may have.
        """
        value = self.expression()
        if self.at_keyword("as"):
            self.next()
            name = self.expect(NAME)
            return Node("MatchAs", name.line, name.col, pattern=value,
                        name=name.value)
        return value

    def one_pattern(self):
        """One pattern: an `or_test`, optionally captured with `as`.

        NOT a full expression: inside a `case` an `if` begins the guard and an
        `as` begins a capture, and the expression grammar would take both.
        """
        value = self.or_test()
        if self.at_keyword("as"):
            self.next()
            name = self.expect(NAME)
            return Node("MatchAs", name.line, name.col, pattern=value,
                        name=name.value)
        return value

    def pattern(self):
        """A match pattern, as far as the tree needs it.

        Patterns are parsed as EXPRESSIONS and re-read, because their syntax
        is a subset of expression syntax with different meaning -- and the
        meaning is `_pyvalidate`'s and the lowering's business, not the token
        reader's.
        """
        t = self.peek()
        was = self.in_pattern
        self.in_pattern = True
        items = [self.one_pattern()]
        while self.accept(OP, ","):
            if self.at(OP, ":") or self.at_keyword("if"):
                break
            items.append(self.one_pattern())
        self.in_pattern = was
        if len(items) == 1:
            return Node("pattern", t.line, t.col, value=items[0])
        return Node("pattern", t.line, t.col,
                    value=Node("Tuple", t.line, t.col, elts=items))

    # -- expressions -----------------------------------------------------
    def expressions(self, allow_star=False):
        """One expression, or a TUPLE of several separated by commas."""
        t = self.peek()
        first = self.star_expression() if allow_star else self.expression()
        if not self.at(OP, ","):
            return first
        items = [first]
        while self.accept(OP, ","):
            if self.ends_statement() or self.at(OP, "=") or self.at(OP, ")") \
                    or self.at(OP, "]") or self.at(OP, "}") \
                    or self.at(OP, ":"):
                break
            items.append(self.star_expression() if allow_star
                         else self.expression())
        return Node("Tuple", t.line, t.col, elts=items)

    def element(self):
        """One element of a display. Inside a `case` pattern it may carry an
        `as` capture of its own; everywhere else it is a plain element."""
        value = self.star_expression()
        if self.in_pattern and self.at_keyword("as"):
            self.next()
            name = self.expect(NAME)
            return Node("MatchAs", name.line, name.col, pattern=value,
                        name=name.value)
        return value

    def star_expression(self):
        if self.at(OP, "*"):
            t = self.next()
            return self.node("Starred", t, value=self.expression())
        return self.expression()

    def named_expression(self):
        """`x := v` -- allowed where a plain expression is, and nowhere else.

        The RESTRICTION is that the target is a NAME: `(f() := 1)` is a
        SyntaxError, and that is checked here rather than left to the
        assignment rules, which this does not go through.
        """
        t = self.peek()
        value = self.expression()
        if self.at(OP, ":="):
            self.next()
            if value.kind != "Name":
                raise ParseError("cannot use assignment expressions with "
                                 + _TARGET_NAMES.get(value.kind, value.kind),
                                 value.lineno, value.col_offset)
            return self.node("NamedExpr", t, target=value,
                             value=self.expression())
        return value

    def expression(self):
        if self.at_keyword("lambda"):
            return self.lambda_expression()
        if self.at_keyword("yield"):
            return self.yield_expression()
        value = self.ternary()
        if self.at(OP, ":="):
            t = self.next()
            if value.kind != "Name":
                raise ParseError("cannot use assignment expressions with "
                                 + _TARGET_NAMES.get(value.kind, value.kind),
                                 value.lineno, value.col_offset)
            return self.node("NamedExpr", t, target=value,
                             value=self.expression())
        return value

    def lambda_expression(self):
        t = self.next()
        args = self.parameters(False)
        self.expect(OP, ":")
        return self.node("Lambda", t, args=args, body=self.expression())

    def yield_expression(self):
        t = self.next()
        if self.at_keyword("from"):
            self.next()
            return self.node("YieldFrom", t, value=self.expression())
        if self.ends_statement() or self.at(OP, ")") or self.at(OP, "]") \
                or self.at(OP, "}"):
            return self.node("Yield", t, value=None)
        return self.node("Yield", t, value=self.expressions())

    def ternary(self):
        t = self.peek()
        value = self.or_test()
        if self.at_keyword("if"):
            self.next()
            test = self.or_test()
            self.expect_keyword("else")
            return self.node("IfExp", t, test=test, body=value,
                             orelse=self.expression())
        return value

    def or_test(self):
        t = self.peek()
        value = self.and_test()
        if not self.at_keyword("or"):
            return value
        items = [value]
        while self.accept_soft("or"):
            items.append(self.and_test())
        return Node("BoolOp", t.line, t.col, op="Or", values=items)

    def and_test(self):
        t = self.peek()
        value = self.not_test()
        if not self.at_keyword("and"):
            return value
        items = [value]
        while self.accept_soft("and"):
            items.append(self.not_test())
        return Node("BoolOp", t.line, t.col, op="And", values=items)

    def not_test(self):
        if self.at_keyword("not"):
            t = self.next()
            return self.node("UnaryOp", t, op="Not", operand=self.not_test())
        return self.comparison()

    def comparison(self):
        t = self.peek()
        left = self.binary(0)
        ops = []
        rest = []
        while True:
            if self.peek().kind == OP and self.peek().value in _COMPARISONS:
                ops.append(self.next().value)
            elif self.at_keyword("in"):
                self.next()
                ops.append("in")
            elif self.at_keyword("not") and self.peek(1).kind == NAME \
                    and self.peek(1).value == "in":
                self.next()
                self.next()
                ops.append("not in")
            elif self.at_keyword("is"):
                self.next()
                if self.at_keyword("not"):
                    self.next()
                    ops.append("is not")
                else:
                    ops.append("is")
            else:
                break
            rest.append(self.binary(0))
        if not ops:
            return left
        return Node("Compare", t.line, t.col, left=left, ops=ops,
                    comparators=rest)

    def binary(self, level):
        if level >= len(_BINARY_LEVELS):
            return self.unary()
        t = self.peek()
        left = self.binary(level + 1)
        while self.peek().kind == OP \
                and self.peek().value in _BINARY_LEVELS[level]:
            op = self.next().value
            right = self.binary(level + 1)
            left = Node("BinOp", t.line, t.col, left=left, op=op, right=right)
        return left

    def unary(self):
        t = self.peek()
        if t.kind == OP and t.value in ("-", "+", "~"):
            self.next()
            name = {"-": "USub", "+": "UAdd", "~": "Invert"}[t.value]
            return self.node("UnaryOp", t, op=name, operand=self.unary())
        if self.at_keyword("await"):
            self.next()
            return self.node("Await", t, value=self.unary())
        return self.power()

    def power(self):
        t = self.peek()
        base = self.postfix()
        if self.at(OP, "**"):
            self.next()
            # RIGHT ASSOCIATIVE, and binding tighter on the right than a
            # unary minus on the left: `-2 ** 2` is -4.
            return Node("BinOp", t.line, t.col, left=base, op="**",
                        right=self.unary())
        return base

    def postfix(self):
        value = self.atom()
        while True:
            t = self.peek()
            if self.at(OP, "."):
                self.next()
                name = self.expect(NAME)
                value = Node("Attribute", t.line, t.col, value=value,
                             attr=name.value, ctx="Load")
            elif self.at(OP, "("):
                value = self.call(value)
            elif self.at(OP, "["):
                self.next()
                index = self.subscript()
                self.expect(OP, "]")
                value = Node("Subscript", t.line, t.col, value=value,
                             slice=index, ctx="Load")
            else:
                return value

    def subscript(self):
        t = self.peek()
        items = []
        while not self.at(OP, "]"):
            items.append(self.slice_item())
            if not self.accept(OP, ","):
                break
        if not items:
            self.fail("invalid syntax")
        if len(items) == 1:
            return items[0]
        return Node("Tuple", t.line, t.col, elts=items)

    def slice_item(self):
        t = self.peek()
        lower = None
        if not self.at(OP, ":"):
            lower = self.star_expression()
        if not self.at(OP, ":"):
            return lower
        self.next()
        upper = None
        step = None
        if not self.at(OP, ":") and not self.at(OP, "]") and not self.at(OP,
                                                                        ","):
            upper = self.expression()
        if self.accept(OP, ":"):
            if not self.at(OP, "]") and not self.at(OP, ","):
                step = self.expression()
        return Node("Slice", t.line, t.col, lower=lower, upper=upper,
                    step=step)

    def call(self, func):
        t = self.expect(OP, "(")
        args = []
        keywords = []
        while not self.at(OP, ")"):
            if self.accept(OP, "**"):
                keywords.append(Node("keyword", t.line, t.col, arg=None,
                                     value=self.expression()))
            elif self.at(OP, "*"):
                self.next()
                args.append(self.node("Starred", t, value=self.expression()))
            elif self.at(NAME) and self.peek().value not in KEYWORDS \
                    and self.peek(1).kind == OP and self.peek(1).value == "=":
                key = self.next().value
                self.next()
                keywords.append(Node("keyword", t.line, t.col, arg=key,
                                     value=self.expression()))
            else:
                value = self.named_expression()
                if self.at_keyword("for") or self.at_keyword("async"):
                    value = self.comprehension("GeneratorExp", value, None, t)
                args.append(value)
            if not self.accept(OP, ","):
                break
        self.expect(OP, ")")
        return Node("Call", t.line, t.col, func=func, args=args,
                    keywords=keywords)

    def comprehension(self, kind, element, value, t):
        """The `for` clauses of a comprehension, however many there are."""
        generators = []
        while self.at_keyword("for") or self.at_keyword("async"):
            is_async = False
            if self.at_keyword("async"):
                self.next()
                is_async = True
            self.expect_keyword("for")
            target = self.targets_until_in()
            self.expect_keyword("in")
            iterable = self.or_test()
            ifs = []
            while self.at_keyword("if"):
                self.next()
                ifs.append(self.or_test())
            generators.append(Node("comprehension", t.line, t.col,
                                   target=target, iter=iterable, ifs=ifs,
                                   is_async=1 if is_async else 0))
        if kind == "DictComp":
            return Node(kind, t.line, t.col, key=element, value=value,
                        generators=generators)
        return Node(kind, t.line, t.col, elt=element, generators=generators)

    def atom(self):
        t = self.peek()
        if t.kind == NUMBER:
            self.next()
            return self.node("Constant", t, value=_number_value(t.value),
                             raw=t.value)
        if t.kind == STRING:
            return self.strings()
        if t.kind == NAME:
            if t.value in ("True", "False", "None"):
                self.next()
                value = True if t.value == "True" else \
                    False if t.value == "False" else None
                return self.node("Constant", t, value=value, raw=t.value)
            if t.value in KEYWORDS and t.value not in ("lambda", "yield",
                                                       "await", "not", "None",
                                                       "True", "False"):
                self.fail("invalid syntax")
            self.next()
            return self.node("Name", t, id=t.value, ctx="Load")
        if t.kind == OP and t.value == "(":
            return self.parenthesised()
        if t.kind == OP and t.value == "[":
            return self.list_display()
        if t.kind == OP and t.value == "{":
            return self.brace_display()
        if t.kind == OP and t.value == "...":
            self.next()
            return self.node("Constant", t, value=Ellipsis, raw="...")
        self.fail("invalid syntax")

    def parenthesised(self):
        t = self.next()
        if self.accept(OP, ")"):
            return Node("Tuple", t.line, t.col, elts=[])
        if self.at_keyword("yield"):
            inner = self.yield_expression()
            self.expect(OP, ")")
            return inner
        first = self.star_expression()
        if self.at_keyword("for") or self.at_keyword("async"):
            made = self.comprehension("GeneratorExp", first, None, t)
            self.expect(OP, ")")
            return made
        if self.at(OP, ","):
            items = [first]
            while self.accept(OP, ","):
                if self.at(OP, ")"):
                    break
                items.append(self.star_expression())
            self.expect(OP, ")")
            return Node("Tuple", t.line, t.col, elts=items)
        self.expect(OP, ")")
        # WHICH IT WAS, recorded for one caller: `simple_statement` refuses a
        # bare `x := 1` and must not refuse `(x := 1)`, and by the time it
        # looks, both are the same NamedExpr.
        first.parenthesised = True
        return first

    def list_display(self):
        t = self.next()
        if self.accept(OP, "]"):
            return Node("List", t.line, t.col, elts=[])
        first = self.star_expression()
        if self.at_keyword("for") or self.at_keyword("async"):
            made = self.comprehension("ListComp", first, None, t)
            self.expect(OP, "]")
            return made
        items = [first]
        while self.accept(OP, ","):
            if self.at(OP, "]"):
                break
            items.append(self.element())
        self.expect(OP, "]")
        return Node("List", t.line, t.col, elts=items)

    def brace_display(self):
        t = self.next()
        if self.accept(OP, "}"):
            return Node("Dict", t.line, t.col, keys=[], values=[])
        if self.at(OP, "**"):
            return self.dict_rest(t, None, None)
        first = self.star_expression()
        if self.at(OP, ":"):
            self.next()
            value = self.element()
            if self.at_keyword("for") or self.at_keyword("async"):
                made = self.comprehension("DictComp", first, value, t)
                self.expect(OP, "}")
                return made
            return self.dict_rest(t, first, value)
        if self.at_keyword("for") or self.at_keyword("async"):
            made = self.comprehension("SetComp", first, None, t)
            self.expect(OP, "}")
            return made
        items = [first]
        while self.accept(OP, ","):
            if self.at(OP, "}"):
                break
            items.append(self.element())
        self.expect(OP, "}")
        return Node("Set", t.line, t.col, elts=items)

    def dict_rest(self, t, key, value):
        keys = []
        values = []
        if key is not None:
            keys.append(key)
            values.append(value)
        while True:
            if not self.accept(OP, ","):
                break
            if self.at(OP, "}"):
                break
            if self.accept(OP, "**"):
                # `{**a}` -- a SPREAD, which the tree records as a key of None.
                keys.append(None)
                values.append(self.or_test())
                continue
            k = self.expression()
            self.expect(OP, ":")
            keys.append(k)
            values.append(self.element())
        if not keys and self.at(OP, "**"):
            self.next()
            keys.append(None)
            values.append(self.or_test())
            while self.accept(OP, ","):
                if self.at(OP, "}"):
                    break
                if self.accept(OP, "**"):
                    keys.append(None)
                    values.append(self.or_test())
                    continue
                k = self.expression()
                self.expect(OP, ":")
                keys.append(k)
                values.append(self.element())
        self.expect(OP, "}")
        return Node("Dict", t.line, t.col, keys=keys, values=values)

    def strings(self):
        """One or more adjacent string literals, which CONCATENATE.

        An f-string among them makes the whole a `JoinedStr`, which is what
        CPython's tree says and what keeps `"a" f"{b}"` one value.
        """
        t = self.peek()
        pieces = []
        formatted = False
        while self.at(STRING):
            tok = self.next()
            prefix, body, raw = _split_string(tok)
            if prefix.find("f") >= 0 or prefix.find("t") >= 0:
                formatted = True
                pieces.append(("f", body, raw, tok))
            else:
                pieces.append(("s", body, raw, tok))
        if not formatted:
            text = ""
            is_bytes = False
            for kind, body, raw, tok in pieces:
                _, _, was_raw = _split_string(tok)
                text = text + (body if was_raw else _unescape(body, tok))
                if _split_string(tok)[0].find("b") >= 0:
                    is_bytes = True
            return Node("Constant", t.line, t.col,
                        value=text.encode("utf-8") if is_bytes else text,
                        raw="".join([p[1] for p in pieces]))
        values = []
        for kind, body, raw, tok in pieces:
            if kind == "s":
                if body:
                    values.append(Node("Constant", tok.line, tok.col,
                                       value=body, raw=body))
            else:
                values.extend(self.fstring_parts(body, tok))
        return Node("JoinedStr", t.line, t.col, values=values)

    def fstring_parts(self, body, tok):
        """Split an f-string into its literal pieces and replacement fields.

        THE FIELDS ARE PARSED, which is the whole reason this is here and not
        in the lexer: `f"{}"` is empty, `f"{1+}"` does not parse, and
        `f"{x!z}"` names a conversion that does not exist. All three are
        SyntaxErrors and none of them is visible to a tokeniser.
        """
        out = []
        text = ""
        i = 0
        n = len(body)
        while i < n:
            ch = body[i]
            if ch == "{" and i + 1 < n and body[i + 1] == "{":
                text = text + "{"
                i = i + 2
                continue
            if ch == "}" and i + 1 < n and body[i + 1] == "}":
                text = text + "}"
                i = i + 2
                continue
            if ch == "}":
                raise ParseError("f-string: single '}' is not allowed",
                                 tok.line, tok.col)
            if ch != "{":
                text = text + ch
                i = i + 1
                continue
            # A REPLACEMENT FIELD. Scan to the matching `}`, tracking nesting
            # and quotes so a `}` inside a nested string or format spec does
            # not end it early.
            depth = 0
            j = i + 1
            quote = ""
            while j < n:
                c = body[j]
                if quote:
                    if c == quote:
                        quote = ""
                elif c in ('"', "'"):
                    quote = c
                elif c in "([{":
                    depth = depth + 1
                elif c in ")]":
                    depth = depth - 1
                elif c == "}":
                    if depth == 0:
                        break
                    depth = depth - 1
                j = j + 1
            if j >= n:
                raise ParseError("f-string: expecting '}'", tok.line, tok.col)
            inner = body[i + 1:j]
            i = j + 1
            if text:
                out.append(Node("Constant", tok.line, tok.col, value=text,
                                raw=text))
                text = ""
            out.append(self.fstring_field(inner, tok))
        if text:
            out.append(Node("Constant", tok.line, tok.col, value=text,
                            raw=text))
        return out

    def fstring_field(self, inner, tok):
        spec = None
        conversion = -1
        # THE FORMAT SPEC comes after a `:` that is not inside brackets, and
        # the conversion after a `!` that is not `!=`.
        depth = 0
        cut = -1
        k = 0
        while k < len(inner):
            c = inner[k]
            if c in "([{":
                depth = depth + 1
            elif c in ")]}":
                depth = depth - 1
            elif c == ":" and depth == 0:
                cut = k
                break
            k = k + 1
        if cut >= 0:
            spec = inner[cut + 1:]
            inner = inner[:cut]
        if len(inner) > 1 and inner[-2] == "!" and inner[-1] != "=":
            conversion = inner[-1]
            inner = inner[:-2]
            if conversion not in ("s", "r", "a"):
                raise ParseError("f-string: invalid conversion character "
                                 + repr(conversion)
                                 + ": expected 's', 'r', or 'a'",
                                 tok.line, tok.col)
            conversion = ord(conversion)
        stripped = inner.strip()
        if stripped.endswith("="):
            # `f"{x=}"` -- the SELF-DOCUMENTING form, which keeps the text and
            # shows the value.
            stripped = stripped[:-1].strip()
        if not stripped:
            raise ParseError("f-string: valid expression required before '}'",
                             tok.line, tok.col)
        value = parse_expression_text(stripped, tok)
        return Node("FormattedValue", tok.line, tok.col, value=value,
                    conversion=conversion,
                    format_spec=Node("Constant", tok.line, tok.col,
                                     value=spec, raw=spec)
                    if spec is not None else None)


def parse_expression_text(text, tok):
    """Parse a fragment as one expression, reporting at `tok`'s position.

    Used for an f-string's replacement fields: the fragment is real Python and
    a failure in it is a SyntaxError in the enclosing string.
    """
    try:
        inner = Parser(tokenize(text), "eval")
        return inner.parse_expression().body
    except LexError as exc:
        raise ParseError("f-string: " + exc.msg, tok.line, tok.col)
    except ParseError as exc:
        raise ParseError("f-string: " + exc.msg, tok.line, tok.col)


def _split_string(tok):
    """A string token's prefix, its body, and whether it was raw."""
    text = tok.value
    at = 0
    while text[at] not in ('"', "'"):
        at = at + 1
    prefix = text[:at].lower()
    quote = text[at]
    triple = text[at:at + 3] == quote * 3
    width = 3 if triple else 1
    return (prefix, text[at + width:len(text) - width], prefix.find("r") >= 0)


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\",
            "'": "'", '"': '"', "a": "\a", "b": "\b", "f": "\f", "v": "\v"}


def _unescape(body, tok):
    out = ""
    i = 0
    while i < len(body):
        ch = body[i]
        if ch != "\\":
            out = out + ch
            i = i + 1
            continue
        if i + 1 >= len(body):
            out = out + ch
            break
        nxt = body[i + 1]
        if nxt == "\n":
            i = i + 2
            continue
        if nxt in _ESCAPES:
            out = out + _ESCAPES[nxt]
            i = i + 2
            continue
        if nxt == "x" and i + 3 < len(body) + 1:
            out = out + chr(int(body[i + 2:i + 4], 16))
            i = i + 4
            continue
        if nxt == "u":
            out = out + chr(int(body[i + 2:i + 6], 16))
            i = i + 6
            continue
        if nxt == "U":
            out = out + chr(int(body[i + 2:i + 10], 16))
            i = i + 10
            continue
        # AN UNKNOWN ESCAPE KEEPS THE BACKSLASH, which is what Python does --
        # `"\d"` is two characters, with a DeprecationWarning and not an error.
        out = out + ch + nxt
        i = i + 2
    return out


def _number_value(text):
    """A NUMBER token's value. The literal is already known well-formed."""
    body = text.replace("_", "")
    lowered = body.lower()
    if lowered.endswith("j"):
        return complex(0.0, float(body[:-1]))
    if lowered.startswith("0x"):
        return int(body[2:], 16)
    if lowered.startswith("0o"):
        return int(body[2:], 8)
    if lowered.startswith("0b"):
        return int(body[2:], 2)
    if body.find(".") >= 0 or lowered.find("e") >= 0:
        return float(body)
    return int(body)


def parse(source, mode="exec"):
    """Source to a tree. Raises `LexError` or `ParseError` on bad input."""
    tokens = tokenize(source)
    parser = Parser(tokens, mode)
    if mode == "eval":
        return parser.parse_expression()
    return parser.parse_module()
