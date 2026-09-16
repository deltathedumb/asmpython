/* <stdio.h> -- asmpython C frontend.

   OUTPUT ONLY, and the reason is the platform floor: `plat_write`,
   `plat_exit` and `plat_heap`. There is no way to READ, so every input
   function here reports end of file rather than returning something
   plausible, and `fopen` answers NULL. A program that needs input needs a
   floor with a fourth function in it, and `objects/floor.py` argues carefully
   for why there are three.

   FLOATING-POINT CONVERSION IS NOT CORRECTLY ROUNDED. The digits come from
   scaling the value into [1,10) and taking one digit at a time, which is
   accurate to about sixteen significant figures -- enough that `%f`, `%e` and
   `%g` at their usual precisions agree with a hosted libc, and not enough
   that `%.17g` always does. Said here rather than discovered later.

   ONE ENGINE, FOUR ENTRY POINTS. `printf`, `fprintf`, `snprintf` and their
   `v` forms all run `__vformat` over a sink that either buffers towards a
   descriptor or fills a caller's array -- so a padding rule cannot be right
   in one of them and wrong in another. */
#ifndef _ASMPYTHON_STDIO_H
#define _ASMPYTHON_STDIO_H

#include <stddef.h>
#include <stdarg.h>
#include <__asmpython_base.h>

#define EOF (-1)
#define BUFSIZ 512
#define FOPEN_MAX 3
#define FILENAME_MAX 260
#define L_tmpnam 20
#define SEEK_SET 0
#define SEEK_CUR 1
#define SEEK_END 2
#define _IOFBF 0
#define _IOLBF 1
#define _IONBF 2
#define TMP_MAX 1

typedef struct __FILE { int __fd; int __eof; int __err; } FILE;
typedef long fpos_t;

static FILE __stdin_file = { 0, 1, 0 };
static FILE __stdout_file = { 1, 0, 0 };
static FILE __stderr_file = { 2, 0, 0 };

#define stdin  (&__stdin_file)
#define stdout (&__stdout_file)
#define stderr (&__stderr_file)

/* ── the sink ─────────────────────────────────────────────────────────── */
#define __SINK_HOLD 256

typedef struct {
    char *__dst;        /* NULL when writing to a descriptor */
    size_t __cap;       /* room in __dst, terminator included */
    size_t __count;     /* characters the format PRODUCED, capped by nothing */
    int __fd;
    int __held;
    char __hold[__SINK_HOLD];
} __sink;

static void __sink_flush(__sink *__s)
{
    if (__s->__dst == NULL && __s->__held > 0) {
        plat_write((long)__s->__fd, __s->__hold, (long)__s->__held);
        __s->__held = 0;
    }
}

static void __sink_put(__sink *__s, char __c)
{
    if (__s->__dst != NULL) {
        /* `snprintf` RETURNS WHAT IT WOULD HAVE WRITTEN, so the count keeps
           going after the buffer is full. Truncating the count instead is
           the classic way to make a caller's "how much room do I need" loop
           spin for ever. */
        if (__s->__cap > 0 && __s->__count + 1 < __s->__cap)
            __s->__dst[__s->__count] = __c;
    } else {
        if (__s->__held >= __SINK_HOLD) __sink_flush(__s);
        __s->__hold[__s->__held++] = __c;
    }
    __s->__count++;
}

static void __sink_pad(__sink *__s, char __c, int __n)
{
    int i;
    for (i = 0; i < __n; i++) __sink_put(__s, __c);
}

static void __sink_end(__sink *__s)
{
    __sink_flush(__s);
    if (__s->__dst != NULL && __s->__cap > 0) {
        size_t at = __s->__count < __s->__cap - 1 ? __s->__count : __s->__cap - 1;
        __s->__dst[at] = 0;
    }
}

