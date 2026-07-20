/* Freestanding AArch64 valid-index list deletion runtime. */
.text
.balign 4

/*
 * x0=list header, x1=valid signed index -> return value ignored.
 *
 * Negative indices are normalized relative to the active length. The caller
 * must provide an in-range index until the ARM64 exception runtime can raise a
 * catchable IndexError; this slice deliberately does not fake that path.
 */
.global _abi_list_del
.type _abi_list_del, %function
_abi_list_del:
    ldr     x9, [x0, #8]            /* old active length */
    cmp     x1, #0
    b.ge    .Llist_del_index_ready
    add     x1, x1, x9              /* valid negative index -> non-negative */
.Llist_del_index_ready:
    ldr     x10, [x0, #16]          /* element buffer */
    add     x11, x1, #1             /* source index for left shift */
.Llist_del_shift:
    cmp     x11, x9
    b.hs    .Llist_del_shifted
    ldr     x12, [x10, x11, lsl #3]
    sub     x13, x11, #1
    str     x12, [x10, x13, lsl #3]
    add     x11, x11, #1
    b       .Llist_del_shift
.Llist_del_shifted:
    sub     x9, x9, #1
    str     x9, [x0, #8]
    ret
.size _abi_list_del, .-_abi_list_del

.section .note.GNU-stack,"",%progbits
