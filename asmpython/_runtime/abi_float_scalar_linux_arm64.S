/* Exact non-raising scalar float helpers for AArch64. */
.text
.balign 4

/* double fabs(double value) -- clear the IEEE-754 sign bit exactly. */
.global fabs
.type fabs, %function
fabs:
    fabs d0, d0
    ret
.size fabs, .-fabs

/* double copysign(double magnitude, double sign) -- replace only sign bit. */
.global copysign
.type copysign, %function
copysign:
    fmov x9, d0
    fmov x10, d1
    lsr x10, x10, #63
    bfi x9, x10, #63, #1
    fmov d0, x9
    ret
.size copysign, .-copysign

/* double nearbyint(double value) -- current FPCR mode, no inexact exception. */
.global nearbyint
.type nearbyint, %function
nearbyint:
    frinti d0, d0
    ret
.size nearbyint, .-nearbyint

/* double fdim(double x, double y) -- x-y when x>y, +0 otherwise. */
.global fdim
.type fdim, %function
fdim:
    fmov x9, d0
    lsl x10, x9, #1
    lsr x10, x10, #1
    movz x11, #0x7ff0, lsl #48
    cmp x10, x11
    b.hi .Lfdim_done

    fmov x9, d1
    lsl x10, x9, #1
    lsr x10, x10, #1
    cmp x10, x11
    b.hi .Lfdim_y_nan

    fcmp d0, d1
    b.gt .Lfdim_sub
    fmov d0, xzr
    ret
.Lfdim_sub:
    fsub d0, d0, d1
    ret
.Lfdim_y_nan:
    fmov d0, x9
.Lfdim_done:
    ret
.size fdim, .-fdim

.section .note.GNU-stack,"",%progbits
