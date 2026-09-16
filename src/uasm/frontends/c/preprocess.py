"""Translation phase 4: directives and macro expansion.

MACRO EXPANSION IS PROSSER'S ALGORITHM, hide sets and all. Not because it is
elegant -- it is not -- but because every shorter rule anybody writes down is
wrong on a program somebody has already written. The three that a "expand until
nothing changes, with a recursion guard" approach gets wrong:

    #define f(a) a*g
    #define g(a) f(a)
    f(2)(9)                 -> 2*9*g     (and NOT 2*9*f(9), nor a loop)

    #define x  2
    #define f(a) f(x * (a))
    #define t(a) a
    t(t(t)(f)(x))           -> the argument's own expansion may not re-enter
                               the macro it came out of, but MAY re-enter one
                               it merely passed through

    #define foo bar
    #define bar foo
    foo                     -> foo, once, and then stops

A hide set per TOKEN, intersected at the closing parenthesis of a
function-like invocation, is what settles all three. It is written out in the
standard's own rationale and in Prosser's 1986 note; this is that.

THE FILE IS PROCESSED IN CHUNKS BETWEEN DIRECTIVES, not token by token. A macro
invocation's argument list may span lines, so expansion cannot stop at a
newline; but the macro TABLE changes at every `#define` and `#undef`, so
expansion must not run past one either. The run of non-directive lines between
two directives is exactly the largest region where both hold, and it is what is
handed to `expand`. A program whose argument list straddles a directive is
undefined behaviour in the standard and is one chunk short of working here --
which is the failure the standard leaves room for.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from ...diagnostics import (
    DiagnosticSink, SourceFile, Span, error, warning,
)
from .lexer import lex
from .literals import LiteralError, decode_string
from .ppexpr import evaluate, evaluate_value
from .tokens import Kind, Token

#: Where the bundled standard headers live. Searched LAST, after everything the
#: user named, so a project with its own `string.h` on an `-I` path gets its
#: own -- the same rule the Python frontend applies to its bundled modules.
BUNDLED = Path(__file__).parent / "include"

#: How deep `#include` may nest before the compiler decides the program is
#: including itself. 200 is what gcc uses.
MAX_INCLUDE_DEPTH = 200

#: A token that occupies a position and has no spelling. It exists for exactly
#: one rule: `a ## b` where one side came from an empty macro argument has to
#: produce the other side rather than pasting with nothing and losing it.
#: Deleted at the end of substitution, so nothing downstream sees one.
_PLACEMARKER = "\x00placemarker"


def _is_placemarker(t: Token) -> bool:
    return t.kind is Kind.OTHER and t.text == _PLACEMARKER


@dataclass(slots=True)
class Macro:
    """One `#define`.

    THE VARIADIC PARAMETER IS IN `params`, named `__VA_ARGS__` unless the
    definition used GNU's `rest...` form and gave it a name. Normalising it
    here means every later rule counts parameters the same way -- the
    alternative is `len(params) + variadic` written in four places, and the
    fourth one is always the one that forgets.
    """

    name: str
    #: None for an object-like macro. Empty list for `f()`.
    params: list[str] | None
    variadic: bool
    body: list[Token]
    span: Span
    #: Set for the handful whose value depends on where they are written.
    dynamic: str | None = None

    @property
    def function_like(self) -> bool:
        return self.params is not None


@dataclass(slots=True)
class Search:
    """Where `#include` looks, in order."""

    #: Directories for the `"..."` form, the including file's own first.
    quote: list[Path] = field(default_factory=list)
    #: Directories for the `<...>` form.
    angle: list[Path] = field(default_factory=list)
    #: True to search the bundled headers. `--no-bundled-headers` turns it off
    #: for a project supplying a whole libc of its own.
    bundled: bool = True

    def resolve(self, name: str, *, angled: bool, here: Path | None) -> Path | None:
        roots: list[Path] = []
        if not angled:
            # THE INCLUDING FILE'S OWN DIRECTORY, not the working directory.
            # `#include "util.h"` inside `src/a/b.c` means `src/a/util.h`, and
            # a compiler that reads it relative to wherever it was invoked
            # finds a different file depending on which directory you typed
            # `uasm` in.
            if here is not None:
                roots.append(here)
            roots.extend(self.quote)
        roots.extend(self.angle)
        if self.bundled:
            roots.append(BUNDLED)
        for root in roots:
            candidate = root / name
            if candidate.is_file():
                return candidate
        return None


class Preprocessor:
    """Phase 4 over one translation unit."""

    def __init__(self, sink: DiagnosticSink, search: Search | None = None, *,
                 trigraphs: bool = False,
                 defines: dict[str, str] | None = None) -> None:
        self.sink = sink
        self.search = search or Search()
        self.trigraphs = trigraphs
        self.macros: dict[str, Macro] = {}
        #: Files that asked not to be read twice, by resolved path.
        self.once: set[Path] = set()
        self.counter = 0
        self.depth = 0
        #: The chain of files being read, innermost last. `__BASE_FILE__`
        #: reads the first and `#include` pushes and pops.
        self._including: list[Path] = []
        #: `#pragma` lines nothing consumed, kept so the driver can report
        #: them rather than have them vanish.
        self.pragmas: list[tuple[str, Span]] = []
        #: `#line`'s effect, as (offset, presumed line, presumed file) sorted
        #: by offset per file. Only `__LINE__` and `__FILE__` read it.
        self._line_map: dict[str, list[tuple[int, int, str]]] = {}
        self._predefine()
        for name, value in (defines or {}).items():
            self._command_line_define(name, value)

    # ── predefined macros ───────────────────────────────────────────────────
    def _predefine(self) -> None:
        """The macros a program may assume without defining them.

        THE WIDTH MACROS ARE HERE AND NOT IN A HEADER on purpose: `<limits.h>`
        and `<stdint.h>` have to agree with what the compiler actually does,
        and a header that writes `2147483647` by hand is a second answer that
        can drift from the first. They are spelled `__INT_MAX__` and so on
        because that is what gcc and clang call them, so a header written for
        either works here unchanged.
        """
        now = time.localtime()
        text = {
            "__STDC__": "1",
            "__STDC_VERSION__": "202311L",
            "__STDC_HOSTED__": "1",
            "__STDC_UTF_16__": "1",
            # `#embed` AND `__has_embed` ANSWER WITH THESE, which C23 makes
            # predefined so that a program can compare without naming a
            # header -- `__has_embed` is a preprocessor operator and there is
            # no header it could have been told to include first.
            "__STDC_EMBED_NOT_FOUND__": "0",
            "__STDC_EMBED_FOUND__": "1",
            "__STDC_EMBED_EMPTY__": "2",
            "__STDC_UTF_32__": "1",
            # `wchar_t` HOLDS A UNICODE CODE POINT, so this says so. C makes
            # it a claim rather than a constant: define it, and every
            # character in the extended character set has the value ISO
            # 10646 gives it -- which here follows from the encoding being
            # UTF-8 and `mbrtowc` decoding it to the code point, with no
            # locale that could put anything else there. The value names a
            # revision of the standard, and any code point at all survives
            # the round trip, so it names a recent one.
            #
            # `__STDC_MB_MIGHT_NEQ_WC__` IS THE OTHER HALF and is correctly
            # ABSENT: it would say that a basic character might differ
            # between `char` and `wchar_t`, and ASCII and its code points
            # are the same numbers.
            "__STDC_ISO_10646__": "202405L",
            "__uasm__": "1",
            "__UIR__": "1",
            "__CHAR_BIT__": "8",
            "__SCHAR_MAX__": "127",
            "__SHRT_MAX__": "32767",
            "__INT_MAX__": "2147483647",
            "__LONG_MAX__": "9223372036854775807L",
            "__LONG_LONG_MAX__": "9223372036854775807LL",
            "__INTMAX_MAX__": "9223372036854775807L",
            "__SIZE_MAX__": "18446744073709551615UL",
            "__PTRDIFF_MAX__": "9223372036854775807L",
            "__WCHAR_MAX__": "2147483647",
            "__WCHAR_MIN__": "(-2147483647 - 1)",
            "__SIZEOF_INT__": "4",
            "__SIZEOF_LONG__": "8",
            "__SIZEOF_LONG_LONG__": "8",
            "__SIZEOF_SHORT__": "2",
            "__SIZEOF_POINTER__": "8",
            "__SIZEOF_FLOAT__": "4",
            "__SIZEOF_DOUBLE__": "8",
            "__SIZEOF_LONG_DOUBLE__": "16",
            "__SIZEOF_SIZE_T__": "8",
            "__SIZEOF_WCHAR_T__": "4",
            "__CHAR_UNSIGNED__ ": "",
            "__BYTE_ORDER__": "1234",
            "__ORDER_LITTLE_ENDIAN__": "1234",
            "__ORDER_BIG_ENDIAN__": "4321",
            "__SIZE_TYPE__": "unsigned long",
            "__PTRDIFF_TYPE__": "long",
            "__WCHAR_TYPE__": "int",
            "__INTPTR_TYPE__": "long",
            "__UINTPTR_TYPE__": "unsigned long",
            "__INTMAX_TYPE__": "long",
            "__UINTMAX_TYPE__": "unsigned long",
            "__DATE__": time.strftime("\"%b %e %Y\"", now),
            "__TIME__": time.strftime("\"%H:%M:%S\"", now),
        }
        # PLAIN `char` IS SIGNED here (see literals.PREFIXES), so the macro
        # that says otherwise must not be defined. It is in the table above
        # with a trailing space in its name so that the intent -- "considered
        # and deliberately absent" -- is visible rather than being an omission
        # a reader has to notice.
        text.pop("__CHAR_UNSIGNED__ ", None)
        for name, value in text.items():
            self._command_line_define(name, value)
        for name in ("__FILE__", "__LINE__", "__COUNTER__", "__BASE_FILE__",
                     "__INCLUDE_LEVEL__", "__TIMESTAMP__"):
            self.macros[name] = Macro(name, None, False, [], _nowhere(),
                                      dynamic=name)

    def _command_line_define(self, name: str, value: str) -> None:
        """`-D name=value`, and every predefined macro, through one path.

        Lexed rather than constructed, because `-D FOO=(1+2)` has to become
        three tokens and a hand-built single token would paste wrongly.
        """
        src = SourceFile(value, f"<predefined {name}>")
        body = [t for t in lex(src, self.sink) if t.kind is not Kind.EOF]
        self.macros[name] = Macro(name, None, False, body, _nowhere())

    # ── entry point ─────────────────────────────────────────────────────────
    def run(self, source: SourceFile) -> list[Token]:
        """Every token of the translation unit, macros expanded, ending EOF."""
        self._including = [source.path] if source.path else []
        out = self._file(source)
        out.append(Token(Kind.EOF, "", _end_span(source), ws=True, bol=True))
        return out

    def _file(self, source: SourceFile) -> list[Token]:
        toks = lex(source, self.sink, trigraphs=self.trigraphs)
        return self._lines(toks, source)

    # ── the directive / text loop ───────────────────────────────────────────
    def _lines(self, toks: list[Token], source: SourceFile) -> list[Token]:
        out: list[Token] = []
        chunk: list[Token] = []
        i = 0
        #: One entry per open conditional: [taken_so_far, seen_else, span].
        conds: list[list] = []

        def flush() -> None:
            if chunk:
                out.extend(self.expand(chunk))
                chunk.clear()

        while toks[i].kind is not Kind.EOF:
            t = toks[i]
            if t.bol and t.is_punct("#"):
                line, i = _directive_line(toks, i + 1)
                flush()
                self._directive(t, line, conds, out, source)
                continue
            if conds and not conds[-1][0]:
                i += 1
                continue
            chunk.append(t)
            i += 1
        flush()
        for state in conds:
            self.sink.report(
                error("E1110", "unterminated #if")
                .at(state[2])
                .help("every #if needs a matching #endif in the same file"))
        return out

    def _skipping(self, conds: list[list]) -> bool:
        return bool(conds) and not conds[-1][0]

    def _directive(self, hash_tok: Token, line: list[Token], conds: list[list],
                   out: list[Token], source: SourceFile) -> None:
        if not line:
            return                      # `#` alone is a null directive
        name = line[0]
        rest = line[1:]
        word = name.text if name.kind is Kind.IDENT else ""

        # CONDITIONALS ARE READ EVEN WHILE SKIPPING, because a nested `#if`
        # inside a dead branch still has to be matched by its own `#endif`.
        # Everything else is ignored there -- which is what lets a dead branch
        # hold code for another compiler, misspelled directives included.
        if word in ("if", "ifdef", "ifndef"):
            if self._skipping(conds):
                conds.append([False, False, hash_tok.span, False])
                return
            taken = self._condition(word, rest, name)
            conds.append([taken, False, hash_tok.span, taken])
            return
        if word in ("elif", "elifdef", "elifndef"):
            if not conds:
                self._stray(name, "#" + word)
                return
            state = conds[-1]
            if state[1]:
                self.sink.report(
                    error("E1111", f"#{word} after #else").at(name.span))
                return
            if state[3]:               # an earlier branch already ran
                state[0] = False
                return
            if len(conds) > 1 and not conds[-2][0]:
                state[0] = False       # the whole conditional is inside a dead one
                return
            kind = {"elif": "if", "elifdef": "ifdef",
                    "elifndef": "ifndef"}[word]
            state[0] = self._condition(kind, rest, name)
            state[3] = state[3] or state[0]
            return
        if word == "else":
            if not conds:
                self._stray(name, "#else")
                return
            state = conds[-1]
            if state[1]:
                self.sink.report(
                    error("E1111", "#else after #else").at(name.span))
                return
            state[1] = True
            outer_live = len(conds) == 1 or conds[-2][0]
            state[0] = outer_live and not state[3]
            state[3] = state[3] or state[0]
            self._expect_end(rest, "#else")
            return
        if word == "endif":
            if not conds:
                self._stray(name, "#endif")
                return
            conds.pop()
            self._expect_end(rest, "#endif")
            return

        if self._skipping(conds):
            return

        if word == "define":
            self._define(rest, name)
        elif word == "undef":
            self._undef(rest, name)
        elif word in ("include", "include_next"):
            out.extend(self._include(rest, name, source, word == "include_next"))
        elif word == "line":
            self._line(rest, name, source)
        elif word == "error":
            self.sink.report(
                error("E1112", "#error " + _spell(rest)).at(name.span))
        elif word == "warning":
            self.sink.report(
                warning("W1113", "#warning " + _spell(rest)).at(name.span))
        elif word == "pragma":
            self._pragma(rest, name)
        elif word == "embed":
            out.extend(self._embed(rest, name))
        elif name.kind is Kind.NUMBER:
            # `# 1 "file.h"` -- the line marker gcc's own preprocessor emits.
            # Accepted and ignored so preprocessed source can be fed back in.
            pass
        else:
            self.sink.report(
                error("E1115", f"unknown directive #{word or name.text}")
                .at(name.span))

    def _stray(self, tok: Token, what: str) -> None:
        self.sink.report(
            error("E1116", f"{what} without #if").at(tok.span))

    def _expect_end(self, rest: list[Token], what: str) -> None:
        if rest:
            self.sink.report(
                warning("W1117", f"extra tokens after {what}")
                .at(rest[0].span)
                .help("put them in a comment"))

    # ── #if and friends ─────────────────────────────────────────────────────
    def _condition(self, kind: str, rest: list[Token], at: Token) -> bool:
        if kind in ("ifdef", "ifndef"):
            if not rest or rest[0].kind is not Kind.IDENT:
                self.sink.report(
                    error("E1118", f"#{kind} needs a macro name").at(at.span))
                return False
            self._expect_end(rest[1:], "#" + kind)
            defined = rest[0].text in self.macros
            return defined if kind == "ifdef" else not defined
        return evaluate(self._if_line(rest), self.sink)

    def _if_line(self, toks: list[Token]) -> list[Token]:
        """A `#if` line, ready to evaluate.

        `defined X` IS RESOLVED BEFORE EXPANSION and again after. Before,
        because that is the rule: `#define X 1` then `#if defined X` must not
        expand `X` to `1` and then ask whether `1` is defined. After, because
        `#define HAVE defined(FOO)` followed by `#if HAVE` is undefined
        behaviour that every real compiler accepts, and a header in the wild
        relies on it.
        """
        toks = self._resolve_queries(toks)
        toks = self.expand(toks, in_if=True)
        return self._resolve_queries(toks)

    def _resolve_queries(self, toks: list[Token]) -> list[Token]:
        out: list[Token] = []
        i = 0
        while i < len(toks):
            t = toks[i]
            if t.kind is Kind.IDENT and t.text == "defined":
                name, i = self._query_name(toks, i + 1, t)
                out.append(_number(1 if name in self.macros else 0, t.span))
                continue
            if t.kind is Kind.IDENT and t.text == "__has_embed":
                got, i = self._has_embed(toks, i + 1, t)
                out.append(_number(got, t.span))
                continue
            if t.kind is Kind.IDENT and t.text in ("__has_include",
                                                   "__has_include_next"):
                got, i = self._has_include(toks, i + 1, t)
                out.append(_number(1 if got else 0, t.span))
                continue
            if t.kind is Kind.IDENT and t.text in ("__has_builtin",
                                                   "__has_feature",
                                                   "__has_attribute",
                                                   "__has_c_attribute"):
                name, i = self._query_name(toks, i + 1, t)
                if t.text == "__has_builtin":
                    from .builtins import BUILTINS
                    out.append(_number(1 if name in BUILTINS else 0, t.span))
                elif t.text == "__has_c_attribute":
                    # THE ANSWER IS A DATE, not a 1: C versions its
                    # attributes, so a program that wants the `nodiscard`
                    # that takes a message tests `>= 202003L` and a `0`
                    # would tell it nothing it could act on.
                    from .attributes import STANDARD
                    out.append(_number(STANDARD.get(name, 0), t.span))
                else:
                    # `__has_attribute` AND `__has_feature` ARE NOT C's, and
                    # answering 0 is the truthful answer rather than a
                    # placeholder: `parser._attributes` parses
                    # `__attribute__((...))` and acts on none of it, so a
                    # header asking whether one works should take its
                    # fallback.
                    out.append(_number(0, t.span))
                continue
            out.append(t)
            i += 1
        return out

    def _query_name(self, toks: list[Token], i: int, at: Token) -> tuple[str, int]:
        """`defined X` or `defined(X)`; returns the name and the next index."""
        paren = i < len(toks) and toks[i].is_punct("(")
        if paren:
            i += 1
        if i >= len(toks) or toks[i].kind is not Kind.IDENT:
            self.sink.report(
                error("E1119", f"{at.text} needs a name").at(at.span))
            return "", len(toks)
        name = toks[i].text
        i += 1
        if paren:
            if i < len(toks) and toks[i].is_punct(")"):
                i += 1
            else:
                self.sink.report(
                    error("E1120", f"expected `)` after {at.text}(").at(at.span))
        return name, i

    def _has_include(self, toks: list[Token], i: int,
                     at: Token) -> tuple[bool, int]:
        if i >= len(toks) or not toks[i].is_punct("("):
            self.sink.report(
                error("E1121", "__has_include needs a header name in parentheses")
                .at(at.span))
            return False, len(toks)
        i += 1
        name, angled, i = self._header_name(toks, i, at)
        if name is None:
            # `__has_include(WHICH)` -- the same second try `#include` makes
            # above, and for the same reason: C says the argument is macro
            # expanded when it is not already one of the two header-name
            # forms. The argument is everything up to the matching `)`.
            start, depth, k = i, 0, i
            while k < len(toks):
                if toks[k].is_punct("("):
                    depth += 1
                elif toks[k].is_punct(")"):
                    if depth == 0:
                        break
                    depth -= 1
                k += 1
            name, angled, _ = self._header_name(
                self.expand(toks[start:k]), 0, at, verbatim=False)
            i = k
        if i < len(toks) and toks[i].is_punct(")"):
            i += 1
        if name is None:
            return False, i
        return self._find(name, angled, at) is not None, i

    # ── #define / #undef ────────────────────────────────────────────────────
    #: THE NAMES A PROGRAM MAY NOT REDEFINE. C lists these seven and the
    #: identifier `defined` in 6.10.9.3; `defined` is a "shall not" and so a
    #: constraint violation, and the seven are undefined behaviour, which is
    #: worse rather than better. Both are refused, because the program that
    #: writes `#define __LINE__ 5` has confused itself and every diagnostic
    #: after it would point at the wrong line.
    #:
    #: NOT THE WHOLE `__STDC_` NAMESPACE, which is reserved but not
    #: protected: a program that defines `__STDC_WANT_LIB_EXT1__` before
    #: including a header is doing what C tells it to.
    _PROTECTED = frozenset({
        "__DATE__", "__FILE__", "__LINE__", "__STDC__", "__STDC_HOSTED__",
        "__STDC_VERSION__", "__TIME__",
    })

    def _define(self, rest: list[Token], at: Token) -> None:
        if not rest or rest[0].kind is not Kind.IDENT:
            self.sink.report(
                error("E1122", "#define needs a macro name").at(at.span))
            return
        name_tok = rest[0]
        name = name_tok.text
        if name == "defined":
            self.sink.report(
                error("E1123", "`defined` cannot be used as a macro name")
                .at(name_tok.span))
            return
        if name in self._PROTECTED:
            self.sink.report(
                error("E1124", f"`{name}` is predefined and cannot be "
                               f"redefined").at(name_tok.span)
                .note("C reserves the seven predefined macro names and "
                      "`defined` from `#define` and `#undef`"))
            return
        params: list[str] | None = None
        variadic = False
        i = 1
        # ONE SPACE DECIDES WHAT THIS IS. `#define f(x) x` is function-like;
        # `#define f (x) x` is object-like with the body `(x) x`. The only
        # difference in the token stream is `ws` on the `(`, which is why the
        # lexer carries it.
        if i < len(rest) and rest[i].is_punct("(") and not rest[i].ws:
            params, variadic, i = self._parameters(rest, i + 1, at)
            if params is None:
                return
        body = rest[i:]
        if body and (body[0].is_punct("##") or body[-1].is_punct("##")):
            self.sink.report(
                error("E1124", "`##` cannot begin or end a macro body")
                .at((body[0] if body[0].is_punct("##") else body[-1]).span))
            return
        # The body's tokens keep their own spans -- a diagnostic about the
        # expansion can then show the `#define` as well as the use.
        macro = Macro(name, params, variadic, [replace(t) for t in body],
                      name_tok.span)
        old = self.macros.get(name)
        if old is not None and old.dynamic is None and not _same(old, macro):
            # NOT AN ERROR: the standard says a redefinition must be identical
            # and every toolchain in existence has a header that violates it.
            # Warning and taking the new one is what gcc does.
            self.sink.report(
                warning("W1125", f"{name!r} redefined")
                .at(name_tok.span)
                .also(old.span, "the previous definition"))
        self.macros[name] = macro

    def _parameters(self, rest: list[Token], i: int,
                    at: Token) -> tuple[list[str] | None, bool, int]:
        params: list[str] = []
        variadic = False
        if i < len(rest) and rest[i].is_punct(")"):
            return params, False, i + 1
        while True:
            if i >= len(rest):
                self.sink.report(
                    error("E1126", "unterminated macro parameter list")
                    .at(at.span))
                return None, False, i
            t = rest[i]
            if t.is_punct("..."):
                variadic = True
                params.append("__VA_ARGS__")
                i += 1
            elif t.kind is Kind.IDENT:
                if t.text in params:
                    self.sink.report(
                        error("E1127", f"duplicate macro parameter {t.text!r}")
                        .at(t.span))
                    return None, False, i
                # GNU named variadic: `#define f(args...)`. The name takes the
                # place of `__VA_ARGS__`, which is what the headers that use
                # it expect.
                if i + 1 < len(rest) and rest[i + 1].is_punct("..."):
                    params.append(t.text)
                    variadic = True
                    i += 2
                else:
                    params.append(t.text)
                    i += 1
            else:
                self.sink.report(
                    error("E1128", f"expected a parameter name, got {t.text!r}")
                    .at(t.span))
                return None, False, i
            if i < len(rest) and rest[i].is_punct(")"):
                return params, variadic, i + 1
            if i < len(rest) and rest[i].is_punct(",") and not variadic:
                i += 1
                continue
            self.sink.report(
                error("E1129", "expected `,` or `)` in macro parameter list")
                .at(rest[i].span if i < len(rest) else at.span))
            return None, False, i

    def _undef(self, rest: list[Token], at: Token) -> None:
        if not rest or rest[0].kind is not Kind.IDENT:
            self.sink.report(
                error("E1130", "#undef needs a macro name").at(at.span))
            return
        name = rest[0].text
        if name == "defined":
            self.sink.report(
                error("E1123", "`defined` cannot be used as a macro name")
                .at(rest[0].span))
            return
        if name in self._PROTECTED:
            self.sink.report(
                error("E1124", f"`{name}` is predefined and cannot be "
                               f"undefined").at(rest[0].span)
                .note("C reserves the seven predefined macro names and "
                      "`defined` from `#define` and `#undef`"))
            return
        self.macros.pop(name, None)
        self._expect_end(rest[1:], "#undef")

    # ── #include ────────────────────────────────────────────────────────────
    def _header_name(self, toks: list[Token], i: int, at: Token,
                     *, verbatim: bool = True) -> tuple[str | None, bool, int]:
        """The header name starting at `i`, and the index after it.

        `<stdio.h>` IS NOT A TOKEN after phase 3 -- it is `<`, `stdio`, `.`,
        `h`, `>` -- and reassembling it from those spellings loses the spacing
        in `<sys / types.h>`. So the name is cut out of the ORIGINAL source
        text between the two angle brackets, which is what the standard's
        h-char-sequence actually is.

        `verbatim=False` FOR TOKENS A MACRO PRODUCED, which have no original
        text to cut: an expanded token carries the span of the INVOCATION, so
        that a diagnostic points at what the program actually wrote, and
        slicing between two of those gives an empty string. There the
        spellings are joined instead -- which is what C means by the method
        of combining them being implementation-defined, and makes
        `#include WHICH` after `#define WHICH <string.h>` read `string.h`.
        """
        if i >= len(toks):
            return None, False, i
        t = toks[i]
        if t.kind is Kind.STRING and t.text.startswith('"'):
            return t.text[1:-1], False, i + 1
        if t.is_punct("<"):
            depth = i
            while depth < len(toks) and not toks[depth].is_punct(">"):
                depth += 1
            if depth >= len(toks):
                self.sink.report(
                    error("E1131", "expected `>` after `<` in #include")
                    .at(t.span))
                return None, True, len(toks)
            close = toks[depth]
            if (verbatim and close.span.file is t.span.file
                    and t.span.end <= close.span.start):
                name = t.span.file.text[t.span.end:close.span.start]
            else:
                name = "".join(x.text for x in toks[i + 1:depth])
            return name.strip(), True, depth + 1
        return None, False, i

    def _find(self, name: str, angled: bool, at: Token) -> Path | None:
        here = None
        f = at.span.file
        if f.path is not None:
            here = f.path.parent
        return self.search.resolve(name, angled=angled, here=here)

    def _include(self, rest: list[Token], at: Token, source: SourceFile,
                 next_: bool) -> list[Token]:
        name, angled, used = self._header_name(rest, 0, at)
        if name is None:
            # `#include MACRO` -- expand the line and try once more. Tried
            # second because `#include <a/b.h>` must NOT have `a` expanded if
            # a macro of that name happens to exist.
            expanded = self.expand(rest)
            name, angled, used = self._header_name(expanded, 0, at,
                                                   verbatim=False)
            rest = expanded
        if name is None:
            self.sink.report(
                error("E1132", "#include needs \"file\" or <file>").at(at.span))
            return []
        self._expect_end(rest[used:], "#include")
        if not name:
            self.sink.report(error("E1133", "empty header name").at(at.span))
            return []
        path = self._find(name, angled, at)
        if path is None:
            self.sink.report(
                error("E1134", f"cannot find include file {name!r}")
                .at(at.span)
                .note("searched: " + (", ".join(
                    str(p) for p in ([] if angled else self.search.quote)
                    + self.search.angle
                    + ([BUNDLED] if self.search.bundled else [])) or "nowhere"))
                .help("add a directory with --include-path"))
            return []
        path = path.resolve()
        if path in self.once:
            return []
        if self.depth >= MAX_INCLUDE_DEPTH:
            self.sink.report(
                error("E1135", f"#include nested more than "
                               f"{MAX_INCLUDE_DEPTH} deep")
                .at(at.span)
                .note("a header that includes itself needs an include guard"))
            return []
        try:
            included = SourceFile.read(path)
        except OSError as exc:
            self.sink.report(
                error("E1136", f"cannot read {path}: {exc.strerror}")
                .at(at.span))
            return []
        self.depth += 1
        self._including.append(path)
        try:
            return self._file(included)
        finally:
            self._including.pop()
            self.depth -= 1

    # ── #embed ──────────────────────────────────────────────────────────────
    def _embed(self, rest: list[Token], at: Token) -> list[Token]:
        """`#embed "file"` -- the file's bytes, as integer constants.

        IT IS A DIRECTIVE AND NOT A FUNCTION, which is the whole reason it
        exists: a program that wants a file in an array had to run a script
        that wrote one out, and the array it wrote was a C source file with a
        hundred thousand tokens in it that every build had to lex. This
        produces the same tokens without the file, and a compiler may
        recognise the shape and skip the tokens entirely.

        THE BYTES COME OUT AS A COMMA-SEPARATED LIST and nothing else, so the
        braces are the program's: `= { #embed "x" }`. `prefix` and `suffix`
        put tokens either side and are dropped when the resource is empty --
        which is what makes `{ 0, #embed "x" }` and `{ #embed "x" prefix(0,) }`
        different, and why both spellings exist.
        """
        name, angled, used = self._header_name(rest, 0, at)
        if name is None:
            expanded = self.expand(rest)
            name, angled, used = self._header_name(expanded, 0, at,
                                                   verbatim=False)
            rest = expanded
        if not name:
            self.sink.report(
                error("E1114", '#embed needs "file" or <file>').at(at.span))
            return []
        params = self._embed_params(rest[used:], at)
        if params is None:
            return []
        limit, prefix, suffix, if_empty = params
        data = self._embed_read(name, angled, at)
        if data is None:
            return []
        if limit is not None:
            data = data[:limit]
        if not data:
            return list(if_empty)
        out = list(prefix)
        for i, byte in enumerate(data):
            if i:
                out.append(Token(Kind.PUNCT, ",", at.span, ws=False))
            out.append(_number(byte, at.span))
        out.extend(suffix)
        return out

    def _embed_read(self, name: str, angled: bool, at: Token) -> bytes | None:
        path = self._find(name, angled, at)
        if path is None:
            self.sink.report(
                error("E1146", f"cannot find embedded file {name!r}")
                .at(at.span)
                .help("`__has_embed` answers before the directive does"))
            return None
        try:
            return path.read_bytes()
        except OSError as exc:
            self.sink.report(
                error("E1147", f"cannot read {path}: {exc.strerror}")
                .at(at.span))
            return None

    def _embed_params(self, toks: list[Token], at: Token):
        """`limit(n) prefix(...) suffix(...) if_empty(...)`, in any order."""
        limit: int | None = None
        prefix: list[Token] = []
        suffix: list[Token] = []
        if_empty: list[Token] = []
        i = 0
        while i < len(toks):
            t = toks[i]
            if t.kind is not Kind.IDENT:
                self.sink.report(
                    error("E1145", f"expected an #embed parameter, found "
                                   f"{t.text!r}").at(t.span))
                return None
            word = t.text
            i += 1
            # A VENDOR'S PARAMETER IS PREFIXED, exactly as an attribute is,
            # and is ignored for the same reason: the prefix says it belongs
            # to somebody else.
            vendor = False
            if i < len(toks) and toks[i].is_punct("::"):
                vendor = True
                i += 2
            if i >= len(toks) or not toks[i].is_punct("("):
                if vendor:
                    continue
                self.sink.report(
                    error("E1145", f"#embed parameter {word!r} needs "
                                   f"parentheses").at(t.span))
                return None
            depth, j = 0, i
            while j < len(toks):
                if toks[j].is_punct("("):
                    depth += 1
                elif toks[j].is_punct(")"):
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            if j >= len(toks):
                self.sink.report(
                    error("E1145", f"#embed parameter {word!r} is never "
                                   f"closed").at(t.span))
                return None
            body = toks[i + 1:j]
            i = j + 1
            if vendor:
                continue
            if word == "limit":
                value = evaluate_value(
                    self._resolve_queries(
                        self.expand(self._resolve_queries(body),
                                    in_if=True)), self.sink)
                if value is None:
                    return None
                limit = max(0, value)
            elif word == "prefix":
                prefix = body
            elif word == "suffix":
                suffix = body
            elif word == "if_empty":
                if_empty = body
            else:
                self.sink.report(
                    error("E1145", f"unknown #embed parameter {word!r}")
                    .at(t.span)
                    .note("C23 has `limit`, `prefix`, `suffix` and "
                          "`if_empty`; anything else is a vendor's and "
                          "goes in its own namespace"))
                return None
        return limit, prefix, suffix, if_empty

    def _has_embed(self, toks: list[Token], i: int,
                   at: Token) -> tuple[int, int]:
        """`__has_embed(header)` -- 0 missing, 1 found, 2 found and empty."""
        if i < len(toks) and toks[i].is_punct("("):
            depth, j = 0, i
            while j < len(toks):
                if toks[j].is_punct("("):
                    depth += 1
                elif toks[j].is_punct(")"):
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            inner = toks[i + 1:j]
            after = min(j + 1, len(toks))
            name, angled, used = self._header_name(inner, 0, at)
            if name:
                path = self._find(name, angled, at)
                if path is None:
                    return 0, after
                try:
                    return (2 if path.stat().st_size == 0 else 1), after
                except OSError:
                    return 0, after
            return 0, after
        self.sink.report(
            error("E1144", "__has_embed needs a header name in parentheses")
            .at(at.span))
        return 0, len(toks)

    # ── #line and #pragma ───────────────────────────────────────────────────
    def _line(self, rest: list[Token], at: Token, source: SourceFile) -> None:
        rest = self.expand(rest)
        if not rest or rest[0].kind is not Kind.NUMBER:
            self.sink.report(
                error("E1137", "#line needs a line number").at(at.span))
            return
        try:
            number = int(rest[0].text, 10)
        except ValueError:
            self.sink.report(
                error("E1137", f"{rest[0].text!r} is not a line number")
                .at(rest[0].span))
            return
        name = source.name
        if len(rest) > 1 and rest[1].kind is Kind.STRING:
            try:
                _, values = decode_string(rest[1].text)
                name = "".join(chr(v) for v in values)
            except LiteralError as exc:
                self.sink.report(error("E1010", str(exc)).at(rest[1].span))
                return
        # RECORDED, NOT APPLIED. Spans stay byte offsets into the real file so
        # the caret is drawn under real text; only `__LINE__` and `__FILE__`
        # read this, which is the whole of what a `#line` is for.
        self._line_map.setdefault(source.name, []).append(
            (at.span.end, number, name))

    def _pragma(self, rest: list[Token], at: Token) -> None:
        if rest and rest[0].is_ident("once"):
            f = at.span.file
            if f.path is not None:
                self.once.add(f.path.resolve())
            return
        if rest and rest[0].is_ident("STDC"):
            return          # FP_CONTRACT, FENV_ACCESS, CX_LIMITED_RANGE
        # NOT DROPPED SILENTLY. An unrecognised pragma is usually harmless and
        # occasionally the reason a program is miscompiled (`#pragma pack`),
        # so it is recorded and the driver can list them.
        self.pragmas.append((_spell(rest), at.span))

    # ── macro expansion ─────────────────────────────────────────────────────
    def expand(self, tokens: list[Token], *, in_if: bool = False) -> list[Token]:
        """Prosser's `expand`. See the module docstring."""
        out: list[Token] = []
        work = list(reversed(tokens))
        while work:
            t = work.pop()
            if t.kind is Kind.IDENT and t.text == "_Pragma" \
                    and self._pragma_operator(t, work):
                continue
            if t.kind is not Kind.IDENT or t.text in t.hide:
                out.append(t)
                continue
            macro = self.macros.get(t.text)
            if macro is None:
                out.append(t)
                continue
            if macro.dynamic is not None:
                out.append(self._dynamic(macro, t))
                continue
            if not macro.function_like:
                body = self._subst(macro, t, [], t.hide | {macro.name})
                work.extend(reversed(body))
                continue
            if not work or not work[-1].is_punct("("):
                # A function-like macro's name without a following `(` is not
                # an invocation and is left exactly as written. That is what
                # makes `#define min(a,b) ...` coexist with a variable called
                # `min` used as a value.
                out.append(t)
                continue
            args, close = self._arguments(work, macro, t)
            if args is None:
                out.append(t)
                continue
            # THE INTERSECTION AT THE CLOSING PAREN. `t.hide & close.hide` is
            # the part of Prosser's rule that lets `f(2)(9)` work: the tokens
            # that came from outside the invocation keep their own hiding, and
            # only what BOTH ends agree on survives into the expansion.
            hide = (t.hide & close.hide) | {macro.name}
            body = self._subst(macro, t, args, hide)
            work.extend(reversed(body))
        return out

    def _pragma_operator(self, at: Token, work: list[Token]) -> bool:
        """`_Pragma("...")` -- a `#pragma` written where a token goes.

        HERE AND NOT IN `_lines`, because C says the operator is processed
        after its arguments have been through macro replacement: a header
        writes `_Pragma(STRINGIFY(x))`, and by the time the expander reaches
        this the string is a string. It also has to be here for the plain
        case, because `_Pragma` may appear in the middle of a line where a
        directive cannot.

        DESTRINGIZING IS TWO SUBSTITUTIONS, which is the whole of 6.10.9:
        `\\"` becomes `"` and `\\\\` becomes `\\`, and nothing else in the
        string means anything. The result is lexed as if it had been written
        after a `#pragma`, so `_Pragma("once")` and `#pragma once` are the
        same thing said twice.
        """
        if not work or not work[-1].is_punct("(") \
                or len(work) < 2 or work[-2].kind is not Kind.STRING:
            # NOT LEFT AS AN IDENTIFIER. `_Pragma` is an operator and the
            # name is reserved, so a program cannot have meant anything else
            # by it -- and "`_Pragma` is not declared" from the parser two
            # phases later names neither the problem nor the fix.
            self.sink.report(
                error("E1148", "`_Pragma` takes a parenthesised string")
                .at(at.span)
                .help('`_Pragma("once")`, which is `#pragma once` written '
                      'where a token goes'))
            return True
        saved = list(work)
        work.pop()                                  # `(`
        text = work.pop().text
        if not work or not work[-1].is_punct(")"):
            work[:] = saved
            self.sink.report(
                error("E1148", "`_Pragma` takes a parenthesised string")
                .at(at.span))
            return True
        work.pop()                                  # `)`
        body = text
        for prefix in ("u8", "L", "u", "U"):
            if body.startswith(prefix):
                body = body[len(prefix):]
                break
        if body.startswith('"') and body.endswith('"') and len(body) >= 2:
            body = body[1:-1]
        body = body.replace('\\"', '"').replace("\\\\", "\\")
        line = SourceFile(body + "\n", f"<_Pragma at {at.span.file.name}>")
        toks = [x for x in lex(line, self.sink, trigraphs=self.trigraphs)
                if x.kind is not Kind.EOF]
        self._pragma(toks, at)
        return True

    def _arguments(self, work: list[Token], macro: Macro,
                   at: Token) -> tuple[list[list[Token]] | None, Token]:
        """Consume `( a, b )` from the reversed stream. Returns the actuals."""
        open_paren = work.pop()
        args: list[list[Token]] = [[]]
        depth = 0
        close = open_paren
        #: How many arguments the invocation must supply. A variadic macro's
        #: last parameter may be given nothing at all, so it is one fewer.
        params = macro.params or []
        named = len(params) - 1 if macro.variadic else len(params)
        while True:
            if not work:
                self.sink.report(
                    error("E1140", f"unterminated argument list for {macro.name!r}")
                    .at(at.span)
                    .also(macro.span, "defined here"))
                return None, close
            t = work.pop()
            if t.is_punct("("):
                depth += 1
            elif t.is_punct(")"):
                if depth == 0:
                    close = t
                    break
                depth -= 1
            elif t.is_punct(",") and depth == 0:
                # Once collection has reached the variadic slot, a comma is
                # part of the argument rather than a separator -- that is what
                # makes `f(1, 2, 3)` give `__VA_ARGS__` the text `2, 3`.
                if not (macro.variadic and len(args) > named):
                    args.append([])
                    continue
            args[-1].append(t)
        if named == 0 and not macro.variadic and args == [[]]:
            args = []
        if macro.variadic and len(args) == named:
            args.append([])
        if macro.variadic and len(args) < named:
            self.sink.report(
                error("E1141",
                      f"{macro.name!r} takes at least {named} argument(s), "
                      f"{len(args)} given")
                .at(at.span).also(macro.span, "defined here"))
            return None, close
        if not macro.variadic and len(args) != named:
            self.sink.report(
                error("E1141",
                      f"{macro.name!r} takes {named} argument(s), "
                      f"{len(args)} given")
                .at(at.span).also(macro.span, "defined here"))
            return None, close
        return args, close

    def _subst(self, macro: Macro, at: Token, args: list[list[Token]],
               hide: frozenset[str]) -> list[Token]:
        """Substitute arguments into a macro body. `#`, `##` and `__VA_OPT__`.

        RAW ARGUMENTS FOR `#` AND `##`, PRE-EXPANDED ONES EVERYWHERE ELSE. That
        asymmetry is the standard's and it is not arbitrary: `#x` must
        stringise what the programmer wrote, and `a ## b` must paste spellings
        rather than whatever those spellings expand to.
        """
        names = list(macro.params or ())
        index = {n: i for i, n in enumerate(names)}
        #: The variadic slot, or -1. Read by `__VA_OPT__` and by GNU's comma
        #: swallowing, both of which ask "was anything passed for `...`".
        va = len(names) - 1 if macro.variadic else -1
        expanded: dict[int, list[Token]] = {}

        def raw(i: int) -> list[Token]:
            return args[i] if i < len(args) else []

        def pre(i: int) -> list[Token]:
            if i not in expanded:
                expanded[i] = self.expand(list(raw(i)))
            return expanded[i]

        body = self._va_opt(macro, bool(va >= 0 and raw(va)))
        out: list[Token] = []
        i = 0
        while i < len(body):
            t = body[i]
            # `#param`
            if (macro.function_like and t.is_punct("#")
                    and i + 1 < len(body) and body[i + 1].kind is Kind.IDENT
                    and body[i + 1].text in index):
                out.append(_stringize(raw(index[body[i + 1].text]), t.span))
                i += 2
                continue
            if i + 1 < len(body) and body[i + 1].is_punct("##"):
                seq = list(self._operand(t, index, raw))
                i += 1
                while i < len(body) and body[i].is_punct("##"):
                    paste_at = body[i]
                    i += 1
                    if i >= len(body):
                        break
                    rhs_tok = body[i]
                    rhs = list(self._operand(rhs_tok, index, raw))
                    # GNU's comma swallowing: in `, ## __VA_ARGS__` the `##`
                    # is not a paste at all. With no variadic arguments it
                    # deletes the comma; with some, it stands aside and the
                    # arguments follow the comma unpasted. glibc and every BSD
                    # header use it, and `x ## 1` -- which is what a literal
                    # reading produces -- is not even a token.
                    if (seq and seq[-1].is_punct(",") and va >= 0
                            and rhs_tok.kind is Kind.IDENT
                            and index.get(rhs_tok.text, -2) == va):
                        if raw(va):
                            seq.extend(rhs)
                        else:
                            seq.pop()
                    elif not rhs:
                        pass
                    elif not seq:
                        seq = rhs
                    else:
                        left = seq.pop()
                        seq.append(self._paste(left, rhs[0], paste_at))
                        seq.extend(rhs[1:])
                    i += 1
                out.extend(seq)
                continue
            if t.kind is Kind.IDENT and t.text in index:
                out.extend(pre(index[t.text]))
                i += 1
                continue
            out.append(t)
            i += 1

        result = []
        for t in out:
            if _is_placemarker(t):
                continue
            if not result:
                # THE EXPANSION INHERITS THE INVOCATION'S SPACING. Without
                # this, `a cat(b,c)` stringises as `abc`: the first token of
                # the replacement carries whatever spacing it happened to have
                # inside the macro body, which is nothing.
                t = replace(t, ws=at.ws)
            # EVERY TOKEN OF THE EXPANSION POINTS AT THE INVOCATION. An error
            # in a macro body is reported where the macro was USED, because
            # that is the line the programmer can change -- with the
            # definition named as a secondary label by whoever reports it.
            result.append(t.at(at.span, macro=macro.name).with_hide(hide))
        return result

    def _operand(self, t: Token, index: dict[str, int],
                 raw) -> list[Token]:
        """One side of a `##`: the RAW argument, or the token itself."""
        if t.kind is Kind.IDENT and t.text in index:
            got = raw(index[t.text])
            return list(got) if got else [_placemarker(t.span)]
        return [t]

    def _va_opt(self, macro: Macro, present: bool) -> list[Token]:
        """Replace `__VA_OPT__(x)` with `x` or with a placemarker.

        Done as a pass over the body before substitution, which is exactly
        what the standard's wording describes: `__VA_OPT__` behaves as if it
        were a parameter whose argument is either its contents or nothing.
        """
        body = macro.body
        if not macro.variadic or not any(
                t.kind is Kind.IDENT and t.text == "__VA_OPT__" for t in body):
            return body
        out: list[Token] = []
        i = 0
        while i < len(body):
            t = body[i]
            if not (t.kind is Kind.IDENT and t.text == "__VA_OPT__"):
                out.append(t)
                i += 1
                continue
            if i + 1 >= len(body) or not body[i + 1].is_punct("("):
                self.sink.report(
                    error("E1142", "__VA_OPT__ needs parentheses").at(t.span))
                return body
            depth = 0
            j = i + 1
            inner: list[Token] = []
            while j < len(body):
                u = body[j]
                if u.is_punct("("):
                    depth += 1
                    if depth == 1:
                        j += 1
                        continue
                elif u.is_punct(")"):
                    depth -= 1
                    if depth == 0:
                        break
                inner.append(u)
                j += 1
            out.extend(inner if present else [_placemarker(t.span)])
            i = j + 1
        return out

    def _paste(self, left: Token, right: Token, at: Token) -> Token:
        if _is_placemarker(left):
            return right
        if _is_placemarker(right):
            return left
        text = left.text + right.text
        sub = SourceFile(text, f"<{left.text}##{right.text}>")
        quiet = DiagnosticSink()
        made = [t for t in lex(sub, quiet) if t.kind is not Kind.EOF]
        if len(made) != 1 or quiet.failed:
            self.sink.report(
                error("E1143", f"pasting {left.text!r} and {right.text!r} "
                               f"does not make one token")
                .at(at.span))
            return left
        return replace(made[0], span=at.span, ws=left.ws, bol=False,
                       hide=left.hide & right.hide)

    def _dynamic(self, macro: Macro, at: Token) -> Token:
        """`__LINE__` and the rest, whose value depends on where they appear."""
        span = at.span
        name = macro.dynamic
        if name == "__LINE__":
            return _number(self._presumed_line(span), span)
        if name == "__COUNTER__":
            self.counter += 1
            return _number(self.counter - 1, span)
        if name == "__INCLUDE_LEVEL__":
            return _number(self.depth, span)
        if name == "__FILE__":
            return _string(self._presumed_file(span), span)
        if name == "__BASE_FILE__":
            files = self._including
            return _string(str(files[0]) if files else span.file.name, span)
        if name == "__TIMESTAMP__":
            path = span.file.path
            stamp = time.localtime(path.stat().st_mtime) if path and path.exists() \
                else time.localtime()
            return _string(time.strftime("%a %b %e %H:%M:%S %Y", stamp), span)
        return _number(0, span)

    def _line_directive_before(self, span: Span) -> tuple[int, str] | None:
        entries = self._line_map.get(span.file.name)
        if not entries:
            return None
        best = None
        for offset, number, name in entries:
            if offset <= span.start:
                best = (offset, number, name)
        if best is None:
            return None
        offset, number, name = best
        # The `#line` sets the number of the line AFTER it, so the presumed
        # line is that number plus however many real lines have gone by.
        real_after = span.file.location(offset).line + 1
        return number + (span.start_loc.line - real_after), name

    def _presumed_line(self, span: Span) -> int:
        got = self._line_directive_before(span)
        return got[0] if got else span.start_loc.line

    def _presumed_file(self, span: Span) -> str:
        got = self._line_directive_before(span)
        return got[1] if got else span.file.name


