/* <math.h> -- uasm C frontend.

   WRITTEN IN C, over `+ - * /` and nothing else. There is no libm here: the
   platform floor is three functions and none of them computes a logarithm, so
   every one of these is an algorithm rather than a call. `sqrt` is Newton from
   a bit-twiddled initial guess; `exp` and `log` reduce the argument and
   evaluate a series; `sin` and `cos` reduce modulo pi/2 with a two-part
   constant so that a large argument does not lose every significant bit to the
   reduction.

   NOT CORRECTLY ROUNDED. glibc's `exp` is within half an ulp of the true
   result and these are within a few, which is the usual difference between a
   libm somebody spent a career on and one that fits in a header. A program
   comparing `exp(x)` against a printed constant to seventeen digits will see
   it; one doing arithmetic will not. */
#ifndef _UASM_MATH_H
#define _UASM_MATH_H

#define M_E        2.7182818284590452354
#define M_LOG2E    1.4426950408889634074
#define M_LOG10E   0.43429448190325182765
#define M_LN2      0.69314718055994530942
#define M_LN10     2.30258509299404568402
#define M_PI       3.14159265358979323846
#define M_PI_2     1.57079632679489661923
#define M_PI_4     0.78539816339744830962
#define M_1_PI     0.31830988618379067154
#define M_2_PI     0.63661977236758134308
#define M_2_SQRTPI 1.12837916709551257390
#define M_SQRT2    1.41421356237309504880
#define M_SQRT1_2  0.70710678118654752440

#define HUGE_VAL  __builtin_huge_val()
#define HUGE_VALF __builtin_huge_valf()
#define INFINITY  __builtin_inff()
#define NAN       __builtin_nanf("")

#define FP_NAN       0
#define FP_INFINITE  1
#define FP_ZERO      2
#define FP_SUBNORMAL 3
#define FP_NORMAL    4

/* THE ONE PLACE THE 80-BIT ENCODING IS READ IN A HEADER. `long double` is
   software here (`support.py`'s `ldouble` unit) and these four questions are
   the only ones a header has to answer about its bits: eight bytes of
   significand, then a sixteen-bit word of sign and exponent. */
union __ld_bits { long double __v;
                  struct { unsigned long __m; unsigned short __se; } __r; };

static int __ld_isnan(long double __x)
{
    union __ld_bits __u;
    __u.__v = __x;
    return (__u.__r.__se & 0x7fff) == 0x7fff
        && (__u.__r.__m & ~((unsigned long)1 << 63)) != 0;
}
static int __ld_isinf(long double __x)
{
    union __ld_bits __u;
    __u.__v = __x;
    return (__u.__r.__se & 0x7fff) == 0x7fff
        && (__u.__r.__m & ~((unsigned long)1 << 63)) == 0;
}
static int __ld_isfinite(long double __x)
{
    union __ld_bits __u;
    __u.__v = __x;
    return (__u.__r.__se & 0x7fff) != 0x7fff;
}
static int __ld_signbit(long double __x)
{
    union __ld_bits __u;
    __u.__v = __x;
    return (__u.__r.__se >> 15) & 1;
}
static int __ld_class(long double __x)
{
    union __ld_bits __u;
    __u.__v = __x;
    if ((__u.__r.__se & 0x7fff) == 0x7fff)
        return __ld_isinf(__x) ? 1 : 0;             /* FP_INFINITE, FP_NAN */
    if ((__u.__r.__se & 0x7fff) == 0)
        return __u.__r.__m == 0 ? 2 : 3;            /* FP_ZERO, FP_SUBNORMAL */
    return 4;                                       /* FP_NORMAL */
}

/* AND THE SAME FOUR FOR THE TWO WIDTHS THE MACHINE HAS, as functions rather
   than as the builtins themselves: a `_Generic` names its branches without
   calling them, and a builtin is not a value. */
static int __c_isnan(double __x) { return __builtin_isnan(__x); }
static int __c_isinf(double __x) { return __builtin_isinf(__x); }
static int __c_isfinite(double __x) { return __builtin_isfinite(__x); }
static int __c_signbit(double __x) { return __builtin_signbit(__x); }
static int __c_class(double __x)
{
    if (__builtin_isnan(__x)) return FP_NAN;
    if (__builtin_isinf(__x)) return FP_INFINITE;
    if (__x == 0.0) return FP_ZERO;
    if (__builtin_fabs(__x) < 2.2250738585072014e-308) return FP_SUBNORMAL;
    return FP_NORMAL;
}

