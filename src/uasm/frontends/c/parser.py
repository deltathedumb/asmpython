"""The C grammar, typed as it is parsed.

A C PARSER CANNOT BE A PURE PARSER. `(a)*b` is a multiplication if `a` is an
object and a cast if `a` is a typedef name, and `a*b;` is an expression
statement or a declaration of a pointer for the same reason. Nothing short of
knowing what `a` was declared as decides it -- so this file keeps scopes, calls
into `sema.py` for every type question, and produces a tree that is already
typed. The alternative, a parse tree that is rewritten once the types are
known, has to guess first and be corrected later, and the corrections are where
the compiler gets its reputation.

WHAT THE FILE IS MADE OF, top to bottom:

    token access          peeking, eating, the typedef test
    expressions           precedence climbing down to the primaries
    types                 specifiers, declarators, struct/union/enum
    initialisers          designators, brace elision, string initialisers
    statements            every one, and the loop/switch context they need
    declarations          objects, functions, K&R definitions, the unit

DECLARATORS ARE BUILT AS CLOSURES. `int (*a[3])(void)` is an array of three
pointers to functions, and the reading order is neither left to right nor right
to left -- it spirals. `_declarator` returns a function from "the type to the
left" to "the type this declares", and composing those functions in the order
the grammar meets them produces the spiral for free. It is four lines and it is
the only implementation of this that is not a special case per shape.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from ...diagnostics import DiagnosticSink, Span
from . import ctype as C
from . import syntax as S
from .builtin_check import TYPE_BUILTINS, check_builtin, parse_type_builtin
from .builtins import BUILTINS, FUNCTION_NAME_IDENTIFIERS
from .ctype import CType, IncompleteType
from .fold import fold, fold_int
from .literals import (
    FloatConst, LiteralError, decode_char, encode,
    join_strings, parse_number,
)
from .sema import Linkage, Sema, Storage, Symbol
from .tokens import KEYWORD_ALIASES, KEYWORDS, Kind, Token

#: Where the bundled standard headers live. NOT imported from `preprocess`,
#: which imports nothing from here and should keep it that way; the one
#: sentence that defines it is `Path(__file__).parent / "include"` in both
#: places, and a test checks that the two agree.
_BUNDLED = Path(__file__).parent / "include"

#: Storage-class keywords, and the `Storage` each maps to.
_STORAGE = {
    "typedef": Storage.TYPEDEF, "extern": Storage.EXTERN,
    "static": Storage.STATIC, "auto": Storage.AUTO,
    "register": Storage.REGISTER,
}
_QUALIFIERS = {"const", "volatile", "restrict", "_Atomic"}
_FUNCTION_SPECIFIERS = {"inline", "_Noreturn"}

#: The combinations of type-specifier keywords C allows, and what each means.
#: A sorted tuple as the key, because `long unsigned int` and `unsigned long`
#: are the same type and a grammar that cares about the order of these has
#: written out twenty productions to say so.
_BASIC: dict[tuple[str, ...], CType] = {}


def _basic(ty: CType, *spellings: str) -> None:
    for spelling in spellings:
        _BASIC[tuple(sorted(spelling.split()))] = ty


_basic(C.VOID, "void")
_basic(C.BOOL, "_Bool")
_basic(C.CHAR, "char")
_basic(C.SCHAR, "signed char")
_basic(C.UCHAR, "unsigned char")
_basic(C.SHORT, "short", "short int", "signed short", "signed short int")
_basic(C.USHORT, "unsigned short", "unsigned short int")
_basic(C.INT, "int", "signed", "signed int")
_basic(C.UINT, "unsigned", "unsigned int")
_basic(C.LONG, "long", "long int", "signed long", "signed long int")
_basic(C.ULONG, "unsigned long", "unsigned long int")
_basic(C.LLONG, "long long", "long long int", "signed long long",
       "signed long long int")
_basic(C.ULLONG, "unsigned long long", "unsigned long long int")
_basic(C.FLOAT, "float")
_basic(C.DOUBLE, "double")
_basic(C.LDOUBLE, "long double")

#: Binary operators by precedence, loosest first. The table IS the parser for
#: this part of the grammar: twelve levels written as twelve functions is
#: twelve places for a precedence to be wrong.
_PRECEDENCE: list[tuple[str, ...]] = [
    ("||",), ("&&",), ("|",), ("^",), ("&",), ("==", "!="),
    ("<", ">", "<=", ">="), ("<<", ">>"), ("+", "-"), ("*", "/", "%"),
]

_ASSIGN_OPS = ("=", "*=", "/=", "%=", "+=", "-=", "<<=", ">>=", "&=", "^=",
               "|=")


class Parser:
    """One translation unit."""

    def __init__(self, tokens: list[Token], sink: DiagnosticSink,
                 prefix: str = "c0.") -> None:
        self.toks = tokens
        self.i = 0
        self.sink = sink
        self.sema = Sema(sink, prefix)
        self.unit = S.Unit(tokens[0].span if tokens else None)
        #: The function being parsed, for `return`, labels and `__func__`.
        self.function: S.FunctionDef | None = None
        #: Whether a `break` or a `continue` has anywhere to go. See
        #: `_loop_body`: the parser counts, lowering names.
        self.breaks: list[bool] = []
        self.continues: list[bool] = []
        self.switches: list[S.Switch] = []
        self._label_n = 0
        #: Anonymous globals -- string literals, compound literals at file
        #: scope -- collected here and emitted by lowering.
        self.strings: dict[tuple[str, bytes], str] = {}
        self.anon: list[Any] = []
        self._gotos: list[tuple[str, Span, tuple[str, ...]]] = []
        #: THE VARIABLY MODIFIED OBJECTS IN SCOPE, outermost first. A `goto`
        #: may leave such a scope and may not enter one -- the length was
        #: computed where the declaration is, and a jump past it reaches an
        #: array whose size was never worked out. Recorded at each `goto`
        #: and at each label, and compared when the function closes, because
        #: a label may be written after the `goto` that reaches it.
        self._vm_scope: list[str] = []
        self._label_vm: dict[str, tuple[str, ...]] = {}

    # ── token access ────────────────────────────────────────────────────────
    @property
    def tok(self) -> Token:
        return self.toks[self.i]

    def peek(self, k: int = 1) -> Token:
        j = min(self.i + k, len(self.toks) - 1)
        return self.toks[j]

    def next(self) -> Token:
        t = self.toks[self.i]
        if t.kind is not Kind.EOF:
            self.i += 1
        return t

    def at(self, *texts: str) -> bool:
        t = self.tok
        return t.kind in (Kind.PUNCT, Kind.IDENT) and t.text in texts

    def at_kw(self, *names: str) -> bool:
        t = self.tok
        return t.kind is Kind.IDENT and _kw(t.text) in names

    def eat(self, *texts: str) -> Token | None:
        if self.at(*texts):
            return self.next()
        return None

    def expect(self, text: str, what: str = "") -> Token | None:
        if self.at(text):
            return self.next()
        got = self.tok
        seen = "end of file" if got.kind is Kind.EOF else repr(got.text)
        self.sema.error("E1200", f"expected `{text}`, found {seen}", got.span,
                        note=what)
        return None

    def is_typedef(self, tok: Token) -> bool:
        if tok.kind is not Kind.IDENT or _kw(tok.text) in KEYWORDS:
            return False
        sym = self.sema.scope.lookup(tok.text)
        return sym is not None and sym.storage is Storage.TYPEDEF

    def label(self, hint: str) -> str:
        self._label_n += 1
        return f"{hint}.{self._label_n}"

    # ── expressions ─────────────────────────────────────────────────────────
    def expression(self) -> S.Expr:
        e = self.assignment()
        while self.at(","):
            span = self.next().span
            right = self.sema.lvalue_conversion(self.assignment())
            e = S.Comma(span, right.type, False, e, right)
        return e

    def assignment(self) -> S.Expr:
        left = self.conditional()
        if not self.at(*_ASSIGN_OPS):
            return left
        op = self.next()
        right = self.assignment()
        return self.sema.build_assign(op.text, left, right, op.span)

    def conditional(self) -> S.Expr:
        cond = self._binary(0)
        if not self.at("?"):
            return cond
        span = self.next().span
        # GNU's `a ?: b` -- the middle operand omitted means `a`. Used in
        # real headers; the value of `a` is computed once.
        if self.at(":"):
            self.next()
            other = self.conditional()
            return self.sema.build_conditional(cond, cond, other, span)
        then = self.expression()
        self.expect(":", "a `?` needs a `:`")
        other = self.conditional()
        return self.sema.build_conditional(cond, then, other, span)

    def _binary(self, level: int) -> S.Expr:
        if level >= len(_PRECEDENCE):
            return self.cast_expression()
        left = self._binary(level + 1)
        while self.tok.kind is Kind.PUNCT and self.tok.text in _PRECEDENCE[level]:
            op = self.next()
            right = self._binary(level + 1)
            left = self.sema.build_binary(op.text, left, right, op.span)
        return left

    #: THE STORAGE CLASSES A COMPOUND LITERAL MAY CARRY, which C23 added so
    #: that `(static int[]){1, 2}` has static storage duration and its
    #: address is therefore a constant -- the one thing a block-scope
    #: compound literal could not be before. `extern`, `auto` and `typedef`
    #: are NOT among them: the literal defines its object right where it
    #: stands, so there is nothing for any of the three to mean.
    #: SPELT AS `_kw` LEAVES THEM, which is why `thread_local` appears here
    #: under its C11 name: `KEYWORD_ALIASES` folds the two spellings into
    #: one keyword and this table is consulted after the fold. Written the
    #: other way it silently matched nothing.
    _LITERAL_STORAGE = ("static", "register", "constexpr", "_Thread_local")

    def _at_literal_storage(self, t: Token) -> bool:
        return (t.kind is Kind.IDENT
                and _kw(t.text) in self._LITERAL_STORAGE)

    def _storage_literal(self) -> S.Expr:
        """`(static T){...}` and the other three. A CAST CANNOT START WITH A
        STORAGE CLASS, so seeing one after the `(` settles what this is
        before the type is read -- which is why this does not go through
        `_at_compound_literal`'s lookahead."""
        self.next()                                   # `(`
        seen: dict[str, Span] = {}
        while self._at_literal_storage(self.tok):
            t = self.next()
            word = _kw(t.text)
            if word in seen:
                self.sema.error("E1604", f"`{t.text}` appears twice here",
                                t.span, also=(seen[word], "the first one"))
            seen[word] = t.span
        ty = self.type_name()
        close = self.expect(")", "a compound literal's type needs a `)`")
        span = close.span if close else self.tok.span
        if not self.at("{"):
            self.sema.error(
                "E1605", "a storage class here needs a compound literal", span,
                note="`(static int)x` is not a cast",
                help="write `(static int){x}`, or drop the storage class")
            return self.sema.poison(span)
        if "_Thread_local" in seen and "static" not in seen:
            # C says `thread_local` goes with `static` or `extern` and a
            # compound literal cannot be `extern`, so `static` is the only
            # company left. Reported rather than assumed: a program that
            # wrote one and meant a per-call object should hear about it.
            self.sema.error(
                "E1606", "`thread_local` here needs `static` too",
                seen["_Thread_local"],
                note="a per-thread object outlives the call that made it",
                help="write `(static thread_local T){...}`")
        if "constexpr" in seen:
            ty = ty.qualified({"const"})
        return self._compound_literal(
            ty, span, static="static" in seen or "_Thread_local" in seen,
            thread_local="_Thread_local" in seen,
            constexpr="constexpr" in seen, register="register" in seen)

    def cast_expression(self) -> S.Expr:
        if self.at("(") and self._at_literal_storage(self.peek()):
            return self.postfix(self._storage_literal())
        if self.at("(") and self._type_starts(self.peek()):
            self.next()
            ty = self.type_name()
            close = self.expect(")", "a cast's type needs a closing `)`")
            if self.at("{"):
                # `(T){...}` is a compound literal, not a cast.
                return self.postfix(self._compound_literal(ty, close.span if close else self.tok.span))
            operand = self.cast_expression()
            return self.sema.build_cast(ty, operand, close.span if close else self.tok.span)
        return self.unary()

    def unary(self) -> S.Expr:
        t = self.tok
        if t.kind is Kind.PUNCT and t.text in ("&", "*", "+", "-", "~", "!"):
            self.next()
            return self.sema.build_unary(t.text, self.cast_expression(), t.span)
        if self.at("++", "--"):
            self.next()
            operand = self.unary()
            return self.sema.build_unary(t.text, operand, t.span)
        if self.at_kw("__real__", "__imag__"):
            # gcc's TWO HALVES OF A COMPLEX VALUE, spelled as operators
            # rather than as functions. Real headers use them -- glibc's own
            # `<complex.h>` is written with them -- and so is this one's.
            self.next()
            return self.sema.build_unary(_kw(t.text), self.unary(), t.span)
        if self.at_kw("sizeof", "_Alignof"):
            self.next()
            is_align = _kw(t.text) == "_Alignof"
            if self.at("(") and self._type_starts(self.peek()):
                self.next()
                ty = self.type_name()
                self.expect(")")
                return self._sizeof_type(ty, t.span, is_align)
            if is_align:
                self.sema.error("E1201", "_Alignof needs a parenthesised type",
                                t.span)
                return self.sema.poison(t.span)
            operand = self.unary()
            if operand.type.is_function:
                self.sema.error("E1202", "sizeof of a function", t.span,
                                note="a function has no size")
                return self.sema.poison(t.span)
            if isinstance(operand, S.MemberAccess) and operand.path \
                    and operand.path[-1].is_bitfield:
                # A BIT-FIELD IS NOT AN OBJECT of its declared type: it is
                # some bits inside one, and the answer would have to be the
                # container's size, which is not what anybody asking means.
                # C forbids the question rather than picking an answer.
                self.sema.error(
                    "E1220", "`sizeof` of a bit-field", t.span,
                    note="a bit-field is part of an object, not one of its "
                         "own", help="ask for the width you declared")
                return self.sema.poison(t.span)
            return self._sizeof_type(operand.type, t.span, False, operand)
        return self.postfix(self.primary())

    def _sizeof_type(self, ty: CType, span: Span, alignment: bool,
                     operand: S.Expr | None = None) -> S.Expr:
        if ty.unqualified().is_void:
            # REFUSED, AND NOT WARNED ABOUT. gcc answers 1 here as an
            # extension, and the reason it is worth having is that it makes
            # `p + 1` on a `void *` mean something -- which this frontend
            # refuses outright (E1419), as C says to. Allowing half of the
            # extension would leave `sizeof(void)` an answer with nothing to
            # do: the two decisions have to be the same one.
            self.sema.error(
                "E1203", f"`{'_Alignof' if alignment else 'sizeof'}` of "
                         f"`void`", span,
                note="`void` is an incomplete type and has no size",
                help="did you mean `sizeof(void *)`?")
            return self.sema.poison(span)
        node = S.SizeofType(span, C.SIZE_T, False, ty, None, alignment)
        if ty.is_vla and not alignment:
            node.dynamic = self.sema.vla_size(ty, span)
            return node
        try:
            ty.size if not alignment else ty.align
        except IncompleteType:
            what = "_Alignof" if alignment else "sizeof"
            self.sema.error("E1203",
                            f"{what} of the incomplete type {C.spell(ty)}",
                            span,
                            note="its size is not known here")
            return self.sema.poison(span)
        return node

    def postfix(self, e: S.Expr) -> S.Expr:
        while True:
            t = self.tok
            if self.at("["):
                self.next()
                index = self.expression()
                self.expect("]")
                e = self.sema.build_index(e, index, t.span)
            elif self.at("("):
                # THE TYPE-TAKING BUILTINS ARE DECIDED HERE, before the
                # argument list is read: `__builtin_va_arg(ap, int)` has a
                # TYPE where an expression belongs, and parsing it as one
                # reports that `int` is not a value.
                if isinstance(e, S.Ident) and e.name in TYPE_BUILTINS:
                    e = parse_type_builtin(self, e.name, t.span)
                    continue
                self.next()
                args = []
                if not self.at(")"):
                    args.append(self.assignment())
                    while self.eat(","):
                        args.append(self.assignment())
                self.expect(")", "a call's arguments need a closing `)`")
                e = self._call(e, args, t.span)
            elif self.at(".", "->"):
                arrow = t.text == "->"
                self.next()
                name = self.tok
                if name.kind is not Kind.IDENT:
                    self.sema.error("E1204",
                                    f"expected a member name after `{t.text}`",
                                    name.span)
                    return self.sema.poison(t.span)
                self.next()
                e = self.sema.build_member(e, name.text, arrow, t.span)
            elif self.at("++", "--"):
                self.next()
                e = self.sema.build_incdec(t.text, e, t.span, postfix=True)
            else:
                return e

    def _call(self, func: S.Expr, args: list[S.Expr], span: Span) -> S.Expr:
        name = func.name if isinstance(func, S.Ident) else None
        if name in BUILTINS:
            return self._builtin(name, args, span)
        return self.sema.build_call(func, args, span)

    def primary(self) -> S.Expr:
        t = self.tok
        if t.kind is Kind.NUMBER:
            self.next()
            return self._number(t)
        if t.kind is Kind.CHARCONST:
            self.next()
            try:
                c = decode_char(t.text)
            except LiteralError as exc:
                self.sema.error(getattr(exc, "code", "E1010"), str(exc), t.span)
                return self.sema.poison(t.span)
            # A NARROW CHARACTER CONSTANT HAS TYPE `int`, not `char`. That is
            # 6.4.4.4p10 and it is why `sizeof('a')` is 4 in C and 1 in C++.
            ty = {"": C.INT, "u8": C.UCHAR, "L": C.WCHAR_T,
                  "u": C.CHAR16_T, "U": C.CHAR32_T}[c.prefix]
            return S.IntLit(t.span, ty, False, c.value)
        if t.kind is Kind.STRING:
            return self._string()
        if self.at("("):
            self.next()
            if self.at("{"):
                return self._statement_expression(t.span)
            e = self.expression()
            self.expect(")", "this `(` is never closed")
            return e
        if self.at_kw("_Generic"):
            return self._generic()
        if t.kind is Kind.IDENT and _kw(t.text) in ("true", "false"):
            self.next()
            return S.IntLit(t.span, C.BOOL, False, 1 if t.text == "true" else 0)
        if t.kind is Kind.IDENT and _kw(t.text) == "nullptr":
            self.next()
            return S.IntLit(t.span, C.VOID_PTR, False, 0)
        if t.kind is Kind.IDENT and not t.is_keyword:
            self.next()
            if t.text in TYPE_BUILTINS or (
                    t.text in BUILTINS
                    and self.sema.scope.lookup(t.text) is None):
                # A BUILTIN IS NOT DECLARED ANYWHERE. Nothing writes a
                # prototype for `__builtin_memcpy`, so the ordinary lookup
                # would report it undeclared; the name stands in for itself
                # until `postfix` reaches the `(` and dispatches on it.
                # `__builtin_va_arg` and the other two that take a TYPE
                # cannot be parsed as calls at all -- see `builtin_check`.
                return S.Ident(t.span, C.INT, False, t.text, None)
            return self._name(t)
        if t.kind is Kind.EOF:
            self.sema.error("E1205", "expected an expression, found end of file",
                            t.span)
            return self.sema.poison(t.span)
        if t.kind is Kind.OTHER:
            # Phase 3 kept it without complaint -- see `lexer.tokens`. It is
            # reported HERE, where it has survived every conditional and every
            # macro and is really part of the program.
            self.sema.error("E1001", f"stray {t.text!r} in program", t.span)
            self.next()
            return self.sema.poison(t.span)
        self.sema.error("E1205", f"expected an expression, found {t.text!r}",
                        t.span)
        self.next()
        return self.sema.poison(t.span)

    def _number(self, t: Token) -> S.Expr:
        try:
            got = parse_number(t.text)
        except LiteralError as exc:
            self.sema.error(getattr(exc, "code", "E1010"), str(exc), t.span)
            return self.sema.poison(t.span)
        if isinstance(got, FloatConst):
            ty = (C.FLOAT if got.size == 4
                  else C.LDOUBLE if got.size == 16 else C.DOUBLE)
            if ty is C.LDOUBLE and got.exact is not None and not got.imaginary:
                # THE EXACT VALUE, which is a `Fraction` rather than a float:
                # see `literals.FloatConst.exact` and `ldouble.py`.
                return S.FloatLit(t.span, ty, False, got.exact)
            if got.imaginary:
                # THE VALUE IS THE PAIR, which is what makes the rest of the
                # compiler need no special case: a complex literal is a
                # `FloatLit` whose value is a Python complex, folding
                # computes with it, and lowering stores two halves.
                return S.FloatLit(t.span, C.complex_of(ty), False,
                                  complex(0.0, got.value))
            return S.FloatLit(t.span, ty, False, got.value)
        ty = C.bitint(got.bits, got.signed) if got.bitint \
            else C.integer(got.bits, got.signed)
        return S.IntLit(t.span, ty, False, got.value)

    def _string(self) -> S.Expr:
        span = self.tok.span
        parts = []
        while self.tok.kind is Kind.STRING:
            parts.append(self.next().text)
        literal: list[bool] = []
        try:
            prefix, values = join_strings(parts, literal)
        except LiteralError as exc:
            self.sema.error("E1010", str(exc), span)
            return self.sema.poison(span)
        width, _, _ = _prefix_info(prefix)
        elem = {"": C.CHAR, "u8": C.UCHAR, "L": C.WCHAR_T,
                "u": C.CHAR16_T, "U": C.CHAR32_T}[prefix]
        # `literal` SAYS WHICH VALUES WERE WRITTEN AS BYTES, which is the one
        # thing `encode` cannot work out: `"\xe9"` is one byte and `"\u00e9"`
        # is the two UTF-8 spells that character with.
        data = encode(values, prefix, literal) + b"\x00" * width
        count = len(data) // width
        node = S.StringLit(span, C.array_of(elem, count), True, prefix, data)
        key = (prefix, data)
        if key not in self.strings:
            self.strings[key] = f"{self.sema.prefix}str.{len(self.strings)}"
        node.symbol = self.strings[key]
        return node

    def _name(self, t: Token) -> S.Expr:
        sym = self.sema.scope.lookup(t.text)
        if sym is None:
            if t.text in FUNCTION_NAME_IDENTIFIERS and self.function is not None:
                return self._func_name(t)
            if self.at("("):
                # AN IMPLICIT DECLARATION. C99 removed it; every real program
                # that relies on it is missing a header, and guessing
                # `int f()` makes the call compile and then behave wrongly if
                # it returns anything else. Refused, with the reason.
                self.sema.error(
                    "E1206", f"call to undeclared function {t.text!r}", t.span,
                    note="C99 removed implicit function declarations",
                    help="include the header that declares it, or write a "
                         "declaration before this call")
            else:
                self.sema.error("E1207", f"{t.text!r} is not declared", t.span)
            return self.sema.poison(t.span)
        if sym.storage is Storage.TYPEDEF:
            self.sema.error("E1208",
                            f"{t.text!r} is a type, not a value", t.span,
                            also=(sym.span, "declared here") if sym.span else None)
            return self.sema.poison(t.span)
        if sym.storage is Storage.ENUM_CONST:
            return S.IntLit(t.span, sym.type, False, sym.value)
        if "deprecated" in sym.attrs:
            self.sema.warn(
                "W1219", f"{t.text!r} is deprecated", t.span,
                note="its declaration carries `[[deprecated]]`")
        return S.Ident(t.span, sym.type, not sym.type.is_function, t.text, sym)

    def _func_name(self, t: Token) -> S.Expr:
        """`__func__`: a static array holding the function's name, 6.4.2.2."""
        name = self.function.name
        data = name.encode("utf-8") + b"\x00"
        key = ("", data)
        if key not in self.strings:
            self.strings[key] = f"{self.sema.prefix}str.{len(self.strings)}"
        node = S.StringLit(t.span, C.array_of(C.CHAR.qualified({"const"}),
                                              len(data)), True, "", data)
        node.symbol = self.strings[key]
        return node

    def _generic(self) -> S.Expr:
        span = self.next().span
        self.expect("(", "_Generic needs parentheses")
        control = self.sema.lvalue_conversion(self.assignment())
        chosen: S.Expr | None = None
        default: S.Expr | None = None
        seen: list[CType] = []
        while self.eat(","):
            if self.at_kw("default"):
                self.next()
                self.expect(":")
                got = self.assignment()
                if default is not None:
                    self.sema.error("E1209",
                                    "_Generic has more than one `default`",
                                    got.span)
                default = got
                continue
            ty = self.type_name()
            self.expect(":")
            got = self.assignment()
            # QUALIFIERS COUNT. `int` and `const int` are not compatible
            # types -- 6.2.7 requires identical qualifiers -- so listing both
            # is legal, and a control expression of type `int` matches only
            # the first. Comparing unqualified types made the pair a
            # duplicate and refused a program gcc accepts.
            for earlier in seen:
                if C.compatible(earlier, ty):
                    self.sema.error(
                        "E1210",
                        f"_Generic lists {C.spell(ty)} more than once", got.span)
            seen.append(ty)
            if C.compatible(control.type.unqualified(), ty):
                chosen = got
        self.expect(")")
        picked = chosen if chosen is not None else default
        if picked is None:
            self.sema.error(
                "E1211",
                f"_Generic has no association for {C.spell(control.type)}",
                span,
                help="add a `default:` association")
            return self.sema.poison(span)
        return picked

    def _statement_expression(self, span: Span) -> S.Expr:
        body = self.compound_statement()
        self.expect(")", "a statement expression needs a closing `)`")
        ty = C.VOID
        if body.items and isinstance(body.items[-1], S.ExprStmt) and \
                body.items[-1].expr is not None:
            ty = body.items[-1].expr.type
        return S.StmtExpr(span, ty, False, body)

    def _compound_literal(self, ty: CType, span: Span, *,
                          static: bool = False, thread_local: bool = False,
                          constexpr: bool = False,
                          register: bool = False) -> S.Expr:
        init = self.initializer(ty, span)
        if ty.is_array and ty.count is None:
            ty = C.array_of(ty.of, init.size // max(1, ty.of.size))
        node = S.CompoundLiteral(span, ty, True, init)
        node.register = register
        if constexpr:
            # THE SAME RULE A `constexpr` OBJECT LIVES BY, and checked the
            # same way: every entry has to fold. There is no name to
            # remember a scalar value under -- a literal is not something a
            # later expression can spell -- so this is the check alone.
            for entry in init.entries:
                if entry.value is None or entry.data is not None:
                    continue
                if fold(entry.value) is None:
                    self.sema.error(
                        "E1607",
                        "this `constexpr` compound literal has an "
                        "initialiser that is not a constant",
                        entry.value.span,
                        note="`constexpr` says the value is known at "
                             "compile time, and this one is not")
                    break
        # A FILE-SCOPE LITERAL IS STATIC WHETHER IT SAYS SO OR NOT, because
        # there is no frame for it to live in; `(static T){...}` is how a
        # literal INSIDE a function asks for the same thing.
        if static or self.sema.scope.is_file:
            node.static = True
            node.thread_local = thread_local
            node.symbol = self.sema.unique(self.sema.prefix + "compound")
            self.anon.append(node)
        return node

    def _builtin(self, name: str, args: list[S.Expr], span: Span) -> S.Expr:
        """A `__builtin_*`. Type checking in `builtin_check`, code in `lower`."""
        return check_builtin(self, name, args, span)

    # ── types ───────────────────────────────────────────────────────────────
    def _type_starts(self, t: Token) -> bool:
        if t.kind is not Kind.IDENT:
            return False
        word = _kw(t.text)
        if word in ("void", "char", "short", "int", "long", "float", "double",
                    "signed", "unsigned", "_Bool", "_Complex", "struct",
                    "union", "enum", "const", "volatile", "restrict",
                    "_Atomic", "typedef", "extern", "static", "auto",
                    "register", "inline", "_Noreturn", "_Alignas",
                    "_Thread_local", "typeof", "typeof_unqual", "constexpr",
                    "_BitInt"):
            return True
        return self.is_typedef(t)

    def _past_attributes(self, j: int) -> int:
        """The index just past any `[[...]]` sequences starting at `j`.

        A LOOKAHEAD AND NOT A PARSE, because the caller has not decided yet
        what it is looking at. The brackets nest, so counting them is enough:
        an attribute's argument clause may contain its own `[` and `]` and a
        string with a `]` in it is one token rather than a punctuator.
        """
        while (j + 1 < len(self.toks) and self.toks[j].is_punct("[")
               and self.toks[j + 1].is_punct("[")):
            depth = 0
            while j < len(self.toks) and self.toks[j].kind is not Kind.EOF:
                if self.toks[j].is_punct("["):
                    depth += 1
                elif self.toks[j].is_punct("]"):
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
        return j

    def is_declaration(self) -> bool:
        """Whether what follows is a declaration rather than a statement."""
        t = self.tok
        if self._at_attribute():
            # `[[maybe_unused]] int x;` AND `[[fallthrough]];` BOTH BEGIN
            # WITH `[[`, and only the token after the sequence says which of
            # the two it is. Nothing is consumed here; whichever branch the
            # caller takes parses the attributes itself.
            j = self._past_attributes(self.i)
            t = self.toks[j]
            if t.kind is Kind.IDENT and _kw(t.text) == "_Static_assert":
                return True
            if not self._type_starts(t):
                return False
            return not (self.is_typedef(t)
                        and j + 1 < len(self.toks)
                        and self.toks[j + 1].is_punct(":"))
        if self.at_kw("_Static_assert"):
            return True
        if not self._type_starts(t):
            return False
        # `x;` where `x` is a typedef name is a declaration of nothing, and
        # `x * y;` is a declaration of a pointer -- but a LABEL looks the same
        # until the colon. `foo: ;` inside a function must not be read as a
        # declaration of a typedef'd `foo`.
        if self.is_typedef(t) and self.peek().is_punct(":"):
            return False
        return True

    def type_name(self) -> CType:
        """A type-name: specifiers plus an abstract declarator."""
        spec = self.declaration_specifiers(allow_storage=False)
        name, build, span = self._declarator(abstract=True)
        if name is not None:
            self.sema.error("E1212",
                            f"a type name cannot declare {name!r}", span)
        return build(spec.type)

    class Spec:
        """What a declaration's specifiers said."""

        __slots__ = ("type", "storage", "inline", "noreturn", "align",
                     "attrs", "constexpr", "infer",
                     "span", "thread_local", "explicit", "align_span")

        def __init__(self) -> None:
            self.type: CType = C.INT
            self.storage: Storage | None = None
            self.inline = False
            self.noreturn = False
            self.attrs: set[str] = set()
            self.constexpr = False
            self.infer = False
            self.align: int | None = None
            self.align_span: Span | None = None
            self.thread_local = False
            self.span: Span | None = None
            #: False when no type specifier was written at all, which C99
            #: refuses and K&R allowed.
            self.explicit = False

    def declaration_specifiers(self, *, allow_storage: bool = True) -> Spec:
        spec = Parser.Spec()
        words: list[str] = []
        quals: set[str] = set()
        attrs: set[str] = set()
        made: CType | None = None
        complex_ = False
        bit_width: int | None = None
        start = self.tok.span
        spec.span = start
        while True:
            t = self.tok
            if self._at_attribute():
                # `[[noreturn]] void f(void);` -- the sequence may come
                # before the specifiers, between them or after them, and C
                # says it appertains to the whole declaration either way.
                attrs |= self._std_attributes()
                continue
            if t.kind is not Kind.IDENT:
                break
            word = _kw(t.text)
            if word in _STORAGE:
                if not allow_storage:
                    break
                if spec.storage is not None:
                    self.sema.error("E1213",
                                    "more than one storage class", t.span)
                spec.storage = _STORAGE[word]
                self.next()
                continue
            if word == "_Thread_local":
                spec.thread_local = True
                self.next()
                continue
            if word == "constexpr":
                # A STORAGE-CLASS SPECIFIER THAT IS NOT ONE OF THE OTHERS.
                # C23 lets it appear WITH `static`, `auto`, `register` or
                # `thread_local` -- it says the value is known now, not
                # where the object lives -- so it is a flag beside
                # `spec.storage` rather than an entry in `_STORAGE`.
                spec.constexpr = True
                self.next()
                continue
            if word in _QUALIFIERS:
                # `_Atomic (T)` is a specifier, not a qualifier.
                if word == "_Atomic" and self.peek().is_punct("("):
                    self.next()
                    self.next()
                    made = self.type_name()
                    self.expect(")")
                    spec.explicit = True
                    continue
                quals.add(word)
                self.next()
                continue
            if word in _FUNCTION_SPECIFIERS:
                if word == "inline":
                    spec.inline = True
                else:
                    spec.noreturn = True
                self.next()
                continue
            if word == "_Alignas":
                at = self.next()
                self.expect("(")
                if self._type_starts(self.tok):
                    spec.align = self.type_name().align
                else:
                    e = self.conditional()
                    got = fold_int(e)
                    if got is None:
                        self.sema.error(
                            "E1257", "an alignment must be a constant",
                            e.span or at.span,
                            note="`_Alignas` is part of the type and is "
                                 "decided when the type is")
                    elif got <= 0 or (got & (got - 1)) != 0:
                        # A POWER OF TWO AND NOT ZERO, which is what an
                        # alignment IS: an address is aligned to `n` when it
                        # is a multiple of `n`, and every valid one here is
                        # a whole number of bytes that a machine can ask for.
                        self.sema.error(
                            "E1257",
                            f"an alignment of {got} is not a positive power "
                            f"of two", e.span or at.span)
                        got = None
                    spec.align = got
                self.expect(")")
                spec.align_span = at.span
                continue
            if word in ("struct", "union"):
                made = self.struct_or_union()
                spec.explicit = True
                continue
            if word == "enum":
                made = self.enum_specifier()
                spec.explicit = True
                continue
            if word in ("typeof", "typeof_unqual"):
                self.next()
                self.expect("(")
                if self._type_starts(self.tok):
                    made = self.type_name()
                else:
                    made = self.sema.lvalue_conversion(self.expression()).type
                if word == "typeof_unqual":
                    made = made.unqualified()
                self.expect(")")
                spec.explicit = True
                continue
            if word in ("void", "char", "short", "int", "long", "float",
                        "double", "signed", "unsigned", "_Bool"):
                words.append(word)
                spec.explicit = True
                self.next()
                continue
            if word == "_BitInt":
                bit_width = self._bitint_width()
                spec.explicit = True
                continue
            if word == "_Complex":
                # A FLAG RATHER THAN A WORD, because it combines with the
                # words rather than replacing them: `long double _Complex`
                # and `_Complex double` are the same type, and `_BASIC`
                # would need three more entries for each element to say so.
                complex_ = True
                spec.explicit = True
                self.next()
                continue
            if word == "_Imaginary":
                # NOT A GAP, AND NOT REFUSABLE ELSEWHERE. Imaginary types are
                # Annex G, which is CONDITIONAL: an implementation supports
                # them only if it defines `__STDC_IEC_559_COMPLEX__`, which
                # this one does not -- and neither does gcc, which has never
                # had them. `_Complex` says everything they can say, and
                # `_Complex_I` is how you write i.
                self.sema.error("E1214", "`_Imaginary` is not supported",
                                t.span,
                                note="imaginary types are Annex G, which an "
                                     "implementation may leave out; this one "
                                     "does, as gcc does",
                                help="use `_Complex` and `_Complex_I`: "
                                     "`double _Complex z = 2.0 * I;`")
                self.next()
                continue
            if made is None and not words and self.is_typedef(t):
                sym = self.sema.scope.lookup(t.text)
                made = sym.type.named(t.text)
                spec.explicit = True
                self.next()
                continue
            if word == "__attribute__" or word == "__attribute":
                self._attributes()
                continue
            break
        if "noreturn" in attrs or "_Noreturn" in attrs:
            # THE SAME THING AS `_Noreturn`, which is what C23 says: the two
            # spellings name one promise, and a program may write either.
            spec.noreturn = True
        spec.attrs = attrs
        if bit_width is not None:
            # `signed` AND `unsigned` COMBINE WITH IT and nothing else does:
            # `long _BitInt(8)` is not a type, and letting `_BASIC` see the
            # words would have answered `long` and dropped the width.
            if words not in ([], ["signed"], ["unsigned"]):
                self.sema.error(
                    "E1281",
                    f"`{' '.join(words)} _BitInt({bit_width})` is not a type",
                    start,
                    note="only `signed` and `unsigned` go with `_BitInt`")
            signed_bits = words != ["unsigned"]
            if signed_bits and bit_width < 2:
                # ONE BIT AND A SIGN LEAVES NO VALUE BITS. C says a signed
                # bit-precise type is at least 2 wide and an unsigned one at
                # least 1, which is the same sentence read twice.
                self.sema.error(
                    "E1284", f"`_BitInt({bit_width})` needs a bit for the "
                             f"sign and one for a value", start,
                    help="`unsigned _BitInt(1)` is a type; the signed one "
                         "starts at 2")
                bit_width = 2
            made = C.bitint(bit_width, signed=signed_bits)
            words = []
        if words:
            key = tuple(sorted(words))
            basic = _BASIC.get(key)
            if basic is None:
                self.sema.error("E1215",
                                f"`{' '.join(words)}` is not a type", start)
                basic = C.INT
            if made is not None:
                self.sema.error("E1216", "two type specifiers", start)
            made = basic
        # C23's `auto`: WITH NO TYPE SPECIFIER IT IS NOT A STORAGE CLASS,
        # it is a request to take the type from the initialiser. `auto int x`
        # is still the old keyword, which has meant nothing since C89 gave
        # every block-scope object automatic storage by default -- so the
        # test is that no specifier named a type, which is what `made is
        # None` says here and nowhere later.
        spec.infer = (spec.storage is Storage.AUTO and not spec.explicit
                      and made is None and not complex_)
        if made is None:
            made = C.INT
        if complex_:
            # `_Complex` ALONE IS `_Complex double`. C's grammar lists the
            # three spellings with a real floating type in them, so a bare
            # one is a constraint violation -- but gcc reads it as `double`,
            # every header that uses it means that, and refusing would be a
            # diagnostic about nothing.
            if made.is_float:
                made = C.complex_of(made)
            elif not spec.explicit or made.kind is C.K.INT:
                made = C.CDOUBLE
            else:
                self.sema.error(
                    "E1217",
                    f"`{C.spell(made)} _Complex` is not a complex type", start,
                    note="the element of a complex type is `float`, `double` "
                         "or `long double`")
                made = C.CDOUBLE
        if "restrict" in quals and not (made.is_pointer or made.is_array):
            # `restrict` IS A PROMISE ABOUT A POINTER and means nothing on
            # anything else, which is why C makes it a constraint violation
            # rather than a no-op. An ARRAY passes: a parameter declared
            # `int a[restrict 4]` is a restrict-qualified pointer.
            self.sema.error(
                "E1218", f"`restrict` needs a pointer, not {C.spell(made)}",
                start, help="drop the `restrict`")
            quals = quals - {"restrict"}
        spec.type = made.qualified(quals) if quals else made
        return spec

    def _at_attribute(self) -> bool:
        """`[[`, WHICH IS TWO TOKENS AND NOT ONE. It has to be: `a[[0]]` is
        a subscript by an array element and C23 did not break it, so the
        opening of an attribute is recognised by the pair and nothing in the
        lexer is allowed to join them."""
        return self.tok.is_punct("[") and self.peek().is_punct("[")

    def _skip_attributes(self) -> set[str]:
        """Both spellings, in any order, and what the C23 ones said.

        ONE CALL SITE PER PLACE C ALLOWS THEM, and there are a dozen -- a
        declaration, a declarator, a member, an enumerator, a parameter, a
        statement, a label. `__attribute__` was already at most of them;
        this answers the standard names on top of that.
        """
        seen: set[str] = set()
        while True:
            if self.at("__attribute__", "__attribute"):
                self._attributes()
            elif self._at_attribute():
                seen |= self._std_attributes()
            else:
                return seen

    def _std_attributes(self) -> set[str]:
        """`[[a, b::c(...), d]]`, one or more sequences of them.

        WHAT COMES BACK IS THE STANDARD NAMES, unprefixed: the caller acts on
        `noreturn` and on `nodiscard`, and a prefixed name is somebody else's
        and is dropped here. `attributes.py` says why an unknown one is a
        warning rather than an error.
        """
        from .attributes import STANDARD
        seen: set[str] = set()
        while self._at_attribute():
            self.next()
            self.next()
            while not (self.at("]") or self.tok.kind is Kind.EOF):
                if self.eat(","):
                    continue            # an empty item is allowed
                if self.tok.kind is not Kind.IDENT:
                    self.sema.error("E1219", "expected an attribute name",
                                    self.tok.span)
                    break
                first = self.next()
                name, prefixed = first.text, False
                if self.at("::"):
                    self.next()
                    prefixed = True
                    if self.tok.kind is Kind.IDENT:
                        self.next()
                if self.at("("):
                    self._balanced()
                if prefixed:
                    continue            # a vendor's, and not this one
                if name in STANDARD:
                    seen.add(name)
                    continue
                self.sema.warn(
                    "W1218", f"ignoring unknown attribute `{name}`",
                    first.span,
                    note="the unprefixed attribute names are the "
                         "standard's, and this is not one of them",
                    help="a vendor's attribute goes in its own namespace, "
                         "like `[[gnu::packed]]`, which is ignored quietly")
            self.expect("]")
            self.expect("]")
        return seen

    def _balanced(self) -> None:
        """A parenthesised token sequence, skipped. Nesting counted."""
        depth = 0
        while self.tok.kind is not Kind.EOF:
            if self.at("("):
                depth += 1
            elif self.at(")"):
                depth -= 1
                if depth == 0:
                    self.next()
                    return
            self.next()

    def _bitint_width(self) -> int:
        """`_BitInt ( N )` -- the N, checked.

        THE WIDTH IS PART OF THE TYPE and has to be known here, so it is an
        integer constant expression like an array bound rather than anything
        that could depend on a value. A SIGNED one needs two bits, because
        one of them is the sign and a type with no value bits is not a type;
        an unsigned one needs one. `ctype.BITINT_MAXWIDTH` says why 64 is the
        top, and C23 requires only that it be at least that.
        """
        self.next()
        at = self.tok.span
        self.expect("(", "`_BitInt` takes a width in parentheses")
        got = fold_int(self.conditional())
        self.expect(")")
        if got is None:
            self.sema.error("E1282", "`_BitInt` needs a constant width", at,
                            note="the width is part of the type, so it has "
                                 "to be known at compile time")
            return C.BITINT_MAXWIDTH
        if got > C.BITINT_MAXWIDTH:
            self.sema.error(
                "E1283",
                f"`_BitInt({got})` is wider than this implementation's "
                f"{C.BITINT_MAXWIDTH}", at,
                note="a wider one would be arithmetic in software over "
                     "several machine registers, which is what `long double` "
                     "already costs and what a bit-precise integer is for "
                     "avoiding",
                help=f"`BITINT_MAXWIDTH` is {C.BITINT_MAXWIDTH}, which C23 "
                     f"permits: it requires only ULLONG_WIDTH")
            return C.BITINT_MAXWIDTH
        if got < 1:
            self.sema.error("E1285", f"`_BitInt({got})` has no width", at,
                            note="a width is at least 1, and at least 2 with "
                                 "a sign bit to pay for")
            return C.BITINT_MAXWIDTH
        return got

    def _attributes(self) -> None:
        """`__attribute__((...))`, parsed and discarded.

        DISCARDED AND NOT REFUSED. Every system header is full of them and
        almost all are advisory (`__pure__`, `__nonnull__`, `__deprecated__`).
        The ones that are not -- `packed`, `aligned`, `section` -- would
        change layout, and a frontend that silently ignored those would
        produce a struct that does not match the one the header describes. So
        those are named and reported; see `_attribute_names`.
        """
        self.next()
        if not self.eat("("):
            return
        depth = 1
        names: list[Token] = []
        while depth and self.tok.kind is not Kind.EOF:
            if self.at("("):
                depth += 1
            elif self.at(")"):
                depth -= 1
                if depth == 0:
                    self.next()
                    break
            elif self.tok.kind is Kind.IDENT:
                names.append(self.tok)
            self.next()
        for n in names:
            bare = n.text.strip("_")
            if bare in ("packed", "aligned", "section", "vector_size",
                        "transparent_union", "mode"):
                self.sema.warn(
                    "W1217", f"ignoring `__attribute__((...{bare}...))`",
                    n.span,
                    note="this attribute changes layout or placement, and "
                         "this frontend does not implement it",
                    help="the resulting object may not match what the "
                         "declaring header describes")

    def struct_or_union(self) -> CType:
        kw = self.next()
        kind = C.K.STRUCT if _kw(kw.text) == "struct" else C.K.UNION
        self._skip_attributes()
        name: str | None = None
        if self.tok.kind is Kind.IDENT and not self.tok.is_keyword:
            name = self.next().text
        if not self.at("{"):
            if name is None:
                self.sema.error("E1218",
                                f"expected a name or `{{` after `{kw.text}`",
                                self.tok.span)
                return C.INT
            tag = self.sema.scope.lookup_tag(name)
            if tag is None or tag.kind is not kind:
                # A REFERENCE DECLARES THE TAG in the current scope when it is
                # not already visible -- `struct s *p;` is legal with no
                # definition anywhere, and creates an incomplete type.
                tag = C.Tag(kind, name, span=kw.span)
                self.sema.scope.declare_tag(tag)
            return C.record(tag)
        # A definition. `struct s { ... }` in an inner scope declares a NEW
        # tag even if an outer one has the same name.
        tag = self.sema.scope.lookup_tag(name, here=True) if name else None
        if tag is None or tag.kind is not kind:
            tag = C.Tag(kind, name, span=kw.span)
            self.sema.scope.declare_tag(tag)
        elif tag.complete:
            self.sema.error("E1219", f"{kw.text} {name} is defined twice",
                            kw.span,
                            also=(tag.span, "the first definition")
                            if tag.span else None)
        self.next()
        if self.sema.scope.is_function_prototype:
            # A TAG DEFINED IN A PARAMETER LIST goes out of scope with the
            # list, so nothing outside can name the type and no caller can
            # make one to pass. The declaration compiles and is useless,
            # which is worth a word -- gcc says the same.
            self.sema.warn(
                "W1222",
                f"this `{kw.text}` is declared inside a parameter list",
                kw.span,
                note="its scope ends with the list, so no caller can name "
                     "the type")
        before = len(self.sema.sink.diagnostics)
        self._members(tag)
        # AND NOT AFTER A MEMBER WAS REJECTED. A body whose only member was
        # refused is empty because of that refusal, and saying so a second
        # time in different words sends the reader looking for a second
        # mistake.
        if not tag.members and len(self.sema.sink.diagnostics) == before:
            # THE THIRD GNU EXTENSION IN THIS FILE, and the same treatment:
            # C requires a member list to declare at least one member, and
            # an empty one is a real thing to want -- a tag used only as a
            # marker. `sizeof` of it is 0 here, as it is in gcc.
            self.sema.warn(
                "W1221", f"this `{kw.text}` has no members", kw.span,
                note="C requires at least one member; this is an extension")
        self.expect("}", f"this `{kw.text}` body is never closed")
        self._skip_attributes()
        C.layout(tag)
        return C.record(tag)

    def _members(self, tag: C.Tag) -> None:
        while not self.at("}") and self.tok.kind is not Kind.EOF:
            # NEVER SPIN. Every loop in this parser that is bounded by a
            # closing token needs this: if one pass consumes nothing -- which
            # a malformed member or a bug in a declarator can both cause --
            # the loop runs for ever and the compiler HANGS, which is the one
            # failure a user cannot diagnose. `translation_unit` has had the
            # same guard since the first version; this one had not, and a
            # mis-indented `return` in `struct_or_union` found that out.
            before = self.i
            if self.at_kw("_Static_assert"):
                self._static_assert()
                continue
            spec = self.declaration_specifiers(allow_storage=False)
            if self.at(";"):
                self.next()
                # An anonymous struct or union member: its members are
                # reached as if they were members of the enclosing one.
                if spec.type.is_record and spec.type.tag and \
                        spec.type.tag.name is None:
                    tag.members.append(C.Member(None, spec.type,
                                                span=spec.span))
                else:
                    self.sema.warn("W1220",
                                   "declaration declares nothing", spec.span)
                continue
            while True:
                name, build, span = self._declarator(abstract=True)
                ty = build(spec.type)
                bits: int | None = None
                if self.eat(":"):
                    bits = self._bitfield_width(ty, span)
                    if spec.align is not None:
                        # A BIT-FIELD HAS NO ADDRESS, so there is nothing for
                        # an alignment to be. C forbids it outright and so
                        # does this -- unlike the three extensions above,
                        # there is no reading of it that means anything.
                        self.sema.error(
                            "E1225", "a bit-field cannot be `_Alignas`",
                            span,
                            note="alignment is a property of an address, and "
                                 "a bit-field has not got one")
                self._skip_attributes()
                if name is None and bits is None:
                    self.sema.error("E1221", "expected a member name", span)
                elif _ends_with_flexible(tag):
                    # ASKED OF THE MEMBER LIST, not of `tag.flexible`: that
                    # flag is set by `layout`, which has not run yet -- so the
                    # check never fired and a struct with a member after its
                    # flexible array compiled without a word.
                    self.sema.error(
                        "E1222",
                        "a flexible array member must be the last member",
                        span)
                else:
                    self._check_member(tag, name, ty, span, bits, spec.align)
                if not self.eat(","):
                    break
            self.expect(";", "a member declaration ends with `;`")
            if self.i == before:
                self.next()

    def _bitfield_width(self, ty: CType, span: Span) -> int:
        got = fold_int(self.conditional())
        if got is None:
            self.sema.error("E1223", "a bit-field width must be a constant",
                            span)
            return 1
        if not ty.is_integer:
            self.sema.error("E1224",
                            f"a bit-field must have an integer type, not "
                            f"{C.spell(ty)}", span)
            return 1
        if got < 0 or got > ty.bits:
            self.sema.error("E1225",
                            f"a bit-field of {C.spell(ty)} can be 0 to "
                            f"{ty.bits} bits, not {got}", span)
            return 1
        return got

    def _check_member(self, tag: C.Tag, name: str | None, ty: CType,
                      span: Span, bits: int | None,
                      align: int | None = None) -> None:
        if name is not None and any(m.name == name for m in tag.members):
            self.sema.error("E1226", f"duplicate member {name!r}", span)
            return
        if ty.is_function:
            self.sema.error("E1227",
                            f"a member cannot be a function; {name!r} has "
                            f"type {C.spell(ty)}", span,
                            help="use a function pointer")
            return
        if ty.is_vla:
            # A STRUCTURE HAS ONE LAYOUT, decided when the type is, and a
            # member whose length is an expression has not got one. C
            # forbids a variably modified member outright; this reached
            # `layout`, which asks every member for its size, and raised.
            self.sema.error(
                "E1248",
                f"a member cannot have a variable length; {name!r} has "
                f"type {C.spell(ty)}", span,
                note="a structure's layout is fixed when its type is",
                help="use a pointer and allocate the elements")
            return
        if ty.is_array and ty.count is None and not ty.is_vla:
            if tag.kind is C.K.UNION or not tag.members:
                self.sema.error(
                    "E1228",
                    "a flexible array member needs at least one member "
                    "before it, in a struct", span)
                return
        elif not ty.complete:
            self.sema.error("E1229",
                            f"member {name!r} has the incomplete type "
                            f"{C.spell(ty)}", span,
                            note="a struct cannot contain itself; use a "
                                 "pointer")
            return
        tag.members.append(C.Member(name, ty, bits=bits, span=span,
                                    align=align))

    def enum_specifier(self) -> CType:
        kw = self.next()
        name: str | None = None
        if self.tok.kind is Kind.IDENT and not self.tok.is_keyword:
            name = self.next().text
        base: CType | None = None
        if self.eat(":"):
            base = self.declaration_specifiers(allow_storage=False).type
        if not self.at("{"):
            if name is None:
                self.sema.error("E1230", "expected a name or `{` after `enum`",
                                self.tok.span)
                return C.INT
            tag = self.sema.scope.lookup_tag(name)
            if tag is None or tag.kind is not C.K.ENUM:
                tag = C.Tag(C.K.ENUM, name, span=kw.span, base=base or C.INT)
                self.sema.scope.declare_tag(tag)
            return C.record(tag)
        tag = self.sema.scope.lookup_tag(name, here=True) if name else None
        if tag is None or tag.kind is not C.K.ENUM:
            tag = C.Tag(C.K.ENUM, name, span=kw.span)
            self.sema.scope.declare_tag(tag)
        elif tag.complete:
            self.sema.error("E1219", f"enum {name} is defined twice", kw.span)
        self.next()
        ty = C.record(tag)
        value = 0
        lo = hi = 0
        while not self.at("}") and self.tok.kind is not Kind.EOF:
            n = self.tok
            if n.kind is not Kind.IDENT or n.is_keyword:
                self.sema.error("E1231", "expected an enumeration constant",
                                n.span)
                break
            self.next()
            self._skip_attributes()
            if self.eat("="):
                got = fold_int(self.conditional())
                if got is None:
                    self.sema.error("E1232",
                                    "an enumeration value must be a constant",
                                    n.span)
                else:
                    value = got
            if base is not None and not _fits_in(value, base):
                # A FIXED UNDERLYING TYPE IS A PROMISE ABOUT THE VALUES, so
                # one that does not fit is the declaration contradicting
                # itself rather than a type to be widened.
                self.sema.error(
                    "E1289",
                    f"the enumerator {n.text!r} is {value}, which "
                    f"{C.spell(base)} cannot hold", n.span,
                    note=f"this `enum` has a fixed underlying type")
                value = 0
            tag.values[n.text] = value
            lo, hi = min(lo, value), max(hi, value)
            existing = self.sema.scope.names.get(n.text)
            if existing is not None:
                self.sema.error("E1233", f"{n.text!r} is declared twice",
                                n.span,
                                also=(existing.span, "the first declaration")
                                if existing.span else None)
            sym = Symbol(n.text, ty, Storage.ENUM_CONST, span=n.span,
                         value=value)
            self.sema.scope.declare(sym)
            value += 1
            if not self.eat(","):
                break
        self.expect("}", "this `enum` body is never closed")
        # THE UNDERLYING TYPE IS CHOSEN FROM THE VALUES, which is what makes
        # `sizeof(enum e)` 4 for ordinary enumerations and 8 for one holding a
        # value that does not fit. An explicit `: T` wins.
        if base is None:
            if lo >= 0 and hi <= 0x7FFFFFFF:
                base = C.INT
            elif lo >= -(1 << 31) and hi <= 0x7FFFFFFF:
                base = C.INT
            elif lo >= 0 and hi <= (1 << 64) - 1:
                base = C.ULONG
            else:
                base = C.LONG
        tag.base = base
        tag.complete = True
        tag.size = base.size
        tag.align = base.align
        return ty

    # ── declarators ─────────────────────────────────────────────────────────
    def _declarator(self, *, abstract: bool
                    ) -> tuple[str | None, Callable[[CType], CType], Span]:
        """Returns (name, build, span). See the module docstring."""
        start = self.tok.span
        self._skip_attributes()
        if self.at("*"):
            self.next()
            quals: set[str] = set()
            while self.tok.kind is Kind.IDENT and _kw(self.tok.text) in _QUALIFIERS:
                quals.add(_kw(self.next().text))
            name, inner, span = self._declarator(abstract=abstract)
            return (name, lambda base: inner(C.pointer_to(base, quals)),
                    start.to(span) if span.file is start.file else start)
        return self._direct_declarator(abstract=abstract, start=start)

    def _direct_declarator(self, *, abstract: bool, start: Span):
        name: str | None = None
        core: Callable[[CType], CType] = _same_type
        span = start
        if self.tok.kind is Kind.IDENT and not self.tok.is_keyword and \
                not self.is_typedef(self.tok):
            t = self.next()
            name, span = t.text, t.span
        elif self.at("(") and (self._nested_declarator()):
            self.next()
            name, core, span = self._declarator(abstract=abstract)
            self.expect(")", "this declarator's `(` is never closed")
        elif not abstract:
            self.sema.error("E1234", "expected a name in this declaration",
                            self.tok.span)
        suffix = self._suffixes()
        return name, (lambda base: core(suffix(base))), span

    def _nested_declarator(self) -> bool:
        """Whether a `(` here opens a nested declarator or a parameter list.

        `int (x)` declares `x`; `int (void)` is a function type. The test is
        whether what follows could start a parameter declaration -- and an
        empty `()` is a parameter list, not a declarator.
        """
        after = self.peek()
        if after.is_punct(")"):
            return False
        if after.is_punct("*", "(", "["):
            return True
        if after.kind is Kind.IDENT and not self._type_starts(after):
            return True
        return False

    def _suffixes(self) -> Callable[[CType], CType]:
        if self._at_attribute():
            # `int a [[maybe_unused]]` AND NOT AN ARRAY OF `[maybe_unused]`.
            # Nothing else can follow a declarator with `[` `[`, because a
            # `[` cannot begin an expression -- so C23 could take the pair
            # here without breaking an array bound.
            self._std_attributes()
            return self._suffixes()
        if self.at("["):
            open_tok = self.next()
            # `int a[static 3]` and `int a[const 4]` -- legal only in a
            # parameter, where the brackets describe the POINTER the array
            # decays to. Both are accepted and neither is represented:
            # `static` is a promise about the caller that changes no code
            # here, and the qualifiers would have to land on the pointer
            # rather than the element, which is the same field `const A x`
            # (for an array typedef) needs for the OPPOSITE meaning. The cost
            # is accepting `a = p` inside a function that wrote `int a[const
            # 4]`, which gcc refuses; laxity, not a wrong answer.
            while self.tok.kind is Kind.IDENT and (
                    _kw(self.tok.text) in _QUALIFIERS
                    or _kw(self.tok.text) == "static"):
                self.next()
            count: int | None = None
            vla: S.Expr | None = None
            if self.at("*") and self.peek().is_punct("]"):
                self.next()             # `[*]`, an unspecified VLA bound
                vla = S.IntLit(open_tok.span, C.SIZE_T, False, 1)
            elif not self.at("]"):
                size = self.assignment()
                got = fold_int(size)
                if got is None:
                    vla = self.sema.lvalue_conversion(size)
                    if not vla.type.is_integer:
                        self.sema.error(
                            "E1235",
                            f"an array size must be an integer, not "
                            f"{C.spell(vla.type)}", size.span)
                        vla = None
                        count = 1
                elif got < 0:
                    self.sema.error("E1236", f"array size {got} is negative",
                                    size.span)
                    count = 1
                else:
                    if got == 0:
                        # A CONSTRAINT VIOLATION AND NOT AN ERROR HERE, the
                        # way gcc has it: C says the bound "shall have a
                        # value greater than zero", and a zero-length array
                        # is a GNU extension this frontend keeps -- struct
                        # hacks in real headers use it. A diagnostic is what
                        # C asks for and a warning is one.
                        self.sema.warn(
                            "W1236", "an array of no elements", size.span,
                            note="C requires an array bound greater than "
                                 "zero; this is an extension")
                    count = got
            self.expect("]", "this `[` is never closed")
            rest = self._suffixes()

            def build_array(base: CType, count=count, vla=vla) -> CType:
                inner = rest(base)
                if inner.unqualified().is_void:
                    self.sema.error(
                        "E1238", "an array of `void`", open_tok.span,
                        note="every element of an array has a size, and "
                             "`void` has not got one")
                    return C.array_of(C.CHAR, count)
                if inner.is_function:
                    self.sema.error("E1237", "an array of functions", open_tok.span,
                                    help="use an array of function pointers")
                    return inner
                return C.array_of(inner, count, vla)
            return build_array
        if self.at("("):
            self.next()
            params, variadic, kr = self._parameters()
            self.expect(")", "this parameter list's `(` is never closed")
            rest = self._suffixes()

            def build_function(base: CType) -> CType:
                inner = rest(base)
                if inner.is_function:
                    self.sema.error("E1238",
                                    "a function cannot return a function",
                                    self.tok.span,
                                    help="return a function pointer")
                if inner.is_array:
                    self.sema.error("E1239",
                                    "a function cannot return an array",
                                    self.tok.span)
                return C.function(inner, None if kr else tuple(params),
                                  variadic,
                                  tuple(p.name for p in params if p.name)
                                  if kr else ())
            return build_function
        return lambda base: base

    def _parameters(self) -> tuple[list[C.Param], bool, bool]:
        """A parameter list. `kr` is True for the unprototyped `f()` form."""
        if self.at(")"):
            return [], False, True
        # `(void)` -- a prototype with no parameters, distinct from `()`.
        if self.at_kw("void") and self.peek().is_punct(")"):
            self.next()
            return [], False, False
        # K&R: `f(a, b)` where a and b are not types.
        if self.tok.kind is Kind.IDENT and not self._type_starts(self.tok):
            names: list[C.Param] = []
            while True:
                t = self.tok
                if t.kind is not Kind.IDENT:
                    break
                self.next()
                names.append(C.Param(t.text, C.INT, t.span))
                if not self.eat(","):
                    break
            return names, False, True
        params: list[C.Param] = []
        variadic = False
        self.sema.push(prototype=True)
        try:
            while True:
                if self.at("..."):
                    self.next()
                    variadic = True
                    break
                spec = self.declaration_specifiers()
                name, build, span = self._declarator(abstract=True)
                # `int a [[maybe_unused]]` -- after the declarator as well as
                # before the specifiers, which C allows for a parameter and
                # for nothing else in a parameter list.
                self._skip_attributes()
                ty = build(spec.type)
                # A PARAMETER'S ARRAY TYPE IS A POINTER, 6.7.6.3p7. `int a[4]`
                # as a parameter is `int *a`, which is why `sizeof a` inside
                # the function is 8 and not 16, and why the two spellings
                # declare the same function.
                ty = C.decay(ty)
                if spec.storage not in (None, Storage.REGISTER):
                    self.sema.error("E1240",
                                    "a parameter cannot have a storage class",
                                    span)
                if name and self.sema.scope.names.get(name) is not None:
                    self.sema.error("E1276", f"{name!r} is declared twice",
                                    span)
                # THE SYMBOL TRAVELS WITH THE PARAMETER. A variable-length
                # parameter type names an EARLIER PARAMETER -- `int m[n][n]`
                # -- and the expression that does so captured the symbol from
                # this prototype scope. A definition that made fresh symbols
                # for its body would leave that expression reading a
                # parameter with no storage in the function it is used in.
                psym = (Symbol(name, ty, Storage.PARAM, span=span)
                        if name else None)
                params.append(C.Param(name, ty, span, psym))
                if psym is not None:
                    self.sema.scope.declare(psym)
                if not self.eat(","):
                    break
        finally:
            self.sema.pop()
        if len(params) == 1 and params[0].name is None and params[0].type.is_void:
            return [], False, False
        for p in params:
            if p.type.is_void:
                self.sema.error("E1241",
                                "a parameter cannot have type `void`",
                                p.span or self.tok.span)
        return params, variadic, False

    # ── initialisers ────────────────────────────────────────────────────────
    def _at_compound_literal(self) -> bool:
        """`(T){` -- which a cast and a parenthesised expression both look
        like until the token after the `)`."""
        if not self.at("(") or not self._type_starts(self.peek()):
            return False
        depth = 0
        j = self.i
        while j < len(self.toks) and self.toks[j].kind is not Kind.EOF:
            if self.toks[j].is_punct("("):
                depth += 1
            elif self.toks[j].is_punct(")"):
                depth -= 1
                if depth == 0:
                    return (j + 1 < len(self.toks)
                            and self.toks[j + 1].is_punct("{"))
            j += 1
        return False

    def _splice_compound(self, ty: CType, offset: int, e: S.Expr,
                         out: S.Init, member: C.Member | None) -> bool:
        """`= (T){...}` -- the literal's own entries, at this offset.

        AN INITIALISER IS NOT A COPY OF AN OBJECT, it is the bytes. C says
        the value of a compound literal initialising an object of its own
        type is that object's value, and a nested initialiser is exactly the
        flat list `S.Init` already holds -- so splicing leaves one shape for
        `_check_static_init` and `_init_bytes` to understand rather than two,
        and makes `static struct P p = (struct P){1, 2};` the constant it is.

        NOT FOR AN ARRAY, because `int a[3] = (int[]){1, 2, 3};` is not C:
        an array is not assignable from an expression, and splicing would
        quietly accept it. An ARRAY literal's address is the constant there,
        and `fold` answers that one. Not for a bit-field either -- the width
        lives on the entry, and a spliced one would not carry it.
        """
        if ty.is_array or (member is not None and member.bits is not None):
            return False
        inner = e
        while isinstance(inner, S.Conv) and inner.operand is not None:
            inner = inner.operand
        if not isinstance(inner, S.CompoundLiteral):
            return False
        if not C.compatible(inner.type.unqualified(), ty.unqualified()):
            return False
        for ent in inner.init.entries:
            out.entries.append(S.InitEntry(offset + ent.offset, ent.type,
                                           ent.value, ent.bits,
                                           ent.bit_offset, ent.data))
        return True

    def initializer(self, ty: CType, span: Span) -> S.Init:
        out = S.Init(span)
        if self.at("{"):
            self.next()
            self._braced(ty, 0, out)
            self.expect("}", "this initialiser's `{` is never closed")
        elif ty.is_array:
            if self.tok.kind is Kind.STRING:
                self._string_init(ty, 0, out)
            else:
                self.sema.error("E1242",
                                f"an initialiser for {C.spell(ty)} needs "
                                f"braces", self.tok.span)
                self.assignment()
        else:
            self._scalar(ty, 0, out, None)
        out.size = max((e.offset + _entry_size(e) for e in out.entries),
                       default=0)
        return out

    def _scalar(self, ty: CType, offset: int, out: S.Init,
                member: C.Member | None) -> None:
        # A SCALAR'S INITIALISER MAY BE BRACED: `int x = {1};` is legal, and
        # so is the innermost brace of an elided nest.
        braced = bool(self.eat("{"))
        e = self.assignment()
        if self._splice_compound(ty, offset, e, out, member):
            converted = None            # spliced; there is nothing to add
        else:
            converted = self.sema.assignable(ty, e, "initialisation", e.span)
        if converted is not None:
            out.entries.append(S.InitEntry(
                offset, ty, converted,
                member.bits if member else None,
                member.bit_offset if member else 0))
        if braced:
            while self.eat(","):
                if self.at("}"):
                    break
                self.sema.warn("W1243", "excess elements in initialiser",
                               self.tok.span)
                self.assignment()
            self.expect("}")

    def _string_init(self, ty: CType, offset: int, out: S.Init) -> None:
        lit = self._string()
        if not isinstance(lit, S.StringLit):
            return
        elem = ty.of.unqualified()
        if not (elem.is_integer and elem.size == _prefix_info(lit.prefix)[0]):
            self.sema.error("E1244",
                            f"cannot initialise {C.spell(ty)} from a "
                            f"{lit.prefix or 'narrow'} string literal", lit.span)
            return
        data = lit.data
        if ty.count is None:
            ty_count = len(data) // elem.size
        else:
            ty_count = ty.count
            want = ty_count * elem.size
            if len(data) - elem.size > want:
                self.sema.error(
                    "E1245",
                    f"this string is {len(data) // elem.size - 1} characters, "
                    f"which does not fit in {C.spell(ty)}", lit.span)
                return
            # `char s[3] = "abc"` is legal: the terminator is dropped.
            data = data[:want]
        out.entries.append(S.InitEntry(offset, C.array_of(elem, ty_count),
                                       None, data=data))

    def _braced(self, ty: CType, offset: int, out: S.Init) -> None:
        """Inside `{ ... }` for `ty`. Consumes up to but not the `}`."""
        if not ty.is_array and not ty.is_record:
            # `int x = {1, 2}` -- braces around a scalar hold exactly one.
            if not self.at("}"):
                e = self.assignment()
                got = self.sema.assignable(ty, e, "initialisation", e.span)
                if got is not None:
                    out.entries.append(S.InitEntry(offset, ty, got))
            while self.eat(","):
                if self.at("}"):
                    break
                self.sema.warn("W1243", "excess elements in initialiser",
                               self.tok.span)
                self.assignment()
            return
        index = 0
        highest = 0
        while not self.at("}") and self.tok.kind is not Kind.EOF:
            if self.at("[", "."):
                picked = self._designation(ty, offset)
                if picked is None:
                    # THE DESIGNATOR WAS REJECTED, and the rest of this entry
                    # still has to be CONSUMED. Returning here left the `= 1`
                    # and everything after it to the enclosing declaration,
                    # which then reported seven more errors about the one
                    # mistake -- and the seventh is what the reader sees.
                    self.eat("=")
                    self._skip_initializer()
                    if not self.eat(","):
                        break
                    continue
                index, sub_ty, sub_off, member = picked
                self.expect("=", "a designator is followed by `=`")
            else:
                got = _element(ty, index)
                if got is None:
                    self.sema.warn("W1243", "excess elements in initialiser",
                                   self.tok.span)
                    self._skip_initializer()
                    if not self.eat(","):
                        break
                    continue
                sub_ty, rel, member = got
                sub_off = offset + rel
            if sub_ty.is_array and sub_ty.count is None and not sub_ty.is_vla:
                # A FLEXIBLE ARRAY MEMBER HAS NO ELEMENTS until something
                # allocates room for them, so there is nowhere for an
                # initialiser to put anything. C forbids it and gcc reports
                # it; the struct hack works by allocating and assigning.
                self.sema.error(
                    "E1302", "a flexible array member cannot be initialised",
                    self.tok.span,
                    note="it has no elements until an allocation gives it "
                         "some", help="allocate the room and assign after")
                self._skip_initializer()
                if not self.eat(","):
                    break
                continue
            self._one(sub_ty, sub_off, out, member)
            index += 1
            highest = max(highest, index)
            if not self.eat(","):
                break
        if ty.is_array and ty.count is None:
            # `int a[] = {1,2,3}` -- the bound is the highest index reached.
            out.size = max(out.size, highest * ty.of.size)

    def _one(self, ty: CType, offset: int, out: S.Init,
             member: C.Member | None) -> None:
        """One initialiser for `ty`, braced, elided or scalar."""
        if self.at("{"):
            self.next()
            self._braced(ty, offset, out)
            self.expect("}", "this initialiser's `{` is never closed")
            return
        if ty.is_array and self.tok.kind is Kind.STRING:
            self._string_init(ty, offset, out)
            return
        if ty.is_array or ty.is_record:
            if ty.is_record and self._at_compound_literal():
                # `{ (struct point){8, 9}, 10 }` -- a member of struct type
                # initialised by a compound literal of THAT type, which is
                # not brace elision: elision would read the `8` as the first
                # scalar inside it and complain about the `struct point`.
                self._scalar(ty, offset, out, member)
                return
            self._elided(ty, offset, out)
            return
        self._scalar(ty, offset, out, member)

    def _elided(self, ty: CType, offset: int, out: S.Init) -> None:
        """Fill an aggregate from initialisers that have no braces of their own.

        `int a[2][2] = {1,2,3,4}` -- the inner arrays take two each. The rule
        is that an aggregate with no brace consumes exactly as many
        initialisers as it has elements, and then STOPS without eating the
        comma that follows: that comma belongs to whoever is filling the
        enclosing object.
        """
        index = 0
        while self.tok.kind is not Kind.EOF and not self.at("}"):
            if self.at("[", "."):
                return              # a designator returns to the outer level
            got = _element(ty, index)
            if got is None:
                return
            sub_ty, rel, member = got
            self._one(sub_ty, offset + rel, out, member)
            index += 1
            if _element(ty, index) is None:
                return              # full
            if not self.at(","):
                return
            self.next()

    def _designation(self, ty: CType, offset: int):
        """`[3]` and `.name`, possibly chained. Returns (next index, type,
        offset, member)."""
        index = 0
        cur = ty
        cur_off = offset
        member: C.Member | None = None
        while True:
            if self.at("["):
                open_tok = self.next()
                low = fold_int(self.conditional())
                high = None
                if self.at("..."):
                    self.next()
                    high = fold_int(self.conditional())
                self.expect("]")
                if not cur.is_array or low is None:
                    self.sema.error("E1246",
                                    f"`[...]` designates an array element, and "
                                    f"this is {C.spell(cur)}", open_tok.span)
                    return None
                if low < 0:
                    self.sema.error(
                        "E1247",
                        f"element {low} is before the start of "
                        f"{C.spell(cur)}", open_tok.span)
                    return None
                if cur.count is not None and low >= cur.count:
                    self.sema.error(
                        "E1247",
                        f"element {low} is past the end of {C.spell(cur)}",
                        open_tok.span)
                    return None
                if high is not None:
                    self.sema.warn(
                        "W1248",
                        "a designated range initialises only its first element",
                        open_tok.span,
                        note="`[a ... b] = v` is a GNU extension")
                index = low
                cur_off += low * cur.of.size
                cur = cur.of
                member = None
            elif self.at("."):
                dot = self.next()
                name = self.tok
                if name.kind is not Kind.IDENT:
                    self.sema.error("E1249", "expected a member name after `.`",
                                    name.span)
                    return None
                self.next()
                if not cur.is_record:
                    self.sema.error("E1250",
                                    f"`.{name.text}` designates a member, and "
                                    f"this is {C.spell(cur)}", dot.span)
                    return None
                path = C.find_member(cur, name.text)
                if path is None:
                    self.sema.error("E1432",
                                    f"{C.spell(cur)} has no member named "
                                    f"{name.text!r}", name.span)
                    return None
                for m in path:
                    cur_off += m.offset
                member = path[-1]
                index = cur.tag.members.index(path[0])
                cur = member.type
            else:
                break
        return index, cur, cur_off, member

    def _skip_initializer(self) -> None:
        depth = 0
        while self.tok.kind is not Kind.EOF:
            if self.at("{"):
                depth += 1
            elif self.at("}"):
                if depth == 0:
                    return
                depth -= 1
            elif self.at(",") and depth == 0:
                return
            self.next()

    # ── statements ──────────────────────────────────────────────────────────
    def compound_statement(self) -> S.Compound:
        open_tok = self.expect("{", "a block starts with `{`") or self.tok
        block = S.Compound(open_tok.span)
        self.sema.push()
        vm_depth = len(self._vm_scope)
        try:
            while not self.at("}") and self.tok.kind is not Kind.EOF:
                if self.is_declaration():
                    decls = self.declaration()
                    block.items.extend(decls)
                    if any(getattr(d, "vla_size", None) is not None
                           for d in decls):
                        block.has_vla = True
                else:
                    block.items.append(self.statement())
        finally:
            self.sema.pop()
            del self._vm_scope[vm_depth:]
        self.expect("}", "this block's `{` is never closed")
        return block

    def statement(self) -> S.Stmt:
        # A STATEMENT MAY CARRY ATTRIBUTES: `[[fallthrough]];` is the one
        # every program writes, and `[[maybe_unused]] again: ;` labels one.
        # None of the standard ones changes what the statement means here,
        # so they are read and dropped -- see `attributes.py`.
        if self._at_attribute():
            self._std_attributes()
        t = self.tok
        if self.at("{"):
            return self.compound_statement()
        if self.at(";"):
            self.next()
            return S.Empty(t.span)
        if t.kind is Kind.IDENT and not t.is_keyword and self.peek().is_punct(":"):
            self.next()
            self.next()
            name = t.text
            if self.function is not None:
                if name in self.function.labels:
                    self.sema.error("E1251", f"label {name!r} appears twice",
                                    t.span)
                self.function.labels[name] = self.label(f"L.{name}")
                self._label_vm[name] = tuple(self._vm_scope)
            # C23 LET A LABEL PRECEDE A DECLARATION, and let one end a
            # block. Both are the same shape here: the label's body is an
            # empty statement and whatever follows is the next item of the
            # enclosing block, which is where a declaration has to go --
            # `block.items` holds declarations and statements in order, so
            # control reaches the declaration by falling into it and its
            # initialiser runs exactly as it would have.
            if self.at("}") or self.is_declaration():
                return S.Label(t.span, name, S.Empty(t.span))
            return S.Label(t.span, name, self.statement())
        word = _kw(t.text) if t.kind is Kind.IDENT else ""
        if word == "if":
            return self._if()
        if word == "while":
            return self._while()
        if word == "do":
            return self._do()
        if word == "for":
            return self._for()
        if word == "switch":
            return self._switch()
        if word == "case":
            return self._case()
        if word == "default":
            return self._default()
        if word == "goto":
            return self._goto()
        if word == "break":
            self.next()
            self.expect(";")
            if not self.breaks:
                self.sema.error("E1252", "`break` outside a loop or switch",
                                t.span)
            return S.Break(t.span)
        if word == "continue":
            self.next()
            self.expect(";")
            if not self.continues:
                self.sema.error("E1253", "`continue` outside a loop", t.span)
            return S.Continue(t.span)
        if word == "return":
            return self._return()
        e = self.expression()
        self.expect(";", "a statement ends with `;`")
        self._check_discard(e)
        return S.ExprStmt(t.span, e)

    def _check_discard(self, e: S.Expr) -> None:
        """`[[nodiscard]] int f(void); f();` -- the one attribute whose whole
        purpose is a diagnostic at the call.

        `(void)f()` IS THE WAY TO SAY "I MEANT IT", which is why the cast is
        checked for rather than the warning being unconditional: C says the
        attribute is for a result that is almost always a mistake to drop,
        and a program that drops one on purpose has a spelling for it.
        """
        if isinstance(e, S.Cast) and e.type.is_void:
            return
        while isinstance(e, S.Conv) and e.operand is not None:
            e = e.operand
        if not isinstance(e, S.Call):
            return
        func = e.func
        while isinstance(func, S.Conv) and func.operand is not None:
            func = func.operand
        sym = func.sym if isinstance(func, S.Ident) else None
        if sym is not None and "nodiscard" in sym.attrs:
            self.sema.warn(
                "W1221", f"the result of {sym.name!r} is not used", e.span,
                note="its declaration carries `[[nodiscard]]`",
                help=f"write `(void){sym.name}(...)` to say that dropping "
                     f"it is deliberate")

    def _condition(self, keyword: str) -> S.Expr | None:
        self.expect("(", f"`{keyword}` needs a parenthesised condition")
        e = self.expression()
        self.expect(")")
        return self.sema.truth(e, f"`{keyword}`")

    def _if(self) -> S.Stmt:
        span = self.next().span
        cond = self._condition("if")
        then = self.statement()
        other = None
        if self.at_kw("else"):
            self.next()
            other = self.statement()
        return S.If(span, cond, then, other)

    def _while(self) -> S.Stmt:
        span = self.next().span
        cond = self._condition("while")
        node = S.While(span, cond, None)
        node.body = self._loop_body()
        return node

    def _loop_body(self) -> S.Stmt:
        """Parse a loop's body with `break` and `continue` legal inside it.

        THE STACKS COUNT, they do not name. Where each one JUMPS is lowering's
        question and lowering keeps its own stack of blocks; all the parser
        needs to know is whether there is a loop to break out of, because
        `break;` at file scope is a diagnostic and not a jump to nowhere.
        """
        self.breaks.append(True)
        self.continues.append(True)
        try:
            return self.statement()
        finally:
            self.breaks.pop()
            self.continues.pop()

    def _do(self) -> S.Stmt:
        span = self.next().span
        body = self._loop_body()
        self.expect("while", "a `do` body is followed by `while`")
        cond = self._condition("do ... while")
        self.expect(";")
        return S.DoWhile(span, body, cond)

    def _for(self) -> S.Stmt:
        span = self.next().span
        self.expect("(", "`for` needs parentheses")
        self.sema.push()
        try:
            init: Any = None
            if self.at(";"):
                self.next()
            elif self.is_declaration():
                init = self.declaration()
            else:
                init = S.ExprStmt(self.tok.span, self.expression())
                self.expect(";", "the first clause of `for` ends with `;`")
            cond = None
            if not self.at(";"):
                cond = self.sema.truth(self.expression(), "`for`")
            self.expect(";", "the second clause of `for` ends with `;`")
            step = None
            if not self.at(")"):
                step = self.expression()
            self.expect(")")
            node = S.For(span, init, cond, step, None)
            node.body = self._loop_body()
            return node
        finally:
            self.sema.pop()

    def _switch(self) -> S.Stmt:
        span = self.next().span
        self.expect("(", "`switch` needs a parenthesised expression")
        e = self.sema.lvalue_conversion(self.expression())
        self.expect(")")
        if not e.type.is_integer:
            self.sema.error("E1254",
                            f"`switch` needs an integer, not {C.spell(e.type)}",
                            e.span)
            e = self.sema.poison(e.span)
        # THE CONTROLLING EXPRESSION IS PROMOTED and every case label is
        # converted to that type, 6.8.4.2p5. That is what makes `case -1:`
        # match when the expression is `unsigned`.
        e = self.sema.convert(e, C.promote(e.type), "promotion")
        node = S.Switch(span, e, None)
        self.breaks.append(True)
        self.switches.append(node)
        try:
            node.body = self.statement()
        finally:
            self.breaks.pop()
            self.switches.pop()
        return node

    def _case(self) -> S.Stmt:
        t = self.next()
        if not self.switches:
            self.sema.error("E1255", "`case` outside a switch", t.span)
        value = fold_int(self.conditional())
        high = None
        if self.at("..."):
            dots = self.next()
            high = fold_int(self.conditional())
            # ANOTHER GNU EXTENSION THAT WORKS, and says so. C has one value
            # per `case` label; a range is gcc's, and a program using one is
            # not portable to a compiler that has not got it.
            self.sema.warn(
                "W1256", "a `case` range is not standard C", dots.span,
                note="C gives a `case` label one value; this is gcc's "
                     "extension")
        self.expect(":", "a `case` label ends with `:`")
        if value is None:
            self.sema.error("E1256", "a `case` label must be a constant", t.span)
            value = 0
        node = S.Case(t.span, value, None, self.label("case"), high)
        if self.switches:
            sw = self.switches[-1]
            from .fold import wrap
            lo = wrap(value, sw.expr.type)
            top = wrap(high, sw.expr.type) if high is not None else lo
            if top < lo:
                self.sema.warn("W1257", "this `case` range is empty", t.span)
            span_count = top - lo + 1
            if span_count > 4096:
                self.sema.error(
                    "E1258",
                    f"this `case` range covers {span_count} values", t.span,
                    note="a range is expanded to one label per value",
                    help="use an `if` for a range this wide")
                span_count = 1
            for v in range(lo, lo + span_count):
                if any(existing == v for existing, _ in sw.cases):
                    self.sema.error("E1259",
                                    f"duplicate `case {v}` in this switch",
                                    t.span)
                else:
                    sw.cases.append((v, node.label))
            node.value = lo
        node.body = self.statement() if not self.at("}") else S.Empty(t.span)
        return node

    def _default(self) -> S.Stmt:
        t = self.next()
        self.expect(":", "`default` is followed by `:`")
        if not self.switches:
            self.sema.error("E1255", "`default` outside a switch", t.span)
        node = S.Default(t.span, None, self.label("default"))
        if self.switches:
            sw = self.switches[-1]
            if sw.default_label is not None:
                self.sema.error("E1260",
                                "this switch already has a `default`", t.span)
            sw.default_label = node.label
        node.body = self.statement() if not self.at("}") else S.Empty(t.span)
        return node

    def _goto(self) -> S.Stmt:
        t = self.next()
        if self.at("*"):
            self.next()
            target = self.sema.lvalue_conversion(self.unary())
            self.expect(";")
            self.sema.error("E1261", "a computed `goto` is not supported",
                            t.span,
                            note="`goto *p` needs a label address, which this "
                                 "IR has no way to take")
            return S.Goto(t.span, "", target)
        name = self.tok
        if name.kind is not Kind.IDENT:
            self.sema.error("E1262", "expected a label after `goto`", name.span)
            return S.Empty(t.span)
        self.next()
        self.expect(";")
        self._gotos.append((name.text, name.span, tuple(self._vm_scope)))
        return S.Goto(t.span, name.text)

    def _return(self) -> S.Stmt:
        t = self.next()
        want = self.function.type.ret if self.function else C.VOID
        if self.at(";"):
            self.next()
            if not want.is_void:
                self.sema.error(
                    "E1263",
                    f"`return` with no value in a function returning "
                    f"{C.spell(want)}", t.span)
            return S.Return(t.span, None)
        e = self.expression()
        self.expect(";")
        if want.is_void:
            if not e.type.is_void:
                self.sema.error("E1264",
                                "`return` with a value in a function "
                                "returning `void`", t.span)
            return S.Return(t.span, None)
        got = self.sema.assignable(want, e, "return", e.span)
        return S.Return(t.span, got)

    # ── declarations ────────────────────────────────────────────────────────
    def _static_assert(self) -> None:
        t = self.next()
        self.expect("(", "_Static_assert needs parentheses")
        e = self.conditional()
        message = None
        if self.eat(","):
            if self.tok.kind is Kind.STRING:
                message = self._string()
            else:
                self.sema.error("E1265",
                                "_Static_assert's message must be a string",
                                self.tok.span)
        self.expect(")")
        self.expect(";")
        got = fold(e)
        if got is None:
            self.sema.error("E1266",
                            "_Static_assert needs a constant expression", t.span)
        elif not e.type.is_integer:
            # AN INTEGER CONSTANT EXPRESSION, which a string literal and a
            # floating constant are not -- and `_Static_assert("x", "no")`
            # would otherwise be an assertion that always holds, which is
            # the opposite of what it was written for.
            self.sema.error(
                "E1268",
                f"_Static_assert needs an integer, not {C.spell(e.type)}",
                e.span or t.span)
        elif not got:
            text = ""
            if isinstance(message, S.StringLit):
                text = ": " + message.data.rstrip(b"\x00").decode("utf-8",
                                                                 "replace")
            self.sema.error("E1267", f"static assertion failed{text}", t.span)

    def declaration(self) -> list[S.Decl]:
        if self.at_kw("_Static_assert"):
            self._static_assert()
            return []
        spec = self.declaration_specifiers()
        out: list[S.Decl] = []
        if self.at(";"):
            self.next()
            if not spec.explicit:
                self.sema.warn("W1220", "declaration declares nothing",
                               spec.span)
            return out
        name, build, span = self._declarator(abstract=True)
        self._skip_attributes()
        if self.at("__asm__", "__asm", "asm"):
            self._skip_asm_name()
        return self.declaration_rest(spec, name, build(spec.type), span)

    def declaration_rest(self, spec: Spec, name: str | None, ty: CType,
                         span: Span) -> list[S.Decl]:
        """The declarators of a declaration, the first one already parsed."""
        out: list[S.Decl] = []
        while True:
            if name is None:
                self.sema.error("E1234", "expected a name in this declaration",
                                span)
            else:
                out.append(self._declare(name, ty, spec, span))
            if not self.eat(","):
                break
            if spec.infer:
                # `auto a = 1, b = 2;` HAS NO TYPE TO SHARE. Every other
                # declaration's declarators are built on one specifier list;
                # here the type comes from each initialiser, so two
                # declarators would be two different types written as one
                # declaration. C23 allows exactly one.
                self.sema.error(
                    "E1249", "`auto` allows one declarator and this is the "
                             "second", self.tok.span,
                    note="the type comes from the initialiser, so two "
                         "declarators would be two types",
                    help="write a second declaration")
            name, build, span = self._declarator(abstract=True)
            ty = build(spec.type)
            self._skip_attributes()
            if self.at("__asm__", "__asm", "asm"):
                self._skip_asm_name()
        self.expect(";", "a declaration ends with `;`")
        return out

    def _skip_asm_name(self) -> None:
        t = self.next()
        self.sema.warn("W1268", "ignoring an `asm` symbol name", t.span,
                       note="the declared name is used as written")
        if self.eat("("):
            depth = 1
            while depth and self.tok.kind is not Kind.EOF:
                if self.at("("):
                    depth += 1
                elif self.at(")"):
                    depth -= 1
                self.next()

    def _declare(self, name: str, ty: CType, spec: Spec, span: Span) -> S.Decl:
        file_scope = self.sema.scope.is_file
        storage = spec.storage
        pre: S.Expr | None = None
        if spec.infer:
            ty, pre = self._infer(name, ty, spec, span)
        if storage is Storage.TYPEDEF:
            return self._typedef(name, ty, span)
        if ty.is_function:
            return self._declare_function(name, ty, spec, span)
        # STORAGE DURATION AND LINKAGE ARE TWO QUESTIONS, and C answers them
        # from the same keyword. At file scope everything has static duration
        # whatever was written; `static` there means internal LINKAGE, not
        # static storage, and a block-scope `static` means the opposite --
        # static storage with no linkage at all.
        if file_scope:
            storage = Storage.EXTERN if spec.storage is Storage.EXTERN \
                else Storage.STATIC
            linkage = (Linkage.INTERNAL if spec.storage is Storage.STATIC
                       else Linkage.EXTERNAL)
        elif spec.storage is Storage.EXTERN:
            storage, linkage = Storage.EXTERN, Linkage.EXTERNAL
        elif spec.storage is Storage.STATIC:
            storage, linkage = Storage.STATIC, Linkage.NONE
        else:
            # `register` KEPT AND NOT FOLDED INTO `auto`. It asks for no
            # storage of its own and lowering treats the two alike, but it
            # is the reason `&x` is a constraint violation, and a symbol
            # that has forgotten it cannot say so.
            storage = (Storage.REGISTER
                       if spec.storage is Storage.REGISTER else Storage.AUTO)
            linkage = Linkage.NONE
        sym = self._merge(name, ty, storage, linkage, span)
        if spec.constexpr:
            sym.type = ty = ty.qualified({"const"})
        if spec.attrs:
            sym.attrs |= frozenset(spec.attrs)
        if spec.thread_local:
            # ONE COPY PER THREAD, which is a property of the OBJECT rather
            # than of this declaration: a second declaration without the
            # keyword refers to the same thread-local object.
            if storage in (Storage.AUTO, Storage.REGISTER):
                self.sema.error(
                    "E1278",
                    f"{name!r} is `_Thread_local` and has automatic storage",
                    span,
                    note="a local already has one copy per thread",
                    help="add `static`, or move it to file scope")
            sym.thread_local = True
        decl = S.Decl(span, name, sym.type, sym)
        if self.at("=") and sym.defined:
            # TWO DEFINITIONS, not two declarations. `int x; int x;` at file
            # scope is a pair of tentative definitions and is fine; `int x =
            # 1; int x = 2;` is the program saying two different things.
            self.sema.error("E1276", f"{name!r} is defined twice", span,
                            also=(sym.span, "the first definition")
                            if sym.span else None)
        if pre is not None or self.eat("="):
            if storage is Storage.EXTERN and not file_scope:
                self.sema.error("E1269",
                                f"{name!r} is declared `extern` and "
                                f"initialised", span)
            if sym.type.is_vla:
                # NOTHING TO INITIALISE IT FROM. The length is not known
                # until the declaration is reached, so there is no list a
                # translator could check against and no storage to write
                # into before the size is computed. C forbids it outright.
                self.sema.error(
                    "E1301",
                    f"{name!r} has a variable length and cannot be "
                    f"initialised", span,
                    help="assign to the elements after the declaration")
            elif not sym.type.complete and not (
                    sym.type.is_array and sym.type.count is None):
                self.sema.error("E1270",
                                f"cannot initialise the incomplete type "
                                f"{C.spell(sym.type)}", span)
            init = (self._init_from(sym.type, pre, span) if pre is not None
                    else self.initializer(sym.type, span))
            if sym.type.is_array and sym.type.count is None:
                elem = max(1, sym.type.of.size)
                sym.type = C.array_of(sym.type.of,
                                      max(1, (init.size + elem - 1) // elem))
                decl.type = sym.type
            decl.init = init
            sym.defined = True
            if sym.is_global:
                sym.init = init
            self._check_static_init(sym, init)
            if spec.constexpr:
                self._check_constexpr(sym, init, span)
        elif spec.constexpr:
            self.sema.error(
                "E1279", f"{name!r} is `constexpr` and has no initialiser",
                span,
                note="`constexpr` says what the value IS, so there has to "
                     "be one")
        elif sym.type.is_vla:
            decl.vla_size = self.sema.vla_size(sym.type, span)
            if self.function is not None:
                self._vm_scope.append(name)
            if sym.is_global:
                self.sema.error("E1271",
                                "a variable-length array cannot have static "
                                "storage", span)
        elif not sym.type.complete and storage is not Storage.EXTERN:
            if file_scope and sym.type.is_array and sym.type.count is None:
                # A TENTATIVE DEFINITION of an array with no bound. C gives it
                # one element at the end of the unit.
                sym.type = C.array_of(sym.type.of, 1)
                decl.type = sym.type
            else:
                self.sema.error("E1272",
                                f"{name!r} has the incomplete type "
                                f"{C.spell(sym.type)}", span,
                                note="its size is not known here")
        if spec.align is not None:
            if spec.align < sym.type.align:
                # STRENGTHENING ONLY. A weaker alignment is not a request the
                # machine could honour and still have the object work: C says
                # the specifier shall not be less strict than the type's own.
                self.sema.error(
                    "E1258",
                    f"an alignment of {spec.align} is weaker than "
                    f"{C.spell(sym.type)}'s own {sym.type.align}",
                    spec.align_span or span,
                    help="`_Alignas` can ask for more and not for less")
            else:
                sym.align = spec.align
        if self.function is not None and sym not in self.function.locals:
            # A BLOCK-SCOPE `static` IS STILL DECLARED IN THIS FUNCTION, and
            # it was left off this list because it has static storage -- so
            # nothing ever created the global it lives in, and every use was
            # a `global_addr` of a symbol the module did not contain. The
            # list means "declared here", not "has automatic storage";
            # lowering asks about the storage itself.
            self.function.locals.append(sym)
        return decl

    def _infer(self, name: str, ty: CType, spec: Spec,
               span: Span) -> tuple[CType, "S.Expr | None"]:
        """C23's `auto x = e;` -- the type is `e`'s, and `e` is parsed here.

        IT HAS TO BE PARSED BEFORE THE OBJECT EXISTS, which is why this is
        not in `initializer`: the declaration has no type until the
        initialiser has one, and a name cannot be declared without a type.
        The expression comes back so that the ordinary path can use it.

        THE TYPE IS AFTER DECAY AND LVALUE CONVERSION: `auto p = "hi";` is a
        `char *` and not a `char[3]`, `auto q = a;` for an array is a pointer
        to its first element, and `auto x = c;` for a `const int c` is a
        plain `int` -- reading an object gives a value, and a value has no
        qualifiers.
        """
        if ty is not spec.type:
            # `auto *p = ...` -- C23 allows one plain identifier and nothing
            # built on it, because there is no type yet for a `*` to be
            # applied to.
            self.sema.error(
                "E1286", f"`auto` infers the type of {name!r} and cannot be "
                         f"combined with a declarator", span,
                help="write the type out, or drop the `*` and let the "
                     "initialiser supply it")
            return C.INT, None
        if not self.at("="):
            self.sema.error(
                "E1287", f"`auto` needs an initialiser to take {name!r}'s "
                         f"type from", span)
            return C.INT, None
        self.next()
        e = self.assignment()
        got = C.decay(self.sema.lvalue_conversion(e).type).unqualified()
        if got.is_void or not got.complete:
            self.sema.error(
                "E1288", f"`auto` cannot take {name!r}'s type from "
                         f"{C.spell(got)}", e.span)
            return C.INT, e
        # THE QUALIFIERS ARE THE DECLARATION'S, not the initialiser's:
        # `const auto x = f();` is a `const` object of `f`'s return type.
        return got.qualified(spec.type.qual) if spec.type.qual else got, e

    def _init_from(self, ty: CType, e: S.Expr, span: Span) -> S.Init:
        """An `S.Init` for an initialiser that is already parsed."""
        out = S.Init(span)
        got = self.sema.assignable(ty, e, "initialisation", e.span)
        if got is not None:
            out.entries.append(S.InitEntry(0, ty, got))
        try:
            out.size = ty.size
        except C.IncompleteType:
            out.size = 0
        return out

    def _check_constexpr(self, sym: Symbol, init: S.Init, span: Span) -> None:
        """`constexpr int n = 7;` -- and then `n` IS 7 wherever a constant is.

        THE CHECK AND THE VALUE ARE THE SAME WORK. Every entry has to fold
        for the object to be constant at all, and a SCALAR one's single
        folded entry is what makes the name usable in an array bound, a
        `case` label or another `constexpr` -- which is the whole of what
        this specifier buys over `const`, since `const int n = 7;` has
        always produced the same code and never been a constant expression.

        AN AGGREGATE KEEPS THE CHECK AND NOT THE VALUE: `constexpr struct P
        p = {1, 2};` is required to be constant-initialised and `p` is still
        not something an array bound can be written with, so there is
        nothing to remember.
        """
        for entry in init.entries:
            if entry.value is None or entry.data is not None:
                continue
            if fold(entry.value) is None:
                if not sym.is_global:
                    # ONE COMPLAINT AND NOT TWO: an object with static
                    # storage has already been told its initialiser is not
                    # constant, by `_check_static_init`, in the same words.
                    self.sema.error(
                        "E1280",
                        f"the initialiser for {sym.name!r} is not a constant",
                        entry.value.span,
                        note="`constexpr` says the value is known at compile "
                             "time, and this one is not")
                return
        if sym.type.is_record or sym.type.is_array:
            return
        only = [e for e in init.entries if e.value is not None]
        if len(only) == 1 and only[0].offset == 0:
            sym.const_value = fold(only[0].value)

    def _check_static_init(self, sym: Symbol, init: S.Init) -> None:
        """An object with static storage duration needs constant initialisers.

        Checked HERE rather than in lowering because it is a language rule
        with a position: the diagnostic names the element that is not
        constant, and lowering has no token to point at.
        """
        if not sym.is_global:
            return
        for entry in init.entries:
            if entry.value is None:
                continue
            if fold(entry.value) is None:
                self.sema.error(
                    "E1273",
                    f"the initialiser for {sym.name!r} is not a constant",
                    entry.value.span,
                    note="an object with static storage duration is "
                         "initialised before the program starts, so its "
                         "value has to be known at compile time")
                return

    def _typedef(self, name: str, ty: CType, span: Span) -> S.Decl:
        existing = self.sema.scope.names.get(name)
        if existing is not None:
            if existing.storage is not Storage.TYPEDEF or \
                    not C.compatible(existing.type, ty):
                self.sema.error("E1274", f"{name!r} is redeclared", span,
                                also=(existing.span, "the first declaration")
                                if existing.span else None)
        sym = Symbol(name, ty, Storage.TYPEDEF, span=span)
        self.sema.scope.declare(sym)
        return S.Decl(span, name, ty, sym)

    def _merge(self, name: str, ty: CType, storage: Storage, linkage: Linkage,
               span: Span) -> Symbol:
        """Declare `name`, reconciling it with any earlier declaration."""
        scope = self.sema.scope
        existing = scope.names.get(name)
        if existing is None and linkage is Linkage.EXTERNAL and not scope.is_file:
            existing = self.sema.file_scope.names.get(name)
        if existing is not None and existing.storage is not Storage.TYPEDEF:
            if not C.compatible(existing.type.unqualified(), ty.unqualified()):
                self.sema.error(
                    "E1275",
                    f"{name!r} is declared as {C.spell(ty)} here and as "
                    f"{C.spell(existing.type)} before", span,
                    also=(existing.span, "the earlier declaration")
                    if existing.span else None)
                return existing
            if linkage is Linkage.NONE and existing.linkage is Linkage.NONE \
                    and scope.names.get(name) is not None:
                self.sema.error("E1276", f"{name!r} is declared twice", span,
                                also=(existing.span, "the first declaration")
                                if existing.span else None)
                return existing
            existing.type = C.composite(existing.type, ty)
            if existing.linkage is Linkage.EXTERNAL and \
                    linkage is Linkage.INTERNAL:
                self.sema.error("E1277",
                                f"{name!r} is `static` after being declared "
                                f"without it", span)
            if linkage is Linkage.INTERNAL:
                existing.linkage = linkage
            return existing
        sym = Symbol(name, ty, storage, linkage, span=span)
        # A NAME WITH INTERNAL LINKAGE IS NOT THE PLATFORM'S. `static int
        # printf(const char *, ...)` -- which is exactly what this frontend's
        # own `<stdio.h>` declares -- must not become a symbol called `printf`
        # in the IR: the C backend emits a self-contained file that includes
        # the real `<stdio.h>`, and two `printf`s with different signatures is
        # a compile error in generated code the user never wrote. `static`
        # means "this name is this unit's", and the prefix says so. A
        # block-scope `static` carries its function's name too, because two
        # functions may each have a `static int count;` and the IR has one
        # flat namespace of globals.
        own = _library_prefix(span) or self.sema.prefix
        sym.ir_name = (
            name if linkage is Linkage.EXTERNAL
            else self.sema.unique(f"{own}{name}" if scope.is_file
                                  else f"{own}{_owner(self)}.{name}"))
        if linkage is Linkage.EXTERNAL:
            self.sema.taken.add(name)
        scope.declare(sym)
        return sym

    def _declare_function(self, name: str, ty: CType, spec: Spec,
                          span: Span) -> S.Decl:
        if spec.constexpr:
            # C23 GAVE `constexpr` TO OBJECTS AND NOT TO FUNCTIONS, which is
            # the difference from C++ that surprises people: there is no
            # constant-evaluated call here, and a function saying `constexpr`
            # would be claiming one. Checked HERE and not in `_declare`,
            # because a definition never passes through that one.
            self.sema.error(
                "E1300",
                f"{name!r} is a function and cannot be `constexpr`", span,
                note="C23 has `constexpr` objects and not `constexpr` "
                     "functions")
        linkage = (Linkage.INTERNAL if spec.storage is Storage.STATIC
                   else Linkage.EXTERNAL)
        target = self.sema.scope if self.sema.scope.is_file else self.sema.file_scope
        saved = self.sema.scope
        self.sema.scope = target
        try:
            sym = self._merge(name, ty, Storage.EXTERN, linkage, span)
        finally:
            self.sema.scope = saved
        if saved is not target:
            saved.declare(sym)
        sym.inline = sym.inline or spec.inline
        # ATTRIBUTES ACCUMULATE ACROSS DECLARATIONS, which is what C says: a
        # header declaring `[[nodiscard]] int f(void);` and a file defining
        # `int f(void) { ... }` describe one function, and the definition
        # does not take the attribute away.
        if spec.attrs:
            sym.attrs |= frozenset(spec.attrs)
        return S.Decl(span, name, sym.type, sym)

    # ── the unit ────────────────────────────────────────────────────────────
    def translation_unit(self) -> S.Unit:
        while self.tok.kind is not Kind.EOF:
            before = self.i
            self.external_declaration()
            if self.i == before:
                self.next()         # never spin on an unconsumable token
        self._finish()
        return self.unit

    def _finish(self) -> None:
        for sym in self.sema.file_scope.names.values():
            if sym.storage is Storage.TYPEDEF or sym.linkage is Linkage.NONE:
                continue
            if sym.type.is_function and sym.linkage is Linkage.INTERNAL and \
                    not sym.has_body and not sym.inline:
                self.sema.warn(
                    "W1278", f"{sym.name!r} is declared `static` and never "
                             f"defined", sym.span or self.unit.span)

    def external_declaration(self) -> None:
        if self.at(";"):
            self.next()
            return
        if self.at_kw("_Static_assert"):
            self._static_assert()
            return
        if self.at("__attribute__", "__attribute"):
            self._attributes()
            return
        if self.at_kw("__extension__"):
            self.next()
            return self.external_declaration()
        spec = self.declaration_specifiers()
        if self.at(";"):
            self.next()
            if not spec.explicit and not spec.attrs:
                # `[[deprecated]];` DECLARES NOTHING ON PURPOSE: C calls it
                # an attribute declaration and a program writes one to
                # attach an attribute to a statement or to nothing at all.
                self.sema.warn("W1220", "declaration declares nothing", spec.span)
            return
        name, build, span = self._declarator(abstract=True)
        ty = build(spec.type)
        self._skip_attributes()
        if self.at("__asm__", "__asm", "asm"):
            self._skip_asm_name()
        # A FUNCTION DEFINITION is a declarator followed by `{` -- or, in the
        # K&R form, by parameter declarations and then `{`.
        #
        # AND THE SPECIFIERS ARE NOT RE-READ TO FIND OUT. Rewinding to the
        # start of the declaration and parsing it again is the obvious way to
        # write this, and it is wrong: `struct s { int a; } x;` defines the
        # tag while the specifiers are read, so a second pass defines it a
        # second time and reports that `struct s` has two definitions and two
        # members called `a`. Everything up to here happens ONCE, and the
        # declarator already in hand is handed on.
        if ty.is_function and (self.at("{") or self._kr_declarations_ahead()):
            if name is None:
                self.sema.error("E1234", "expected a function name", span)
                self.compound_statement()
                return
            self.function_definition(name, ty, spec, span)
            return
        for decl in self.declaration_rest(spec, name, ty, span):
            self.unit.decls.append(decl)

    def _kr_declarations_ahead(self) -> bool:
        return self._type_starts(self.tok) and not self.at(";")

    def function_definition(self, name: str, ty: CType, spec: Spec,
                            span: Span) -> None:
        params = list(ty.params or ())
        kr: dict[str, CType] = {}
        if ty.params is None or self._kr_declarations_ahead():
            if ty.params is None:
                params = [C.Param(n, C.INT, span) for n in ty.kr_names]
            # K&R: `int f(a, b) int a; char *b; { ... }`. Accepted because it
            # is still C, and because a program that uses it has no other way
            # to be compiled; the resulting prototype is built from the
            # declarations, so callers are checked normally afterwards.
            names = [p.name for p in params]
            while self._kr_declarations_ahead():
                for decl in self.declaration():
                    kr[decl.name] = decl.type
            params = [C.Param(n, C.decay(kr.get(n, C.INT)), span)
                      for n in names]
            ty = C.function(ty.ret, tuple(params), False)
            if names:
                self.sema.warn(
                    "W1279",
                    f"{name!r} is defined in the old parameter style", span,
                    help="write the types in the parameter list")
        sym = self._declare_function(name, ty, spec, span).sym
        if sym.has_body:
            self.sema.error("E1280", f"{name!r} is defined twice", span,
                            also=(sym.span, "the first definition")
                            if sym.span else None)
        sym.has_body = True
        sym.defined = True
        fn = S.FunctionDef(span, name, sym.type, sym, variadic=ty.variadic)
        self.function = fn
        self._gotos = []
        self._vm_scope = []
        self._label_vm = {}
        self.sema.push()
        try:
            for index, p in enumerate(params):
                if not p.type.complete and not p.type.is_vla:
                    self.sema.error(
                        "E1282",
                        (f"parameter {p.name!r}" if p.name
                         else f"parameter {index + 1}")
                        + f" has the incomplete type {C.spell(p.type)}",
                        p.span or span)
                if p.name is None:
                    # C23 LETS A PARAMETER IN A DEFINITION GO UNNAMED, which
                    # is how a function says "this argument is part of my
                    # interface and I do not read it" without the
                    # `(void)unused;` line that used to be the only way.
                    #
                    # IT STILL TAKES ITS PLACE. The symbol is made and
                    # appended and only the DECLARING is skipped -- dropping
                    # it would shift every argument after it by one, which is
                    # a miscompilation rather than a diagnostic.
                    fn.params.append(p.sym or Symbol("<unnamed>", p.type,
                                                     Storage.PARAM,
                                                     span=p.span))
                    continue
                if self.sema.scope.names.get(p.name) is not None:
                    self.sema.error("E1276",
                                    f"{p.name!r} is declared twice",
                                    p.span or span)
                    continue
                psym = p.sym or Symbol(p.name, p.type, Storage.PARAM,
                                       span=p.span)
                self.sema.scope.declare(psym)
                fn.params.append(psym)
            fn.body = self.compound_statement()
        finally:
            self.sema.pop()
            self.function = None
        for label, where, at_goto in self._gotos:
            if label not in fn.labels:
                self.sema.error("E1283", f"no label {label!r} in this function",
                                where)
                continue
            at_label = self._label_vm.get(label, ())
            if at_label[:len(at_goto)] != at_goto[:len(at_label)] \
                    or len(at_label) > len(at_goto):
                # THE LABEL IS INSIDE A SCOPE THE `goto` IS OUTSIDE OF, and
                # something in that scope has a length that is computed when
                # the declaration is reached. Jumping past the declaration
                # reaches an array whose size was never worked out, so C
                # forbids the jump rather than leaving the array undefined.
                inside = [v for v in at_label if v not in at_goto]
                self.sema.error(
                    "E1303",
                    f"this `goto` jumps into the scope of "
                    f"{inside[0]!r}, which has a variable length", where,
                    note="its length is computed where it is declared, and "
                         "this jump goes past that")
        self.unit.decls.append(fn)


def _same_type(ty: CType) -> CType:
    """The identity, as the starting point of a declarator's composition."""
    return ty


def _fits_in(value: int, ty: CType) -> bool:
    """Whether an integer is representable in `ty`. Written from the type's
    WIDTH and signedness rather than from a table of limits, so a
    `_BitInt(5)` is answered as readily as an `int`."""
    bits = ty.bits
    if ty.signed:
        return -(1 << (bits - 1)) <= value < (1 << (bits - 1))
    return 0 <= value < (1 << bits)


def _kw(text: str) -> str:
    return KEYWORD_ALIASES.get(text, text)


def _owner(p: Parser) -> str:
    return p.function.name if p.function is not None else "static"


def _library_prefix(span: Span | None) -> str | None:
    """`"c."` for a name declared in a bundled header, else None.

    WHY THE LIBRARY IS NOT A UNIT'S OWN. `static` in `<stdio.h>` means "this
    translation unit's", and with one unit per build that was the whole
    story. With several, every unit that includes `<stdio.h>` would get its
    own `printf`, its own `errno` and its own `malloc` arena -- so a program
    whose `fopen` fails in one file and whose `perror` is in another would
    print the wrong thing, and `srand` in one would not reach `rand` in the
    next. A real toolchain does not have that problem because libc is ONE
    object linked once, and this is the same answer: the bundled headers are
    the library, they share a prefix across the build, and `merge.py` keeps
    one copy of each.

    BY WHERE THE DECLARATION IS, which is the only honest test. A name is the
    library's because it came out of `include/`, not because it looks
    standard -- a user may write their own `printf` and it is theirs.
    """
    path = getattr(span.file, "path", None) if span is not None else None
    if path is None:
        return None
    return "c." if _BUNDLED in path.parents else None


def _prefix_info(prefix: str):
    from .literals import PREFIXES
    return PREFIXES[prefix]


def _ends_with_flexible(tag: C.Tag) -> bool:
    if not tag.members:
        return False
    last = tag.members[-1].type
    return last.is_array and last.count is None and not last.is_vla


def _element(ty: CType, index: int):
    """The `index`-th sub-object of an aggregate: (type, relative offset,
    member). None when there is no such element."""
    if ty.is_array:
        if ty.count is not None and index >= ty.count:
            return None
        return ty.of, index * ty.of.size, None
    if ty.is_record and ty.tag is not None and ty.tag.complete:
        members = [m for m in ty.tag.members
                   if m.name is not None or m.type.is_record]
        if ty.kind is C.K.UNION and index > 0:
            return None
        if index >= len(members):
            return None
        m = members[index]
        return m.type, m.offset, m
    return None


def _entry_size(e: S.InitEntry) -> int:
    if e.data is not None:
        return len(e.data)
    try:
        return e.type.size
    except IncompleteType:
        return 0


def parse(tokens: list[Token], sink: DiagnosticSink,
          prefix: str = "c0.") -> tuple[S.Unit, Parser]:
    p = Parser(tokens, sink, prefix)
    return p.translation_unit(), p
