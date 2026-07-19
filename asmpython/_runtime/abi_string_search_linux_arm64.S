/*
 * Non-allocating string-search additions for the freestanding AArch64 runtime.
 *
 * Exports:
 *   _abi_str_starts_with(text, prefix)
 *   _abi_str_ends_with(text, suffix)
 *   _abi_str_count(text, needle)
 *
 * Strings are UTF-8 byte sequences. Prefix/suffix and non-empty substring
 * counting are byte-safe for valid UTF-8 because complete encoded code points
 * cannot spuriously match from a continuation byte. The empty-needle case
 * counts UTF-8 code points, not bytes, to preserve Python's len(text)+1 rule.
 */

.text
.balign 4

.global _abi_str_starts_with
.type _abi_str_starts_with, %function
_abi_str_starts_with:
    cbnz    x1, .Lstarts_prefix_ready
    adrp    x1, .Lsearch_empty
    add     x1, x1, :lo12:.Lsearch_empty
.Lstarts_prefix_ready:
    cbnz    x0, .Lstarts_loop
    ldrb    w9, [x1]
    cmp     w9, #0
    cset    x0, eq
    ret
.Lstarts_loop:
    ldrb    w9, [x1], #1
    cbz     w9, .Lstarts_true
    ldrb    w10, [x0], #1
    cmp     w10, w9
    b.eq    .Lstarts_loop
    mov     x0, xzr
    ret
.Lstarts_true:
    mov     x0, #1
    ret
.size _abi_str_starts_with, .-_abi_str_starts_with

.global _abi_str_ends_with
.type _abi_str_ends_with, %function
_abi_str_ends_with:
    stp     x29, x30, [sp, #-64]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]

    mov     x19, x0
    mov     x20, x1
    cbnz    x19, .Lends_text_ready
    adrp    x19, .Lsearch_empty
    add     x19, x19, :lo12:.Lsearch_empty
.Lends_text_ready:
    cbnz    x20, .Lends_suffix_ready
    adrp    x20, .Lsearch_empty
    add     x20, x20, :lo12:.Lsearch_empty
.Lends_suffix_ready:

    mov     x0, x19
    bl      strlen
    mov     x21, x0
    mov     x0, x20
    bl      strlen
    mov     x22, x0
    cmp     x22, x21
    b.hi    .Lends_false

    sub     x19, x21, x22
    add     x19, x0, xzr           /* x0 currently suffix length; overwritten below */
    /* Reconstruct text start from its preserved pointer and byte lengths. */
    ldr     x9, [sp, #16]          /* saved original x19/text */
    cbnz    x9, .Lends_have_original
    adrp    x9, .Lsearch_empty
    add     x9, x9, :lo12:.Lsearch_empty
.Lends_have_original:
    sub     x10, x21, x22
    add     x9, x9, x10
    mov     x10, x20
.Lends_compare:
    ldrb    w11, [x10], #1
    cbz     w11, .Lends_true
    ldrb    w12, [x9], #1
    cmp     w12, w11
    b.eq    .Lends_compare
.Lends_false:
    mov     x0, xzr
    b       .Lends_done
.Lends_true:
    mov     x0, #1
.Lends_done:
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #64
    ret
.size _abi_str_ends_with, .-_abi_str_ends_with

.global _abi_str_count
.type _abi_str_count, %function
_abi_str_count:
    stp     x29, x30, [sp, #-80]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]
    stp     x23, x24, [sp, #48]

    mov     x19, x0
    mov     x20, x1
    cbnz    x19, .Lcount_text_ready
    adrp    x19, .Lsearch_empty
    add     x19, x19, :lo12:.Lsearch_empty
.Lcount_text_ready:
    cbnz    x20, .Lcount_needle_ready
    adrp    x20, .Lsearch_empty
    add     x20, x20, :lo12:.Lsearch_empty
.Lcount_needle_ready:

    mov     x0, x20
    bl      strlen
    mov     x21, x0               /* needle byte length */
    mov     x22, xzr              /* count */
    mov     x23, x19              /* current haystack position */
    cbz     x21, .Lcount_empty_needle

.Lcount_outer:
    ldrb    w9, [x23]
    cbz     w9, .Lcount_done
    mov     x24, xzr              /* byte index within needle */
.Lcount_match:
    cmp     x24, x21
    b.eq    .Lcount_matched
    ldrb    w10, [x23, x24]
    cbz     w10, .Lcount_done
    ldrb    w11, [x20, x24]
    cmp     w10, w11
    b.ne    .Lcount_no_match
    add     x24, x24, #1
    b       .Lcount_match
.Lcount_matched:
    add     x22, x22, #1
    add     x23, x23, x21
    b       .Lcount_outer
.Lcount_no_match:
    add     x23, x23, #1
    b       .Lcount_outer

/* Python's empty-substring count is Unicode length + 1, not byte length + 1. */
.Lcount_empty_needle:
    mov     x22, #1
.Lcount_codepoints:
    ldrb    w9, [x23], #1
    cbz     w9, .Lcount_done
    and     w10, w9, #0xc0
    cmp     w10, #0x80
    b.eq    .Lcount_codepoints
    add     x22, x22, #1
    b       .Lcount_codepoints

.Lcount_done:
    mov     x0, x22
    ldp     x23, x24, [sp, #48]
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #80
    ret
.size _abi_str_count, .-_abi_str_count

.section .rodata
.Lsearch_empty:
    .asciz  ""

.section .note.GNU-stack,"",%progbits
