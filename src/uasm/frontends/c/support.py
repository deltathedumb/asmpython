"""The frontend's own support code, written in C and compiled by itself.

TWO UNITS, EACH SPLICED ONLY WHEN THE PROGRAM NEEDS IT. `vla` is the arena a
variable-length array's storage comes from; `args` is the command line a
`main` that declares parameters is handed. Separately, because each costs
something a program that does not use it must not pay: the arena is 64 KiB of
heap the first time it is touched, and the command line is a call into
`objects/hostsvc.py`'s `env` group, which a backend may not have -- and a
program whose `main` takes no arguments must still run on such a target.

THE ARENA, and it exists because of a real gap: `Op.ALLOCA` takes
a LITERAL byte count, and the whole point of a variable-length array is that
the count is not a literal. So a VLA's storage comes from an arena with a
bump pointer, and `__c_vla_mark`/`__c_vla_release` bracket each block that
declares one -- which is what gives a VLA inside a loop the lifetime C
promises rather than one allocation per iteration.

THE COMMAND LINE, and it is here for the same reason: `main(argc, argv)` is
an array of pointers and a block of characters that have to be built from
`host_arg_count` and `host_arg_get` before the program's own first statement
runs. Twenty lines of C, or forty `Instruction`s in `lower.py` that nothing
would ever read.

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
/* uasm C frontend: variable-length array storage. See support.py. */
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

ARGS_SOURCE = r"""
/* uasm C frontend: the command line, for a `main` that takes one.
   See support.py. */
extern void *plat_heap(long);
extern long host_arg_count(void);
extern long host_arg_get(long, void *, long);

static char **__c_argv_vec;
static long __c_argv_n;
static int __c_argv_done;

/* ONE BLOCK FOR BOTH THE POINTERS AND THE CHARACTERS, which is what makes
   this need no allocator: `plat_heap` is asked once for the array of
   `argc + 1` pointers followed by every argument's bytes, and nothing is
   ever freed -- `argv` lives as long as the program does, which is exactly
   the lifetime the platform floor's heap has.

   ASKED TWICE FOR EACH ARGUMENT, and the contract is why: `host_arg_get`
   copies into a caller's buffer and answers the length it NEEDED, so a
   caller learns the size by asking with no room at all and then asks again
   with enough. The layer must not allocate, because who frees it has a
   different answer in every backend. */
char **__c_args_build(void)
{
    long n, i, len, total = 0, used = 0;
    char **vec;
    char *text;
    if (__c_argv_done) return __c_argv_vec;
    __c_argv_done = 1;
    n = host_arg_count();
    if (n < 0) n = 0;
    for (i = 0; i < n; i++) {
        len = host_arg_get(i, 0, 0);
        if (len < 0) len = 0;
        total += len + 1;
    }
    vec = (char **)plat_heap((n + 1) * (long)sizeof(char *) + total);
    if (vec == 0) return 0;
    text = (char *)vec + (n + 1) * (long)sizeof(char *);
    for (i = 0; i < n; i++) {
        len = host_arg_get(i, text + used, total - used);
        if (len < 0) len = 0;
        if (len > total - used - 1) len = total - used - 1;
        text[used + len] = 0;
        vec[i] = text + used;
        used += len + 1;
    }
    /* `argv[argc]` IS A NULL POINTER, which C requires and which a program
       that walks the array instead of counting relies on. */
    vec[n] = 0;
    __c_argv_vec = vec;
    __c_argv_n = n;
    return vec;
}

long __c_args_count(void)
{
    __c_args_build();
    return __c_argv_n;
}
"""

COMPLEX_SOURCE = r"""
/* uasm C frontend: complex multiplication and division. See support.py.

   THESE TWO AND NOT THE OTHER TWO. Complex addition is two adds and the
   lowering emits them; multiplication and division have a formula each, and
   the formula is not the hard part -- what an infinity times a zero has to
   produce is. C's Annex G says exactly that, libgcc's `__muldc3` and
   `__divdc3` implement it, and this is the same algorithm: without the
   recovery step, `(inf + 0i) * (2 + 3i)` is `nan + nan i` instead of
   `inf + inf i`, and a program cannot tell an overflow from a mistake.

   THE FLOAT VERSION COMPUTES IN FLOAT, and that is not fussiness either:
   doing it in double and rounding once at the end gives a different last bit
   from every other compiler, which is exactly the kind of difference the
   three-way test exists to catch. */
#include <math.h>

