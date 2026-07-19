/* Exact binary64 modf helpers for AArch64. */
.text
.balign 4

/* double _math_modf_int(double value) -- truncation toward zero as a float. */
.global _math_modf_int
.type _math_modf_int, %function
_math_modf_int:
    fmov    x9, d0
    lsl     x10, x9, #1
    lsr     x10, x10, #1
    lsr     x11, x10, #52
    cmp     x11, #0x7ff
    b.eq    .Lmodf_int_done
    sub     x11, x11, #1023
    cmp     x11, #0
    b.lt    .Lmodf_int_zero
    cmp     x11, #52
    b.ge    .Lmodf_int_done

    mov     x12, #52
    sub     x12, x12, x11
    mov     x13, #1
    lsl     x13, x13, x12
    sub     x13, x13, #1
    bic     x9, x9, x13
    b       .Lmodf_int_done
.Lmodf_int_zero:
    lsr     x9, x9, #63
    lsl     x9, x9, #63
.Lmodf_int_done:
    fmov    d0, x9
    ret
.size _math_modf_int, .-_math_modf_int

/* double _math_modf_frac(double value) -- signed fractional component. */
.global _math_modf_frac
.type _math_modf_frac, %function
_math_modf_frac:
    stp     x29, x30, [sp, #-32]!
    mov     x29, sp
    str     x19, [sp, #16]

    fmov    x19, d0
    lsl     x9, x19, #1
    lsr     x9, x9, #1
    movz    x10, #0x7ff0, lsl #48
    cmp     x9, x10
    b.hi    .Lmodf_frac_nan
    b.eq    .Lmodf_frac_zero

    fmov    d0, x19
    bl      _math_modf_int
    fmov    d1, x19
    fsub    d0, d1, d0
    fmov    x9, d0
    lsl     x10, x9, #1
    lsr     x10, x10, #1
    cbnz    x10, .Lmodf_frac_done
.Lmodf_frac_zero:
    lsr     x9, x19, #63
    lsl     x9, x9, #63
    fmov    d0, x9
    b       .Lmodf_frac_done
.Lmodf_frac_nan:
    fmov    d0, x19
.Lmodf_frac_done:
    ldr     x19, [sp, #16]
    ldp     x29, x30, [sp], #32
    ret
.size _math_modf_frac, .-_math_modf_frac

.section .note.GNU-stack,"",%progbits