/* ── floating point to digits ─────────────────────────────────────────── */
/* EXACT, AND IT HAS TO BE. Every finite double IS a terminating decimal --
   `m * 2^e` with e >= 0 is an integer, and with e < 0 it is `m * 5^-e` with
   the point shifted -- so `printf("%f", 1e300)` has a right answer with 301
   integer digits in it, not seventeen digits and a tail of zeros. Scaling the
   value into [1,10) and taking digits off the front is the usual shortcut and
   it gets sixteen figures right; this gets all of them, which is what makes
   the output comparable with a hosted libc's rather than merely close to it.

   The arithmetic is a small base-10^9 bignum: set it to the mantissa, then
   multiply by 2^e or by 5^-e in chunks that keep every product under 2^64.
   768 digits is the worst case (`5^1074` times a 53-bit mantissa), so 96
   limbs is room to spare. */
#define __BN_LIMBS 96
#define __DIG_BUF (__BN_LIMBS * 9 + 4)

typedef struct { unsigned int __d[__BN_LIMBS]; int __n; } __bignum;

static void __bn_from(__bignum *__b, unsigned long __v)
{
    __b->__n = 0;
    while (__v != 0) {
        __b->__d[__b->__n++] = (unsigned int)(__v % 1000000000UL);
        __v /= 1000000000UL;
    }
    if (__b->__n == 0) { __b->__d[0] = 0; __b->__n = 1; }
}

static void __bn_mul(__bignum *__b, unsigned long __m)
{
    unsigned long carry = 0;
    int i;
    /* Every limb is below 10^9 and every multiplier below 2^31, so the
       product plus the carry stays below 2^64 and needs no wider type --
       which matters, because there is no wider type. */
    for (i = 0; i < __b->__n; i++) {
        unsigned long t = (unsigned long)__b->__d[i] * __m + carry;
        __b->__d[i] = (unsigned int)(t % 1000000000UL);
        carry = t / 1000000000UL;
    }
    while (carry != 0 && __b->__n < __BN_LIMBS) {
        __b->__d[__b->__n++] = (unsigned int)(carry % 1000000000UL);
        carry /= 1000000000UL;
    }
}

static int __bn_digits(const __bignum *__b, char *__out)
{
    char tmp[12];
    unsigned int v = __b->__d[__b->__n - 1];
    int len = 0, n = 0, i, j;
    if (v == 0) tmp[len++] = '0';
    while (v != 0) { tmp[len++] = (char)('0' + v % 10u); v /= 10u; }
    for (j = len; j > 0; j--) __out[n++] = tmp[j - 1];
    for (i = __b->__n - 2; i >= 0; i--) {
        unsigned int w = __b->__d[i];
        for (j = 8; j >= 0; j--) { __out[n + j] = (char)('0' + w % 10u); w /= 10u; }
        n += 9;
    }
    return n;
}

static int __float_class(double __v)
{
    /* 0 finite, 1 infinite, 2 NaN -- read off the bits, because `__v != __v`
       is the only one of the three with an arithmetic spelling. */
    union { double __d; unsigned long __u; } x;
    unsigned long bits;
    x.__d = __v;
    bits = x.__u & 0x7FFFFFFFFFFFFFFFUL;
    if (bits > 0x7FF0000000000000UL) return 2;
    if (bits == 0x7FF0000000000000UL) return 1;
    return 0;
}

static int __float_negative(double __v)
{
    union { double __d; unsigned long __u; } x;
    x.__d = __v;
    return (int)(x.__u >> 63);
}

/* The exact decimal digits of a positive finite `__v`, with `*__e10` set so
   that __v == 0.d[0]d[1]... * 10^(*__e10). Returns how many digits. */