/* THE CLASSIFICATION MACROS READ THE TYPE, and they have to: a `long double`
   bigger than `DBL_MAX` would look infinite to a test that converted it to
   double first, and one smaller than `DBL_TRUE_MIN` would look like zero. */
#define isnan(x)    _Generic((x), \
    long double: __ld_isnan, default: __c_isnan)(x)
#define isinf(x)    _Generic((x), \
    long double: __ld_isinf, default: __c_isinf)(x)
#define isfinite(x) _Generic((x), \
    long double: __ld_isfinite, default: __c_isfinite)(x)
#define signbit(x)  _Generic((x), \
    long double: __ld_signbit, default: __c_signbit)(x)
#define fpclassify(x) _Generic((x), \
    long double: __ld_class, default: __c_class)(x)
#define isnormal(x) (fpclassify(x) == FP_NORMAL)
#define isgreater(a, b)      ((a) > (b))
#define isgreaterequal(a, b) ((a) >= (b))
#define isless(a, b)         ((a) < (b))
#define islessequal(a, b)    ((a) <= (b))
#define islessgreater(a, b)  ((a) < (b) || (a) > (b))
#define isunordered(a, b)    (isnan(a) || isnan(b))

typedef double double_t;
typedef float float_t;

static double fabs(double __x) { return __builtin_fabs(__x); }
static float fabsf(float __x) { return __builtin_fabsf(__x); }
static double copysign(double __x, double __y)
{ return __builtin_copysign(__x, __y); }

/* ── scaling by powers of two, which is exact ─────────────────────────── */
static double ldexp(double __x, int __n)
{
    union { double __d; unsigned long __u; } v;
    int e;
    if (__x == 0.0 || __builtin_isnan(__x) || __builtin_isinf(__x)) return __x;
    /* ONE STEP AT A TIME NEAR THE ENDS, because building 2^n as a double
       overflows for n above 1023 and underflows below -1022 -- and a program
       scaling by 2000 in two halves is the reason `ldexp` exists. */
    while (__n > 1000) { __x *= 8.98846567431158e307; __n -= 1023; }
    while (__n < -1000) { __x *= 1.1125369292536007e-308; __n += 1022; }
    v.__d = 1.0;
    e = 1023 + __n;
    if (e < 1) { /* subnormal territory: halve the hard way */
        while (__n < 0) { __x *= 0.5; __n++; }
        return __x;
    }
    if (e > 2046) return __x * 8.98846567431158e307 * 8.98846567431158e307;
    v.__u = ((unsigned long)e) << 52;
    return __x * v.__d;
}
static double scalbn(double __x, int __n) { return ldexp(__x, __n); }
static double scalbln(double __x, long __n) { return ldexp(__x, (int)__n); }
static float scalbnf(float __x, int __n) { return (float)ldexp((double)__x, __n); }
static float ldexpf(float __x, int __n) { return (float)ldexp((double)__x, __n); }
static float copysignf(float __x, float __y)
{ return (float)__builtin_copysign((double)__x, (double)__y); }

static double frexp(double __x, int *__e)
{
    union { double __d; unsigned long __u; } v;
    int biased;
    *__e = 0;
    if (__x == 0.0 || __builtin_isnan(__x) || __builtin_isinf(__x)) return __x;
    v.__d = __x;
    biased = (int)((v.__u >> 52) & 0x7FFUL);
    if (biased == 0) {                 /* subnormal: normalise first */
        __x *= 18014398509481984.0;    /* 2^54 */
        v.__d = __x;
        biased = (int)((v.__u >> 52) & 0x7FFUL);
        *__e = -54;
    }
    *__e += biased - 1022;
    v.__u = (v.__u & 0x800FFFFFFFFFFFFFUL) | (1022UL << 52);
    return v.__d;
}

