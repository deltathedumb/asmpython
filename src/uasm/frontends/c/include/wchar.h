/* <wchar.h> -- the wide-string functions that are loops over memory.

   `wchar_t` IS FOUR BYTES here and holds a code point, so these are the
   narrow functions with a wider element and no encoding to think about. The
   wide STREAM functions are absent for the same reason the narrow input
   functions are: the platform floor cannot read. */
#ifndef _UASM_WCHAR_H
#define _UASM_WCHAR_H

#include <stddef.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <time.h>
#include <__uasm_wide.h>

static size_t wcslen(const wchar_t *__s)
{ size_t n = 0; while (__s[n]) n++; return n; }

static wchar_t *wcscpy(wchar_t *__d, const wchar_t *__s)
{ size_t i = 0; while ((__d[i] = __s[i]) != 0) i++; return __d; }

static wchar_t *wcsncpy(wchar_t *__d, const wchar_t *__s, size_t __n)
{ size_t i = 0; while (i < __n && __s[i]) { __d[i] = __s[i]; i++; }
  while (i < __n) __d[i++] = 0; return __d; }

static wchar_t *wcscat(wchar_t *__d, const wchar_t *__s)
{ size_t i = wcslen(__d), j = 0; while ((__d[i + j] = __s[j]) != 0) j++; return __d; }

static int wcscmp(const wchar_t *__a, const wchar_t *__b)
{ size_t i = 0; while (__a[i] && __a[i] == __b[i]) i++;
  return __a[i] < __b[i] ? -1 : (__a[i] > __b[i] ? 1 : 0); }

static int wcsncmp(const wchar_t *__a, const wchar_t *__b, size_t __n)
{ size_t i = 0; while (i < __n) { if (__a[i] != __b[i])
      return __a[i] < __b[i] ? -1 : 1; if (!__a[i]) return 0; i++; } return 0; }

static wchar_t *wcschr(const wchar_t *__s, wchar_t __c)
{ size_t i = 0; for (;;) { if (__s[i] == __c) return (wchar_t *)(__s + i);
    if (!__s[i]) return 0; i++; } }

static wchar_t *wcsrchr(const wchar_t *__s, wchar_t __c)
{ const wchar_t *found = 0; size_t i = 0;
  for (;;) { if (__s[i] == __c) found = __s + i; if (!__s[i]) break; i++; }
  return (wchar_t *)found; }

static wchar_t *wcsstr(const wchar_t *__h, const wchar_t *__n)
{ size_t nl = wcslen(__n), i;
  if (nl == 0) return (wchar_t *)__h;
  for (i = 0; __h[i]; i++) { size_t j = 0;
    while (j < nl && __h[i + j] == __n[j]) j++;
    if (j == nl) return (wchar_t *)(__h + i); }
  return 0; }

static wchar_t *wmemcpy(wchar_t *__d, const wchar_t *__s, size_t __n)
{ size_t i; for (i = 0; i < __n; i++) __d[i] = __s[i]; return __d; }

static wchar_t *wmemmove(wchar_t *__d, const wchar_t *__s, size_t __n)
{ size_t i; if (__d < __s) { for (i = 0; i < __n; i++) __d[i] = __s[i]; }
  else if (__d > __s) { for (i = __n; i > 0; i--) __d[i-1] = __s[i-1]; }
  return __d; }

static wchar_t *wmemset(wchar_t *__d, wchar_t __c, size_t __n)
{ size_t i; for (i = 0; i < __n; i++) __d[i] = __c; return __d; }

static int wmemcmp(const wchar_t *__a, const wchar_t *__b, size_t __n)
{ size_t i; for (i = 0; i < __n; i++) if (__a[i] != __b[i])
      return __a[i] < __b[i] ? -1 : 1; return 0; }

static wchar_t *wmemchr(const wchar_t *__s, wchar_t __c, size_t __n)
{ size_t i; for (i = 0; i < __n; i++) if (__s[i] == __c)
      return (wchar_t *)(__s + i); return 0; }

/* UTF-8 IS THE MULTIBYTE ENCODING, which `literals.py` also decided, so
   these two agree with what a string literal in the source becomes. */


