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

#endif
