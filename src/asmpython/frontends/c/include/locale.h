/* <locale.h> -- the "C" locale, and only that one.

   There is no operating system underneath to ask for another. `setlocale`
   answers "C" when that is what was asked for and NULL otherwise, which is
   the standard's own way of saying a locale is unavailable -- a program that
   checks the return value gets a correct answer rather than a wrong one. */
#ifndef _ASMPYTHON_LOCALE_H
#define _ASMPYTHON_LOCALE_H

#include <stddef.h>

#define LC_ALL 0
#define LC_COLLATE 1
#define LC_CTYPE 2
#define LC_MONETARY 3
#define LC_NUMERIC 4
#define LC_TIME 5

struct lconv {
    char *decimal_point, *thousands_sep, *grouping;
    char *mon_decimal_point, *mon_thousands_sep, *mon_grouping;
    char *positive_sign, *negative_sign, *currency_symbol;
    char *int_curr_symbol;
    char frac_digits, p_cs_precedes, p_sep_by_space, n_cs_precedes;
    char n_sep_by_space, p_sign_posn, n_sign_posn, int_frac_digits;
    char int_p_cs_precedes, int_p_sep_by_space, int_n_cs_precedes;
    char int_n_sep_by_space, int_p_sign_posn, int_n_sign_posn;
};

static char __locale_c[] = "C";
static char __locale_empty[] = "";
static char __locale_point[] = ".";
static struct lconv __locale_conv;

static char *setlocale(int __cat, const char *__name)
{
    (void)__cat;
    if (__name == 0) return __locale_c;
    if (__name[0] == 0) return __locale_c;
    if (__name[0] == 'C' && __name[1] == 0) return __locale_c;
    return 0;                   /* any other locale is unavailable */
}

static struct lconv *localeconv(void)
{
    __locale_conv.decimal_point = __locale_point;
    __locale_conv.thousands_sep = __locale_empty;
    __locale_conv.grouping = __locale_empty;
    __locale_conv.mon_decimal_point = __locale_empty;
    __locale_conv.mon_thousands_sep = __locale_empty;
    __locale_conv.mon_grouping = __locale_empty;
    __locale_conv.positive_sign = __locale_empty;
    __locale_conv.negative_sign = __locale_empty;
    __locale_conv.currency_symbol = __locale_empty;
    __locale_conv.int_curr_symbol = __locale_empty;
    __locale_conv.frac_digits = (char)255;
    __locale_conv.int_frac_digits = (char)255;
    return &__locale_conv;
}

#endif
