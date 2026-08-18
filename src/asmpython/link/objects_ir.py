"""The part of the object runtime that is IR, and how it gets into a program.

STAGE 3 OF `docs/INERT-RUNTIME.md`. The object runtime is 15,560 lines of C
(`link/objects.py`) plus an 8,583-line Python re-implementation for the IR
interpreter (`ir/objects_host.py`), and a backend that wants dynamic Python has
to find 229 `apy_*` symbols -- which is why exactly one backend has them. The
way out is to write the runtime in the machine subset and let it be IR, so that
every backend gets it for free and the two copies become one.

This is the mechanism, and one kind has gone through it.

## What happens

`src/asmpython/runtime/*.py` is asmpython source in the machine subset. It is
compiled BY THE PYTHON FRONTEND, as a library -- definitions only, no entry --
and the resulting functions and globals are merged into the program's module
before any backend sees it. No artifact is checked in and nothing is generated
ahead of time: the runtime is compiled by the compiler it is part of, every
time, which is the only arrangement in which it cannot go stale.

## Why this replaces rather than adds

A ported function keeps the C's NAME, so a program calls `apy_from_int` and
does not know or care which one it got. That means the C definition has to go,
or the two collide at link time -- so `objects_c()` takes the set of ported
names and `#if`s those definitions out. One switch, and it is the same switch
that makes a regression one flag away from being isolated.

## Why the IR interpreter does NOT get it, and what that tells us

It was going to. The interpreter calls `_host` for a symbol the module does not
DEFINE, so splicing a definition would have made it run the same IR the C
backend compiles -- one runtime, both paths, which is the design document's
central promise.

It cannot, and the reason is worth more than the feature would have been:
**the two runtimes disagree about what an `apy_value` IS.**
`ir/objects_host.py` represents one as a HANDLE into a Python-side table; the
ported code represents one as an ADDRESS in the interpreter's flat memory. So a
ported `apy_from_int` hands back something every unported function rejects --
observed as `apy_is: 2248 is not a runtime value handle`, a message naming
neither runtime.

**The port is therefore not incremental on the interpreter path.** It is
all-or-nothing there, and `Interpreter._call` says so: the host runtime owns
every `apy_*` name it claims, whatever the module defines.

That is not a setback for stage 3, it is what stage 3 is for. The interpreter
stays the ORACLE while the C path runs the ported code, so the corpus compares
a ported `apy_from_int` against an unported one on every program it runs --
which is a sharper test than having both paths run the same code would be.
What it changes is stage 6: `objects_host.py` cannot be retired function by
function, only in one step, once every kind it claims has been ported.
"""
from __future__ import annotations

from pathlib import Path

#: Where the subset-written runtime lives.
SOURCE_DIR = Path(__file__).resolve().parent.parent / "runtime"

