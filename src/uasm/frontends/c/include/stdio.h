/* <stdio.h> -- uasm C frontend.

   TWO LAYERS UNDERNEATH, AND A PROGRAM PAYS FOR THE SECOND ONLY IF IT ASKS.
   Writing to `stdout` and `stderr` reaches `plat_write`, which is the
   platform floor every backend owes -- so hello world runs on a target with
   no filesystem at all, as it always did. Everything else -- `fopen`, and
   every read, `stdin` included -- reaches `objects/hostsvc.py`'s `file`
   group, and `Backend.check_host_services` refuses a program that calls into
   it on a backend whose target has none. `lower._prune` drops the
   declarations nothing reaches, which is what keeps the first sentence true.

   WHY `fopen` AND NOT `fputs` IS THE DOOR. `fputs(s, stdout)` and
   `fputs(s, f)` are the same call, and which one a program makes is not
   knowable until it runs -- so the write path reaches the host through a
   POINTER that only `fopen`, `freopen` and `tmpfile` ever set. Nothing else
   mentions `host_file_write`, so nothing else drags the group in. Reading is
   not arranged that way and does not need to be: a program that reads has
   asked for a file, whichever descriptor it reads.

   FLOATING-POINT CONVERSION IS EXACT. `__format_float` converts through a
   base-10^9 bignum and rounds ties to even, so `%.17g` and `%f` of a large
   value agree with a hosted libc digit for digit rather than nearly. The
   comment on `__bn_from` says how.

   ONE ENGINE, FOUR ENTRY POINTS, TWICE. `printf`, `fprintf`, `snprintf` and
   their `v` forms all run `__vformat` over a sink that either buffers towards
   a handle or fills a caller's array; `scanf`, `fscanf` and `sscanf` all run
   `__vscan` over a source that is either a stream or a string. A padding rule
   or a conversion cannot be right in one of them and wrong in another. */
#ifndef _UASM_STDIO_H
#define _UASM_STDIO_H

#include <stddef.h>
#include <stdarg.h>
#include <errno.h>
#include <string.h>
#include <__uasm_num.h>
#include <__uasm_base.h>
#include <__uasm_host.h>

#define EOF (-1)
#define BUFSIZ 512
#define FOPEN_MAX 16
#define FILENAME_MAX 260
#define L_tmpnam 20
#define SEEK_SET 0
#define SEEK_CUR 1
#define SEEK_END 2
#define _IOFBF 0
#define _IONBF 2
#define _IOLBF 1
#define TMP_MAX 4096

/* THE HANDLE IS THE HOST'S, and 0, 1 and 2 are the standard three -- the
   same numbering `plat_write` uses, which is not a coincidence:
   `objects/hostsvc.py` fixed it so that a program cannot end up with two
   numbering schemes for one descriptor and interleaved output nobody can
   explain.

   READS ARE BUFFERED AND WRITES ARE NOT. A read from the host is a call per
   `BUFSIZ` rather than per character, which is the difference between
   `fgetc` in a loop being usable and being a joke; a write goes straight
   out, which is what makes `printf` to a terminal appear when it was
   printed and `fflush` a no-op that is honest. `__vformat` does its own
   batching into 256-byte holds, so a formatted line is still one call. */
typedef struct __FILE {
    long __h;              /* the host handle; 0, 1, 2 are the three     */
    int __eof;
    int __err;
    int __append;          /* opened "a": every write goes to the end    */
    int __used;            /* this slot is open; see `__file_slot`       */
    int __tmp;             /* `tmpfile`: remove it on close              */
    int __rd, __rn;        /* the read buffer: cursor, and bytes in it   */
    char __name[L_tmpnam]; /* `tmpfile`'s, so close can remove it        */
    char __rbuf[BUFSIZ];
} FILE;

/* A POSITION IS A BYTE OFFSET, which is what the host's seek takes and
   answers. C allows `fpos_t` to be anything a program only ever passes back;
   a long is the honest spelling of what this one is. */
typedef long fpos_t;

static FILE __stdin_file = { 0, 0, 0, 0, 1, 0, 0, 0, { 0 }, { 0 } };
static FILE __stdout_file = { 1, 0, 0, 0, 1, 0, 0, 0, { 0 }, { 0 } };
static FILE __stderr_file = { 2, 0, 0, 0, 1, 0, 0, 0, { 0 }, { 0 } };

#define stdin  (&__stdin_file)
#define stdout (&__stdout_file)
#define stderr (&__stderr_file)

/* ── the way out ──────────────────────────────────────────────────────── */
/* THE POINTER THE HEADER COMMENT IS ABOUT. It is null until `fopen` runs,
   and every write to a handle above 2 goes through it -- so a program that
   never opens a file never mentions `host_file_write`, and the `file` group
   is not among the host services its module asks for. The three seek and
   close hooks are set at the same moment and for the same reason. */
static long (*__host_write_fn)(long, const void *, long);
static long (*__host_seek_fn)(long, long, long);
static long (*__host_close_fn)(long);

static void __file_hooks(void)
{
    __host_write_fn = host_file_write;
    __host_seek_fn = host_file_seek;
    __host_close_fn = host_file_close;
}

/* EVERY BYTE THIS LIBRARY WRITES LEAVES THROUGH HERE. The standard three go
   to the floor, which every backend has; anything else is a file, and a file
   without the hooks set cannot exist -- the only thing that hands out a
   handle above 2 is the function that sets them. */
static long __file_out(long __h, const void *__p, long __n)
{
    if (__h >= 0 && __h <= 2) return plat_write(__h, __p, __n);
    if (__host_write_fn == 0) return -1;
    return __host_write_fn(__h, __p, __n);
}

