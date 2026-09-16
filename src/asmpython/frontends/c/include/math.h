/* <math.h> -- asmpython C frontend.

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
#ifndef _ASMPYTHON_MATH_H
#define _ASMPYTHON_MATH_H

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

#define isnan(x)    __builtin_isnan((double)(x))
#define isinf(x)    __builtin_isinf((double)(x))
#define isfinite(x) __builtin_isfinite((double)(x))
#define signbit(x)  __builtin_signbit((double)(x))
#define isnormal(x) (__builtin_isfinite((double)(x)) && (x) != 0.0)
#define fpclassify(x) (isnan(x) ? FP_NAN : isinf(x) ? FP_INFINITE : \
                       (x) == 0.0 ? FP_ZERO : FP_NORMAL)
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

/* The float spellings, computed in double and narrowed. Wider intermediate
   arithmetic is permitted and is what FLT_EVAL_METHOD 0 means here. */
static float sinf(float __x) { return (float)sin((double)__x); }
static float cosf(float __x) { return (float)cos((double)__x); }
static float tanf(float __x) { return (float)tan((double)__x); }
static float expf(float __x) { return (float)exp((double)__x); }
static float logf(float __x) { return (float)log((double)__x); }
static float log10f(float __x) { return (float)log10((double)__x); }
static float floorf(float __x) { return (float)floor((double)__x); }
static float ceilf(float __x) { return (float)ceil((double)__x); }
static float fmodf(float __x, float __y) { return (float)fmod((double)__x, (double)__y); }
static float atan2f(float __y, float __x) { return (float)atan2((double)__y, (double)__x); }

#endif