void __c_cmul(double a, double b, double c, double d, double *out)
{
    double ac = a * c, bd = b * d, ad = a * d, bc = b * c;
    double x = ac - bd, y = ad + bc;
    if (isnan(x) && isnan(y)) {
        int recalc = 0;
        /* AN INFINITY THAT BECAME A NAN. `inf * 0` is a nan, and a product
           with an infinity in it must be an infinity -- so the infinite
           operand becomes a signed one or zero and the product is redone at
           infinite scale, which is Annex G's rule written out. */
        if (isinf(a) || isinf(b)) {
            a = copysign(isinf(a) ? 1.0 : 0.0, a);
            b = copysign(isinf(b) ? 1.0 : 0.0, b);
            if (isnan(c)) c = copysign(0.0, c);
            if (isnan(d)) d = copysign(0.0, d);
            recalc = 1;
        }
        if (isinf(c) || isinf(d)) {
            c = copysign(isinf(c) ? 1.0 : 0.0, c);
            d = copysign(isinf(d) ? 1.0 : 0.0, d);
            if (isnan(a)) a = copysign(0.0, a);
            if (isnan(b)) b = copysign(0.0, b);
            recalc = 1;
        }
        if (!recalc && (isinf(ac) || isinf(bd) || isinf(ad) || isinf(bc))) {
            if (isnan(a)) a = copysign(0.0, a);
            if (isnan(b)) b = copysign(0.0, b);
            if (isnan(c)) c = copysign(0.0, c);
            if (isnan(d)) d = copysign(0.0, d);
            recalc = 1;
        }
        if (recalc) {
            x = INFINITY * (a * c - b * d);
            y = INFINITY * (a * d + b * c);
        }
    }
    out[0] = x;
    out[1] = y;
}

void __c_cmulf(float a, float b, float c, float d, float *out)
{
    float ac = a * c, bd = b * d, ad = a * d, bc = b * c;
    float x = ac - bd, y = ad + bc;
    if (isnan(x) && isnan(y)) {
        int recalc = 0;
        if (isinf(a) || isinf(b)) {
            a = (float)copysign(isinf(a) ? 1.0 : 0.0, a);
            b = (float)copysign(isinf(b) ? 1.0 : 0.0, b);
            if (isnan(c)) c = (float)copysign(0.0, c);
            if (isnan(d)) d = (float)copysign(0.0, d);
            recalc = 1;
        }
        if (isinf(c) || isinf(d)) {
            c = (float)copysign(isinf(c) ? 1.0 : 0.0, c);
            d = (float)copysign(isinf(d) ? 1.0 : 0.0, d);
            if (isnan(a)) a = (float)copysign(0.0, a);
            if (isnan(b)) b = (float)copysign(0.0, b);
            recalc = 1;
        }
        if (!recalc && (isinf(ac) || isinf(bd) || isinf(ad) || isinf(bc))) {
            if (isnan(a)) a = (float)copysign(0.0, a);
            if (isnan(b)) b = (float)copysign(0.0, b);
            if (isnan(c)) c = (float)copysign(0.0, c);
            if (isnan(d)) d = (float)copysign(0.0, d);
            recalc = 1;
        }
        if (recalc) {
            x = (float)INFINITY * (a * c - b * d);
            y = (float)INFINITY * (a * d + b * c);
        }
    }
    out[0] = x;
    out[1] = y;
}

/* THE SCALING IS WHAT MAKES THE DIVISION USABLE. `(1e300 + 1e300i) /
   (1e300 + 1e300i)` is 1, and the obvious formula computes `c*c + d*d` on
   the way -- which overflows. Dividing both by a power of two first costs
   nothing and cannot lose a bit. */
static int __c_ilogb2(double x)
{
    int e = 0;
    if (x == 0.0 || isnan(x) || isinf(x)) return 0;
    frexp(x, &e);
    return e - 1;
}

void __c_cdiv(double a, double b, double c, double d, double *out)
{
    double denom, x, y, big = fabs(c) > fabs(d) ? fabs(c) : fabs(d);
    int ilogbw = 0;
    if (big != 0.0 && !isinf(big) && !isnan(big)) {
        ilogbw = __c_ilogb2(big);
        c = scalbn(c, -ilogbw);
        d = scalbn(d, -ilogbw);
    }
    denom = c * c + d * d;
    x = scalbn((a * c + b * d) / denom, -ilogbw);
    y = scalbn((b * c - a * d) / denom, -ilogbw);
    if (isnan(x) && isnan(y)) {
        if (denom == 0.0 && (!isnan(a) || !isnan(b))) {
            /* DIVISION BY ZERO IS AN INFINITY, not a nan, and it keeps the
               sign of the zero it was divided by. */
            x = copysign(INFINITY, c) * a;
            y = copysign(INFINITY, c) * b;
        } else if ((isinf(a) || isinf(b)) && !isinf(c) && !isnan(c)
                   && !isinf(d) && !isnan(d)) {
            a = copysign(isinf(a) ? 1.0 : 0.0, a);
            b = copysign(isinf(b) ? 1.0 : 0.0, b);
            x = INFINITY * (a * c + b * d);
            y = INFINITY * (b * c - a * d);
        } else if ((isinf(big)) && !isinf(a) && !isnan(a)
                   && !isinf(b) && !isnan(b)) {
            c = copysign(isinf(c) ? 1.0 : 0.0, c);
            d = copysign(isinf(d) ? 1.0 : 0.0, d);
            x = 0.0 * (a * c + b * d);
            y = 0.0 * (b * c - a * d);
        }
    }
    out[0] = x;
    out[1] = y;
}

