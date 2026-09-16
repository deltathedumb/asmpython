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

#endif
