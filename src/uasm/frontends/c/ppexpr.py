"""`#if` arithmetic.

A SEPARATE EVALUATOR FROM THE ONE IN `sema.py`, and deliberately. The two look
alike and answer different questions: this one works only in `intmax_t` and
`uintmax_t`, knows no types, no `sizeof`, no casts and no enumeration
constants, and treats every surviving identifier as `0`. Sharing one evaluator
between them means either teaching this one about types it must not have, or
teaching that one to invent zeros -- and `#if sizeof(int) == 4` quietly
answering "yes" in a compiler where `sizeof` is not allowed in `#if` at all is
exactly the bug the separation prevents.

SHORT CIRCUIT IS NOT AN OPTIMISATION HERE. `#if defined(X) && X > 2` and
`#if 0 && 1/0` are both ordinary, portable C, and an evaluator that computes
both sides before combining them reports a division by zero in a branch the
program said not to look at. So `live` is threaded through every rule: a dead
subexpression is still PARSED, because it must be syntactically valid, and its
errors are not reported.
"""
from __future__ import annotations

from dataclasses import dataclass

from ...diagnostics import DiagnosticSink, error
from .literals import LiteralError, decode_char, is_float_spelling, parse_number
from .tokens import Kind, Token

_MASK = (1 << 64) - 1


@dataclass(frozen=True, slots=True)
class Value:
    """An `intmax_t` or a `uintmax_t`. The flag decides `/`, `%`, `>>` and
    every ordered comparison, exactly as signedness on a type does in the IR."""

    v: int
    unsigned: bool = False

    def as_signed(self) -> int:
        x = self.v & _MASK
        return x - (1 << 64) if x >= (1 << 63) else x

    def as_unsigned(self) -> int:
        return self.v & _MASK

    @property
    def truth(self) -> bool:
        return (self.v & _MASK) != 0


def _wrap(v: int, unsigned: bool) -> Value:
    x = v & _MASK
    if not unsigned and x >= (1 << 63):
        x -= 1 << 64
    return Value(x, unsigned)