/* ── rounding ─────────────────────────────────────────────────────────── */
static double trunc(double __x)
{
    if (__builtin_isnan(__x) || __builtin_isinf(__x)) return __x;
    if (__x >= 9.007199254740992e15 || __x <= -9.007199254740992e15) return __x;
    /* Below 2^53 every integer is representable, so the cast is exact and the
       sign survives -- `(double)(long long)` is the whole of it. */
    return (double)(long)__x;
}
static double floor(double __x)
{
    double t = trunc(__x);
    if (t > __x) t -= 1.0;
    return __x == 0.0 ? __x : t;
}
static double ceil(double __x)
{
    double t = trunc(__x);
    if (t < __x) t += 1.0;
    return __x == 0.0 ? __x : t;
}
static double round(double __x)
{
    /* HALF AWAY FROM ZERO, which is what `round` is defined to do and is NOT
       what the FPU's default mode does -- `rint` is the one that rounds to
       even. Keeping them apart is the whole point of having both. */
    double t = trunc(__x);
    double f = __x - t;
    if (f >= 0.5) return t + 1.0;
    if (f <= -0.5) return t - 1.0;
    return t;
}
static double rint(double __x)
{
    double t = trunc(__x);
    double f = __x - t;
    if (f > 0.5 || (f == 0.5 && ((long)t & 1L))) return t + 1.0;
    if (f < -0.5 || (f == -0.5 && ((long)t & 1L))) return t - 1.0;
    return t;
}
static double nearbyint(double __x) { return rint(__x); }
static long lround(double __x) { return (long)round(__x); }
static long lrint(double __x) { return (long)rint(__x); }
static long long llround(double __x) { return (long long)round(__x); }

static double modf(double __x, double *__ip)
{
    double t = trunc(__x);
    *__ip = t;
    if (__builtin_isinf(__x)) return __builtin_copysign(0.0, __x);
    return __x - t;
}

static double fmod(double __x, double __y)
{
    double r;
    int neg;
    if (__builtin_isnan(__x) || __builtin_isnan(__y) || __y == 0.0
        || __builtin_isinf(__x)) return __builtin_nan("");
    if (__builtin_isinf(__y)) return __x;
    neg = __x < 0.0;
    r = fabs(__x);
    __y = fabs(__y);
    if (r < __y) return __x;
    {
        /* REPEATED SUBTRACTION IN BINARY, not `x - trunc(x/y)*y`: the latter
           loses every bit of the answer once x/y exceeds 2^53, and `fmod` is
           exact by definition. */
        int e1, e2, i;
        double m1 = frexp(r, &e1), m2 = frexp(__y, &e2);
        double scaled = ldexp(m2, e1);
        (void)m1;
        for (i = e1; i >= e2; i--) {
            if (r >= scaled) r -= scaled;
            scaled *= 0.5;
        }
    }
    return neg ? -r : r;
}
static double remainder(double __x, double __y)
{
    double r = fmod(__x, __y);
    double h = fabs(__y) * 0.5;
    if (r > h) r -= fabs(__y);
    else if (r < -h) r += fabs(__y);
    return r;
}

static double fmin(double __a, double __b)
{ if (__builtin_isnan(__a)) return __b; if (__builtin_isnan(__b)) return __a;
  return __a < __b ? __a : __b; }
static double fmax(double __a, double __b)
{ if (__builtin_isnan(__a)) return __b; if (__builtin_isnan(__b)) return __a;
  return __a > __b ? __a : __b; }
static double fdim(double __a, double __b)
{ return __a > __b ? __a - __b : 0.0; }
static double fma(double __a, double __b, double __c)
{ return __a * __b + __c; }

/* ── roots ────────────────────────────────────────────────────────────── */
static double sqrt(double __x)
{
    union { double __d; unsigned long __u; } v;
    double y;
    int i;
    if (__builtin_isnan(__x)) return __x;
    if (__x < 0.0) return __builtin_nan("");
    if (__x == 0.0 || __builtin_isinf(__x)) return __x;
    /* HALVE THE EXPONENT to get the first three bits right, then Newton. Five
       iterations take a 3-bit guess past 53 bits, because each one doubles
       the number of correct digits. */
    v.__d = __x;
    v.__u = (v.__u >> 1) + 0x1FF8000000000000UL;
    y = v.__d;
    for (i = 0; i < 5; i++) y = 0.5 * (y + __x / y);
    return y;
}
static float sqrtf(float __x) { return (float)sqrt((double)__x); }
static double cbrt(double __x)
{
    double y;
    int i, neg = __x < 0.0;
    if (__x == 0.0 || __builtin_isnan(__x) || __builtin_isinf(__x)) return __x;
    if (neg) __x = -__x;
    { int e; double m = frexp(__x, &e);
      y = ldexp(m, e / 3); if (y <= 0.0) y = 1.0; }
    for (i = 0; i < 20; i++) y = (2.0 * y + __x / (y * y)) / 3.0;
    return neg ? -y : y;
}
static double hypot(double __a, double __b)
{
    __a = fabs(__a); __b = fabs(__b);
    if (__a < __b) { double t = __a; __a = __b; __b = t; }
    if (__a == 0.0) return 0.0;
    { double r = __b / __a; return __a * sqrt(1.0 + r * r); }
}

