/* Exact int64 math.gcd/math.lcm helpers for AArch64. */
.text
.balign 4

/* int64 _math_gcd(int64 a, int64 b) -- Euclidean, non-negative result. */
.global _math_gcd
.type _math_gcd, %function
_math_gcd:
    cmp     x0, #0
    cneg    x0, x0, lt
    cmp     x1, #0
    cneg    x1, x1, lt
.Lgcd_loop:
    cbz     x1, .Lgcd_done
    udiv    x2, x0, x1
    msub    x2, x2, x1, x0
    mov     x0, x1
    mov     x1, x2
    b       .Lgcd_loop
.Lgcd_done:
    ret
.size _math_gcd, .-_math_gcd

/* int64 _math_lcm(int64 a, int64 b) -- abs(a / gcd(a,b) * b), zero-aware. */
.global _math_lcm
.type _math_lcm, %function
_math_lcm:
    stp     x29, x30, [sp, #-48]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]

    cbz     x0, .Llcm_zero
    cbz     x1, .Llcm_zero
    cmp     x0, #0
    cneg    x19, x0, lt
    cmp     x1, #0
    cneg    x20, x1, lt

    mov     x0, x19
    mov     x1, x20
    bl      _math_gcd
    udiv    x19, x19, x0
    mul     x0, x19, x20
    b       .Llcm_done
.Llcm_zero:
    mov     x0, xzr
.Llcm_done:
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #48
    ret
.size _math_lcm, .-_math_lcm

.section .note.GNU-stack,"",%progbits
