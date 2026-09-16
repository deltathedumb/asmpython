"""Translation phases 1 to 3: source text to preprocessing tokens.

    1. map the physical source characters (line endings; trigraphs, if asked)
    2. splice every backslash-newline away
    3. replace each comment with a space, and decompose what is left into
       preprocessing tokens

WHY PHASES 1 AND 2 ARE A PRECOMPUTED STRING AND NOT A CHARACTER READER.
Both delete characters, and a reader that skips them on the fly makes every
lookahead a loop -- `strn` has to look four characters ahead to reject `strn\\
cmp` as one identifier, and each of those four may itself sit behind a splice.
The cost of that is quadratic in the pathological case and, far worse, it is
paid in every `_peek` in this file. So the two phases run once, producing a
LOGICAL TEXT and an offset map back into the original; everything after reads a
plain string with plain indices.

THE MAP IS WHY DIAGNOSTICS STILL POINT AT THE USER'S SOURCE. A span is a byte
range in a `SourceFile`, and the file holds what the user wrote -- with the
splices and the trigraphs still in it. `_orig[i]` is the offset in that text of
logical character `i`, so an identifier written

    va\\
    lue

is one token whose span covers both lines, and the caret is drawn where the
name starts rather than at some offset into a string the user has never seen.
"""
from __future__ import annotations

from ...diagnostics import DiagnosticSink, SourceFile, Span, error
from .tokens import DIGRAPHS, Kind, PUNCTUATORS, Token, eof_token

#: Phase 1's trigraphs. OFF BY DEFAULT, and that is not laziness: C23 deleted
#: them, every compiler that still has them needs a flag, and leaving them on
#: means `printf("what??!\n")` prints `what|`. A program that wants them asks.
TRIGRAPHS: dict[str, str] = {
    "=": "#", "(": "[", "/": "\\", ")": "]", "'": "^",
    "<": "{", "!": "|", ">": "}", "-": "~",
}

_IDENT_START = "_abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
_DIGITS = "0123456789"
_IDENT_CONT = _IDENT_START + _DIGITS


def _is_ident_start(ch: str) -> bool:
    # NON-ASCII IS AN IDENTIFIER CHARACTER. C99 allowed it through universal
    # character names, C11 through extended characters directly, and C23
    # spells the whole rule in terms of XID_Start. Accepting any character
    # above ASCII is more permissive than C23's table and refuses nothing a
    # conforming program writes; the alternative is shipping a Unicode
    # property table the compiler has no other use for.
    return ch in _IDENT_START or ord(ch) > 127


def _is_ident_cont(ch: str) -> bool:
    return ch in _IDENT_CONT or ord(ch) > 127


def phase12(text: str, *, trigraphs: bool = False) -> tuple[str, list[int]]:
    """Phases 1 and 2. Returns the logical text and its offset map.

    The map is one entry per logical character plus a final entry for the end,
    so `_orig[i]` is always a valid index to build a span from -- including at
    the very end of file, where "expected `}`" has to point somewhere.
    """
    out: list[str] = []
    orig: list[int] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        # Phase 1: line endings. A file written on Windows and one written on
        # Unix must tokenise identically, and the difference must not reach a
        # string literal's contents either.
        if ch == "\r":
            out.append("\n")
            orig.append(i)
            i += 2 if i + 1 < n and text[i + 1] == "\n" else 1
            continue
        if trigraphs and ch == "?" and text[i:i + 2] == "??" and i + 2 < n:
            mapped = TRIGRAPHS.get(text[i + 2])
            if mapped is not None:
                out.append(mapped)
                orig.append(i)
                i += 3
                continue
        # Phase 2: a backslash immediately before a newline deletes both. The
        # newline may be `\r\n`, because phase 1 has not been applied to the
        # text this loop is reading -- the two phases are fused here so the
        # offset map has one entry per surviving character either way.
        if ch == "\\":
            j = i + 1
            if j < n and text[j] == "\r":
                j += 1
                if j < n and text[j] == "\n":
                    j += 1
                i = j
                continue
            if j < n and text[j] == "\n":
                i = j + 1
                continue
        out.append(ch)
        orig.append(i)
        i += 1
    orig.append(n)
    return "".join(out), orig