/* ── exponential and logarithm ────────────────────────────────────────── */
static double exp(double __x)
{
    double r, term, sum;
    int k, i;
    if (__builtin_isnan(__x)) return __x;
    if (__x > 709.782712893384) return __builtin_huge_val();
    if (__x < -745.133219101941) return 0.0;
    /* x = k*ln2 + r with |r| <= ln2/2, so the series converges in a dozen
       terms and the exponent is recovered exactly by `ldexp`. */
    k = (int)round(__x * 1.4426950408889634074);
    /* ln2 IN TWO PIECES, the first with its low 21 bits zero so that
       `k * ln2_hi` is EXACT for every k a double exponent can reach. One
       piece loses the bottom of the reduction and costs an ulp in the
       answer -- which is exactly what `exp(20)` showed against glibc before
       this: right to sixteen figures and wrong in the seventeenth. */
    r = __x - (double)k * 6.93147180369123816490e-01;
    r -= (double)k * 1.90821492927058770002e-10;
    sum = 1.0;
    term = 1.0;
    for (i = 1; i <= 16; i++) {
        term = term * r / (double)i;
        sum += term;
    }
    return ldexp(sum, k);
}
static double exp2(double __x) { return exp(__x * 0.69314718055994530942); }
static double expm1(double __x)
{
    if (fabs(__x) > 0.25) return exp(__x) - 1.0;
    { double term = __x, sum = __x; int i;
      for (i = 2; i <= 18; i++) { term = term * __x / (double)i; sum += term; }
      return sum; }
}

static double log(double __x)
{
    int e, i;
    double m, s, s2, sum, p;
    if (__builtin_isnan(__x)) return __x;
    if (__x < 0.0) return __builtin_nan("");
    if (__x == 0.0) return -__builtin_huge_val();
    if (__builtin_isinf(__x)) return __x;
    m = frexp(__x, &e);
    /* Keep the mantissa near 1 so the atanh series converges fast: below
       sqrt(1/2) it is better to double it and take one off the exponent. */
    if (m < 0.70710678118654752440) { m *= 2.0; e -= 1; }
    s = (m - 1.0) / (m + 1.0);
    s2 = s * s;
    sum = 0.0;
    p = s;
    for (i = 1; i <= 25; i += 2) { sum += p / (double)i; p *= s2; }
    return 2.0 * sum + (double)e * 0.6931471805599453094172321214581766;
}
static double log2(double __x) { return log(__x) * 1.4426950408889634074; }
static double log10(double __x) { return log(__x) * 0.43429448190325182765; }
static double log1p(double __x)
{
    if (fabs(__x) > 0.25) return log(1.0 + __x);
    { double s = __x / (2.0 + __x), s2 = s * s, sum = 0.0, p = s; int i;
      for (i = 1; i <= 25; i += 2) { sum += p / (double)i; p *= s2; }
      return 2.0 * sum; }
}

static double pow(double __x, double __y)
{
    long n;
    if (__y == 0.0) return 1.0;
    if (__builtin_isnan(__x) || __builtin_isnan(__y)) return __builtin_nan("");
    if (__x == 1.0) return 1.0;
    /* AN INTEGER EXPONENT IS DONE BY SQUARING, not by exp(y*log x): the
       latter is wrong for a negative base and loses precision for a small
       one, and `pow(x, 2)` appearing in a loop is the common case. */
    n = (long)__y;
    if ((double)n == __y && n > -1024 && n < 1024) {
        double r = 1.0, b = __x;
        long k = n < 0 ? -n : n;
        while (k) { if (k & 1L) r *= b; b *= b; k >>= 1; }
        return n < 0 ? 1.0 / r : r;
    }
    if (__x < 0.0) return __builtin_nan("");
    if (__x == 0.0) return __y > 0.0 ? 0.0 : __builtin_huge_val();
    return exp(__y * log(__x));
}
static float powf(float __x, float __y) { return (float)pow(__x, __y); }

/* ── trigonometry ─────────────────────────────────────────────────────── */
/* Pi over two in three pieces, so that reducing a large argument does not
   lose the bits that decide the answer. */
