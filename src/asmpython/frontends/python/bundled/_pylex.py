"""Turn Python source into tokens.

Written in Python and compiled by this compiler, because a produced binary has
no CPython to borrow `tokenize` from -- and `compile()`, `exec()` and `eval()`
are the three builtins that need one at RUN time. See `_pycompile` for what is
built on top.

THE ONLY THING THIS HAS TO GET EXACTLY RIGHT is which inputs are ill-formed.
A tokeniser that accepts too much makes `compile()` answer `accepted` where
CPython raises `SyntaxError`, and that is a wrong answer rather than a missing
feature -- so every rule below that REFUSES something is written out with the
spelling it refuses.

AN F-STRING IS ONE TOKEN HERE, where CPython 3.12 and later split it into a
start, the literal pieces, the tokens of each replacement expression, and an
end. Deliberate: what is inside one is only checkable once there is a parser
to check it with -- `f"{}"` is empty, `f"{1+}"` does not parse, `f"{x!z}"`
names a conversion that does not exist -- so the literal is kept whole and
`_pyparse` cracks it open. That is the ONE place this model differs from
CPython's, and it differs in shape rather than in what it accepts.

WHAT IS DELIBERATELY NOT HERE: the encoding declaration (source arrives as
text already), and the `type_comments` mode.
"""

#: What a token IS. Strings rather than an enum: the parser compares them,
#: they appear in messages, and an enum would be one more thing to keep in
#: step for no gain a program can see.
NAME = "NAME"
NUMBER = "NUMBER"
STRING = "STRING"
OP = "OP"
NEWLINE = "NEWLINE"
INDENT = "INDENT"
DEDENT = "DEDENT"
END = "END"

#: The keywords, which are NAMEs the parser treats specially. `match`, `case`
#: and `type` are NOT here: they are SOFT keywords, meaningful only in the
#: position that starts one of their statements, and a program is free to use
#: them as ordinary names everywhere else.
KEYWORDS = frozenset({
    "False", "None", "True", "and", "as", "assert", "async", "await", "break",
    "class", "continue", "def", "del", "elif", "else", "except", "finally",
    "for", "from", "global", "if", "import", "in", "is", "lambda", "nonlocal",
    "not", "or", "pass", "raise", "return", "try", "while", "with", "yield",
})

#: The string prefixes Python accepts, lowercased. `u` may not combine with
#: anything -- `ur"x"` was Python 2 and is a SyntaxError now -- and `b` may
#: not combine with `f`, because bytes have no formatting.
_PREFIXES = frozenset({
    "", "r", "b", "f", "u", "t",
    "rb", "br", "rf", "fr", "rt", "tr",
})

#: Operators, LONGEST FIRST. The order is the whole of the maximal-munch rule:
#: `**=` must be tried before `**`, and `**` before `*`, or `a **= b` becomes
#: three tokens that parse as something else.
_OPERATORS = (
    "**=", "//=", ">>=", "<<=", "...", "!=",
    ">=", "<=", "==", "->", ":=", "+=", "-=", "*=", "/=", "%=", "@=", "&=",
    "|=", "^=", "**", "//", "<<", ">>",
    "+", "-", "*", "/", "%", "@", "&", "|", "^", "~", "<", ">", "(", ")",
    "[", "]", "{", "}", ",", ":", ".", ";", "=",
)

_OPENERS = "([{"
_CLOSERS = ")]}"
_MATCHING = {")": "(", "]": "[", "}": "{"}


class Token:
    """One token: what it is, what it said, and where it was.

    The position is kept as a LINE AND COLUMN rather than an offset, because
    that is what a `SyntaxError` reports and what a program reads back off one.
    """

    def __init__(self, kind, value, line, col):
        self.kind = kind
        self.value = value
        self.line = line
        self.col = col

    def __repr__(self):
        return "Token(" + self.kind + ", " + repr(self.value) + ", line " \
               + str(self.line) + ")"


class LexError(Exception):
    """Source that cannot be tokenised.

    Carries the position so `_pycompile` can build the `SyntaxError` a program
    catches, with the line and offset CPython would give it.
    """

    def __init__(self, message, line, col):
        # `super()`, not `Exception.__init__(self, ...)`: the second
        # spelling asks a builtin exception TYPE for an attribute, and this
        # runtime's exception hierarchy is a table of names rather than a
        # chain of classes with methods on them.
        super().__init__(message)
        self.msg = message
        self.line = line
        self.col = col


