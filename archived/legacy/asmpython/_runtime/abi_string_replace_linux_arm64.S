/* Exact str.replace(old, new) for the freestanding AArch64 runtime. */
.text
.balign 4

/* x0=size -> x0=unique block, or NULL on exhaustion. */
.type .Lreplace_alloc, %function
.Lreplace_alloc:
    add x0, x0, #7
    and x0, x0, #-8
    adrp x9, .Lreplace_heap_pos
    add x9, x9, :lo12:.Lreplace_heap_pos
    ldr x10, [x9]
    add x11, x10, x0
    mov x12, #0x10
    lsl x12, x12, #16
    cmp x11, x12
    b.hi .Lreplace_alloc_fail
    str x11, [x9]
    adrp x0, .Lreplace_heap
    add x0, x0, :lo12:.Lreplace_heap
    add x0, x0, x10
    ret
.Lreplace_alloc_fail:
    mov x0, xzr
    ret
.size .Lreplace_alloc, .-.Lreplace_alloc

/* x0=destination, x1=NUL-terminated source -> x0=updated destination. */
.type .Lreplace_copy_cstr, %function
.Lreplace_copy_cstr:
    mov x9, x0
.Lreplace_copy_cstr_loop:
    ldrb w10, [x1], #1
    cbz w10, .Lreplace_copy_cstr_done
    strb w10, [x9], #1
    b .Lreplace_copy_cstr_loop
.Lreplace_copy_cstr_done:
    mov x0, x9
    ret
.size .Lreplace_copy_cstr, .-.Lreplace_copy_cstr

/* Replace all non-overlapping matches; empty old inserts at code-point gaps. */
.global _abi_str_replace
.type _abi_str_replace, %function
_abi_str_replace:
    stp x29, x30, [sp, #-128]!
    mov x29, sp
    stp x19, x20, [sp, #16]
    stp x21, x22, [sp, #32]
    stp x23, x24, [sp, #48]
    stp x25, x26, [sp, #64]
    stp x27, x28, [sp, #80]

    mov x19, x0
    mov x20, x1
    mov x21, x2
    cbnz x19, .Lreplace_text_ready
    adrp x19, .Lreplace_empty
    add x19, x19, :lo12:.Lreplace_empty
.Lreplace_text_ready:
    cbnz x20, .Lreplace_old_ready
    adrp x20, .Lreplace_empty
    add x20, x20, :lo12:.Lreplace_empty
.Lreplace_old_ready:
    cbnz x21, .Lreplace_new_ready
    adrp x21, .Lreplace_empty
    add x21, x21, :lo12:.Lreplace_empty
.Lreplace_new_ready:

    mov x0, x19
    bl strlen
    mov x22, x0
    mov x0, x20
    bl strlen
    mov x23, x0
    mov x0, x21
    bl strlen
    mov x24, x0
    mov x0, x19
    mov x1, x20
    bl _abi_str_count
    mov x25, x0
    cbz x25, .Lreplace_original

    cmp x24, x23
    b.lo .Lreplace_shrinks
    sub x9, x24, x23
    umulh x10, x25, x9
    cbnz x10, .Lreplace_fail
    mul x9, x25, x9
    adds x26, x22, x9
    b.cs .Lreplace_fail
    b .Lreplace_length_ready
.Lreplace_shrinks:
    sub x9, x23, x24
    mul x9, x25, x9
    sub x26, x22, x9
.Lreplace_length_ready:
    adds x0, x26, #1
    b.cs .Lreplace_fail
    bl .Lreplace_alloc
    mov x27, x0
    cbz x27, .Lreplace_done
    mov x28, x19
    str x27, [sp, #96]
    cbz x23, .Lreplace_empty_old

.Lreplace_nonempty_loop:
    ldrb w9, [x28]
    cbz w9, .Lreplace_terminate
    mov x0, x28
    mov x1, x20
    bl _abi_str_starts_with
    cbz x0, .Lreplace_copy_codepoint
    ldr x0, [sp, #96]
    mov x1, x21
    bl .Lreplace_copy_cstr
    str x0, [sp, #96]
    add x28, x28, x23
    b .Lreplace_nonempty_loop

.Lreplace_empty_old:
    ldrb w9, [x28]
    cbz w9, .Lreplace_empty_tail
    ldr x0, [sp, #96]
    mov x1, x21
    bl .Lreplace_copy_cstr
    str x0, [sp, #96]
.Lreplace_copy_codepoint:
    ldr x9, [sp, #96]
    ldrb w10, [x28], #1
    strb w10, [x9], #1
.Lreplace_copy_continuations:
    ldrb w10, [x28]
    and w11, w10, #0xc0
    cmp w11, #0x80
    b.ne .Lreplace_codepoint_done
    add x28, x28, #1
    strb w10, [x9], #1
    b .Lreplace_copy_continuations
.Lreplace_codepoint_done:
    str x9, [sp, #96]
    cbz x23, .Lreplace_empty_old
    b .Lreplace_nonempty_loop

.Lreplace_empty_tail:
    ldr x0, [sp, #96]
    mov x1, x21
    bl .Lreplace_copy_cstr
    str x0, [sp, #96]
.Lreplace_terminate:
    ldr x9, [sp, #96]
    strb wzr, [x9]
    b .Lreplace_done
.Lreplace_original:
    mov x27, x19
    b .Lreplace_done
.Lreplace_fail:
    mov x27, xzr
.Lreplace_done:
    mov x0, x27
    ldp x27, x28, [sp, #80]
    ldp x25, x26, [sp, #64]
    ldp x23, x24, [sp, #48]
    ldp x21, x22, [sp, #32]
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #128
    ret
.size _abi_str_replace, .-_abi_str_replace

.section .rodata
.Lreplace_empty:
    .asciz ""
.bss
.balign 16
.Lreplace_heap_pos:
    .quad 0
.Lreplace_heap:
    .skip 1048576
.section .note.GNU-stack,"",%progbits
