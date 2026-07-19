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

/* C rounding functions returning binary64 integral values. */
.global ceil
.type ceil, %function
ceil:
    frintp d0, d0
    ret
.size ceil, .-ceil

.global floor
.type floor, %function
floor:
    frintm d0, d0
    ret
.size floor, .-floor

.global trunc
.type trunc, %function
trunc:
    frintz d0, d0
    ret
.size trunc, .-trunc

.global round
.type round, %function
round:
    frinta d0, d0
    ret
.size round, .-round

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

/* double nextafter(double x, double y) -- one representable step toward y. */
.global nextafter
.type nextafter, %function
nextafter:
    fmov x9, d0
    fmov x10, d1
    lsl x11, x9, #1
    lsr x11, x11, #1
    movz x12, #0x7ff0, lsl #48
    cmp x11, x12
    b.hi .Lnext_done

    lsl x13, x10, #1
    lsr x13, x13, #1
    cmp x13, x12
    b.hi .Lnext_return_y

    fcmp d0, d1
    b.eq .Lnext_return_y
    cbz x11, .Lnext_from_zero
    tbz x9, #63, .Lnext_x_positive
    b.lt .Lnext_decrement
    add x9, x9, #1
    b .Lnext_finish
.Lnext_x_positive:
    b.lt .Lnext_increment
.Lnext_decrement:
    sub x9, x9, #1
    b .Lnext_finish
.Lnext_increment:
    add x9, x9, #1
    b .Lnext_finish
.Lnext_from_zero:
    lsr x9, x10, #63
    lsl x9, x9, #63
    orr x9, x9, #1
.Lnext_finish:
    fmov d0, x9
.Lnext_done:
    ret
.Lnext_return_y:
    fmov d0, x10
    ret
.size nextafter, .-nextafter

.section .note.GNU-stack,"",%progbits
