"""Indentation-aware lexer for the asmpython Python subset.

Emits INDENT/DEDENT tokens like CPython's tokenizer. Newlines inside
parentheses are suppressed so multi-line argument lists are legal.
"""

from __future__ import annotations

from dataclasses import dataclass

from .errors import ErrorCode, LexError, SourcePos


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
    "global",
    "nonlocal",
    "del",
    "lambda",
    "yield",
    "with",
    "async",
    "await",
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


def _is_ignore_marker(line: str, which: str) -> bool:
    """True if `line` is a `# [compiler: <which>]` directive comment.

    Accepts surrounding whitespace and either spacing inside the brackets, so
    all of these match for `which="ignore_start"`:
        # [compiler: ignore_start]
        #[compiler:ignore_start]
            #  [compiler:  ignore_start]
    """
    s = line.strip()
    if not s.startswith("#"):
        return False
    s = s[1:].strip()
    if not (s.startswith("[") and s.endswith("]")):
        return False
    inner = s[1:-1]
    colon = inner.find(":")
    if colon < 0:
        return False
    key = inner[:colon].strip()
    val = inner[colon + 1:].strip()
    return key == "compiler" and val == which


def _split_fstring_spec(raw: str) -> tuple[str, str, str]:
    """Split an f-string replacement field into (expression, format-spec,
    conversion).

    A trailing `!r`/`!s`/`!a` conversion is captured separately so codegen can
    apply it (`!r`/`!a` use repr-style formatting; `!s` is the default str
    conversion). A top-level `:format-spec` (outside any `()[]{}`) is also
    returned separately so codegen can honour `:.2f`-style specs. `{a != b}`
    and `{d[1:2]}` keep their `!=` / slice intact because only depth-0 markers
    count.

    A module-level function rather than a staticmethod so the lexer stays within
    asmpython's own compilable subset (the compiler doesn't model staticmethods).
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
                return raw[:i].strip(), raw[i + 1:].strip(), ""
            if (
                ch == "!"
                and i + 1 < n
                and raw[i + 1] in "rsa"
                and (i + 2 == n or raw[i + 2] == ":")
            ):
                conv = raw[i + 1]
                rest = raw[i + 2:]
                if rest.startswith(":"):
                    return raw[:i].strip(), rest[1:].strip(), conv
                return raw[:i].strip(), "", conv
        i += 1
    return raw.strip(), "", ""


def _strip_fstring_spec(raw: str) -> str:
    """Back-compat: just the expression text (spec/conversion dropped)."""
    expr, _, _ = _split_fstring_spec(raw)
    return expr


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
        self.tokens: list[Token] = []

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

    def _skip_ignore_block(self) -> None:
        """Consume input from after an `ignore_start` marker through the matching
        `ignore_end` marker (inclusive). Called from the main loop's comment
        branch with the cursor just past the opening marker comment.

        Scans line by line: for each line, skip leading whitespace, and if what
        follows is a `# ...ignore_end]` comment, consume that line and stop.
        Everything else is consumed unconditionally. An unterminated block
        (`ignore_start` with no `ignore_end`) is consumed to end-of-file.
        """
        while self.pos < len(self.src):
            # We are at a line boundary (the opening marker's newline hasn't
            # been consumed yet on the first iteration, so consume up to and
            # including the next newline first).
            while self._peek() and self._peek() != "\n":
                self._advance()
            if self._peek() == "\n":
                self._advance()
            # Now at the start of a fresh line. Skip indentation and inspect a
            # leading comment, if any.
            while self._peek() in (" ", "\t"):
                self._advance()
            if self._peek() == "#":
                cstart = self.pos
                while self._peek() and self._peek() != "\n":
                    self._advance()
                if _is_ignore_marker(self.src[cstart : self.pos], "ignore_end"):
                    # Consume the end marker's line and return.
                    if self._peek() == "\n":
                        self._advance()
                    return
            # Any other line (or the rest of a non-end comment line) is consumed
            # by the top of the next loop iteration.

    def tokenize(self) -> list[Token]:
        # Build the token list eagerly (no generators) so the lexer stays
        # within asmpython's own compilable subset — a self-host requirement,
        # since the compiler has no `yield` / generator support.
        self.tokens: list[Token] = []
        while self.pos < len(self.src):
            if self.at_line_start and self.paren_depth == 0:
                self._handle_indentation()
                self.at_line_start = False
                if self.pos >= len(self.src):
                    break

            ch = self._peek()

            if ch == "":
                break

            if ch == "#":
                # Read the comment text (up to, not including, the newline).
                cstart = self.pos
                while self._peek() and self._peek() != "\n":
                    self._advance()
                comment = self.src[cstart : self.pos]
                # `# [compiler: ignore_start]` opens a block of linter-only code
                # the compiler must not see. Skip everything (respecting string
                # context isn't a concern here: we only reach this branch with a
                # real `#` comment outside any string) until the matching
                # `ignore_end`. Handled inside the lexer rather than as a text
                # pre-pass so markers appearing inside string literals — e.g. in
                # this very file's docstrings — are never mistaken for real ones.
                if _is_ignore_marker(comment, "ignore_start"):
                    self._skip_ignore_block()
                continue

            if ch == "\n":
                line, col = self.line, self.col
                self._advance()
                if self.paren_depth == 0:
                    self.tokens.append(Token("NEWLINE", "\n", line, col))
                    self.at_line_start = True
                continue

            if ch in " \t":
                self._advance()
                continue

            if ch == "\\" and self._peek(1) == "\n":
                self._advance()
                self._advance()
                continue

            # String-literal prefixes: f/r/b singly, and the combined raw
            # forms rb/br/rf/fr -- all case-insensitive in real Python
            # (R"...", Rb"...", BR"...", etc. are just as valid as their
            # lowercase spellings). Checked as a genuine 2-character lookup
            # BEFORE any single-character prefix check, since a single-char
            # check alone would consume just the first letter of "rb" and
            # leave a bare "b" sitting in front of the string quote, which
            # nothing downstream expects (confirmed via a real repro: PIL's
            # own `rb'tiff:Orientation...'` lexed as NAME('rb') + a
            # not-actually-raw STRING token, silently losing the raw-string
            # backslash-literal rule and producing "expected NEWLINE, got
            # STRING" once the parser choked on the stray NAME).
            two = (self._peek(0) + self._peek(1)).lower()
            if two in ("rb", "br") and self._peek(2) in ('"', "'"):
                self._advance()  # consume both prefix letters; only the
                self._advance()  # quote is left for _read_raw_string
                tok = self._read_raw_string()
                self.tokens.append(Token("BYTES", tok.value, tok.pos.line, tok.pos.col))
                continue
            if two in ("rf", "fr") and self._peek(2) in ('"', "'"):
                self._advance()  # consume both prefix letters; only the
                self._advance()  # quote is left for _read_fstring
                self.tokens.append(self._read_fstring(raw=True))
                continue

            lower_ch = ch.lower()
            if lower_ch == "f" and self._peek(1) in ('"', "'"):
                self._advance()  # consume 'f'/'F'; only the quote is left
                self.tokens.append(self._read_fstring())
                continue

            # r"..." raw strings: backslashes are literal (no escape processing).
            if lower_ch == "r" and self._peek(1) in ('"', "'"):
                self._advance()  # consume 'r'/'R'
                self.tokens.append(self._read_raw_string())
                continue

            # b"..." / b'...' byte literals: emit as BYTES token (list[int]).
            if lower_ch == "b" and self._peek(1) in ('"', "'"):
                self._advance()  # consume 'b'/'B'
                tok = self._read_string()
                self.tokens.append(Token("BYTES", tok.value, tok.pos.line, tok.pos.col))
                continue

            if ch.isalpha() or ch == "_":
                self.tokens.append(self._read_identifier())
                continue

            # A leading-dot float (`.5`) starts with `.`, not a digit. `...` is
            # excluded because the char after the dot must be a digit, and an
            # attribute like `x.5` is not valid Python in the first place, so a
            # dot-then-digit is unambiguously a float.
            if ch.isdigit() or (ch == "." and self._peek(1).isdigit()):
                self.tokens.append(self._read_number())
                continue

            if ch == '"' or ch == "'":
                self.tokens.append(self._read_string())
                continue

            self.tokens.append(self._read_operator())

        while len(self.indents) > 1:
            self.indents.pop()
            self.tokens.append(Token("DEDENT", "", self.line, 1))
        self.tokens.append(Token("EOF", "", self.line, self.col))
        return self.tokens

    def _handle_indentation(self) -> None:
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
            self.tokens.append(Token("INDENT", depth, self.line, 1))
        else:
            while depth < self.indents[-1]:
                self.indents.pop()
                self.tokens.append(Token("DEDENT", "", self.line, 1))
            if depth != self.indents[-1]:
                raise LexError("inconsistent indentation", SourcePos(self.line, 1), ErrorCode.L_INCONSISTENT_INDENT)

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
        # A trailing-dot float (`5.`) has no digit after the point. CPython
        # consumes the dot unconditionally here -- `1.real` is a syntax error,
        # not attribute access on an int -- so requiring a following digit both
        # rejected `5.` and diverged from CPython. `1..real` still works: the
        # first dot ends the float, the second starts the attribute.
        if self._peek() == ".":
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
                raise LexError(f"invalid float literal {text!r}", SourcePos(line, col), ErrorCode.L_INVALID_FLOAT)
        return Token("INT", int(text), line, col)

    def _read_fstring(self, raw: bool = False) -> Token:
        """Read an f-string. Returns an FSTRING token whose value is a list
        of segments: ('str', text) or ('expr', raw_source_string).

        The parser will re-lex the expression segments later. This keeps the
        lexer's state-machine flat (we don't need to track 'inside-fstring-
        expression' depth across the main lex loop).

        Prefix letter(s) already consumed by the caller (matching
        _read_raw_string's convention) -- the only remaining character is
        the opening quote. `raw=True` is for the combined rf"..."/fr"..."
        form: literal-text segments keep backslashes verbatim (only an
        escaped closing quote is special, same exception _read_raw_string
        itself makes), while `{expr}` segments are unaffected either way
        (they're just source text the parser re-lexes on its own later).
        """
        line, col = self.line, self.col
        quote = self._advance()
        segments: list[tuple[str, str]] = []
        text: list[str] = []
        while True:
            c = self._peek()
            if c == "":
                raise LexError("unterminated f-string", SourcePos(line, col), ErrorCode.L_UNTERMINATED_FSTRING)
            if c == "\n":
                raise LexError("newline in f-string", SourcePos(line, col), ErrorCode.L_NEWLINE_IN_FSTRING)
            if c == quote:
                self._advance()
                break
            if c == "\\" and raw:
                # Only escaped-quote is special in a raw f-string (prevents
                # closing) -- everything else stays a literal backslash.
                self._advance()
                if self._peek() == quote:
                    text.append(self._advance())
                else:
                    text.append("\\")
                continue
            if c == "\\":
                self._advance()
                esc = self._advance()
                text.append(
                    {
                        "n": "\n",
                        "t": "\t",
                        "r": "\r",
                        "b": "\b",
                        "f": "\f",
                        "v": "\v",
                        "a": "\a",
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
                _expr, _spec, _conv = _split_fstring_spec(
                    "".join(expr_chars).strip()
                )
                segments.append(("expr", _expr, _spec, _conv))
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

    def _read_raw_string(self) -> Token:
        """Read r"..." or r'...': backslashes are literal, no escape processing.

        Also handles the triple-quoted form (r\"\"\"...\"\"\" / r'''...'''),
        exactly like _read_string does for a plain string -- real-world raw
        docstrings/strings spanning multiple lines are common (e.g. `requests`'
        own r\"\"\"Sends a GET request.\n...\"\"\"), and this reader used to have
        no triple-quote detection at all, so it always fell through to the
        single-line body loop below and raised "newline in string literal" on
        the first embedded newline. Confirmed via a real repro: asmpython's
        own CLI import-policy check walks a real pip-installed package's
        source tree and crashed trying to parse `requests/api.py`'s very
        first raw docstring.
        """
        line, col = self.line, self.col
        quote = self._advance()
        if self._peek() == quote and self._peek(1) == quote:
            self._advance()
            self._advance()
            return self._read_triple_string(quote, line, col, raw=True)
        chars: list[str] = []
        while True:
            c = self._peek()
            if c == "":
                raise LexError("unterminated raw string literal", SourcePos(line, col), ErrorCode.L_UNTERMINATED_RAW)
            if c == "\n":
                raise LexError("newline in raw string literal", SourcePos(line, col), ErrorCode.L_NEWLINE_IN_STRING)
            if c == quote:
                self._advance()
                break
            if c == "\\" and self._peek(1) == quote:
                # Only escaped-quote is special in raw strings (prevents closing).
                self._advance()
                chars.append(self._advance())
            else:
                chars.append(self._advance())
        return Token("STRING", "".join(chars), line, col)

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
                raise LexError("unterminated string literal", SourcePos(line, col), ErrorCode.L_UNTERMINATED_STRING)
            if c == "\n":
                raise LexError("newline in string literal", SourcePos(line, col), ErrorCode.L_NEWLINE_IN_STRING)
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
                        "b": "\b",
                        "f": "\f",
                        "v": "\v",
                        "a": "\a",
                        "0": "\0",
                        "\\": "\\",
                        "'": "'",
                        '"': '"',
                    }.get(esc, esc)
                )
            else:
                chars.append(self._advance())
        return Token("STRING", "".join(chars), line, col)

    def _read_triple_string(
        self, quote: str, line: int, col: int, raw: bool = False
    ) -> Token:
        """Reader for the body of a triple-quoted literal. Opening triple
        quote already consumed by the caller.

        `raw`: called from _read_raw_string for r\"\"\"...\"\"\"/r'''...'''
        (backslashes are literal, matching _read_raw_string's own single-line
        rule) -- the only special case is a backslash immediately before the
        closing quote character, which must not be allowed to prevent the
        triple-quote terminator from matching (same escaped-quote exception
        _read_raw_string's single-line path already has).
        """
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
                if raw:
                    chars.append(self._advance())
                    if self._peek() == quote:
                        chars.append(self._advance())
                    continue
                self._advance()
                esc = self._advance()
                chars.append(
                    {
                        "n": "\n",
                        "t": "\t",
                        "r": "\r",
                        "b": "\b",
                        "f": "\f",
                        "v": "\v",
                        "a": "\a",
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
        if three in ("//=", "**=", "<<=", ">>=", "..."):
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
            "**",
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
            ":=",
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
        raise LexError(f"unexpected character {ch!r}", SourcePos(line, col), ErrorCode.L_UNEXPECTED_CHAR)
