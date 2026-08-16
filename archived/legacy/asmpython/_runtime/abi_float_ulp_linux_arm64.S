/* Exact CPython-compatible math.ulp for IEEE-754 binary64 on AArch64. */
.text
.balign 4

/*
 * double _math_ulp(double value)
 *
 * NaNs are returned unchanged (including payload/sign), negative finite values
 * are treated by magnitude, infinities return +inf, and zero/subnormals return
 * the minimum positive subnormal. For a normal exponent field E, the spacing
 * is 2**(E-1075): subnormal result bit 1<<(E-1) for E<=52, otherwise a normal
 * power of two with exponent field E-52. The E=2046 maximum-finite case falls
 * out naturally as 0x7ca0000000000000, the spacing to the previous float.
 */
.global _math_ulp
.type _math_ulp, %function
_math_ulp:
    fmov x9, d0
    lsl x10, x9, #1
    lsr x10, x10, #1

    movz x11, #0x7ff0, lsl #48
    cmp x10, x11
    b.hi .Lulp_nan
    b.eq .Lulp_inf

    lsr x12, x10, #52
    cbz x12, .Lulp_min_subnormal
    cmp x12, #52
    b.hi .Lulp_normal_result

    sub x12, x12, #1
    mov x9, #1
    lsl x9, x9, x12
    fmov d0, x9
    ret

.Lulp_normal_result:
    sub x12, x12, #52
    lsl x9, x12, #52
    fmov d0, x9
    ret

.Lulp_min_subnormal:
    mov x9, #1
    fmov d0, x9
    ret

.Lulp_inf:
    fmov d0, x10
.Lulp_nan:
    ret
.size _math_ulp, .-_math_ulp

.section .note.GNU-stack,"",%progbits
