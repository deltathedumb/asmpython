/* <complex.h>.

   A COMPLEX VALUE IS TWO FLOATS SIDE BY SIDE, real part first, which is what
   C says it is (6.2.5p13: "an array of two elements") and what every ABI
   does. The UIR has no complex type and needs none: a value in memory with
   an address is what an aggregate already is here, so passing, returning,
   copying and storing one is the machinery structs use, and the arithmetic
   is in `lower.py` (`+` and `-`) and `support.py` (`*` and `/`).

   `*` AND `/` ARE ANNEX G's ALGORITHMS, not the four-multiply formula. The
   formula is the easy part; what an infinity times a zero must produce is
   not. Without the recovery step `(inf + 0i) * (2 + 3i)` is `nan + nan i`
   rather than `inf + inf i`, and a program cannot tell an overflow from a
   mistake. libgcc's `__muldc3` and `__divdc3` do the same thing, so the
   answers agree with a hosted compiler's rather than nearly.

   IMAGINARY TYPES ARE NOT HERE, and that is conforming rather than missing:
   `_Imaginary` is Annex G, which an implementation supports only if it
   defines `__STDC_IEC_559_COMPLEX__`. gcc has never had them either. So
   `imaginary` and `_Imaginary_I` are not defined, which is exactly how the
   standard says an implementation without them says so.

   THE ELEMENTARY FUNCTIONS ARE THE PRINCIPAL-VALUE FORMULAS, written over
   `<math.h>`'s real ones. They are accurate to a few ulp and they are not
   the branch-cut-perfect implementations Annex G describes for every
   infinity and zero: `csqrt(-4)` is `2i` and `clog(-1)` is `pi i`, and a
   program that depends on which side of a cut a signed zero lands on should
   not depend on this one. Said here rather than discovered later. */
#ifndef _UASM_COMPLEX_H
#define _UASM_COMPLEX_H
#define __STDC_VERSION_COMPLEX_H__ 202311L

#include <math.h>

#define complex _Complex

/* THE IMAGINARY UNIT. `1.0iF` is gcc's imaginary constant suffix and this
   frontend accepts it for the same reason gcc's own `<complex.h>` uses it:
   there is no other way to write `i` that does not go through arithmetic
   with an infinity in it. It is `float _Complex`, as C requires, so that
   `2.0 * I` is `double _Complex` and `2.0f * I` is `float _Complex`. */
#define _Complex_I (1.0iF)
#define I _Complex_I

/* CMPLX BUILDS A VALUE FROM ITS TWO HALVES EXACTLY, which `x + y*I` does
   not: if `y` is an infinity, `y*I` is `nan + inf i` and the sum is wrong.
   C11 added these three for that reason. The union is how it is done here --
   the layout IS two elements, so reading them as an array is not a trick. */
static double _Complex __c_cmplx(double __re, double __im)
{
    union { double __p[2]; double _Complex __z; } __u;
    __u.__p[0] = __re;
    __u.__p[1] = __im;
    return __u.__z;
}
static float _Complex __c_cmplxf(float __re, float __im)
{
    union { float __p[2]; float _Complex __z; } __u;
    __u.__p[0] = __re;
    __u.__p[1] = __im;
    return __u.__z;
}
static long double _Complex __c_cmplxl(long double __re, long double __im)
{
    union { long double __p[2]; long double _Complex __z; } __u;
    __u.__p[0] = __re;
    __u.__p[1] = __im;
    return __u.__z;
}

#define CMPLX(x, y) __c_cmplx((double)(x), (double)(y))
#define CMPLXF(x, y) __c_cmplxf((float)(x), (float)(y))
#define CMPLXL(x, y) __c_cmplxl((long double)(x), (long double)(y))

/* ── the two halves ───────────────────────────────────────────────────── */
static double creal(double _Complex __z) { return __real__ __z; }
static double cimag(double _Complex __z) { return __imag__ __z; }
static float crealf(float _Complex __z) { return __real__ __z; }
static float cimagf(float _Complex __z) { return __imag__ __z; }
static long double creall(long double _Complex __z) { return __real__ __z; }
static long double cimagl(long double _Complex __z) { return __imag__ __z; }

