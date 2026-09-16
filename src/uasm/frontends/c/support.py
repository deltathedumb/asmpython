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

`long double`, WHICH IS AN ARITHMETIC TYPE WRITTEN IN SOFTWARE. The UIR has
`f32` and `f64` and nothing wider, and should not have a third width that
only one machine has -- so 80-bit extended is built out of 64-bit integers,
here, and every backend runs the same code. It is the widest unit and the one
with the most to check: `ldouble` is validated against x87 hardware by the
differential suite.

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

/* THE SAME TWO AGAIN AT THE WIDEST TYPE. `long double` arithmetic is calls
   into the `ldouble` unit, so this reads as ordinary C and compiles into
   them -- and `isnan`, `fabsl` and the rest are the ones `<math.h>` picks
   for this width, which read the 80-bit encoding rather than converting to
   double and losing the range. */
void __c_cmull(long double a, long double b, long double c, long double d,
               long double *out)
{
    long double ac = a * c, bd = b * d, ad = a * d, bc = b * c;
    long double x = ac - bd, y = ad + bc;
    long double inf = (long double)INFINITY;
    if (isnan(x) && isnan(y)) {
        int recalc = 0;
        if (isinf(a) || isinf(b)) {
            a = copysignl(isinf(a) ? 1.0L : 0.0L, a);
            b = copysignl(isinf(b) ? 1.0L : 0.0L, b);
            if (isnan(c)) c = copysignl(0.0L, c);
            if (isnan(d)) d = copysignl(0.0L, d);
            recalc = 1;
        }
        if (isinf(c) || isinf(d)) {
            c = copysignl(isinf(c) ? 1.0L : 0.0L, c);
            d = copysignl(isinf(d) ? 1.0L : 0.0L, d);
            if (isnan(a)) a = copysignl(0.0L, a);
            if (isnan(b)) b = copysignl(0.0L, b);
            recalc = 1;
        }
        if (!recalc && (isinf(ac) || isinf(bd) || isinf(ad) || isinf(bc))) {
            if (isnan(a)) a = copysignl(0.0L, a);
            if (isnan(b)) b = copysignl(0.0L, b);
            if (isnan(c)) c = copysignl(0.0L, c);
            if (isnan(d)) d = copysignl(0.0L, d);
            recalc = 1;
        }
        if (recalc) {
            x = inf * (a * c - b * d);
            y = inf * (a * d + b * c);
        }
    }
    out[0] = x;
    out[1] = y;
}

