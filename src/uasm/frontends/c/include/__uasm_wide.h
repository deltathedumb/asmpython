/* The multibyte primitives, where `<stdlib.h>` can reach them.

   `mblen`, `mbtowc`, `mbstowcs` AND THE OTHER TWO ARE IN `<stdlib.h>`, which
   C decided long before `<wchar.h>` existed, and they are the same encoder
   and decoder `<wchar.h>`'s restartable ones are. One of the two headers had
   to hold them and neither is allowed to include the other -- `<wchar.h>`
   wants `strtol` for `wcstol` -- so they are here, and both include this.

   THE ENCODING IS UTF-8. C leaves the execution character set to the
   implementation and this one chose the source's, which is why `MB_CUR_MAX`
   is 4 and a program can tell this apart from a hosted libc in the `"C"`
   locale. `include/README.md` says it at more length. */
#ifndef _UASM_WIDE_H
#define _UASM_WIDE_H

#include <stddef.h>

#ifndef WEOF
#define WEOF ((wint_t)-1)
#endif

typedef int wint_t;
typedef struct { int __count; unsigned int __value; } mbstate_t;

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
