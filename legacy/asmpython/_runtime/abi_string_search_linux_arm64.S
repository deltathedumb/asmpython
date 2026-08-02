/* Exact UTF-8-safe string-search helpers for the freestanding AArch64 runtime. */
.text
.balign 4

/* x0=text -> x0=Unicode code-point length for valid UTF-8. */
.type .Lutf8_length, %function
.Lutf8_length:
    cbnz x0, .Lulen_ready
    adrp x0, .Lsearch_empty
    add x0, x0, :lo12:.Lsearch_empty
.Lulen_ready:
    mov x9, x0
    mov x10, xzr
.Lulen_loop:
    ldrb w11, [x9], #1
    cbz w11, .Lulen_done
    and w12, w11, #0xc0
    cmp w12, #0x80
    b.eq .Lulen_loop
    add x10, x10, #1
    b .Lulen_loop
.Lulen_done:
    mov x0, x10
    ret
.size .Lutf8_length, .-.Lutf8_length

/* x0=text, x1=code-point index -> x0=byte pointer at that index. */
.type .Lutf8_ptr_at, %function
.Lutf8_ptr_at:
    cbnz x0, .Luptr_ready
    adrp x0, .Lsearch_empty
    add x0, x0, :lo12:.Lsearch_empty
.Luptr_ready:
    mov x9, x0
    mov x10, xzr
.Luptr_outer:
    cmp x10, x1
    b.hs .Luptr_done
    ldrb w11, [x9], #1
    cbz w11, .Luptr_done
.Luptr_skip_cont:
    ldrb w11, [x9]
    and w12, w11, #0xc0
    cmp w12, #0x80
    b.ne .Luptr_advanced
    add x9, x9, #1
    b .Luptr_skip_cont
.Luptr_advanced:
    add x10, x10, #1
    b .Luptr_outer
.Luptr_done:
    mov x0, x9
    ret
.size .Lutf8_ptr_at, .-.Lutf8_ptr_at

/* x0=haystack byte pointer, x1=needle -> x0=1 iff needle matches here. */
.type .Lmatch_at, %function
.Lmatch_at:
    mov x9, x0
    mov x10, x1
.Lmatch_loop:
    ldrb w11, [x10], #1
    cbz w11, .Lmatch_true
    ldrb w12, [x9], #1
    cbz w12, .Lmatch_false
    cmp w12, w11
    b.eq .Lmatch_loop
.Lmatch_false:
    mov x0, xzr
    ret
.Lmatch_true:
    mov x0, #1
    ret
.size .Lmatch_at, .-.Lmatch_at

.global _abi_str_starts_with
.type _abi_str_starts_with, %function
_abi_str_starts_with:
    cbnz x1, .Lstarts_prefix_ready
    adrp x1, .Lsearch_empty
    add x1, x1, :lo12:.Lsearch_empty
.Lstarts_prefix_ready:
    cbnz x0, .Lstarts_loop
    ldrb w9, [x1]
    cmp w9, #0
    cset x0, eq
    ret
.Lstarts_loop:
    ldrb w9, [x1], #1
    cbz w9, .Lstarts_true
    ldrb w10, [x0], #1
    cmp w10, w9
    b.eq .Lstarts_loop
    mov x0, xzr
    ret
.Lstarts_true:
    mov x0, #1
    ret
.size _abi_str_starts_with, .-_abi_str_starts_with

