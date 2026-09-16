/* Decimal text to binary floating point, EXACTLY. Private to this directory.

   THE OTHER HALF OF `<stdio.h>`'s bignum. That one converts a double to
   decimal exactly, on the argument that every finite double IS a terminating
   decimal and that seventeen digits and a tail of zeros is not the same
   answer. This is the way back: given digits and a decimal exponent, the
   double NEAREST the value they name, with ties to even -- which is what the
   round trip needs from both ends, and what makes `strtod(buf)` recover the
   value `printf("%.17g", v)` wrote whatever that value was.

   WHY NOT SCALING, which is what the naive version did and what a small
   library usually does. `v = v * 10 + digit` and then `v /= 10^k` rounds at every
   step, and the errors accumulate: `1e23` comes out one ulp low, and so do
   about one input in a thousand near the ends of the range. The error is
   invisible until a program compares two numbers that should be equal.

   HOW IT IS EXACT, and it costs no division of one bignum by another. The
   value is D * 10^e for an integer D. With e >= 0 that IS an integer, so the
   top 54 bits of it plus one sticky bit decide the answer. With e < 0 it is
   D / 5^-e * 2^e, and the quotient's top 54 bits come from 54 rounds of
   compare-and-subtract -- long division, one bit at a time, which is all that
   is needed because a double has no more bits than that to fill.

   A HEXADECIMAL SIGNIFICAND goes through the same last step: it is already
   binary, so there is nothing to divide by and the bits are read straight
   off. `%a` in `<stdio.h>` writes them and this reads them back. */
#ifndef _ASMPYTHON_NUM_H
#define _ASMPYTHON_NUM_H

/* THE THREE SIZES, AND WHY THEY ARE THESE.

   780 DIGITS is where a decimal stops being able to sit exactly on a
   rounding boundary: the boundaries are m/2^1074 for integer m, whose
   decimal expansions terminate within 767 digits, so a longer input is
   strictly on one side and the digits past the cut only have to be known to
   be NONZERO. That is what the sticky digit is for.

   100 LIMBS of 32 bits is 3200, and the largest number that reaches one is
   5^1159 (2691 bits) or 780 digits shifted up by 54 (2712). Both fit with
   room, and a bignum that overflowed would round the wrong way silently --
   so `__big_muladd` refuses to grow past the array rather than wrapping.

   1024 CHARACTERS is what `<stdio.h>`'s `%f` scanner collects before it
   stops; a longer run of digits cannot change the answer, by the first
   paragraph. */
#define __NUM_LIMBS 100
#define __NUM_DIGITS 780
#define __NUM_TEXT_MAX 1024

typedef struct { unsigned int __d[__NUM_LIMBS]; int __n; } __numbig;

static void __big_set(__numbig *__b, unsigned int __v)
{
    __b->__d[0] = __v;
    __b->__n = __v ? 1 : 0;
}

/* b = b * m + a. The product of two 32-bit words and a 32-bit carry is
   always below 2^64, which is what makes the whole file need no wider
   arithmetic than the machine has. */
static void __big_muladd(__numbig *__b, unsigned int __m, unsigned int __a)
{
    unsigned long carry = __a;
    int i;
    for (i = 0; i < __b->__n; i++) {
        unsigned long cur = (unsigned long)__b->__d[i] * (unsigned long)__m
                          + carry;
        __b->__d[i] = (unsigned int)cur;
        carry = cur >> 32;
    }
    while (carry != 0 && __b->__n < __NUM_LIMBS) {
        __b->__d[__b->__n] = (unsigned int)carry;
        __b->__n++;
        carry >>= 32;
    }
}

static int __big_bits(const __numbig *__b)
{
    unsigned int top;
    int k = 0;
    if (__b->__n == 0) return 0;
    top = __b->__d[__b->__n - 1];
    while (top) { k++; top >>= 1; }
    return (__b->__n - 1) * 32 + k;
}

static int __big_bit(const __numbig *__b, int __i)
{
    if (__i < 0 || __i >= __b->__n * 32) return 0;
    return (int)((__b->__d[__i / 32] >> (__i % 32)) & 1u);
}

static int __big_cmp(const __numbig *__a, const __numbig *__b)
{
    int i;
    if (__a->__n != __b->__n) return __a->__n < __b->__n ? -1 : 1;
    for (i = __a->__n - 1; i >= 0; i--)
        if (__a->__d[i] != __b->__d[i])
            return __a->__d[i] < __b->__d[i] ? -1 : 1;
    return 0;
}

/* a -= b, and the caller has already established that a >= b. The borrow is
   the top half of the 64-bit difference: all ones when it wrapped. */