static int __float_digits(double __v, char *__out, int *__e10)
{
    union { double __d; unsigned long __u; } x;
    __bignum b;
    unsigned long frac, m;
    int biased, e, k, nd, left;
    x.__d = __v;
    biased = (int)((x.__u >> 52) & 0x7FFUL);
    frac = x.__u & 0xFFFFFFFFFFFFFUL;
    if (biased == 0) { m = frac; e = -1074; }
    else { m = frac | 0x10000000000000UL; e = biased - 1075; }
    if (m == 0) { __out[0] = '0'; *__e10 = 1; return 1; }
    __bn_from(&b, m);
    k = 0;
    if (e >= 0) {
        left = e;
        while (left >= 29) { __bn_mul(&b, 536870912UL); left -= 29; }
        if (left > 0) __bn_mul(&b, 1UL << left);
    } else {
        left = -e;
        k = left;
        /* 5^13 is 1220703125, the largest power of five below 2^31. */
        while (left >= 13) { __bn_mul(&b, 1220703125UL); left -= 13; }
        while (left > 0) { __bn_mul(&b, 5UL); left--; }
    }
    nd = __bn_digits(&b, __out);
    *__e10 = nd - k;
    return nd;
}

/* Round the digit string to `__keep` digits. TIES GO TO EVEN, which is the
   rounding a hosted libc does because it is the FPU's default mode -- so
   `printf("%.0f", 2.5)` is `2` and not `3`. An exact tie is recognisable only
   because the digits above are exact: every digit after the fifth is a real
   zero rather than the end of an approximation. */
static int __float_round(char *__d, int __n, int __keep, int *__e10)
{
    int i, up;
    if (__keep >= __n) return __n;
    if (__keep < 0) { __d[0] = '0'; return 0; }
    if (__d[__keep] > '5') up = 1;
    else if (__d[__keep] < '5') up = 0;
    else {
        int rest = 0;
        for (i = __keep + 1; i < __n; i++) if (__d[i] != '0') { rest = 1; break; }
        if (rest) up = 1;
        else up = (__keep > 0) ? ((__d[__keep - 1] - '0') & 1) : 0;
    }
    if (!up) return __keep;
    i = __keep - 1;
    while (i >= 0) {
        if (__d[i] != '9') { __d[i]++; return __keep; }
        __d[i] = '0';
        i--;
    }
    /* Every kept digit was a nine: 999 -> 1000, one digit longer and one
       decimal exponent higher. */
    for (i = __keep; i > 0; i--) __d[i] = __d[i - 1];
    __d[0] = '1';
    *__e10 += 1;
    return __keep > 0 ? __keep : 1;
}

/* ── the formatter ────────────────────────────────────────────────────── */
#define __F_LEFT   1
#define __F_PLUS   2
#define __F_SPACE  4
#define __F_ALT    8
#define __F_ZERO  16

static void __emit_padded(__sink *__s, const char *__body, int __len,
                          const char *__prefix, int __plen, int __flags,
                          int __width, int __zeros)
{
    int pad = __width - __len - __plen - __zeros;
    if (pad < 0) pad = 0;
    if (!(__flags & __F_LEFT) && !(__flags & __F_ZERO)) __sink_pad(__s, ' ', pad);
    { int i; for (i = 0; i < __plen; i++) __sink_put(__s, __prefix[i]); }
    if (!(__flags & __F_LEFT) && (__flags & __F_ZERO)) __sink_pad(__s, '0', pad);
    __sink_pad(__s, '0', __zeros);
    { int i; for (i = 0; i < __len; i++) __sink_put(__s, __body[i]); }
    if (__flags & __F_LEFT) __sink_pad(__s, ' ', pad);
}