/* ── the ones the narrow header has and this one was missing ──────────── */
static wchar_t *wcsncat(wchar_t *__d, const wchar_t *__s, size_t __n)
{
    size_t d = wcslen(__d), i = 0;
    while (i < __n && __s[i]) { __d[d + i] = __s[i]; i++; }
    __d[d + i] = 0;
    return __d;
}

static size_t wcsspn(const wchar_t *__s, const wchar_t *__set)
{
    size_t n = 0;
    while (__s[n] && wcschr(__set, __s[n])) n++;
    return n;
}

static size_t wcscspn(const wchar_t *__s, const wchar_t *__set)
{
    size_t n = 0;
    while (__s[n] && !wcschr(__set, __s[n])) n++;
    return n;
}

static wchar_t *wcspbrk(const wchar_t *__s, const wchar_t *__set)
{
    size_t n = wcscspn(__s, __set);
    return __s[n] ? (wchar_t *)__s + n : NULL;
}

/* `wcstok` TAKES ITS STATE AS AN ARGUMENT and `strtok` keeps it in a static,
   which is the one place the wide function is the better-designed of the
   pair: C gave the wide one a third parameter from the start. */
static wchar_t *wcstok(wchar_t *__s, const wchar_t *__set, wchar_t **__save)
{
    wchar_t *p;
    if (__s == NULL) __s = *__save;
    if (__s == NULL) return NULL;
    __s += wcsspn(__s, __set);
    if (*__s == 0) { *__save = NULL; return NULL; }
    p = __s + wcscspn(__s, __set);
    if (*p) { *p = 0; *__save = p + 1; } else { *__save = NULL; }
    return __s;
}

/* ONE LOCALE, SO COLLATION IS COMPARISON and the transform is a copy --
   `<string.h>`'s `strcoll` and `strxfrm` say the same thing. */
static int wcscoll(const wchar_t *__a, const wchar_t *__b)
{ return wcscmp(__a, __b); }

static size_t wcsxfrm(wchar_t *__d, const wchar_t *__s, size_t __n)
{
    size_t len = wcslen(__s), i;
    for (i = 0; i < __n && i <= len; i++) __d[i] = __s[i];
    return len;
}

/* ── between the two encodings ────────────────────────────────────────── */
static wint_t btowc(int __c)
{
    /* A SINGLE BYTE IS A CHARACTER ONLY IF IT IS ASCII here, because the
       encoding is UTF-8: a byte with its top bit set is part of a sequence
       and is not a character on its own. */
    if (__c == EOF || (unsigned)__c > 127) return WEOF;
    return (wint_t)__c;
}

static int wctob(wint_t __w)
{ return __w != WEOF && (unsigned int)__w < 128u ? (int)__w : EOF; }

static size_t mbrlen(const char *__s, size_t __n, mbstate_t *__st)
{
    static mbstate_t __own;
    return mbrtowc(NULL, __s, __n, __st ? __st : &__own);
}

static size_t mbsrtowcs(wchar_t *__d, const char **__s, size_t __n,
                        mbstate_t *__st)
{
    mbstate_t own;
    size_t i = 0, k;
    const char *p = *__s;
    size_t left = strlen(p) + 1;
    wchar_t w;
    if (__st == NULL) { own.__count = 0; own.__value = 0; __st = &own; }
    while (__d == NULL || i < __n) {
        k = mbrtowc(&w, p, left, __st);
        if (k == (size_t)-1 || k == (size_t)-2) return (size_t)-1;
        if (k == 0) {
            if (__d) { __d[i] = 0; *__s = NULL; }
            return i;
        }
        if (__d) __d[i] = w;
        i++;
        p += k;
        left -= k;
    }
    *__s = p;
    return i;
}

static size_t wcsrtombs(char *__d, const wchar_t **__s, size_t __n,
                        mbstate_t *__st)
{
    mbstate_t own;
    char buf[8];
    size_t i = 0, k, j;
    const wchar_t *p = *__s;
    if (__st == NULL) { own.__count = 0; own.__value = 0; __st = &own; }
    for (;;) {
        if (*p == 0) {
            if (__d && i < __n) { __d[i] = 0; *__s = NULL; }
            return i;
        }
        k = wcrtomb(buf, *p, __st);
        if (k == (size_t)-1) return (size_t)-1;
        if (__d) {
            if (i + k > __n) { *__s = p; return i; }
            for (j = 0; j < k; j++) __d[i + j] = buf[j];
        }
        i += k;
        p++;
    }
}

