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
from ...options import Option, OptionError
from ...ir import Module
from .lower import Lowerer
from .parser import parse
from .preprocess import Preprocessor, Search


class CFrontend(Frontend):
    name = "c"
    #: `.h` IS NOT HERE. A header is not a translation unit, and claiming the
    #: extension would make `asmpython build foo.h` compile something that
    #: was never meant to stand alone. `.i` is preprocessed C and is still C.
    extensions = (".c", ".i")
    description = "C23, with the standard library compiled from C"

    #: THE FLAGS A C COMPILER HAS ALWAYS HAD, declared here rather than on
    #: the driver's parser: they are this frontend's and nobody else's, and a
    #: Python build offered `--include-path` would be offered a flag that
    #: means nothing to it. `--c:include-path` is always spellable too.
    options = (
        # `-I` AND `-D`, spelled as every C compiler spells them. They are
        # the two flags a C build is most likely to be handed by a Makefile
        # written elsewhere, and a compiler that only accepted the long form
        # would need that Makefile rewritten to use it.
        Option("include-path",
               "where #include <...> looks, before the bundled headers",
               metavar="DIR", repeat=True, short="I"),
        Option("define", "define a preprocessor macro; no value means 1",
               metavar="NAME[=VALUE]", repeat=True, short="D"),
        Option("trigraphs",
               "translate ??= and the rest; C23 deleted them", metavar="1|0"),
        Option("bundled-headers",
               "search the frontend's own standard headers",
               metavar="1|0"),
    )

    def __init__(self, *, include_paths: tuple[Path, ...] = (),
                 defines: tuple[tuple[str, str], ...] = (),
                 trigraphs: bool = False, bundled: bool = True) -> None:
        self.include_paths = include_paths
        self.defines = defines
        self.trigraphs = trigraphs
        self.bundled = bundled

    def configure(self, values: dict, context, sink: DiagnosticSink
                  ) -> "CFrontend":
        """A frontend carrying this run's flags. See `Frontend.configure`.

        `context` IS UNUSED HERE, deliberately: `#include "foo.h"` resolves
        against the INCLUDING FILE's directory, which the preprocessor knows
        from the file it is reading, and `<foo.h>` against the search path.
        Neither needs the source the driver started from, and nothing this
        frontend does is scoped by the target platform.
        """
        return CFrontend(
            include_paths=tuple(Path(p) for p in
                                values.get("include-path", ())),
            defines=tuple(_split_define(d) for d in values.get("define", ())),
            trigraphs=_truth(values, "trigraphs", self.trigraphs),
            bundled=_truth(values, "bundled-headers", self.bundled))

    def compile(self, source: SourceFile, sink: DiagnosticSink) -> Module | None:
        search = Search(angle=list(self.include_paths), bundled=self.bundled)
        pp = Preprocessor(sink, search, trigraphs=self.trigraphs,
                          defines=dict(self.defines))
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


def _split_define(item: str) -> tuple[str, str]:
    """`-D NAME=VALUE` and `-D NAME`, the latter defined as `1`.

    Split on the FIRST `=` only, because a macro body may contain one:
    `-D MAX(a,b)=((a)>(b)?(a):(b))` is a definition every build system
    writes, and splitting on all of them loses most of it.
    """
    name, sep, value = item.partition("=")
    return name, value if sep else "1"


def _truth(values: dict, name: str, default: bool) -> bool:
    """A `1|0` flag. The spelling `plugin add --cwd 1|0` already uses."""
    raw = values.get(name)
    if raw is None:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    raise OptionError(f"--{name} takes 1 or 0, not {raw!r}")


register(CFrontend())