.global _abi_str_ends_with
.type _abi_str_ends_with, %function
_abi_str_ends_with:
    stp x29, x30, [sp, #-64]!
    mov x29, sp
    stp x19, x20, [sp, #16]
    stp x21, x22, [sp, #32]
    mov x19, x0
    mov x20, x1
    cbnz x19, .Lends_text_ready
    adrp x19, .Lsearch_empty
    add x19, x19, :lo12:.Lsearch_empty
.Lends_text_ready:
    cbnz x20, .Lends_suffix_ready
    adrp x20, .Lsearch_empty
    add x20, x20, :lo12:.Lsearch_empty
.Lends_suffix_ready:
    mov x0, x19
    bl strlen
    mov x21, x0
    mov x0, x20
    bl strlen
    mov x22, x0
    cmp x22, x21
    b.hi .Lends_false
    sub x10, x21, x22
    add x9, x19, x10
    mov x10, x20
.Lends_compare:
    ldrb w11, [x10], #1
    cbz w11, .Lends_true
    ldrb w12, [x9], #1
    cmp w12, w11
    b.eq .Lends_compare
.Lends_false:
    mov x0, xzr
    b .Lends_done
.Lends_true:
    mov x0, #1
.Lends_done:
    ldp x21, x22, [sp, #32]
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #64
    ret
.size _abi_str_ends_with, .-_abi_str_ends_with

.global _abi_str_count
.type _abi_str_count, %function
_abi_str_count:
    stp x29, x30, [sp, #-80]!
    mov x29, sp
    stp x19, x20, [sp, #16]
    stp x21, x22, [sp, #32]
    stp x23, x24, [sp, #48]
    mov x19, x0
    mov x20, x1
    cbnz x19, .Lcount_text_ready
    adrp x19, .Lsearch_empty
    add x19, x19, :lo12:.Lsearch_empty
.Lcount_text_ready:
    cbnz x20, .Lcount_needle_ready
    adrp x20, .Lsearch_empty
    add x20, x20, :lo12:.Lsearch_empty
.Lcount_needle_ready:
    mov x0, x20
    bl strlen
    mov x21, x0
    mov x22, xzr
    mov x23, x19
    cbz x21, .Lcount_empty_needle
.Lcount_outer:
    ldrb w9, [x23]
    cbz w9, .Lcount_done
    mov x24, xzr
.Lcount_match:
    cmp x24, x21
    b.eq .Lcount_matched
    ldrb w10, [x23, x24]
    cbz w10, .Lcount_done
    ldrb w11, [x20, x24]
    cmp w10, w11
    b.ne .Lcount_no_match
    add x24, x24, #1
    b .Lcount_match
.Lcount_matched:
    add x22, x22, #1
    add x23, x23, x21
    b .Lcount_outer
.Lcount_no_match:
    add x23, x23, #1
    b .Lcount_outer
.Lcount_empty_needle:
    mov x22, #1
.Lcount_codepoints:
    ldrb w9, [x23], #1
    cbz w9, .Lcount_done
    and w10, w9, #0xc0
    cmp w10, #0x80
    b.eq .Lcount_codepoints
    add x22, x22, #1
    b .Lcount_codepoints
.Lcount_done:
    mov x0, x22
    ldp x23, x24, [sp, #48]
    ldp x21, x22, [sp, #32]
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #80
    ret
.size _abi_str_count, .-_abi_str_count

/* First matching Unicode code-point index, or -1. */
.global _abi_str_index_of
.type _abi_str_index_of, %function
_abi_str_index_of:
    stp x29, x30, [sp, #-64]!
    mov x29, sp
    stp x19, x20, [sp, #16]
    stp x21, x22, [sp, #32]
    mov x19, x0
    mov x20, x1
    cbnz x19, .Lfind_text_ready
    adrp x19, .Lsearch_empty
    add x19, x19, :lo12:.Lsearch_empty
.Lfind_text_ready:
    cbnz x20, .Lfind_needle_ready
    adrp x20, .Lsearch_empty
    add x20, x20, :lo12:.Lsearch_empty
.Lfind_needle_ready:
    mov x21, x19
    mov x22, xzr
    ldrb w9, [x20]
    cbz w9, .Lfind_found
.Lfind_outer:
    ldrb w9, [x21]
    cbz w9, .Lfind_miss
    mov x0, x21
    mov x1, x20
    bl .Lmatch_at
    cbnz x0, .Lfind_found
    add x21, x21, #1
.Lfind_skip_cont:
    ldrb w9, [x21]
    and w10, w9, #0xc0
    cmp w10, #0x80
    b.ne .Lfind_advanced
    add x21, x21, #1
    b .Lfind_skip_cont
.Lfind_advanced:
    add x22, x22, #1
    b .Lfind_outer
.Lfind_miss:
    mov x0, #-1
    b .Lfind_done
.Lfind_found:
    mov x0, x22
.Lfind_done:
    ldp x21, x22, [sp, #32]
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #64
    ret
.size _abi_str_index_of, .-_abi_str_index_of

/* Last matching Unicode code-point index, or -1. */
.global _abi_str_rindex_of
.type _abi_str_rindex_of, %function
_abi_str_rindex_of:
    stp x29, x30, [sp, #-64]!
    mov x29, sp
    stp x19, x20, [sp, #16]
    stp x21, x22, [sp, #32]
    stp x23, x24, [sp, #48]
    mov x19, x0
    mov x20, x1
    cbnz x19, .Lrfind_text_ready
    adrp x19, .Lsearch_empty
    add x19, x19, :lo12:.Lsearch_empty
.Lrfind_text_ready:
    cbnz x20, .Lrfind_needle_ready
    adrp x20, .Lsearch_empty
    add x20, x20, :lo12:.Lsearch_empty
.Lrfind_needle_ready:
    ldrb w9, [x20]
    cbz w9, .Lrfind_empty
    mov x21, x19
    mov x22, xzr
    mov x23, #-1
.Lrfind_outer:
    ldrb w9, [x21]
    cbz w9, .Lrfind_result
    mov x0, x21
    mov x1, x20
    bl .Lmatch_at
    cbz x0, .Lrfind_advance
    mov x23, x22
.Lrfind_advance:
    add x21, x21, #1
.Lrfind_skip_cont:
    ldrb w9, [x21]
    and w10, w9, #0xc0
    cmp w10, #0x80
    b.ne .Lrfind_advanced
    add x21, x21, #1
    b .Lrfind_skip_cont
.Lrfind_advanced:
    add x22, x22, #1
    b .Lrfind_outer
.Lrfind_empty:
    mov x0, x19
    bl .Lutf8_length
    b .Lrfind_done
.Lrfind_result:
    mov x0, x23
.Lrfind_done:
    ldp x23, x24, [sp, #48]
    ldp x21, x22, [sp, #32]
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #64
    ret
.size _abi_str_rindex_of, .-_abi_str_rindex_of

/* First match at or after a Python-normalized Unicode start index. */
.global _abi_str_index_of_start
.type _abi_str_index_of_start, %function
_abi_str_index_of_start:
    stp x29, x30, [sp, #-80]!
    mov x29, sp
    stp x19, x20, [sp, #16]
    stp x21, x22, [sp, #32]
    stp x23, x24, [sp, #48]
    mov x19, x0
    mov x20, x1
    mov x21, x2
    cbnz x19, .Lfinds_text_ready
    adrp x19, .Lsearch_empty
    add x19, x19, :lo12:.Lsearch_empty
.Lfinds_text_ready:
    cbnz x20, .Lfinds_needle_ready
    adrp x20, .Lsearch_empty
    add x20, x20, :lo12:.Lsearch_empty
.Lfinds_needle_ready:
    mov x0, x19
    bl .Lutf8_length
    mov x22, x0
    mov x23, x21
    cmp x23, #0
    b.ge .Lfinds_nonnegative
    add x23, x23, x22
    cmp x23, #0
    b.ge .Lfinds_nonnegative
    mov x23, xzr
.Lfinds_nonnegative:
    cmp x23, x22
    b.gt .Lfinds_miss
    mov x0, x19
    mov x1, x23
    bl .Lutf8_ptr_at
    mov x1, x20
    bl _abi_str_index_of
    cmp x0, #0
    b.lt .Lfinds_done
    add x0, x0, x23
    b .Lfinds_done
.Lfinds_miss:
    mov x0, #-1
.Lfinds_done:
    ldp x23, x24, [sp, #48]
    ldp x21, x22, [sp, #32]
    ldp x19, x20, [sp, #16]
    ldp x29, x30, [sp], #80
    ret
.size _abi_str_index_of_start, .-_abi_str_index_of_start

.section .rodata
.Lsearch_empty:
    .asciz ""
.section .note.GNU-stack,"",%progbits