static void __big_sub(__numbig *__a, const __numbig *__b)
{
    unsigned long borrow = 0;
    int i;
    for (i = 0; i < __a->__n; i++) {
        unsigned long take = (i < __b->__n) ? (unsigned long)__b->__d[i] : 0UL;
        unsigned long cur = (unsigned long)__a->__d[i] - take - borrow;
        __a->__d[i] = (unsigned int)cur;
        borrow = (cur >> 32) != 0 ? 1UL : 0UL;
    }
    while (__a->__n > 0 && __a->__d[__a->__n - 1] == 0) __a->__n--;
}

static void __big_shl1(__numbig *__b)
{
    unsigned int carry = 0;
    int i;
    for (i = 0; i < __b->__n; i++) {
        unsigned int out = __b->__d[i] >> 31;
        __b->__d[i] = (__b->__d[i] << 1) | carry;
        carry = out;
    }
    if (carry && __b->__n < __NUM_LIMBS) {
        __b->__d[__b->__n] = carry;
        __b->__n++;
    }
}

static void __big_shl(__numbig *__b, int __k)
{
    int words = __k / 32, bits = __k % 32, i;
    if (__b->__n == 0 || __k <= 0) return;
    if (words > 0) {
        for (i = __b->__n - 1; i >= 0; i--)
            if (i + words < __NUM_LIMBS) __b->__d[i + words] = __b->__d[i];
        for (i = 0; i < words && i < __NUM_LIMBS; i++) __b->__d[i] = 0;
        __b->__n += words;
        if (__b->__n > __NUM_LIMBS) __b->__n = __NUM_LIMBS;
    }
    for (i = 0; i < bits; i++) __big_shl1(__b);
}

/* b *= 10^k, in chunks of nine digits because 10^9 is the largest power of
   ten below 2^32 and `__big_muladd` takes one word at a time. */
static void __big_mul_pow10(__numbig *__b, int __k)
{
    static const unsigned int __pow10[10] = {
        1u, 10u, 100u, 1000u, 10000u, 100000u, 1000000u, 10000000u,
        100000000u, 1000000000u };
    while (__k >= 9) { __big_muladd(__b, 1000000000u, 0u); __k -= 9; }
    if (__k > 0) __big_muladd(__b, __pow10[__k], 0u);
}

/* b *= 5^k, in chunks of thirteen: 5^13 is 1220703125, and 5^14 is not a
   32-bit number. */
static void __big_mul_pow5(__numbig *__b, int __k)
{
    static const unsigned int __pow5[13] = {
        1u, 5u, 25u, 125u, 625u, 3125u, 15625u, 78125u, 390625u, 1953125u,
        9765625u, 48828125u, 244140625u };
    while (__k >= 13) { __big_muladd(__b, 1220703125u, 0u); __k -= 13; }
    if (__k > 0) __big_muladd(__b, __pow5[__k], 0u);
}

/* ── the one place a double is built ──────────────────────────────────── */
/* THE VALUE IS `q * 2^e2`, with `q` carrying at most 54 significant bits and
   `sticky` saying whether anything nonzero was dropped below them. That is
   exactly the information rounding needs: the guard bit is `q`'s lowest, and
   ties go to even by looking at the bit above it.

   BUILT AS BITS RATHER THAN BY ARITHMETIC, because the subnormal range
   cannot be reached by halving: each halving of a subnormal loses its low
   bit, so a loop that scaled down would round twice and land next door. */
static double __num_make(unsigned long long __q, int __e2, int __sticky,
                         int __neg)
{
    union { double __d; unsigned long __u; } x;
    unsigned long long q = __q;
    unsigned long sign = __neg ? 0x8000000000000000UL : 0UL;
    int e2 = __e2, guard, exp;
    if (q == 0) { x.__u = sign; return x.__d; }
    /* EXACTLY 54 BITS, so that bit 0 is the guard wherever the value came
       from. A small exact integer arrives with fewer and nothing has been
       dropped, so shifting it up is free. */
    while (q < (1ULL << 53)) { q <<= 1; e2--; }
    if (e2 >= -1075) {
        guard = (int)(q & 1u);
        q >>= 1;
        e2++;
        if (guard && (__sticky || (q & 1u))) {
            q++;
            if (q == (1ULL << 53)) { q >>= 1; e2++; }
        }
        exp = e2 + 52 + 1023;             /* the biased exponent */
        if (exp >= 2047) {                /* rounded up out of range */
            x.__u = sign | 0x7FF0000000000000UL;
            return x.__d;
        }
        x.__u = sign | ((unsigned long)exp << 52)
              | (unsigned long)(q & ((1ULL << 52) - 1));
        return x.__d;
    }
    {
        /* SUBNORMAL: the step is 2^-1074 whatever the value is, so the
           rounding position is fixed and the shift is however far away from
           it `q` sits. */
        int k = -1074 - e2;
        unsigned long long m;
        if (k >= 64) { x.__u = sign; return x.__d; }
        guard = k >= 1 ? (int)((q >> (k - 1)) & 1u) : 0;
        if (k >= 2 && (q & ((1ULL << (k - 1)) - 1)) != 0) __sticky = 1;
        m = k > 0 ? (q >> k) : q;
        if (guard && (__sticky || (m & 1u))) m++;
        x.__u = sign | (unsigned long)m;   /* 2^52 here IS the smallest normal */
        return x.__d;
    }
}

