/* Exact IEEE-754 binary64 classification helpers for AArch64. */
.text
.balign 4

/* int _math_isnan(double value) */
.global _math_isnan
.type _math_isnan, %function
_math_isnan:
    fmov x9, d0
    lsl x9, x9, #1
    lsr x9, x9, #1
    movz x10, #0x7ff0, lsl #48
    cmp x9, x10
    cset x0, hi
    ret
.size _math_isnan, .-_math_isnan

/* int _math_isinf(double value) */
.global _math_isinf
.type _math_isinf, %function
_math_isinf:
    fmov x9, d0
    lsl x9, x9, #1
    lsr x9, x9, #1
    movz x10, #0x7ff0, lsl #48
    cmp x9, x10
    cset x0, eq
    ret
.size _math_isinf, .-_math_isinf

/* int _math_isfinite(double value) */
.global _math_isfinite
.type _math_isfinite, %function
_math_isfinite:
    fmov x9, d0
    lsl x9, x9, #1
    lsr x9, x9, #1
    movz x10, #0x7ff0, lsl #48
    cmp x9, x10
    cset x0, lo
    ret
.size _math_isfinite, .-_math_isfinite

.section .note.GNU-stack,"",%progbits
