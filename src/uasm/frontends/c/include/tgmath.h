/* <tgmath.h> -- one name per operation, whichever type it is given.

   Each macro is a `_Generic` that picks the function matching its argument:
   the `f` one for a `float`, the `l` one for a `long double`, the `c` one
   for a complex, `cf` and `cl` for the narrower and wider complexes, and the
   plain `double` one for everything else. The `double` case is written
   `(sqrt)(x)` rather than `sqrt(x)`: a function-like macro name is only
   invoked when a `(` follows it, so the parentheses name the FUNCTION
   `<math.h>` declared instead of re-entering this macro. (The hide set would
   stop the recursion anyway -- see `preprocess.py` -- but a reader should not
   have to know that to read this file.)

   THE TYPE IS PICKED FROM THE FLOATING ARGUMENTS AND NO OTHERS, which is
   why there are four shapes rather than one: `ldexp(x, n)` and `frexp(x, e)`
   decide on `x` alone because the other argument is an `int` either way,
   `remquo(x, y, q)` on `x` and `y`, and `nexttoward(x, y)` on `x` because C
   says its second argument is a `long double` in every one of the three.

   BOTH HEADERS COME WITH IT, as C requires: `<tgmath.h>` includes
   `<math.h>` and `<complex.h>`, so `I` and `creal` are in scope too. */
#ifndef _UASM_TGMATH_H
#define _UASM_TGMATH_H

#include <math.h>
#include <complex.h>

/* ── real only ────────────────────────────────────────────────────────── */
#define __tg1(f, ff, fl, x) \
    _Generic((x), float: ff, long double: fl, default: f)(x)
#define __tg2(f, ff, fl, x, y) \
    _Generic((x) + (y), float: ff, long double: fl, default: f)(x, y)
#define __tg3(f, ff, fl, x, y, z) \
    _Generic((x) + (y) + (z), float: ff, long double: fl, default: f)(x, y, z)

/* ── real, with an argument that is not floating and does not count ───── */
#define __tg1i(f, ff, fl, x, a) \
    _Generic((x), float: ff, long double: fl, default: f)(x, a)
#define __tg2i(f, ff, fl, x, y, a) \
    _Generic((x) + (y), float: ff, long double: fl, default: f)(x, y, a)

/* ── real or complex, answering the same kind ─────────────────────────── */
#define __tgc1(f, ff, fl, cf, cff, cfl, x) _Generic((x), \
    float: ff, \
    long double: fl, \
    float _Complex: cff, \
    double _Complex: cf, \
    long double _Complex: cfl, \
    default: f)(x)

#define __tgc2(f, ff, fl, cf, cff, cfl, x, y) _Generic((x) + (y), \
    float: ff, \
    long double: fl, \
    float _Complex: cff, \
    double _Complex: cf, \
    long double _Complex: cfl, \
    default: f)(x, y)

/* ── complex only: a real argument is CONVERTED rather than dispatched ── */
#define __tgcc1(cf, cff, cfl, x) _Generic((x), \
    float: cff, \
    float _Complex: cff, \
    long double: cfl, \
    long double _Complex: cfl, \
    default: cf)(x)

/* ── the seventeen with a complex counterpart ─────────────────────────── */
#define sqrt(x)  __tgc1((sqrt), sqrtf, sqrtl, csqrt, csqrtf, csqrtl, x)
#define sin(x)   __tgc1((sin), sinf, sinl, csin, csinf, csinl, x)
#define cos(x)   __tgc1((cos), cosf, cosl, ccos, ccosf, ccosl, x)
#define tan(x)   __tgc1((tan), tanf, tanl, ctan, ctanf, ctanl, x)
#define exp(x)   __tgc1((exp), expf, expl, cexp, cexpf, cexpl, x)
#define log(x)   __tgc1((log), logf, logl, clog, clogf, clogl, x)
#define asin(x)  __tgc1((asin), asinf, asinl, casin, casinf, casinl, x)
#define acos(x)  __tgc1((acos), acosf, acosl, cacos, cacosf, cacosl, x)
#define atan(x)  __tgc1((atan), atanf, atanl, catan, catanf, catanl, x)
#define sinh(x)  __tgc1((sinh), sinhf, sinhl, csinh, csinhf, csinhl, x)
#define cosh(x)  __tgc1((cosh), coshf, coshl, ccosh, ccoshf, ccoshl, x)
#define tanh(x)  __tgc1((tanh), tanhf, tanhl, ctanh, ctanhf, ctanhl, x)
#define asinh(x) __tgc1((asinh), asinhf, asinhl, casinh, casinhf, casinhl, x)
#define acosh(x) __tgc1((acosh), acoshf, acoshl, cacosh, cacoshf, cacoshl, x)
#define atanh(x) __tgc1((atanh), atanhf, atanhl, catanh, catanhf, catanhl, x)
#define pow(x, y) __tgc2((pow), powf, powl, cpow, cpowf, cpowl, x, y)

