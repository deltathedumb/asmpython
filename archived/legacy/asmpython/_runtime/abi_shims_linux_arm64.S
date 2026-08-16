/*
 * Minimal freestanding AArch64 runtime slice for the Stage-1 backend.
 *
 * Exports only the symbols needed by the first real source-level print probe:
 *
 *   _abi_int_to_base(value, base, prefix) -> distinct NUL-terminated buffer
 *   printf(format, ...)                    -> %s / %% formatter using write(2)
 *   strlen(text)                           -> byte length
 *
 * No libc is required.  Every integer conversion receives a separate 128-byte
 * block from a process-local bump arena, specifically avoiding the historical
 * shared-static-format-buffer aliasing bug described in AGENTS.md.  The arena
 * is intentionally simple and non-reclaiming: the wider native runtime already
 * has no memory-management pass yet, and this file is an independently
 * executable bring-up slice rather than the final allocator.
 */

.text
.balign 4

/* x0=size -> x0=zero-uninitialized unique block, or NULL on exhaustion. */
.type .Larm64_alloc, %function
.Larm64_alloc:
    add     x0, x0, #7
    and     x0, x0, #-8

    adrp    x9, .Larm64_heap_pos
    add     x9, x9, :lo12:.Larm64_heap_pos
    ldr     x10, [x9]
    add     x11, x10, x0
    mov     x12, #0x10
    lsl     x12, x12, #16          /* 1 MiB */
    cmp     x11, x12
    b.hi    .Lalloc_fail
    str     x11, [x9]

    adrp    x0, .Larm64_heap
    add     x0, x0, :lo12:.Larm64_heap
    add     x0, x0, x10
    ret
.Lalloc_fail:
    mov     x0, xzr
    ret
.size .Larm64_alloc, .-.Larm64_alloc

/*
 * char *_abi_int_to_base(int64 value, uint64 base, const char *prefix)
 *
 * Sign precedes prefix, matching Python: hex(-10) == "-0xa".  Magnitude is
 * divided as unsigned, so INT64_MIN works without signed-negation overflow.
 */