static double _Complex conj(double _Complex __z)
{ return CMPLX(creal(__z), -cimag(__z)); }
static float _Complex conjf(float _Complex __z)
{ return CMPLXF(crealf(__z), -cimagf(__z)); }
static long double _Complex conjl(long double _Complex __z)
{ return CMPLXL(creall(__z), -cimagl(__z)); }

static double cabs(double _Complex __z) { return hypot(creal(__z), cimag(__z)); }
static float cabsf(float _Complex __z)
{ return (float)hypot((double)crealf(__z), (double)cimagf(__z)); }
static long double cabsl(long double _Complex __z)
{ return hypotl(creall(__z), cimagl(__z)); }

static double carg(double _Complex __z) { return atan2(cimag(__z), creal(__z)); }
static float cargf(float _Complex __z)
{ return (float)atan2((double)cimagf(__z), (double)crealf(__z)); }
static long double cargl(long double _Complex __z)
{ return atan2l(cimagl(__z), creall(__z)); }

/* THE PROJECTION ONTO THE RIEMANN SPHERE: every infinity is the same point,
   and C says which one -- `INFINITY + 0i` with the sign of the imaginary
   part kept, so that `cproj` distinguishes nothing but the sign of zero. */
static double _Complex cproj(double _Complex __z)
{
    if (isinf(creal(__z)) || isinf(cimag(__z)))
        return CMPLX(INFINITY, copysign(0.0, cimag(__z)));
    return __z;
}
static float _Complex cprojf(float _Complex __z)
{
    if (isinf(crealf(__z)) || isinf(cimagf(__z)))
        return CMPLXF((float)INFINITY, copysignf(0.0f, cimagf(__z)));
    return __z;
}
static long double _Complex cprojl(long double _Complex __z)
{
    if (isinf(creall(__z)) || isinf(cimagl(__z)))
        return CMPLXL((long double)INFINITY,
                      copysignl(0.0L, cimagl(__z)));
    return __z;
}

/* ── the elementary functions ─────────────────────────────────────────── */
static double _Complex cexp(double _Complex __z)
{
    double __r = exp(creal(__z));
    return CMPLX(__r * cos(cimag(__z)), __r * sin(cimag(__z)));
}

static double _Complex clog(double _Complex __z)
{ return CMPLX(log(cabs(__z)), carg(__z)); }

/* THE STABLE SQUARE ROOT. `sqrt((|x| + |z|)/2)` never subtracts two nearly
   equal numbers, which the obvious `sqrt((x + |z|)/2)` does when x is
   negative -- and the halves are then swapped rather than recomputed. */
static double _Complex csqrt(double _Complex __z)
{
    double __x = creal(__z), __y = cimag(__z), __t;
    if (__x == 0.0 && __y == 0.0) return CMPLX(0.0, __y);
    __t = sqrt((fabs(__x) + cabs(__z)) * 0.5);
    if (__x >= 0.0) return CMPLX(__t, __y / (2.0 * __t));
    return CMPLX(fabs(__y) / (2.0 * __t), copysign(__t, __y));
}

static double _Complex cpow(double _Complex __z, double _Complex __w)
{
    if (creal(__z) == 0.0 && cimag(__z) == 0.0)
        return (creal(__w) == 0.0 && cimag(__w) == 0.0) ? CMPLX(1.0, 0.0)
                                                        : CMPLX(0.0, 0.0);
    return cexp(__w * clog(__z));
}

static double _Complex csin(double _Complex __z)
{
    return CMPLX(sin(creal(__z)) * cosh(cimag(__z)),
                 cos(creal(__z)) * sinh(cimag(__z)));
}
static double _Complex ccos(double _Complex __z)
{
    return CMPLX(cos(creal(__z)) * cosh(cimag(__z)),
                 -sin(creal(__z)) * sinh(cimag(__z)));
}
static double _Complex ctan(double _Complex __z)
{ return csin(__z) / ccos(__z); }

