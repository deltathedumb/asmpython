"""Several translation units, one module.

WHY THIS IS NOT A LINKER. A linker joins OBJECT FILES: relocations, sections,
symbol tables, archives, weak symbols and the order they are searched in.
This joins MODULES, which is a different and much smaller thing -- the IR has
no relocations, a function is a function, and a symbol is a name in one flat
namespace. `link/` is where the other kind lives, and it works on what a
backend produced rather than on what a frontend did.

WHAT MAKES IT POSSIBLE AT ALL is that `parser._merge` gives every unit's own
`static` names a prefix of that unit's -- `c0.`, `c1.` -- so two files that
each define `static int count` arrive here as two different names and nothing
has to be renamed. The one shared prefix is `c.`, the bundled library's: every
unit that includes `<stdio.h>` compiles its own copy of `printf`, and this
keeps ONE of them, exactly as a real toolchain links one `libc.a`. That is
what makes `errno` set in one file readable in another.

WHAT IS AN ERROR HERE, and it is the one thing a C programmer expects from
this stage: two units that define the same external name. C says a program
may have one external definition of each identifier, a linker says
"multiple definition of", and so does this -- naming both files.

DECLARATION VERSUS DEFINITION is the other half of the same question. Unit A
calling `helper` and unit B defining it is the ordinary case and the whole
point: the call is an external DECLARATION, the definition wins, and the
verifier that runs afterwards sees exactly one of each name.
"""
from __future__ import annotations

from ...diagnostics import DiagnosticSink, error
from ...ir.module import Function, Global, Linkage, Module
from .lower import USER_MAIN, prune

#: The prefix the bundled headers' own names carry, from `parser._merge`.
LIBRARY = "c."

#: What the frontend's own support code is called -- `support.py`'s arena and
#: command line. Every unit that needs one splices its own copy, and they are
#: the same code, so the merge keeps one without complaining.
SUPPORT = "__c_"


def merge(modules: list[Module], sources: list[str],
          sink: DiagnosticSink) -> Module:
    """One module from several, or diagnostics saying why not."""
    out = Module(name=modules[0].name)
    out.metadata = dict(modules[0].metadata)
    out.metadata["sources"] = ", ".join(sources)
    where: dict[str, str] = {}          # name -> the source that defined it
    for module, source in zip(modules, sources):
        for fn in module.functions:
            _add_function(out, fn, source, where, sink)
        for g in module.globals:
            _add_global(out, g, source, where, sink)
    # THE SECOND PRUNE, and it is not a repeat of the first. Each unit
    # dropped what IT could not reach; the merge then dropped duplicate
    # copies of the library, which leaves the string literals and the
    # statics those copies were the only users of. Nothing else would.
    prune(out)
    return out


def _shared(name: str) -> bool:
    """Whether two units may legitimately both define `name`.

    The library and the frontend's own support code are compiled once per
    unit and are the same text each time, so the second copy is not a
    program saying two different things -- it is one thing said twice.
    """
    return name.startswith(LIBRARY) or name.startswith(SUPPORT)


def _add_function(out: Module, fn: Function, source: str,
                  where: dict[str, str], sink: DiagnosticSink) -> None:
    have = out.function(fn.name)
    if have is None:
        out.functions.append(fn)
        if not fn.external:
            where[fn.name] = source
        return
    if fn.external:
        return                              # a declaration of what we have
    if have.external:
        out.functions[out.functions.index(have)] = fn
        where[fn.name] = source
        return
    if _shared(fn.name):
        return                              # one copy of the library
    first = where.get(fn.name, "another translation unit")
    shown = "main" if fn.name == USER_MAIN else fn.name
    sink.report(
        error("E1600", f"{shown!r} is defined in more than one translation "
                       f"unit")
        .at(fn.span)
        .note(f"first defined in {first}, again in {source}")
        .help("a program has one external definition of each name; make one "
              "of them `static` if they are meant to be different functions"))


def _add_global(out: Module, g: Global, source: str,
                where: dict[str, str], sink: DiagnosticSink) -> None:
    have = out.global_(g.name)
    if have is None:
        out.globals.append(g)
        if not g.is_zero_filled:
            where[g.name] = source
        return
    if g.is_zero_filled:
        return
    if have.is_zero_filled:
        # A TENTATIVE DEFINITION IN ONE UNIT AND A REAL ONE IN ANOTHER, which
        # is `int x;` here and `int x = 3;` there. C says a program has one
        # external definition of each object and this is two, so it is
        # undefined -- and the two answers in the field are gcc's old
        # `-fcommon` (merge them, the initialised one wins) and its current
        # `-fno-common` (refuse). This merges, because that is what the code
        # written against the older rule expects and because refusing a
        # program a linker would accept is the less useful of the two errors
        # to be right about.
        out.globals[out.globals.index(have)] = g
        where[g.name] = source
        return
    if _shared(g.name) or g.data == have.data:
        return
    first = where.get(g.name, "another translation unit")
    sink.report(
        error("E1601", f"{g.name!r} is initialised in more than one "
                       f"translation unit")
        .at(g.span)
        .note(f"first in {first}, again in {source}")
        .help("a program has one external definition of each object; declare "
              "it `extern` in all but one of them"))
