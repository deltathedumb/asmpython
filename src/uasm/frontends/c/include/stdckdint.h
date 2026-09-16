/* <stdckdint.h> -- C23's checked integer arithmetic.

   `ckd_add(&r, a, b)` stores the wrapped result and answers whether the true
   result would not fit. The check is done in the WIDEST type and then
   narrowed, which is why each of these is a statement expression rather than
   a function: the result type is the caller's, and there is no way to write a
   function that takes it. */
#ifndef _UASM_STDCKDINT_H
#define _UASM_STDCKDINT_H

#define __STDC_VERSION_STDCKDINT_H__ 202311L

#include <stdint.h>

#define __ckd_fits(r, wide) \
    ((__typeof__(*(r)))(wide) == (wide))

#define ckd_add(r, a, b) ({ __typeof__(*(r)) *__r = (r); \
    intmax_t __w = (intmax_t)(a) + (intmax_t)(b); \
    *__r = (__typeof__(*(r)))__w; !((__typeof__(*(r)))__w == __w); })
#define ckd_sub(r, a, b) ({ __typeof__(*(r)) *__r = (r); \
    intmax_t __w = (intmax_t)(a) - (intmax_t)(b); \
    *__r = (__typeof__(*(r)))__w; !((__typeof__(*(r)))__w == __w); })
#define ckd_mul(r, a, b) ({ __typeof__(*(r)) *__r = (r); \
    intmax_t __x = (intmax_t)(a), __y = (intmax_t)(b); \
    intmax_t __w = __x * __y; \
    _Bool __over = (__x != 0 && __w / __x != __y); \
    *__r = (__typeof__(*(r)))__w; \
    __over || !((__typeof__(*(r)))__w == __w); })

#endif