# ── small constructors ──────────────────────────────────────────────────────
def _nowhere() -> Span:
    from ...diagnostics import NO_SPAN
    return NO_SPAN


def _end_span(source: SourceFile) -> Span:
    return Span(source, len(source.text), len(source.text))


def _number(value: int, span: Span) -> Token:
    return Token(Kind.NUMBER, str(value), span, ws=True)


def _string(value: str, span: Span) -> Token:
    body = value.replace("\\", "\\\\").replace('"', '\\"')
    return Token(Kind.STRING, f'"{body}"', span, ws=True)


def _placemarker(span: Span) -> Token:
    return Token(Kind.OTHER, _PLACEMARKER, span)


def _spell(toks: list[Token]) -> str:
    """The tokens as text, one space where the source had whitespace."""
    out = []
    for i, t in enumerate(toks):
        if i and t.ws:
            out.append(" ")
        out.append(t.text)
    return "".join(out)


def _stringize(toks: list[Token], span: Span) -> Token:
    """`#x`. Escapes are re-escaped: `#` of `"a"` is `"\\"a\\""`."""
    parts = []
    for i, t in enumerate(toks):
        if i and t.ws:
            parts.append(" ")
        text = t.text
        if t.kind in (Kind.STRING, Kind.CHARCONST):
            text = text.replace("\\", "\\\\").replace('"', '\\"')
        parts.append(text)
    return Token(Kind.STRING, '"' + "".join(parts) + '"', span, ws=True)


def _same(a: Macro, b: Macro) -> bool:
    """Whether two `#define`s of one name are the identical definition."""
    if (a.params or []) != (b.params or []) or a.variadic != b.variadic:
        return False
    if (a.params is None) != (b.params is None):
        return False
    if len(a.body) != len(b.body):
        return False
    return all(x.text == y.text and x.ws == y.ws
               for x, y in zip(a.body, b.body))


def _directive_line(toks: list[Token], i: int) -> tuple[list[Token], int]:
    """The tokens of one directive, and the index after it."""
    out = []
    while toks[i].kind is not Kind.EOF and not toks[i].bol:
        out.append(toks[i])
        i += 1
    return out, i
