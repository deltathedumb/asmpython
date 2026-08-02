"""APC tokenizer.

Newlines are statement separators (APC has no semicolons), so they are emitted
as real tokens -- but suppressed inside ``(``/``[`` so a multi-line argument or
parameter list still reads as one statement. That suppression is what lets the
expression parser stop at a newline and still accept wrapped calls.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import APCError


# Longest-first: `::` must be tried before `:`, `<<` before `<`.
_OPERATORS = (
    "::", "..", "==", "!=", "<=", ">=", "<<", ">>",
    "{", "}", "(", ")", "[", "]",
    "+", "-", "*", "/", "%", "&", "|", "^", "~", "!",
    "<", ">", "=", ":", ",", ".", ";",
)

KEYWORDS = frozenset({
    "import", "export", "extern",
    "func", "type", "layout", "enum", "const", "let",
    "pub", "plain", "constructor",
    "if", "else", "while", "for", "ret", "break", "continue",
    "as", "is", "sizeof",
    "true", "false", "none", "null",
})

_STR_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "0": "\0", "\\": "\\", '"': '"'}


@dataclass(frozen=True)
class Token:
    kind: str          # ident | int | float | str | op | nl | eof
    value: str
    line: int
    col: int

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Token({self.kind}, {self.value!r}, {self.line}:{self.col})"

    def is_op(self, *ops: str) -> bool:
        return self.kind == "op" and self.value in ops

    def is_kw(self, *kws: str) -> bool:
        return self.kind == "ident" and self.value in kws


def tokenize(src: str) -> list[Token]:
    toks: list[Token] = []
    i = 0
    line = 1
    line_start = 0
    depth = 0          # ( and [ nesting; newlines inside are insignificant
    n = len(src)

    while i < n:
        c = src[i]
        col = i - line_start + 1

        if c == "\n":
            if depth == 0 and toks and toks[-1].kind != "nl":
                toks.append(Token("nl", "\\n", line, col))
            i += 1
            line += 1
            line_start = i
            continue
        if c in " \t\r":
            i += 1
            continue
        if c == "#":
            while i < n and src[i] != "\n":
                i += 1
            continue

        # ── strings ──────────────────────────────────────────────────────
        if c == '"':
            i += 1
            buf: list[str] = []
            while i < n and src[i] != '"':
                if src[i] == "\n":
                    raise APCError("unterminated string", line, col, src)
                if src[i] == "\\" and i + 1 < n:
                    nxt = src[i + 1]
                    if nxt == "(":
                        # "\(0x00)" -- escape holding an expression. Kept raw;
                        # the parser decides what it means.
                        j = src.find(")", i)
                        if j < 0:
                            raise APCError("unterminated \\( escape", line, col, src)
                        buf.append(src[i:j + 1])
                        i = j + 1
                        continue
                    buf.append(_STR_ESCAPES.get(nxt, nxt))
                    i += 2
                    continue
                buf.append(src[i])
                i += 1
            if i >= n:
                raise APCError("unterminated string", line, col, src)
            i += 1
            toks.append(Token("str", "".join(buf), line, col))
            continue

        # ── numbers ──────────────────────────────────────────────────────
        if c.isdigit():
            start = i
            if src.startswith(("0x", "0X", "0b", "0B"), i):
                digits = "0123456789abcdefABCDEF_" if src[i + 1] in "xX" else "01_"
                i += 2
                while i < n and src[i] in digits:
                    i += 1
                toks.append(Token("int", src[start:i].replace("_", ""), line, col))
                continue
            while i < n and (src[i].isdigit() or src[i] == "_"):
                i += 1
            # A float needs a digit after the dot, so `0..8` stays a range.
            if i + 1 < n and src[i] == "." and src[i + 1].isdigit():
                i += 1
                while i < n and (src[i].isdigit() or src[i] == "_"):
                    i += 1
                toks.append(Token("float", src[start:i].replace("_", ""), line, col))
                continue
            toks.append(Token("int", src[start:i].replace("_", ""), line, col))
            continue

        # ── identifiers / keywords ───────────────────────────────────────
        if c.isalpha() or c == "_":
            start = i
            while i < n and (src[i].isalnum() or src[i] == "_"):
                i += 1
            toks.append(Token("ident", src[start:i], line, col))
            continue

        # ── operators ────────────────────────────────────────────────────
        for op in _OPERATORS:
            if src.startswith(op, i):
                if op in ("(", "["):
                    depth += 1
                elif op in (")", "]"):
                    depth = max(0, depth - 1)
                toks.append(Token("op", op, line, col))
                i += len(op)
                break
        else:
            raise APCError(f"unexpected character {c!r}", line, col, src)

    if toks and toks[-1].kind != "nl":
        toks.append(Token("nl", "\\n", line, i - line_start + 1))
    toks.append(Token("eof", "", line, i - line_start + 1))
    return toks


__all__ = ["KEYWORDS", "Token", "tokenize"]