class IndentError(LexError):
    """Source whose INDENTATION is wrong, as distinct from its tokens.

    Its own class because a program catches it by name: `IndentationError` is
    a subclass of `SyntaxError` in Python, and `except IndentationError:`
    around a `compile()` is exactly what the conformance case writes. One
    exception for both would make that clause never fire.
    """


def _is_name_start(ch):
    # `isidentifier` ON ONE CHARACTER asks exactly XID_Start, which is the
    # rule -- and it is right for the non-ASCII half too, which a range test
    # would not be.
    return ch == "_" or ch.isidentifier()


def _is_name_part(ch):
    return ch == "_" or ("a" + ch).isidentifier()


class Lexer:
    """The tokeniser, as an object because it carries position and state.

    THREE PIECES OF STATE and each earns its place: `depth` is how many
    brackets are open (inside them a newline is not a NEWLINE), `stack` is the
    indentation column stack (which produces INDENT and DEDENT), and `at_line`
    says whether indentation is still being measured.
    """

    def __init__(self, source):
        self.src = source
        self.n = len(source)
        self.i = 0
        self.line = 1
        self.col = 0
        self.depth = 0
        self.stack = [0]
        self.tokens = []
        #: Whether the last emitted token ended a logical line, so the next
        #: non-blank line measures its indentation.
        self.at_line = True

    # -- reading ---------------------------------------------------------
    def _peek(self, ahead=0):
        at = self.i + ahead
        return self.src[at] if at < self.n else ""

    def _advance(self, count=1):
        for _ in range(count):
            if self.i < self.n:
                if self.src[self.i] == "\n":
                    self.line = self.line + 1
                    self.col = 0
                else:
                    self.col = self.col + 1
                self.i = self.i + 1

    def _emit(self, kind, value, line, col):
        self.tokens.append(Token(kind, value, line, col))

    def _fail(self, message):
        raise LexError(message, self.line, self.col)

    # -- the main loop ---------------------------------------------------
    def run(self):
        """Every token, ending with a NEWLINE, the closing DEDENTs and END."""
        while self.i < self.n:
            if self.at_line and self.depth == 0:
                if not self._indentation():
                    continue
            ch = self._peek()
            if ch == "":
                break
            if ch == "\n":
                self._newline()
                continue
            if ch == "\r":
                self._advance()
                continue
            if ch == "#":
                while self.i < self.n and self._peek() != "\n":
                    self._advance()
                continue
            if ch == "\\" and self._peek(1) in ("\n", "\r"):
                # A LINE CONTINUATION joins the next line to this one, so no
                # NEWLINE is produced and no indentation is measured.
                self._advance()
                while self.i < self.n and self._peek() in ("\r", "\n"):
                    self._advance()
                continue
            if ch == " " or ch == "\t" or ch == "\f":
                self._advance()
                continue
            if self._string_here():
                self._string()
                continue
            if _is_name_start(ch):
                self._name()
                continue
            if ch.isdigit() or (ch == "." and self._peek(1).isdigit()):
                self._number()
                continue
            self._operator()
        return self._finish()

    def _finish(self):
        if self.depth:
            raise LexError("'(' was never closed", self.line, self.col)
        # A FILE THAT DOES NOT END IN A NEWLINE still ends a logical line, and
        # a parser written against "every suite ends with NEWLINE" needs one.
        if self.tokens and self.tokens[-1].kind not in (NEWLINE, INDENT,
                                                        DEDENT):
            self._emit(NEWLINE, "", self.line, self.col)
        while len(self.stack) > 1:
            del self.stack[-1]
            self._emit(DEDENT, "", self.line, self.col)
        self._emit(END, "", self.line, self.col)
        return self.tokens

    # -- indentation -----------------------------------------------------
    def _indentation(self):
        """Measure the indentation of a line and emit INDENT/DEDENT.

        Answers False for a line with nothing on it, which is skipped
        entirely: a blank or comment-only line has no indentation to speak of
        and must not produce a DEDENT.
        """
        width = 0
        start = self.i
        while self.i < self.n:
            ch = self._peek()
            if ch == " ":
                width = width + 1
            elif ch == "\t":
                # A TAB ADVANCES TO THE NEXT MULTIPLE OF EIGHT, which is the
                # rule the reference implementation uses when it has to
                # compare a tabbed line with a spaced one.
                width = width + 8 - (width % 8)
            else:
                break
            self._advance()
        ch = self._peek()
        if ch == "" or ch == "\n" or ch == "\r" or ch == "#":
            # NOTHING ON THIS LINE, so the indent stack must not move: a
            # blank or comment-only line has no indentation to speak of and
            # must not produce a DEDENT.
            #
            # THE REST OF THE LINE IS CONSUMED HERE. Answering False without
            # it left the caller looking at the same blank line forever, with
            # `at_line` still set -- the one place in this file where doing
            # nothing is an infinite loop rather than a no-op.
            while self.i < self.n and self._peek() != "\n":
                self._advance()
            if self.i < self.n:
                self._advance()
            return False
        self.at_line = False
        if width > self.stack[-1]:
            self.stack.append(width)
            self._emit(INDENT, self.src[start:self.i], self.line, 0)
            return True
        while len(self.stack) > 1 and width < self.stack[-1]:
            del self.stack[-1]
            self._emit(DEDENT, "", self.line, width)
        if width != self.stack[-1]:
            # A DEDENT TO A COLUMN NOBODY OPENED. This is the one indentation
            # mistake that is not a missing or extra block, and CPython names
            # it separately.
            raise IndentError("unindent does not match any outer indentation "
                           "level", self.line, width)
        return True

    def _newline(self):
        self._advance()
        if self.depth:
            # INSIDE BRACKETS A NEWLINE IS WHITESPACE, which is what lets a
            # call or a display span lines without continuations.
            return
        if self.tokens and self.tokens[-1].kind not in (NEWLINE, INDENT,
                                                        DEDENT):
            self._emit(NEWLINE, "", self.line - 1, self.col)
        self.at_line = True

    # -- names -----------------------------------------------------------
    def _name(self):
        line, col, start = self.line, self.col, self.i
        while self.i < self.n and _is_name_part(self._peek()):
            self._advance()
        self._emit(NAME, self.src[start:self.i], line, col)

    # -- numbers ---------------------------------------------------------
    def _number(self):
        line, col, start = self.line, self.col, self.i
        text = ""
        if self._peek() == "0" and self._peek(1) in ("x", "X", "o", "O",
                                                     "b", "B"):
            base = self._peek(1).lower()
            self._advance(2)
            digits = self._digits(
                "0123456789abcdefABCDEF" if base == "x"
                else "01234567" if base == "o" else "01", True)
            if not digits:
                self._fail("invalid " + ("hexadecimal" if base == "x"
                                         else "octal" if base == "o"
                                         else "digital") + " literal")
            self._emit(NUMBER, self.src[start:self.i], line, col)
            return
        whole = self._digits("0123456789")
        text = whole
        seen_dot = False
        seen_exp = False
        if self._peek() == "." :
            seen_dot = True
            self._advance()
            text = text + "." + self._digits("0123456789")
        if self._peek() in ("e", "E") and (self._peek(1).isdigit()
                                           or (self._peek(1) in ("+", "-")
                                               and self._peek(2).isdigit())):
            seen_exp = True
            self._advance()
            if self._peek() in ("+", "-"):
                self._advance()
            self._digits("0123456789")
        if self._peek() in ("j", "J"):
            self._advance()
            self._emit(NUMBER, self.src[start:self.i], line, col)
            return
        # A LEADING ZERO ON A DECIMAL IS REFUSED -- `01` was octal in Python 2
        # and is a SyntaxError now, while `0`, `00` and `0.5` are all fine.
        # This is the one number rule that exists only to reject.
        if not seen_dot and not seen_exp and len(whole) > 1 \
                and whole.replace("_", "")[0] == "0" \
                and whole.replace("_", "").strip("0") != "":
            raise LexError("leading zeros in decimal integer literals are not "
                           "permitted; use an 0o prefix for octal integers",
                           line, col)
        self._emit(NUMBER, self.src[start:self.i], line, col)

    def _digits(self, allowed, after_prefix=False):
        """Digits of one base, with UNDERSCORES as separators.

        An underscore may sit only BETWEEN digits, which is why the previous
        character is tested rather than simply skipping them -- except
        directly after a base prefix, where `0x_FF` is legal because the
        prefix is what the underscore separates from.
        """
        out = ""
        last_was_underscore = not after_prefix
        while self.i < self.n:
            ch = self._peek()
            if ch == "_":
                if last_was_underscore:
                    self._fail("invalid decimal literal")
                last_was_underscore = True
                out = out + ch
                self._advance()
                continue
            if allowed.find(ch) < 0:
                break
            last_was_underscore = False
            out = out + ch
            self._advance()
        if out and out[-1] == "_":
            self._fail("invalid decimal literal")
        return out

    # -- strings ---------------------------------------------------------
    def _string_here(self):
        """Is a string literal starting here, prefix and all?"""
        ch = self._peek()
        if ch == '"' or ch == "'":
            return True
        if not _is_name_start(ch):
            return False
        ahead = 0
        while ahead < 3 and _is_name_part(self._peek(ahead)):
            ahead = ahead + 1
        return self._peek(ahead) == '"' or self._peek(ahead) == "'"

    def _string(self):
        line, col, start = self.line, self.col, self.i
        prefix = ""
        while self._peek() not in ('"', "'"):
            prefix = prefix + self._peek()
            self._advance()
        lowered = prefix.lower()
        if lowered not in _PREFIXES:
            # `ur"x"` IS THE ONE THAT MATTERS: it was Python 2 and is refused
            # now, and a tokeniser that shrugged at unknown prefixes would
            # accept it.
            raise LexError("invalid syntax", line, col)
        quote = self._peek()
        triple = self._peek(1) == quote and self._peek(2) == quote
        self._advance(3 if triple else 1)
        raw = lowered.find("r") >= 0
        formatted = lowered.find("f") >= 0 or lowered.find("t") >= 0
        #: How many replacement fields are open. PEP 701 lets a nested string
        #: inside one reuse the enclosing quote, so at depth zero a quote ends
        #: the literal and inside a field it opens another.
        braces = 0
        while True:
            if self.i >= self.n:
                raise LexError(
                    "unterminated triple-quoted string literal" if triple
                    else "unterminated string literal", line, col)
            ch = self._peek()
            if ch == "\\" and not raw:
                # AN ESCAPE CONSUMES THE NEXT CHARACTER WHATEVER IT IS, so a
                # backslash before the closing quote does not end the string.
                self._advance(2)
                continue
            if ch == "\\" and raw:
                # EVEN RAW, a backslash still hides the next quote from the
                # scanner -- `r"\""` is one string. It stays in the text.
                self._advance(2)
                continue
            if formatted and ch == "{":
                if self._peek(1) == "{":
                    self._advance(2)
                    continue
                braces = braces + 1
            elif formatted and ch == "}" and braces:
                braces = braces - 1
            if ch == "\n" and not triple:
                raise LexError("unterminated string literal", line, col)
            if ch == quote and braces:
                # PEP 701: A NESTED STRING INSIDE A REPLACEMENT FIELD may use
                # the same quote -- `f"{d["k"]}"` is one f-string in 3.12 and
                # later. Consumed whole here, so its quotes cannot end the
                # literal that encloses it.
                self._advance()
                while self.i < self.n and self._peek() != quote:
                    if self._peek() == "\\":
                        self._advance()
                    self._advance()
                if self.i >= self.n:
                    raise LexError("unterminated string literal", line, col)
                self._advance()
                continue
            if ch == quote:
                if not triple:
                    self._advance()
                    break
                if self._peek(1) == quote and self._peek(2) == quote:
                    self._advance(3)
                    break
            self._advance()
        self._emit(STRING, self.src[start:self.i], line, col)

    # -- operators -------------------------------------------------------
    def _operator(self):
        line, col = self.line, self.col
        for op in _OPERATORS:
            if self.src[self.i:self.i + len(op)] == op:
                if op in _OPENERS:
                    self.depth = self.depth + 1
                elif op in _CLOSERS:
                    if self.depth <= 0:
                        raise LexError("unmatched '" + op + "'", line, col)
                    self.depth = self.depth - 1
                self._advance(len(op))
                self._emit(OP, op, line, col)
                return
        self._fail("invalid character '" + self._peek() + "'")


def tokenize(source):
    """Every token in `source`, or a `LexError` naming where it went wrong."""
    return Lexer(source).run()
