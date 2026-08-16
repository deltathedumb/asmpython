/* Exact binary64 frexp helpers for AArch64. */
.text
.balign 4

/* double _math_frexp_m(double value) -- signed mantissa in [0.5, 1). */
.global _math_frexp_m
.type _math_frexp_m, %function
_math_frexp_m:
    fmov    x9, d0
    lsl     x10, x9, #1
    lsr     x10, x10, #1
    lsr     x11, x10, #52
    cmp     x11, #0x7ff
    b.eq    .Lfrexp_m_done

    mov     x12, #-1
    lsr     x12, x12, #12
    cbnz    x11, .Lfrexp_m_normal
    cbz     x10, .Lfrexp_m_done
    clz     x13, x10
    mov     x14, #63
    sub     x13, x14, x13
    mov     x14, #52
    sub     x14, x14, x13
    lsl     x10, x10, x14
.Lfrexp_m_normal:
    and     x10, x10, x12
    lsr     x13, x9, #63
    lsl     x13, x13, #63
    orr     x10, x10, x13
    movz    x14, #0x3fe0, lsl #48
    orr     x9, x10, x14
.Lfrexp_m_done:
    fmov    d0, x9
    ret
.size _math_frexp_m, .-_math_frexp_m

/* int64 _math_frexp_e(double value) -- exponent paired with the mantissa. */
.global _math_frexp_e
.type _math_frexp_e, %function
_math_frexp_e:
    fmov    x9, d0
    lsl     x9, x9, #1
    lsr     x9, x9, #1
    lsr     x10, x9, #52
    cbz     x10, .Lfrexp_e_subnormal
    cmp     x10, #0x7ff
    b.eq    .Lfrexp_e_zero
    sub     x0, x10, #1022
    ret
.Lfrexp_e_subnormal:
    cbz     x9, .Lfrexp_e_zero
    clz     x10, x9
    mov     x11, #63
    sub     x10, x11, x10
    sub     x0, x10, #1073
    ret
.Lfrexp_e_zero:
    mov     x0, xzr
    ret
.size _math_frexp_e, .-_math_frexp_e

.section .note.GNU-stack,"",%progbits
