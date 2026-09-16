/* <uchar.h> -- the UTF-16 and UTF-32 character types.

   `char32_t` and `wchar_t` are the same width and hold the same thing here,
   so the conversions are `<wchar.h>`'s with a rename; `char16_t` needs the
   surrogate pair that UTF-16 uses above the basic plane, which is the only
   real work in this file. */
#ifndef _UASM_UCHAR_H
#define _UASM_UCHAR_H
#define __STDC_VERSION_UCHAR_H__ 202311L

#include <wchar.h>

typedef unsigned char char8_t;
typedef unsigned short char16_t;
typedef unsigned int char32_t;

/* ── UTF-8 code units, which C23 added ────────────────────────────────── */
/* THE ENCODING HERE IS ALREADY UTF-8, so these two are a buffer and a state
   rather than a conversion: `mbrtoc8` hands back one byte per call and keeps
   the rest, and `c8rtomb` collects bytes until the sequence is complete. The
   `(size_t)-3` return is the whole reason they are stateful -- it means "a
   code unit came out and nothing went in", which is how a function that
   answers one byte at a time reports the second one.

   A ZERO BYTE MEANS "ABSENT" in the packed state, which is safe because no
   byte of a multi-byte UTF-8 sequence is zero: a lead byte is 0xC0..0xF7 and
   a continuation byte is 0x80..0xBF. That is what lets `c8rtomb` work out
   how many it has collected without a second counter. */
static size_t mbrtoc8(char8_t *__c, const char *__s, size_t __n,
                      mbstate_t *__st)
{
    static mbstate_t __own;
    char __buf[8];
    wchar_t __w = 0;
    size_t __r, __k, __i;
    if (__st == NULL) __st = &__own;
    if (__st->__count > 0) {
        if (__c) *__c = (char8_t)(__st->__value & 0xFFu);
        __st->__value >>= 8;
        __st->__count--;
        return (size_t)-3;
    }
    __r = mbrtowc(&__w, __s, __n, __st);
    if (__r == (size_t)-1 || __r == (size_t)-2) return __r;
    __k = wcrtomb(__buf, __w, __st);
    if (__k == (size_t)-1) return (size_t)-1;
    if (__c) *__c = (char8_t)(unsigned char)__buf[0];
    __st->__value = 0;
    for (__i = __k; __i > 1; __i--)
        __st->__value = (__st->__value << 8)
                      | (unsigned int)(unsigned char)__buf[__i - 1];
    __st->__count = (int)__k - 1;
    return __r;
}

static size_t c8rtomb(char *__s, char8_t __c, mbstate_t *__st)
{
    static mbstate_t __own;
    unsigned int __need, __have = 0, __lead;
    size_t __i;
    if (__st == NULL) __st = &__own;
    if (__s == NULL) { __st->__count = 0; __st->__value = 0; return 1; }
    while (__have < 4 && ((__st->__value >> (__have * 8)) & 0xFFu) != 0)
        __have++;
    if (__have == 0) {
        if (__c < 0x80) { __s[0] = (char)__c; return 1; }
        if ((__c & 0xE0u) == 0xC0u) __need = 2;
        else if ((__c & 0xF0u) == 0xE0u) __need = 3;
        else if ((__c & 0xF8u) == 0xF0u) __need = 4;
        else return (size_t)-1;
        __st->__value = __c;
        __st->__count = (int)__need;
        return 0;
    }
    if ((__c & 0xC0u) != 0x80u) {
        __st->__count = 0; __st->__value = 0;
        return (size_t)-1;
    }
    __st->__value |= ((unsigned int)__c) << (__have * 8);
    __have++;
    __lead = __st->__value & 0xFFu;
    __need = (__lead & 0xE0u) == 0xC0u ? 2u
           : (__lead & 0xF0u) == 0xE0u ? 3u : 4u;
    if (__have < __need) return 0;
    for (__i = 0; __i < __need; __i++)
        __s[__i] = (char)((__st->__value >> (__i * 8)) & 0xFFu);
    __st->__count = 0;
    __st->__value = 0;
    return (size_t)__need;
}

static size_t mbrtoc32(char32_t *__c, const char *__s, size_t __n,
                       mbstate_t *__st)
{ wchar_t w = 0; size_t r = mbrtowc(&w, __s, __n, __st);
  if (__c && r != (size_t)-1 && r != (size_t)-2) *__c = (char32_t)w;
  return r; }

static size_t c32rtomb(char *__s, char32_t __c, mbstate_t *__st)
{ return wcrtomb(__s, (wchar_t)__c, __st); }

static size_t mbrtoc16(char16_t *__c, const char *__s, size_t __n,
                       mbstate_t *__st)
{
    wchar_t w = 0;
    size_t r;
    /* THE PENDING LOW SURROGATE lives in the state, which is what `mbstate_t`
       is for: a character above the basic plane is TWO `char16_t` and the
       standard says the second comes from a call that consumes no input. */
    if (__st && __st->__count == 1) {
        if (__c) *__c = (char16_t)__st->__value;
        __st->__count = 0;
        return (size_t)-3;
    }
    r = mbrtowc(&w, __s, __n, __st);
    if (r == (size_t)-1 || r == (size_t)-2) return r;
    if ((unsigned int)w >= 0x10000u) {
        unsigned int v = (unsigned int)w - 0x10000u;
        if (__c) *__c = (char16_t)(0xD800u | (v >> 10));
        if (__st) { __st->__count = 1; __st->__value = 0xDC00u | (v & 0x3FFu); }
        return r;
    }
    if (__c) *__c = (char16_t)w;
    return r;
}

static size_t c16rtomb(char *__s, char16_t __c, mbstate_t *__st)
{
    if (__st && __c >= 0xD800 && __c < 0xDC00) {
        __st->__count = 1; __st->__value = __c;
        return 0;
    }
    if (__st && __st->__count == 1 && __c >= 0xDC00 && __c < 0xE000) {
        unsigned int v = 0x10000u + (((__st->__value - 0xD800u) << 10)
                                     | (__c - 0xDC00u));
        __st->__count = 0;
        return wcrtomb(__s, (wchar_t)v, __st);
    }
    return wcrtomb(__s, (wchar_t)__c, __st);
}

#endif