/* The top 54 bits of a big integer, with everything below them collapsed
   into one sticky bit -- which is all that a correctly rounded result needs
   to know about them. */
static unsigned long long __big_top54(const __numbig *__b, int *__e2,
                                      int *__sticky)
{
    unsigned long long q = 0;
    int bits = __big_bits(__b), drop, i;
    *__sticky = 0;
    if (bits == 0) { *__e2 = 0; return 0; }
    drop = bits - 54;
    if (drop < 0) drop = 0;
    for (i = bits - 1; i >= drop; i--) q = (q << 1) | (unsigned long long)__big_bit(__b, i);
    for (i = drop - 1; i >= 0; i--)
        if (__big_bit(__b, i)) { *__sticky = 1; break; }
    *__e2 = drop;
    return q;
}

/* ── digits and an exponent, to the nearest double ────────────────────── */
static double __num_decimal(const char *__dig, int __nd, long __e10, int __neg)
{
    __numbig a, m;
    unsigned long long q;
    int e2 = 0, sticky = 0, i;
    long top;
    union { double __d; unsigned long __u; } x;
    if (__nd <= 0) {
        x.__u = __neg ? 0x8000000000000000UL : 0UL;
        return x.__d;
    }
    /* THE DECIMAL EXPONENT OF THE LEADING DIGIT decides overflow and
       underflow before any work is done -- and has to, because 10^5000 is
       not a number this bignum can hold and does not need to be. */
    top = (long)__nd - 1 + __e10;
    if (top > 309) {
        x.__u = (__neg ? 0x8000000000000000UL : 0UL) | 0x7FF0000000000000UL;
        return x.__d;
    }
    if (top < -400) {
        x.__u = __neg ? 0x8000000000000000UL : 0UL;
        return x.__d;
    }
    __big_set(&a, 0u);
    for (i = 0; i < __nd; i++) __big_muladd(&a, 10u, (unsigned int)(__dig[i] - '0'));
    if (__e10 >= 0) {
        /* AN INTEGER, so the answer is its top bits and nothing is divided. */
        __big_mul_pow10(&a, (int)__e10);
        q = __big_top54(&a, &e2, &sticky);
        return __num_make(q, e2, sticky, __neg);
    }
    {
        /* D / 5^k * 2^-k, and the quotient one bit at a time. 54 rounds of
           compare-and-subtract is a whole long division for a double, which
           is why this file needs no bignum divide. */
        int k = (int)(-__e10), scale = (int)__e10, ba, bm, s;
        __big_set(&m, 1u);
        __big_mul_pow5(&m, k);
        ba = __big_bits(&a);
        bm = __big_bits(&m);
        s = ba - bm;
        if (s > 0) { __big_shl(&m, s); scale += s; }
        else if (s < 0) { __big_shl(&a, -s); scale += s; }
        if (__big_cmp(&a, &m) < 0) { __big_shl1(&a); scale -= 1; }
        q = 0;
        for (i = 0; i < 54; i++) {
            q <<= 1;
            if (__big_cmp(&a, &m) >= 0) { __big_sub(&a, &m); q |= 1u; }
            __big_shl1(&a);
        }
        sticky = a.__n != 0;
        return __num_make(q, scale - 53, sticky, __neg);
    }
}

/* ── the C grammar for a floating-point number ────────────────────────── */
static int __num_space(int __c)
{
    return __c == ' ' || __c == '\t' || __c == '\n' || __c == '\v'
        || __c == '\f' || __c == '\r';
}

static int __num_hexval(int __c)
{
    if (__c >= '0' && __c <= '9') return __c - '0';
    if (__c >= 'a' && __c <= 'f') return __c - 'a' + 10;
    if (__c >= 'A' && __c <= 'F') return __c - 'A' + 10;
    return -1;
}

static int __num_word(const char *__p, const char *__w)
{
    int i;
    for (i = 0; __w[i]; i++)
        if ((__p[i] | 32) != __w[i]) return 0;
    return 1;
}

/* `strtod`, and `<stdlib.h>`'s is this one: the conversion belongs with the
   arithmetic above rather than being written twice, and `<stdio.h>`'s `%f`
   scanner calls it too so that the two cannot disagree about what a number
   looks like. */
