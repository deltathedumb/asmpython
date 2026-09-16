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
#define __STDC_VERSION_MATH_H__ 202311L

/* THESE FUNCTIONS SET `errno`, which is what `math_errhandling` below
   promises and why this header includes `<errno.h>`. C offers two ways to
   report a domain or range error -- `errno`, and the floating-point
   exception flags -- and an implementation says which it uses. There are no
   exception flags here (`<fenv.h>` and `<float.h>` both say why: the IR has
   no instruction that reads a floating-point status word), so `errno` is
   the only one left, and `math_errhandling` cannot be zero: C requires it
   to name at least one. So every domain error below actually sets EDOM and
   every pole and overflow sets ERANGE, rather than the header claiming a
   mechanism it does not implement.

   WHERE glibc DIFFERS. It sets `math_errhandling` to 3 and then leans on
   the exception flags for some of it: `pow(2.0, 2000.0)` overflows to
   infinity and leaves `errno` alone, and `pow(+0.0, -1.0)` is a pole with
   no error while `pow(-0.0, -3.0)` is a pole with one. C asks for ERANGE in
   all three. This follows C. */
#include <errno.h>

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
#define HUGE_VALL ((long double)__builtin_huge_val())
#define INFINITY  __builtin_inff()
#define NAN       __builtin_nanf("")

/* `ilogb` HAS TO ANSWER SOMETHING for a zero, a NaN and an infinity, and
   these are the three answers. They are `INT_MIN` and `INT_MAX` spelled
   out rather than included from `<limits.h>`, because a program that
   includes `<math.h>` has not asked for `<limits.h>`. */
#define FP_ILOGB0   (-2147483647 - 1)
/* C LETS THIS BE `INT_MAX` OR `INT_MIN` and this is `INT_MIN`, which is
   glibc's choice too. AN INFINITY IS A SEPARATE CASE and always answers
   `INT_MAX`, whichever way this one goes -- 7.12.6.5 spells out all
   three, and reading `FP_ILOGBNAN` as the infinity's answer is the slip
   this note exists to stop. */
#define FP_ILOGBNAN (-2147483647 - 1)

#define MATH_ERRNO     1
#define MATH_ERREXCEPT 2
#define math_errhandling MATH_ERRNO

#define FP_NAN       0
#define FP_INFINITE  1
#define FP_ZERO      2
#define FP_SUBNORMAL 3
#define FP_NORMAL    4

/* THE TWO WAYS A RESULT IS WRONG, as one-line functions rather than a
   statement at each site: a domain or range error is a `return` in every
   place one happens, and `return __math_dom(nan)` keeps it one. */
static double __math_dom(double __r) { errno = EDOM; return __r; }
static double __math_ran(double __r) { errno = ERANGE; return __r; }
static long double __math_doml(long double __r) { errno = EDOM; return __r; }
static long double __math_ranl(long double __r) { errno = ERANGE; return __r; }

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
        return __x == 0.0 ? __math_ran(__x) : __x;
    }
    if (e > 2046)
        return __math_ran(__x * 8.98846567431158e307 * 8.98846567431158e307);
    v.__u = ((unsigned long)e) << 52;
    /* THE STEP-DOWN LOOP ABOVE CAN HAVE REACHED ZERO ALREADY, so the test
       is here and not only in the subnormal branch. A zero argument went
       home at the top, so a zero HERE is an underflow. */
    __x = __x * v.__d;
    return (__x == 0.0 || __builtin_isinf(__x)) ? __math_ran(__x) : __x;
}
static double scalbn(double __x, int __n) { return ldexp(__x, __n); }
static double scalbln(double __x, long __n) { return ldexp(__x, (int)__n); }
static float scalbnf(float __x, int __n) { return (float)ldexp((double)__x, __n); }
static float scalblnf(float __x, long __n)
{ return (float)ldexp((double)__x, (int)__n); }
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
    /* Below 2^53 every integer is representable, so the cast is exact; the
       `copysign` is what carries the sign of a value that truncates to
       zero, because `(long)(-0.5)` is `0` and `trunc(-0.5)` is `-0.0`. */
    return __builtin_copysign((double)(long)__x, __x);
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
    /* THE ZERO KEEPS x's SIGN, which the subtraction does not: a value with
       no fractional part gives `x - x`, and that is `+0.0` for a negative
       x in every rounding mode but one. */
    if (__x == t) return __builtin_copysign(0.0, __x);
    return __x - t;
}