/* ── the sink ─────────────────────────────────────────────────────────── */
#define __SINK_HOLD 256

typedef struct {
    char *__dst;        /* NULL when writing to a stream */
    size_t __cap;       /* room in __dst, terminator included */
    size_t __count;     /* characters the format PRODUCED, capped by nothing */
    long __h;           /* the host handle, when __dst is NULL */
    int __held;
    char __hold[__SINK_HOLD];
} __sink;

static void __sink_flush(__sink *__s)
{
    if (__s->__dst == NULL && __s->__held > 0) {
        __file_out(__s->__h, __s->__hold, (long)__s->__held);
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

/* ── positioning a stream before a write ──────────────────────────────── */
/* AN UPDATE STREAM READS AHEAD, so the host's position is past the one the
   program has seen, and a write would land in the wrong place. C requires a
   positioning call between a read and a write on such a stream; doing it
   here as well costs one branch and turns undefined behaviour into the
   obvious answer.

   AN APPEND STREAM WRITES AT THE END, always, whatever the position is --
   which the host's open modes cannot express for a stream that also reads
   ("ab" is write-only), so `fopen` records it and this enforces it. */
static void __stream_prepare(FILE *__f)
{
    if (__f == NULL || __f->__h <= 2) return;
    if (__f->__rn > __f->__rd && __host_seek_fn != 0)
        __host_seek_fn(__f->__h, -(long)(__f->__rn - __f->__rd),
                       __HOST_SEEK_CUR);
    __f->__rd = 0;
    __f->__rn = 0;
    if (__f->__append && __host_seek_fn != 0)
        __host_seek_fn(__f->__h, 0, __HOST_SEEK_END);
}

static long __stream_write(FILE *__f, const void *__p, long __n)
{
    __stream_prepare(__f);
    return __file_out(__f ? __f->__h : 1, __p, __n);
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
    if (__conv == 'a' || __conv == 'A') {
        /* THE HEXADECIMAL FORM, WHICH IS THE VALUE ITSELF. `%a` exists so
           that a double can be written down and read back with nothing
           lost, and it can because the significand is already binary: four
           bits to a digit, no conversion, no rounding unless a precision
           asks for one. `__uasm_num.h` reads it back.

           A SUBNORMAL LEADS WITH 0 AND KEEPS THE EXPONENT -1022, which is
           what makes the form continuous across the boundary -- the
           alternative is a leading 1 and an exponent below the smallest a
           normal can have, which no reader expects. */
        union { double __d; unsigned long __u; } bits;
        unsigned long frac, keep;
        const char *hexd = (__conv == 'A') ? "0123456789ABCDEF"
                                           : "0123456789abcdef";
        int expo2, lead, nib, i;
        bits.__d = __v;
        frac = bits.__u & 0xFFFFFFFFFFFFFUL;
        expo2 = (int)((bits.__u >> 52) & 0x7FFUL);
        if (expo2 == 0) { lead = 0; expo2 = (frac == 0) ? 0 : -1022; }
        else { lead = 1; expo2 -= 1023; }
        nib = 13;                          /* 52 bits, four to a digit */
        keep = frac;
        if (__prec >= 0 && __prec < 13) {
            int drop = 52 - 4 * __prec;
            unsigned long rest = frac & ((1UL << drop) - 1UL);
            unsigned long half = 1UL << (drop - 1);
            keep = frac >> drop;
            /* TIES TO EVEN, which is the rounding every other conversion
               here uses and what the default floating-point environment
               would have done. */
            if (rest > half || (rest == half && (keep & 1UL)))
                keep++;
            if (__prec == 0) {
                if (keep) { lead++; keep = 0; }
            } else if (keep >> (4 * __prec)) {
                keep = 0;
                lead++;
            }
            nib = __prec;
        } else if (__prec < 0) {
            /* NO PRECISION MEANS EXACTLY ENOUGH, so the trailing zeros that
               carry no information come off. */
            while (nib > 0 && ((keep >> (4 * (13 - nib))) & 0xFUL) == 0) nib--;
            /* THE DIGITS MOVE DOWN TO WHERE THE LOOP BELOW LOOKS FOR THEM:
               a rounded significand has already been shifted, so the two
               paths have to agree about where the last digit is. */
            keep >>= 4 * (13 - nib);
        } else {
            nib = 13;
            /* A precision beyond thirteen digits is zeros: the value has no
               more bits. */
        }
        prefix[plen++] = '0';
        prefix[plen++] = (__conv == 'A') ? 'X' : 'x';
        body[len++] = (char)('0' + lead);
        if (nib > 0 || (__prec > 0) || (__flags & __F_ALT)) body[len++] = '.';
        for (i = 0; i < nib && i < 13; i++)
            body[len++] = hexd[(keep >> (4 * (nib - 1 - i))) & 0xFUL];
        for (i = 13; i < __prec; i++) body[len++] = '0';
        body[len++] = (__conv == 'A') ? 'P' : 'p';
        body[len++] = expo2 < 0 ? '-' : '+';
        {
            int e = expo2 < 0 ? -expo2 : expo2, at = len, j;
            char tmp[8];
            int tn = 0;
            do { tmp[tn++] = (char)('0' + e % 10); e /= 10; } while (e);
            for (j = tn - 1; j >= 0; j--) body[at++] = tmp[j];
            len = at;
        }
        __emit_padded(__s, body, len, prefix, plen, __flags, __width, 0);
        return 0;
    }
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
                   || conv == 'g' || conv == 'G' || conv == 'a'
                   || conv == 'A') {
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

static __sink __make_stream_sink(long __h)
{
    __sink s;
    s.__dst = NULL; s.__cap = 0; s.__count = 0; s.__h = __h; s.__held = 0;
    return s;
}

static int vfprintf(FILE *__f, const char *__fmt, va_list __ap)
{
    __sink s;
    __stream_prepare(__f);
    s = __make_stream_sink(__f ? __f->__h : 1);
    return __vformat(&s, __fmt, __ap);
}

static int vprintf(const char *__fmt, va_list __ap)
{
    __sink s = __make_stream_sink(1);
    return __vformat(&s, __fmt, __ap);
}

static int vsnprintf(char *__buf, size_t __n, const char *__fmt, va_list __ap)
{
    __sink s;
    s.__dst = __buf; s.__cap = __n; s.__count = 0; s.__h = -1; s.__held = 0;
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
    if (__stream_write(__f, &b, 1) < 0) { if (__f) __f->__err = 1; return EOF; }
    return (unsigned char)b;
}
static int putc(int __c, FILE *__f) { return fputc(__c, __f); }
static int putchar(int __c) { return fputc(__c, stdout); }

static int fputs(const char *__s, FILE *__f)
{
    long n = 0;
    while (__s[n]) n++;
    if (__stream_write(__f, __s, n) < 0) { if (__f) __f->__err = 1; return EOF; }
    return 0;
}

static int puts(const char *__s)
{
    if (fputs(__s, stdout) == EOF) return EOF;
    return fputc('\n', stdout) == EOF ? EOF : 0;
}

static size_t fwrite(const void *__p, size_t __size, size_t __n, FILE *__f)
{
    long got;
    if (__size == 0 || __n == 0) return 0;
    got = __stream_write(__f, __p, (long)(__size * __n));
    if (got < 0) { if (__f) __f->__err = 1; return 0; }
    return (size_t)got / __size;
}

/* NOTHING IS HELD, so there is nothing to flush: a write leaves through
   `plat_write` or the host as it is made. Answering 0 is therefore the truth
   rather than a stub -- every byte a program has written really is out. */
static int fflush(FILE *__f) { (void)__f; return 0; }
static void setbuf(FILE *__f, char *__b) { (void)__f; (void)__b; }
/* THE BUFFERING A PROGRAM ASKS FOR IS WHAT IT ALREADY HAS on the writing
   side, and the reading side's buffer is inside the FILE rather than in the
   caller's array. Accepting the request and answering success is what a
   hosted libc does for a mode it cannot honour exactly. */
static int setvbuf(FILE *__f, char *__b, int __mode, size_t __n)
{ (void)__f; (void)__b; (void)__mode; (void)__n; return 0; }

/* ── opening a file ───────────────────────────────────────────────────── */
/* A STATIC POOL AND NOT `malloc`, which keeps this header independent of
   `<stdlib.h>` -- a program that opens a file gets the pool and nothing
   else, and a program that does not gets neither. FOPEN_MAX is what C
   promises a program may have open at once and is what this is; a hosted
   libc has a limit too, it is just further away. */
static FILE __file_pool[FOPEN_MAX];

static FILE *__file_slot(void)
{
    int i;
    for (i = 0; i < FOPEN_MAX; i++)
        if (!__file_pool[i].__used) {
            FILE *f = &__file_pool[i];
            f->__h = -1; f->__eof = 0; f->__err = 0; f->__append = 0;
            f->__used = 1; f->__tmp = 0; f->__rd = 0; f->__rn = 0;
            f->__name[0] = 0;
            return f;
        }
    return NULL;
}

/* THE MODE STRING, WHICH IS FOUR QUESTIONS AND NOT ONE. `b` is accepted and
   ignored because every stream here is binary already -- newline translation
   is a property of text, and `objects/hostsvc.py` says at length why the
   layer underneath will not do it. `x` is C11's exclusive create. The `+`
   forms need the update mode, and `w+` and `a+` need it after something else
   has happened to the file -- see `fopen`. */
static int __mode_read(const char *__m, int *__plus, int *__excl)
{
    int i;
    *__plus = 0;
    *__excl = 0;
    if (__m == NULL || (__m[0] != 'r' && __m[0] != 'w' && __m[0] != 'a'))
        return -1;
    for (i = 1; __m[i]; i++) {
        if (__m[i] == '+') *__plus = 1;
        else if (__m[i] == 'x') *__excl = 1;
        else if (__m[i] != 'b') return -1;
    }
    return __m[0];
}

static long __path_len(const char *__p)
{
    long n = 0;
    while (__p[n]) n++;
    return n;
}

/* THE ONE PLACE A HANDLE ABOVE 2 COMES FROM, which is what makes the
   header's promise about `host_file_write` true: the hooks are set here and
   nowhere else, so a program that never opens a file never names the `file`
   group's writing half. */
static FILE *__file_open(const char *__path, const char *__mode, FILE *__into)
{
    int plus, excl, kind;
    long h, n;
    FILE *f;
    kind = __mode_read(__mode, &plus, &excl);
    if (kind < 0 || __path == NULL) { errno = EINVAL; return NULL; }
    n = __path_len(__path);
    __file_hooks();
    if (excl) {
        /* EXCLUSIVE CREATE, ASKED AS A QUESTION AND THEN DONE, because the
           open modes have no `O_EXCL`. The window between the two is real
           and a hosted libc does not have it; a program that needs the
           atomic form needs a host service that offers one. */
        if (kind != 'w' || host_file_kind(__path, n) != __HOST_KIND_MISSING) {
            errno = EEXIST;
            return NULL;
        }
    }
    if (kind == 'r') {
        h = host_file_open(__path, n, plus ? __HOST_OPEN_UPDATE
                                           : __HOST_OPEN_READ);
    } else if (kind == 'w') {
        /* `w+` IS TRUNCATE AND THEN READ-WRITE, and the host has no single
           mode for it: `HOST_OPEN_WRITE` is write-only. Truncating with it
           and reopening for update is what the two modes together mean. */
        h = host_file_open(__path, n, __HOST_OPEN_WRITE);
        if (plus && h >= 0) {
            host_file_close(h);
            h = host_file_open(__path, n, __HOST_OPEN_UPDATE);
        }
    } else {
        /* `a` AND `a+`. Update is the mode that can also read, and the
           append rule -- every write goes to the end, whatever the position
           -- is enforced by `__stream_prepare` rather than by the host. The
           file has to exist first, which is what the create is for. */
        h = host_file_open(__path, n, __HOST_OPEN_UPDATE);
        if (h < 0) {
            long made = host_file_open(__path, n, __HOST_OPEN_WRITE);
            if (made >= 0) {
                host_file_close(made);
                h = host_file_open(__path, n, __HOST_OPEN_UPDATE);
            }
        }
    }
    if (h < 0) { errno = __host_errno(h); return NULL; }
    f = __into ? __into : __file_slot();
    if (f == NULL) { host_file_close(h); errno = EMFILE; return NULL; }
    f->__h = h;
    f->__eof = 0; f->__err = 0; f->__rd = 0; f->__rn = 0;
    f->__append = kind == 'a';
    f->__used = 1;
    if (f->__append) host_file_seek(h, 0, __HOST_SEEK_END);
    return f;
}

static FILE *fopen(const char *__path, const char *__mode)
{
    return __file_open(__path, __mode, NULL);
}

static int fclose(FILE *__f)
{
    long h;
    if (__f == NULL) return EOF;
    h = __f->__h;
    if (__f->__tmp && __f->__name[0]) host_file_remove(__f->__name,
                                                       __path_len(__f->__name));
    __f->__used = __f == stdin || __f == stdout || __f == stderr;
    __f->__rd = 0; __f->__rn = 0; __f->__h = -1;
    if (h <= 2) return 0;                /* never close the standard three */
    if (__host_close_fn == 0) return 0;
    return __host_close_fn(h) < 0 ? EOF : 0;
}

static FILE *freopen(const char *__path, const char *__mode, FILE *__f)
{
    if (__f == NULL) return NULL;
    if (__path == NULL) {
        /* THE "SAME FILE, NEW MODE" FORM, which C says may fail and which
           this one does -- BEFORE closing anything, so a program whose
           `freopen` is refused still has the stream it had. The host hands
           out a handle and not a path, so there is nothing to reopen. */
        errno = ENOSYS;
        return NULL;
    }
    if (__f->__h > 2 && __host_close_fn != 0) __host_close_fn(__f->__h);
    __f->__rd = 0; __f->__rn = 0; __f->__eof = 0; __f->__err = 0;
    return __file_open(__path, __mode, __f);
}

/* ── reading ──────────────────────────────────────────────────────────── */
/* ONE HOST CALL PER BUFFER, not per character. `fgetc` in a loop is how most
   C reads a file, and a call into the host for each byte would make it
   unusable for anything larger than a line. */
static int __refill(FILE *__f)
{
    long got;
    if (__f == NULL || __f->__eof || __f->__err) return 0;
    got = host_file_read(__f->__h, __f->__rbuf, (long)BUFSIZ);
    if (got < 0) { __f->__err = 1; errno = __host_errno(got); return 0; }
    if (got == 0) { __f->__eof = 1; return 0; }
    __f->__rd = 0;
    __f->__rn = (int)got;
    return 1;
}

static int fgetc(FILE *__f)
{
    if (__f == NULL) return EOF;
    if (__f->__rd >= __f->__rn && !__refill(__f)) return EOF;
    return (unsigned char)__f->__rbuf[__f->__rd++];
}
static int getc(FILE *__f) { return fgetc(__f); }
static int getchar(void) { return fgetc(stdin); }

/* ONE CHARACTER OF PUSHBACK, which is all C promises. It goes back into the
   buffer the character came out of -- there is always room, because the
   cursor only moves forward over bytes that are already there. */
static int ungetc(int __c, FILE *__f)
{
    if (__f == NULL || __c == EOF) return EOF;
    if (__f->__rd > 0) {
        __f->__rbuf[--__f->__rd] = (char)__c;
    } else if (__f->__rn < BUFSIZ) {
        int i;
        for (i = __f->__rn; i > 0; i--) __f->__rbuf[i] = __f->__rbuf[i - 1];
        __f->__rbuf[0] = (char)__c;
        __f->__rn++;
    } else {
        return EOF;
    }
    __f->__eof = 0;
    return (unsigned char)__c;
}

static char *fgets(char *__s, int __n, FILE *__f)
{
    int i = 0, c;
    if (__s == NULL || __n <= 0 || __f == NULL) return NULL;
    while (i < __n - 1) {
        c = fgetc(__f);
        if (c == EOF) break;
        __s[i++] = (char)c;
        if (c == '\n') break;
    }
    if (i == 0) { __s[0] = 0; return NULL; }
    __s[i] = 0;
    return __s;
}

static size_t fread(void *__p, size_t __size, size_t __n, FILE *__f)
{
    char *out = (char *)__p;
    size_t want, done = 0;
    long got;
    if (__f == NULL || __size == 0 || __n == 0) return 0;
    want = __size * __n;
    /* WHAT IS ALREADY IN THE BUFFER FIRST -- a `fread` after an `fgetc` must
       not skip the character the refill read ahead. */
    while (done < want && __f->__rd < __f->__rn)
        out[done++] = __f->__rbuf[__f->__rd++];
    while (done < want) {
        got = host_file_read(__f->__h, out + done, (long)(want - done));
        if (got < 0) { __f->__err = 1; errno = __host_errno(got); break; }
        if (got == 0) { __f->__eof = 1; break; }
        done += (size_t)got;
    }
    return done / __size;
}

/* ── position ─────────────────────────────────────────────────────────── */
static int fseek(FILE *__f, long __off, int __whence)
{
    long at, ahead;
    if (__f == NULL) { errno = EINVAL; return -1; }
    ahead = (long)(__f->__rn - __f->__rd);
    /* THE BUFFER IS PART OF THE POSITION. A relative seek is relative to
       where the PROGRAM is, which is `ahead` bytes behind where the host
       is; forgetting that is the classic read-ahead bug. */
    if (__whence == SEEK_CUR) __off -= ahead;
    __f->__rd = 0; __f->__rn = 0;
    at = host_file_seek(__f->__h, __off, (long)__whence);
    if (at < 0) { errno = __host_errno(at); return -1; }
    __f->__eof = 0;
    return 0;
}

static long ftell(FILE *__f)
{
    long at;
    if (__f == NULL) { errno = EINVAL; return -1L; }
    at = host_file_seek(__f->__h, 0, __HOST_SEEK_CUR);
    if (at < 0) { errno = __host_errno(at); return -1L; }
    return at - (long)(__f->__rn - __f->__rd);
}

static void rewind(FILE *__f)
{
    fseek(__f, 0L, SEEK_SET);
    if (__f) __f->__err = 0;
}

static int fgetpos(FILE *__f, fpos_t *__at)
{
    long where = ftell(__f);
    if (where < 0 || __at == NULL) return -1;
    *__at = where;
    return 0;
}

static int fsetpos(FILE *__f, const fpos_t *__at)
{
    if (__at == NULL) return -1;
    return fseek(__f, *__at, SEEK_SET);
}

static int feof(FILE *__f) { return __f ? __f->__eof : 0; }
static int ferror(FILE *__f) { return __f ? __f->__err : 0; }
static void clearerr(FILE *__f) { if (__f) { __f->__eof = 0; __f->__err = 0; } }

/* ── files as names ───────────────────────────────────────────────────── */
static int remove(const char *__p)
{
    long r;
    if (__p == NULL) { errno = EINVAL; return -1; }
    r = host_file_remove(__p, __path_len(__p));
    if (r < 0) { errno = __host_errno(r); return -1; }
    return 0;
}

/* THE HOST SERVICES HAVE NO RENAME, and `objects/hostsvc.py`'s `file` group
   is ten operations chosen on purpose -- `bundled/os.py` refuses `os.rename`
   for exactly this reason rather than approximating it. Copy-and-remove is
   not a rename: it is not atomic, it loses every link and permission, and it
   cannot move a directory. So this fails, which is an answer C allows, and
   says why through `errno`. */
static int rename(const char *__a, const char *__b)
{
    (void)__a; (void)__b;
    errno = ENOSYS;
    return -1;
}

/* A NAME THAT IS NOT TAKEN, asked of the host rather than assumed. The
   counter makes TMP_MAX distinct attempts, as C requires, and each is
   checked before it is offered. */
static unsigned long __tmp_counter;
static char __tmpnam_buf[L_tmpnam];

static char *tmpnam(char *__s)
{
    char *out = __s ? __s : __tmpnam_buf;
    unsigned long n;
    int i, tries;
    for (tries = 0; tries < TMP_MAX; tries++) {
        n = ++__tmp_counter;
        out[0] = 't'; out[1] = 'm'; out[2] = 'p';
        for (i = 10; i >= 3; i--) { out[i] = (char)('0' + (int)(n % 10)); n /= 10; }
        out[11] = 0;
        if (host_file_kind(out, 11) == __HOST_KIND_MISSING) return out;
    }
    return NULL;
}

static FILE *tmpfile(void)
{
    char name[L_tmpnam];
    FILE *f;
    int i;
    if (tmpnam(name) == NULL) return NULL;
    f = __file_open(name, "w+b", NULL);
    if (f == NULL) return NULL;
    /* REMOVED WHEN IT IS CLOSED, which is what C promises. A hosted libc
       unlinks it immediately and lets the descriptor keep it alive; there is
       no unlink-while-open in the host services, so the removal waits for
       `fclose` -- and a program that exits without closing leaves the file
       behind, which is the one difference and is said here. */
    f->__tmp = 1;
    for (i = 0; i < L_tmpnam; i++) f->__name[i] = name[i];
    return f;
}

static void perror(const char *__s)
{
    if (__s && __s[0]) { fputs(__s, stderr); fputs(": ", stderr); }
    fputs(strerror(errno), stderr);
    fputc('\n', stderr);
}

/* ── the scanner ──────────────────────────────────────────────────────── */
/* THE OTHER ENGINE. `scanf`, `fscanf` and `sscanf` differ only in where the
   characters come from, so they differ only in this structure: a stream, or
   a string with a cursor. Everything about a conversion -- the width, the
   length modifier, what counts as white space, when a match fails -- is
   written once.

   MATCHING FAILURE AND INPUT FAILURE ARE DIFFERENT and C makes a program
   able to tell: a conversion that read nothing because the input ran out
   answers EOF when no assignment has been made yet, and the number of
   assignments otherwise. */
typedef struct {
    FILE *__f;              /* NULL when the source is a string */
    const char *__s;
    size_t __at;
    int __back;             /* the string's one pushback slot, or -1 */
    long __taken;           /* characters consumed, for `%n` */
} __scan;

static int __scan_get(__scan *__sc)
{
    int c;
    if (__sc->__back >= 0) { c = __sc->__back; __sc->__back = -1; }
    else if (__sc->__f != NULL) c = fgetc(__sc->__f);
    else c = __sc->__s[__sc->__at] ? (unsigned char)__sc->__s[__sc->__at++] : EOF;
    if (c != EOF) __sc->__taken++;
    return c;
}

static void __scan_unget(__scan *__sc, int __c)
{
    if (__c == EOF) return;
    __sc->__taken--;
    if (__sc->__f != NULL) ungetc(__c, __sc->__f);
    else __sc->__back = __c;
}

static int __scan_space(int __c)
{
    return __c == ' ' || __c == '\t' || __c == '\n' || __c == '\v'
        || __c == '\f' || __c == '\r';
}

static int __scan_skip(__scan *__sc)
{
    int c;
    do { c = __scan_get(__sc); } while (c != EOF && __scan_space(c));
    if (c != EOF) __scan_unget(__sc, c);
    return c;
}

/* THE LENGTH MODIFIERS, as a number, so the assignment below is a switch
   rather than a tree of comparisons. */
#define __LEN_INT   0
#define __LEN_CHAR  1
#define __LEN_SHORT 2
#define __LEN_LONG  3
#define __LEN_LLONG 4
#define __LEN_MAX   5
#define __LEN_SIZE  6
#define __LEN_PTRD  7
#define __LEN_LDBL  8

static void __scan_put_int(void *__p, int __len, unsigned long long __v,
                           int __neg)
{
    long long v = __neg ? -(long long)__v : (long long)__v;
    switch (__len) {
    case __LEN_CHAR:  *(char *)__p = (char)v; break;
    case __LEN_SHORT: *(short *)__p = (short)v; break;
    case __LEN_LONG:  *(long *)__p = (long)v; break;
    case __LEN_LLONG: *(long long *)__p = v; break;
    case __LEN_MAX:   *(long long *)__p = v; break;
    case __LEN_SIZE:  *(size_t *)__p = (size_t)v; break;
    case __LEN_PTRD:  *(ptrdiff_t *)__p = (ptrdiff_t)v; break;
    default:          *(int *)__p = (int)v; break;
    }
}

static int __scan_digit(int __c, int __base)
{
    int v;
    if (__c >= '0' && __c <= '9') v = __c - '0';
    else if (__c >= 'a' && __c <= 'z') v = __c - 'a' + 10;
    else if (__c >= 'A' && __c <= 'Z') v = __c - 'A' + 10;
    else return -1;
    return v < __base ? v : -1;
}

/* AN INTEGER, WITH THE BASE POSSIBLY UNDECIDED. `%i` takes the base from the
   text the way a C literal does, which is what makes it different from `%d`,
   and `%x` accepts the `0x` it does not need. */
static int __scan_int(__scan *__sc, int __base, int __width, int __want_signed,
                      void *__out, int __len)
{
    unsigned long long v = 0;
    int c, neg = 0, any = 0, used = 0;
    c = __scan_get(__sc);
    used++;
    if (c == '+' || c == '-') {
        neg = c == '-';
        if (__width && used >= __width) { __scan_unget(__sc, c); return 0; }
        c = __scan_get(__sc);
        used++;
    }
    if ((__base == 0 || __base == 16) && c == '0') {
        int save;
        any = 1;
        v = 0;
        if (!__width || used < __width) {
            save = __scan_get(__sc);
            used++;
            if (save == 'x' || save == 'X') {
                __base = 16;
                any = 0;
                c = (!__width || used < __width) ? __scan_get(__sc) : EOF;
                if (c != EOF) used++;
            } else {
                if (__base == 0) __base = 8;
                c = save;
            }
        } else {
            if (__base == 0) __base = 8;
            c = EOF;
        }
    } else if (__base == 0) {
        __base = 10;
    }
    while (c != EOF && __scan_digit(c, __base) >= 0) {
        v = v * (unsigned long long)__base
            + (unsigned long long)__scan_digit(c, __base);
        any = 1;
        if (__width && used >= __width) { c = EOF; break; }
        c = __scan_get(__sc);
        used++;
    }
    __scan_unget(__sc, c);
    if (!any) return 0;
    if (__out != NULL) {
        if (__want_signed) __scan_put_int(__out, __len, v, neg);
        else __scan_put_int(__out, __len, neg ? (unsigned long long)0 - v : v, 0);
    }
    return 1;
}

/* A FLOATING-POINT NUMBER, COLLECTED AND THEN CONVERTED, because the
   conversion is `__num_decimal`'s and doing it here would be a second one to
   keep right. `inf`, `infinity` and `nan` are part of the grammar C gives
   `%f`, and a hex significand is too. */
static int __scan_float(__scan *__sc, int __width, void *__out, int __len)
{
    char buf[__NUM_TEXT_MAX];
    int n = 0, c, used = 0, any = 0;
    double v;
    c = __scan_get(__sc); used++;
    if (c == '+' || c == '-') {
        buf[n++] = (char)c;
        if (__width && used >= __width) { __scan_unget(__sc, c); return 0; }
        c = __scan_get(__sc); used++;
    }
    if (c == 'i' || c == 'I' || c == 'n' || c == 'N') {
        const char *w = (c == 'i' || c == 'I') ? "infinity" : "nan";
        int matched = 0;
        while (c != EOF && matched < 8 && w[matched]
               && (c | 32) == w[matched]) {
            buf[n++] = (char)c;
            matched++;
            /* `inf` is complete at three and `infinity` at eight; anything
               between is put back, which is what the grammar says. */
            if (__width && used >= __width) { c = EOF; break; }
            c = __scan_get(__sc); used++;
        }
        __scan_unget(__sc, c);
        buf[n] = 0;
        if (n >= 3) {
            any = 1;
        } else {
            return 0;
        }
    } else {
        int hex = 0;
        if (c == '0') {
            buf[n++] = (char)c;
            any = 1;
            if (!__width || used < __width) {
                c = __scan_get(__sc); used++;
                if (c == 'x' || c == 'X') {
                    hex = 1;
                    buf[n++] = (char)c;
                    any = 0;
                    c = (!__width || used < __width) ? __scan_get(__sc) : EOF;
                    if (c != EOF) used++;
                }
            } else {
                c = EOF;
            }
        }
        while (c != EOF && n < __NUM_TEXT_MAX - 2) {
            int d = hex ? __scan_digit(c, 16) : __scan_digit(c, 10);
            if (d < 0) break;
            buf[n++] = (char)c;
            any = 1;
            if (__width && used >= __width) { c = EOF; break; }
            c = __scan_get(__sc); used++;
        }
        if (c == '.' && n < __NUM_TEXT_MAX - 2) {
            buf[n++] = (char)c;
            if (!__width || used < __width) { c = __scan_get(__sc); used++; }
            else c = EOF;
            while (c != EOF && n < __NUM_TEXT_MAX - 2) {
                int d = hex ? __scan_digit(c, 16) : __scan_digit(c, 10);
                if (d < 0) break;
                buf[n++] = (char)c;
                any = 1;
                if (__width && used >= __width) { c = EOF; break; }
                c = __scan_get(__sc); used++;
            }
        }
        if (any && ((hex && (c == 'p' || c == 'P'))
                    || (!hex && (c == 'e' || c == 'E')))) {
            int mark = n;
            buf[n++] = (char)c;
            if (!__width || used < __width) { c = __scan_get(__sc); used++; }
            else c = EOF;
            if (c == '+' || c == '-') {
                buf[n++] = (char)c;
                if (!__width || used < __width) { c = __scan_get(__sc); used++; }
                else c = EOF;
            }
            if (c != EOF && __scan_digit(c, 10) >= 0) {
                while (c != EOF && __scan_digit(c, 10) >= 0
                       && n < __NUM_TEXT_MAX - 2) {
                    buf[n++] = (char)c;
                    if (__width && used >= __width) { c = EOF; break; }
                    c = __scan_get(__sc); used++;
                }
            } else {
                /* AN EXPONENT THAT IS NOT ONE is not part of the number:
                   `1e+` is `1` followed by `e+`, and the characters go
                   back. Only one can, so the rest are dropped -- which is
                   what a hosted libc's `scanf` does too. */
                __scan_unget(__sc, c);
                n = mark;
                c = EOF;
            }
        }
        __scan_unget(__sc, c);
        buf[n] = 0;
        if (!any) return 0;
    }
    v = __num_strtod(buf, NULL);
    if (__out != NULL) {
        if (__len == __LEN_LDBL) *(long double *)__out = (long double)v;
        else if (__len == __LEN_LONG) *(double *)__out = v;
        else *(float *)__out = (float)v;
    }
    return 1;
}

static int __vscan(__scan *__sc, const char *__fmt, va_list __ap)
{
    int done = 0, c;
    const char *f = __fmt;
    while (*f) {
        if (__scan_space((unsigned char)*f)) {
            /* WHITE SPACE IN THE FORMAT MATCHES ANY AMOUNT, INCLUDING NONE,
               and never fails -- which is why it is not a conversion and is
               not counted. */
            __scan_skip(__sc);
            f++;
            continue;
        }
        if (*f != '%') {
            c = __scan_get(__sc);
            if (c == EOF) return done ? done : EOF;
            if (c != (unsigned char)*f) { __scan_unget(__sc, c); return done; }
            f++;
            continue;
        }
        f++;
        if (*f == '%') {
            c = __scan_skip(__sc);
            c = __scan_get(__sc);
            if (c == EOF) return done ? done : EOF;
            if (c != '%') { __scan_unget(__sc, c); return done; }
            f++;
            continue;
        }
        {
            int suppress = 0, width = 0, len = __LEN_INT, ok;
            void *out;
            if (*f == '*') { suppress = 1; f++; }
            while (*f >= '0' && *f <= '9') width = width * 10 + (*f++ - '0');
            if (*f == 'h') { f++; len = __LEN_SHORT;
                             if (*f == 'h') { f++; len = __LEN_CHAR; } }
            else if (*f == 'l') { f++; len = __LEN_LONG;
                                  if (*f == 'l') { f++; len = __LEN_LLONG; } }
            else if (*f == 'j') { f++; len = __LEN_MAX; }
            else if (*f == 'z') { f++; len = __LEN_SIZE; }
            else if (*f == 't') { f++; len = __LEN_PTRD; }
            else if (*f == 'L') { f++; len = __LEN_LDBL; }
            if (*f == 0) return done;
            c = *f++;
            /* THE POINTER IS TAKEN BEFORE THE CONVERSION RUNS and only when
               there is one to take: `%*d` reads an argument from nobody. */
            out = suppress ? NULL : va_arg(__ap, void *);
            switch (c) {
            case 'd': case 'u':
                if (__scan_skip(__sc) == EOF) return done ? done : EOF;
                ok = __scan_int(__sc, 10, width, c == 'd', out, len);
                break;
            case 'i':
                if (__scan_skip(__sc) == EOF) return done ? done : EOF;
                ok = __scan_int(__sc, 0, width, 1, out, len);
                break;
            case 'o':
                if (__scan_skip(__sc) == EOF) return done ? done : EOF;
                ok = __scan_int(__sc, 8, width, 0, out, len);
                break;
            case 'x': case 'X':
                if (__scan_skip(__sc) == EOF) return done ? done : EOF;
                ok = __scan_int(__sc, 16, width, 0, out, len);
                break;
            case 'b':
                if (__scan_skip(__sc) == EOF) return done ? done : EOF;
                ok = __scan_int(__sc, 2, width, 0, out, len);
                break;
            case 'p': {
                unsigned long long v = 0;
                int any = 0, used = 0, d;
                if (__scan_skip(__sc) == EOF) return done ? done : EOF;
                c = __scan_get(__sc); used++;
                if (c == '0') {
                    int nxt = __scan_get(__sc);
                    if (nxt == 'x' || nxt == 'X') { c = __scan_get(__sc); used += 2; }
                    else { __scan_unget(__sc, nxt); }
                }
                while (c != EOF && (d = __scan_digit(c, 16)) >= 0) {
                    v = v * 16u + (unsigned long long)d;
                    any = 1;
                    if (width && used >= width) { c = EOF; break; }
                    c = __scan_get(__sc); used++;
                }
                __scan_unget(__sc, c);
                if (any && out != NULL) *(void **)out = (void *)(size_t)v;
                ok = any;
                break;
            }
            case 'e': case 'E': case 'f': case 'F': case 'g': case 'G':
            case 'a': case 'A':
                if (__scan_skip(__sc) == EOF) return done ? done : EOF;
                ok = __scan_float(__sc, width, out, len);
                break;
            case 'c': {
                /* NO SKIPPING, NO TERMINATOR: `%c` is the one conversion
                   that takes white space as data, and it writes exactly as
                   many characters as its width says. */
                int want = width ? width : 1, i;
                char *dst = (char *)out;
                ok = 1;
                for (i = 0; i < want; i++) {
                    int got = __scan_get(__sc);
                    if (got == EOF) { ok = i > 0 ? 0 : -1; break; }
                    if (dst) dst[i] = (char)got;
                }
                if (ok < 0) return done ? done : EOF;
                break;
            }
            case 's': {
                char *dst = (char *)out;
                int i = 0;
                if (__scan_skip(__sc) == EOF) return done ? done : EOF;
                while (!width || i < width) {
                    int got = __scan_get(__sc);
                    if (got == EOF || __scan_space(got)) {
                        __scan_unget(__sc, got);
                        break;
                    }
                    if (dst) dst[i] = (char)got;
                    i++;
                }
                if (dst) dst[i] = 0;
                ok = i > 0;
                break;
            }
            case '[': {
                /* THE SCANSET, WHICH IS A SET AND NOT A PATTERN. `^` at the
                   front inverts it, `]` first is a literal, and `a-z` is a
                   range -- the three rules C gives, and no others. */
                char member[256];
                char *dst = (char *)out;
                int negate = 0, i, prev = -1;
                for (i = 0; i < 256; i++) member[i] = 0;
                if (*f == '^') { negate = 1; f++; }
                if (*f == ']') { member[(unsigned char)']'] = 1; prev = ']'; f++; }
                while (*f && *f != ']') {
                    if (*f == '-' && prev >= 0 && f[1] && f[1] != ']') {
                        int hi = (unsigned char)f[1];
                        for (i = prev; i <= hi; i++) member[i] = 1;
                        f += 2;
                        prev = -1;
                        continue;
                    }
                    prev = (unsigned char)*f;
                    member[prev] = 1;
                    f++;
                }
                if (*f == ']') f++;
                i = 0;
                while (!width || i < width) {
                    int got = __scan_get(__sc);
                    if (got == EOF) { __scan_unget(__sc, got); break; }
                    if ((member[got] != 0) == (negate != 0)) {
                        __scan_unget(__sc, got);
                        break;
                    }
                    if (dst) dst[i] = (char)got;
                    i++;
                }
                if (dst) dst[i] = 0;
                ok = i > 0;
                break;
            }
            case 'n':
                /* NOT A CONVERSION, so it is not counted and cannot fail --
                   which is why it is the one case that does not touch `ok`. */
                if (out != NULL) __scan_put_int(out, len,
                                                (unsigned long long)__sc->__taken, 0);
                continue;
            default:
                return done;
            }
            if (!ok) return done;
            if (!suppress) done++;
        }
    }
    return done;
}

static int vfscanf(FILE *__f, const char *__fmt, va_list __ap)
{
    __scan sc;
    sc.__f = __f; sc.__s = NULL; sc.__at = 0; sc.__back = -1; sc.__taken = 0;
    return __vscan(&sc, __fmt, __ap);
}

static int vscanf(const char *__fmt, va_list __ap)
{
    return vfscanf(stdin, __fmt, __ap);
}

static int vsscanf(const char *__s, const char *__fmt, va_list __ap)
{
    __scan sc;
    sc.__f = NULL; sc.__s = __s; sc.__at = 0; sc.__back = -1; sc.__taken = 0;
    return __vscan(&sc, __fmt, __ap);
}

static int scanf(const char *__fmt, ...)
{
    va_list ap; int n;
    va_start(ap, __fmt);
    n = vscanf(__fmt, ap);
    va_end(ap);
    return n;
}

static int fscanf(FILE *__f, const char *__fmt, ...)
{
    va_list ap; int n;
    va_start(ap, __fmt);
    n = vfscanf(__f, __fmt, ap);
    va_end(ap);
    return n;
}

static int sscanf(const char *__s, const char *__fmt, ...)
{
    va_list ap; int n;
    va_start(ap, __fmt);
    n = vsscanf(__s, __fmt, ap);
    va_end(ap);
    return n;
}

#endif