void __c_cdivl(long double a, long double b, long double c, long double d,
               long double *out)
{
    long double denom, x, y, inf = (long double)INFINITY;
    long double big = fabsl(c) > fabsl(d) ? fabsl(c) : fabsl(d);
    int ilogbw = 0;
    if (big != 0.0L && !isinf(big) && !isnan(big)) {
        ilogbw = __c_ilogb2((double)big);
        /* THE SCALE MUST NOT GO THROUGH DOUBLE, because a value this type
           can hold may be far outside double's range -- which is the whole
           reason the exponent is taken from `big` rather than from `c`. */
        c = scalbnl(c, -ilogbw);
        d = scalbnl(d, -ilogbw);
    }
    denom = c * c + d * d;
    x = scalbnl((a * c + b * d) / denom, -ilogbw);
    y = scalbnl((b * c - a * d) / denom, -ilogbw);
    if (isnan(x) && isnan(y)) {
        if (denom == 0.0L && (!isnan(a) || !isnan(b))) {
            x = copysignl(inf, c) * a;
            y = copysignl(inf, c) * b;
        } else if ((isinf(a) || isinf(b)) && !isinf(c) && !isnan(c)
                   && !isinf(d) && !isnan(d)) {
            a = copysignl(isinf(a) ? 1.0L : 0.0L, a);
            b = copysignl(isinf(b) ? 1.0L : 0.0L, b);
            x = inf * (a * c + b * d);
            y = inf * (b * c - a * d);
        } else if (isinf(big) && !isinf(a) && !isnan(a)
                   && !isinf(b) && !isnan(b)) {
            c = copysignl(isinf(c) ? 1.0L : 0.0L, c);
            d = copysignl(isinf(d) ? 1.0L : 0.0L, d);
            x = 0.0L * (a * c + b * d);
            y = 0.0L * (b * c - a * d);
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

LDOUBLE_SOURCE = r"""
/* uasm C frontend: `long double`, which is 80-bit extended and is software.
   See support.py.

   WHY IT IS HERE AND NOT IN THE IR. The UIR has `f32` and `f64` and nothing
   wider, and it should not: a third floating width would have to be
   implemented by every backend -- x86-64 has the hardware, the JVM does not
   have it at all, and a WebAssembly target never will. So the width that
   only one machine has is built out of the two every machine has, in C, and
   every backend runs the same code.

   WHY 80-BIT AND NOT DOUBLE-DOUBLE, which is the other way to get more
   precision out of doubles. This is the format x86-64's ABI calls `long
   double`, so `LDBL_MANT_DIG` is 64 here as it is there, `sizeof` is 16,
   and a program's answers can be compared against a hosted compiler's --
   which is what the differential suite does with every other type and could
   not do with a format nothing else has.

   THE LAYOUT IS x87's: eight bytes of significand with the leading bit
   EXPLICIT, then a sixteen-bit word holding the sign and a 15-bit exponent
   biased by 16383, then six bytes of padding nothing reads.

     exp == 0x7fff, significand == 1<<63     an infinity
     exp == 0x7fff, anything else            a NaN
     exp == 0, significand == 0              a zero
     exp == 0, significand != 0              a subnormal, 2^-16382 scaled
     otherwise                               m * 2^(exp-16383-63), bit 63 set

   ROUNDING IS TO NEAREST, TIES TO EVEN, everywhere, which is the default
   mode every one of these formats is defined against and the one the
   hardware would use. */

typedef unsigned long __ld_u64;
typedef unsigned int __ld_u32;

struct __ld { __ld_u64 __m; unsigned short __se; };

#define __LD_BIAS 16383
#define __LD_TOP  ((__ld_u64)1 << 63)

static int __ld_sign(const struct __ld *__x) { return __x->__se >> 15; }
static int __ld_exp(const struct __ld *__x) { return __x->__se & 0x7fff; }

static void __ld_set(struct __ld *__o, int __sign, int __exp, __ld_u64 __m)
{
    __o->__m = __m;
    __o->__se = (unsigned short)(((__sign & 1) << 15) | (__exp & 0x7fff));
}

static int __ld_is_nan(const struct __ld *__x)
{ return __ld_exp(__x) == 0x7fff && (__x->__m & ~__LD_TOP) != 0; }

static int __ld_is_inf(const struct __ld *__x)
{ return __ld_exp(__x) == 0x7fff && (__x->__m & ~__LD_TOP) == 0; }

static int __ld_is_zero(const struct __ld *__x)
{ return __ld_exp(__x) == 0 && __x->__m == 0; }

static void __ld_nan(struct __ld *__o) { __ld_set(__o, 0, 0x7fff, __LD_TOP | ((__ld_u64)1 << 62)); }
static void __ld_inf(struct __ld *__o, int __sign) { __ld_set(__o, __sign, 0x7fff, __LD_TOP); }
static void __ld_zero(struct __ld *__o, int __sign) { __ld_set(__o, __sign, 0, 0); }

/* THE ONE ROUNDING POINT. `m` is the 64-bit significand and `lo` the bits
   below it as a 64-bit fraction; `exp` is the unbiased-plus-bias exponent
   for `m` read with its point after bit 63. Everything above hands its
   result here so that "nearest, ties to even" is implemented once. */
static void __ld_round(struct __ld *__o, int __sign, int __exp,
                       __ld_u64 __m, __ld_u64 __lo)
{
    if (__m == 0 && __lo == 0) { __ld_zero(__o, __sign); return; }
    /* NORMALISE UP, pulling bits out of the low word. A subtraction can
       cancel almost everything, and this is what makes `1 - (1 - 2^-64)`
       exact rather than zero. */
    while ((__m & __LD_TOP) == 0 && __exp > 1) {
        __m = (__m << 1) | (__lo >> 63);
        __lo <<= 1;
        __exp--;
    }
    if (__exp >= 0x7fff) { __ld_inf(__o, __sign); return; }
    if (__exp <= 0) {
        /* SUBNORMAL: the exponent cannot go below 1, so the significand
           goes down instead and the rounding happens where the value's last
           representable bit is. */
        int __shift = 1 - __exp;
        if (__shift >= 64) {
            if (__shift > 64 || __m == 0) { __ld_zero(__o, __sign); return; }
            __lo = (__m != 0) | (__lo != 0);
            __m = 0;
        } else {
            __ld_u64 __out = __m << (64 - __shift);
            __lo = __out | (__lo != 0);
            __m >>= __shift;
        }
        __exp = 0;
    }
    if (__lo > __LD_TOP || (__lo == __LD_TOP && (__m & 1))) {
        __m++;
        if (__m == 0) {                 /* carried out of the top */
            __m = __LD_TOP;
            __exp++;
            if (__exp >= 0x7fff) { __ld_inf(__o, __sign); return; }
        }
    }
    if (__exp == 0 && (__m & __LD_TOP)) __exp = 1;   /* rounded up to normal */
    /* A SIGNIFICAND WITHOUT ITS LEADING BIT IS A SUBNORMAL, and a subnormal
       has a biased exponent of ZERO. The two encodings scale identically --
       exponent 0 and exponent 1 mean the same power of two -- so this is a
       rewriting rather than a change of value, and without it the result is
       an x87 "pseudo-denormal", which is not a number at all. */
    if ((__m & __LD_TOP) == 0) __exp = 0;
    __ld_set(__o, __sign, __exp, __m);
}

/* THE SIGNIFICAND AND THE EXPONENT AS THE ARITHMETIC WANTS THEM: a
   subnormal is read as its true value with a small exponent rather than as
   a special case in every operation. */
static void __ld_unpack(const struct __ld *__x, int *__sign, int *__exp,
                        __ld_u64 *__m)
{
    *__sign = __ld_sign(__x);
    *__exp = __ld_exp(__x);
    *__m = __x->__m;
    if (*__exp == 0 && *__m != 0) *__exp = 1;       /* subnormal */
}

void __c_ldadd(const void *__ap, const void *__bp, void *__op);

static void __ld_addsub(const struct __ld *__a, const struct __ld *__b,
                        struct __ld *__o, int __negate_b)
{
    int __sa, __sb, __ea, __eb, __sign, __exp, __swap;
    __ld_u64 __ma, __mb, __lo = 0, __sum;
    if (__ld_is_nan(__a) || __ld_is_nan(__b)) { __ld_nan(__o); return; }
    __ld_unpack(__a, &__sa, &__ea, &__ma);
    __ld_unpack(__b, &__sb, &__eb, &__mb);
    __sb ^= __negate_b;
    if (__ld_is_inf(__a) || __ld_is_inf(__b)) {
        if (__ld_is_inf(__a) && __ld_is_inf(__b) && __sa != __sb) {
            __ld_nan(__o);              /* inf - inf */
            return;
        }
        __ld_inf(__o, __ld_is_inf(__a) ? __sa : __sb);
        return;
    }
    if (__ma == 0 && __mb == 0) {
        /* `-0 + -0` IS `-0` and every other zero sum is `+0`, which is what
           the default rounding direction says. */
        __ld_zero(__o, (__sa && __sb) ? 1 : 0);
        return;
    }
    /* THE RAW EXPONENTS, not the unpacked ones: `__ld_unpack` reads a
       subnormal's exponent as 1 so the arithmetic can treat it like any
       other, and writing that back out would encode a pseudo-denormal. */
    if (__mb == 0) { __ld_set(__o, __sa, __ld_exp(__a), __ma); return; }
    if (__ma == 0) { __ld_set(__o, __sb, __ld_exp(__b), __mb); return; }
    /* THE LARGER ONE FIRST, so the shift below is always to the right. */
    __swap = (__ea < __eb) || (__ea == __eb && __ma < __mb);
    if (__swap) {
        int __t = __sa; __sa = __sb; __sb = __t;
        __t = __ea; __ea = __eb; __eb = __t;
        { __ld_u64 __u = __ma; __ma = __mb; __mb = __u; }
    }
    {
        /* THE SMALLER ONE MOVES DOWN, and every bit it loses is kept: `lo`
           is the part below the significand, as a fraction of one unit in
           its last place, and the rounding below reads it. Dropping those
           bits and calling them "nonzero" is what made `x + ulp/2` round up
           when it should have gone to even -- exactly half is a value this
           has to be able to say. */
        int __shift = __ea - __eb;
        if (__shift == 0) {
            __lo = 0;
        } else if (__shift < 64) {
            __lo = __mb << (64 - __shift);
            __mb >>= __shift;
        } else if (__shift < 128) {
            int __k = __shift - 64;
            __ld_u64 __lost = __k == 0 ? 0 : (__mb << (64 - __k));
            __lo = (__mb >> __k) | (__lost != 0);
            __mb = 0;
        } else {
            __lo = (__mb != 0);         /* far below: sticky and nothing else */
            __mb = 0;
        }
    }
    __sign = __sa;
    __exp = __ea;
    if (__sa == __sb) {
        __sum = __ma + __mb;
        if (__sum < __ma) {             /* carried out of bit 63 */
            __lo = (__lo >> 1) | (__sum << 63);
            __sum = (__sum >> 1) | __LD_TOP;
            __exp++;
        }
    } else {
        if (__lo != 0) {
            __sum = __ma - __mb - 1;
            __lo = (__ld_u64)0 - __lo;
        } else {
            __sum = __ma - __mb;
        }
        if (__sum == 0 && __lo == 0) { __ld_zero(__o, 0); return; }
    }
    __ld_round(__o, __sign, __exp, __sum, __lo);
}

void __c_ldadd(const void *__ap, const void *__bp, void *__op)
{ __ld_addsub((const struct __ld *)__ap, (const struct __ld *)__bp,
              (struct __ld *)__op, 0); }

void __c_ldsub(const void *__ap, const void *__bp, void *__op)
{ __ld_addsub((const struct __ld *)__ap, (const struct __ld *)__bp,
              (struct __ld *)__op, 1); }

/* 64 x 64 -> 128, out of four 32-bit products, because that is the widest
   multiply the IR has. */
static void __ld_mul64(__ld_u64 __a, __ld_u64 __b, __ld_u64 *__hi,
                       __ld_u64 *__lo)
{
    __ld_u64 __a0 = __a & 0xffffffffu, __a1 = __a >> 32;
    __ld_u64 __b0 = __b & 0xffffffffu, __b1 = __b >> 32;
    __ld_u64 __p00 = __a0 * __b0, __p01 = __a0 * __b1;
    __ld_u64 __p10 = __a1 * __b0, __p11 = __a1 * __b1;
    __ld_u64 __mid = (__p00 >> 32) + (__p01 & 0xffffffffu)
                   + (__p10 & 0xffffffffu);
    *__lo = (__p00 & 0xffffffffu) | (__mid << 32);
    *__hi = __p11 + (__p01 >> 32) + (__p10 >> 32) + (__mid >> 32);
}

void __c_ldmul(const void *__ap, const void *__bp, void *__op)
{
    const struct __ld *__a = (const struct __ld *)__ap;
    const struct __ld *__b = (const struct __ld *)__bp;
    struct __ld *__o = (struct __ld *)__op;
    int __sa, __sb, __ea, __eb, __sign;
    __ld_u64 __ma, __mb, __hi, __lo;
    if (__ld_is_nan(__a) || __ld_is_nan(__b)) { __ld_nan(__o); return; }
    __ld_unpack(__a, &__sa, &__ea, &__ma);
    __ld_unpack(__b, &__sb, &__eb, &__mb);
    __sign = __sa ^ __sb;
    if (__ld_is_inf(__a) || __ld_is_inf(__b)) {
        if (__ma == 0 || __mb == 0) { __ld_nan(__o); return; }   /* inf * 0 */
        __ld_inf(__o, __sign);
        return;
    }
    if (__ma == 0 || __mb == 0) { __ld_zero(__o, __sign); return; }
    __ld_mul64(__ma, __mb, &__hi, &__lo);
    /* BOTH HAVE BIT 63 SET, so the product has bit 127 or bit 126 set and
       one shift is the most that can be needed. */
    if ((__hi & __LD_TOP) == 0) {
        __hi = (__hi << 1) | (__lo >> 63);
        __lo <<= 1;
        __ea--;
    }
    __ld_round(__o, __sign, __ea + __eb - __LD_BIAS + 1, __hi, __lo);
}

void __c_lddiv(const void *__ap, const void *__bp, void *__op)
{
    const struct __ld *__a = (const struct __ld *)__ap;
    const struct __ld *__b = (const struct __ld *)__bp;
    struct __ld *__o = (struct __ld *)__op;
    int __sa, __sb, __ea, __eb, __sign, __i, __top = 0;
    __ld_u64 __ma, __mb, __q = 0, __r;
    if (__ld_is_nan(__a) || __ld_is_nan(__b)) { __ld_nan(__o); return; }
    __ld_unpack(__a, &__sa, &__ea, &__ma);
    __ld_unpack(__b, &__sb, &__eb, &__mb);
    __sign = __sa ^ __sb;
    if (__ld_is_inf(__a)) {
        if (__ld_is_inf(__b)) { __ld_nan(__o); return; }
        __ld_inf(__o, __sign);
        return;
    }
    if (__ld_is_inf(__b)) { __ld_zero(__o, __sign); return; }
    if (__mb == 0) {
        if (__ma == 0) { __ld_nan(__o); return; }       /* 0 / 0 */
        __ld_inf(__o, __sign);                          /* x / 0 */
        return;
    }
    if (__ma == 0) { __ld_zero(__o, __sign); return; }
    /* A SUBNORMAL HAS NO LEADING BIT, so both are normalised here and the
       exponent is adjusted; the division loop below needs bit 63 set. */
    while ((__ma & __LD_TOP) == 0) { __ma <<= 1; __ea--; }
    while ((__mb & __LD_TOP) == 0) { __mb <<= 1; __eb--; }
    {
        int __exp = __ea - __eb + __LD_BIAS;
        __ld_u64 __lo;
        __r = __ma;
        if (__ma < __mb) {
            /* THE QUOTIENT IS BELOW ONE, so the numerator is doubled once
               here and the exponent pays for it -- which keeps all 64 bits
               rather than shifting one away at the end. */
            __exp--;
            __top = (int)(__r >> 63);
            __r <<= 1;
        }
        /* THE FIRST BIT IS ALWAYS A ONE, by the line above: the numerator
           is now at least the divisor and less than twice it. */
        __q = 1;
        __r -= __mb;
        __top = 0;
        /* RESTORING DIVISION for the other 63. The remainder afterwards is
           what decides the rounding, and it is a FRACTION OF THE DIVISOR --
           `2r` against `mb` is the whole test, written as `r` against
           `mb - r` so that nothing overflows. */
        for (__i = 0; __i < 63; __i++) {
            __q <<= 1;
            __top = (int)(__r >> 63);
            __r <<= 1;
            if (__top || __r >= __mb) { __r -= __mb; __q |= 1; __top = 0; }
        }
        if (__r == 0) __lo = 0;
        else if (__r > __mb - __r) __lo = __LD_TOP + 1;
        else if (__r == __mb - __r) __lo = __LD_TOP;
        else __lo = 1;
        __ld_round(__o, __sign, __exp, __q, __lo);
    }
}

void __c_ldneg(const void *__ap, void *__op)
{
    const struct __ld *__a = (const struct __ld *)__ap;
    struct __ld *__o = (struct __ld *)__op;
    __ld_set(__o, !__ld_sign(__a), __ld_exp(__a), __a->__m);
}

/* -1, 0, 1 as usual, and 2 for "unordered" -- which is not a value the
   ordering can take, so a caller can tell a NaN from a less-than. */
int __c_ldcmp(const void *__ap, const void *__bp)
{
    const struct __ld *__a = (const struct __ld *)__ap;
    const struct __ld *__b = (const struct __ld *)__bp;
    int __sa, __sb;
    if (__ld_is_nan(__a) || __ld_is_nan(__b)) return 2;
    if (__ld_is_zero(__a) && __ld_is_zero(__b)) return 0;   /* -0 == +0 */
    __sa = __ld_sign(__a);
    __sb = __ld_sign(__b);
    if (__sa != __sb) return __sa ? -1 : 1;
    if (__ld_exp(__a) != __ld_exp(__b))
        return (__ld_exp(__a) < __ld_exp(__b)) == (__sa == 0) ? -1 : 1;
    if (__a->__m != __b->__m)
        return (__a->__m < __b->__m) == (__sa == 0) ? -1 : 1;
    return 0;
}

/* ── the conversions ──────────────────────────────────────────────────── */
void __c_ldfromd(double __v, void *__op)
{
    struct __ld *__o = (struct __ld *)__op;
    union { double __d; __ld_u64 __u; } __x;
    int __sign, __exp;
    __ld_u64 __frac;
    __x.__d = __v;
    __sign = (int)(__x.__u >> 63);
    __exp = (int)((__x.__u >> 52) & 0x7ff);
    __frac = __x.__u & (((__ld_u64)1 << 52) - 1);
    if (__exp == 0x7ff) {
        if (__frac) __ld_nan(__o);
        else __ld_inf(__o, __sign);
        return;
    }
    if (__exp == 0) {
        if (__frac == 0) { __ld_zero(__o, __sign); return; }
        /* A DOUBLE'S SUBNORMAL IS A NORMAL HERE: the exponent range is far
           wider, so there is nothing to lose and normalising is exact. */
        __exp = 1;
        while ((__frac & ((__ld_u64)1 << 52)) == 0) { __frac <<= 1; __exp--; }
        __frac &= ((__ld_u64)1 << 52) - 1;
    }
    __ld_set(__o, __sign, __exp - 1023 + __LD_BIAS,
             __LD_TOP | (__frac << 11));
}

double __c_ldtod(const void *__ap)
{
    const struct __ld *__a = (const struct __ld *)__ap;
    union { double __d; __ld_u64 __u; } __x;
    int __sign = __ld_sign(__a), __exp = __ld_exp(__a);
    __ld_u64 __m = __a->__m, __frac, __lo;
    if (__ld_is_nan(__a)) {
        __x.__u = ((__ld_u64)0x7ff8 << 48);
        return __x.__d;
    }
    if (__ld_is_inf(__a) || __exp - __LD_BIAS + 1023 >= 0x7ff) {
        if (__ld_is_inf(__a) || __exp != 0) {
            __x.__u = ((__ld_u64)__sign << 63) | ((__ld_u64)0x7ff << 52);
            return __x.__d;
        }
    }
    if (__exp == 0 && __m == 0) {
        __x.__u = (__ld_u64)__sign << 63;
        return __x.__d;
    }
    if (__exp == 0) __exp = 1;
    __exp = __exp - __LD_BIAS + 1023;
    /* ELEVEN BITS GO, and they decide the rounding: the round bit is the
       highest of them and the rest are sticky. */
    if (__exp <= 0) {
        int __shift = 1 - __exp;
        if (__shift > 63) {
            __x.__u = (__ld_u64)__sign << 63;
            return __x.__d;
        }
        __lo = (__m << (64 - __shift)) | 0;
        __m = (__m >> __shift) | (__lo ? 1 : 0);
        __exp = 0;
    }
    __frac = __m >> 11;
    __lo = __m & 0x7ff;
    if (__lo > 0x400 || (__lo == 0x400 && (__frac & 1))) {
        __frac++;
        if (__frac >> 53) { __frac >>= 1; __exp++; }
    }
    if (__exp >= 0x7ff) {
        __x.__u = ((__ld_u64)__sign << 63) | ((__ld_u64)0x7ff << 52);
        return __x.__d;
    }
    if (__exp <= 0) {
        __x.__u = ((__ld_u64)__sign << 63) | (__frac & (((__ld_u64)1 << 52) - 1));
        return __x.__d;
    }
    __x.__u = ((__ld_u64)__sign << 63) | ((__ld_u64)__exp << 52)
            | (__frac & (((__ld_u64)1 << 52) - 1));
    return __x.__d;
}

void __c_ldfromi(long __v, void *__op)
{
    struct __ld *__o = (struct __ld *)__op;
    int __sign = 0;
    __ld_u64 __m;
    if (__v == 0) { __ld_zero(__o, 0); return; }
    if (__v < 0) { __sign = 1; __m = (__ld_u64)0 - (__ld_u64)__v; }
    else __m = (__ld_u64)__v;
    {
        int __exp = __LD_BIAS + 63;
        while ((__m & __LD_TOP) == 0) { __m <<= 1; __exp--; }
        __ld_set(__o, __sign, __exp, __m);
    }
}

void __c_ldfromu(unsigned long __v, void *__op)
{
    struct __ld *__o = (struct __ld *)__op;
    __ld_u64 __m = __v;
    int __exp = __LD_BIAS + 63;
    if (__v == 0) { __ld_zero(__o, 0); return; }
    while ((__m & __LD_TOP) == 0) { __m <<= 1; __exp--; }
    __ld_set(__o, 0, __exp, __m);
}

/* TRUNCATING TOWARD ZERO, which is what C says a conversion to an integer
   does -- and out of range is undefined, so the whole value is answered
   rather than a saturated one. */
long __c_ldtoi(const void *__ap)
{
    const struct __ld *__a = (const struct __ld *)__ap;
    int __sign, __exp;
    __ld_u64 __m;
    __ld_unpack(__a, &__sign, &__exp, &__m);
    if (__ld_is_nan(__a) || __m == 0) return 0;
    __exp -= __LD_BIAS;
    if (__exp < 0) return 0;
    if (__exp > 63) return __sign ? (-9223372036854775807L - 1) : 9223372036854775807L;
    __m >>= (63 - __exp);
    return __sign ? -(long)__m : (long)__m;
}

unsigned long __c_ldtou(const void *__ap)
{
    const struct __ld *__a = (const struct __ld *)__ap;
    int __sign, __exp;
    __ld_u64 __m;
    __ld_unpack(__a, &__sign, &__exp, &__m);
    if (__ld_is_nan(__a) || __m == 0) return 0;
    __exp -= __LD_BIAS;
    if (__exp < 0) return 0;
    if (__exp > 63) return (unsigned long)-1;
    __m >>= (63 - __exp);
    return __sign ? (__ld_u64)0 - __m : __m;
}
"""

TLS_SOURCE = r"""
/* uasm C frontend: `_Thread_local` storage. See support.py.

   ONE COPY PER THREAD, MADE ON FIRST USE. The key is created before `main`
   runs -- by the unit's initialiser, where the program is still one thread
   -- so there is no race to make one and this only has to answer "has this
   thread got a copy yet".

   THE TEMPLATE IS THE OBJECT ITSELF as lowering emitted it: a
   `_Thread_local int x = 5;` is a global holding 5, and every thread's copy
   starts as a copy of it. That is what C says a thread-local object's
   initial value is, and it costs nothing to have.

   NOTHING IS EVER FREED, because the destructor a key can carry runs in the
   HOST's thread cleanup and would have to call back into the program.
   `plat_heap`'s memory is not returned anyway -- its contract says so. */
extern void *plat_heap(long);
extern long host_tss_get(long);
extern long host_tss_set(long, void *);

void *__c_tls_get(void *keyp, long size, void *template_)
{
    long key = *(long *)keyp;
    char *p;
    long i;
    /* NO KEY MEANS NO THREADS: the host refused to make one, so there is
       one copy and it is the template. A program that never starts a thread
       cannot tell the difference. */
    if (key <= 0) return template_;
    p = (char *)host_tss_get(key);
    if (p == 0) {
        p = (char *)plat_heap(size);
        if (p == 0) return template_;
        for (i = 0; i < size; i++) p[i] = ((char *)template_)[i];
        host_tss_set(key, p);
    }
    return (void *)p;
}
"""

#: The units, by the name `lower.py` asks for. See the module docstring for
#: why they are separate rather than one file with everything in it.
UNITS = {"vla": SOURCE, "args": ARGS_SOURCE, "complex": COMPLEX_SOURCE,
         "ldouble": LDOUBLE_SOURCE, "tls": TLS_SOURCE}


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
