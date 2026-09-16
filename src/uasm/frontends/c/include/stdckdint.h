/* <stdckdint.h> -- C23's checked integer arithmetic.

   `ckd_add(&r, a, b)` stores the wrapped result and answers whether the
   true result would not fit.

   NOT COMPUTED IN A WIDER TYPE, because there is not one. The obvious
   implementation widens both operands to `intmax_t`, does the arithmetic
   and asks whether the answer survives the narrowing -- and that is wrong
   for exactly the cases the header exists for: `INTMAX_MAX + 1` overflows
   the wide type too and wraps to a value that fits, so the check answers
   "no overflow" for an overflow, and an unsigned operand above `INTMAX_MAX`
   is already negative before the arithmetic starts. Sixty-four bits is the
   widest integer here, so there is no type to escape into.

   SO THE MATHEMATICAL VALUE IS CARRIED AS A SIGN AND A MAGNITUDE. Every
   operand becomes one of those, exactly, whatever its type; the three
   operations are defined on them, with a flag for a magnitude that leaves
   even the unsigned range; and the fit is decided against the DESTINATION's
   width and signedness, which the macro knows and a function cannot. The
   stored value is the low bits, which are right whether or not it fitted --
   C asks for the mathematical result modulo 2^N either way. */
#ifndef _UASM_STDCKDINT_H
#define _UASM_STDCKDINT_H

#define __STDC_VERSION_STDCKDINT_H__ 202311L

#include <stdint.h>

typedef struct {
    _Bool __neg;
    uintmax_t __mag;
    /* THE MAGNITUDE ITSELF DID NOT FIT: a sum that carried out of
       `uintmax_t`, or a product that wrapped. `__mag` is then the low bits
       and nothing smaller can hold the value, so the answer is always
       "does not fit". */
    _Bool __huge;
} __ckd_val;

static __ckd_val __ckd_of(_Bool __neg, uintmax_t __mag)
{
    __ckd_val __v;
    /* NEGATIVE ZERO IS ZERO, once, here -- so no comparison below has to
       remember that `-0` and `0` are the same number. */
    __v.__neg = __neg && __mag != 0;
    __v.__mag = __mag;
    __v.__huge = 0;
    return __v;
}

static __ckd_val __ckd_negv(__ckd_val __v)
{ return __ckd_of(!__v.__neg, __v.__mag); }

static __ckd_val __ckd_addv(__ckd_val __a, __ckd_val __b)
{
    __ckd_val __r;
    if (__a.__neg == __b.__neg) {
        uintmax_t __s = __a.__mag + __b.__mag;
        __r = __ckd_of(__a.__neg, __s);
        __r.__huge = __a.__huge || __b.__huge || __s < __a.__mag;
        return __r;
    }
    /* OPPOSITE SIGNS SUBTRACT, and the sign is the larger magnitude's. */
    if (__a.__mag >= __b.__mag)
        __r = __ckd_of(__a.__neg, __a.__mag - __b.__mag);
    else
        __r = __ckd_of(__b.__neg, __b.__mag - __a.__mag);
    __r.__huge = __a.__huge || __b.__huge;
    return __r;
}

static __ckd_val __ckd_mulv(__ckd_val __a, __ckd_val __b)
{
    uintmax_t __p = __a.__mag * __b.__mag;
    __ckd_val __r = __ckd_of(__a.__neg != __b.__neg, __p);
    __r.__huge = __a.__huge || __b.__huge
              || (__a.__mag != 0 && __p / __a.__mag != __b.__mag);
    return __r;
}

/* DOES IT FIT A TYPE `__bits` WIDE, signed or not. The limits are written
   as a shift of one rather than as a constant, because the destination is
   whatever the caller's pointer points at and there is no macro naming
   that type's maximum. A 64-bit unsigned limit is the special case: the
   shift that would produce it is the one C leaves undefined. */
static _Bool __ckd_fits(__ckd_val __v, unsigned __bits, _Bool __is_signed)
{
    uintmax_t __limit;
    if (__v.__huge) return 0;
    if (!__is_signed) {
        if (__v.__neg) return 0;
        __limit = __bits >= 64 ? (uintmax_t)-1
                               : (((uintmax_t)1 << __bits) - 1);
        return __v.__mag <= __limit;
    }
    /* A SIGNED TYPE REACHES ONE FURTHER DOWNWARDS than up. */
    __limit = (uintmax_t)1 << (__bits - 1);
    return __v.__neg ? __v.__mag <= __limit : __v.__mag < __limit;
}

static uintmax_t __ckd_bits(__ckd_val __v)
{ return __v.__neg ? (uintmax_t)0 - __v.__mag : __v.__mag; }

/* AN OPERAND'S MATHEMATICAL VALUE. `(x) < 0` is false for every unsigned
   type, so the cast below is the value itself there; for a negative one,
   subtracting the two's-complement pattern from zero IS the magnitude,
   `INTMAX_MIN` included. */
#define __ckd_arg(x) \
    __ckd_of((x) < 0, (x) < 0 ? (uintmax_t)0 - (uintmax_t)(x) \
                              : (uintmax_t)(x))

/* A STATEMENT EXPRESSION AND NOT A FUNCTION, because the result type is
   the caller's and no function can take it. */
#define __ckd_do(r, v) ({ \
    __typeof__(*(r)) *__ckd_r = (r); \
    __ckd_val __ckd_v = (v); \
    *__ckd_r = (__typeof__(*(r)))__ckd_bits(__ckd_v); \
    !__ckd_fits(__ckd_v, (unsigned)(sizeof *__ckd_r * 8), \
                (__typeof__(*(r)))-1 < 0); })

#define ckd_add(r, a, b) __ckd_do(r, __ckd_addv(__ckd_arg(a), __ckd_arg(b)))
#define ckd_sub(r, a, b) \
    __ckd_do(r, __ckd_addv(__ckd_arg(a), __ckd_negv(__ckd_arg(b))))
#define ckd_mul(r, a, b) __ckd_do(r, __ckd_mulv(__ckd_arg(a), __ckd_arg(b)))

#endif