static int __format_float(__sink *__s, double __v, char __conv, int __prec,
                          int __flags, int __width)
{
    char digits[__DIG_BUF];
    char body[1200];
    char prefix[4];
    int plen = 0, n, e10, i, len = 0, cls, keep, expo;
    int negative = __float_negative(__v);

    if (negative) prefix[plen++] = '-';
    else if (__flags & __F_PLUS) prefix[plen++] = '+';
    else if (__flags & __F_SPACE) prefix[plen++] = ' ';

    cls = __float_class(__v);
    if (cls != 0) {
        const char *text;
        if (cls == 2) text = (__conv >= 'A' && __conv <= 'Z') ? "NAN" : "nan";
        else text = (__conv >= 'A' && __conv <= 'Z') ? "INF" : "inf";
        for (i = 0; text[i]; i++) body[len++] = text[i];
        /* A ZERO FLAG IS IGNORED FOR AN INFINITY, which is the standard's
           rule and the one that stops `%08f` printing `0000-inf`. */
        __emit_padded(__s, body, len, prefix, plen, __flags & ~__F_ZERO,
                      __width, 0);
        return 0;
    }
    if (negative) __v = -__v;
    if (__prec < 0) __prec = 6;
    if (__prec > 300) __prec = 300;

    n = __float_digits(__v, digits, &e10);
    if (__v == 0.0) e10 = 1;

    if (__conv == 'f' || __conv == 'F') {
        keep = e10 + __prec;
        if (keep > n) keep = n;
        n = __float_round(digits, n, keep, &e10);
        if (e10 <= 0) {
            body[len++] = '0';
        } else {
            for (i = 0; i < e10; i++)
                body[len++] = i < n ? digits[i] : '0';
        }
        if (__prec > 0 || (__flags & __F_ALT)) body[len++] = '.';
        for (i = 0; i < __prec; i++) {
            int at = e10 + i;
            body[len++] = (at >= 0 && at < n) ? digits[at] : '0';
        }
        __emit_padded(__s, body, len, prefix, plen, __flags, __width, 0);
        return 0;
    }
    if (__conv == 'e' || __conv == 'E') {
        n = __float_round(digits, n, __prec + 1, &e10);
        expo = e10 - 1;
        if (__v == 0.0) expo = 0;
        body[len++] = n > 0 ? digits[0] : '0';
        if (__prec > 0 || (__flags & __F_ALT)) body[len++] = '.';
        for (i = 1; i <= __prec; i++) body[len++] = i < n ? digits[i] : '0';
        body[len++] = (__conv == 'E') ? 'E' : 'e';
        body[len++] = expo < 0 ? '-' : '+';
        if (expo < 0) expo = -expo;
        if (expo >= 100) {
            body[len++] = (char)('0' + expo / 100);
            body[len++] = (char)('0' + (expo / 10) % 10);
            body[len++] = (char)('0' + expo % 10);
        } else {
            body[len++] = (char)('0' + expo / 10);
            body[len++] = (char)('0' + expo % 10);
        }
        __emit_padded(__s, body, len, prefix, plen, __flags, __width, 0);
        return 0;
    }
    /* %g and %G: the standard's own rule, written as it is written. */
    {
        int prec = __prec == 0 ? 1 : __prec;
        int keepg, x, alt = __flags & __F_ALT;
        n = __float_round(digits, n, prec, &e10);
        x = (__v == 0.0) ? 0 : e10 - 1;
        if (x < -4 || x >= prec) {
            int p = prec - 1;
            expo = x;
            body[len++] = n > 0 ? digits[0] : '0';
            keepg = p;
            if (!alt) { while (keepg > 0 && (keepg >= n || digits[keepg] == '0')) keepg--; }
            if (keepg > 0 || alt) body[len++] = '.';
            for (i = 1; i <= (alt ? p : keepg); i++)
                body[len++] = i < n ? digits[i] : '0';
            body[len++] = (__conv == 'G') ? 'E' : 'e';
            body[len++] = expo < 0 ? '-' : '+';
            if (expo < 0) expo = -expo;
            if (expo >= 100) {
                body[len++] = (char)('0' + expo / 100);
                body[len++] = (char)('0' + (expo / 10) % 10);
                body[len++] = (char)('0' + expo % 10);
            } else {
                body[len++] = (char)('0' + expo / 10);
                body[len++] = (char)('0' + expo % 10);
            }
        } else {
            int p = prec - 1 - x;
            if (e10 <= 0) body[len++] = '0';
            else for (i = 0; i < e10; i++) body[len++] = i < n ? digits[i] : '0';
            keepg = p;
            if (!alt) {
                while (keepg > 0) {
                    int at = e10 + keepg - 1;
                    if (at < n && digits[at] != '0') break;
                    keepg--;
                }
            }
            if (keepg > 0 || alt) body[len++] = '.';
            for (i = 0; i < (alt ? p : keepg); i++) {
                int at = e10 + i;
                body[len++] = (at >= 0 && at < n) ? digits[at] : '0';
            }
        }
        __emit_padded(__s, body, len, prefix, plen, __flags, __width, 0);
    }
    return 0;
}