#define __PIO2_1 1.57079632673412561417e+00
#define __PIO2_2 6.07710050650619224932e-11
#define __PIO2_3 2.02226624879595063154e-21

static double __sin_poly(double __r)
{
    double r2 = __r * __r;
    return __r * (1.0 + r2 * (-1.66666666666666657415e-01 + r2 *
           (8.33333333333329961475e-03 + r2 * (-1.98412698412696162806e-04 +
           r2 * (2.75573192239198852272e-06 + r2 * (-2.50521083763502045810e-08
           + r2 * (1.60590438368216145994e-10 - r2 * 7.64716373181981647590e-13
           )))))));
}

static double __cos_poly(double __r)
{
    double r2 = __r * __r;
    return 1.0 + r2 * (-0.5 + r2 * (4.16666666666666019037e-02 + r2 *
           (-1.38888888888741095749e-03 + r2 * (2.48015872894767294178e-05 +
           r2 * (-2.75573143513906633035e-07 + r2 *
           (2.08757232129817482790e-09 - r2 * 1.13596475577881948265e-11))))));
}

static int __trig_reduce(double __x, double *__r)
{
    double q = round(__x * 0.63661977236758134308);
    double r = __x - q * __PIO2_1;
    r -= q * __PIO2_2;
    r -= q * __PIO2_3;
    *__r = r;
    { long n = (long)q; return (int)(n & 3L); }
}

static double sin(double __x)
{
    double r;
    int q;
    if (__builtin_isnan(__x) || __builtin_isinf(__x)) return __builtin_nan("");
    q = __trig_reduce(__x, &r);
    if (q < 0) q += 4;
    if (q == 0) return __sin_poly(r);
    if (q == 1) return __cos_poly(r);
    if (q == 2) return -__sin_poly(r);
    return -__cos_poly(r);
}

static double cos(double __x)
{
    double r;
    int q;
    if (__builtin_isnan(__x) || __builtin_isinf(__x)) return __builtin_nan("");
    q = __trig_reduce(__x, &r);
    if (q < 0) q += 4;
    if (q == 0) return __cos_poly(r);
    if (q == 1) return -__sin_poly(r);
    if (q == 2) return -__cos_poly(r);
    return __sin_poly(r);
}

static double tan(double __x)
{
    double s = sin(__x), c = cos(__x);
    if (c == 0.0) return __builtin_copysign(__builtin_huge_val(), s);
    return s / c;
}

static double atan(double __x)
{
    int invert = 0, neg = 0, i;
    double s, sum, p, s2;
    if (__builtin_isnan(__x)) return __x;
    if (__x < 0.0) { __x = -__x; neg = 1; }
    if (__builtin_isinf(__x)) { s = 1.57079632679489661923; return neg ? -s : s; }
    if (__x > 1.0) { __x = 1.0 / __x; invert = 1; }
    /* Halve twice with the identity atan(x) = 2*atan(x / (1 + sqrt(1+x^2)))
       so the series argument is under 0.27 and twenty terms are plenty. */
    __x = __x / (1.0 + sqrt(1.0 + __x * __x));
    __x = __x / (1.0 + sqrt(1.0 + __x * __x));
    s = __x;
    s2 = s * s;
    sum = 0.0;
    p = s;
    for (i = 1; i <= 29; i += 2) {
        sum += (((i - 1) / 2) & 1) ? -p / (double)i : p / (double)i;
        p *= s2;
    }
    sum *= 4.0;
    if (invert) sum = 1.57079632679489661923 - sum;
    return neg ? -sum : sum;
}

static double atan2(double __y, double __x)
{
    if (__x == 0.0 && __y == 0.0)
        return __builtin_signbit(__x) ? __builtin_copysign(3.14159265358979323846, __y) : __builtin_copysign(0.0, __y);
    if (__x == 0.0) return __y > 0.0 ? 1.57079632679489661923 : -1.57079632679489661923;
    { double a = atan(__y / __x);
      if (__x > 0.0) return a;
      return __y >= 0.0 ? a + 3.14159265358979323846
                        : a - 3.14159265358979323846; }
}

static double asin(double __x)
{
    if (__x > 1.0 || __x < -1.0) return __builtin_nan("");
    if (__x == 1.0) return 1.57079632679489661923;
    if (__x == -1.0) return -1.57079632679489661923;
    return atan(__x / sqrt(1.0 - __x * __x));
}
static double acos(double __x)
{
    if (__x > 1.0 || __x < -1.0) return __builtin_nan("");
    return 1.57079632679489661923 - asin(__x);
}

