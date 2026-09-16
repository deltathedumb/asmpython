/* <stdint.h> -- exact-width types, from the compiler's own table. */
#ifndef _UASM_STDINT_H
#define _UASM_STDINT_H
#define __STDC_VERSION_STDINT_H__ 202311L

typedef signed char        int8_t;
typedef short              int16_t;
typedef int                int32_t;
typedef long               int64_t;
typedef unsigned char      uint8_t;
typedef unsigned short     uint16_t;
typedef unsigned int       uint32_t;
typedef unsigned long      uint64_t;

typedef signed char        int_least8_t;
typedef short              int_least16_t;
typedef int                int_least32_t;
typedef long               int_least64_t;
typedef unsigned char      uint_least8_t;
typedef unsigned short     uint_least16_t;
typedef unsigned int       uint_least32_t;
typedef unsigned long      uint_least64_t;

/* THE FAST TYPES ARE THE WORD, not the smallest that fits: a 64-bit machine
   loads and stores a word without a sign-extension step, which is the whole
   point of asking for the fast one. `int_fast8_t` is the exception glibc
   makes and it is kept for compatibility with headers that assume it. */
typedef signed char        int_fast8_t;
typedef long               int_fast16_t;
typedef long               int_fast32_t;
typedef long               int_fast64_t;
typedef unsigned char      uint_fast8_t;
typedef unsigned long      uint_fast16_t;
typedef unsigned long      uint_fast32_t;
typedef unsigned long      uint_fast64_t;

typedef __INTPTR_TYPE__    intptr_t;
typedef __UINTPTR_TYPE__   uintptr_t;
typedef __INTMAX_TYPE__    intmax_t;
typedef __UINTMAX_TYPE__   uintmax_t;

#define INT8_MAX    127
#define INT8_MIN    (-128)
#define UINT8_MAX   255
#define INT16_MAX   32767
#define INT16_MIN   (-32768)
#define UINT16_MAX  65535
#define INT32_MAX   2147483647
#define INT32_MIN   (-2147483647 - 1)
#define UINT32_MAX  4294967295U
#define INT64_MAX   9223372036854775807L
#define INT64_MIN   (-9223372036854775807L - 1)
#define UINT64_MAX  18446744073709551615UL

#define INT_LEAST8_MAX INT8_MAX
#define INT_LEAST8_MIN INT8_MIN
#define UINT_LEAST8_MAX UINT8_MAX
#define INT_LEAST16_MAX INT16_MAX
#define INT_LEAST16_MIN INT16_MIN
#define UINT_LEAST16_MAX UINT16_MAX
#define INT_LEAST32_MAX INT32_MAX
#define INT_LEAST32_MIN INT32_MIN
#define UINT_LEAST32_MAX UINT32_MAX
#define INT_LEAST64_MAX INT64_MAX
#define INT_LEAST64_MIN INT64_MIN
#define UINT_LEAST64_MAX UINT64_MAX

#define INT_FAST8_MAX INT8_MAX
#define INT_FAST8_MIN INT8_MIN
#define UINT_FAST8_MAX UINT8_MAX
#define INT_FAST16_MAX INT64_MAX
#define INT_FAST16_MIN INT64_MIN
#define UINT_FAST16_MAX UINT64_MAX
#define INT_FAST32_MAX INT64_MAX
#define INT_FAST32_MIN INT64_MIN
#define UINT_FAST32_MAX UINT64_MAX
#define INT_FAST64_MAX INT64_MAX
#define INT_FAST64_MIN INT64_MIN
#define UINT_FAST64_MAX UINT64_MAX

#define INTPTR_MAX  INT64_MAX
#define INTPTR_MIN  INT64_MIN
#define UINTPTR_MAX UINT64_MAX
#define INTMAX_MAX  INT64_MAX
#define INTMAX_MIN  INT64_MIN
#define UINTMAX_MAX UINT64_MAX
#define PTRDIFF_MAX __PTRDIFF_MAX__
#define PTRDIFF_MIN (-__PTRDIFF_MAX__ - 1)
#define SIZE_MAX    __SIZE_MAX__
#define WCHAR_MAX   __WCHAR_MAX__
#define WCHAR_MIN   __WCHAR_MIN__
#define WINT_MAX    INT32_MAX
#define WINT_MIN    INT32_MIN
#define SIG_ATOMIC_MAX INT32_MAX
#define SIG_ATOMIC_MIN INT32_MIN

/* THE WIDTHS, which C23 asks for beside every limit above. A width
   counts the sign bit where there is one, so a signed type and its
   unsigned counterpart have the SAME width -- `INT8_WIDTH` and
   `UINT8_WIDTH` are both 8, and neither is 7. */
#define INT8_WIDTH  8
#define UINT8_WIDTH 8
#define INT16_WIDTH  16
#define UINT16_WIDTH 16
#define INT32_WIDTH  32
#define UINT32_WIDTH 32
#define INT64_WIDTH  64
#define UINT64_WIDTH 64
#define INT_LEAST8_WIDTH  8
#define UINT_LEAST8_WIDTH 8
#define INT_LEAST16_WIDTH  16
#define UINT_LEAST16_WIDTH 16
#define INT_LEAST32_WIDTH  32
#define UINT_LEAST32_WIDTH 32
#define INT_LEAST64_WIDTH  64
#define UINT_LEAST64_WIDTH 64

/* AND THE FAST ONES ARE THE WIDTHS OF THE TYPES ABOVE and not of the
   numbers they were asked for: `int_fast16_t` is a `long` here, so
   `INT_FAST16_WIDTH` is 64. A macro saying 16 would be describing a
   different type from the one the header declares. */
#define INT_FAST8_WIDTH  8
#define UINT_FAST8_WIDTH 8
#define INT_FAST16_WIDTH  64
#define UINT_FAST16_WIDTH 64
#define INT_FAST32_WIDTH  64
#define UINT_FAST32_WIDTH 64
#define INT_FAST64_WIDTH  64
#define UINT_FAST64_WIDTH 64

#define INTPTR_WIDTH     64
#define UINTPTR_WIDTH    64
#define INTMAX_WIDTH     64
#define UINTMAX_WIDTH    64
#define PTRDIFF_WIDTH    64
#define SIZE_WIDTH       64
#define SIG_ATOMIC_WIDTH 32
#define WCHAR_WIDTH      32
#define WINT_WIDTH       32

#define INT8_C(v)   v
#define INT16_C(v)  v
#define INT32_C(v)  v
#define INT64_C(v)  v ## L
#define UINT8_C(v)  v ## U
#define UINT16_C(v) v ## U
#define UINT32_C(v) v ## U
#define UINT64_C(v) v ## UL
#define INTMAX_C(v)  v ## L
#define UINTMAX_C(v) v ## UL

#endif