.global _abi_int_to_base
.type _abi_int_to_base, %function
_abi_int_to_base:
    stp     x29, x30, [sp, #-96]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]
    stp     x23, x24, [sp, #48]
    stp     x25, x26, [sp, #64]

    mov     x19, x0               /* signed input */
    mov     x20, x1               /* base */
    mov     x21, x2               /* prefix */

    cmp     x20, #2
    b.hs    .Lbase_low_ok
    mov     x20, #10
.Lbase_low_ok:
    cmp     x20, #36
    b.ls    .Lbase_ok
    mov     x20, #10
.Lbase_ok:

    mov     x0, #128
    bl      .Larm64_alloc
    cbz     x0, .Lint_done
    mov     x22, x0               /* result base */

    mov     x23, xzr              /* negative flag */
    cmp     x19, #0
    b.ge    .Lmagnitude_ready
    mov     x23, #1
    neg     x24, x19              /* unsigned magnitude; INT64_MIN is valid */
    b       .Ldigits_start
.Lmagnitude_ready:
    mov     x24, x19

.Ldigits_start:
    add     x25, x22, #127         /* exclusive end of reverse digits */
    mov     x26, x25
    adrp    x9, .Ldigit_table
    add     x9, x9, :lo12:.Ldigit_table
.Ldigit_loop:
    udiv    x10, x24, x20
    msub    x11, x10, x20, x24
    ldrb    w12, [x9, x11]
    sub     x26, x26, #1
    strb    w12, [x26]
    mov     x24, x10
    cbnz    x24, .Ldigit_loop

    mov     x19, x22               /* output cursor */
    cbz     x23, .Lcopy_prefix
    mov     w9, #45                /* '-' */
    strb    w9, [x19], #1

.Lcopy_prefix:
    cbz     x21, .Lcopy_digits
.Lprefix_loop:
    ldrb    w9, [x21], #1
    cbz     w9, .Lcopy_digits
    strb    w9, [x19], #1
    b       .Lprefix_loop

.Lcopy_digits:
    cmp     x26, x25
    b.hs    .Lterminate_int
    ldrb    w9, [x26], #1
    strb    w9, [x19], #1
    b       .Lcopy_digits

.Lterminate_int:
    strb    wzr, [x19]
    mov     x0, x22
.Lint_done:
    ldp     x25, x26, [sp, #64]
    ldp     x23, x24, [sp, #48]
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #96
    ret
.size _abi_int_to_base, .-_abi_int_to_base

.global strlen
.type strlen, %function
strlen:
    mov     x9, x0
.Lstrlen_loop:
    ldrb    w10, [x0], #1
    cbnz    w10, .Lstrlen_loop
    sub     x0, x0, x9
    sub     x0, x0, #1
    ret
.size strlen, .-strlen

/* x0=C string -> x0=write() result. */
.type .Lwrite_cstr, %function
.Lwrite_cstr:
    mov     x9, x0
    mov     x10, x0
.Lwrite_len_loop:
    ldrb    w11, [x10], #1
    cbnz    w11, .Lwrite_len_loop
    sub     x2, x10, x9
    sub     x2, x2, #1
    mov     x0, #1                /* stdout */
    mov     x1, x9
    mov     x8, #64               /* Linux AArch64 write */
    svc     #0
    ret
.size .Lwrite_cstr, .-.Lwrite_cstr

/*
 * Minimal printf used by IR lowering's print() path.
 * Supports literal bytes, %% and %s.  x1-x7 hold the first seven variadic
 * arguments; further pointers are read from the caller's original stack.
 */
.global printf
.type printf, %function
printf:
    stp     x29, x30, [sp, #-176]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]
    stp     x23, x24, [sp, #48]
    stp     x25, x26, [sp, #64]
    stp     x27, x28, [sp, #80]

    add     x27, sp, #176         /* caller SP: stacked variadic args */
    str     x1, [sp, #112]
    str     x2, [sp, #120]
    str     x3, [sp, #128]
    str     x4, [sp, #136]
    str     x5, [sp, #144]
    str     x6, [sp, #152]
    str     x7, [sp, #160]

    mov     x19, x0               /* original format */
    mov     x20, x0               /* scan cursor */
    mov     x21, xzr              /* variadic %s index */
    mov     x22, xzr              /* bytes written */

.Lprintf_loop:
    ldrb    w9, [x20]
    cbz     w9, .Lprintf_done
    cmp     w9, #37               /* '%' */
    b.ne    .Lprintf_literal

    ldrb    w10, [x20, #1]
    cmp     w10, #37
    b.eq    .Lprintf_percent
    cmp     w10, #115             /* 's' */
    b.eq    .Lprintf_string
    /* Unknown conversion: preserve the '%' literally and continue. */

.Lprintf_literal:
    mov     x0, #1
    mov     x1, x20
    mov     x2, #1
    mov     x8, #64
    svc     #0
    cmp     x0, #0
    b.lt    .Lprintf_error
    add     x22, x22, x0
    add     x20, x20, #1
    b       .Lprintf_loop

.Lprintf_percent:
    add     x20, x20, #1          /* write the second '%' byte */
    b       .Lprintf_literal

.Lprintf_string:
    cmp     x21, #7
    b.hs    .Lprintf_stack_arg
    add     x9, sp, #112
    ldr     x23, [x9, x21, lsl #3]
    b       .Lprintf_arg_ready
.Lprintf_stack_arg:
    sub     x9, x21, #7
    ldr     x23, [x27, x9, lsl #3]
.Lprintf_arg_ready:
    cbnz    x23, .Lprintf_write_string
    adrp    x23, .Lnull_text
    add     x23, x23, :lo12:.Lnull_text
.Lprintf_write_string:
    mov     x0, x23
    bl      .Lwrite_cstr
    cmp     x0, #0
    b.lt    .Lprintf_error
    add     x22, x22, x0
    add     x21, x21, #1
    add     x20, x20, #2
    b       .Lprintf_loop

.Lprintf_error:
    mov     x22, x0
.Lprintf_done:
    mov     x0, x22
    ldp     x27, x28, [sp, #80]
    ldp     x25, x26, [sp, #64]
    ldp     x23, x24, [sp, #48]
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #176
    ret
.size printf, .-printf

.section .rodata
.balign 16
.Ldigit_table:
    .ascii  "0123456789abcdefghijklmnopqrstuvwxyz"
.Lnull_text:
    .asciz  "(null)"

.bss
.balign 16
.Larm64_heap_pos:
    .quad   0
.Larm64_heap:
    .skip   1048576

.section .note.GNU-stack,"",%progbits
