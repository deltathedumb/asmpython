/* Exact Unicode-width str.zfill for the freestanding AArch64 runtime. */
.text
.balign 4

.type .Lpadding_alloc, %function
.Lpadding_alloc:
    add x0, x0, #7
    and x0, x0, #-8
    adrp x9, .Lpadding_heap_pos
    add x9, x9, :lo12:.Lpadding_heap_pos
    ldr x10, [x9]
    add x11, x10, x0
    mov x12, #0x10
    lsl x12, x12, #16
    cmp x11, x12
    b.hi .Lpadding_alloc_fail
    str x11, [x9]
    adrp x0, .Lpadding_heap
    add x0, x0, :lo12:.Lpadding_heap
    add x0, x0, x10
    ret
.Lpadding_alloc_fail:
    mov x0, xzr
    ret
.size .Lpadding_alloc, .-.Lpadding_alloc

.global _abi_str_zfill
.type _abi_str_zfill, %function
_abi_str_zfill:
    stp x29, x30, [sp, #-96]!
    mov x29, sp
    stp x19, x20, [sp, #16]
    stp x21, x22, [sp, #32]
    stp x23, x24, [sp, #48]
    stp x25, x26, [sp, #64]

    mov x19, x0
    mov x20, x1
    cbnz x19, .Lzfill_text_ready
    adrp x19, .Lpadding_empty
    add x19, x19, :lo12:.Lpadding_empty
.Lzfill_text_ready:

    mov x9, x19
    mov x21, xzr
    mov x22, xzr
.Lzfill_measure:
    ldrb w10, [x9], #1
    cbz w10, .Lzfill_measured
    add x21, x21, #1
    and w11, w10, #0xc0
    cmp w11, #0x80
    b.eq .Lzfill_measure
    add x22, x22, #1
    b .Lzfill_measure
.Lzfill_measured:
    cmp x20, x22
    b.le .Lzfill_original
    sub x23, x20, x22
    adds x0, x21, x23
    b.cs .Lzfill_fail
    adds x0, x0, #1
    b.cs .Lzfill_fail
    bl .Lpadding_alloc
    mov x24, x0
    cbz x24, .Lzfill_done
    mov x25, x24
    mov x26, x19

    ldrb w9, [x26]
    cmp w9, #43
    b.eq .Lzfill_copy_sign
    cmp w9, #45
    b.ne .Lzfill_zeros
.Lzfill_copy_sign:
    strb w9, [x25], #1
    add x26, x26, #1
.Lzfill_zeros:
    cbz x23, .Lzfill_copy_text
    mov w9, #48
.Lzfill_zero_loop:
    strb w9, [x25], #1
    subs x23, x23, #1
    b.ne .Lzfill_zero_loop
.Lzfill_copy_text:
    ldrb w9, [x26], #1
    strb w9, [x25], #1
    cbnz w9, .Lzfill_copy_text
    b .Lzfill_done
.Lzfill_original:
    mov x24, x19
    b .Lzfill_done
.Lzfill_fail:
    mov x24, xzr
.Lzfill_done:
    mov x0, x24
    ldp x25, x26, [sp, #64]
    ldp x23, x24, [sp, #48]
    ldp x21, x22, [sp, #32]
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #96
    ret
.size _abi_str_zfill, .-_abi_str_zfill

.section .rodata
.Lpadding_empty:
    .asciz ""
.bss
.balign 16
.Lpadding_heap_pos:
    .quad 0
.Lpadding_heap:
    .skip 1048576
.section .note.GNU-stack,"",%progbits
