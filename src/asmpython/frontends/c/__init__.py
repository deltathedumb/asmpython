"""The C frontend: it compiles C.

    lexer.py         phases 1-3: source text -> preprocessing tokens
    preprocess.py    phase 4: directives and macro expansion
    parser.py        phases 7a: the grammar, typed as it is parsed
    sema.py          the type rules the parser calls into
    fold.py          constant expressions, which C requires in six places
    lower.py         typed tree -> APIR
    include/         the standard headers, written in C

WHAT IT ACCEPTS is C23, which is to say C: every version's syntax, every
version's semantics where they agree, and the newer one where they do not.
`asmpython frontends -v` prints the four places it knowingly differs from a
hosted implementation on x86-64 Linux, and they are all here in one list:

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
K&R definitions, statement expressions, `__attribute__` where it is advisory
-- is implemented, and the four above are the whole list rather than the
beginning of one.

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
from .preprocess import BUNDLED, Preprocessor, Search

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
