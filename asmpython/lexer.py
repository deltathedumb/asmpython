"""Indentation-aware lexer for the serpent Python subset.

Emits INDENT/DEDENT tokens like CPython's tokenizer. Newlines inside
parentheses are suppressed so multi-line argument lists are legal.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from .errors import LexError, SourcePos


KEYWORDS = {
    "def",
    "return",
    "if",
    "elif",
    "else",
    "while",
    "for",
    "in",
    "and",
    "or",
    "not",
    "True",
    "False",
    "None",
    "pass",
    "break",
    "continue",
    "import",
    "from",
    "as",
    "class",
    "try",
    "except",
    "finally",
    "raise",
    "is",
    "assert",
}


@dataclass
class Token:
    kind: str
    value: object
    line: int
    col: int

    def __repr__(self) -> str:
        return f"Token({self.kind!r}, {self.value!r}, L{self.line}:{self.col})"

    @property
    def pos(self) -> SourcePos:
        return SourcePos(self.line, self.col)


class Lexer:
    def __init__(self, src: str) -> None:
        if not src.endswith("\n"):
            src += "\n"
        self.src = src
        self.pos = 0
        self.line = 1
        self.col = 1
        self.indents: list[int] = [0]
        self.paren_depth = 0
        self.at_line_start = True

    def _peek(self, off: int = 0) -> str:
        p = self.pos + off
        return self.src[p] if p < len(self.src) else ""

    def _advance(self) -> str:
        ch = self.src[self.pos]
        self.pos += 1
        if ch == "\n":
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch

    def tokenize(self) -> list[Token]:
        return list(self._iter())

    def _iter(self) -> Iterator[Token]:
        while self.pos < len(self.src):
            if self.at_line_start and self.paren_depth == 0:
                yield from self._handle_indentation()
                self.at_line_start = False
                if self.pos >= len(self.src):
                    break

            ch = self._peek()

            if ch == "":
                break

            if ch == "#":
                while self._peek() and self._peek() != "\n":
                    self._advance()
                continue

            if ch == "\n":
                line, col = self.line, self.col
                self._advance()
                if self.paren_depth == 0:
                    yield Token("NEWLINE", "\n", line, col)
                    self.at_line_start = True
                continue

            if ch in " \t":
                self._advance()
                continue

            if ch == "\\" and self._peek(1) == "\n":
                self._advance()
                self._advance()
                continue

            # f-string prefix: f"..." or f'...'
            if ch == "f" and self._peek(1) in ('"', "'"):
                yield self._read_fstring()
                continue

            if ch.isalpha() or ch == "_":
                yield self._read_identifier()
                continue

            if ch.isdigit():
                yield self._read_number()
                continue

            if ch == '"' or ch == "'":
                yield self._read_string()
                continue

            yield self._read_operator()

        while len(self.indents) > 1:
            self.indents.pop()
            yield Token("DEDENT", "", self.line, 1)
        yield Token("EOF", "", self.line, self.col)

    def _handle_indentation(self) -> Iterator[Token]:
        depth = 0
        while True:
            c = self._peek()
            if c == " ":
                depth += 1
                self._advance()
            elif c == "\t":
                depth += 8 - (depth % 8)
                self._advance()
            else:
                break
        if self._peek() in ("\n", "#", ""):
            return
        current = self.indents[-1]
        if depth > current:
            self.indents.append(depth)
            yield Token("INDENT", depth, self.line, 1)
        else:
            while depth < self.indents[-1]:
                self.indents.pop()
                yield Token("DEDENT", "", self.line, 1)
            if depth != self.indents[-1]:
                raise LexError("inconsistent indentation", SourcePos(self.line, 1))

    def _read_identifier(self) -> Token:
        line, col = self.line, self.col
        start = self.pos
        while self._peek() and (self._peek().isalnum() or self._peek() == "_"):
            self._advance()
        word = self.src[start : self.pos]
        if word in KEYWORDS:
            return Token("KEYWORD", word, line, col)
        return Token("NAME", word, line, col)

    def _read_number(self) -> Token:
        line, col = self.line, self.col
        start = self.pos
        # 0x..., 0b..., 0o... prefixes share a single int-parser path.
        if self._peek() == "0" and self._peek(1) in ("x", "X", "b", "B", "o", "O"):
            self._advance()
            self._advance()
            while self._peek() and (self._peek().isalnum() or self._peek() == "_"):
                self._advance()
            text = self.src[start : self.pos].replace("_", "")
            try:
                value = int(text, 0)
            except ValueError:
                raise LexError(
                    f"invalid numeric literal {text!r}", SourcePos(line, col)
                )
            return Token("INT", value, line, col)
        while self._peek() and (self._peek().isdigit() or self._peek() == "_"):
            self._advance()
        is_float = False
        if self._peek() == "." and self._peek(1).isdigit():
            is_float = True
            self._advance()
            while self._peek() and (self._peek().isdigit() or self._peek() == "_"):
                self._advance()
        if self._peek() in ("e", "E"):
            is_float = True
            self._advance()
            if self._peek() in ("+", "-"):
                self._advance()
            while self._peek() and (self._peek().isdigit() or self._peek() == "_"):
                self._advance()
        text = self.src[start : self.pos].replace("_", "")
        if is_float:
            try:
                return Token("FLOAT", float(text), line, col)
            except ValueError:
                raise LexError(f"invalid float literal {text!r}", SourcePos(line, col))
        return Token("INT", int(text), line, col)

    def _read_fstring(self) -> Token:
        """Read an f-string. Returns an FSTRING token whose value is a list
        of segments: ('str', text) or ('expr', raw_source_string).

        The parser will re-lex the expression segments later. This keeps the
        lexer's state-machine flat (we don't need to track 'inside-fstring-
        expression' depth across the main lex loop).
        """
        line, col = self.line, self.col
        self._advance()  # 'f'
        quote = self._advance()
        segments: list[tuple[str, str]] = []
        text: list[str] = []
        while True:
            c = self._peek()
            if c == "":
                raise LexError("unterminated f-string", SourcePos(line, col))
            if c == "\n":
                raise LexError("newline in f-string", SourcePos(line, col))
            if c == quote:
                self._advance()
                break
            if c == "\\":
                self._advance()
                esc = self._advance()
                text.append(
                    {
                        "n": "\n",
                        "t": "\t",
                        "r": "\r",
                        "0": "\0",
                        "\\": "\\",
                        "'": "'",
                        '"': '"',
                        "{": "{",
                        "}": "}",
                    }.get(esc, esc)
                )
                continue
            if c == "{":
                self._advance()
                if self._peek() == "{":  # `{{` escapes to literal `{`
                    self._advance()
                    text.append("{")
                    continue
                if text:
                    segments.append(("str", "".join(text)))
                    text = []
                # Collect until matching `}`. Track nested braces so e.g.
                # f"{ d['k'] }" still works in the future.
                depth = 1
                expr_chars: list[str] = []
                while True:
                    ec = self._peek()
                    if ec == "" or ec == "\n":
                        raise LexError(
                            "unterminated expression in f-string",
                            SourcePos(self.line, self.col),
                        )
                    if ec == "{":
                        depth += 1
                        expr_chars.append(self._advance())
                    elif ec == "}":
                        depth -= 1
                        if depth == 0:
                            self._advance()
                            break
                        expr_chars.append(self._advance())
                    else:
                        expr_chars.append(self._advance())
                segments.append(
                    ("expr", self._strip_fstring_spec("".join(expr_chars).strip()))
                )
                continue
            if c == "}":
                self._advance()
                if self._peek() == "}":
                    self._advance()
                    text.append("}")
                    continue
                raise LexError(
                    "single '}' is not allowed in f-string",
                    SourcePos(self.line, self.col),
                )
            text.append(self._advance())
        if text:
            segments.append(("str", "".join(text)))
        return Token("FSTRING", segments, line, col)

    @staticmethod
    def _strip_fstring_spec(raw: str) -> str:
        """Drop a trailing `!r`/`!s`/`!a` conversion and/or `:format-spec`
        from an f-string replacement field, leaving just the expression text.

        Only top-level (outside any `()[]{}`) markers count, so `{a != b}` and
        `{d[1:2]}` keep their `!=` / slice intact. Conversions and format
        specs aren't applied yet — they're stripped so the expression re-lexes
        cleanly. (TODO: honour `!r` and `:.2f` once repr / format land.)
        """
        depth = 0
        n = len(raw)
        i = 0
        while i < n:
            ch = raw[i]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif depth == 0:
                if ch == ":":
                    return raw[:i].strip()
                if (
                    ch == "!"
                    and i + 1 < n
                    and raw[i + 1] in "rsa"
                    and (i + 2 == n or raw[i + 2] == ":")
                ):
                    return raw[:i].strip()
            i += 1
        return raw.strip()

    def _read_string(self) -> Token:
        line, col = self.line, self.col
        quote = self._advance()
        # Triple-quoted (""" or ''')? Two more identical quotes opens a
        # multi-line literal that consumes everything until the matching
        # triple-quote terminator. Embedded newlines are preserved verbatim.
        if self._peek() == quote and self._peek(1) == quote:
            self._advance()
            self._advance()
            return self._read_triple_string(quote, line, col)
        chars: list[str] = []
        while True:
            c = self._peek()
            if c == "":
                raise LexError("unterminated string literal", SourcePos(line, col))
            if c == "\n":
                raise LexError("newline in string literal", SourcePos(line, col))
            if c == quote:
                self._advance()
                break
            if c == "\\":
                self._advance()
                esc = self._advance()
                chars.append(
                    {
                        "n": "\n",
                        "t": "\t",
                        "r": "\r",
                        "0": "\0",
                        "\\": "\\",
                        "'": "'",
                        '"': '"',
                    }.get(esc, esc)
                )
            else:
                chars.append(self._advance())
        return Token("STRING", "".join(chars), line, col)

    def _read_triple_string(self, quote: str, line: int, col: int) -> Token:
        """Reader for the body of a triple-quoted literal. Opening triple
        quote already consumed by the caller."""
        chars: list[str] = []
        while True:
            c = self._peek()
            if c == "":
                raise LexError(
                    "unterminated triple-quoted string", SourcePos(line, col)
                )
            if c == quote and self._peek(1) == quote and self._peek(2) == quote:
                self._advance()
                self._advance()
                self._advance()
                break
            if c == "\\":
                self._advance()
                esc = self._advance()
                chars.append(
                    {
                        "n": "\n",
                        "t": "\t",
                        "r": "\r",
                        "0": "\0",
                        "\\": "\\",
                        "'": "'",
                        '"': '"',
                    }.get(esc, esc)
                )
            else:
                # Newlines pass through unchanged (_advance updates self.line).
                chars.append(self._advance())
        return Token("STRING", "".join(chars), line, col)

    def _read_operator(self) -> Token:
        line, col = self.line, self.col
        three = self.src[self.pos : self.pos + 3]
        if three == "//=":
            self._advance()
            self._advance()
            self._advance()
            return Token("OP", three, line, col)
        two = self.src[self.pos : self.pos + 2]
        if two in (
            "==",
            "!=",
            "<=",
            ">=",
            "->",
            "//",
            "<<",
            ">>",
            "+=",
            "-=",
            "*=",
            "/=",
            "%=",
            "&=",
            "|=",
            "^=",
        ):
            self._advance()
            self._advance()
            return Token("OP", two, line, col)
        ch = self._advance()
        if ch in "([{":
            self.paren_depth += 1
        elif ch in ")]}":
            self.paren_depth -= 1
        if ch in "+-*/%<>=,:()[]{}&|^~.@":
            return Token("OP", ch, line, col)
        raise LexError(f"unexpected character {ch!r}", SourcePos(line, col))