static int __vformat(__sink *__s, const char *__fmt, va_list __ap)
{
    const char *p = __fmt;
    char body[1200];
    char prefix[4];
    while (*p) {
        int flags = 0, width = 0, prec = -1, len = 0, plen = 0, zeros = 0;
        int lmod = 0;           /* 0 int, 1 long, 2 long long, -1 short, -2 char */
        char conv;
        unsigned long uv;
        long sv;
        int negative = 0;
        const char *base_digits = "0123456789abcdef";
        int base = 10;

        if (*p != '%') { __sink_put(__s, *p); p++; continue; }
        p++;
        if (*p == '%') { __sink_put(__s, '%'); p++; continue; }
        for (;;) {
            if (*p == '-') flags |= __F_LEFT;
            else if (*p == '+') flags |= __F_PLUS;
            else if (*p == ' ') flags |= __F_SPACE;
            else if (*p == '#') flags |= __F_ALT;
            else if (*p == '0') flags |= __F_ZERO;
            else break;
            p++;
        }
        if (*p == '*') {
            width = va_arg(__ap, int);
            if (width < 0) { flags |= __F_LEFT; width = -width; }
            p++;
        } else {
            while (*p >= '0' && *p <= '9') { width = width * 10 + (*p - '0'); p++; }
        }
        if (*p == '.') {
            p++;
            prec = 0;
            if (*p == '*') { prec = va_arg(__ap, int); p++; if (prec < 0) prec = -1; }
            else while (*p >= '0' && *p <= '9') { prec = prec * 10 + (*p - '0'); p++; }
        }
        if (*p == 'h') { p++; lmod = -1; if (*p == 'h') { p++; lmod = -2; } }
        else if (*p == 'l') { p++; lmod = 1; if (*p == 'l') { p++; lmod = 2; } }
        else if (*p == 'z' || *p == 't' || *p == 'j' || *p == 'L') { p++; lmod = 1; }
        conv = *p;
        if (conv == 0) break;
        p++;

        if (conv == 'd' || conv == 'i') {
            if (lmod >= 1) sv = va_arg(__ap, long);
            else sv = (long)va_arg(__ap, int);
            if (lmod == -1) sv = (long)(short)sv;
            else if (lmod == -2) sv = (long)(signed char)sv;
            if (sv < 0) { negative = 1; uv = (unsigned long)(-(sv + 1)) + 1UL; }
            else uv = (unsigned long)sv;
        } else if (conv == 'u' || conv == 'o' || conv == 'x' || conv == 'X') {
            if (lmod >= 1) uv = va_arg(__ap, unsigned long);
            else uv = (unsigned long)va_arg(__ap, unsigned int);
            if (lmod == -1) uv &= 0xFFFFUL;
            else if (lmod == -2) uv &= 0xFFUL;
            if (conv == 'o') base = 8;
            else if (conv != 'u') base = 16;
            if (conv == 'X') base_digits = "0123456789ABCDEF";
        } else if (conv == 'c') {
            body[len++] = (char)va_arg(__ap, int);
            __emit_padded(__s, body, len, prefix, 0, flags & ~__F_ZERO, width, 0);
            continue;
        } else if (conv == 's') {
            const char *str = va_arg(__ap, const char *);
            if (str == 0) str = "(null)";
            while (str[len] && (prec < 0 || len < prec)) { body[len] = str[len]; len++; }
            __emit_padded(__s, body, len, prefix, 0, flags & ~__F_ZERO, width, 0);
            continue;
        } else if (conv == 'p') {
            void *ptr = va_arg(__ap, void *);
            uv = (unsigned long)ptr;
            if (uv == 0) {
                const char *nil = "(nil)";
                while (nil[len]) { body[len] = nil[len]; len++; }
                __emit_padded(__s, body, len, prefix, 0, flags & ~__F_ZERO, width, 0);
                continue;
            }
            base = 16;
            prefix[plen++] = '0';
            prefix[plen++] = 'x';
        } else if (conv == 'f' || conv == 'F' || conv == 'e' || conv == 'E'
                   || conv == 'g' || conv == 'G') {
            __format_float(__s, va_arg(__ap, double), conv, prec, flags, width);
            continue;
        } else if (conv == 'n') {
            int *where = va_arg(__ap, int *);
            if (where) *where = (int)__s->__count;
            continue;
        } else {
            /* An unknown conversion prints as written, which is what makes a
               stray `%` in a message survive instead of eating its argument. */
            __sink_put(__s, '%');
            __sink_put(__s, conv);
            continue;
        }

        /* The integer conversions share this tail. */
        if (negative) prefix[plen++] = '-';
        else if (conv != 'u' && conv != 'o' && conv != 'x' && conv != 'X'
                 && conv != 'p') {
            if (flags & __F_PLUS) prefix[plen++] = '+';
            else if (flags & __F_SPACE) prefix[plen++] = ' ';
        }
        if ((flags & __F_ALT) && base == 16 && uv != 0 && conv != 'p') {
            prefix[plen++] = '0';
            prefix[plen++] = (conv == 'X') ? 'X' : 'x';
        }
        {
            char tmp[72];
            int tn = 0, i;
            if (uv == 0 && prec != 0) tmp[tn++] = '0';
            while (uv) { tmp[tn++] = base_digits[uv % (unsigned long)base];
                         uv /= (unsigned long)base; }
            if ((flags & __F_ALT) && base == 8 && (tn == 0 || tmp[tn - 1] != '0'))
                tmp[tn++] = '0';
            for (i = 0; i < tn; i++) body[i] = tmp[tn - 1 - i];
            len = tn;
        }
        if (prec >= 0) {
            zeros = prec - len;
            if (zeros < 0) zeros = 0;
            /* A PRECISION TURNS THE ZERO FLAG OFF for an integer -- 6.5.2.1
               again -- so `%08.3d` is `     007` and not `00000007`. */
            flags &= ~__F_ZERO;
        }
        __emit_padded(__s, body, len, prefix, plen, flags, width, zeros);
    }
    __sink_end(__s);
    return (int)__s->__count;
}

