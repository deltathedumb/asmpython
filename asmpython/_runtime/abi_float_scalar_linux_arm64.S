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

.section .note.GNU-stack,"",%progbits
