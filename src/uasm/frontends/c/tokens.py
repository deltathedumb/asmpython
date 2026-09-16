"""C tokens, and the one distinction that costs a compiler a rewrite if it is
got wrong: a PREPROCESSING token is not a token.

`0x1p-3`, `1.2.3` and `0xEp+q` are all one *pp-number* -- a single
preprocessing token, by the grammar in 6.4.8 -- and only the first is a valid
C constant. A lexer that tries to produce a `FLOAT` with a value while it is
still in translation phase 3 has to decide what `1.2.3` is before anything has
told it whether that text will even survive to phase 7 (`#if 0` may delete it,
`##` may paste it into something valid). So phase 3 produces NUMBER carrying
its spelling and nothing else, and the value is computed in `sema.py`, once,
where a bad one is a real error at a real place.

The same rule is why STRING and CHARCONST carry their spelling with the quotes
and prefix still on. Adjacent string literal concatenation happens in phase 6,
after macro expansion, and an escape sequence is only decoded once the encoding
prefix of the whole concatenation is known -- `"a" L"b"` is one wide string.

HIDE SETS live on the token. That is Prosser's algorithm (the one the standard's
committee used to settle what recursive macro expansion means), and the hide set
has to travel with each token individually: a token that came out of `f`'s
expansion carries `{f}` for the rest of its life, even after it has been passed
as an argument to `g` and re-expanded there. Storing it anywhere but on the
token loses exactly the cases the algorithm exists for.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, replace

from ...diagnostics import Span


class Kind(enum.Enum):
    """What a preprocessing token is. Phase 7 refines NUMBER and IDENT."""

    IDENT = "identifier"
    NUMBER = "number"           #: a pp-number: spelling only, no value yet
    CHARCONST = "character constant"
    STRING = "string literal"
    PUNCT = "punctuator"
    HEADER = "header name"      #: only inside #include, only when asked for
    OTHER = "stray character"   #: a `$`, a backtick -- legal to lex, not to use
    EOF = "end of file"

    def __str__(self) -> str:
        return self.value


#: Every punctuator, longest first so a greedy match is a plain prefix test.
#: Digraphs are in here and are mapped to what they spell by `DIGRAPHS` below,
#: because `<:` IS `[` everywhere after phase 3 -- the standard says the two
#: are interchangeable, not that one is a shorthand the parser should know
#: about.
PUNCTUATORS: tuple[str, ...] = (
    "%:%:", "...", "<<=", ">>=",
    "->", "++", "--", "<<", ">>", "<=", ">=", "==", "!=", "&&", "||",
    "*=", "/=", "%=", "+=", "-=", "&=", "^=", "|=", "##",
    "<:", ":>", "<%", "%>", "%:",
    "[", "]", "(", ")", "{", "}", ".", "&", "*", "+", "-", "~", "!",
    "/", "%", "<", ">", "^", "|", "?", ":", ";", "=", ",", "#",
)

#: A digraph and what it spells. Applied at the moment the token is made, so
#: nothing downstream ever sees one -- except `text`, which keeps the spelling
#: the user wrote so a diagnostic quotes their source and not a translation.
DIGRAPHS: dict[str, str] = {
    "<:": "[", ":>": "]", "<%": "{", "%>": "}", "%:": "#", "%:%:": "##",
}

#: The keywords of C23, plus the older spellings that are still keywords.
#: `sema` looks names up here before it looks them up in a scope, so a program
#: that writes `int if;` is refused by the parser rather than accepted as a
#: declaration of a variable called `if`.
KEYWORDS: frozenset[str] = frozenset({
    # C89
    "auto", "break", "case", "char", "const", "continue", "default", "do",
    "double", "else", "enum", "extern", "float", "for", "goto", "if", "int",
    "long", "register", "return", "short", "signed", "sizeof", "static",
    "struct", "switch", "typedef", "union", "unsigned", "void", "volatile",
    "while",
    # C99
    "inline", "restrict", "_Bool", "_Complex", "_Imaginary",
    # C11
    "_Alignas", "_Alignof", "_Atomic", "_Generic", "_Noreturn",
    "_Static_assert", "_Thread_local",
    # C23
    "alignas", "alignof", "bool", "constexpr", "false", "nullptr", "static_assert",
    "thread_local", "true", "typeof", "typeof_unqual", "_BitInt", "_Decimal32",
    "_Decimal64", "_Decimal128",
    # GNU's double-underscore spellings. They are keywords because a real
    # header writes `__typeof__(x)` and `__restrict` where the standard
    # spells them without the underscores -- and the reason the underscores
    # exist is that the plain names are not reserved, so a header cannot use
    # them. Folded to the standard spelling by `KEYWORD_ALIASES` below.
    "__typeof__", "__typeof", "__inline__", "__inline", "__const__",
    "__const", "__restrict__", "__restrict", "__volatile__", "__volatile",
    "__signed__", "__signed", "__alignof__", "__alignof", "__complex__",
})

#: C23 spells several C11 keywords without the underscore, and the two are the
#: same keyword rather than synonyms a parser should handle twice. Folded here,
#: at the one place a keyword is recognised.
KEYWORD_ALIASES: dict[str, str] = {
    "alignas": "_Alignas", "alignof": "_Alignof",
    "static_assert": "_Static_assert", "thread_local": "_Thread_local",
    "bool": "_Bool",
    "__typeof__": "typeof", "__typeof": "typeof",
    "__inline__": "inline", "__inline": "inline",
    "__const__": "const", "__const": "const",
    "__restrict__": "restrict", "__restrict": "restrict",
    "__volatile__": "volatile", "__volatile": "volatile",
    "__signed__": "signed", "__signed": "signed",
    "__alignof__": "_Alignof", "__alignof": "_Alignof",
    "__complex__": "_Complex",
}


@dataclass(slots=True)
class Token:
    """One preprocessing token.

    `text` is the SPELLING, digraphs excepted: what the user wrote, so that a
    diagnostic quotes them. `ws` and `bol` are the whitespace facts the
    preprocessor needs and nothing else does -- `#` only introduces a directive
    at the start of a line, and `#define f(x)` is a function-like macro while
    `#define f (x)` is an object-like one whose body is `(x)`. Those two are
    distinguished by ONE SPACE, so the space has to survive phase 3.
    """

    kind: Kind
    text: str
    span: Span
    #: Preceded by whitespace (or a comment, which is whitespace).
    ws: bool = False
    #: First token on a logical source line.
    bol: bool = False
    #: Macros this token may not be re-expanded by. See the module docstring.
    hide: frozenset[str] = frozenset()
    #: Set on a token the preprocessor synthesised (`#` stringising, `##`
    #: pasting, a predefined macro's body) so a diagnostic can say where the
    #: expansion came from rather than pointing at a `#define` in a header.
    from_macro: str | None = None

    def is_(self, text: str) -> bool:
        """True if this is the punctuator or keyword `text`."""
        return self.text == text and self.kind in (Kind.PUNCT, Kind.IDENT)

    def is_punct(self, *texts: str) -> bool:
        return self.kind is Kind.PUNCT and self.text in texts

    def is_ident(self, *names: str) -> bool:
        return self.kind is Kind.IDENT and self.text in names

    @property
    def is_keyword(self) -> bool:
        return self.kind is Kind.IDENT and self.text in KEYWORDS

    def with_hide(self, names: frozenset[str]) -> Token:
        return replace(self, hide=self.hide | names)

    def at(self, span: Span, *, macro: str | None = None) -> Token:
        """A copy pointing somewhere else. Used when a macro body's token is
        placed at the call site: the expansion's tokens report the CALL, which
        is where the user can do something about the error."""
        return replace(self, span=span,
                       from_macro=macro if macro is not None else self.from_macro)

    def __str__(self) -> str:
        return self.text


#: What an empty token stream ends with. A real EOF token rather than `None`
#: so every consumer can say `tok.span` without asking whether there is one --
#: "expected `}` at end of file" needs a position as much as any other error.
def eof_token(span: Span) -> Token:
    return Token(Kind.EOF, "", span, ws=True, bol=True)
