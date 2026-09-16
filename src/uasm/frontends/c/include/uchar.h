/* <uchar.h> -- the UTF-16 and UTF-32 character types.

   `char32_t` and `wchar_t` are the same width and hold the same thing here,
   so the conversions are `<wchar.h>`'s with a rename; `char16_t` needs the
   surrogate pair that UTF-16 uses above the basic plane, which is the only
   real work in this file. */
#ifndef _UASM_UCHAR_H
#define _UASM_UCHAR_H

#include <wchar.h>

typedef unsigned char char8_t;
typedef unsigned short char16_t;
typedef unsigned int char32_t;

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