void __c_cdivf(float a, float b, float c, float d, float *out)
{
    float denom, x, y, big = fabsf(c) > fabsf(d) ? fabsf(c) : fabsf(d);
    int ilogbw = 0;
    if (big != 0.0f && !isinf(big) && !isnan(big)) {
        ilogbw = __c_ilogb2((double)big);
        c = (float)scalbn((double)c, -ilogbw);
        d = (float)scalbn((double)d, -ilogbw);
    }
    denom = c * c + d * d;
    x = (float)scalbn((double)((a * c + b * d) / denom), -ilogbw);
    y = (float)scalbn((double)((b * c - a * d) / denom), -ilogbw);
    if (isnan(x) && isnan(y)) {
        if (denom == 0.0f && (!isnan(a) || !isnan(b))) {
            x = (float)copysign(INFINITY, c) * a;
            y = (float)copysign(INFINITY, c) * b;
        } else if ((isinf(a) || isinf(b)) && !isinf(c) && !isnan(c)
                   && !isinf(d) && !isnan(d)) {
            a = (float)copysign(isinf(a) ? 1.0 : 0.0, a);
            b = (float)copysign(isinf(b) ? 1.0 : 0.0, b);
            x = (float)INFINITY * (a * c + b * d);
            y = (float)INFINITY * (b * c - a * d);
        } else if (isinf(big) && !isinf(a) && !isnan(a)
                   && !isinf(b) && !isnan(b)) {
            c = (float)copysign(isinf(c) ? 1.0 : 0.0, c);
            d = (float)copysign(isinf(d) ? 1.0 : 0.0, d);
            x = 0.0f * (a * c + b * d);
            y = 0.0f * (b * c - a * d);
        }
    }
    out[0] = x;
    out[1] = y;
}
"""

#: The units, by the name `lower.py` asks for. See the module docstring for
#: why they are separate rather than one file with everything in it.
UNITS = {"vla": SOURCE, "args": ARGS_SOURCE, "complex": COMPLEX_SOURCE}


@lru_cache(maxsize=None)
def _compiled(name: str) -> tuple[tuple[Function, ...], tuple[Global, ...]]:
    # IMPORTED HERE, not at the top: `lower` imports this module to splice the
    # result in, so a top-level import the other way is a cycle.
    from .lower import Lowerer
    from .parser import parse
    from .preprocess import Preprocessor
    sink = DiagnosticSink()
    source = SourceFile(UNITS[name], f"<uasm C support: {name}>")
    tokens = Preprocessor(sink).run(source)
    # A PREFIX OF ITS OWN, so that a `static` helper in here cannot collide
    # with one in the program this is spliced into. The bundled headers it
    # includes keep the shared `c.` prefix and merge with the program's copy
    # of them, which is the whole point of that rule -- see `parser._merge`.
    unit, parser = parse(tokens, sink, "cs.")
    if sink.failed:
        raise AssertionError(
            f"the C frontend's own support code ({name}) does not compile: "
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


def compile_support(sink: DiagnosticSink, units: tuple[str, ...] = ("vla",)
                    ) -> tuple[list[Function], list[Global]]:
    """The support functions and their globals, compiled once per process."""
    functions: list[Function] = []
    globals_: list[Global] = []
    for name in units:
        fns, gs = _compiled(name)
        functions.extend(_clone_function(f) for f in fns)
        globals_.extend(
            Global(g.name, g.size, g.data, g.readonly, g.linkage, g.align,
                   g.span)
            for g in gs)
    return functions, globals_


def _clone_function(fn: Function) -> Function:
    """A fresh copy, because a Module owns its functions and two compilations
    in one process must not share the blocks of one."""
    from ...ir.module import Block
    out = Function(fn.name, fn.ret, list(fn.params), dict(fn.registers), [],
                   fn.linkage, fn.external, fn.span, fn._next_register)
    for blk in fn.blocks:
        out.blocks.append(Block(blk.label, [i.clone() for i in blk.instructions]))
    return out
