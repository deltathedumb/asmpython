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
#define BOOL_WIDTH 1
#define CHAR_WIDTH 8
#define SHRT_WIDTH 16
#define INT_WIDTH 32
#define LONG_WIDTH 64
#define LLONG_WIDTH 64

#endif