/* ── the numerals, which are ASCII whatever the element is ────────────── */
/* THE NUMERIC SYNTAX HAS NO WIDE CHARACTER IN IT: every character a numeral
   may contain is in the basic set, so a wide numeral is the narrow one in a
   wider element. Copying the prefix that could belong to a number into a
   narrow buffer and handing it to `<stdlib.h>`'s parser is the whole
   conversion, and the end pointer maps back one character to one -- which is
   why it is a COUNT that comes back from the narrow call rather than a
   pointer into a string the caller has never seen.

   A NUMERAL LONGER THAN THE BUFFER GOES ON THE HEAP, because `strtod` of a
   thousand digits is a real thing to write and truncating it would be a
   wrong answer rather than a refusal. */
#define __WCS_SMALL 256

static int __wcs_numeric(wchar_t __c)
{
    return (__c >= L'0' && __c <= L'9') || (__c >= L'a' && __c <= L'z')
        || (__c >= L'A' && __c <= L'Z') || __c == L'+' || __c == L'-'
        || __c == L'.';
}

static size_t __wcs_numeral(const wchar_t *__s)
{
    size_t i = 0;
    while (__s[i] == L' ' || (__s[i] >= 9 && __s[i] <= 13)) i++;
    while (__wcs_numeric(__s[i])) i++;
    return i;
}

static char *__wcs_narrow(const wchar_t *__s, char *__small, size_t *__len)
{
    size_t n = __wcs_numeral(__s), i;
    char *buf = __small;
    if (n >= __WCS_SMALL) {
        buf = (char *)malloc(n + 1);
        if (buf == NULL) { *__len = 0; return NULL; }
    }
    for (i = 0; i < n; i++) buf[i] = (char)__s[i];
    buf[n] = 0;
    *__len = n;
    return buf;
}

/* `__VA_OPT__` FOR THE FOUR THAT TAKE A BASE, because the extra parameter
   carries a comma of its own and one macro argument cannot. */
#define __WCS_NUM(NAME, T, CALL, ...)                                         \
    static T NAME(const wchar_t *__s, wchar_t **__end                         \
                  __VA_OPT__(,) __VA_ARGS__)                                  \
    {                                                                         \
        char __small[__WCS_SMALL];                                            \
        size_t __n;                                                           \
        char *__buf = __wcs_narrow(__s, __small, &__n), *__stop;              \
        T __r;                                                                \
        if (__buf == NULL) { if (__end) *__end = (wchar_t *)__s; return 0; }  \
        __r = CALL;                                                           \
        if (__end) *__end = (wchar_t *)__s + (size_t)(__stop - __buf);        \
        if (__buf != __small) free(__buf);                                    \
        return __r;                                                           \
    }

__WCS_NUM(wcstod, double, strtod(__buf, &__stop))
__WCS_NUM(wcstof, float, strtof(__buf, &__stop))
__WCS_NUM(wcstold, long double, strtold(__buf, &__stop))
__WCS_NUM(wcstol, long, strtol(__buf, &__stop, __base), int __base)
__WCS_NUM(wcstoll, long long, strtoll(__buf, &__stop, __base), int __base)
__WCS_NUM(wcstoul, unsigned long, strtoul(__buf, &__stop, __base), int __base)
__WCS_NUM(wcstoull, unsigned long long, strtoull(__buf, &__stop, __base),
          int __base)

/* ── the calendar, in wide characters ─────────────────────────────────── */
/* THE FORMAT AND THE ANSWER ARE BOTH ASCII for every conversion `strftime`
   has, so this is `strftime` with two conversions around it. A `%` specifier
   that produced a non-ASCII character would need more; none does here,
   because there is one locale and its month names are English. */
static size_t wcsftime(wchar_t *__d, size_t __n, const wchar_t *__fmt,
                       const struct tm *__tm)
{
    char fmt[256], out[512];
    size_t i = 0, got;
    while (__fmt[i] && i + 1 < sizeof fmt) { fmt[i] = (char)__fmt[i]; i++; }
    fmt[i] = 0;
    got = strftime(out, sizeof out, fmt, __tm);
    if (got == 0 || got >= __n) return 0;
    for (i = 0; i < got; i++) __d[i] = (wchar_t)(unsigned char)out[i];
    __d[got] = 0;
    return got;
}


