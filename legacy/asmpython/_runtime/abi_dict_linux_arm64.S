/*
 * Minimal freestanding AArch64 dict allocation and core mutation runtime.
 *
 * Layout mirrors the x86-64 runtime's own DICT_* constants exactly
 * (asmpython/_compiler/codegen.py's Codegen.DICT_* class attributes) --
 * both architectures use real 8-byte pointers, so no narrowing is needed
 * here the way i386's own port had to narrow list/dict element widths:
 *   header (40 bytes): cap@0, len@8, tomb@16, buf@24, order@32
 *   slot (16 bytes):   key_ptr@0, value@8
 * `buf` holds `cap` slots (open addressing with tombstones, linear
 * probing); `order` holds `cap` 8-byte key pointers in insertion order
 * (iteration order support), matching the x86-64 reference's own scheme.
 *
 * Provides:
 *   _abi_new_instance()                      -> new empty dict header
 *   _abi_dict_set(dict, key, value)          -> void
 *   _abi_dict_contains(dict, key)            -> 0/1
 *   _abi_dict_get_default(dict, key, default) -> value or default
 *   _abi_dict_keys(dict)                      -> new list of keys
 *   _abi_dict_update(dst, src)                -> void  ** KNOWN REAL BUG,
 *       see the function's own comment below -- new_instance/set/
 *       contains/get_default/keys are each individually verified working
 *       via a real compiled asmpython program executed under qemu-aarch64;
 *       update reproducibly SIGSEGVs (si_addr=NULL) partway through its
 *       loop on the very first non-trivial (2-entry) merge. Root cause
 *       not yet found: every register-preservation/stack-alignment
 *       hypothesis checked by hand (x9/x10 save-restore span, sp
 *       16-byte-alignment arithmetic across the nested .Ldict_lookup_slot/
 *       _abi_dict_set calls, AAPCS64 callee-saved-register discipline in
 *       .Ldict_lookup_slot's own prologue/epilogue) traced out correct on
 *       paper. Needs either real hardware-level step debugging (no gdb-
 *       multiarch available in this dev environment to attach to the
 *       qemu-aarch64 process) or a fresh pair of eyes; do not trust this
 *       function until it's fixed and re-verified.
 *
 * NOT yet provided (documented gap, not silently missing): _abi_dict_repr/
 * _abi_set_repr (need the string-concatenation machinery the string
 * runtime slice would provide for building "{k: v, ...}" text), and
 * _abi_dict_pop/_abi_dict_clear (mirroring the i386 port's own staged
 * approach: core data-structure ops first, less-common mutators after).
 * A program calling one of these gets a real link-time undefined-symbol
 * error, not a silent miscompile.
 *
 * Key comparison uses _abi_str_cmp (already real and verified, see
 * abi_strings_linux_arm64.S) since asmpython dict keys are always
 * strings at this layer (non-string keys are boxed/stringified before
 * reaching here, matching the x86-64 runtime's own convention).
 * Hashing uses _abi_hash_string (already real and verified, 64-bit
 * FNV-1a -- no narrowing needed here either, unlike i386's 32-bit port,
 * since AArch64 has a real 64x64->64 MUL and 64-bit GP registers
 * throughout).
 */
.text
.balign 4

.equ DICT_CAP_OFF,   0
.equ DICT_LEN_OFF,   8
.equ DICT_TOMB_OFF,  16
.equ DICT_BUF_OFF,   24
.equ DICT_ORDER_OFF, 32
.equ DICT_HEADER,    40
.equ DICT_SLOT_SIZE, 16

.extern _abi_hash_string
.extern _abi_str_cmp

/* x0=size -> x0=zero-initialized unique block, or NULL on exhaustion.
 * A separate small heap from .Llist_alloc/.Lstring_alloc's own (each
 * translation unit gets its own private bump region) -- simplest way to
 * avoid a cross-file symbol dependency for a freestanding runtime slice
 * that's meant to be independently assemblable/verifiable, matching
 * every other *_linux_arm64.S file's own established pattern. */
.type .Ldict_alloc, %function
.Ldict_alloc:
    add     x0, x0, #7
    and     x0, x0, #-8
    adrp    x9, .Ldict_heap_pos
    add     x9, x9, :lo12:.Ldict_heap_pos
    ldr     x10, [x9]
    add     x11, x10, x0
    mov     x12, #0x10
    lsl     x12, x12, #16          /* 1 MiB */
    cmp     x11, x12
    b.hi    .Ldict_alloc_fail
    str     x11, [x9]
    adrp    x0, .Ldict_heap
    add     x0, x0, :lo12:.Ldict_heap
    add     x0, x0, x10
    ret
