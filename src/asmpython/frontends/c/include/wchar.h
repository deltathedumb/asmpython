/* <wchar.h> -- the wide-string functions that are loops over memory.

   `wchar_t` IS FOUR BYTES here and holds a code point, so these are the
   narrow functions with a wider element and no encoding to think about. The
   wide STREAM functions are absent for the same reason the narrow input
   functions are: the platform floor cannot read. */
#ifndef _ASMPYTHON_WCHAR_H
#define _ASMPYTHON_WCHAR_H

#include <stddef.h>
#include <stdarg.h>

#ifndef WEOF
#define WEOF ((wint_t)-1)
#endif

typedef int wint_t;
typedef struct { int __count; unsigned int __value; } mbstate_t;

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
static size_t mbrtowc(wchar_t *__w, const char *__s, size_t __n, mbstate_t *__st)
{
    unsigned char c;
    unsigned int v;
    size_t need, i;
    (void)__st;
    if (__s == 0 || __n == 0) return (size_t)-2;
    c = (unsigned char)__s[0];
    if (c < 0x80) { if (__w) *__w = (wchar_t)c; return c ? 1u : 0u; }
    if ((c & 0xE0) == 0xC0) { need = 2; v = c & 0x1Fu; }
    else if ((c & 0xF0) == 0xE0) { need = 3; v = c & 0x0Fu; }
    else if ((c & 0xF8) == 0xF0) { need = 4; v = c & 0x07u; }
    else return (size_t)-1;
    if (__n < need) return (size_t)-2;
    for (i = 1; i < need; i++) {
        unsigned char k = (unsigned char)__s[i];
        if ((k & 0xC0) != 0x80) return (size_t)-1;
        v = (v << 6) | (k & 0x3Fu);
    }
    if (__w) *__w = (wchar_t)v;
    return need;
}

static size_t wcrtomb(char *__s, wchar_t __w, mbstate_t *__st)
{
    unsigned int v = (unsigned int)__w;
    (void)__st;
    if (__s == 0) return 1;
    if (v < 0x80) { __s[0] = (char)v; return 1; }
    if (v < 0x800) { __s[0] = (char)(0xC0 | (v >> 6));
        __s[1] = (char)(0x80 | (v & 0x3F)); return 2; }
    if (v < 0x10000) { __s[0] = (char)(0xE0 | (v >> 12));
        __s[1] = (char)(0x80 | ((v >> 6) & 0x3F));
        __s[2] = (char)(0x80 | (v & 0x3F)); return 3; }
    __s[0] = (char)(0xF0 | (v >> 18));
    __s[1] = (char)(0x80 | ((v >> 12) & 0x3F));
    __s[2] = (char)(0x80 | ((v >> 6) & 0x3F));
    __s[3] = (char)(0x80 | (v & 0x3F));
    return 4;
}

static int mbsinit(const mbstate_t *__st) { return __st == 0 || __st->__count == 0; }

#endif