class Lexer:
    """Phase 3. `tokens()` is the whole interface."""

    def __init__(self, source: SourceFile, sink: DiagnosticSink, *,
                 trigraphs: bool = False) -> None:
        self.source = source
        self.sink = sink
        self.text, self._orig = phase12(source.text, trigraphs=trigraphs)
        self.pos = 0
        self.n = len(self.text)

    # ── positions ───────────────────────────────────────────────────────────
    def span(self, start: int, end: int) -> Span:
        """A span over the ORIGINAL source for a logical range."""
        a = self._orig[min(start, len(self._orig) - 1)]
        b = self._orig[min(end, len(self._orig) - 1)]
        return Span(self.source, a, max(a, b))

    def _at(self, k: int = 0) -> str:
        i = self.pos + k
        return self.text[i] if i < self.n else "\0"

    def _starts(self, s: str) -> bool:
        return self.text.startswith(s, self.pos)

    # ── the scan ────────────────────────────────────────────────────────────
    def tokens(self) -> list[Token]:
        """Every preprocessing token in the file, ending with EOF."""
        out: list[Token] = []
        ws = False
        bol = True
        while True:
            ws, bol, stop = self._skip_space(ws, bol)
            if stop:
                break
            start = self.pos
            tok = self._one()
            if tok is None:
                # A character that begins no other token IS a preprocessing
                # token -- 6.4p1's last alternative, "each non-white-space
                # character that cannot be one of the above". So phase 3
                # accepts it and says nothing, and the PARSER reports it if it
                # ever reaches phase 7. That is not pedantry: `#if 0` around a
                # block of another language's source is ordinary, and a lexer
                # that complains about a `@` inside one complains about text
                # the program deleted.
                self.pos = start + 1
                tok = Token(Kind.OTHER, self.text[start],
                            self.span(start, start + 1))
            tok.ws, tok.bol = ws, bol
            out.append(tok)
            ws, bol = False, False
        out.append(eof_token(self.span(self.n, self.n)))
        return out

    def _skip_space(self, ws: bool, bol: bool) -> tuple[bool, bool, bool]:
        """Consume whitespace and comments. Returns (ws, bol, at_eof)."""
        while self.pos < self.n:
            ch = self.text[self.pos]
            if ch == "\n":
                self.pos += 1
                ws, bol = True, True
                continue
            if ch in " \t\v\f":
                self.pos += 1
                ws = True
                continue
            if self._starts("/*"):
                begin = self.pos
                end = self.text.find("*/", self.pos + 2)
                if end < 0:
                    self.sink.report(
                        error("E1002", "unterminated comment")
                        .at(self.span(begin, begin + 2))
                        .note("a /* comment runs to the next */, across lines"))
                    self.pos = self.n
                    return True, bol, True
                # A COMMENT IS ONE SPACE, not nothing: `a/**/b` is two
                # tokens. A comment spanning lines does NOT end a directive --
                # `#define X 1 /* a\n */ + 2` defines X as `1 + 2` -- which is
                # why `bol` is left alone here and only a real newline sets it.
                self.pos = end + 2
                ws = True
                continue
            if self._starts("//"):
                end = self.text.find("\n", self.pos)
                self.pos = self.n if end < 0 else end
                ws = True
                continue
            return ws, bol, False
        return ws, bol, True

    def _one(self) -> Token | None:
        """The token starting at `self.pos`, or None if nothing does."""
        ch = self._at()
        start = self.pos

        # A pp-number: a digit, or a `.` followed by one. `.5` is a number and
        # `.x` is a `.` -- so the lookahead is load-bearing.
        if ch in _DIGITS or (ch == "." and self._at(1) in _DIGITS):
            return self._number()

        # An encoding prefix belongs to the literal that follows it and to
        # nothing else: `u8"x"` is one token, `u8` alone is an identifier.
        for prefix in ("u8", "u", "U", "L"):
            if self._starts(prefix):
                after = self._at(len(prefix))
                if after == '"':
                    return self._quoted('"', Kind.STRING, len(prefix))
                if after == "'":
                    # `u8'x'` is C23 and the other three are older. Accepting
                    # all four costs nothing; refusing one refuses a
                    # conforming program.
                    return self._quoted("'", Kind.CHARCONST, len(prefix))

        if ch == '"':
            return self._quoted('"', Kind.STRING, 0)
        if ch == "'":
            return self._quoted("'", Kind.CHARCONST, 0)

        if _is_ident_start(ch) or (ch == "\\" and self._at(1) in "uU"):
            return self._identifier()

        for p in PUNCTUATORS:
            if self._starts(p):
                # `...` must not be found as `.` `.` `.`, and `%:%:` must not
                # be found as `%:` `%:` -- PUNCTUATORS is ordered longest-first
                # so the first hit is the longest. The one case that is NOT
                # decided by length is `<::`, which C++ special-cases and C
                # does not: `a<::b` is `a`, `<:`, `:`, `b` here, exactly as
                # the standard's maximal munch requires.
                self.pos += len(p)
                return Token(Kind.PUNCT, DIGRAPHS.get(p, p),
                             self.span(start, self.pos))
        return None

    def _number(self) -> Token:
        """A pp-number. Its VALUE is not computed here -- see tokens.py."""
        start = self.pos
        self.pos += 1
        while self.pos < self.n:
            ch = self.text[self.pos]
            if ch in "eEpP" and self._at(1) in "+-":
                # An exponent sign is part of the number: `1e+5` is one token.
                # This is also why `0xEp+q` is one token and a later error
                # rather than a lexing failure.
                self.pos += 2
                continue
            if ch == "'" and _is_ident_cont(self._at(1)):
                # C23 digit separators: 1'000'000. Only between digits, which
                # the lookahead approximates; a trailing `1'` would otherwise
                # swallow the next character constant.
                self.pos += 2
                continue
            if _is_ident_cont(ch) or ch == ".":
                self.pos += 1
                continue
            break
        return Token(Kind.NUMBER, self.text[start:self.pos],
                     self.span(start, self.pos))

    def _identifier(self) -> Token:
        start = self.pos
        while self.pos < self.n:
            ch = self.text[self.pos]
            if _is_ident_cont(ch):
                self.pos += 1
                continue
            # A universal character name is part of the identifier that
            # contains it: `Ångström` is one name, and splitting it
            # would produce a stray backslash and two identifiers.
            if ch == "\\" and self._at(1) in "uU":
                want = 4 if self._at(1) == "u" else 8
                digits = self.text[self.pos + 2:self.pos + 2 + want]
                if len(digits) == want and all(
                        c in "0123456789abcdefABCDEF" for c in digits):
                    self.pos += 2 + want
                    continue
            break
        return Token(Kind.IDENT, self.text[start:self.pos],
                     self.span(start, self.pos))

    def _quoted(self, quote: str, kind: Kind, prefix: int) -> Token:
        """A string literal or character constant, spelling intact.

        ESCAPES ARE NOT DECODED HERE. The only thing this needs to know about
        `\\` is that it protects the next character from ending the literal --
        decoding `\\x41` means knowing the encoding prefix of the whole
        concatenation, which phase 6 has not run yet.
        """
        start = self.pos
        self.pos += prefix + 1
        while self.pos < self.n:
            ch = self.text[self.pos]
            if ch == "\\" and self.pos + 1 < self.n:
                self.pos += 2
                continue
            if ch == quote:
                self.pos += 1
                if kind is Kind.CHARCONST and self.pos - start == prefix + 2:
                    self.sink.report(
                        error("E1004", "empty character constant")
                        .at(self.span(start, self.pos))
                        .help("write '\\0' for a null character"))
                return Token(kind, self.text[start:self.pos],
                             self.span(start, self.pos))
            if ch == "\n":
                break
            self.pos += 1
        what = "string literal" if kind is Kind.STRING else "character constant"
        self.sink.report(
            error("E1003", f"unterminated {what}")
            .at(self.span(start, min(self.pos, self.n)))
            .note("a literal may not contain a newline; end it, or continue "
                  "it with a backslash at the end of the line"))
        return Token(kind, self.text[start:self.pos] + quote,
                     self.span(start, self.pos))


def lex(source: SourceFile, sink: DiagnosticSink, *,
        trigraphs: bool = False) -> list[Token]:
    """Every preprocessing token in `source`, ending with EOF."""
    return Lexer(source, sink, trigraphs=trigraphs).tokens()