/* REPEATED SUBTRACTION IN BINARY, not `x - trunc(x/y)*y`: the latter loses
   every bit of the answer once x/y exceeds 2^53, and all three of `fmod`,
   `remainder` and `remquo` are exact by definition. The quotient's low bits
   come out of the same loop -- one per halving, most significant first --
   which is what `remquo` is asked for and what tells `remainder` whether a
   value exactly half way between two multiples rounds up or down. */
static double __c_divrem(double __x, double __y, unsigned long *__q)
{
    double r = fabs(__x), b = fabs(__y), scaled;
    unsigned long k = 0;
    int e1, e2, i;
    if (r < b) { *__q = 0; return r; }
    frexp(r, &e1);
    scaled = ldexp(frexp(b, &e2), e1);
    for (i = e1; i >= e2; i--) {
        k <<= 1;
        if (r >= scaled) { r -= scaled; k |= 1; }
        scaled *= 0.5;
    }
    *__q = k;
    return r;
}
static double fmod(double __x, double __y)
{
    unsigned long k;
    double r;
    if (__builtin_isnan(__x) || __builtin_isnan(__y))
        return __builtin_nan("");
    /* A ZERO DIVISOR OR AN INFINITE DIVIDEND IS A DOMAIN ERROR. C makes
       this one a "may" and glibc takes it, so both report EDOM and a
       program can rely on it. A NaN argument is NOT an error and is
       answered above, which is why the two tests are not one. */
    if (__y == 0.0 || __builtin_isinf(__x))
        return __math_dom(__builtin_nan(""));
    if (__builtin_isinf(__y) || __x == 0.0) return __x;
    r = __c_divrem(__x, __y, &k);
    return __builtin_signbit(__x) ? -r : r;
}
/* `r + r` AND NOT `b * 0.5`: doubling is exact until it overflows, and when
   it does, the comparison it was for has already been decided the right
   way. A remainder exactly half of `y` rounds to the EVEN quotient, which
   is why the parity comes back from the loop rather than from `x / y`. */
static double remainder(double __x, double __y)
{
    unsigned long k;
    double r, b;
    if (__builtin_isnan(__x) || __builtin_isnan(__y))
        return __builtin_nan("");
    /* A ZERO DIVISOR OR AN INFINITE DIVIDEND IS A DOMAIN ERROR. C makes
       this one a "may" and glibc takes it, so both report EDOM and a
       program can rely on it. A NaN argument is NOT an error and is
       answered above, which is why the two tests are not one. */
    if (__y == 0.0 || __builtin_isinf(__x))
        return __math_dom(__builtin_nan(""));
    if (__builtin_isinf(__y) || __x == 0.0) return __x;
    b = fabs(__y);
    r = __c_divrem(__x, __y, &k);
    if (r + r > b || (r + r == b && (k & 1))) r -= b;
    return __builtin_signbit(__x) ? -r : r;
}

static double fmin(double __a, double __b)
{ if (__builtin_isnan(__a)) return __b; if (__builtin_isnan(__b)) return __a;
  return __a < __b ? __a : __b; }
static double fmax(double __a, double __b)
{ if (__builtin_isnan(__a)) return __b; if (__builtin_isnan(__b)) return __a;
  return __a > __b ? __a : __b; }
static double fdim(double __a, double __b)
{ if (__builtin_isnan(__a) || __builtin_isnan(__b)) return __builtin_nan("");
  return __a > __b ? __a - __b : 0.0; }
static double fma(double __a, double __b, double __c)
{ return __a * __b + __c; }

