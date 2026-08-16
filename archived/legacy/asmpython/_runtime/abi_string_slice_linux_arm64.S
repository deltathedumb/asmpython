/* Exact plain str[start:stop] for the freestanding AArch64 runtime. */
.text
.balign 4

.type .Lslice_alloc, %function
.Lslice_alloc:
    add x0, x0, #7
    and x0, x0, #-8
    adrp x9, .Lslice_heap_pos
    add x9, x9, :lo12:.Lslice_heap_pos
    ldr x10, [x9]
    add x11, x10, x0
    mov x12, #0x10
    lsl x12, x12, #16
    cmp x11, x12
    b.hi .Lslice_alloc_fail
    str x11, [x9]
    adrp x0, .Lslice_heap
    add x0, x0, :lo12:.Lslice_heap
    add x0, x0, x10
    ret
.Lslice_alloc_fail:
    mov x0, xzr
    ret
.size .Lslice_alloc, .-.Lslice_alloc

.type .Lslice_utf8_length, %function
.Lslice_utf8_length:
    mov x9, x0
    mov x10, xzr
.Lslice_len_loop:
    ldrb w11, [x9], #1
    cbz w11, .Lslice_len_done
    and w12, w11, #0xc0
    cmp w12, #0x80
    b.eq .Lslice_len_loop
    add x10, x10, #1
    b .Lslice_len_loop
.Lslice_len_done:
    mov x0, x10
    ret
.size .Lslice_utf8_length, .-.Lslice_utf8_length

.type .Lslice_utf8_ptr_at, %function
.Lslice_utf8_ptr_at:
    mov x9, x0
    mov x10, xzr
.Lslice_ptr_outer:
    cmp x10, x1
    b.hs .Lslice_ptr_done
    ldrb w11, [x9], #1
    cbz w11, .Lslice_ptr_done
.Lslice_ptr_skip:
    ldrb w11, [x9]
    and w12, w11, #0xc0
    cmp w12, #0x80
    b.ne .Lslice_ptr_advanced
    add x9, x9, #1
    b .Lslice_ptr_skip
.Lslice_ptr_advanced:
    add x10, x10, #1
    b .Lslice_ptr_outer
.Lslice_ptr_done:
    mov x0, x9
    ret
.size .Lslice_utf8_ptr_at, .-.Lslice_utf8_ptr_at

.global _abi_str_slice
.type _abi_str_slice, %function
_abi_str_slice:
    stp x29, x30, [sp, #-112]!
    mov x29, sp
    stp x19, x20, [sp, #16]
    stp x21, x22, [sp, #32]
    stp x23, x24, [sp, #48]
    stp x25, x26, [sp, #64]
    stp x27, x28, [sp, #80]

    mov x19, x0
    mov x20, x1
    mov x21, x2
    cbnz x19, .Lslice_text_ready
    adrp x19, .Lslice_empty
    add x19, x19, :lo12:.Lslice_empty
.Lslice_text_ready:
    mov x0, x19
    bl .Lslice_utf8_length
    mov x22, x0

    mov x23, x20
    cmp x23, #0
    b.ge .Lslice_start_nonnegative
    add x23, x23, x22
.Lslice_start_nonnegative:
    cmp x23, #0
    b.ge .Lslice_start_low_ok
    mov x23, xzr
.Lslice_start_low_ok:
    cmp x23, x22
    b.le .Lslice_start_ready
    mov x23, x22
.Lslice_start_ready:

    mov x24, x21
    cmp x24, #0
    b.ge .Lslice_stop_nonnegative
    add x24, x24, x22
.Lslice_stop_nonnegative:
    cmp x24, #0
    b.ge .Lslice_stop_low_ok
    mov x24, xzr
.Lslice_stop_low_ok:
    cmp x24, x22
    b.le .Lslice_stop_ready
    mov x24, x22
.Lslice_stop_ready:

    cmp x24, x23
    b.le .Lslice_empty_result
    mov x0, x19
    mov x1, x23
    bl .Lslice_utf8_ptr_at
    mov x25, x0
    mov x0, x19
    mov x1, x24
    bl .Lslice_utf8_ptr_at
    mov x26, x0
    sub x28, x26, x25
    adds x0, x28, #1
    b.cs .Lslice_fail
    bl .Lslice_alloc
    mov x27, x0
    cbz x27, .Lslice_done
    mov x9, x27
    mov x10, x25
    mov x11, x28
.Lslice_copy:
    cbz x11, .Lslice_terminate
    ldrb w12, [x10], #1
    strb w12, [x9], #1
    sub x11, x11, #1
    b .Lslice_copy
.Lslice_terminate:
    strb wzr, [x9]
    b .Lslice_done
.Lslice_empty_result:
    adrp x27, .Lslice_empty
    add x27, x27, :lo12:.Lslice_empty
    b .Lslice_done
.Lslice_fail:
    mov x27, xzr
.Lslice_done:
    mov x0, x27
    ldp x27, x28, [sp, #80]
    ldp x25, x26, [sp, #64]
    ldp x23, x24, [sp, #48]
    ldp x21, x22, [sp, #32]
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #112
    ret
.size _abi_str_slice, .-_abi_str_slice

.section .rodata
.Lslice_empty:
    .asciz ""
.bss
.balign 16
.Lslice_heap_pos:
    .quad 0
.Lslice_heap:
    .skip 1048576
.section .note.GNU-stack,"",%progbits
