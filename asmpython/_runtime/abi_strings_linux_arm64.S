/*
 * Exact scalar/string additions for the freestanding AArch64 runtime.
 *
 * This file is assembled separately from abi_shims_linux_arm64.S and combined
 * with it through `ld -r`.  Keeping the slice separate makes each runtime
 * expansion independently inspectable and assembler-verifiable.
 *
 * Exports:
 *   labs(value)
 *   _abi_str_concat(left, right)
 *   _abi_str_concat_dup(text)
 *   _abi_str_repeat(text, count)
 *   _abi_str_eq(left, right)
 *   _abi_str_cmp(left, right)
 *   _abi_hash_string(text)
 */

.text
.balign 4

/* x0=size -> x0=unique zero-initialized block, or NULL on exhaustion. */
.type .Lstring_alloc, %function
.Lstring_alloc:
    add     x0, x0, #7
    and     x0, x0, #-8

    adrp    x9, .Lstring_heap_pos
    add     x9, x9, :lo12:.Lstring_heap_pos
    ldr     x10, [x9]
    add     x11, x10, x0
    mov     x12, #0x10
    lsl     x12, x12, #16          /* 1 MiB */
    cmp     x11, x12
    b.hi    .Lstring_alloc_fail
    str     x11, [x9]

    adrp    x0, .Lstring_heap
    add     x0, x0, :lo12:.Lstring_heap
    add     x0, x0, x10
    ret
.Lstring_alloc_fail:
    mov     x0, xzr
    ret
.size .Lstring_alloc, .-.Lstring_alloc

/* int64 labs(int64 value) -- matches the host-runtime symbol IR lowering uses. */
.global labs
.type labs, %function
labs:
    tbz     x0, #63, .Llabs_done
    neg     x0, x0
.Llabs_done:
    ret
.size labs, .-labs

