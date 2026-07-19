/* Minimal freestanding AArch64 list allocation and core mutation runtime. */
.text
.balign 4

/* x0=size -> x0=zero-initialized unique block, or NULL on exhaustion. */
.type .Llist_alloc, %function
.Llist_alloc:
    add     x0, x0, #7
    and     x0, x0, #-8
    adrp    x9, .Llist_heap_pos
    add     x9, x9, :lo12:.Llist_heap_pos
    ldr     x10, [x9]
    add     x11, x10, x0
    mov     x12, #0x10
    lsl     x12, x12, #16          /* 1 MiB */
    cmp     x11, x12
    b.hi    .Llist_alloc_fail
    str     x11, [x9]
    adrp    x0, .Llist_heap
    add     x0, x0, :lo12:.Llist_heap
    add     x0, x0, x10
    ret
.Llist_alloc_fail:
    mov     x0, xzr
    ret
.size .Llist_alloc, .-.Llist_alloc

/*
 * x0=capacity -> x0=list header {cap@0, len@8, buf@16}.
 * Capacity is clamped to at least one element, matching the x86-64 ABI shim.
 */
.global _abi_new_list
.type _abi_new_list, %function
_abi_new_list:
    stp     x29, x30, [sp, #-64]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]

    mov     x19, x0
    cmp     x19, #1
    b.ge    .Lnew_list_cap_ok
    mov     x19, #1
.Lnew_list_cap_ok:
    lsr     x9, x19, #61           /* cap * 8 must not wrap */
    cbnz    x9, .Lnew_list_fail

    mov     x0, #24
    bl      .Llist_alloc
    cbz     x0, .Lnew_list_fail
    mov     x20, x0
    str     x19, [x20]
    str     xzr, [x20, #8]

    lsl     x0, x19, #3
    bl      .Llist_alloc
    cbz     x0, .Lnew_list_fail
    str     x0, [x20, #16]
    mov     x0, x20
    b       .Lnew_list_done

.Lnew_list_fail:
    mov     x0, xzr
.Lnew_list_done:
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #64
    ret
.size _abi_new_list, .-_abi_new_list

/*
 * x0=list header, x1=value -> x0=list header (or NULL on growth failure).
 *
 * When full, capacity doubles and is clamped to at least four, exactly matching
 * the established x86-64 runtime. The old buffer is copied into a fresh bump-
 * allocated block; the wider runtime does not reclaim allocations yet either.
 */
.global _abi_list_append
.type _abi_list_append, %function
_abi_list_append:
    stp     x29, x30, [sp, #-96]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]
    stp     x23, x24, [sp, #48]
    str     x25, [sp, #64]

    mov     x19, x0               /* header */
    mov     x20, x1               /* value */
    ldr     x21, [x19, #8]        /* len */
    ldr     x22, [x19]            /* cap */
    cmp     x21, x22
    b.lo    .Llist_append_store

    lsl     x23, x22, #1
    cmp     x23, #4
    b.hs    .Llist_append_cap_ready
    mov     x23, #4
.Llist_append_cap_ready:
    lsr     x9, x23, #61          /* new_cap * 8 must not wrap */
    cbnz    x9, .Llist_append_fail
    lsl     x0, x23, #3
    bl      .Llist_alloc
    cbz     x0, .Llist_append_fail
    mov     x24, x0               /* new buffer */
    ldr     x25, [x19, #16]       /* old buffer */
    mov     x9, xzr
.Llist_append_copy:
    cmp     x9, x21
    b.hs    .Llist_append_copied
    ldr     x10, [x25, x9, lsl #3]
    str     x10, [x24, x9, lsl #3]
    add     x9, x9, #1
    b       .Llist_append_copy
.Llist_append_copied:
    str     x24, [x19, #16]
    str     x23, [x19]

.Llist_append_store:
    ldr     x9, [x19, #16]
    str     x20, [x9, x21, lsl #3]
    add     x21, x21, #1
    str     x21, [x19, #8]
    mov     x0, x19
    b       .Llist_append_done

.Llist_append_fail:
    mov     x0, xzr
.Llist_append_done:
    ldr     x25, [sp, #64]
    ldp     x23, x24, [sp, #48]
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #96
    ret
.size _abi_list_append, .-_abi_list_append

/*
 * x0=destination list, x1=source list -> x0=destination (or NULL if append
 * growth fails). Source length and buffer are snapshotted before mutation so
 * xs.extend(xs) duplicates exactly the original active cells once.
 */
.global _abi_list_extend
.type _abi_list_extend, %function
_abi_list_extend:
    stp     x29, x30, [sp, #-80]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]
    str     x23, [sp, #48]

    mov     x19, x0               /* destination */
    mov     x20, x1               /* source */
    ldr     x21, [x20, #8]        /* source length snapshot */
    ldr     x22, [x20, #16]       /* source buffer snapshot */
    mov     x23, xzr
.Llist_extend_loop:
    cmp     x23, x21
    b.hs    .Llist_extend_done
    ldr     x1, [x22, x23, lsl #3]
    mov     x0, x19
    bl      _abi_list_append
    cbz     x0, .Llist_extend_fail
    add     x23, x23, #1
    b       .Llist_extend_loop
.Llist_extend_done:
    mov     x0, x19
    b       .Llist_extend_return
.Llist_extend_fail:
    mov     x0, xzr
.Llist_extend_return:
    ldr     x23, [sp, #48]
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #80
    ret
.size _abi_list_extend, .-_abi_list_extend

/*
 * x0=list header, x1=index, x2=value -> x0=list header (or NULL if append
 * growth fails). Insertion indices use Python's clamping semantics.
 */
.global _abi_list_insert
.type _abi_list_insert, %function
_abi_list_insert:
    stp     x29, x30, [sp, #-96]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]
    stp     x23, x24, [sp, #48]
    str     x25, [sp, #64]

    mov     x19, x0               /* header */
    mov     x20, x1               /* requested index */
    mov     x21, x2               /* inserted value */
    ldr     x22, [x19, #8]        /* old length */

    cmp     x20, #0
    b.ge    .Llist_insert_high_clamp
    add     x20, x20, x22
    cmp     x20, #0
    b.ge    .Llist_insert_high_clamp
    mov     x20, xzr
.Llist_insert_high_clamp:
    cmp     x20, x22
    b.le    .Llist_insert_index_ready
    mov     x20, x22
.Llist_insert_index_ready:
    mov     x0, x19
    mov     x1, x21
    bl      _abi_list_append
    cbz     x0, .Llist_insert_fail

    ldr     x23, [x19, #16]       /* append may have relocated the buffer */
    mov     x24, x22              /* destination starts at old length */
.Llist_insert_shift:
    cmp     x24, x20
    b.le    .Llist_insert_store
    sub     x25, x24, #1
    ldr     x9, [x23, x25, lsl #3]
    str     x9, [x23, x24, lsl #3]
    mov     x24, x25
    b       .Llist_insert_shift
.Llist_insert_store:
    str     x21, [x23, x20, lsl #3]
    mov     x0, x19
    b       .Llist_insert_done
.Llist_insert_fail:
    mov     x0, xzr
.Llist_insert_done:
    ldr     x25, [sp, #64]
    ldp     x23, x24, [sp, #48]
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #96
    ret
.size _abi_list_insert, .-_abi_list_insert

/*
 * x0=list header -> x0=removed final 8-byte cell.
 *
 * This intentionally matches the existing native runtime's current contract:
 * callers must provide a non-empty list. Empty-pop IndexError support remains
 * gated on the catchable ARM64 exception runtime rather than being faked here.
 */
.global _abi_list_pop
.type _abi_list_pop, %function
_abi_list_pop:
    ldr     x9, [x0, #8]
    sub     x9, x9, #1
    str     x9, [x0, #8]
    ldr     x10, [x0, #16]
    ldr     x0, [x10, x9, lsl #3]
    ret
.size _abi_list_pop, .-_abi_list_pop

/* x0=list header -> reverse its active cells in place, return value ignored. */
.global _abi_list_reverse
.type _abi_list_reverse, %function
_abi_list_reverse:
    ldr     x9, [x0, #8]
    cmp     x9, #1
    b.ls    .Llist_reverse_done
    ldr     x10, [x0, #16]
    mov     x11, xzr              /* left index */
    sub     x12, x9, #1           /* right index */
.Llist_reverse_loop:
    cmp     x11, x12
    b.hs    .Llist_reverse_done
    ldr     x13, [x10, x11, lsl #3]
    ldr     x14, [x10, x12, lsl #3]
    str     x14, [x10, x11, lsl #3]
    str     x13, [x10, x12, lsl #3]
    add     x11, x11, #1
    sub     x12, x12, #1
    b       .Llist_reverse_loop
.Llist_reverse_done:
    ret
.size _abi_list_reverse, .-_abi_list_reverse

.bss
.balign 16
.Llist_heap_pos:
    .quad 0
.Llist_heap:
    .skip 1048576

.section .note.GNU-stack,"",%progbits
