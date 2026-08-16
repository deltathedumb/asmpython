/* Freestanding AArch64 plain list slicing runtime. */
.text
.balign 4

/*
 * x0=source list, x1=start or INT64_MIN, x2=stop or INT64_MAX.
 * Returns a newly allocated shallow-copy list in x0.
 *
 * Missing and explicit bounds follow Python's positive-step slice rules:
 * omitted start -> 0, omitted stop -> len, negative bounds are relative to
 * len, and both endpoints are clamped to [0, len]. The result length is
 * max(0, stop-start). Element cells are opaque 8-byte values.
 */
.global _abi_list_slice
.type _abi_list_slice, %function
_abi_list_slice:
    stp     x29, x30, [sp, #-64]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]
    stp     x23, x24, [sp, #48]

    mov     x19, x0                  /* source header */
    ldr     x22, [x19, #8]          /* source length */

    /* Normalize start. */
    mov     x20, x1
    mov     x9, #1
    lsl     x9, x9, #63              /* INT64_MIN */
    cmp     x20, x9
    b.ne    .Lslice_start_present
    mov     x20, #0
    b       .Lslice_start_clamp_high
.Lslice_start_present:
    cmp     x20, #0
    b.ge    .Lslice_start_clamp_low
    add     x20, x20, x22
.Lslice_start_clamp_low:
    cmp     x20, #0
    csel    x20, x20, xzr, ge
.Lslice_start_clamp_high:
    cmp     x20, x22
    csel    x20, x20, x22, le

    /* Normalize stop. */
    mov     x21, x2
    mov     x9, #-1
    lsr     x9, x9, #1               /* INT64_MAX */
    cmp     x21, x9
    b.ne    .Lslice_stop_present
    mov     x21, x22
    b       .Lslice_stop_clamp_high
.Lslice_stop_present:
    cmp     x21, #0
    b.ge    .Lslice_stop_clamp_low
    add     x21, x21, x22
.Lslice_stop_clamp_low:
    cmp     x21, #0
    csel    x21, x21, xzr, ge
.Lslice_stop_clamp_high:
    cmp     x21, x22
    csel    x21, x21, x22, le

    /* n = max(0, stop-start), capacity=max(n, 4). */
    subs    x23, x21, x20
    csel    x23, x23, xzr, gt
    mov     x0, #4
    cmp     x23, #4
    csel    x0, x23, x0, ge
    bl      _abi_new_list
    mov     x24, x0

    cbz     x23, .Lslice_done
    ldr     x9, [x19, #16]           /* source element buffer */
    add     x9, x9, x20, lsl #3
    ldr     x10, [x24, #16]          /* destination element buffer */
    mov     x11, #0
.Lslice_copy_loop:
    cmp     x11, x23
    b.hs    .Lslice_copy_done
    ldr     x12, [x9, x11, lsl #3]
    str     x12, [x10, x11, lsl #3]
    add     x11, x11, #1
    b       .Lslice_copy_loop
.Lslice_copy_done:
    str     x23, [x24, #8]

.Lslice_done:
    mov     x0, x24
    ldp     x23, x24, [sp, #48]
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #64
    ret
.size _abi_list_slice, .-_abi_list_slice

.section .note.GNU-stack,"",%progbits
