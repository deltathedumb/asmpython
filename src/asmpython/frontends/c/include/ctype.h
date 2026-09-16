/* <ctype.h> -- ASCII only, and it says so.

   The standard defines these in terms of the current locale, and the only
   locale this library has is "C". A program that needs `isalpha` to be true
   for a letter outside ASCII needs a Unicode table; this one has none and
   answers false rather than guessing.

   THE ARGUMENT IS AN `int` HOLDING AN `unsigned char` OR `EOF`, which is why
   every one of these takes an int and why passing a `char` that is negative
   is undefined. The range check below makes that case answer false rather
   than reading off the front of a table -- there is no table, so it is free. */
#ifndef _ASMPYTHON_CTYPE_H
#define _ASMPYTHON_CTYPE_H

static int isdigit(int __c) { return __c >= '0' && __c <= '9'; }
static int isxdigit(int __c)
{
    return (__c >= '0' && __c <= '9') || (__c >= 'a' && __c <= 'f')
        || (__c >= 'A' && __c <= 'F');
}
static int islower(int __c) { return __c >= 'a' && __c <= 'z'; }
static int isupper(int __c) { return __c >= 'A' && __c <= 'Z'; }
static int isalpha(int __c) { return islower(__c) || isupper(__c); }
static int isalnum(int __c) { return isalpha(__c) || isdigit(__c); }
static int isspace(int __c)
{
    return __c == ' ' || __c == '\t' || __c == '\n' || __c == '\v'
        || __c == '\f' || __c == '\r';
}
static int isblank(int __c) { return __c == ' ' || __c == '\t'; }
static int iscntrl(int __c) { return (__c >= 0 && __c < 32) || __c == 127; }
static int isprint(int __c) { return __c >= 32 && __c < 127; }
static int isgraph(int __c) { return __c > 32 && __c < 127; }
static int ispunct(int __c) { return isgraph(__c) && !isalnum(__c); }
static int tolower(int __c) { return isupper(__c) ? __c + 32 : __c; }
static int toupper(int __c) { return islower(__c) ? __c - 32 : __c; }

#endif