#: The C definitions a ported module replaces, per source file.
#:
#: WRITTEN OUT rather than derived from what the compiled module exports,
#: because most of what a runtime file defines is its own helpers --
#: `apy_int_alloc`, `apy_small_slot` -- and those must NOT displace anything.
#: Only the names listed here are `#if`d out of the C, and a name listed here
#: that the module does not define is caught by `check()`.
REPLACES: dict[str, tuple[str, ...]] = {
    "arena.py": ("apy_obj_alloc",),
    "int_cell.py": ("apy_from_int", "apy_as_int"),
    # THE THREE STRING CONSTRUCTORS THAT BORROW THEIR BYTES. The ones that OWN
    # them -- `apy_str_take`, `apy_str_copy` -- are `static` in the C, so
    # `_definition_of` cannot find them and `signatures()` never typed them:
    # the subset cannot name them, let alone replace them. That is the wall
    # stage 5 stops at, and it is a C edit rather than more IR.
    # `apy_str_copy_bytes` IS THE ONE THE RUNTIME BUILDS EVERY STRING WITH --
    # 24 call sites reach it through the `apy_str_copy` shim -- so replacing it
    # moves a compiled program's string BYTES off malloc and onto the arena.
    # It could only be named once the promotion made it visible, and it needed
    # an `apy_value` parameter because that is what an IR `ptr` compiles to.
    "str_cell.py": ("apy_from_cstr", "apy_from_bytes", "apy_bytes_literal",
                    "apy_str_copy_bytes"),
    # THE BUFFERS, which is the prerequisite docs/INERT-RUNTIME.md names ahead
    # of `list` and `dict`: those two grow by DOUBLING and release the block
    # they grew out of, and stage 4's bump arena can neither resize nor
    # reclaim. Size classes and a free list over the same arena, so the
    # platform floor is still three functions.
    "blocks.py": ("apy_alloc_block", "apy_realloc_block", "apy_free_block"),
    # THE ASCII PREDICATES, and the smallest thing in the runtime that can be
    # ported: they take a number and answer a number, so there is no cell
    # layout to know and no allocation to get right. The four not listed here
    # are written out as specifications at the foot of `runtime/ascii.py`; a
    # name joins this tuple when its function exists, and until then the C's
    # version is what a program uses.
    "ascii.py": ("apy_c_lower",),
}

#: The C functions a ported module takes the FRONT of, per source file.
#:
#: Different from `REPLACES` in exactly one way and it is the important one:
#: the C body is not dropped, it is renamed `<name>_slow` and the ported code
#: calls it for everything it does not handle. `apy_add` is polymorphic over
#: eighteen kinds and its integer case is four instructions, so a port that had
#: to be total could not begin. See `link/objects.split_c`.
SPLITS: dict[str, tuple[str, ...]] = {
    "int_arith.py": ("apy_add", "apy_sub", "apy_mul"),
    # THE FIRST PORTED CODE THAT READS A CELL RATHER THAN BUILDING ONE. All
    # three are polymorphic over every kind and all three DECLINE everything
    # that is not a str or bytes -- which matters most for `apy_truth`, whose
    # default is TRUE: a fast path that read `v.s.n` for a type would read its
    # base pointer instead.
    "str_len.py": ("apy_raw_len", "apy_len", "apy_truth"),
    # THE FIRST PORTED CODE THAT ALLOCATES A BUFFER. `apy_chr` asks the arena
    # for five bytes and hands them to a cell the rest of the runtime reads --
    # which is the question every later kind runs into (who owns the bytes,
    # and what happens when nobody can give them back) asked at the smallest
    # scale there is.
    "str_code.py": ("apy_ord", "apy_chr"),
    # THE LARGEST GROUP SO FAR, and the safest: six entry points over one
    # shared search, and not one of them allocates anything but the integer it
    # answers with. The whole group is verifiable by comparing values, with no
    # question about who owns a buffer.
    "str_find.py": ("apy_str_find", "apy_str_find2", "apy_str_find3",
                    "apy_str_rfind", "apy_str_rfind2", "apy_str_rfind3"),
}

#: Every C symbol currently provided by IR instead. What `objects_c()` guards.
PORTED = tuple(sorted(
    [n for names in REPLACES.values() for n in names]
    + [n for names in SPLITS.values() for n in names]))

#: Every C symbol that keeps its body under a new name.
SPLIT = tuple(sorted(n for names in SPLITS.values() for n in names))


def sources() -> list[Path]:
    """The runtime modules, in a stable order."""
    return [SOURCE_DIR / name
            for name in sorted(set(REPLACES) | set(SPLITS))]