class PPExpr:
    """Recursive descent over the tokens of one `#if` line."""

    def __init__(self, tokens: list[Token], sink: DiagnosticSink) -> None:
        self.toks = tokens
        self.i = 0
        self.sink = sink
        self.failed = False

    # ── token access ────────────────────────────────────────────────────────
    def peek(self) -> Token | None:
        return self.toks[self.i] if self.i < len(self.toks) else None

    def at(self, *texts: str) -> bool:
        t = self.peek()
        return t is not None and t.kind is Kind.PUNCT and t.text in texts

    def take(self) -> Token | None:
        t = self.peek()
        if t is not None:
            self.i += 1
        return t

    def _error(self, code: str, message: str, tok: Token | None = None) -> None:
        self.failed = True
        d = error(code, message)
        where = tok or self.peek() or (self.toks[-1] if self.toks else None)
        if where is not None:
            d = d.at(where.span)
        self.sink.report(d)

    # ── entry ───────────────────────────────────────────────────────────────
    def run(self) -> bool:
        """The line's truth value. Reports and returns False on a bad line."""
        if not self.toks:
            self._error("E1100", "#if with no expression")
            return False
        value = self.conditional(True)
        left = self.peek()
        if left is not None and not self.failed:
            self._error("E1101", f"unexpected {left.text!r} after #if expression",
                        left)
        return value.truth and not self.failed

    # ── the grammar, lowest precedence first ────────────────────────────────
    def conditional(self, live: bool) -> Value:
        cond = self.logical_or(live)
        if not self.at("?"):
            return cond
        self.take()
        then = self.conditional(live and cond.truth)
        if not self.at(":"):
            self._error("E1102", "expected `:` in #if conditional")
            return Value(0)
        self.take()
        other = self.conditional(live and not cond.truth)
        picked = then if cond.truth else other
        # The usual arithmetic conversions still apply to the two arms, so
        # `#if 1 ? -1 : 0u` is unsigned -- and therefore huge, and therefore
        # true. Dropping that makes `#if (1 ? -1 : 0u) > 0` answer differently
        # from the same expression compiled.
        unsigned = then.unsigned or other.unsigned
        return _wrap(picked.v, unsigned)

    def logical_or(self, live: bool) -> Value:
        left = self.logical_and(live)
        while self.at("||"):
            self.take()
            right = self.logical_and(live and not left.truth)
            left = Value(1 if left.truth or right.truth else 0)
        return left

    def logical_and(self, live: bool) -> Value:
        left = self.bit_or(live)
        while self.at("&&"):
            self.take()
            right = self.bit_or(live and left.truth)
            left = Value(1 if left.truth and right.truth else 0)
        return left

    def _binary(self, ops: tuple[str, ...], nxt, live: bool) -> Value:
        left = nxt(live)
        while self.at(*ops):
            op = self.take().text
            right = nxt(live)
            left = self._apply(op, left, right, live)
        return left

    def bit_or(self, live):  return self._binary(("|",), self.bit_xor, live)
    def bit_xor(self, live): return self._binary(("^",), self.bit_and, live)
    def bit_and(self, live): return self._binary(("&",), self.equality, live)
    def equality(self, live): return self._binary(("==", "!="), self.relational, live)

    def relational(self, live):
        return self._binary(("<", ">", "<=", ">="), self.shift, live)

    def shift(self, live): return self._binary(("<<", ">>"), self.additive, live)
    def additive(self, live): return self._binary(("+", "-"), self.multiplicative, live)

    def multiplicative(self, live):
        return self._binary(("*", "/", "%"), self.unary, live)

    def unary(self, live: bool) -> Value:
        t = self.peek()
        if t is not None and t.kind is Kind.PUNCT and t.text in ("+", "-", "~", "!"):
            self.take()
            v = self.unary(live)
            if t.text == "+":
                return v
            if t.text == "-":
                return _wrap(-v.v, v.unsigned)
            if t.text == "~":
                return _wrap(~v.v, v.unsigned)
            return Value(0 if v.truth else 1)
        return self.primary(live)

    def primary(self, live: bool) -> Value:
        t = self.take()
        if t is None:
            self._error("E1103", "#if expression ends early")
            return Value(0)
        if t.kind is Kind.PUNCT and t.text == "(":
            v = self.conditional(live)
            if not self.at(")"):
                self._error("E1104", "expected `)` in #if expression", t)
                return Value(0)
            self.take()
            return v
        if t.kind is Kind.NUMBER:
            if is_float_spelling(t.text):
                # A float in `#if` is a constraint violation, not a rounding
                # question: the standard restricts the expression to integers.
                self._error("E1105",
                            "floating constant in #if; only integers are "
                            "allowed here", t)
                return Value(0)
            try:
                c = parse_number(t.text)
            except LiteralError as exc:
                if live:
                    self._error(getattr(exc, "code", "E1010"), str(exc), t)
                return Value(0)
            return _wrap(c.value, not c.signed)
        if t.kind is Kind.CHARCONST:
            try:
                return Value(decode_char(t.text).value)
            except LiteralError as exc:
                if live:
                    self._error("E1010", str(exc), t)
                return Value(0)
        if t.kind is Kind.STRING:
            self._error("E1106", "string literal in #if", t)
            return Value(0)
        # EVERY REMAINING IDENTIFIER IS ZERO, including keywords. That is the
        # standard's rule (6.10.1p4) and it is what makes `#if UNDEFINED_THING`
        # false instead of an error -- the single most-used property of the
        # preprocessor's arithmetic.
        return Value(0)

    def _apply(self, op: str, a: Value, b: Value, live: bool) -> Value:
        # The usual arithmetic conversions, at one width: if either side is
        # unsigned, both are. `#if -1 < 0u` is FALSE in C and this is why.
        unsigned = a.unsigned or b.unsigned
        if op in ("<<", ">>"):
            # The shift's type is the LEFT operand's alone -- the right one is
            # promoted independently and never drags the result unsigned.
            unsigned = a.unsigned
        x = a.as_unsigned() if unsigned else a.as_signed()
        y = b.as_unsigned() if unsigned else b.as_signed()
        if op in ("==", "!=", "<", ">", "<=", ">="):
            r = {"==": x == y, "!=": x != y, "<": x < y,
                 ">": x > y, "<=": x <= y, ">=": x >= y}[op]
            return Value(1 if r else 0)
        if op in ("/", "%"):
            if y == 0:
                if live:
                    self._error("E1107", "division by zero in #if")
                return Value(0)
            # C truncates toward zero; Python floors. The correction is the
            # same one the IR's DIV needs and is written out rather than
            # imported, because this module has no IR in it.
            q = abs(x) // abs(y)
            q = -q if (x < 0) != (y < 0) else q
            return _wrap(q if op == "/" else x - q * y, unsigned)
        if op in ("<<", ">>"):
            n = b.as_unsigned() & 63 if b.as_signed() >= 0 else 0
            if b.as_signed() < 0:
                if live:
                    self._error("E1108", "negative shift count in #if")
                return Value(0)
            return _wrap(x << n if op == "<<" else x >> n, unsigned)
        table = {"+": lambda: x + y, "-": lambda: x - y, "*": lambda: x * y,
                 "&": lambda: x & y, "|": lambda: x | y, "^": lambda: x ^ y}
        return _wrap(table[op](), unsigned)


def evaluate(tokens: list[Token], sink: DiagnosticSink) -> bool:
    """The truth value of a `#if` line whose macros are already expanded."""
    return PPExpr(tokens, sink).run()


def evaluate_value(tokens: list[Token], sink: DiagnosticSink) -> int | None:
    """The VALUE of the same grammar, for `#embed`'s `limit(n)`.

    `#if` WANTS A TRUTH AND `limit` WANTS A NUMBER, and they are the same
    arithmetic: the same operators, the same `intmax_t` width, the same
    rule that an undefined identifier is 0. None on a line this reports
    a diagnostic about.
    """
    p = PPExpr(tokens, sink)
    if not p.toks:
        p._error("E1100", "#embed limit with no expression")
        return None
    value = p.conditional(True)
    left = p.peek()
    if left is not None and not p.failed:
        p._error("E1101", f"unexpected {left.text!r} after the limit", left)
    return None if p.failed else value.as_signed()