static __sink __make_fd_sink(int __fd)
{
    __sink s;
    s.__dst = NULL; s.__cap = 0; s.__count = 0; s.__fd = __fd; s.__held = 0;
    return s;
}

static int vfprintf(FILE *__f, const char *__fmt, va_list __ap)
{
    __sink s = __make_fd_sink(__f ? __f->__fd : 1);
    return __vformat(&s, __fmt, __ap);
}

static int vprintf(const char *__fmt, va_list __ap)
{
    __sink s = __make_fd_sink(1);
    return __vformat(&s, __fmt, __ap);
}

static int vsnprintf(char *__buf, size_t __n, const char *__fmt, va_list __ap)
{
    __sink s;
    s.__dst = __buf; s.__cap = __n; s.__count = 0; s.__fd = -1; s.__held = 0;
    return __vformat(&s, __fmt, __ap);
}

static int vsprintf(char *__buf, const char *__fmt, va_list __ap)
{
    return vsnprintf(__buf, (size_t)-1, __fmt, __ap);
}

static int printf(const char *__fmt, ...)
{
    va_list ap; int n;
    va_start(ap, __fmt);
    n = vprintf(__fmt, ap);
    va_end(ap);
    return n;
}

static int fprintf(FILE *__f, const char *__fmt, ...)
{
    va_list ap; int n;
    va_start(ap, __fmt);
    n = vfprintf(__f, __fmt, ap);
    va_end(ap);
    return n;
}