/* ── the wide streams ─────────────────────────────────────────────────── */
/* A WIDE STREAM IS A BYTE STREAM WITH A CONVERSION ON IT, which is what C
   says it is and what makes this layer short: writing a wide character means
   writing its multibyte encoding, reading one means decoding the next
   sequence, and `fwprintf` is `fprintf` with the format converted. The bytes
   go through `<stdio.h>`'s own machinery, so a wide-oriented stream and a
   byte-oriented one are the same file with the same buffer.

   THE ONE REWRITE IS `%c`. In a wide `printf` an `int` argument to `%c` is
   converted to `wchar_t` and written -- so above 127 it is a multibyte
   sequence, where the narrow `%c` would write one byte. `%lc` in the narrow
   formatter does exactly that, so the conversion becomes one. `%s` needs no
   rewrite: a narrow `%s` in a wide `printf` takes a multibyte string and
   writes it back, which is a copy either way.

   THE ORIENTATION IS RECORDED AND NOT ENFORCED, which is the one place this
   is laxer than C: a stream takes one on its first operation and using the
   other kind afterwards is undefined, and glibc makes the second call FAIL.
   Here there is one buffer under both faces, so both keep working --
   stricter-than-the-standard in the safe direction, like a local surviving a
   `longjmp`. `fwide` still answers truthfully, which is what a program that
   asks actually wants to know. */
#define __W_FMT 512

static size_t __w_narrow_format(char *__d, size_t __n, const wchar_t *__f)
{
    mbstate_t __ws;
    char __one[8];
    size_t __i = 0, __k, __j;
    int __in = 0, __length = 0;
    __ws.__count = 0;
    __ws.__value = 0;
    while (*__f && __i + 8 < __n) {
        wchar_t __c = *__f++;
        if (!__in) {
            if (__c == L'%') { __in = 1; __length = 0; }
        } else if (__c == L'%') {
            __in = 0;
        } else if (__c == L'h' || __c == L'l' || __c == L'j' || __c == L'z'
                   || __c == L't' || __c == L'L') {
            __length = 1;
        } else if ((__c >= L'a' && __c <= L'z') || (__c >= L'A' && __c <= L'Z')) {
            if (__c == L'c' && !__length) __d[__i++] = 'l';
            __in = 0;
        }
        __k = wcrtomb(__one, __c, &__ws);
        if (__k == (size_t)-1) break;
        for (__j = 0; __j < __k; __j++) __d[__i++] = __one[__j];
    }
    __d[__i] = 0;
    return __i;
}

static int fputwc(wchar_t __c, FILE *__f)
{
    mbstate_t __ws;
    char __buf[8];
    size_t __k, __i;
    __ws.__count = 0;
    __ws.__value = 0;
    __k = wcrtomb(__buf, __c, &__ws);
    if (__k == (size_t)-1) return WEOF;
    if (__f != NULL && __f->__ori == 0) __f->__ori = 1;
    for (__i = 0; __i < __k; __i++)
        if (fputc((unsigned char)__buf[__i], __f) == EOF) return WEOF;
    return (wint_t)__c;
}

static wint_t putwc(wchar_t __c, FILE *__f) { return (wint_t)fputwc(__c, __f); }
static wint_t putwchar(wchar_t __c) { return (wint_t)fputwc(__c, stdout); }

static int fputws(const wchar_t *__s, FILE *__f)
{
    while (*__s)
        if (fputwc(*__s++, __f) == (int)WEOF) return WEOF;
    return 0;
}

static int fwide(FILE *__f, int __mode)
{
    if (__f == NULL) return 0;
    if (__f->__ori == 0 && __mode != 0)
        __f->__ori = __mode > 0 ? 1 : -1;
    return __f->__ori;
}

static int vfwprintf(FILE *__f, const wchar_t *__fmt, va_list __ap)
{
    char __small[__W_FMT], *__buf = __small;
    if (__f != NULL && __f->__ori == 0) __f->__ori = 1;
    size_t __need = wcslen(__fmt) * 4 + 16;
    int __r;
    if (__need > sizeof __small) {
        __buf = (char *)malloc(__need);
        if (__buf == NULL) return -1;
    }
    __w_narrow_format(__buf, __need > sizeof __small ? __need : sizeof __small,
                      __fmt);
    __r = vfprintf(__f, __buf, __ap);
    if (__buf != __small) free(__buf);
    return __r;
}

