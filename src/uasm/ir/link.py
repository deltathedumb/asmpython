"""Joining several modules into one, at the IR.

WHY THIS IS A STAGE AND NOT A CONVENIENCE. A compiler that can only see one
translation unit at a time has to give up at every file boundary: a call
across it is an opaque external, so nothing is inlined through it, no argument
is known to be non-null, and no unused definition is proved unused. Joining
first and optimising second is the whole reason to have an IR that outlives a
single compilation -- it is what `-flto` buys, without the "time" part.

NOT THE PLATFORM LINKER, and deliberately not spelled like it. This resolves
NAMES between modules the compiler produced; `link/` resolves SYMBOLS between
objects a platform produced, and only one of those can put a program on disk.
`uasm link` picks between them by what it was handed, which is why both are
reachable through one verb -- see `driver/cli.py`.

A DEFINITION BEATS A DECLARATION and two definitions are an error. That is the
same rule every linker has, and it is here rather than in the verifier because
the verifier sees one module and would report "two functions named `f`" against
the second one, naming neither file it came from.
"""
from __future__ import annotations

from ..diagnostics import DiagnosticSink, error
from .module import Module


def merge(modules: list[tuple[str, Module]],
          sink: DiagnosticSink) -> Module | None:
    """One module from many, or None having reported why not.

    `modules` is (name, module) pairs, in the order the user gave them,
    because every message here has to be able to say WHICH input -- a
    duplicate symbol whose report names only the symbol sends the reader to
    grep for it.

    ORDER IS THE USER'S. A later definition does not override an earlier one
    (that is the error), but the ORDER of what comes out follows the order of
    what went in, so the result of joining the same files twice is the same
    file.
    """
    if not modules:
        sink.report(error("E9110", "nothing to link"))
        return None

    out = Module(name=modules[0][1].name)
    #: name -> the input that DEFINED it. Declarations are not recorded: a
    #: symbol may be declared in every input and that is not a conflict.
    defined: dict[str, str] = {}
    globals_from: dict[str, str] = {}
    failed = False

    for where, module in modules:
        for fn in module.functions:
            if not fn.external:
                if fn.name in defined:
                    sink.report(
                        error("E9111",
                              f"{fn.name} is defined in two inputs")
                        .note(f"first in {defined[fn.name]}")
                        .note(f"again in {where}")
                        .help("a symbol may be DECLARED in every input and "
                              "defined in exactly one"))
                    failed = True
                    continue
                defined[fn.name] = where
            out.functions.append(fn)
        for g in module.globals:
            if g.name in globals_from:
                # TWO GLOBALS OF ONE NAME, unlike two declarations, is always
                # a conflict: a global is storage, and there is no spelling
                # for "this is the same storage the other file means".
                sink.report(
                    error("E9111", f"global {g.name} is defined in two inputs")
                    .note(f"first in {globals_from[g.name]}")
                    .note(f"again in {where}"))
                failed = True
                continue
            globals_from[g.name] = where
            out.globals.append(g)

    if failed:
        return None

    # A DEFINITION BEATS A DECLARATION, and the declaration goes. Keeping both
    # is one name and two functions, which the verifier rejects -- correctly,
    # and unhelpfully, because by then nothing knows there was a link.
    #
    # KEPT IN INPUT ORDER rather than rebuilt from `defined`, so the output of
    # joining the same files twice is byte-for-byte the same.
    seen_external: set[str] = set()
    kept = []
    for fn in out.functions:
        if not fn.external:
            kept.append(fn)
            continue
        if fn.name in defined or fn.name in seen_external:
            continue
        seen_external.add(fn.name)
        kept.append(fn)
    out.functions = kept

    out.metadata = dict(modules[0][1].metadata)
    out.metadata["linked"] = ", ".join(where for where, _ in modules)
    return out