static double sinh(double __x)
{ double e = exp(__x); return (e - 1.0 / e) * 0.5; }
static double cosh(double __x)
{ double e = exp(__x); return (e + 1.0 / e) * 0.5; }
static double tanh(double __x)
{
    double e;
    if (__x > 20.0) return 1.0;
    if (__x < -20.0) return -1.0;
    e = exp(2.0 * __x);
    return (e - 1.0) / (e + 1.0);
}
static double asinh(double __x)
{ return __x < 0.0 ? -log(-__x + sqrt(__x * __x + 1.0))
                   : log(__x + sqrt(__x * __x + 1.0)); }
static double acosh(double __x)
{ return __x < 1.0 ? __builtin_nan("") : log(__x + sqrt(__x * __x - 1.0)); }
static double atanh(double __x)
{
    if (__x >= 1.0 || __x <= -1.0) return __builtin_nan("");
    return 0.5 * log((1.0 + __x) / (1.0 - __x));
}

/* ── the `f` and `l` families ─────────────────────────────────────────── */
/* COMPUTED IN DOUBLE AND NARROWED, which is what `FLT_EVAL_METHOD == 0`
   permits and what makes the `f` forms as accurate as the double ones
   rather than half as accurate. `long double` IS `double` here, so an `l`
   form is the plain one under another name -- a name a program may still
   take the address of, which is why these are functions and not macros.

   WRITTEN BY A MACRO because there are ninety of them and a hand-written
   list is ninety chances to call `cosh` from `sinhf`. That really is the
   kind of mistake this shape of file collects. */
#define __C_MATH1(NAME) \
    static float NAME##f(float __x) { return (float)NAME((double)__x); } \
    static long double NAME##l(long double __x) { return NAME((double)__x); }

#define __C_MATH2(NAME) \
    static float NAME##f(float __x, float __y) \
    { return (float)NAME((double)__x, (double)__y); } \
    static long double NAME##l(long double __x, long double __y) \
    { return NAME((double)__x, (double)__y); }

__C_MATH1(sin)
__C_MATH1(cos)
__C_MATH1(tan)
__C_MATH1(asin)
__C_MATH1(acos)
__C_MATH1(atan)
__C_MATH1(sinh)
__C_MATH1(cosh)
__C_MATH1(tanh)
__C_MATH1(asinh)
__C_MATH1(acosh)
__C_MATH1(atanh)
__C_MATH1(exp)
__C_MATH1(exp2)
__C_MATH1(expm1)
__C_MATH1(log)
__C_MATH1(log2)
__C_MATH1(log10)
__C_MATH1(log1p)
__C_MATH1(cbrt)
__C_MATH1(floor)
__C_MATH1(ceil)
__C_MATH1(trunc)
__C_MATH1(round)
__C_MATH1(rint)
__C_MATH1(nearbyint)
__C_MATH2(fmod)
__C_MATH2(atan2)
__C_MATH2(hypot)
__C_MATH2(fmin)
__C_MATH2(fmax)
__C_MATH2(fdim)
__C_MATH2(remainder)

/* THE ONES THE MACRO CANNOT WRITE, because their signatures are not the two
   shapes above: a different return type, a pointer, or a third argument. */
/* NEWTON FROM A DOUBLE'S ANSWER, which is 53 correct bits: each step
   doubles them, so two steps pass the 64 this format holds. The scaling is
   done on the EXPONENT rather than by dividing, because a value this type
   can hold may be far outside double's range -- and an even power of two
   comes out of a square root exactly. */
static long double sqrtl(long double __x)
{
    union __ld_bits __u;
    int __e, __half;
    long double __y, __r;
    if (__ld_isnan(__x) || __x == 0.0L || __ld_isinf(__x)) {
        if (__ld_isinf(__x) && __ld_signbit(__x)) return __builtin_nan("");
        return __x;
    }
    if (__ld_signbit(__x)) return __builtin_nan("");
    __u.__v = __x;
    if ((__u.__r.__se & 0x7fff) == 0) {
        /* A SUBNORMAL HAS NO LEADING BIT: scaling it up by 2^64 makes it a
           normal one exactly, and the exponent below pays it back. */
        __u.__v = __x * 18446744073709551616.0L;
        __e = (int)(__u.__r.__se & 0x7fff) - 16383 - 64;
    } else {
        __e = (int)(__u.__r.__se & 0x7fff) - 16383;
    }
    __half = __e >> 1;                          /* floor, for a negative e */
    __u.__r.__se = (unsigned short)(16383 + (__e - 2 * __half));
    __y = __u.__v;                              /* now in [1, 4) */
    __r = (long double)sqrt((double)__y);
    __r = 0.5L * (__r + __y / __r);
    __r = 0.5L * (__r + __y / __r);
    __u.__v = __r;
    __u.__r.__se = (unsigned short)((int)(__u.__r.__se & 0x7fff) + __half);
    return __u.__v;
}
/* ON THE BITS, not through double: `fabsl` and `copysignl` of a value
   outside double's range must still be that value. */
