"""tokenize: Python source code tokenizer.

Generates tokens from Python source code. Implements the same token
types as the token module.

Usage::

    import tokenize

    tokens = tokenize.tokenize_string("x = 1 + 2")
    for tok in tokens:
        print(tok.type_name, repr(tok.string))
"""
from __future__ import annotations

import token as _token

# Token type constants (re-export from token)
ENDMARKER: int = _token.ENDMARKER
NAME: int = _token.NAME
NUMBER: int = _token.NUMBER
STRING: int = _token.STRING
NEWLINE: int = _token.NEWLINE
NL: int = _token.NL
INDENT: int = _token.INDENT
DEDENT: int = _token.DEDENT
COMMENT: int = _token.COMMENT
OP: int = _token.OP
ENCODING: int = _token.ENCODING
ERRORTOKEN: int = _token.ERRORTOKEN

# Additional types
EXACT_TYPE = OP


class TokenError(Exception):
    def __init__(self, msg: str = "", pos: list = [0, 0]) -> None:
        self.msg: str = msg
        self.pos: list = pos

    def __str__(self) -> str:
        return self.msg


class TokenInfo:
    """A single token from the tokenizer."""

    def __init__(self, type: int, string: str, start: list, end: list,
                 line: str) -> None:
        self.type: int = type
        self.string: str = string
        self.start: list = start  # [line, col]
        self.end: list = end      # [line, col]
        self.line: str = line

    def type_name(self) -> str:
        return _token.tok_name_of(self.type)

    def exact_type(self) -> int:
        return self.type

    def __repr__(self) -> str:
        return ("TokenInfo(type=" + str(self.type) + ", string=" +
                repr(self.string) + ", start=" + str(self.start) +
                ", end=" + str(self.end) + ")")


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------

_KEYWORDS: set = set()


def _init_keywords() -> None:
    for kw in ["False", "None", "True", "and", "as", "assert", "async",
               "await", "break", "class", "continue", "def", "del",
               "elif", "else", "except", "finally", "for", "from",
               "global", "if", "import", "in", "is", "lambda", "nonlocal",
               "not", "or", "pass", "raise", "return", "try", "while",
               "with", "yield"]:
        _KEYWORDS.add(kw)


_init_keywords()


def _is_keyword(s: str) -> int:
    return s in _KEYWORDS


# ---------------------------------------------------------------------------
# Main tokenizer
# ---------------------------------------------------------------------------

