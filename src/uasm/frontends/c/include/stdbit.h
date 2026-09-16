/* <stdbit.h> -- C23's bit utilities, over the builtins.

   Every one of these is one instruction on every machine and a loop in C, so
   they are `__builtin_*` here for the same reason they are there: see
   `builtins.py`.

   FIVE WIDTHS AND FOURTEEN OPERATIONS IS SEVENTY FUNCTIONS, which is a
   hand-written list with seventy chances to count within the wrong width --
   and counting within the wrong width is exactly the bug that would be
   invisible, because `stdc_leading_zeros_uc(1)` answering 31 instead of 7 is
   a plausible-looking number. So one macro writes the whole family for a
   width, and the width appears once in each expansion.

   THE COUNTING IS DONE IN 64 BITS AND CORRECTED, which is what makes the
   macro possible: `__builtin_clzll` of a zero-extended value counts the
   64-bit leading zeros, and the ones above the type's own width are the
   difference between 64 and W. `ctzll` needs no correction -- a zero bit
   below the value is a zero bit in either width -- except for the value 0,
   which every operation here has a named answer for. */
#ifndef _UASM_STDBIT_H
#define _UASM_STDBIT_H

#define __STDC_VERSION_STDBIT_H__ 202311L
#define __STDC_ENDIAN_LITTLE__ 1234
#define __STDC_ENDIAN_BIG__ 4321
#define __STDC_ENDIAN_NATIVE__ __STDC_ENDIAN_LITTLE__

#include <stdint.h>
#include <stdbool.h>

/* THE POSITION IS ONE-BASED AND 0 MEANS "THERE IS NONE", which is C23's
   convention for the `first_*` four and the one thing about this header that
   a reader will not guess: `stdc_first_leading_one_uc(0x80)` is 1, and
   `stdc_first_leading_one_uc(0)` is 0 rather than 9. */
#define __STDBIT(SUF, T, W)                                                   \
    static unsigned int stdc_leading_zeros_##SUF(T __v)                       \
    { return __v ? (unsigned int)__builtin_clzll((unsigned long long)__v)     \
                   - (64u - (W)) : (unsigned int)(W); }                       \
    static unsigned int stdc_leading_ones_##SUF(T __v)                        \
    { return stdc_leading_zeros_##SUF((T)~__v); }                             \
    static unsigned int stdc_trailing_zeros_##SUF(T __v)                      \
    { return __v ? (unsigned int)__builtin_ctzll((unsigned long long)__v)     \
                 : (unsigned int)(W); }                                       \
    static unsigned int stdc_trailing_ones_##SUF(T __v)                       \
    { return stdc_trailing_zeros_##SUF((T)~__v); }                            \
    static unsigned int stdc_count_ones_##SUF(T __v)                          \
    { return (unsigned int)__builtin_popcountll((unsigned long long)__v); }   \
    static unsigned int stdc_count_zeros_##SUF(T __v)                         \
    { return (unsigned int)(W) - stdc_count_ones_##SUF(__v); }                \
    static unsigned int stdc_first_leading_zero_##SUF(T __v)                  \
    { unsigned int __n = stdc_leading_ones_##SUF(__v);                        \
      return __n == (unsigned int)(W) ? 0u : __n + 1u; }                      \
    static unsigned int stdc_first_leading_one_##SUF(T __v)                   \
    { unsigned int __n = stdc_leading_zeros_##SUF(__v);                       \
      return __n == (unsigned int)(W) ? 0u : __n + 1u; }                      \
    static unsigned int stdc_first_trailing_zero_##SUF(T __v)                 \
    { unsigned int __n = stdc_trailing_ones_##SUF(__v);                       \
      return __n == (unsigned int)(W) ? 0u : __n + 1u; }                      \
    static unsigned int stdc_first_trailing_one_##SUF(T __v)                  \
    { unsigned int __n = stdc_trailing_zeros_##SUF(__v);                      \
      return __n == (unsigned int)(W) ? 0u : __n + 1u; }                      \
    static bool stdc_has_single_bit_##SUF(T __v)                              \
    { return __v != 0 && (T)(__v & (T)(__v - 1)) == 0; }                      \
    static unsigned int stdc_bit_width_##SUF(T __v)                           \
    { return (unsigned int)(W) - stdc_leading_zeros_##SUF(__v); }             \
    static T stdc_bit_floor_##SUF(T __v)                                      \
    { return __v ? (T)((T)1 << (stdc_bit_width_##SUF(__v) - 1u)) : (T)0; }    \
    static T stdc_bit_ceil_##SUF(T __v)                                       \
    { return __v <= (T)1 ? (T)1                                               \
                         : (T)((T)1 << stdc_bit_width_##SUF((T)(__v - 1))); }

__STDBIT(uc, unsigned char, 8)
__STDBIT(us, unsigned short, 16)
__STDBIT(ui, unsigned int, 32)
__STDBIT(ul, unsigned long, 64)
__STDBIT(ull, unsigned long long, 64)

/* THE GENERIC MACRO PICKS BY THE TYPE and there are five of them, so the
   `_Generic` is written once too. `default` is the `ull` one, which is what
   an argument of any other unsigned type widens to. */
#define __STDBIT_G(NAME, v) _Generic((v), \
    unsigned char: NAME##_uc, \
    unsigned short: NAME##_us, \
    unsigned int: NAME##_ui, \
    unsigned long: NAME##_ul, \
    default: NAME##_ull)(v)

#define stdc_leading_zeros(v)       __STDBIT_G(stdc_leading_zeros, v)
#define stdc_leading_ones(v)        __STDBIT_G(stdc_leading_ones, v)
#define stdc_trailing_zeros(v)      __STDBIT_G(stdc_trailing_zeros, v)
#define stdc_trailing_ones(v)       __STDBIT_G(stdc_trailing_ones, v)
#define stdc_first_leading_zero(v)  __STDBIT_G(stdc_first_leading_zero, v)
#define stdc_first_leading_one(v)   __STDBIT_G(stdc_first_leading_one, v)
#define stdc_first_trailing_zero(v) __STDBIT_G(stdc_first_trailing_zero, v)
#define stdc_first_trailing_one(v)  __STDBIT_G(stdc_first_trailing_one, v)
#define stdc_count_zeros(v)         __STDBIT_G(stdc_count_zeros, v)
#define stdc_count_ones(v)          __STDBIT_G(stdc_count_ones, v)
#define stdc_has_single_bit(v)      __STDBIT_G(stdc_has_single_bit, v)
#define stdc_bit_width(v)           __STDBIT_G(stdc_bit_width, v)
#define stdc_bit_floor(v)           __STDBIT_G(stdc_bit_floor, v)
#define stdc_bit_ceil(v)            __STDBIT_G(stdc_bit_ceil, v)

#endif
