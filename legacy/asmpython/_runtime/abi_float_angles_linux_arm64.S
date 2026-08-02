/* Exact math.degrees/math.radians helpers using established constants. */
.text
.balign 4

.global _math_degrees
.type _math_degrees, %function
_math_degrees:
    adrp x9, .Ldegrees_factor
    add x9, x9, :lo12:.Ldegrees_factor
    ldr d1, [x9]
    fmul d0, d0, d1
    ret
.size _math_degrees, .-_math_degrees

.global _math_radians
.type _math_radians, %function
_math_radians:
    adrp x9, .Lradians_factor
    add x9, x9, :lo12:.Lradians_factor
    ldr d1, [x9]
    fmul d0, d0, d1
    ret
.size _math_radians, .-_math_radians

.section .rodata
.balign 8
.Ldegrees_factor:
    .quad 0x404ca5dc1a63c1f8
.Lradians_factor:
    .quad 0x3f91df46a2529d39

.section .note.GNU-stack,"",%progbits
