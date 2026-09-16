/* <limits.h> -- the ranges, taken from the compiler's own macros.
   Written in terms of __*_MAX__ so this file cannot disagree with `sizeof`. */
#ifndef _UASM_LIMITS_H
#define _UASM_LIMITS_H

#define CHAR_BIT   __CHAR_BIT__
#define SCHAR_MAX  __SCHAR_MAX__
#define SCHAR_MIN  (-__SCHAR_MAX__ - 1)
#define UCHAR_MAX  (__SCHAR_MAX__ * 2 + 1)

/* PLAIN `char` IS SIGNED here. `literals.py` decides that, once, and this
   follows it -- `__CHAR_UNSIGNED__` is deliberately not defined. */
#define CHAR_MAX   SCHAR_MAX
#define CHAR_MIN   SCHAR_MIN

#define SHRT_MAX   __SHRT_MAX__
#define SHRT_MIN   (-__SHRT_MAX__ - 1)
#define USHRT_MAX  (__SHRT_MAX__ * 2 + 1)

#define INT_MAX    __INT_MAX__
#define INT_MIN    (-__INT_MAX__ - 1)
#define UINT_MAX   (__INT_MAX__ * 2U + 1U)

#define LONG_MAX   __LONG_MAX__
#define LONG_MIN   (-__LONG_MAX__ - 1L)
#define ULONG_MAX  (__LONG_MAX__ * 2UL + 1UL)

#define LLONG_MAX  __LONG_LONG_MAX__
#define LLONG_MIN  (-__LONG_LONG_MAX__ - 1LL)
#define ULLONG_MAX (__LONG_LONG_MAX__ * 2ULL + 1ULL)

#define MB_LEN_MAX 4

/* THE WIDTHS C23 ASKS FOR, and the unsigned ones are the same numbers: a
   type and its unsigned counterpart have the same width, which is what
   makes `UINT_WIDTH` worth defining at all rather than leaving the reader
   to wonder whether it counts the sign bit. */
#define BOOL_WIDTH 1
#define CHAR_WIDTH 8
#define SCHAR_WIDTH 8
#define UCHAR_WIDTH 8
#define SHRT_WIDTH 16
#define USHRT_WIDTH 16
#define INT_WIDTH 32
#define UINT_WIDTH 32
#define LONG_WIDTH 64
#define ULONG_WIDTH 64
#define LLONG_WIDTH 64
#define ULLONG_WIDTH 64
#define BOOL_MAX 1

/* THE WIDEST `_BitInt` THIS IMPLEMENTATION HAS. C23 requires only that it be
   at least `ULLONG_WIDTH`, and this is exactly that: a wider one would be
   arithmetic in software over several machine registers -- the `long double`
   story again -- for a type whose whole appeal is being a machine integer
   with a narrower range. `ctype.BITINT_MAXWIDTH` is the same number, and a
   program asking for more is refused by name. */
#define BITINT_MAXWIDTH 64

#endif