def tokenize_string(source: str) -> list:
    """Tokenize a Python source string. Returns list of TokenInfo."""
    tokens: list = []
    n: int = len(source)
    i: int = 0
    lineno: int = 1
    col_start: int = 0  # offset of line start
    indent_stack: list = [0]
    paren_depth: int = 0  # disable INDENT/DEDENT inside brackets

    tokens.append(TokenInfo(ENCODING, "utf-8", [0, 0], [0, 0], ""))

    while i < n:
        line_col: int = i - col_start
        c: str = source[i]

        # Newline handling
        if c in "\r\n":
            if paren_depth == 0:
                # Physical newline is significant
                end: int = i + 1
                if c == "\r" and i + 1 < n and source[i + 1] == "\n":
                    end = i + 2
                tokens.append(TokenInfo(NEWLINE, source[i:end],
                                        [lineno, line_col], [lineno, line_col + 1],
                                        ""))
                if c == "\r" and i + 1 < n and source[i + 1] == "\n":
                    i = i + 2
                else:
                    i = i + 1
                lineno = lineno + 1
                col_start = i
                # Handle indentation
                indent: int = 0
                while i < n and source[i] in " \t":
                    if source[i] == "\t":
                        indent = (indent + 8) & ~7
                    else:
                        indent = indent + 1
                    i = i + 1
                if i < n and source[i] not in "\r\n#":
                    top: int = indent_stack[len(indent_stack) - 1]
                    if indent > top:
                        tokens.append(TokenInfo(INDENT, "",
                                                [lineno, 0], [lineno, 0], ""))
                        indent_stack.append(indent)
                    while indent < top and len(indent_stack) > 1:
                        indent_stack = indent_stack[:len(indent_stack) - 1]
                        top = indent_stack[len(indent_stack) - 1]
                        tokens.append(TokenInfo(DEDENT, "",
                                                [lineno, 0], [lineno, 0], ""))
            else:
                # Inside brackets: NL
                tokens.append(TokenInfo(NL, c,
                                        [lineno, line_col], [lineno, line_col + 1],
                                        ""))
                i = i + 1
                lineno = lineno + 1
                col_start = i
            continue

        # Whitespace
        if c in " \t":
            i = i + 1
            continue

        # Comments
        if c == "#":
            start_i: int = i
            while i < n and source[i] not in "\r\n":
                i = i + 1
            tokens.append(TokenInfo(COMMENT, source[start_i:i],
                                    [lineno, line_col], [lineno, i - col_start], ""))
            continue

        # String literals
        if c in ('"', "'") or (c in "bBrRuUfF" and i + 1 < n and
                                source[i + 1] in ('"', "'", "b", "B", "r", "R")):
            start_i = i
            # Collect prefix
            prefix: str = ""
            while i < n and source[i] in "bBrRuUfF":
                prefix = prefix + source[i]
                i = i + 1
            if i >= n:
                break
            quote: str = source[i]
            i = i + 1
            # Triple quote?
            triple: int = 0
            if i + 1 < n and source[i] == quote and source[i + 1] == quote:
                triple = 1
                i = i + 2
            # Read until end quote
            if triple:
                end_q: str = quote + quote + quote
                end_pos: int = source.find(end_q, i)
                if end_pos < 0:
                    end_pos = n
                    i = n
                else:
                    i = end_pos + 3
            else:
                while i < n and source[i] != quote and source[i] not in "\r\n":
                    if source[i] == "\\" and i + 1 < n:
                        i = i + 2
                    else:
                        i = i + 1
                if i < n and source[i] == quote:
                    i = i + 1
            tok_str: str = source[start_i:i]
            tokens.append(TokenInfo(STRING, tok_str,
                                    [lineno, line_col], [lineno, i - col_start], ""))
            continue

        # Numbers
        if c.isdigit() or (c == "." and i + 1 < n and source[i + 1].isdigit()):
            start_i = i
            if c == "0" and i + 1 < n and source[i + 1] in "xXbBoO":
                # Hex/binary/octal
                i = i + 2
                while i < n and (source[i].isalnum() or source[i] == "_"):
                    i = i + 1
            else:
                while i < n and (source[i].isdigit() or source[i] == "_"):
                    i = i + 1
                if i < n and source[i] == ".":
                    i = i + 1
                    while i < n and (source[i].isdigit() or source[i] == "_"):
                        i = i + 1
                if i < n and source[i] in "eE":
                    i = i + 1
                    if i < n and source[i] in "+-":
                        i = i + 1
                    while i < n and source[i].isdigit():
                        i = i + 1
                if i < n and source[i] in "jJ":
                    i = i + 1
            tok_str = source[start_i:i]
            tokens.append(TokenInfo(NUMBER, tok_str,
                                    [lineno, line_col], [lineno, i - col_start], ""))
            continue

        # Identifiers and keywords
        if c.isalpha() or c == "_":
            start_i = i
            while i < n and (source[i].isalnum() or source[i] == "_"):
                i = i + 1
            word: str = source[start_i:i]
            tok_type: int = NAME
            tokens.append(TokenInfo(tok_type, word,
                                    [lineno, line_col], [lineno, i - col_start], ""))
            continue

        # Operators and brackets
        if c in "([{":
            paren_depth = paren_depth + 1
        elif c in ")]}":
            paren_depth = paren_depth - 1
            if paren_depth < 0:
                paren_depth = 0

        # Multi-char operators
        two: str = source[i:i + 2] if i + 1 < n else ""
        three: str = source[i:i + 3] if i + 2 < n else ""

        op: str = ""
        if three in ("...", "**=", "//=", "<<=", ">>="):
            op = three
        elif two in ("==", "!=", "<=", ">=", "**", "//", "<<", ">>",
                     "+=", "-=", "*=", "/=", "%=", "&=", "|=", "^=",
                     "->", ":=", "@="):
            op = two
        elif c in "+-*/%&|^~<>=!@.,:;()[]{}":
            op = c

        if op:
            tokens.append(TokenInfo(OP, op,
                                    [lineno, line_col],
                                    [lineno, line_col + len(op)], ""))
            i = i + len(op)
        else:
            tokens.append(TokenInfo(ERRORTOKEN, c,
                                    [lineno, line_col], [lineno, line_col + 1], ""))
            i = i + 1

    # Emit remaining DEDENTs
    while len(indent_stack) > 1:
        indent_stack = indent_stack[:len(indent_stack) - 1]
        tokens.append(TokenInfo(DEDENT, "", [lineno, 0], [lineno, 0], ""))

    tokens.append(TokenInfo(NEWLINE, "", [lineno, 0], [lineno, 0], ""))
    tokens.append(TokenInfo(ENDMARKER, "", [lineno, 0], [lineno, 0], ""))
    return tokens


def generate_tokens(readline: int) -> list:
    """Tokenize source via a readline() callable.

    Since asmpython doesn't support generators, returns a list of all tokens.
    Pass a function that returns successive lines, or None when done.
    """
    lines: list = []
    fn: int = readline
    while True:
        line: str = fn()
        if not line:
            break
        lines.append(line)
    source: str = "".join(lines)
    return tokenize_string(source)


def tokenize(readline: int) -> list:
    """Tokenize binary source (identical to generate_tokens in asmpython)."""
    return generate_tokens(readline)


def untokenize(iterable: list) -> str:
    """Convert tokens back to source text."""
    result: str = ""
    prev_end: list = [1, 0]
    for tok in iterable:
        if tok.start[0] > prev_end[0]:
            result = result + "\n"
            prev_end = [tok.start[0], 0]
        gap: int = tok.start[1] - prev_end[1]
        if gap > 0:
            j: int = 0
            while j < gap:
                result = result + " "
                j = j + 1
        result = result + tok.string
        prev_end = tok.end
    return result


def detect_encoding(readline: int) -> list:
    """Detect encoding of Python source. Returns [encoding, lines]."""
    return ["utf-8", []]


def open(filename: str) -> object:
    """Open a file for tokenization (returns file object)."""
    return _open_builtin(filename, "r")


_open_builtin = open