static double _Complex csinh(double _Complex __z)
{
    return CMPLX(sinh(creal(__z)) * cos(cimag(__z)),
                 cosh(creal(__z)) * sin(cimag(__z)));
}
static double _Complex ccosh(double _Complex __z)
{
    return CMPLX(cosh(creal(__z)) * cos(cimag(__z)),
                 sinh(creal(__z)) * sin(cimag(__z)));
}
static double _Complex ctanh(double _Complex __z)
{ return csinh(__z) / ccosh(__z); }

/* THE INVERSES, as their logarithmic forms. Each is the principal value. */
static double _Complex casin(double _Complex __z)
{
    double _Complex __w = clog(CMPLX(-cimag(__z), creal(__z))
                               + csqrt(CMPLX(1.0, 0.0) - __z * __z));
    return CMPLX(cimag(__w), -creal(__w));      /* -i * w */
}
static double _Complex cacos(double _Complex __z)
{
    double _Complex __s = casin(__z);
    return CMPLX(1.5707963267948966 - creal(__s), -cimag(__s));
}
static double _Complex catan(double _Complex __z)
{
    double _Complex __w = clog((CMPLX(0.0, 1.0) + __z)
                               / (CMPLX(0.0, 1.0) - __z));
    return CMPLX(-cimag(__w) * 0.5, creal(__w) * 0.5);   /* (i/2) * w */
}
static double _Complex casinh(double _Complex __z)
{ return clog(__z + csqrt(__z * __z + CMPLX(1.0, 0.0))); }
static double _Complex cacosh(double _Complex __z)
{ return clog(__z + csqrt(__z * __z - CMPLX(1.0, 0.0))); }
static double _Complex catanh(double _Complex __z)
{
    return 0.5 * clog((CMPLX(1.0, 0.0) + __z) / (CMPLX(1.0, 0.0) - __z));
}

/* THE `f` AND `l` FAMILIES. The `f` forms compute in double and round once,
   which is a better answer than float arithmetic would give and costs
   nothing; the `l` forms compute in double too and WIDEN, because these are
   series over `<math.h>`'s real functions and those are double's. The halves
   are wide even so -- `creall` and `cimagl` of the result are 80-bit -- and
   the ones that are exact rather than approximate, `CMPLXL`, `conjl` and
   `cprojl`, never go through a double at all. */
#define __C_CFLOAT1(NAME) \
    static float _Complex NAME##f(float _Complex __z) \
    { double _Complex __r = NAME(CMPLX(crealf(__z), cimagf(__z))); \
      return CMPLXF((float)creal(__r), (float)cimag(__r)); } \
    static long double _Complex NAME##l(long double _Complex __z) \
    { return NAME(__z); }

__C_CFLOAT1(cexp)
__C_CFLOAT1(clog)
__C_CFLOAT1(csqrt)
__C_CFLOAT1(csin)
__C_CFLOAT1(ccos)
__C_CFLOAT1(ctan)
__C_CFLOAT1(csinh)
__C_CFLOAT1(ccosh)
__C_CFLOAT1(ctanh)
__C_CFLOAT1(casin)
__C_CFLOAT1(cacos)
__C_CFLOAT1(catan)
__C_CFLOAT1(casinh)
__C_CFLOAT1(cacosh)
__C_CFLOAT1(catanh)

static float _Complex cpowf(float _Complex __z, float _Complex __w)
{
    double _Complex __r = cpow(CMPLX(crealf(__z), cimagf(__z)),
                               CMPLX(crealf(__w), cimagf(__w)));
    return CMPLXF((float)creal(__r), (float)cimag(__r));
}
static long double _Complex cpowl(long double _Complex __z,
                                  long double _Complex __w)
{ return cpow(__z, __w); }

#endif