static int vwprintf(const wchar_t *__fmt, va_list __ap)
{ return vfwprintf(stdout, __fmt, __ap); }

/* `swprintf` COUNTS WIDE CHARACTERS AND `snprintf` COUNTS BYTES, so the
   narrow buffer is four times as long -- no character is more -- and the
   answer is the wide length or a negative number, never a "would have
   been" count. C is explicit about that difference from `snprintf`. */
static int vswprintf(wchar_t *__s, size_t __n, const wchar_t *__fmt,
                     va_list __ap)
{
    char __fsmall[__W_FMT], *__fbuf = __fsmall, *__out;
    size_t __fneed = wcslen(__fmt) * 4 + 16, __bytes = __n * 4 + 4, __left;
    const char *__p;
    mbstate_t __ws;
    int __r;
    size_t __i = 0, __k;
    wchar_t __w;
    if (__n == 0) return -1;
    if (__fneed > sizeof __fsmall) {
        __fbuf = (char *)malloc(__fneed);
        if (__fbuf == NULL) return -1;
    }
    __w_narrow_format(__fbuf,
                      __fneed > sizeof __fsmall ? __fneed : sizeof __fsmall,
                      __fmt);
    __out = (char *)malloc(__bytes);
    if (__out == NULL) {
        if (__fbuf != __fsmall) free(__fbuf);
        return -1;
    }
    __r = vsnprintf(__out, __bytes, __fbuf, __ap);
    if (__fbuf != __fsmall) free(__fbuf);
    if (__r < 0 || (size_t)__r >= __bytes) { free(__out); return -1; }
    __ws.__count = 0;
    __ws.__value = 0;
    __p = __out;
    __left = (size_t)__r + 1;
    while (__i + 1 < __n) {
        __k = mbrtowc(&__w, __p, __left, &__ws);
        if (__k == (size_t)-1 || __k == (size_t)-2) { free(__out); return -1; }
        if (__k == 0) break;
        __s[__i++] = __w;
        __p += __k;
        __left -= __k;
    }
    __s[__i] = 0;
    free(__out);
    return (size_t)__i == __n - 1 && __left > 1 ? -1 : (int)__i;
}

static int fwprintf(FILE *__f, const wchar_t *__fmt, ...)
{
    va_list ap;
    int r;
    va_start(ap, __fmt);
    r = vfwprintf(__f, __fmt, ap);
    va_end(ap);
    return r;
}

static int wprintf(const wchar_t *__fmt, ...)
{
    va_list ap;
    int r;
    va_start(ap, __fmt);
    r = vfwprintf(stdout, __fmt, ap);
    va_end(ap);
    return r;
}

static int swprintf(wchar_t *__s, size_t __n, const wchar_t *__fmt, ...)
{
    va_list ap;
    int r;
    va_start(ap, __fmt);
    r = vswprintf(__s, __n, __fmt, ap);
    va_end(ap);
    return r;
}

static wint_t fgetwc(FILE *__f)
{
    int __c, __k, __i, __need;
    unsigned int __v;
    if (__f != NULL && __f->__ori == 0) __f->__ori = 1;
    __c = fgetc(__f);
    if (__c == EOF) return WEOF;
    if ((unsigned int)__c < 0x80u) return (wint_t)__c;
    if (((unsigned int)__c & 0xE0u) == 0xC0u) { __need = 1; __v = (unsigned int)__c & 0x1Fu; }
    else if (((unsigned int)__c & 0xF0u) == 0xE0u) { __need = 2; __v = (unsigned int)__c & 0x0Fu; }
    else if (((unsigned int)__c & 0xF8u) == 0xF0u) { __need = 3; __v = (unsigned int)__c & 0x07u; }
    else return WEOF;
    for (__i = 0; __i < __need; __i++) {
        __k = fgetc(__f);
        if (__k == EOF || ((unsigned int)__k & 0xC0u) != 0x80u) return WEOF;
        __v = (__v << 6) | ((unsigned int)__k & 0x3Fu);
    }
    return (wint_t)__v;
}