static int snprintf(char *__buf, size_t __n, const char *__fmt, ...)
{
    va_list ap; int n;
    va_start(ap, __fmt);
    n = vsnprintf(__buf, __n, __fmt, ap);
    va_end(ap);
    return n;
}

static int sprintf(char *__buf, const char *__fmt, ...)
{
    va_list ap; int n;
    va_start(ap, __fmt);
    n = vsprintf(__buf, __fmt, ap);
    va_end(ap);
    return n;
}

/* ── the small output functions ───────────────────────────────────────── */
static int fputc(int __c, FILE *__f)
{
    char b = (char)__c;
    plat_write((long)(__f ? __f->__fd : 1), &b, 1);
    return (unsigned char)b;
}
static int putc(int __c, FILE *__f) { return fputc(__c, __f); }
static int putchar(int __c) { return fputc(__c, stdout); }

static int fputs(const char *__s, FILE *__f)
{
    long n = 0;
    while (__s[n]) n++;
    plat_write((long)(__f ? __f->__fd : 1), __s, n);
    return 0;
}

static int puts(const char *__s)
{
    fputs(__s, stdout);
    fputc('\n', stdout);
    return 0;
}

static size_t fwrite(const void *__p, size_t __size, size_t __n, FILE *__f)
{
    long got;
    if (__size == 0 || __n == 0) return 0;
    got = plat_write((long)(__f ? __f->__fd : 1), __p, (long)(__size * __n));
    if (got < 0) return 0;
    return (size_t)got / __size;
}

static int fflush(FILE *__f) { (void)__f; return 0; }
static void setbuf(FILE *__f, char *__b) { (void)__f; (void)__b; }
static int setvbuf(FILE *__f, char *__b, int __mode, size_t __n)
{ (void)__f; (void)__b; (void)__mode; (void)__n; return 0; }

/* ── the input that is not there ──────────────────────────────────────── */
static int fgetc(FILE *__f) { if (__f) __f->__eof = 1; return EOF; }
static int getc(FILE *__f) { return fgetc(__f); }
static int getchar(void) { return EOF; }
static char *fgets(char *__s, int __n, FILE *__f)
{ (void)__n; (void)__f; if (__s && __n > 0) __s[0] = 0; return NULL; }
static size_t fread(void *__p, size_t __size, size_t __n, FILE *__f)
{ (void)__p; (void)__size; (void)__n; if (__f) __f->__eof = 1; return 0; }
static int ungetc(int __c, FILE *__f) { (void)__c; (void)__f; return EOF; }

static FILE *fopen(const char *__path, const char *__mode)
{ (void)__path; (void)__mode; return NULL; }
static FILE *freopen(const char *__path, const char *__mode, FILE *__f)
{ (void)__path; (void)__mode; (void)__f; return NULL; }
static int fclose(FILE *__f) { (void)__f; return 0; }
static int feof(FILE *__f) { return __f ? __f->__eof : 1; }
static int ferror(FILE *__f) { return __f ? __f->__err : 0; }
static void clearerr(FILE *__f) { if (__f) { __f->__eof = 0; __f->__err = 0; } }
static int fseek(FILE *__f, long __off, int __whence)
{ (void)__f; (void)__off; (void)__whence; return -1; }
static long ftell(FILE *__f) { (void)__f; return -1L; }
static void rewind(FILE *__f) { (void)__f; }
static int remove(const char *__p) { (void)__p; return -1; }
static int rename(const char *__a, const char *__b) { (void)__a; (void)__b; return -1; }
static void perror(const char *__s)
{
    if (__s && __s[0]) { fputs(__s, stderr); fputs(": ", stderr); }
    fputs("no error information is available\n", stderr);
}

#endif