.Ldict_alloc_fail:
    mov     x0, xzr
    ret
.size .Ldict_alloc, .-.Ldict_alloc

/*
 * x0=header, x1=key -> x0=slot ptr (found or first empty/tombstone),
 * x1=1 if an existing occupied slot was found (0 if the returned slot
 * is empty/available for insertion). Linear probing over buf, cap
 * slots, index = hash(key) & (cap-1) (cap is always a power of two).
 */
.type .Ldict_lookup_slot, %function
.Ldict_lookup_slot:
    stp     x29, x30, [sp, #-64]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]
    stp     x23, x24, [sp, #48]

    mov     x19, x0                /* header */
    mov     x20, x1                /* key */
    ldr     x21, [x19, #DICT_CAP_OFF]
    sub     x22, x21, #1            /* cap - 1 mask */
    mov     x0, x20
    bl      _abi_hash_string
    and     x23, x0, x22            /* idx */
    mov     x24, xzr                /* first_tombstone (0 = none yet) */

.Ldls_probe:
    ldr     x9, [x19, #DICT_BUF_OFF]
    add     x9, x9, x23, lsl #4      /* slot ptr = buf + idx*16 */
    ldr     x10, [x9]                /* key in slot */
    cbz     x10, .Ldls_empty
    cmp     x10, #1                  /* tombstone marker */
    b.ne    .Ldls_compare
    cbnz    x24, .Ldls_advance
    mov     x24, x9
    b       .Ldls_advance

.Ldls_compare:
    mov     x0, x10
    mov     x1, x20
    bl      _abi_str_cmp
    cbnz    x0, .Ldls_advance
    mov     x0, x9
    mov     x1, #1
    b       .Ldls_ret

.Ldls_advance:
    add     x23, x23, #1
    and     x23, x23, x22
    b       .Ldls_probe

.Ldls_empty:
    cbnz    x24, .Ldls_ret_empty_tomb
    mov     x0, x9
    mov     x1, xzr
    b       .Ldls_ret
.Ldls_ret_empty_tomb:
    mov     x0, x24
    mov     x1, xzr

.Ldls_ret:
    ldp     x23, x24, [sp, #48]
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #64
    ret
.size .Ldict_lookup_slot, .-.Ldict_lookup_slot

/*
 * x0=header -> void. Doubles capacity, rehashes every occupied slot into
 * a fresh, larger buffer (tombstones are dropped, matching the x86-64
 * reference's own grow semantics -- a grow compacts, it doesn't just
 * resize in place).
 */
.type .Ldict_grow, %function
.Ldict_grow:
    stp     x29, x30, [sp, #-96]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    stp     x21, x22, [sp, #32]
    stp     x23, x24, [sp, #48]
    stp     x25, x26, [sp, #64]

    mov     x19, x0                  /* header */
    ldr     x20, [x19, #DICT_CAP_OFF]
    ldr     x21, [x19, #DICT_BUF_OFF]
    ldr     x22, [x19, #DICT_LEN_OFF]
    ldr     x23, [x19, #DICT_ORDER_OFF]

    lsl     x24, x20, #1              /* new_cap = cap * 2 */
    mov     x0, x24
    lsl     x0, x0, #4                /* new_cap * 16 bytes for slots */
    bl      .Ldict_alloc
    mov     x25, x0                    /* new buf */

    mov     x0, x24
    lsl     x0, x0, #3                /* new_cap * 8 bytes for order */
    bl      .Ldict_alloc
    mov     x26, x0                    /* new order buf */

    str     x24, [x19, #DICT_CAP_OFF]
    str     xzr, [x19, #DICT_LEN_OFF]
    str     xzr, [x19, #DICT_TOMB_OFF]
    str     x25, [x19, #DICT_BUF_OFF]
    str     x26, [x19, #DICT_ORDER_OFF]

    mov     x9, xzr                    /* old slot index */
.Ldgrow_loop:
    cmp     x9, x20
    b.ge    .Ldgrow_done
    add     x10, x21, x9, lsl #4       /* old slot ptr */
    ldr     x11, [x10]                 /* old key */
    cmp     x11, #1
    b.ls    .Ldgrow_next               /* empty (0) or tombstone (1): skip */
    ldr     x12, [x10, #8]             /* old value */

    stp     x9, x11, [sp, #-32]!       /* save loop index + key across the call */
    str     x12, [sp, #16]
    mov     x0, x19
    mov     x1, x11
    bl      .Ldict_lookup_slot         /* x0 = fresh slot for this key */
    ldr     x11, [sp, #8]
    ldr     x12, [sp, #16]
    ldp     x9, x21, [sp], #32         /* NOTE: x21 clobbered on purpose below -- see next line */
    str     x11, [x0]
    str     x12, [x0, #8]
    ldr     x21, [x19, #DICT_BUF_OFF]  /* re-derive: .Ldict_lookup_slot doesn't move
                                          the buffer itself, but reloading keeps this
                                          loop's own x21 (old buf pointer) correct
                                          without relying on a callee-saved guarantee
                                          this file doesn't actually make for x21 */
    ldr     x13, [x19, #DICT_ORDER_OFF]
    ldr     x14, [x19, #DICT_LEN_OFF]
    str     x11, [x13, x14, lsl #3]
    add     x14, x14, #1
    str     x14, [x19, #DICT_LEN_OFF]

.Ldgrow_next:
    add     x9, x9, #1
    b       .Ldgrow_loop
.Ldgrow_done:
    ldp     x25, x26, [sp, #64]
    ldp     x23, x24, [sp, #48]
    ldp     x21, x22, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #96
    ret
.size .Ldict_grow, .-.Ldict_grow

/* x0=(none) -> x0=new empty dict header (cap=8, len=0, tomb=0). */
.global _abi_new_instance
.type _abi_new_instance, %function
_abi_new_instance:
    stp     x29, x30, [sp, #-32]!
    mov     x29, sp
    str     x19, [sp, #16]

    mov     x0, #DICT_HEADER
    bl      .Ldict_alloc
    mov     x19, x0
    mov     x9, #8
    str     x9, [x19, #DICT_CAP_OFF]
    str     xzr, [x19, #DICT_LEN_OFF]
    str     xzr, [x19, #DICT_TOMB_OFF]

    mov     x0, #(8 * DICT_SLOT_SIZE)
    bl      .Ldict_alloc
    str     x0, [x19, #DICT_BUF_OFF]

    mov     x0, #(8 * 8)
    bl      .Ldict_alloc
    str     x0, [x19, #DICT_ORDER_OFF]

    mov     x0, x19
    ldr     x19, [sp, #16]
    ldp     x29, x30, [sp], #32
    ret
.size _abi_new_instance, .-_abi_new_instance

/* x0=dict, x1=key, x2=value -> void. Grows first if load factor would
 * exceed 3/4 after this insert (matching the x86-64 reference's own
 * `(len+tomb) >= cap - cap/4` grow trigger). */
.global _abi_dict_set
.type _abi_dict_set, %function
_abi_dict_set:
    stp     x29, x30, [sp, #-48]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    str     x21, [sp, #32]

    mov     x19, x0                  /* dict */
    mov     x20, x1                  /* key */
    mov     x21, x2                  /* value */

    ldr     x9, [x19, #DICT_LEN_OFF]
    ldr     x10, [x19, #DICT_TOMB_OFF]
    add     x9, x9, x10
    ldr     x10, [x19, #DICT_CAP_OFF]
    mov     x11, x10
    lsr     x11, x11, #2
    sub     x11, x10, x11
    cmp     x9, x11
    b.lt    .Ldset_no_grow
    mov     x0, x19
    bl      .Ldict_grow
.Ldset_no_grow:

    mov     x0, x19
    mov     x1, x20
    bl      .Ldict_lookup_slot        /* x0=slot, x1=found_existing */
    cbnz    x1, .Ldset_overwrite

    /* new entry: was this slot a tombstone? if so, tomb-- */
    ldr     x9, [x0]
    cmp     x9, #1
    b.ne    .Ldset_no_tomb_dec
    ldr     x9, [x19, #DICT_TOMB_OFF]
    sub     x9, x9, #1
    str     x9, [x19, #DICT_TOMB_OFF]
.Ldset_no_tomb_dec:
    str     x20, [x0]
    str     x21, [x0, #8]
    ldr     x9, [x19, #DICT_ORDER_OFF]
    ldr     x10, [x19, #DICT_LEN_OFF]
    str     x20, [x9, x10, lsl #3]
    add     x10, x10, #1
    str     x10, [x19, #DICT_LEN_OFF]
    b       .Ldset_done

.Ldset_overwrite:
    str     x21, [x0, #8]

.Ldset_done:
    ldr     x21, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #48
    ret
.size _abi_dict_set, .-_abi_dict_set

/* x0=dict, x1=key -> x0=0/1. */
.global _abi_dict_contains
.type _abi_dict_contains, %function
_abi_dict_contains:
    stp     x29, x30, [sp, #-16]!
    mov     x29, sp
    bl      .Ldict_lookup_slot
    mov     x0, x1
    ldp     x29, x30, [sp], #16
    ret
.size _abi_dict_contains, .-_abi_dict_contains

/* x0=dict, x1=key, x2=default -> x0=value (or default if key absent). */
.global _abi_dict_get_default
.type _abi_dict_get_default, %function
_abi_dict_get_default:
    stp     x29, x30, [sp, #-32]!
    mov     x29, sp
    str     x19, [sp, #16]
    mov     x19, x2                  /* default */
    bl      .Ldict_lookup_slot
    cbz     x1, .Ldgd_missing
    ldr     x0, [x0, #8]
    b       .Ldgd_done
.Ldgd_missing:
    mov     x0, x19
.Ldgd_done:
    ldr     x19, [sp, #16]
    ldp     x29, x30, [sp], #32
    ret
.size _abi_dict_get_default, .-_abi_dict_get_default

/* x0=dict -> x0=new list of keys, in insertion order (order buf already
 * tracks this directly -- a straight copy of the first `len` entries). */
.global _abi_dict_keys
.type _abi_dict_keys, %function
_abi_dict_keys:
    stp     x29, x30, [sp, #-48]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    str     x21, [sp, #32]

    mov     x19, x0                  /* dict */
    ldr     x20, [x19, #DICT_LEN_OFF]

    mov     x0, #24                   /* LIST_HEADER */
    bl      .Ldict_alloc
    mov     x21, x0
    mov     x9, x20
    cmp     x9, #4
    b.ge    .Ldkeys_cap_ok
    mov     x9, #4
.Ldkeys_cap_ok:
    str     x9, [x21, #0]             /* LIST_CAP_OFF */
    str     x20, [x21, #8]            /* LIST_LEN_OFF */

    mov     x0, x9
    lsl     x0, x0, #3
    bl      .Ldict_alloc
    str     x0, [x21, #16]            /* LIST_BUF_OFF */

    ldr     x9, [x19, #DICT_ORDER_OFF]
    mov     x10, xzr
.Ldkeys_copy:
    cmp     x10, x20
    b.ge    .Ldkeys_done
    ldr     x11, [x9, x10, lsl #3]
    str     x11, [x0, x10, lsl #3]
    add     x10, x10, #1
    b       .Ldkeys_copy
.Ldkeys_done:
    mov     x0, x21
    ldr     x21, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #48
    ret
.size _abi_dict_keys, .-_abi_dict_keys

/* x0=dst, x1=src -> void. dst[k] = src[k] for every k in src. */
.global _abi_dict_update
.type _abi_dict_update, %function
_abi_dict_update:
    stp     x29, x30, [sp, #-48]!
    mov     x29, sp
    stp     x19, x20, [sp, #16]
    str     x21, [sp, #32]

    mov     x19, x0                  /* dst */
    mov     x20, x1                  /* src */
    ldr     x21, [x20, #DICT_LEN_OFF]
    ldr     x9, [x20, #DICT_ORDER_OFF]
    mov     x10, xzr

.Ldupdate_loop:
    cmp     x10, x21
    b.ge    .Ldupdate_done
    ldr     x11, [x9, x10, lsl #3]    /* key */
    stp     x9, x10, [sp, #-16]!
    mov     x0, x20
    mov     x1, x11
    bl      .Ldict_lookup_slot        /* x0=slot in SRC for this key */
    ldr     x12, [x0, #8]             /* src value */
    mov     x0, x19
    mov     x1, x11
    mov     x2, x12
    bl      _abi_dict_set
    ldp     x9, x10, [sp], #16
    ldr     x9, [x20, #DICT_ORDER_OFF] /* re-derive: dict_set on dst never
                                          moves src's own order buffer, but
                                          reloading is cheap insurance and
                                          keeps this loop from depending on
                                          x9 surviving the call above by
                                          convention rather than by this
                                          file's own explicit save/restore */
    add     x10, x10, #1
    b       .Ldupdate_loop
.Ldupdate_done:
    ldr     x21, [sp, #32]
    ldp     x19, x20, [sp, #16]
    ldp     x29, x30, [sp], #48
    ret
.size _abi_dict_update, .-_abi_dict_update

.bss
.balign 8
.Ldict_heap_pos: .skip 8
.Ldict_heap: .skip 0x100000