/* `fabs` OF A COMPLEX IS `cabs`, which answers a REAL -- the one place a
   type-generic macro changes what kind of value comes back. */
#define fabs(x) _Generic((x), \
    float: fabsf, \
    long double: fabsl, \
    float _Complex: cabsf, \
    double _Complex: cabs, \
    long double _Complex: cabsl, \
    default: (fabs))(x)

/* ── the real-only ones ───────────────────────────────────────────────── */
#define exp2(x)      __tg1((exp2), exp2f, exp2l, x)
#define expm1(x)     __tg1((expm1), expm1f, expm1l, x)
#define log10(x)     __tg1((log10), log10f, log10l, x)
#define log1p(x)     __tg1((log1p), log1pf, log1pl, x)
#define log2(x)      __tg1((log2), log2f, log2l, x)
#define logb(x)      __tg1((logb), logbf, logbl, x)
#define cbrt(x)      __tg1((cbrt), cbrtf, cbrtl, x)
#define floor(x)     __tg1((floor), floorf, floorl, x)
#define ceil(x)      __tg1((ceil), ceilf, ceill, x)
#define trunc(x)     __tg1((trunc), truncf, truncl, x)
#define round(x)     __tg1((round), roundf, roundl, x)
#define rint(x)      __tg1((rint), rintf, rintl, x)
#define nearbyint(x) __tg1((nearbyint), nearbyintf, nearbyintl, x)
#define erf(x)       __tg1((erf), erff, erfl, x)
#define erfc(x)      __tg1((erfc), erfcf, erfcl, x)
#define lgamma(x)    __tg1((lgamma), lgammaf, lgammal, x)
#define tgamma(x)    __tg1((tgamma), tgammaf, tgammal, x)
#define ilogb(x)     __tg1((ilogb), ilogbf, ilogbl, x)
#define lrint(x)     __tg1((lrint), lrintf, lrintl, x)
#define llrint(x)    __tg1((llrint), llrintf, llrintl, x)
#define lround(x)    __tg1((lround), lroundf, lroundl, x)
#define llround(x)   __tg1((llround), llroundf, llroundl, x)

#define atan2(y, x)    __tg2((atan2), atan2f, atan2l, y, x)
#define copysign(x, y) __tg2((copysign), copysignf, copysignl, x, y)
#define fdim(x, y)     __tg2((fdim), fdimf, fdiml, x, y)
#define fmax(x, y)     __tg2((fmax), fmaxf, fmaxl, x, y)
#define fmin(x, y)     __tg2((fmin), fminf, fminl, x, y)
#define fmod(x, y)     __tg2((fmod), fmodf, fmodl, x, y)
#define hypot(x, y)    __tg2((hypot), hypotf, hypotl, x, y)
#define nextafter(x, y) __tg2((nextafter), nextafterf, nextafterl, x, y)
#define remainder(x, y) __tg2((remainder), remainderf, remainderl, x, y)

#define fma(x, y, z) __tg3((fma), fmaf, fmal, x, y, z)

#define frexp(x, e)    __tg1i((frexp), frexpf, frexpl, x, e)
#define ldexp(x, n)    __tg1i((ldexp), ldexpf, ldexpl, x, n)
#define scalbn(x, n)   __tg1i((scalbn), scalbnf, scalbnl, x, n)
#define scalbln(x, n)  __tg1i((scalbln), scalblnf, scalblnl, x, n)
#define remquo(x, y, q) __tg2i((remquo), remquof, remquol, x, y, q)

/* `nexttoward` DECIDES ON ITS FIRST ARGUMENT ALONE: the second is a
   `long double` in all three functions, so adding it would make every call
   pick `nexttowardl`. */
#define nexttoward(x, y) \
    __tg1i((nexttoward), nexttowardf, nexttowardl, x, y)

/* ── the complex-only five ────────────────────────────────────────────── */
#define carg(x)  __tgcc1((carg), cargf, cargl, x)
#define cimag(x) __tgcc1((cimag), cimagf, cimagl, x)
#define creal(x) __tgcc1((creal), crealf, creall, x)
#define conj(x)  __tgcc1((conj), conjf, conjl, x)
#define cproj(x) __tgcc1((cproj), cprojf, cprojl, x)

#endif