static double __num_strtod(const char *__s, char **__end)
{
    const char *p = __s;
    char dig[__NUM_DIGITS + 2];
    int nd = 0, neg = 0, any = 0, dropped = 0;
    long e10 = 0;
    union { double __d; unsigned long __u; } x;
    while (__num_space((unsigned char)*p)) p++;
    if (*p == '+' || *p == '-') { neg = *p == '-'; p++; }
    if (__num_word(p, "inf")) {
        p += 3;
        if (__num_word(p, "inity")) p += 5;
        if (__end) *__end = (char *)p;
        x.__u = (neg ? 0x8000000000000000UL : 0UL) | 0x7FF0000000000000UL;
        return x.__d;
    }
    if (__num_word(p, "nan")) {
        p += 3;
        /* `nan(n-char-sequence)` is part of the grammar and the characters
           are the implementation's to interpret; this one has one quiet NaN
           and consumes them without meaning anything by them. */
        if (*p == '(') {
            const char *q = p + 1;
            while (*q && *q != ')') q++;
            if (*q == ')') p = q + 1;
        }
        if (__end) *__end = (char *)p;
        x.__u = (neg ? 0x8000000000000000UL : 0UL) | 0x7FF8000000000000UL;
        return x.__d;
    }
    if (p[0] == '0' && (p[1] == 'x' || p[1] == 'X') && __num_hexval(p[2]) >= 0) {
        /* A HEXADECIMAL SIGNIFICAND IS ALREADY BINARY: there is nothing to
           multiply or divide by, and the bits are read straight off. */
        __numbig a;
        unsigned long long q;
        int e2 = 0, sticky = 0, v, frac = 0;
        long pexp = 0;
        __big_set(&a, 0u);
        p += 2;
        while ((v = __num_hexval((unsigned char)*p)) >= 0) {
            __big_muladd(&a, 16u, (unsigned int)v);
            p++;
        }
        if (*p == '.') {
            p++;
            while ((v = __num_hexval((unsigned char)*p)) >= 0) {
                __big_muladd(&a, 16u, (unsigned int)v);
                frac += 4;
                p++;
            }
        }
        if (*p == 'p' || *p == 'P') {
            const char *save = p;
            int esign = 0;
            p++;
            if (*p == '+' || *p == '-') { esign = *p == '-'; p++; }
            if (*p >= '0' && *p <= '9') {
                while (*p >= '0' && *p <= '9') {
                    if (pexp < 100000) pexp = pexp * 10 + (*p - '0');
                    p++;
                }
                if (esign) pexp = -pexp;
            } else {
                p = save;
            }
        }
        if (__end) *__end = (char *)p;
        q = __big_top54(&a, &e2, &sticky);
        return __num_make(q, e2 + (int)(pexp - frac), sticky, neg);
    }
    while (*p == '0') { p++; any = 1; }
    while (*p >= '0' && *p <= '9') {
        any = 1;
        if (nd < __NUM_DIGITS) dig[nd++] = *p;
        else { e10++; if (*p != '0') dropped = 1; }
        p++;
    }
    if (*p == '.') {
        p++;
        if (nd == 0) {
            /* LEADING ZEROS AFTER THE POINT ARE NOT DIGITS, they are the
               exponent: `0.00123` is `123 * 10^-5`, and keeping the zeros
               would spend the digit budget on nothing. */
            while (*p == '0') { any = 1; e10--; p++; }
        }
        while (*p >= '0' && *p <= '9') {
            any = 1;
            if (nd < __NUM_DIGITS) { dig[nd++] = *p; e10--; }
            else if (*p != '0') dropped = 1;
            p++;
        }
    }
    if (!any) {
        if (__end) *__end = (char *)__s;
        return 0.0;
    }
    if (*p == 'e' || *p == 'E') {
        const char *save = p;
        int esign = 0;
        long ev = 0;
        p++;
        if (*p == '+' || *p == '-') { esign = *p == '-'; p++; }
        if (*p >= '0' && *p <= '9') {
            while (*p >= '0' && *p <= '9') {
                if (ev < 100000) ev = ev * 10 + (*p - '0');
                p++;
            }
            e10 += esign ? -ev : ev;
        } else {
            p = save;
        }
    }
    if (__end) *__end = (char *)p;
    /* THE STICKY DIGIT. Past 780 digits the value cannot sit exactly on a
       rounding boundary, so the only thing the rest of them can say is "and
       a bit more" -- which is one digit, and costs one place of exponent. */
    if (dropped && nd < __NUM_DIGITS + 1) { dig[nd++] = '1'; e10--; }
    return __num_decimal(dig, nd, e10, neg);
}

#endif
