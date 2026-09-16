/* <stdbit.h> -- C23's bit utilities, over the builtins.

   Every one of these is one instruction on every machine and a loop in C, so
   they are `__builtin_*` here for the same reason they are there: see
   `builtins.py`. The `_uc`/`_us`/`_ui`/`_ul`/`_ull` families differ only in
   the width they count within, which is what makes the generic macro at the
   bottom a `_Generic` over the argument. */
#ifndef _UASM_STDBIT_H
#define _UASM_STDBIT_H

#define __STDC_VERSION_STDBIT_H__ 202311L
#define __STDC_ENDIAN_LITTLE__ 1234
#define __STDC_ENDIAN_BIG__ 4321
#define __STDC_ENDIAN_NATIVE__ __STDC_ENDIAN_LITTLE__

#include <stdint.h>

static unsigned int stdc_leading_zeros_ui(unsigned int __v)
{ return __v ? (unsigned)__builtin_clz(__v) : 32u; }
static unsigned int stdc_leading_zeros_ull(unsigned long long __v)
{ return __v ? (unsigned)__builtin_clzll(__v) : 64u; }
static unsigned int stdc_leading_zeros_uc(unsigned char __v)
{ return __v ? (unsigned)__builtin_clz((unsigned)__v) - 24u : 8u; }
static unsigned int stdc_leading_zeros_us(unsigned short __v)
{ return __v ? (unsigned)__builtin_clz((unsigned)__v) - 16u : 16u; }
static unsigned int stdc_leading_zeros_ul(unsigned long __v)
{ return stdc_leading_zeros_ull((unsigned long long)__v); }

static unsigned int stdc_trailing_zeros_ui(unsigned int __v)
{ return __v ? (unsigned)__builtin_ctz(__v) : 32u; }
static unsigned int stdc_trailing_zeros_ull(unsigned long long __v)
{ return __v ? (unsigned)__builtin_ctzll(__v) : 64u; }
static unsigned int stdc_trailing_zeros_uc(unsigned char __v)
{ return __v ? (unsigned)__builtin_ctz((unsigned)__v) : 8u; }
static unsigned int stdc_trailing_zeros_us(unsigned short __v)
{ return __v ? (unsigned)__builtin_ctz((unsigned)__v) : 16u; }
static unsigned int stdc_trailing_zeros_ul(unsigned long __v)
{ return stdc_trailing_zeros_ull((unsigned long long)__v); }

static unsigned int stdc_count_ones_ui(unsigned int __v)
{ return (unsigned)__builtin_popcount(__v); }
static unsigned int stdc_count_ones_ull(unsigned long long __v)
{ return (unsigned)__builtin_popcountll(__v); }
static unsigned int stdc_count_ones_uc(unsigned char __v)
{ return (unsigned)__builtin_popcount((unsigned)__v); }
static unsigned int stdc_count_ones_us(unsigned short __v)
{ return (unsigned)__builtin_popcount((unsigned)__v); }
static unsigned int stdc_count_ones_ul(unsigned long __v)
{ return stdc_count_ones_ull((unsigned long long)__v); }

static unsigned int stdc_bit_width_ui(unsigned int __v)
{ return 32u - stdc_leading_zeros_ui(__v); }
static unsigned int stdc_bit_width_ull(unsigned long long __v)
{ return 64u - stdc_leading_zeros_ull(__v); }
static unsigned long long stdc_bit_floor_ull(unsigned long long __v)
{ return __v ? 1ULL << (stdc_bit_width_ull(__v) - 1u) : 0ULL; }
static unsigned long long stdc_bit_ceil_ull(unsigned long long __v)
{ return __v <= 1ULL ? 1ULL : 1ULL << stdc_bit_width_ull(__v - 1ULL); }
static _Bool stdc_has_single_bit_ull(unsigned long long __v)
{ return __v != 0ULL && (__v & (__v - 1ULL)) == 0ULL; }

#define stdc_leading_zeros(v) _Generic((v), \
    unsigned char: stdc_leading_zeros_uc, unsigned short: stdc_leading_zeros_us, \
    unsigned int: stdc_leading_zeros_ui, unsigned long: stdc_leading_zeros_ul, \
    default: stdc_leading_zeros_ull)(v)
#define stdc_trailing_zeros(v) _Generic((v), \
    unsigned char: stdc_trailing_zeros_uc, unsigned short: stdc_trailing_zeros_us, \
    unsigned int: stdc_trailing_zeros_ui, unsigned long: stdc_trailing_zeros_ul, \
    default: stdc_trailing_zeros_ull)(v)
#define stdc_count_ones(v) _Generic((v), \
    unsigned char: stdc_count_ones_uc, unsigned short: stdc_count_ones_us, \
    unsigned int: stdc_count_ones_ui, unsigned long: stdc_count_ones_ul, \
    default: stdc_count_ones_ull)(v)
#define stdc_bit_width(v) _Generic((v), \
    unsigned int: stdc_bit_width_ui, default: stdc_bit_width_ull)(v)
#define stdc_bit_floor(v) stdc_bit_floor_ull(v)
#define stdc_bit_ceil(v) stdc_bit_ceil_ull(v)
#define stdc_has_single_bit(v) stdc_has_single_bit_ull(v)

#endif
