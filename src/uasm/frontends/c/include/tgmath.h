/* <tgmath.h> -- one name per operation, whichever type it is given.

   Each macro is a `_Generic` that picks the function matching its argument:
   the `f` one for a `float`, the `c` one for a complex, `cf` for a float
   complex, and the plain `double` one for everything else. The `double` case
   is written `(sqrt)(x)` rather than `sqrt(x)`: a function-like macro name is
   only invoked when a `(` follows it, so the parentheses name the FUNCTION
   `<math.h>` declared instead of re-entering this macro. (The hide set would
   stop the recursion anyway -- see `preprocess.py` -- but a reader should not
   have to know that to read this file.)

   `long double` IS `double` here, so the `l` cases would be the same
   function as the default and are left out; `long double _Complex` is a
   DISTINCT type to the type system even so, which is why it has a case of
   its own in every macro below.

   BOTH HEADERS COME WITH IT, as C requires: `<tgmath.h>` includes
   `<math.h>` and `<complex.h>`, so `I` and `creal` are in scope too. */
#ifndef _UASM_TGMATH_H
#define _UASM_TGMATH_H

#include <math.h>
#include <complex.h>

/* ── real only ────────────────────────────────────────────────────────── */
#define __tg1(f, ff, x) _Generic((x), float: ff, default: f)(x)
#define __tg2(f, ff, x, y) _Generic((x) + (y), float: ff, default: f)(x, y)

/* ── real or complex, answering the same kind ─────────────────────────── */
#define __tgc1(f, ff, cf, cff, x) _Generic((x), \
    float: ff, \
    float _Complex: cff, \
    double _Complex: cf, \
    long double _Complex: cf, \
    default: f)(x)

#define __tgc2(f, ff, cf, cff, x, y) _Generic((x) + (y), \
    float: ff, \
    float _Complex: cff, \
    double _Complex: cf, \
    long double _Complex: cf, \
    default: f)(x, y)

/* ── complex only: the answer is real ─────────────────────────────────── */
#define __tgcr1(cf, cff, x) _Generic((x), \
    float _Complex: cff, \
    float: cff, \
    long double _Complex: cf, \
    default: cf)(x)

#define sqrt(x)  __tgc1((sqrt), sqrtf, csqrt, csqrtf, x)
#define sin(x)   __tgc1((sin), sinf, csin, csinf, x)
#define cos(x)   __tgc1((cos), cosf, ccos, ccosf, x)
#define tan(x)   __tgc1((tan), tanf, ctan, ctanf, x)
#define exp(x)   __tgc1((exp), expf, cexp, cexpf, x)
#define log(x)   __tgc1((log), logf, clog, clogf, x)
#define asin(x)  __tgc1((asin), asinf, casin, casinf, x)
#define acos(x)  __tgc1((acos), acosf, cacos, cacosf, x)
#define atan(x)  __tgc1((atan), atanf, catan, catanf, x)
#define sinh(x)  __tgc1((sinh), sinhf, csinh, csinhf, x)
#define cosh(x)  __tgc1((cosh), coshf, ccosh, ccoshf, x)
#define tanh(x)  __tgc1((tanh), tanhf, ctanh, ctanhf, x)
#define asinh(x) __tgc1((asinh), asinhf, casinh, casinhf, x)
#define acosh(x) __tgc1((acosh), acoshf, cacosh, cacoshf, x)
#define atanh(x) __tgc1((atanh), atanhf, catanh, catanhf, x)
#define pow(x, y) __tgc2((pow), powf, cpow, cpowf, x, y)

/* `fabs` OF A COMPLEX IS `cabs`, which answers a REAL -- the one place a
   type-generic macro changes what kind of value comes back. */
#define fabs(x) _Generic((x), \
    float: fabsf, \
    float _Complex: cabsf, \
    double _Complex: cabs, \
    long double _Complex: cabs, \
    default: (fabs))(x)

#define log10(x) __tg1((log10), log10f, x)
#define log2(x)  __tg1((log2), log2f, x)
#define floor(x) __tg1((floor), floorf, x)
#define ceil(x)  __tg1((ceil), ceilf, x)
#define trunc(x) __tg1((trunc), truncf, x)
#define round(x) __tg1((round), roundf, x)
#define cbrt(x)  __tg1((cbrt), cbrtf, x)
#define expm1(x) __tg1((expm1), expm1f, x)
#define log1p(x) __tg1((log1p), log1pf, x)
#define fmod(x, y)  __tg2((fmod), fmodf, x, y)
#define atan2(y, x) __tg2((atan2), atan2f, y, x)
#define hypot(x, y) __tg2((hypot), hypotf, x, y)
#define fmin(x, y)  __tg2((fmin), fminf, x, y)
#define fmax(x, y)  __tg2((fmax), fmaxf, x, y)
#define copysign(x, y) __tg2((copysign), copysignf, x, y)

/* THE COMPLEX-ONLY FIVE, which C lists separately because a real argument
   is converted to complex rather than the macro picking a real function. */
#define carg(x)  __tgcr1((carg), cargf, x)
#define cimag(x) __tgcr1((cimag), cimagf, x)
#define creal(x) __tgcr1((creal), crealf, x)
#define conj(x) _Generic((x), \
    float _Complex: conjf, float: conjf, \
    long double _Complex: (conj), default: (conj))(x)
#define cproj(x) _Generic((x), \
    float _Complex: cprojf, float: cprojf, \
    long double _Complex: (cproj), default: (cproj))(x)

#endif