static wint_t getwc(FILE *__f) { return fgetwc(__f); }
static wint_t getwchar(void) { return fgetwc(stdin); }

static wint_t ungetwc(wint_t __c, FILE *__f)
{
    mbstate_t __ws;
    char __buf[8];
    size_t __k;
    int __i;
    if (__c == WEOF) return WEOF;
    __ws.__count = 0;
    __ws.__value = 0;
    __k = wcrtomb(__buf, (wchar_t)__c, &__ws);
    if (__k == (size_t)-1) return WEOF;
    /* THE BYTES GO BACK IN REVERSE, so that the next read takes the lead
       byte first. `ungetc` here keeps several, which is what makes a
       multibyte character pushable at all. */
    for (__i = (int)__k - 1; __i >= 0; __i--)
        if (ungetc((unsigned char)__buf[__i], __f) == EOF) return WEOF;
    return __c;
}

static wchar_t *fgetws(wchar_t *__s, int __n, FILE *__f)
{
    int __i = 0;
    wint_t __c;
    if (__n <= 0) return NULL;
    while (__i < __n - 1) {
        __c = fgetwc(__f);
        if (__c == WEOF) break;
        __s[__i++] = (wchar_t)__c;
        if (__c == L'\n') break;
    }
    if (__i == 0) return NULL;
    __s[__i] = 0;
    return __s;
}

/* THE SCANNER NEEDS NO REWRITE AT ALL: C gives `%c`, `%s` and `%[` the same
   meaning in the wide functions as in the narrow ones -- an `l` means a
   `wchar_t` array and its absence means a `char` one, in both -- and the
   input is a multibyte sequence either way. `<stdio.h>`'s `__scan_wchar` is
   what reads one. */
static int vfwscanf(FILE *__f, const wchar_t *__fmt, va_list __ap)
{
    char __small[__W_FMT], *__buf = __small;
    size_t __need = wcslen(__fmt) * 4 + 16;
    int __r;
    if (__need > sizeof __small) {
        __buf = (char *)malloc(__need);
        if (__buf == NULL) return EOF;
    }
    __w_narrow_format(__buf, __need > sizeof __small ? __need : sizeof __small,
                      __fmt);
    __r = vfscanf(__f, __buf, __ap);
    if (__buf != __small) free(__buf);
    return __r;
}

static int vwscanf(const wchar_t *__fmt, va_list __ap)
{ return vfwscanf(stdin, __fmt, __ap); }

static int vswscanf(const wchar_t *__s, const wchar_t *__fmt, va_list __ap)
{
    char __fsmall[__W_FMT], *__fbuf = __fsmall, *__in;
    size_t __fneed = wcslen(__fmt) * 4 + 16, __bytes = wcslen(__s) * 4 + 4;
    mbstate_t __ws;
    const wchar_t *__p = __s;
    int __r;
    if (__fneed > sizeof __fsmall) {
        __fbuf = (char *)malloc(__fneed);
        if (__fbuf == NULL) return EOF;
    }
    __w_narrow_format(__fbuf,
                      __fneed > sizeof __fsmall ? __fneed : sizeof __fsmall,
                      __fmt);
    __in = (char *)malloc(__bytes);
    if (__in == NULL) {
        if (__fbuf != __fsmall) free(__fbuf);
        return EOF;
    }
    __ws.__count = 0;
    __ws.__value = 0;
    wcsrtombs(__in, &__p, __bytes, &__ws);
    __r = vsscanf(__in, __fbuf, __ap);
    if (__fbuf != __fsmall) free(__fbuf);
    free(__in);
    return __r;
}

static int fwscanf(FILE *__f, const wchar_t *__fmt, ...)
{
    va_list ap;
    int r;
    va_start(ap, __fmt);
    r = vfwscanf(__f, __fmt, ap);
    va_end(ap);
    return r;
}

static int wscanf(const wchar_t *__fmt, ...)
{
    va_list ap;
    int r;
    va_start(ap, __fmt);
    r = vfwscanf(stdin, __fmt, ap);
    va_end(ap);
    return r;
}

static int swscanf(const wchar_t *__s, const wchar_t *__fmt, ...)
{
    va_list ap;
    int r;
    va_start(ap, __fmt);
    r = vswscanf(__s, __fmt, ap);
    va_end(ap);
    return r;
}

#endif