static long double fabsl(long double __x)
{
    union __ld_bits __u;
    __u.__v = __x;
    __u.__r.__se &= 0x7fff;
    return __u.__v;
}
static long double powl(long double __x, long double __y)
{ return pow((double)__x, (double)__y); }
static long double copysignl(long double __x, long double __y)
{
    union __ld_bits __u, __v;
    __u.__v = __x;
    __v.__v = __y;
    __u.__r.__se = (unsigned short)((__u.__r.__se & 0x7fff)
                                    | (__v.__r.__se & 0x8000));
    return __u.__v;
}
/* SCALING BY A POWER OF TWO IS EXPONENT ARITHMETIC, and doing it through
   double would overflow for a value this type can hold and double cannot.
   The multiply-by-two loop is the honest way to say it with the arithmetic
   this library has; the count is bounded by the exponent range. */
static long double ldexpl(long double __x, int __n)
{
    long double __r = __x;
    if (!__ld_isfinite(__x) || __x == 0.0L) return __x;
    while (__n > 0) { __r *= 2.0L; __n--; if (__ld_isinf(__r)) return __r; }
    while (__n < 0) { __r *= 0.5L; __n++; if (__r == 0.0L) return __r; }
    return __r;
}
static long double scalbnl(long double __x, int __n) { return ldexpl(__x, __n); }
static long double scalblnl(long double __x, long __n)
{ return ldexpl(__x, (int)__n); }
static float frexpf(float __x, int *__e) { return (float)frexp((double)__x, __e); }
static long double frexpl(long double __x, int *__e)
{ return frexp((double)__x, __e); }
static float modff(float __x, float *__ip)
{
    double __whole;
    float __frac = (float)modf((double)__x, &__whole);
    *__ip = (float)__whole;
    return __frac;
}
static long double modfl(long double __x, long double *__ip)
{
    /* THE CAST IS THE POINT: `long double *` and `double *` are different
       types even where the two are the same size, and a compiler that let
       them be interchanged silently would be hiding the one place this
       equivalence is visible. */
    double __whole;
    long double __frac = modf((double)__x, &__whole);
    *__ip = __whole;
    return __frac;
}
static float fmaf(float __a, float __b, float __c)
{ return (float)fma((double)__a, (double)__b, (double)__c); }
static long double fmal(long double __a, long double __b, long double __c)
{ return fma((double)__a, (double)__b, (double)__c); }
static long lroundf(float __x) { return lround((double)__x); }
static long lroundl(long double __x) { return lround((double)__x); }
static long lrintf(float __x) { return lrint((double)__x); }
static long lrintl(long double __x) { return lrint((double)__x); }
static long long llroundf(float __x) { return llround((double)__x); }
static long long llroundl(long double __x) { return llround((double)__x); }
static long long llrint(double __x) { return (long long)lrint(__x); }
static long long llrintf(float __x) { return llrint((double)__x); }
static long long llrintl(long double __x) { return llrint((double)__x); }

/* THE LAST FEW C23 NAMES, which had nothing to be written in terms of. */
static double nan(const char *__tag) { (void)__tag; return __builtin_nan(""); }
static float nanf(const char *__tag) { (void)__tag; return (float)__builtin_nan(""); }
static long double nanl(const char *__tag) { (void)__tag; return __builtin_nan(""); }

static int ilogb(double __x)
{
    int __e = 0;
    if (__x == 0.0) return -2147483647 - 1;      /* FP_ILOGB0 */
    if (__builtin_isnan(__x) || __builtin_isinf(__x)) return 2147483647;
    frexp(__x, &__e);
    return __e - 1;
}
static int ilogbf(float __x) { return ilogb((double)__x); }
static int ilogbl(long double __x) { return ilogb((double)__x); }
static double logb(double __x)
{
    if (__x == 0.0) return -__builtin_inf();
    if (__builtin_isnan(__x)) return __x;
    if (__builtin_isinf(__x)) return __builtin_inf();
    return (double)ilogb(__x);
}
static float logbf(float __x) { return (float)logb((double)__x); }
static long double logbl(long double __x) { return logb((double)__x); }

