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

.section .note.GNU-stack,"",%progbits