/* ── roots ────────────────────────────────────────────────────────────── */
static double sqrt(double __x)
{
    union { double __d; unsigned long __u; } v;
    double y;
    int i;
    if (__builtin_isnan(__x)) return __x;
    if (__x < 0.0) return __math_dom(__builtin_nan(""));
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
/* THE EXPONENTIAL WITH NOTHING SAID ABOUT `errno`, because two callers
   want different things from it. `exp` below reports the overflow and the
   underflow; `pow` calls THIS one, since an underflow inside `pow` is not
   `pow`'s to report -- C makes that one optional and glibc leaves it
   alone, so a program checking `errno` after `pow(2.0, -2000.0)` sees the
   same nothing from either. */
static double __exp_core(double __x)
{
    double r, term, sum;
    int k, i;
    if (__builtin_isnan(__x)) return __x;
    if (__builtin_isinf(__x)) return __x > 0.0 ? __x : 0.0;
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
/* AND THE REPORTING WRAPPER. A FINITE argument that comes out infinite
   overflowed, and one that comes out zero underflowed; an infinite
   argument did neither -- `exp(INFINITY)` is exactly the infinity it
   returns and nothing went wrong on the way. */
static double exp(double __x)
{
    double __r = __exp_core(__x);
    if (__builtin_isfinite(__x) && (__builtin_isinf(__r) || __r == 0.0))
        return __math_ran(__r);
    return __r;
}
static double exp2(double __x) { return exp(__x * 0.69314718055994530942); }
static double expm1(double __x)
{
    /* ANSWERED DIRECTLY FAR BELOW ZERO, not as `exp(x) - 1`: `exp(-1000)`
       underflows to zero and reports a range error for it, and `expm1` of
       the same argument is exactly -1 with nothing wrong. Below -40 the
       exponential is under 5e-18, which is smaller than the last bit of 1,
       so -1.0 IS the correctly rounded answer. */
    if (__x < -40.0) return -1.0;
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
    if (__x < 0.0) return __math_dom(__builtin_nan(""));
    if (__x == 0.0) return __math_ran(-__builtin_huge_val());
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
    int __whole, __odd;
    double __r;
    if (__y == 0.0) return 1.0;
    if (__builtin_isnan(__x) || __builtin_isnan(__y)) return __builtin_nan("");
    if (__x == 1.0) return 1.0;
    /* IS THE EXPONENT A WHOLE NUMBER, AND IS IT ODD -- asked of `__y`
       itself and not of a `long` it might not fit in. `pow(-2.0, 2000.0)`
       is an ordinary overflow to infinity, and a range check on the cast
       would turn it into a domain error instead, which is the bug this
       shape exists to avoid. Above 2^53 every double is an even whole
       number, so `__odd` is false there without being asked. */
    __whole = (__y == floor(__y));
    __odd = __whole && fabs(__y) < 9007199254740992.0
            && fmod(fabs(__y), 2.0) == 1.0;
    if (__x == 0.0) {
        if (__y > 0.0) return __odd ? __x : 0.0;
        /* A POLE: the limit is infinite and C asks for ERANGE. `-0.0` to an
           odd negative power keeps the sign, which is why the zero's own
           sign is read rather than assumed positive. */
        return __math_ran(__odd
                          ? __builtin_copysign(__builtin_huge_val(), __x)
                          : __builtin_huge_val());
    }
    if (__x < 0.0 && !__whole) return __math_dom(__builtin_nan(""));
    /* AN INTEGER EXPONENT IS DONE BY SQUARING, not by exp(y*log x): the
       latter is wrong for a negative base and loses precision for a small
       one, and `pow(x, 2)` appearing in a loop is the common case. */
    if (__whole && __y > -1024.0 && __y < 1024.0) {
        /* THE CAST IS INSIDE THE RANGE TEST and not above it: converting a
           double too big for a `long` -- an infinite exponent, say -- is
           undefined, and `pow(x, INFINITY)` is an ordinary call. */
        long n = (long)__y;
        double r = 1.0, b = __x;
        long k = n < 0 ? -n : n;
        while (k) { if (k & 1L) r *= b; b *= b; k >>= 1; }
        __r = n < 0 ? 1.0 / r : r;
    } else if (__x < 0.0) {
        __r = __exp_core(__y * log(-__x));
        if (__odd) __r = -__r;
    } else {
        __r = __exp_core(__y * log(__x));
    }
    /* `exp` MAY HAVE REPORTED THE OVERFLOW ALREADY and the squaring path
       never does, so it is settled here for both: a finite base and
       exponent that produce an infinity overflowed. Underflow to zero is
       left alone -- C makes that one optional and glibc does not report it
       either, so a program that checks `errno` after `pow` sees the same
       thing from both. */
    if (__builtin_isinf(__r) && !__builtin_isinf(__x) && !__builtin_isinf(__y))
        return __math_ran(__r);
    return __r;
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
    if (__x > 1.0 || __x < -1.0) return __math_dom(__builtin_nan(""));
    if (__x == 1.0) return 1.57079632679489661923;
    if (__x == -1.0) return -1.57079632679489661923;
    return atan(__x / sqrt(1.0 - __x * __x));
}
static double acos(double __x)
{
    if (__x > 1.0 || __x < -1.0) return __math_dom(__builtin_nan(""));
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
{ return __x < 1.0 ? __math_dom(__builtin_nan(""))
                   : log(__x + sqrt(__x * __x - 1.0)); }
static double atanh(double __x)
{
    /* THE TWO ENDS ARE DIFFERENT ERRORS. `atanh(1)` is a POLE -- the limit
       exists and is infinite -- and `atanh(2)` is outside the domain
       altogether. C asks for ERANGE and an infinity for the first and EDOM
       and a NaN for the second, and one test for both would give the
       wrong one of the two. */
    if (__x == 1.0 || __x == -1.0)
        return __math_ran(__builtin_copysign(__builtin_huge_val(), __x));
    if (__x > 1.0 || __x < -1.0) return __math_dom(__builtin_nan(""));
    return 0.5 * log((1.0 + __x) / (1.0 - __x));
}

/* ── the `f` and `l` families ─────────────────────────────────────────── */
/* COMPUTED IN DOUBLE AND NARROWED, which is what `FLT_EVAL_METHOD == 0`
   permits and what makes the `f` forms as accurate as the double ones
   rather than half as accurate. An `l` form computes in double too and
   WIDENS: `long double` here is 80-bit and these series are not, so the
   answer is a double's worth of precision in the wider type rather than a
   wider answer. `sqrtl`, `fabsl`, `copysignl`, `ldexpl` and the conversions
   below are the exceptions, and they are exact.

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

/* AND THE SAME TWO WITHOUT THE `l`, for the names whose wide form is EXACT
   and is written out below: rounding to an integer, the remainder, the two
   that pick one of their arguments. A double cannot stand in for any of
   them, and a macro that quietly wrote one would be the whole bug. */
#define __C_MATHF1(NAME) \
    static float NAME##f(float __x) { return (float)NAME((double)__x); }

#define __C_MATHF2(NAME) \
    static float NAME##f(float __x, float __y) \
    { return (float)NAME((double)__x, (double)__y); }

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
__C_MATHF1(floor)
__C_MATHF1(ceil)
__C_MATHF1(trunc)
__C_MATHF1(round)
__C_MATHF1(rint)
__C_MATHF1(nearbyint)
__C_MATHF2(fmod)
__C_MATH2(atan2)
__C_MATH2(hypot)
__C_MATHF2(fmin)
__C_MATHF2(fmax)
__C_MATHF2(fdim)
__C_MATHF2(remainder)

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
        if (__ld_isinf(__x) && __ld_signbit(__x))
            return __math_doml(__builtin_nan(""));
        return __x;
    }
    if (__ld_signbit(__x)) return __math_doml(__builtin_nan(""));
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
   The exponent field takes the whole step in one; only the way DOWN into
   the subnormals is a loop, because that is where bits are lost and
   halving one at a time is the honest way to lose exactly the ones the
   format loses. It cannot run more than 64 times before the value is zero. */
static long double ldexpl(long double __x, int __n)
{
    union __ld_bits __u;
    long double __r;
    int __e;
    if (!__ld_isfinite(__x) || __x == 0.0L) return __x;
    __u.__v = __x;
    if ((__u.__r.__se & 0x7fff) == 0) {          /* subnormal: normalise */
        __u.__v = __x * 18446744073709551616.0L; /* 2^64, and exact */
        __n -= 64;
    }
    __e = (int)(__u.__r.__se & 0x7fff) + __n;
    if (__e >= 0x7fff)
        return __math_ranl(__ld_signbit(__x) ? -__builtin_inf()
                                             : __builtin_inf());
    if (__e <= 0) {
        __u.__r.__se = (unsigned short)((__u.__r.__se & 0x8000) | 1);
        __r = __u.__v;
        for (__n = 1 - __e; __n > 0; __n--) {
            __r *= 0.5L;
            if (__r == 0.0L) break;
        }
        return __r == 0.0L ? __math_ranl(__r) : __r;
    }
    __u.__r.__se = (unsigned short)((__u.__r.__se & 0x8000) | (unsigned)__e);
    return __u.__v;
}
static long double scalbnl(long double __x, int __n) { return ldexpl(__x, __n); }
static long double scalblnl(long double __x, long __n)
{ return ldexpl(__x, (int)__n); }

/* ── the wide type, exactly ───────────────────────────────────────────── */
/* WHAT FOLLOWS IS NOT AN APPROXIMATION AND MUST NOT BE COMPUTED IN DOUBLE.
   A series may answer to a double's precision and still be a good answer;
   `floorl(1e30L)` computed in double is a DIFFERENT INTEGER, and `ilogbl`
   of `LDBL_MAX` is the answer for infinity. Every function below is integer
   arithmetic on the encoding -- which, unlike a double's, hands over the
   whole 64-bit significand with its leading bit in it -- so each is exact
   for every value the type holds, including the ones double has not got. */
static int __ld_biased(long double __x)
{ union __ld_bits __u; __u.__v = __x; return (int)(__u.__r.__se & 0x7fff); }
static unsigned long __ld_frac(long double __x)
{ union __ld_bits __u; __u.__v = __x; return __u.__r.__m; }
static long double __ld_make(int __sign, int __exp, unsigned long __m)
{
    union __ld_bits __u;
    __u.__v = 0.0L;                 /* the padding too, before the halves */
    __u.__r.__m = __m;
    __u.__r.__se = (unsigned short)((__sign ? 0x8000 : 0) | (__exp & 0x7fff));
    return __u.__v;
}

static long double frexpl(long double __x, int *__e)
{
    int __b, __n = 0;
    if (__x == 0.0L || !__ld_isfinite(__x)) { *__e = 0; return __x; }
    __b = __ld_biased(__x);
    if (__b == 0) {                              /* subnormal: 2^64 is exact */
        __x *= 18446744073709551616.0L;
        __b = __ld_biased(__x);
        __n = -64;
    }
    *__e = __b - 16382 + __n;
    return __ld_make(__ld_signbit(__x), 16382, __ld_frac(__x));
}

static int ilogbl(long double __x)
{
    int __b;
    if (__x == 0.0L) { errno = EDOM; return FP_ILOGB0; }
    if (__ld_isnan(__x)) { errno = EDOM; return FP_ILOGBNAN; }
    if (__ld_isinf(__x)) { errno = EDOM; return 2147483647; }
    __b = __ld_biased(__x);
    if (__b == 0) {
        __x *= 18446744073709551616.0L;
        return __ld_biased(__x) - 16383 - 64;
    }
    return __b - 16383;
}
static long double logbl(long double __x)
{
    if (__x == 0.0L) return -__builtin_inf();
    if (__ld_isnan(__x)) return __x;
    if (__ld_isinf(__x)) return __builtin_inf();
    return (long double)ilogbl(__x);
}

/* TO AN INTEGRAL VALUE, in the five ways C asks for: 0 toward zero, 1 down,
   2 up, 3 to nearest with halves away from zero, 4 to nearest with halves
   to even. One function because they differ in one line, and that line is
   easier to compare when it is the only difference. */
static long double __ld_integral(long double __x, int __mode)
{
    int __s, __e, __n, __sh, __up;
    unsigned long __m, __keep, __frac, __half, __bit;
    if (!__ld_isfinite(__x) || __x == 0.0L) return __x;
    __s = __ld_signbit(__x);
    __e = __ld_biased(__x);
    __m = __ld_frac(__x);
    __n = __e - 16383;
    if (__n >= 63) return __x;                   /* already an integer */
    if (__n < 0) {                               /* |x| < 1: +-1 or +-0 */
        if (__mode == 1) __up = __s;
        else if (__mode == 2) __up = !__s;
        else if (__mode == 3) __up = (__n == -1);
        else if (__mode == 4)
            __up = (__n == -1) && (__m != ((unsigned long)1 << 63));
        else __up = 0;
        if (__up) return __s ? -1.0L : 1.0L;
        return __ld_make(__s, 0, 0);
    }
    __sh = 63 - __n;
    __bit = (unsigned long)1 << __sh;
    __keep = __m & ~(__bit - 1);
    __frac = __m & (__bit - 1);
    if (__frac == 0) return __x;
    __half = __bit >> 1;
    if (__mode == 1) __up = __s;
    else if (__mode == 2) __up = !__s;
    else if (__mode == 3) __up = (__frac >= __half);
    else if (__mode == 4)
        __up = (__frac > __half)
            || (__frac == __half && ((__keep >> __sh) & 1));
    else __up = 0;
    if (__up) {
        __keep += __bit;
        if (__keep == 0) {                       /* carried off the top */
            __keep = (unsigned long)1 << 63;
            __e++;
        }
    }
    return __ld_make(__s, __e, __keep);
}
static long double truncl(long double __x)     { return __ld_integral(__x, 0); }
static long double floorl(long double __x)     { return __ld_integral(__x, 1); }
static long double ceill(long double __x)      { return __ld_integral(__x, 2); }
static long double roundl(long double __x)     { return __ld_integral(__x, 3); }
static long double rintl(long double __x)      { return __ld_integral(__x, 4); }
static long double nearbyintl(long double __x) { return rintl(__x); }

static long double modfl(long double __x, long double *__ip)
{
    /* THE POINTER IS THE POINT: `long double *` and `double *` are
       different types, and the whole part of a value outside double's
       range has to survive being written through this one. */
    long double __t = truncl(__x);
    *__ip = __t;
    if (__ld_isnan(__x)) return __x;
    /* THE ZERO KEEPS x's SIGN, which `x - x` does not. */
    if (__ld_isinf(__x) || __x == __t)
        return __ld_make(__ld_signbit(__x), 0, 0);
    return __x - __t;
}

/* THE NEXT REPRESENTABLE VALUE, and NOT by incrementing the bit pattern:
   that trick works for a double and not for this format, whose leading
   significand bit is stored rather than implied -- the pattern after the
   largest subnormal is a normal number with no leading bit, which is not a
   number at all. The two halves are stepped separately instead. */
static long double nextafterl(long double __x, long double __y)
{
    int __s, __e;
    unsigned long __m;
    if (__ld_isnan(__x) || __ld_isnan(__y)) return __x + __y;
    if (__x == __y) return __y;
    if (__x == 0.0L) return __ld_make(__ld_signbit(__y), 0, 1);
    __s = __ld_signbit(__x);
    __e = __ld_biased(__x);
    __m = __ld_frac(__x);
    if ((__x < __y) == (__s == 0)) {             /* away from zero */
        if (__e == 0x7fff) return __x;           /* already infinite */
        __m++;
        if (__m == 0) { __m = (unsigned long)1 << 63; __e++; }
        else if (__e == 0 && (__m >> 63)) __e = 1;  /* the first normal */
    } else {                                     /* toward zero */
        /* CROSSING DOWN A BINADE HALVES THE SPACING, so the step below the
           smallest significand is a FULL one at the smaller exponent --
           2^64-1 and not 2^64-2, which would be a step of the binade the
           value is leaving. Infinity's neighbour is the same pattern. */
        if (__e == 0x7fff) { __e = 0x7ffe; __m = ~(unsigned long)0; }
        else if (__m != ((unsigned long)1 << 63)) __m--;
        else if (__e > 1) { __e--; __m = ~(unsigned long)0; }
        else { __e = 0; __m = ((unsigned long)1 << 63) - 1; }
    }
    return __ld_make(__s, __e, __m);
}
static long double nexttowardl(long double __x, long double __y)
{ return nextafterl(__x, __y); }

/* THE EXACT REMAINDER, and the low bits of the quotient with it: one loop
   under `fmodl`, `remainderl` and `remquol`, because `x - truncl(x / y) * y`
   loses every bit of the answer once the quotient passes 2^64 and all three
   of them are exact by definition.

   ON THE SIGNIFICANDS AS INTEGERS, which is the whole reason this format is
   pleasant to work with: both are 64-bit integers with the leading bit in
   them, so long division is shift-and-subtract in `unsigned long` and the
   arithmetic below never calls the software floating point at all. The
   quotient's low three bits are all anybody asks for, and a bit above them
   cannot carry into them, so the ones that fall off the top are gone with
   nothing lost. */
static unsigned long __ld_sig(long double __x)
{
    if (__ld_biased(__x) == 0) __x *= 18446744073709551616.0L;
    return __ld_frac(__x);
}
static long double __ld_divrem(long double __x, long double __y,
                               unsigned long *__q)
{
    long double __a = fabsl(__x), __b = fabsl(__y);
    unsigned long __mx, __my, __k = 0;
    int __d, __i;
    if (__a < __b) { *__q = 0; return __a; }
    __d = ilogbl(__a) - ilogbl(__b);
    __mx = __ld_sig(__a);
    __my = __ld_sig(__b);
    /* `mx < 2^64` and `my >= 2^63`, so one subtraction is the whole of the
       first quotient digit and `mx < my` holds from here on. */
    if (__mx >= __my) { __mx -= __my; __k = 1; }
    for (__i = 0; __i < __d; __i++) {
        __k <<= 1;
        if (__mx >> 63) {
            /* doubling would leave the 64 bits; the subtraction brings it
               back inside them, and the wrap is exactly the carry out. */
            __mx = (__mx << 1) - __my;
            __k |= 1;
        } else {
            __mx <<= 1;
            if (__mx >= __my) { __mx -= __my; __k |= 1; }
        }
    }
    *__q = __k;
    return ldexpl((long double)__mx, ilogbl(__b) - 63);
}
static long double fmodl(long double __x, long double __y)
{
    unsigned long __k;
    long double __r;
    if (__ld_isnan(__x) || __ld_isnan(__y)) return __builtin_nan("");
    if (__ld_isinf(__x) || __y == 0.0L)
        return __math_doml(__builtin_nan(""));
    if (__ld_isinf(__y) || __x == 0.0L) return __x;
    __r = __ld_divrem(__x, __y, &__k);
    return __ld_signbit(__x) ? -__r : __r;
}
/* `r + r` AND NOT `b * 0.5`: doubling is exact until it overflows, and when
   it does the comparison it was for is already decided the right way. */
static long double remainderl(long double __x, long double __y)
{
    unsigned long __k;
    long double __r, __b;
    if (__ld_isnan(__x) || __ld_isnan(__y)) return __builtin_nan("");
    if (__ld_isinf(__x) || __y == 0.0L)
        return __math_doml(__builtin_nan(""));
    if (__ld_isinf(__y) || __x == 0.0L) return __x;
    __b = fabsl(__y);
    __r = __ld_divrem(__x, __y, &__k);
    if (__r + __r > __b || (__r + __r == __b && (__k & 1))) __r -= __b;
    return __ld_signbit(__x) ? -__r : __r;
}
static long double remquol(long double __x, long double __y, int *__quo)
{
    unsigned long __k = 0;
    long double __r, __b;
    if (__ld_isnan(__x) || __ld_isnan(__y)) {
        if (__quo) *__quo = 0;
        return __builtin_nan("");
    }
    if (__ld_isinf(__x) || __y == 0.0L) {
        if (__quo) *__quo = 0;
        return __math_doml(__builtin_nan(""));
    }
    if (__ld_isinf(__y) || __x == 0.0L) {
        if (__quo) *__quo = 0;
        return __x;
    }
    __b = fabsl(__y);
    __r = __ld_divrem(__x, __y, &__k);
    if (__r + __r > __b || (__r + __r == __b && (__k & 1))) { __r -= __b; __k++; }
    if (__quo) {
        int __n = (int)(__k & 7);
        *__quo = (__ld_signbit(__x) != __ld_signbit(__y)) ? -__n : __n;
    }
    return __ld_signbit(__x) ? -__r : __r;
}

static long double fminl(long double __a, long double __b)
{ if (__ld_isnan(__a)) return __b; if (__ld_isnan(__b)) return __a;
  return __a < __b ? __a : __b; }
static long double fmaxl(long double __a, long double __b)
{ if (__ld_isnan(__a)) return __b; if (__ld_isnan(__b)) return __a;
  return __a > __b ? __a : __b; }
static long double fdiml(long double __a, long double __b)
{ if (__ld_isnan(__a) || __ld_isnan(__b)) return __builtin_nan("");
  return __a > __b ? __a - __b : 0.0L; }

/* `fmal` IS THE ONE THAT IS NOT FUSED, and says so: a single rounding of
   a*b+c needs the product's whole 128-bit significand, which this library
   has no way to hold. It is computed in the WIDE type even so, which is
   what makes it better than the double it used to be. */
static long double fmal(long double __a, long double __b, long double __c)
{ return __a * __b + __c; }

/* ── the float forms whose shape the macro could not write ────────────── */
static float frexpf(float __x, int *__e) { return (float)frexp((double)__x, __e); }
static float modff(float __x, float *__ip)
{
    double __whole;
    float __frac = (float)modf((double)__x, &__whole);
    *__ip = (float)__whole;
    return __frac;
}
static float fmaf(float __a, float __b, float __c)
{ return (float)fma((double)__a, (double)__b, (double)__c); }
static long lroundf(float __x) { return lround((double)__x); }
static long lrintf(float __x) { return lrint((double)__x); }
static long long llroundf(float __x) { return llround((double)__x); }
static long long llrint(double __x) { return (long long)lrint(__x); }
static long long llrintf(float __x) { return llrint((double)__x); }
/* AND THE WIDE ONES THROUGH THE EXACT ROUNDING ABOVE, not through double:
   `lroundl` of 2^62 has an answer and `(long)round((double)x)` is not it. */
static long lroundl(long double __x) { return (long)roundl(__x); }
static long lrintl(long double __x) { return (long)rintl(__x); }
static long long llroundl(long double __x) { return (long long)roundl(__x); }
static long long llrintl(long double __x) { return (long long)rintl(__x); }

/* THE LAST FEW C23 NAMES, which had nothing to be written in terms of. */
static double nan(const char *__tag) { (void)__tag; return __builtin_nan(""); }
static float nanf(const char *__tag) { (void)__tag; return (float)__builtin_nan(""); }
static long double nanl(const char *__tag) { (void)__tag; return __builtin_nan(""); }

static int ilogb(double __x)
{
    int __e = 0;
    /* NONE OF THE THREE HAS AN EXPONENT, so each is a domain error as well
       as a named answer -- C allows either a domain or a range error here
       and glibc chooses the domain one. */
    if (__x == 0.0) { errno = EDOM; return FP_ILOGB0; }
    if (__builtin_isnan(__x)) { errno = EDOM; return FP_ILOGBNAN; }
    if (__builtin_isinf(__x)) { errno = EDOM; return 2147483647; }
    frexp(__x, &__e);
    return __e - 1;
}
static int ilogbf(float __x) { return ilogb((double)__x); }
static double logb(double __x)
{
    if (__x == 0.0) return -__builtin_inf();
    if (__builtin_isnan(__x)) return __x;
    if (__builtin_isinf(__x)) return __builtin_inf();
    return (double)ilogb(__x);
}
static float logbf(float __x) { return (float)logb((double)__x); }

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
{
    union { float __f; unsigned int __u; } __a;
    if (__builtin_isnan(__x) || __builtin_isnan(__y)) return __x + __y;
    if (__x == __y) return __y;
    if (__x == 0.0f) {
        __a.__u = 1;
        return __y > 0.0f ? __a.__f : -__a.__f;
    }
    __a.__f = __x;
    if ((__x < __y) == (__x > 0.0f)) __a.__u++;
    else __a.__u--;
    return __a.__f;
}
static double nexttoward(double __x, long double __y)
{
    long double __lx = (long double)__x;
    if (__builtin_isnan(__x) || __ld_isnan(__y)) return __x + (double)__y;
    if (__lx == __y) return (double)__y;
    return nextafter(__x, __lx < __y ? __builtin_inf() : -__builtin_inf());
}
static float nexttowardf(float __x, long double __y)
{
    long double __lx = (long double)__x;
    if (__builtin_isnan(__x) || __ld_isnan(__y)) return __x + (float)__y;
    if (__lx == __y) return (float)__y;
    return nextafterf(__x, __lx < __y ? __builtin_inff() : -__builtin_inff());
}

static double remquo(double __x, double __y, int *__quo)
{
    unsigned long __k = 0;
    double __r, __b;
    /* THE LOW BITS OF THE QUOTIENT, which is all C promises: at least three
       of them, with the sign of x/y. */
    if (__builtin_isnan(__x) || __builtin_isnan(__y) || __y == 0.0
        || __builtin_isinf(__x)) {
        if (__quo) *__quo = 0;
        return __builtin_nan("");
    }
    if (__builtin_isinf(__y) || __x == 0.0) {
        if (__quo) *__quo = 0;
        return __x;
    }
    __b = fabs(__y);
    __r = __c_divrem(__x, __y, &__k);
    if (__r + __r > __b || (__r + __r == __b && (__k & 1))) { __r -= __b; __k++; }
    if (__quo) {
        int __n = (int)(__k & 7);
        *__quo = (__builtin_signbit(__x) != __builtin_signbit(__y)) ? -__n : __n;
    }
    return __builtin_signbit(__x) ? -__r : __r;
}
static float remquof(float __x, float __y, int *__quo)
{ return (float)remquo((double)__x, (double)__y, __quo); }

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
    /* EVERY NON-POSITIVE WHOLE NUMBER IS A POLE of the gamma function, and
       the logarithm of a pole is an infinity: ERANGE and +HUGE_VAL, for
       -0.0 as much as for -3.0. */
    if (__x <= 0.0 && __x == floor(__x))
        return __math_ran(__builtin_huge_val());
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
    double __r;
    if (__builtin_isnan(__x)) return __x;
    /* ZERO IS A POLE AND A NEGATIVE WHOLE NUMBER IS OUTSIDE THE DOMAIN --
       different errors for the same shape of argument, because at zero the
       one-sided limit is infinite and its sign is the zero's, while at -1
       the two sides go opposite ways and there is no value to return. */
    if (__x == 0.0)
        return __math_ran(__builtin_copysign(__builtin_huge_val(), __x));
    if (__x < 0.0 && __x == floor(__x)) return __math_dom(__builtin_nan(""));
    if (__x < 0.5)
        return 3.14159265358979323846
               / (sin(3.14159265358979323846 * __x) * tgamma(1.0 - __x));
    /* `lgamma` REPORTS NOTHING HERE -- its argument is positive -- so an
       infinity out of `exp` is this function's overflow to report. */
    __r = __exp_core(lgamma(__x));
    return __builtin_isinf(__r) && !__builtin_isinf(__x)
           ? __math_ran(__r) : __r;
}
__C_MATH1(lgamma)
__C_MATH1(tgamma)

#endif