/* THE STEP TO THE NEXT REPRESENTABLE VALUE, done on the bits: the doubles
   in increasing order are their bit patterns in increasing order, read as
   integers, which is the property IEEE 754 was designed to have. */
static double nextafter(double __x, double __y)
{
    union { double __d; unsigned long __u; } __a;
    if (__builtin_isnan(__x) || __builtin_isnan(__y)) return __x + __y;
    if (__x == __y) return __y;
    if (__x == 0.0) {
        __a.__u = 1;
        return __y > 0.0 ? __a.__d : -__a.__d;
    }
    __a.__d = __x;
    if ((__x < __y) == (__x > 0.0)) __a.__u++;
    else __a.__u--;
    return __a.__d;
}
static float nextafterf(float __x, float __y)
{ return (float)nextafter((double)__x, (double)__y); }
static long double nextafterl(long double __x, long double __y)
{ return nextafter((double)__x, (double)__y); }
static double nexttoward(double __x, long double __y)
{ return nextafter(__x, (double)__y); }
static float nexttowardf(float __x, long double __y)
{ return (float)nextafter((double)__x, (double)__y); }
static long double nexttowardl(long double __x, long double __y)
{ return nextafter((double)__x, (double)__y); }

static double remquo(double __x, double __y, int *__quo)
{
    double __r = remainder(__x, __y);
    /* THE LOW BITS OF THE QUOTIENT, which is all C promises: at least three
       of them, with the sign of x/y. */
    if (__quo) {
        double __q = (__x - __r) / __y;
        long __n = (long)__q;
        *__quo = (int)(__n & 7) * ((__q < 0.0) ? -1 : 1);
        if (__q < 0.0) *__quo = -(int)((-__n) & 7);
    }
    return __r;
}
static float remquof(float __x, float __y, int *__quo)
{ return (float)remquo((double)__x, (double)__y, __quo); }
static long double remquol(long double __x, long double __y, int *__quo)
{ return remquo((double)__x, (double)__y, __quo); }

/* `erf` AND `tgamma`, BY SERIES. Accurate to about ten significant figures,
   which is enough for what they are used for and is said rather than
   implied: a libm spends a great deal more than this on them. */
static double erf(double __x)
{
    /* Abramowitz and Stegun 7.1.26, with the sign taken out. */
    double __t, __y, __s = __x < 0.0 ? -1.0 : 1.0;
    double __a = fabs(__x);
    if (__a > 6.0) return __s;
    __t = 1.0 / (1.0 + 0.3275911 * __a);
    __y = 1.0 - (((((1.061405429 * __t - 1.453152027) * __t) + 1.421413741)
                  * __t - 0.284496736) * __t + 0.254829592) * __t * exp(-__a * __a);
    return __s * __y;
}
static double erfc(double __x) { return 1.0 - erf(__x); }
__C_MATH1(erf)
__C_MATH1(erfc)

static double lgamma(double __x)
{
    /* Lanczos, g = 7, nine coefficients -- the usual set. */
    static const double __g[9] = {
        0.99999999999980993, 676.5203681218851, -1259.1392167224028,
        771.32342877765313, -176.61502916214059, 12.507343278686905,
        -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7 };
    double __a = __g[0], __t;
    int __i;
    if (__x < 0.5)
        return log(3.14159265358979323846 / fabs(sin(3.14159265358979323846 * __x)))
               - lgamma(1.0 - __x);
    __x -= 1.0;
    for (__i = 1; __i < 9; __i++) __a += __g[__i] / (__x + (double)__i);
    __t = __x + 7.5;
    return 0.5 * log(2.0 * 3.14159265358979323846) + (__x + 0.5) * log(__t)
           - __t + log(__a);
}
static double tgamma(double __x)
{
    if (__x < 0.5)
        return 3.14159265358979323846
               / (sin(3.14159265358979323846 * __x) * tgamma(1.0 - __x));
    return exp(lgamma(__x)) * ((__x - floor(__x) == 0.0 && __x < 0.0) ? 0.0 : 1.0);
}
__C_MATH1(lgamma)
__C_MATH1(tgamma)

#endif
