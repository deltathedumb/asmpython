"""The frontend's own support code, written in C and compiled by itself.

ONE ROUTINE NEEDS IT, and it needs it because of a real gap: `Op.ALLOCA` takes
a LITERAL byte count, and the whole point of a variable-length array is that
the count is not a literal. So a VLA's storage comes from an arena with a
bump pointer, and `__c_vla_mark`/`__c_vla_release` bracket each block that
declares one -- which is what gives a VLA inside a loop the lifetime C
promises rather than one allocation per iteration.

WHY IN C AND NOT IN IR. Three functions hand-built out of `Instruction`s is
sixty lines nobody can read and nothing checks; the same three in C are twenty
lines that the frontend's own parser, type checker and lowering all run over.
A compiler whose support code is written in the language it compiles cannot
have a support routine its own front end would reject -- and if this file ever
stops compiling, the test that compiles it says so before any user program
does.

THE ARENA IS NOT A GENERAL ALLOCATOR. It never frees to the platform, and a
release to a mark in an older region is IGNORED rather than honoured -- so the
worst case is that a block's VLA storage is not reused, never that a pointer
into it stops being valid. `plat_heap`'s contract says regions are not
guaranteed contiguous, and an arena that assumed they were would hand out
addresses past the end of one.
"""
from __future__ import annotations

from functools import lru_cache

from ...diagnostics import DiagnosticSink, SourceFile
from ...ir.module import Function, Global

SOURCE = r"""
/* asmpython C frontend: variable-length array storage. See support.py. */
typedef unsigned long __c_size;

extern void *plat_heap(long);

static char *__c_arena_next;
static char *__c_arena_base;
static char *__c_arena_end;

/* 64 KiB at a time. Large enough that an ordinary VLA never asks twice,
   small enough that a program with no VLAs -- which never calls this --
   is not the one paying for it. */
#define __C_ARENA_CHUNK 65536

void *__c_vla_alloc(long n)
{
    char *p;
    long want;
    if (n < 0) n = 0;
    n = (n + 15) & ~15L;              /* keep every block 16-byte aligned */
    if (__c_arena_next == 0 || __c_arena_next + n > __c_arena_end) {
        want = n > __C_ARENA_CHUNK ? n : __C_ARENA_CHUNK;
        p = (char *)plat_heap(want);
        if (p == 0) return 0;
        __c_arena_base = p;
        __c_arena_next = p;
        __c_arena_end = p + want;
    }
    p = __c_arena_next;
    __c_arena_next = p + n;
    return (void *)p;
}

void *__c_vla_mark(void)
{
    return (void *)__c_arena_next;
}

void __c_vla_release(void *mark)
{
    char *m = (char *)mark;
    /* A mark from BEFORE the current region cannot be restored: the bump
       pointer and the limit belong to the same region, and moving one
       without the other would hand out addresses past the end of it. The
       storage is simply not reused. */
    if (m >= __c_arena_base && m <= __c_arena_end)
        __c_arena_next = m;
}
"""


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[Function, ...], tuple[Global, ...]]:
    # IMPORTED HERE, not at the top: `lower` imports this module to splice the
    # result in, so a top-level import the other way is a cycle.
    from .lower import Lowerer
    from .parser import parse
    from .preprocess import Preprocessor
    sink = DiagnosticSink()
    source = SourceFile(SOURCE, "<asmpython C support>")
    tokens = Preprocessor(sink).run(source)
    unit, parser = parse(tokens, sink)
    if sink.failed:
        raise AssertionError(
            "the C frontend's own support code does not compile: "
            + "; ".join(d.message for d in sink.diagnostics))
    module = Lowerer(unit, parser, source, sink).run()
    # EXTERNALS COME TOO. The arena calls `plat_heap`, and the verifier
    # requires every `call` to name a function in the MODULE -- so splicing
    # the three definitions without the declaration they depend on produces
    # IR that fails to verify in the user's program rather than in this one.
    # `main` does not: the support unit has none, and the wrapper this
    # frontend generates for one would collide with the user's.
    functions = tuple(f for f in module.functions if f.name != "main")
    return functions, tuple(module.globals)


def compile_support(sink: DiagnosticSink) -> tuple[list[Function], list[Global]]:
    """The support functions and their globals, compiled once per process."""
    functions, globals_ = _compiled()
    return [_clone_function(f) for f in functions], [
        Global(g.name, g.size, g.data, g.readonly, g.linkage, g.align, g.span)
        for g in globals_]


def _clone_function(fn: Function) -> Function:
    """A fresh copy, because a Module owns its functions and two compilations
    in one process must not share the blocks of one."""
    from ...ir.module import Block
    out = Function(fn.name, fn.ret, list(fn.params), dict(fn.registers), [],
                   fn.linkage, fn.external, fn.span, fn._next_register)
    for blk in fn.blocks:
        out.blocks.append(Block(blk.label, [i.clone() for i in blk.instructions]))
    return out
