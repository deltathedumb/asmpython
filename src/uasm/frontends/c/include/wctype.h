/* <wctype.h> -- ASCII only, and it says so.

   The classification a hosted implementation does outside ASCII needs a
   Unicode table, and this library has none; every one of these answers for
   the ASCII range and false above it. A program that classifies a letter with
   an accent gets a wrong answer here and would get a right one from glibc --
   which is worth knowing before relying on it. */
#ifndef _UASM_WCTYPE_H
#define _UASM_WCTYPE_H

#include <wchar.h>

typedef unsigned int wctype_t;
typedef unsigned int wctrans_t;

static int iswdigit(wint_t __c) { return __c >= '0' && __c <= '9'; }
static int iswlower(wint_t __c) { return __c >= 'a' && __c <= 'z'; }
static int iswupper(wint_t __c) { return __c >= 'A' && __c <= 'Z'; }
static int iswalpha(wint_t __c) { return iswlower(__c) || iswupper(__c); }
static int iswalnum(wint_t __c) { return iswalpha(__c) || iswdigit(__c); }
static int iswxdigit(wint_t __c)
{ return iswdigit(__c) || (__c >= 'a' && __c <= 'f') || (__c >= 'A' && __c <= 'F'); }
static int iswspace(wint_t __c)
{ return __c == ' ' || __c == '\t' || __c == '\n' || __c == '\v'
      || __c == '\f' || __c == '\r'; }
static int iswblank(wint_t __c) { return __c == ' ' || __c == '\t'; }
static int iswcntrl(wint_t __c) { return __c < 32 || __c == 127; }
static int iswprint(wint_t __c) { return __c >= 32 && __c != 127; }
static int iswgraph(wint_t __c) { return __c > 32 && __c != 127; }
static int iswpunct(wint_t __c) { return iswgraph(__c) && !iswalnum(__c); }
static wint_t towlower(wint_t __c) { return iswupper(__c) ? __c + 32 : __c; }
static wint_t towupper(wint_t __c) { return iswlower(__c) ? __c - 32 : __c; }


/* ── the ones that name a property at run time ────────────────────────── */
/* `wctype("alpha")` EXISTS SO THAT A PROGRAM CAN ASK FOR A CLASS IT READ
   FROM SOMEWHERE -- a configuration file, an argument -- rather than naming
   one at compile time. The twelve standard properties are numbered here and
   `iswctype` dispatches on the number; a name that is not one of them
   answers 0, which `iswctype` then reports false for, as C requires.

   WHY A SWITCH AND NOT A TABLE OF POINTERS: these are the same twelve
   functions above, and a pointer table would be twelve more symbols for the
   linker to keep alive in a program that called `wctype` once. */
static wctype_t wctype(const char *__name)
{
    static const char *const __names[] = {
        "alnum", "alpha", "blank", "cntrl", "digit", "graph", "lower",
        "print", "punct", "space", "upper", "xdigit"
    };
    unsigned int __i, __j;
    for (__i = 0; __i < 12u; __i++) {
        const char *__a = __names[__i], *__b = __name;
        for (__j = 0; __a[__j] && __a[__j] == __b[__j]; __j++) ;
        if (__a[__j] == 0 && __b[__j] == 0) return (wctype_t)(__i + 1u);
    }
    return (wctype_t)0;
}

static int iswctype(wint_t __c, wctype_t __desc)
{
    switch (__desc) {
    case 1: return iswalnum(__c);
    case 2: return iswalpha(__c);
    case 3: return iswblank(__c);
    case 4: return iswcntrl(__c);
    case 5: return iswdigit(__c);
    case 6: return iswgraph(__c);
    case 7: return iswlower(__c);
    case 8: return iswprint(__c);
    case 9: return iswpunct(__c);
    case 10: return iswspace(__c);
    case 11: return iswupper(__c);
    case 12: return iswxdigit(__c);
    default: return 0;
    }
}

/* TWO MAPPINGS AND NO MORE, which is all C requires: `"tolower"` and
   `"toupper"`. A locale may add others and this one has none to add. */
static wctrans_t wctrans(const char *__name)
{
    if (__name[0] == 't' && __name[1] == 'o') {
        if (__name[2] == 'l' && __name[3] == 'o' && __name[4] == 'w'
            && __name[5] == 'e' && __name[6] == 'r' && __name[7] == 0)
            return (wctrans_t)1;
        if (__name[2] == 'u' && __name[3] == 'p' && __name[4] == 'p'
            && __name[5] == 'e' && __name[6] == 'r' && __name[7] == 0)
            return (wctrans_t)2;
    }
    return (wctrans_t)0;
}

static wint_t towctrans(wint_t __c, wctrans_t __desc)
{
    if (__desc == 1) return towlower(__c);
    if (__desc == 2) return towupper(__c);
    return __c;
}

#endif