/* char *_abi_str_concat(const char *left, const char *right) */
.global _abi_str_concat
.type _abi_str_concat, %function
_abi_str_concat:
    stp     x29, x30, [sp, #-80]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]
    stp     x23, x24, [sp, #48]

    mov     x19, x0
    mov     x20, x1
    cbnz    x19, .Lconcat_left_ready
    adrp    x19, .Lempty_text
    add     x19, x19, :lo12:.Lempty_text
.Lconcat_left_ready:
    cbnz    x20, .Lconcat_right_ready
    adrp    x20, .Lempty_text
    add     x20, x20, :lo12:.Lempty_text
.Lconcat_right_ready:

    mov     x0, x19
    bl      strlen
    mov     x21, x0
    mov     x0, x20
    bl      strlen
    mov     x22, x0

    add     x0, x21, x22
    add     x0, x0, #1
    bl      .Lstring_alloc
    mov     x23, x0
    cbz     x23, .Lconcat_done

    mov     x24, x23
    mov     x9, x19
.Lconcat_copy_left:
    ldrb    w10, [x9], #1
    cbz     w10, .Lconcat_copy_right_start
    strb    w10, [x24], #1
    b       .Lconcat_copy_left

.Lconcat_copy_right_start:
    mov     x9, x20
.Lconcat_copy_right:
    ldrb    w10, [x9], #1
    strb    w10, [x24], #1
    cbnz    w10, .Lconcat_copy_right

.Lconcat_done:
    mov     x0, x23
    ldp     x23, x24, [sp, #48]
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #80
    ret
.size _abi_str_concat, .-_abi_str_concat

/* Freshly duplicate one string using the same exact allocation/copy path. */
.global _abi_str_concat_dup
.type _abi_str_concat_dup, %function
_abi_str_concat_dup:
    adrp    x1, .Lempty_text
    add     x1, x1, :lo12:.Lempty_text
    b       _abi_str_concat
.size _abi_str_concat_dup, .-_abi_str_concat_dup

/* char *_abi_str_repeat(const char *text, int64 count) */
.global _abi_str_repeat
.type _abi_str_repeat, %function
_abi_str_repeat:
    stp     x29, x30, [sp, #-80]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]
    stp     x23, x24, [sp, #48]

    mov     x19, x0
    mov     x20, x1
    cbnz    x19, .Lrepeat_text_ready
    adrp    x19, .Lempty_text
    add     x19, x19, :lo12:.Lempty_text
.Lrepeat_text_ready:
    cmp     x20, #0
    b.le    .Lrepeat_empty

    mov     x0, x19
    bl      strlen
    mov     x21, x0
    cbz     x21, .Lrepeat_empty

    /* Detect 64-bit multiplication/addition overflow before allocation. */
    umulh   x9, x21, x20
    cbnz    x9, .Lrepeat_fail
    mul     x21, x21, x20
    adds    x0, x21, #1
    b.cs    .Lrepeat_fail
    bl      .Lstring_alloc
    mov     x22, x0
    cbz     x22, .Lrepeat_done

    mov     x23, x22
    mov     x24, x20
.Lrepeat_outer:
    mov     x9, x19
.Lrepeat_copy:
    ldrb    w10, [x9], #1
    cbz     w10, .Lrepeat_next
    strb    w10, [x23], #1
    b       .Lrepeat_copy
.Lrepeat_next:
    subs    x24, x24, #1
    b.ne    .Lrepeat_outer
    strb    wzr, [x23]
    b       .Lrepeat_done

.Lrepeat_empty:
    adrp    x22, .Lempty_text
    add     x22, x22, :lo12:.Lempty_text
    b       .Lrepeat_done
.Lrepeat_fail:
    mov     x22, xzr
.Lrepeat_done:
    mov     x0, x22
    ldp     x23, x24, [sp, #48]
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #80
    ret
.size _abi_str_repeat, .-_abi_str_repeat

/* int64 _abi_str_eq(const char *left, const char *right) -> 0 or 1 */
.global _abi_str_eq
.type _abi_str_eq, %function
_abi_str_eq:
    cmp     x0, x1
    b.eq    .Lstr_eq_true
    cbz     x0, .Lstr_eq_false
    cbz     x1, .Lstr_eq_false
.Lstr_eq_loop:
    ldrb    w9, [x0], #1
    ldrb    w10, [x1], #1
    cmp     w9, w10
    b.ne    .Lstr_eq_false
    cbnz    w9, .Lstr_eq_loop
.Lstr_eq_true:
    mov     x0, #1
    ret
.Lstr_eq_false:
    mov     x0, xzr
    ret
.size _abi_str_eq, .-_abi_str_eq

/* int64 _abi_str_cmp(const char *left, const char *right) -> -1, 0, or 1 */
.global _abi_str_cmp
.type _abi_str_cmp, %function
_abi_str_cmp:
    cmp     x0, x1
    b.eq    .Lstr_cmp_equal
    cbz     x0, .Lstr_cmp_less
    cbz     x1, .Lstr_cmp_greater
.Lstr_cmp_loop:
    ldrb    w9, [x0], #1
    ldrb    w10, [x1], #1
    cmp     w9, w10
    b.hi    .Lstr_cmp_greater
    b.lo    .Lstr_cmp_less
    cbnz    w9, .Lstr_cmp_loop
.Lstr_cmp_equal:
    mov     x0, xzr
    ret
.Lstr_cmp_less:
    mov     x0, #-1
    ret
.Lstr_cmp_greater:
    mov     x0, #1
    ret
.size _abi_str_cmp, .-_abi_str_cmp

/* uint64 _abi_hash_string(const char *text) -- FNV-1a over UTF-8 bytes. */
.global _abi_hash_string
.type _abi_hash_string, %function
_abi_hash_string:
    mov     x1, x0

    /* FNV-1a offset basis: 0xcbf29ce484222325. */
    movz    x9, #0x2325
    movk    x9, #0x8422, lsl #16
    movk    x9, #0x9ce4, lsl #32
    movk    x9, #0xcbf2, lsl #48

    /* FNV prime: 0x00000100000001b3. */
    movz    x10, #0x01b3
    movk    x10, #0x0100, lsl #32

.Lhash_loop:
    ldrb    w11, [x1], #1
    cbz     w11, .Lhash_done
    eor     x9, x9, x11
    mul     x9, x9, x10
    b       .Lhash_loop
.Lhash_done:
    mov     x0, x9
    ret
.size _abi_hash_string, .-_abi_hash_string

.section .rodata
.Lempty_text:
    .asciz  ""

.bss
.balign 16
.Lstring_heap_pos:
    .quad   0
.Lstring_heap:
    .skip   1048576

.section .note.GNU-stack,"",%progbits