def compile_runtime(sink=None):
    """Compile every runtime module and return one merged IR module.

    A FRESH SINK BY DEFAULT, and diagnostics from it are a compiler bug rather
    than a user's: this source is shipped, not written by whoever is compiling.
    A caller that passes one wants to see them -- the tests do.
    """
    from ..diagnostics import DiagnosticSink, SourceFile
    from ..frontends.python import PythonFrontend
    from ..ir.module import Module

    own = sink if sink is not None else DiagnosticSink()
    frontend = PythonFrontend()
    # ONE COMPILATION UNIT, not one per file. The runtime is a single library
    # and its pieces call each other -- `int_arith.py` reads the cell layout
    # `int_cell.py` defines -- and the static path resolves a call against the
    # functions of the module being compiled. Compiling separately made every
    # cross-file call `call to unknown function`, and the obvious fix
    # (duplicate the helpers) made the merge refuse two definitions of
    # `apy_obj_size`, which is the same problem wearing a different error.
    #
    # The files stay separate because they are separate SUBJECTS, and the
    # order is `sources()`'s so a build is reproducible.
    text = "\n\n".join(
        f"# ── {path.name} " + "─" * 40 + "\n"
        + path.read_text(encoding="utf-8")
        for path in sources())
    module = frontend.compile(
        SourceFile(text, SOURCE_DIR / "<runtime>"), own, library=True)
    if module is None:
        raise RuntimeError(
            "the shipped object runtime did not compile:\n"
            + "\n".join(f"  {d.code}: {d.message}" for d in own.diagnostics))
    # A RUNTIME FILE CALLING ANOTHER'S FUNCTION gets it declared as an external
    # too: the frontend declares the whole `apy_*` runtime and keeps whatever
    # is called. So `apy_from_int` arrives both DECLARED and DEFINED, which is
    # one name and two functions -- rejected by the verifier, after the splice,
    # against the symbol rather than against this.
    defined = {f.name for f in module.functions if not f.external}
    module.functions = [f for f in module.functions
                        if not (f.external and f.name in defined)]
    return module


def _merge(into, module) -> None:
    """Add `module`'s definitions to `into`, dropping duplicate declarations.

    Two runtime files both calling `apy_alloc` each declare it, and a module
    with the symbol declared twice is rejected by the verifier -- which reports
    it against the second declaration rather than against the merge that made
    it.
    """
    have = {f.name for f in into.functions}
    for fn in module.functions:
        if fn.name in have:
            if not fn.external:
                raise RuntimeError(
                    f"the runtime defines {fn.name} more than once")
            continue
        have.add(fn.name)
        into.functions.append(fn)
    names = {g.name for g in into.globals}
    for g in module.globals:
        if g.name not in names:
            names.add(g.name)
            into.globals.append(g)


def wants_runtime(module) -> bool:
    """Whether this program will have the object runtime's C in it.

    NOT "does the program call a ported function", and not even "does it call
    `apy_*`". The C object runtime calls `apy_from_int` in about 120 places,
    and it is included whole -- so the moment ANY of it is compiled in, the
    ported definition has to be there or the reference is unresolved.

    Both narrower questions were tried and both are wrong in the same way.
    Asking about ported names left it undefined for every dynamic program that
    did not build an integer directly. Asking about `apy_*` left it undefined
    for every STATIC program, which links the runtime for `put_int` and gets
    `objects_c()` along with it -- and that one fails only on the machine
    backends, where the runtime is a separate object file and the linker
    cannot drop what a compiler in one translation unit would have:

        rt.c:(.text+0x248f): undefined reference to `apy_from_int'

    So the question is exactly `link.runtime.needs_runtime`, which is what
    decides whether that C is there at all.

    AND THAT WAS ALSO WRONG, for a different reason: it fires for every program
    that merely PRINTS, so ten runtime functions and a two-kilobyte global went
    into programs that never build an object -- which the x86-64 and AArch64
    backends and the lifter all noticed, 101 tests' worth.

    The resolution is that the switch is per-BUILD and reads the module: the C
    omits exactly the definitions this module supplies, so a program with no
    splice keeps the C's. See `omitted_by`.
    """
    from ..ir.opcodes import Op
    return any(ins.sym and ins.sym.startswith("apy_")
               for f in module.functions for b in f.blocks
               for ins in b.instructions if ins.op is Op.CALL)


