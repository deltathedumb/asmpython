"""The C frontend: it compiles C.

    tokens.py        what a preprocessing token is, and why it is not a token
    lexer.py         phases 1-3: source text -> preprocessing tokens
    preprocess.py    phase 4: directives and macro expansion
    ppexpr.py        `#if` arithmetic, which is not the constant folder
    literals.py      decoding the four kinds of literal, for both of those
    ctype.py         the C type system: sizes, layout, conversions
    syntax.py        the tree, already typed
    parser.py        phase 7: the grammar, typed as it is parsed
    sema.py          the type rules the parser calls into
    fold.py          constant expressions, which C requires in six places
    lower.py         typed tree -> APIR
    lower_builtins.py  code for the `__builtin_*` names
    builtins.py      which of them exist; `__has_builtin` reads this
    builtin_check.py their type rules, including the three taking a TYPE
    support.py       the VLA arena, written in C and compiled by this frontend
    include/         the standard headers, written in C

WHAT IT ACCEPTS is C23, which is to say C: every version's syntax, every
version's semantics where they agree, and the newer one where they do not.

FOUR PLACES THE LANGUAGE DIFFERS from a hosted implementation on x86-64
Linux, and this is the whole list rather than the beginning of one. What the
LIBRARY cannot do -- read, tell the time, start a thread -- is a different
list, and it is in `include/README.md` beside the headers that say so:

  * `long double` IS `double`. The IR has `f32` and `f64` and nothing wider.
    `__SIZEOF_LONG_DOUBLE__` says 8 and `<float.h>`'s `LDBL_*` macros have
    `double`'s values, so a program that asks is told the truth.
  * `_Complex` and `_Imaginary` are refused, for the same reason and with a
    diagnostic that says so rather than a parse error.
  * `setjmp`/`longjmp` are refused. A non-local jump needs the machine's
    frame, and the IR deliberately has no way to name one.
  * `main`'s parameters are `0` and a null `argv`. The platform floor is
    `plat_write`, `plat_exit` and `plat_heap`; none of them can ask the host
    for a command line, and inventing one would be worse than saying so.

Everything else -- VLAs, flexible array members, bit-fields, anonymous
members, `_Generic`, designated initialisers, compound literals, `__VA_OPT__`,
K&R definitions, statement expressions, `__attribute__` where it is advisory,
and GNU's `__typeof__`/`__restrict` spellings because real headers use them
-- is implemented.

ALL THIRTY-ONE HEADERS C23 REQUIRES are in `include/`. Three of them refuse
with a reason rather than being absent -- `<complex.h>`, `<setjmp.h>` and
`<threads.h>` -- because a missing file is a mystery and a refusal is an
answer. `<stdatomic.h>` is supported and `<threads.h>` is not, which is not a
contradiction: with one thread every operation is already atomic.

ONE TRANSLATION UNIT PER BUILD. `asmpython build prog.c` compiles `prog.c`
and whatever it includes; there is no separate compilation and no linker step
that joins two objects this frontend produced. A project with several `.c`
files builds as a unity build -- one file that `#include`s the others -- which
is what the `static` definitions in `include/` assume and why they may carry
definitions at all. The driver takes one source for every frontend, so this
is the shape of the tool rather than a limit of the language.

THE STANDARD LIBRARY IS COMPILED FROM C, by this frontend, from `include/`.
It sits on the three platform-floor functions and nothing else, so a C program
built here runs on every backend and in the IR interpreter -- which is what
makes the differential suite able to compare all of them.
"""
from __future__ import annotations

from pathlib import Path

from ...diagnostics import DiagnosticSink, SourceFile, error
from ...frontend import Frontend, register
from ...ir import Module
from .lower import Lowerer
from .parser import parse
from .preprocess import Preprocessor, Search

#: Set by the driver before a compile. A module global for the same reason
#: `frontends/python/imports.py` uses one: a frontend is handed a source and a
#: sink, so anything the driver knows and it needs arrives this way.
_SEARCH = Search()
_DEFINES: dict[str, str] = {}
_TRIGRAPHS = False


def use(*, include_paths=(), quote_paths=(), defines=None, bundled=True,
        trigraphs=False) -> None:
    """Publish the include search path and command-line macros.

    Republished on every compilation, so two in one process cannot see each
    other's paths -- the same rule the Python frontend's `imports.use` keeps.
    """
    global _SEARCH, _DEFINES, _TRIGRAPHS
    _SEARCH = Search(quote=[Path(p) for p in quote_paths],
                     angle=[Path(p) for p in include_paths],
                     bundled=bundled)
    _DEFINES = dict(defines or {})
    _TRIGRAPHS = trigraphs


class CFrontend(Frontend):
    name = "c"
    #: `.h` IS NOT HERE. A header is not a translation unit, and claiming the
    #: extension would make `asmpython build foo.h` compile something that
    #: was never meant to stand alone. `.i` is preprocessed C and is still C.
    extensions = (".c", ".i")
    description = "C23, with the standard library compiled from C"

    def compile(self, source: SourceFile, sink: DiagnosticSink) -> Module | None:
        pp = Preprocessor(sink, _SEARCH, trigraphs=_TRIGRAPHS,
                          defines=_DEFINES)
        tokens = pp.run(source)
        if sink.failed:
            return None
        unit, parser = parse(tokens, sink)
        if sink.failed:
            # Lowering assumes the parser accepted everything it sees. Running
            # it anyway produces IR the verifier rejects, and the user gets an
            # internal-error report on top of their real diagnostics.
            return None
        try:
            return Lowerer(unit, parser, source, sink).run()
        except RecursionError:
            sink.report(
                error("E1599", "this program is too deeply nested to compile")
                .at(unit.span)
                .note("lowering walks the tree recursively, and this one is "
                      "deeper than the interpreter stack allows"))
            return None


register(CFrontend())