def omitted_by(module) -> tuple[str, ...]:
    """The ported names THIS module defines, so the C can stand aside.

    ASKED OF THE MODULE rather than threaded through from the splice, because
    the module is what both C emitters already have and because it cannot get
    out of step with itself: the C omits a definition exactly when the IR
    provides one, whatever decided that.
    """
    if module is None:
        return ()
    have = {f.name for f in module.functions if not f.external}
    return tuple(n for n in PORTED if n in have and n not in SPLIT)


def split_by(module) -> tuple[str, ...]:
    """The SPLIT names this module supplies a front half for.

    Separate from `omitted_by` because the two ask the C for opposite things:
    omit drops the body, split keeps it under another name. A function in both
    lists would be a body renamed and then deleted, which is a link error
    naming `apy_add_slow`.
    """
    if module is None:
        return ()
    have = {f.name for f in module.functions if not f.external}
    return tuple(n for n in SPLIT if n in have)


def splice(module, sink=None, *, provided=frozenset(), enabled: bool = True) -> None:
    """Merge the IR runtime into `module`, in place. A no-op if unneeded.

    Called once per compilation, after the frontend and before any backend.
    A program that never touches the object runtime gets nothing -- not even a
    declaration -- so "a statically typed program has no runtime dependencies"
    stays true, which is the property `link/runtime.needs_runtime` exists to
    protect and the one a silent splice would quietly end.

    `enabled=False` is the whole-build opt-out (`--object-runtime c`) and
    `provided` is the per-backend one (`Backend.object_runtime`). BOTH EXIST
    ON PURPOSE. The reason to write the runtime in IR is that a backend should
    not have to define 229 functions -- which is an argument for making it
    unnecessary, not for making it compulsory. A backend with its own
    implementation keeps it, and a build that wants the C runtime exactly as
    it was before any of this gets it, unchanged and still tested.

    Whatever is left after both is what the C stands aside for, because the C
    omits exactly what the module defines. So there is one rule and no way for
    the two halves to disagree: if it is not spliced, the C still has it.
    """
    if not enabled or not wants_runtime(module):
        return
    runtime = compile_runtime(sink)
    if provided:
        # DROP WHAT THE BACKEND ALREADY HAS -- and only the replaced names, not
        # the helpers behind them: `apy_int_alloc` is this file's own and a
        # backend claiming `apy_from_int` has no opinion about it. An unreached
        # helper is dropped downstream like any other.
        keep = set(provided) & set(PORTED)
        runtime.functions = [f for f in runtime.functions
                             if f.name not in keep]
    #: A DEFINITION BEATS A DECLARATION. The program declared `apy_from_int`
    #: as an external because the frontend declares the whole runtime that
    #: way; the merged module DEFINES it. Keeping both is two functions of one
    #: name, which the verifier rejects -- correctly, and unhelpfully, because
    #: it names the symbol rather than the splice.
    defined = {f.name for f in runtime.functions if not f.external}
    module.functions = [f for f in module.functions
                        if not (f.external and f.name in defined)]
    _merge(module, runtime)


def check() -> list[str]:
    """Problems with the port, as a list of messages. Empty is good.

    Read by a test rather than by the compiler: this asks whether the tree is
    consistent, which is a question about the tree and not about any one
    program.
    """
    problems: list[str] = []
    module = compile_runtime()
    defined = {f.name for f in module.functions if not f.external}
    for source, names in list(REPLACES.items()) + list(SPLITS.items()):
        for name in names:
            if name not in defined:
                problems.append(
                    f"{source} is declared to replace {name}, and does not "
                    f"define it")
    from .objects import signatures
    known = signatures()
    for name in PORTED:
        if name not in known:
            problems.append(f"{name} is not an APY_API symbol in the C")
    return problems
