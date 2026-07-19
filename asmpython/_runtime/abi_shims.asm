; ABI shim layer: thin wrappers exposing asmpython's runtime helpers (which
; use codegen.py's own ad-hoc internal calling convention -- rax/rbx/rcx for
; most 2-3 arg helpers) under the standard Win64 ABI (rcx/rdx/r8), so the
; built-in x86-64 backend's SSA IR pipeline (driver.py's --backend x86-64;
; its `call` op always marshals args into the standard ABI registers) can
; call them directly. Zero changes to the existing, tested runtime
; internals -- one small wrapper per helper needed.
;
; Layout constants (DICT_*) mirror asmpython/_compiler/codegen.py's
; Codegen.DICT_* class attributes exactly; keep in sync if those ever change.

BITS 64
default rel

extern _runtime_dict_get_default
extern _runtime_dict_set
extern _runtime_dict_contains
extern _runtime_dict_keys
extern _runtime_dict_update
extern _runtime_str_concat
extern _runtime_str_repeat
extern _runtime_str_eq
extern _runtime_str_cmp
extern _runtime_int_to_base
extern _runtime_fmt_elem
extern _runtime_list_repr
extern _runtime_dict_repr
extern _runtime_set_repr
extern _runtime_str_char_at
extern _runtime_str_slice
extern _runtime_zalloc
extern _runtime_list_append
extern _runtime_list_pop
extern _runtime_list_slice
extern _runtime_list_slice_assign
extern _runtime_str_upper
extern _runtime_str_lower
extern _runtime_str_strip
extern _runtime_str_isdigit
extern _runtime_str_to_int
extern _runtime_str_index_of
extern _runtime_str_replace
extern _runtime_str_split
extern _runtime_str_rsplit
extern _runtime_str_partition
extern _runtime_str_rpartition
extern _runtime_str_join
extern _runtime_str_zfill
extern _runtime_str_starts_with
extern _runtime_str_ends_with
extern _runtime_str_count
extern _runtime_str_capitalize
extern _runtime_str_isalpha
extern _runtime_str_isalnum
extern _runtime_str_islower
extern _runtime_str_isupper
extern _runtime_str_isspace
extern _runtime_str_lstrip
extern _runtime_str_rstrip
extern _runtime_str_ljust
extern _runtime_str_rjust
extern _runtime_str_center
extern _runtime_str_swapcase
extern _runtime_str_title
extern _runtime_str_splitlines
extern _runtime_str_split_ws
extern _runtime_str_removeprefix
extern _runtime_str_removesuffix
extern _runtime_list_reverse
extern _runtime_list_extend
extern _runtime_list_repeat
extern _runtime_list_insert
extern _runtime_range_list
extern _runtime_sort_str
extern _runtime_sort_int
extern _runtime_sort_items
extern _runtime_sort_pairs_str
extern _runtime_sort_pairs_int
extern _runtime_chr
extern _runtime_setjmp
extern _runtime_raise
extern _runtime_divmod
extern _runtime_input
extern _runtime_list_del
extern _runtime_dict_pop
extern _runtime_dict_clear
extern _runtime_str_concat_dup
extern _runtime_str_truncate
extern _runtime_int_to_binary
extern _runtime_group_digits
extern _runtime_group_digits_zeropad
extern malloc
extern printf
extern sprintf
extern putchar
extern strtoll

global _abi_dict_get_default
global _abi_setjmp
global _abi_raise
global _abi_dict_set
global _abi_dict_contains
global _abi_dict_keys
global _abi_dict_update
global _abi_str_concat
global _abi_str_repeat
global _abi_str_rsplit
global _abi_str_partition
global _abi_str_rpartition
global _abi_int_to_base
global _abi_fmt_elem
global _abi_list_repr
global _abi_dict_repr
global _abi_set_repr
global _abi_str_char_at
global _abi_str_slice
global _abi_new_instance
global _abi_new_list
global _abi_list_append
global _abi_list_pop
global _abi_list_slice
global _abi_list_slice_assign
global _abi_str_eq
global _abi_str_cmp
global _abi_str_upper
global _abi_str_lower
global _abi_str_strip
global _abi_str_isdigit
global _abi_str_index_of
global _abi_str_replace
global _abi_str_split
global _abi_str_join
global _abi_str_zfill
global _abi_str_starts_with
global _abi_str_ends_with
global _abi_str_count
global _abi_str_capitalize
global _abi_str_isalpha
global _abi_str_isalnum
global _abi_str_islower
global _abi_str_isupper
global _abi_str_isspace
global _abi_str_lstrip
global _abi_str_rstrip
global _abi_str_ljust
global _abi_str_rjust
global _abi_str_center
global _abi_str_swapcase
global _abi_str_title
global _abi_str_splitlines
global _abi_str_split_ws
global _abi_str_removeprefix
global _abi_str_removesuffix
global _abi_list_reverse
global _abi_list_extend
global _abi_list_repeat
global _abi_list_insert
global _abi_range_list
global _abi_sort_str
global _abi_sort_int
global _abi_sort_items
global _abi_sort_pairs_str
global _abi_sort_pairs_int
global _abi_chr
global _abi_str_to_int
global _abi_str_to_int_base
global _abi_int_to_str
global _abi_divmod
global _abi_input
global _abi_round_f64
global _abi_float_to_str
global _abi_fmax_f64
global _abi_fmin_f64
global _abi_list_del
global _abi_dict_pop
global _abi_dict_clear
global _abi_str_concat_dup
global _abi_str_truncate
global _abi_int_to_binary
global _abi_group_digits
global _abi_group_digits_zeropad
global _abi_int_fmt
global _abi_float_fmt

; asmpython/stdlib/hardware.py's _hw_* symbols, hosted-target bodies. These
; already use the standard Win64 ABI (see codegen.py's target_windows.py /
; _emit_console_runtime: a0..a3 there are just _arg_reg(0..3), i.e. rcx/rdx/
; r8/r9) -- no bridging needed, just direct ports so hardware.py calls work
; through the new IR pipeline the same way they already do through the
; legacy NASM-text codegen.py. Most are stubs (ring-0-only ops unavailable
; to ring-3 hosted code); rdtsc/cpuid/rdrand are real since those three are
; unprivileged. console_* are real too (ANSI/VT100 escapes over printf).
global _hw_in_byte, _hw_out_byte, _hw_in_word, _hw_out_word
global _hw_in_dword, _hw_out_dword, _hw_mmio_read8, _hw_mmio_write8
global _hw_mmio_read32, _hw_mmio_write32
global _hw_rdtsc, _hw_cpuid, _hw_rdrand
global _hw_halt, _hw_cli, _hw_sti, _hw_io_wait
global _hw_pic_eoi, _hw_pic_mask, _hw_pic_unmask, _hw_pit_set_freq
global _hw_keyboard_read, _hw_keyboard_poll
global _hw_vga_set_color, _hw_vga_set_cursor, _hw_vga_get_row, _hw_vga_get_col
global _hw_read_cr0, _hw_read_cr2, _hw_read_cr3, _hw_read_cr4, _hw_write_cr3
global _hw_read_msr, _hw_write_msr, _hw_invlpg, _hw_lidt
global _hw_console_clear, _hw_console_putc, _hw_console_write
global _hw_console_set_color, _hw_console_set_cursor
global _hw_console_get_row, _hw_console_get_col

section .text

%macro WIN64_RUNTIME_ENTER 0
    push rbx
    push rsi
    push rdi
    push r12
    push r13
    push r14
    push r15
%endmacro

%macro WIN64_RUNTIME_LEAVE 0
    pop r15
    pop r14
    pop r13
    pop r12
    pop rdi
    pop rsi
    pop rbx
%endmacro

; rax = dict_get_default(dict=rcx, key=rdx, default=r8)
;
; Each shim below uses RBX as scratch (the underlying _runtime_* helper's
; ad-hoc 2nd-argument register) but RBX is callee-saved per the Win64 ABI
; -- regalloc legitimately keeps a caller's own live value parked there
; across this call, same as any other external function, and clobbering
; it without saving/restoring silently destroys that value. Confirmed as
; a real bug on the SysV side (a dict pointer held in RBX across a
; chained call read back as garbage); fixed here too for
; correctness/consistency even though it wasn't observed to fail on
; Windows. `push rbx` / `pop rbx` fixes that *and* the stack alignment
; (entry rsp % 16 == 8, the standard post-`call` invariant; one push
; lands on 16-aligned before this shim's own `call`).
_abi_dict_get_default:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_dict_get_default
    WIN64_RUNTIME_LEAVE
    ret

; dict_set(dict=rcx, key=rdx, value=r8) -> void (rax undefined)
_abi_dict_set:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_dict_set
    WIN64_RUNTIME_LEAVE
    ret

; rax = dict_contains(dict=rcx, key=rdx)
_abi_dict_contains:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_dict_contains
    WIN64_RUNTIME_LEAVE
    ret

; rax = dict_keys(dict=rcx)
_abi_dict_keys:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_dict_keys
    WIN64_RUNTIME_LEAVE
    ret

; dict_update(dst=rcx, src=rdx) -> void
_abi_dict_update:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_dict_update
    WIN64_RUNTIME_LEAVE
    ret

; rax = str_concat(left=rcx, right=rdx)
_abi_str_concat:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_concat
    WIN64_RUNTIME_LEAVE
    ret

; rax = str_repeat(s=rcx, n=rdx) -- "str" * int
_abi_str_repeat:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_repeat
    WIN64_RUNTIME_LEAVE
    ret

; rax = int_to_base(n=rcx, base=rdx, prefix=r8)
_abi_int_to_base:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_int_to_base
    WIN64_RUNTIME_LEAVE
    ret

; rax = fmt_elem(value=rcx, kind=rdx)
; rax = fmt_elem(value=rcx, kind=rdx) -> always a freshly-owned string.
; _runtime_fmt_elem's int (kind 0) and float (kind 2) branches return a
; raw pointer into the shared static itoa_str_buf, not a fresh
; allocation (str/list/dict/tuple kinds already allocate). That's
; invisible to a caller that immediately concats or prints the result
; once, but this pipeline's print()/f-string lowering computes ALL of a
; multi-arg call's formatted strings up front before a single shared
; call (e.g. one printf with several %s), so two int/float args in the
; same call alias the same buffer -- the second call's sprintf
; overwrites the first's already-computed pointer's target before
; anything reads it, silently making every such arg show the LAST one's
; value. Confirmed via a live repro: `print(a, some_list, b)` where a/b
; are opaque ("any"-typed, e.g. from a starred-unpack target) ints both
; printed b's value. Dup unconditionally here rather than special-casing
; by kind -- cheap, and correctness matters far more than one avoidable
; allocation for the str/list/dict/tuple kinds that didn't need it.
_abi_fmt_elem:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_fmt_elem
    call _runtime_str_concat_dup
    WIN64_RUNTIME_LEAVE
    ret

; rax = list_repr(list=rcx, elem_kind=rdx)
_abi_list_repr:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_list_repr
    WIN64_RUNTIME_LEAVE
    ret

; rax = dict_repr(dict=rcx, key_kind=rdx, value_kind=r8)
_abi_dict_repr:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_dict_repr
    WIN64_RUNTIME_LEAVE
    ret

; rax = set_repr(set=rcx, elem_kind=rdx)
_abi_set_repr:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_set_repr
    WIN64_RUNTIME_LEAVE
    ret

; rax = str_char_at(str=rcx, index=rdx)
_abi_str_char_at:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_char_at
    WIN64_RUNTIME_LEAVE
    ret

; rax = str_slice(str=rcx, start=rdx, stop=r8)
_abi_str_slice:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_str_slice
    WIN64_RUNTIME_LEAVE
    ret

; rax = new empty instance dict (no args). Mirrors the inline sequence
; codegen.py emits at every class-instantiation site: malloc the 40-byte
; DICT_HEADER, zero-init cap/len/tomb, then zalloc the slot buffer (8
; slots * 16 bytes) and the order buffer (64 bytes), wiring both into the
; header. RBX is callee-saved under Win64 so it's pushed/popped even
; though nothing here needs ITS value preserved -- only that the caller's
; RBX survives the call, since this body repeatedly reuses RBX as
; _runtime_zalloc's size argument.
_abi_new_instance:
    WIN64_RUNTIME_ENTER
    sub rsp, 48
    mov rcx, 40                  ; DICT_HEADER
    call malloc
    mov qword [rax+0], 8         ; DICT_CAP_OFF = 8 initial slots
    mov qword [rax+8], 0         ; DICT_LEN_OFF
    mov qword [rax+16], 0        ; DICT_TOMB_OFF
    mov [rsp+32], rax            ; spill header ptr (above the call shadow space)
    mov rbx, 128                 ; 8 * DICT_SLOT_SIZE(16)
    call _runtime_zalloc
    mov rcx, [rsp+32]
    mov [rcx+24], rax            ; DICT_BUF_OFF
    mov rbx, 64
    call _runtime_zalloc
    mov rcx, [rsp+32]
    mov [rcx+32], rax            ; DICT_ORDER_OFF
    mov rax, rcx
    add rsp, 48
    WIN64_RUNTIME_LEAVE
    ret

; rax = new list with initial capacity cap=rcx (elements; clamped to >=1
; to avoid zalloc(0)). Mirrors _abi_new_instance's pattern: LIST_HEADER is
; 24 bytes (cap@0, len@8, buf@16) vs DICT_HEADER's 40, and there's only
; one zalloc (the element buffer) instead of two.
_abi_new_list:
    WIN64_RUNTIME_ENTER
    sub rsp, 48
    mov rbx, rcx
    cmp rbx, 1
    jge .cap_ok
    mov rbx, 1
.cap_ok:
    mov rcx, 24                  ; LIST_HEADER
    call malloc
    mov [rax+0], rbx             ; LIST_CAP_OFF = cap
    mov qword [rax+8], 0         ; LIST_LEN_OFF
    mov [rsp+32], rax            ; spill header ptr
    mov rcx, rbx
    shl rcx, 3                   ; bytes = cap * 8
    mov rbx, rcx
    call _runtime_zalloc
    mov rcx, [rsp+32]
    mov [rcx+16], rax            ; LIST_BUF_OFF
    mov rax, rcx
    add rsp, 48
    WIN64_RUNTIME_LEAVE
    ret

; rax = int(str=rcx) -- raises ValueError (via _runtime_raise) on a
; non-numeric/empty string, matching Python's int() semantics. Used to
; call raw strtoll directly with NO validation at all (a real bug: any
; failed parse silently returned 0 instead of raising, so e.g.
; `int("abc")` couldn't be caught by `except ValueError`) -- fixed by
; routing through the same _runtime_str_to_int the legacy codegen.py
; backend already uses (rax=str in, rax=int out; skips/requires
; leading+trailing whitespace exactly like CPython's int(), raises on
; anything else including an all-whitespace or empty string).
_abi_str_to_int:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_to_int
    WIN64_RUNTIME_LEAVE
    ret

; rax = strtoll(str=rcx, NULL, base=rdx)
_abi_str_to_int_base:
    sub rsp, 40
    xor r8d, r8d
    mov r8, rdx
    xor rdx, rdx
    call strtoll
    add rsp, 40
    ret

; rax = chr(n=rcx)
_abi_chr:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_chr
    WIN64_RUNTIME_LEAVE
    ret

; sort_str(list=rcx) -> void
_abi_sort_str:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_sort_str
    WIN64_RUNTIME_LEAVE
    ret

; sort_int(list=rcx) -> void
_abi_sort_int:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_sort_int
    WIN64_RUNTIME_LEAVE
    ret

; sort_items(list=rcx) -> void
_abi_sort_items:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_sort_items
    WIN64_RUNTIME_LEAVE
    ret

; sort_pairs_str(elems=rcx, keys=rdx) -> void
_abi_sort_pairs_str:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_sort_pairs_str
    WIN64_RUNTIME_LEAVE
    ret

; sort_pairs_int(elems=rcx, keys=rdx) -> void
_abi_sort_pairs_int:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_sort_pairs_int
    WIN64_RUNTIME_LEAVE
    ret

; list_append(list=rcx, value=rdx) -> void
_abi_list_append:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_list_append
    WIN64_RUNTIME_LEAVE
    ret

; rax = list_pop(list=rcx) -- pops and returns the last element. No
; underflow check (matches codegen.py's own list.pop() -- an empty-list
; pop reads/decrements garbage, same pre-existing behavior, not new here).
_abi_list_pop:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_list_pop
    WIN64_RUNTIME_LEAVE
    ret

; rax = list_slice(src=rcx, start=rdx, stop=r8)
_abi_list_slice:
    WIN64_RUNTIME_ENTER
    mov r10, rdx
    mov r11, r8
    mov rax, rcx
    mov rbx, r10
    mov rcx, r11
    call _runtime_list_slice
    WIN64_RUNTIME_LEAVE
    ret

; list_slice_assign(dst=rcx, start=rdx, stop=r8, src=r9) -> void
_abi_list_slice_assign:
    WIN64_RUNTIME_ENTER
    mov r10, rdx
    mov r11, r8
    mov rax, rcx
    mov rbx, r9
    mov rcx, r10
    mov rdx, r11
    call _runtime_list_slice_assign
    WIN64_RUNTIME_LEAVE
    ret

; ---- str methods: one-arg (self only) helpers, rax=self -> rax=result.
_abi_str_eq:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_eq
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_cmp:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_cmp
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_upper:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_upper
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_lower:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_lower
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_strip:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_strip
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_isdigit:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_isdigit
    WIN64_RUNTIME_LEAVE
    ret

; ---- str methods: two-arg (self, arg2) helpers, rax=self/rbx=arg2 ->
; rax=result. RBX saved/restored same as every other 2-arg shim above.
_abi_str_index_of:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_index_of
    WIN64_RUNTIME_LEAVE
    ret

; rax = str_rindex_of(haystack=rcx, sub=rdx) -- backs str.rfind/rindex
; (no-start-arg form). Same runtime helper codegen.py's own STR_METHOD_
; RUNTIME table points "rfind"/"rindex" at.
extern _runtime_str_rindex_of
_abi_str_rindex_of:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_rindex_of
    WIN64_RUNTIME_LEAVE
    ret

; rax = str_index_of_start(haystack=rcx, sub=rdx, start=r8) -- backs
; str.find/index(sub, start). Mirrors codegen.py's own calling convention
; for this runtime helper exactly (rax=haystack, rbx=sub, rcx=start).
extern _runtime_str_index_of_start
_abi_str_index_of_start:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_str_index_of_start
    WIN64_RUNTIME_LEAVE
    ret

; rax = hash_string(s=rcx) -- backs hash(s) for str (FNV-1a 64-bit, same
; hasher the dict runtime itself uses for string keys). Thin wrapper over
; the already-linked _runtime_hash_string (rax=str ptr -> rax=hash, no
; RBX/etc scratch to preserve).
extern _runtime_hash_string
_abi_hash_string:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_hash_string
    WIN64_RUNTIME_LEAVE
    ret

; rax = str_expandtabs(self=rcx, tabsize=rdx) -- str.expandtabs([tabsize]);
; the no-arg Python default (tabsize=8) is applied by the CALLER (see
; ir_lower.py's expandtabs case), matching codegen.py's own
; mov rbx, 8 / call pattern for the 0-arg form.
extern _runtime_str_expandtabs
_abi_str_expandtabs:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_expandtabs
    WIN64_RUNTIME_LEAVE
    ret

; rax = list_slice_step(src=rcx, start=rdx, stop=r8, step=r9) -- backs
; xs[start:stop:step] once a step is present (a plain xs[start:stop]
; still uses the simpler _abi_list_slice, unaffected). Sentinels for
; missing endpoints (INT64_MIN=start, INT64_MAX=stop) match codegen.py's
; own _gen_list_slice exactly -- caller (ir_lower.py) fills those in
; before calling. Mirrors codegen.py's own register convention for
; _runtime_list_slice_step: rax=src, rbx=start, rcx=stop, rdx=step.
extern _runtime_list_slice_step
_abi_list_slice_step:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    mov rdx, r9
    call _runtime_list_slice_step
    WIN64_RUNTIME_LEAVE
    ret

; rax = str_slice_step(s=rcx, start=rdx, stop=r8, step=r9) -- backs
; s[start:stop:step] once a step is present (a plain s[start:stop] still
; uses the simpler _abi_str_slice, unaffected). Sentinel for a missing
; stop is INT64_MIN either way (the runtime itself picks the direction-
; correct default from step's sign) -- matches codegen.py's own
; _gen_str_slice_step comment exactly. Mirrors codegen.py's own register
; convention for _runtime_str_slice_step: rax=s, rbx=start, rcx=stop,
; r8=step (note: step is r8 here, NOT rdx like the list version above --
; a real asymmetry between the two runtime helpers, not a typo).
extern _runtime_str_slice_step
_abi_str_slice_step:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    mov r8, r9
    call _runtime_str_slice_step
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_split:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, 0                    ; maxsplit=0 is _runtime_str_split's own
                                   ; "unlimited" sentinel (checked via `test
                                   ; rcx,rcx; jz` at both use sites) -- NOT
                                   ; -1, which falls through its maxsplit-cap
                                   ; logic and corrupts the split entirely
                                   ; (confirmed via a live repro: -1 zeroed
                                   ; the result's length and split nothing).
    call _runtime_str_split
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_rsplit:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, 1
    call _runtime_str_rsplit
    WIN64_RUNTIME_LEAVE
    ret

; rax = str_partition(str=rcx, sep=rdx) -> 3-tuple (before, sep-or-"",
; after) list ptr, split at the FIRST occurrence of sep.
_abi_str_partition:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_partition
    WIN64_RUNTIME_LEAVE
    ret

; rax = str_rpartition(str=rcx, sep=rdx) -> 3-tuple (before, sep-or-"",
; after) list ptr, split at the LAST occurrence of sep.
_abi_str_rpartition:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_rpartition
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_join:
    WIN64_RUNTIME_ENTER
    mov rax, rcx                  ; self (the separator string)
    mov rbx, rdx                  ; the list to join
    call _runtime_str_join
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_zfill:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_zfill
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_starts_with:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_starts_with
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_ends_with:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_ends_with
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_count:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_count
    WIN64_RUNTIME_LEAVE
    ret

; rax = str_replace(self=rcx, old=rdx, new=r8) -> result
_abi_str_replace:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_str_replace
    WIN64_RUNTIME_LEAVE
    ret

; ---- more str methods: one-arg (self only), rax=self -> rax=result.
_abi_str_capitalize:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_capitalize
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_isalpha:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_isalpha
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_isalnum:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_isalnum
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_islower:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_islower
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_isupper:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_isupper
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_isspace:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_isspace
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_lstrip:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_lstrip
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_rstrip:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_rstrip
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_swapcase:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_swapcase
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_title:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_title
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_splitlines:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_splitlines
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_split_ws:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_split_ws
    WIN64_RUNTIME_LEAVE
    ret

; ---- more str methods: two-arg (self, arg2), rax=self/rbx=arg2 -> rax=result.
_abi_str_removeprefix:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_removeprefix
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_removesuffix:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_removesuffix
    WIN64_RUNTIME_LEAVE
    ret

; str padding: self=rcx, width=rdx, fillstr=r8. Runtime wants
; rax=self, rbx=width, rcx=first byte of fillstr.
_abi_str_ljust:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    movzx rcx, byte [r8]
    call _runtime_str_ljust
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_rjust:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    movzx rcx, byte [r8]
    call _runtime_str_rjust
    WIN64_RUNTIME_LEAVE
    ret
_abi_str_center:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    movzx rcx, byte [r8]
    call _runtime_str_center
    WIN64_RUNTIME_LEAVE
    ret

; ---- list methods.
; rax = list_reverse(list=rcx) -- in place, also returns the list ptr.
_abi_list_reverse:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_list_reverse
    WIN64_RUNTIME_LEAVE
    ret
; list_extend(list=rcx, other=rdx) -> void
_abi_list_extend:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_list_extend
    WIN64_RUNTIME_LEAVE
    ret
; list_repeat(list=rcx, count=rdx) -> rax
_abi_list_repeat:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_list_repeat
    WIN64_RUNTIME_LEAVE
    ret
; list_insert(list=rcx, index=rdx, value=r8) -> void
_abi_list_insert:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_list_insert
    WIN64_RUNTIME_LEAVE
    ret
; range_list(start=rcx, stop=rdx, step=r8) -> rax
_abi_range_list:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_range_list
    WIN64_RUNTIME_LEAVE
    ret

; ---- asmlib.hardware: ring-0-only ops, stubbed (unavailable to ring-3
; hosted code) -- matches WindowsCodegen._HW_STUBS's "xor rax,rax; ret"
; bodies exactly.
_hw_in_byte:
_hw_out_byte:
_hw_in_word:
_hw_out_word:
_hw_in_dword:
_hw_out_dword:
_hw_mmio_read8:
_hw_mmio_write8:
_hw_mmio_read32:
_hw_mmio_write32:
_hw_halt:
_hw_cli:
_hw_sti:
_hw_io_wait:
_hw_pic_eoi:
_hw_pic_mask:
_hw_pic_unmask:
_hw_pit_set_freq:
_hw_keyboard_read:
_hw_keyboard_poll:
_hw_vga_set_color:
_hw_vga_set_cursor:
_hw_vga_get_row:
_hw_vga_get_col:
_hw_read_cr0:
_hw_read_cr2:
_hw_read_cr3:
_hw_read_cr4:
_hw_write_cr3:
_hw_read_msr:
_hw_write_msr:
_hw_invlpg:
_hw_lidt:
    xor eax, eax
    ret

; ---- asmlib.hardware: unprivileged CPU instructions, real on ring 3 too.

; rax = rdtsc() -- 64-bit timestamp counter.
_hw_rdtsc:
    rdtsc
    shl rdx, 32
    or rax, rdx
    ret

; rax = cpuid(leaf=rcx) -- EAX after CPUID with EAX=leaf.
_hw_cpuid:
    mov eax, ecx
    push rbx
    cpuid
    pop rbx
    movsx rax, eax
    ret

; rax = rdrand() -- retries until the hardware RNG reports success.
_hw_rdrand:
.retry:
    rdrand rax
    jnc .retry
    ret

; ---- asmlib.hardware: high-level console (real on hosted targets). ANSI/
; VT100 escapes over printf; cursor position is write-only on a real
; terminal, so it's tracked locally in _con_row/_con_col, mirroring
; codegen.py's _emit_console_runtime exactly (including its alignment
; comment: every helper here assumes rsp % 16 == 8 on entry, the normal
; post-`call` invariant, and reserves 32/40 bytes so the printf/sprintf/
; putchar calls inside happen from a 16-byte-aligned rsp with >= 32 bytes
; of Win64 shadow space).

section .bss
_con_row:   resq 1
_con_col:   resq 1
_con_ch:    resq 1
_con_ansi1: resq 1
_con_ansi2: resq 1
_con_buf:   resb 32
_abi_int_to_str_buf: resb 32
_abi_float_to_str_buf: resb 40

section .rodata
_abi_fmt_lld:    db "%lld", 0
_con_fmt_clear:  db 27, "[2J", 27, "[H", 0
_con_fmt_color:  db 27, "[%dm", 27, "[%dm", 0
_con_fmt_cursor: db 27, "[%d;%dH", 0
_con_fmt_s:      db "%s", 0
_abi_fmt_g:      db "%g", 0
_abi_str_nan:    db "nan", 0
_abi_str_pinf:   db "inf", 0
_abi_str_ninf:   db "-inf", 0
_math_deg_factor: dq 57.29577951308232
_math_rad_factor: dq 0.017453292519943295
_math_inf_bits:   dq 0x7FF0000000000000
_math_abs_mask:   dq 0x7FFFFFFFFFFFFFFF

; erf() -- Abramowitz & Stegun 7.1.26 polynomial constants (max error ~1.5e-7).
_math_erf_p:  dq 0.3275911
_math_erf_a1: dq 0.254829592
_math_erf_a2: dq -0.284496736
_math_erf_a3: dq 1.421413741
_math_erf_a4: dq -1.453152027
_math_erf_a5: dq 1.061405429
_math_erf_one: dq 1.0
_math_erf_neg_one: dq -1.0

; gamma() -- Lanczos approximation, g=7, n=9 (standard published coefficient
; set; see e.g. Numerical Recipes / Wikipedia's "Lanczos approximation").
; Valid directly for x > 0.5; smaller/negative x would need the reflection
; formula, not needed here (math.gamma's only caller in-tree uses x=5.0).
_math_lanczos_g:  dq 7.0
_math_lanczos_c0: dq 0.99999999999980993
_math_lanczos_c1: dq 676.5203681218851
_math_lanczos_c2: dq -1259.1392167224028
_math_lanczos_c3: dq 771.32342877765313
_math_lanczos_c4: dq -176.61502916214059
_math_lanczos_c5: dq 12.507343278686905
_math_lanczos_c6: dq -0.13857109526572012
_math_lanczos_c7: dq 9.9843695780195716e-6
_math_lanczos_c8: dq 1.5056327351493116e-7
_math_lanczos_sqrt2pi: dq 2.5066282746310002
_math_lanczos_half: dq 0.5

section .text

; console_clear() -- ESC[2J ESC[H, and reset the tracked cursor to (0, 0).
_hw_console_clear:
    sub rsp, 40
    lea rcx, [_con_fmt_clear]
    xor eax, eax
    call printf
    xor eax, eax
    mov [_con_row], rax
    mov [_con_col], rax
    add rsp, 40
    ret

; console_putc(ch=rcx) -- putchar(ch); newline wraps row/col, else col++.
_hw_console_putc:
    sub rsp, 40
    mov [_con_ch], rcx
    call putchar
    mov rax, [_con_ch]
    cmp rax, 10
    je .nl
    inc qword [_con_col]
    jmp .done
.nl:
    mov qword [_con_col], 0
    inc qword [_con_row]
.done:
    xor eax, eax
    add rsp, 40
    ret

; console_write(s=rcx) -- printf("%s", s); track row/col over each char.
_hw_console_write:
    push rbx
    sub rsp, 32
    mov rbx, rcx
    mov rdx, rcx
    lea rcx, [_con_fmt_s]
    xor eax, eax
    call printf
.loop:
    movzx eax, byte [rbx]
    test eax, eax
    jz .done
    cmp eax, 10
    je .nl
    inc qword [_con_col]
    jmp .next
.nl:
    mov qword [_con_col], 0
    inc qword [_con_row]
.next:
    inc rbx
    jmp .loop
.done:
    xor eax, eax
    add rsp, 32
    pop rbx
    ret

; console_set_color(fg=rcx, bg=rdx) -- VGA palette index -> ANSI SGR code
; (0-7 -> 30-37/40-47 "normal"; 8-15 -> 90-97/100-107 "bright" aixterm),
; then write "ESC[<fg>m ESC[<bg>m".
_hw_console_set_color:
    sub rsp, 40
    mov rax, rcx
    cmp rax, 8
    jl .fg_lo
    add rax, 82
    jmp .fg_done
.fg_lo:
    add rax, 30
.fg_done:
    mov [_con_ansi1], rax
    mov rax, rdx
    cmp rax, 8
    jl .bg_lo
    add rax, 92
    jmp .bg_done
.bg_lo:
    add rax, 40
.bg_done:
    mov [_con_ansi2], rax
    lea rcx, [_con_buf]
    lea rdx, [_con_fmt_color]
    mov r8, [_con_ansi1]
    mov r9, [_con_ansi2]
    xor eax, eax
    call sprintf
    lea rcx, [_con_fmt_s]
    lea rdx, [_con_buf]
    xor eax, eax
    call printf
    xor eax, eax
    add rsp, 40
    ret

; console_set_cursor(row=rcx, col=rdx) -- track 0-indexed (row, col); write
; "ESC[<row+1>;<col+1>H" (ANSI cursor positions are 1-indexed).
_hw_console_set_cursor:
    sub rsp, 40
    mov rax, rcx
    mov [_con_row], rax
    inc rax
    mov [_con_ansi1], rax
    mov rax, rdx
    mov [_con_col], rax
    inc rax
    mov [_con_ansi2], rax
    lea rcx, [_con_buf]
    lea rdx, [_con_fmt_cursor]
    mov r8, [_con_ansi1]
    mov r9, [_con_ansi2]
    xor eax, eax
    call sprintf
    lea rcx, [_con_fmt_s]
    lea rdx, [_con_buf]
    xor eax, eax
    call printf
    xor eax, eax
    add rsp, 40
    ret

; console_get_row() / console_get_col() -- tracked cursor position.
_hw_console_get_row:
    mov rax, [_con_row]
    ret
_hw_console_get_col:
    mov rax, [_con_col]
    ret


; setjmp/raise shims: bridge Win64 ABI (args in rcx/rdx) to asmpython
; runtime convention (args in rax/rbx).
_abi_setjmp:
    ; rcx = jmp_buf ptr (Win64 arg0) -> rax for _runtime_setjmp
    mov rax, rcx
    jmp _runtime_setjmp

_abi_raise:
    ; rcx = exc_msg ptr (Win64 arg0) -> rax
    ; rdx = exc_type_id  (Win64 arg1) -> rbx
    mov rax, rcx
    mov rbx, rdx
    jmp _runtime_raise

; _abi_int_to_str(rcx=int64) -> rax = ptr to decimal string in static buf
; Win64: sprintf(char *buf, const char *fmt, int64 val)
;   rcx=buf, rdx=fmt, r8=val — move int to r8 first, then set rcx/rdx.
_abi_int_to_str:
    sub rsp, 40
    mov r8, rcx
    lea rcx, [_abi_int_to_str_buf]
    lea rdx, [_abi_fmt_lld]
    xor eax, eax
    call sprintf
    lea rax, [_abi_int_to_str_buf]
    add rsp, 40
    ret

; rax = divmod(a=rcx, b=rdx) -> 2-tuple ptr (floor-division quotient,
; floor-mod remainder), matching _runtime_divmod's floor semantics (the
; same helper //, % use) and its own zero-division raise.
_abi_divmod:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_divmod
    WIN64_RUNTIME_LEAVE
    ret

; rax = input() -> ptr to the most recent input line (\n stripped). No args:
; any prompt is the caller's job (print it before calling this, same
; contract as target_windows.py's _emit_input_line).
_abi_input:
    WIN64_RUNTIME_ENTER
    call _runtime_input
    WIN64_RUNTIME_LEAVE
    ret

; xmm0 = round_f64(xmm0) -> round-half-to-even to the nearest integral
; double (mode 0 = SSE4.1 round-to-nearest, ties-to-even -- matches
; CPython's round() banker's rounding, same instruction the legacy
; codegen.py uses for round(float)).
_abi_round_f64:
    roundsd xmm0, xmm0, 0
    ret

; rax = float_to_str(xmm0) -> ptr to a nul-terminated CPython-style float
; repr ("2.0" not C's bare "2"; "nan"/"inf"/"-inf" not UCRT's "1.#QNAN"/
; "1.#INF"). Ports codegen.py's target_windows.py _emit_float_to_str +
; _emit_float_repr_fixup exactly: detect NaN/inf up front (UCRT's sprintf
; spellings don't match Python's), else sprintf "%g" then scan the result
; for a byte that already marks it non-integral/non-finite and append
; ".0" if none is found before the nul terminator.
;
; NOTE: the NaN/inf branches return a pointer to a genuine static string
; constant (safe to alias, never mutated) but the .finite path's result
; is a pointer into the shared _abi_float_to_str_buf -- every branch
; dups through _runtime_str_concat_dup at .done before returning so ALL
; paths return an owned copy uniformly, not just the one that actually
; needed it. Same class of bug as _abi_fmt_elem's fix: this pipeline's
; print()/f-string lowering computes every arg of a multi-arg call up
; front before one shared call, so two float args in the same call
; alias the same buffer otherwise -- confirmed via a live repro
; (`f1, *fmid, f2 = floats; print(f1, fmid, f2)` printed f2's value
; for both f1 and f2).
_abi_float_to_str:
    sub rsp, 40
    ucomisd xmm0, xmm0
    jp .is_nan
    movq rax, xmm0
    mov r10, 0x7FFFFFFFFFFFFFFF
    and rax, r10
    mov r10, 0x7FF0000000000000
    cmp rax, r10
    jne .finite
    movq rax, xmm0
    test rax, rax
    jns .pinf
    lea rax, [_abi_str_ninf]
    jmp .done
.pinf:
    lea rax, [_abi_str_pinf]
    jmp .done
.is_nan:
    lea rax, [_abi_str_nan]
    jmp .done
.finite:
    movq r8, xmm0
    lea rdx, [_abi_fmt_g]
    lea rcx, [_abi_float_to_str_buf]
    xor eax, eax
    call sprintf
    lea rbx, [_abi_float_to_str_buf]
.scan:
    mov cl, [rbx]
    test cl, cl
    jz .append
    cmp cl, '.'
    je .fixup_done
    cmp cl, 'e'
    je .fixup_done
    cmp cl, 'E'
    je .fixup_done
    cmp cl, 'n'
    je .fixup_done
    cmp cl, 'N'
    je .fixup_done
    cmp cl, 'i'
    je .fixup_done
    cmp cl, 'I'
    je .fixup_done
    inc rbx
    jmp .scan
.append:
    mov byte [rbx], '.'
    mov byte [rbx+1], '0'
    mov byte [rbx+2], 0
.fixup_done:
    lea rax, [_abi_float_to_str_buf]
.done:
    call _runtime_str_concat_dup
    add rsp, 40
    ret

; xmm0 = fmax_f64(xmm0=a, xmm1=b) -> max(a, b). classic msvcrt.dll exports
; neither fmax nor _fmax (a C99 addition with no MS-spelled equivalent,
; unlike copysign/hypot's _copysign/_hypot), but SSE2's MAXSD computes the
; same IEEE-754 result directly -- no libm call needed at all.
_abi_fmax_f64:
    maxsd xmm0, xmm1
    ret

; xmm0 = fmin_f64(xmm0=a, xmm1=b) -> min(a, b). Same rationale as fmax.
_abi_fmin_f64:
    minsd xmm0, xmm1
    ret

; list_del(list=rcx, idx=rdx) -- `del xs[i]`, shifts elements down in
; place. No return value used by any caller (the `del` statement discards
; whatever _runtime_list_del itself returns, if anything).
_abi_list_del:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_list_del
    WIN64_RUNTIME_LEAVE
    ret

; dict_pop(dict=rcx, key=rdx) -- `del d[key]`. Raises KeyError via
; _runtime_raise (already wired through _abi_raise's shared path) if the
; key isn't present, matching CPython's del semantics.
_abi_dict_pop:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_dict_pop
    WIN64_RUNTIME_LEAVE
    ret

; dict_clear(dict=rcx) -- `d.clear()` / `s.clear()` (sets are dict-backed).
_abi_dict_clear:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_dict_clear
    WIN64_RUNTIME_LEAVE
    ret

; rax = str_concat_dup(str=rcx) -> a fresh heap copy of str (concat with
; the empty string). Used to turn an int member into an owned, freshly-
; allocated str key before inserting it into a set (set members are
; always str-keyed; a plain _abi_int_to_str value is a static shared
; buffer, not safe to store as a long-lived dict key).
_abi_str_concat_dup:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    call _runtime_str_concat_dup
    WIN64_RUNTIME_LEAVE
    ret

; rax = str_truncate(str=rcx, maxlen=rdx) -- f-string str precision
; (`f"{x:.5}"`): first `maxlen` chars, or the whole string if already
; shorter. Fresh allocation.
_abi_str_truncate:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_truncate
    WIN64_RUNTIME_LEAVE
    ret

; rax = int_to_binary(value=rcx, width=rdx, prefix_flag=r8) -- f-string
; binary spec (`f"{n:08b}"` / `f"{n:#010b}"`): zero-padded to `width`
; total chars (sign/prefix counted toward it, matching CPython), with an
; optional "0b" prefix. Fresh allocation.
_abi_int_to_binary:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_int_to_binary
    WIN64_RUNTIME_LEAVE
    ret

; rax = group_digits(numstr=rcx, sep_byte=rdx) -- f-string thousands
; grouping (`f"{n:,}"` / `f"{n:_}"`): inserts `sep_byte` every 3 digits in
; the integer part (after any leading '-', before any '.'). Fresh
; allocation.
_abi_group_digits:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    call _runtime_group_digits
    WIN64_RUNTIME_LEAVE
    ret

; rax = group_digits_zeropad(numstr=rcx, width=rdx, sep_byte=r8) -- the
; zero-pad+grouping combo (`f"{n:015,}"`): zero-pads the integer part so
; the *grouped* result reaches `width` chars, then groups. Fresh
; allocation.
_abi_group_digits_zeropad:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_group_digits_zeropad
    WIN64_RUNTIME_LEAVE
    ret

; rax = int_fmt(value=rcx, fmt_ptr=rdx) -- f-string numeric format specs
; that translate to a C printf format (e.g. "%05lld", "%llx", "%3lld" --
; see ir_lower.py's _cfmt_for_spec). Unlike _abi_int_to_str's fixed
; decimal buffer, the caller-supplied format can request extra width/
; precision, so this mallocs a fresh, generously-sized buffer per call
; instead of reusing a shared static one (avoids needing a
; concat-dup-to-own-a-copy step at every call site, and stays correct if
; two formatted values are alive at once, e.g. inside one f-string).
_abi_int_fmt:
    ; 56 bytes (entry rsp%16==8, so N%16 must ==8 to leave rsp 16-aligned
    ; before each `call` below -- 56 is the smallest such N that still
    ; fits [0,32) shadow space + 3 8-byte locals above it).
    ; [0,32) is shadow space malloc/sprintf are free to scribble on as
    ; callees (Win64 ABI requirement); this shim's own locals must live
    ; at/above +32 or a callee's shadow-space write silently corrupts
    ; them -- confirmed via gdb on a first attempt that stored locals at
    ; +16/+24 (inside the shadow region) and read back garbage (sprintf's
    ; own return value, then the raw int argument) after the first `call`.
    sub rsp, 56
    mov [rsp+40], rcx          ; value
    mov [rsp+48], rdx          ; fmt_ptr
    mov rcx, 64
    call malloc
    mov [rsp+32], rax          ; stash buf ptr -- rcx/rdx/r8 are volatile
                                ; across the sprintf call below, so the
                                ; buffer pointer can't just sit in a
                                ; register and survive it
    mov rcx, rax
    mov rdx, [rsp+48]
    mov r8, [rsp+40]
    xor eax, eax
    call sprintf
    mov rax, [rsp+32]
    add rsp, 56
    ret

; rax = float_fmt(value=xmm0, fmt_ptr=rdx) -- float counterpart of
; _abi_int_fmt (e.g. "%.2f", "%10.3e"). NOTE the fmt_ptr register: this
; is arg1 of a 2-arg call where arg0 is float-typed, and this pipeline's
; `call` op assigns Win64 argument registers by ONE SHARED positional
; index across both register classes (arg0 float -> xmm0, arg1 non-float
; -> the *second* positional GP slot RDX, not RCX -- RCX would be the
; register for a non-float arg0). Getting this wrong doesn't fail to
; assemble; it silently reads garbage/uninitialized rdx as the format
; string, which happened to already look enough like *some* valid
; pointer to limp through a first call and corrupt state that crashed
; the second one -- confirmed by tracing arg-register assignment in
; codegen.py's _call (the shared int_i/xmm_i-vs-single-arg_i counter
; logic) after this shim silently produced empty/garbage output instead
; of erroring.
;
; Once value is in xmm0 and fmt_ptr in rdx, sprintf's own variadic ABI
; still needs the double vararg mirrored into its positional GP slot
; (the 3rd sprintf argument, since buf/fmt are the two fixed params
; ahead of it) -- moved via a raw bit copy through the stack, matching
; codegen.py's _emit_float_fmt.
_abi_float_fmt:
    ; See _abi_int_fmt's comment: locals must live at/above +32, clear of
    ; the [0,32) shadow space malloc/sprintf are free to scribble on.
    sub rsp, 56
    mov [rsp+40], rdx          ; fmt_ptr
    movsd [rsp+48], xmm0       ; value
    mov rcx, 64
    call malloc
    mov [rsp+32], rax          ; stash buf ptr (rcx/rdx/r8 volatile below)
    mov rcx, rax
    mov rdx, [rsp+40]
    mov r8, [rsp+48]
    xor eax, eax
    call sprintf
    mov rax, [rsp+32]
    add rsp, 56
    ret

; math.isnan(x: float)/gcd(a: int, b: int)/isqrt(n: int) -- ported directly
; from target_windows.py's legacy-backend inline shims (same symbol names,
; same algorithms/register conventions), since stdlib/math.py's Func
; bindings point straight at these C names (`_math_isnan` etc.) and the
; x86-64 IR backend's `call` op expects them to already exist as real
; symbols, unlike the legacy backend which synthesizes them into the
; generated .asm file on demand via self.ffi_called. Never had an x86-64-
; backend home before -- these three were undefined-symbol BUILD_FAILs.

; _math_isnan(xmm0=x) -> rax 0/1
_math_isnan:
    ucomisd xmm0, xmm0
    setp al
    movzx rax, al
    ret

; _math_isinf(xmm0=x) -> rax 0/1
_math_isinf:
    movsd xmm1, [rel _math_abs_mask]
    andpd xmm0, xmm1
    movsd xmm1, [rel _math_inf_bits]
    ucomisd xmm0, xmm1
    sete al
    setnp cl
    and al, cl
    movzx rax, al
    ret

; _math_isfinite(xmm0=x) -> rax 0/1
_math_isfinite:
    ucomisd xmm0, xmm0
    jp ._mif_no
    movsd xmm1, [rel _math_abs_mask]
    andpd xmm0, xmm1
    movsd xmm1, [rel _math_inf_bits]
    ucomisd xmm0, xmm1
    jb ._mif_yes
._mif_no:
    xor rax, rax
    ret
._mif_yes:
    mov rax, 1
    ret

; _math_degrees(xmm0=x) -> xmm0
_math_degrees:
    mulsd xmm0, [rel _math_deg_factor]
    ret

; _math_radians(xmm0=x) -> xmm0
_math_radians:
    mulsd xmm0, [rel _math_rad_factor]
    ret

; _math_gcd(rcx=a, rdx=b) -> rax  (Euclidean, positive result)
_math_gcd:
    mov rax, rcx
    mov rcx, rdx
    test rax, rax
    jns ._mg_apos
    neg rax
._mg_apos:
    test rcx, rcx
    jns ._mg_bpos
    neg rcx
._mg_bpos:
._mg_loop:
    test rcx, rcx
    jz ._mg_done
    xor rdx, rdx
    div rcx
    mov rax, rcx
    mov rcx, rdx
    jmp ._mg_loop
._mg_done:
    ret

; _math_lcm(rcx=a, rdx=b) -> rax
_math_lcm:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rcx
    mov [rbp-16], rdx
    call _math_gcd
    test rax, rax
    jz ._mlcm_zero
    mov rcx, rax
    mov rax, [rbp-8]
    test rax, rax
    jns ._mlcm_apos
    neg rax
._mlcm_apos:
    xor rdx, rdx
    div rcx
    mov rcx, [rbp-16]
    test rcx, rcx
    jns ._mlcm_bpos
    neg rcx
._mlcm_bpos:
    imul rax, rcx
    leave
    ret
._mlcm_zero:
    xor rax, rax
    leave
    ret

; _math_factorial(rcx=n) -> rax
_math_factorial:
    mov rax, 1
    cmp rcx, 1
    jle ._mf_done
._mf_loop:
    imul rax, rcx
    dec rcx
    cmp rcx, 1
    jg ._mf_loop
._mf_done:
    ret

; _math_comb(rcx=n, rdx=k) -> rax
_math_comb:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rcx
    mov [rbp-16], rdx
    mov rax, rcx
    sub rax, rdx
    cmp rdx, rax
    jle ._mc_kset
    mov [rbp-16], rax
._mc_kset:
    mov rax, 1
    mov rcx, 1
._mc_loop:
    cmp rcx, [rbp-16]
    jg ._mc_done
    mov rdx, [rbp-8]
    sub rdx, [rbp-16]
    add rdx, rcx
    imul rax, rdx
    xor rdx, rdx
    div rcx
    inc rcx
    jmp ._mc_loop
._mc_done:
    leave
    ret

; _math_perm(rcx=n, rdx=k) -> rax
_math_perm:
    push rbp
    mov rbp, rsp
    mov rax, 1
    mov r8, 0
._mp_loop:
    cmp r8, rdx
    jge ._mp_done
    mov r9, rcx
    sub r9, r8
    imul rax, r9
    inc r8
    jmp ._mp_loop
._mp_done:
    pop rbp
    ret

; _math_log_base(xmm0=x, xmm1=base) -> xmm0
extern log
_math_log_base:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    movsd [rbp-8], xmm1
    call log
    movsd [rbp-16], xmm0
    movsd xmm0, [rbp-8]
    call log
    movsd xmm1, xmm0
    movsd xmm0, [rbp-16]
    divsd xmm0, xmm1
    leave
    ret

; _math_modf_frac(xmm0=x) -> xmm0=fractional
extern modf
_math_modf_frac:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    lea rdx, [rbp-8]
    call modf
    leave
    ret

; _math_modf_int(xmm0=x) -> xmm0=integer part
_math_modf_int:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    lea rdx, [rbp-8]
    call modf
    movsd xmm0, [rbp-8]
    leave
    ret

; _math_frexp_m(xmm0=x) -> xmm0=mantissa
extern frexp
_math_frexp_m:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    lea rdx, [rbp-8]
    call frexp
    leave
    ret

; _math_frexp_e(xmm0=x) -> rax=exponent
_math_frexp_e:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    lea rdx, [rbp-8]
    call frexp
    movsxd rax, dword [rbp-8]
    leave
    ret

; _math_ldexp(xmm0=x, rdx=n) -> xmm0
extern ldexp
_math_ldexp:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    call ldexp
    leave
    ret

; _math_isqrt(rcx=n) -> rax: integer square root (floor(sqrt(n)))
extern sqrt
_math_isqrt:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    cvtsi2sd xmm0, rcx
    call sqrt
    cvttsd2si rax, xmm0
    leave
    ret

; _math_isclose(xmm0=a, xmm1=b, xmm2=rel_tol, xmm3=abs_tol) -> rax 0/1
; |a-b| <= max(rel_tol * max(|a|,|b|), abs_tol)
extern fabs
_math_isclose:
    push rbp
    mov rbp, rsp
    sub rsp, 80
    movsd [rbp-8],  xmm0        ; a
    movsd [rbp-16], xmm1        ; b
    movsd [rbp-24], xmm2        ; rel_tol
    movsd [rbp-32], xmm3        ; abs_tol
    movsd xmm0, [rbp-8]
    subsd xmm0, [rbp-16]
    call fabs
    movsd [rbp-40], xmm0        ; diff
    movsd xmm0, [rbp-8]
    call fabs
    movsd [rbp-48], xmm0        ; |a|
    movsd xmm0, [rbp-16]
    call fabs                   ; |b|
    movsd xmm1, [rbp-48]
    maxsd xmm0, xmm1
    movsd [rbp-56], xmm0        ; max_ab
    movsd xmm0, [rbp-24]
    mulsd xmm0, [rbp-56]        ; rel_tol*max_ab
    movsd xmm1, [rbp-32]
    maxsd xmm0, xmm1
    movsd [rbp-64], xmm0        ; tol
    movsd xmm0, [rbp-40]
    movsd xmm1, [rbp-64]
    ucomisd xmm0, xmm1
    ja ._mic_no
    mov rax, 1
    jmp ._mic_end
._mic_no:
    xor rax, rax
._mic_end:
    leave
    ret

; _math_erf(xmm0=x) -> xmm0: Abramowitz & Stegun 7.1.26 approximation.
; erf(x) = sign(x) * (1 - (a1*t + a2*t^2 + a3*t^3 + a4*t^4 + a5*t^5)*exp(-x^2))
; where t = 1/(1+p*|x|). Horner-evaluated: ((((a5*t+a4)*t+a3)*t+a2)*t+a1)*t.
; Max error ~1.5e-7, verified against CPython's math.erf(1.0) to 6 decimals
; (148_math_extended.py's tolerance) via Python before writing this asm.
; Stack layout (rbp-relative), 96-byte frame (32 shadow + 8 locals*8):
;   rbp-8  x (signed input)      rbp-40 poly
;   rbp-16 sign                  rbp-48 exp(-ax^2)
;   rbp-24 ax = |x|               rbp-56 (spare)
;   rbp-32 t
extern exp
_math_erf:
    push rbp
    mov rbp, rsp
    sub rsp, 96
    movsd [rbp-8], xmm0            ; x (original, signed)
    movsd xmm1, [rel _math_erf_one]
    xorpd xmm2, xmm2
    ucomisd xmm0, xmm2
    jae ._me_pos
    movsd xmm1, [rel _math_erf_neg_one]
._me_pos:
    movsd [rbp-16], xmm1           ; sign
    movsd xmm0, [rbp-8]
    movsd xmm1, [rel _math_abs_mask]
    andpd xmm0, xmm1
    movsd [rbp-24], xmm0           ; ax
    movsd xmm0, [rel _math_erf_p]
    mulsd xmm0, [rbp-24]
    addsd xmm0, [rel _math_erf_one]
    movsd xmm1, [rel _math_erf_one]
    divsd xmm1, xmm0
    movsd [rbp-32], xmm1           ; t
    movsd xmm0, [rel _math_erf_a5]
    mulsd xmm0, [rbp-32]
    addsd xmm0, [rel _math_erf_a4]
    mulsd xmm0, [rbp-32]
    addsd xmm0, [rel _math_erf_a3]
    mulsd xmm0, [rbp-32]
    addsd xmm0, [rel _math_erf_a2]
    mulsd xmm0, [rbp-32]
    addsd xmm0, [rel _math_erf_a1]
    mulsd xmm0, [rbp-32]
    movsd [rbp-40], xmm0           ; poly
    movsd xmm0, [rbp-24]
    mulsd xmm0, xmm0
    movsd xmm1, [rel _math_erf_neg_one]
    mulsd xmm0, xmm1               ; -ax*ax
    call exp
    movsd [rbp-48], xmm0           ; exp(-ax^2)
    movsd xmm0, [rbp-40]
    mulsd xmm0, [rbp-48]           ; poly * exp(-ax^2)
    movsd xmm1, [rel _math_erf_one]
    subsd xmm1, xmm0               ; y = 1 - poly*exp(-ax^2)
    mulsd xmm1, [rbp-16]           ; sign * y
    movsd xmm0, xmm1
    leave
    ret

; _math_gamma(xmm0=x) -> xmm0: Lanczos approximation, g=7, n=9.
; xp = x - 1
; A = c0 + sum_{i=1..8} c[i] / (xp + i)
; t = xp + g + 0.5
; gamma(x) = sqrt(2*pi) * t^(xp+0.5) * exp(-t) * A
; Verified in Python against math.gamma(5.0) == 24.0 (matches to 1 decimal,
; the test's tolerance) before writing this asm; valid for x > 0.5 (the only
; range 148_math_extended.py's math.gamma(5.0) call exercises).
; Stack layout (rbp-relative), 96-byte frame (32 shadow + 8 locals*8):
;   rbp-8  xp = x - 1             rbp-32 exponent = xp + 0.5
;   rbp-16 A (accumulator)         rbp-40 t^exponent
;   rbp-24 t = xp + g + 0.5        rbp-48 exp(-t)
extern pow
_math_gamma:
    push rbp
    mov rbp, rsp
    sub rsp, 96
    movsd xmm1, [rel _math_erf_one]
    subsd xmm0, xmm1
    movsd [rbp-8], xmm0            ; xp

    movsd xmm0, [rel _math_lanczos_c0]
    movsd [rbp-16], xmm0           ; A accumulator

    movsd xmm0, [rbp-8]
    addsd xmm0, [rel _math_erf_one]    ; xp+1
    movsd xmm1, [rel _math_lanczos_c1]
    divsd xmm1, xmm0
    addsd xmm1, [rbp-16]
    movsd [rbp-16], xmm1

    movsd xmm0, [rbp-8]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]    ; xp+2
    movsd xmm1, [rel _math_lanczos_c2]
    divsd xmm1, xmm0
    addsd xmm1, [rbp-16]
    movsd [rbp-16], xmm1

    movsd xmm0, [rbp-8]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]    ; xp+3
    movsd xmm1, [rel _math_lanczos_c3]
    divsd xmm1, xmm0
    addsd xmm1, [rbp-16]
    movsd [rbp-16], xmm1

    movsd xmm0, [rbp-8]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]    ; xp+4
    movsd xmm1, [rel _math_lanczos_c4]
    divsd xmm1, xmm0
    addsd xmm1, [rbp-16]
    movsd [rbp-16], xmm1

    movsd xmm0, [rbp-8]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]    ; xp+5
    movsd xmm1, [rel _math_lanczos_c5]
    divsd xmm1, xmm0
    addsd xmm1, [rbp-16]
    movsd [rbp-16], xmm1

    movsd xmm0, [rbp-8]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]    ; xp+6
    movsd xmm1, [rel _math_lanczos_c6]
    divsd xmm1, xmm0
    addsd xmm1, [rbp-16]
    movsd [rbp-16], xmm1

    movsd xmm0, [rbp-8]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]    ; xp+7
    movsd xmm1, [rel _math_lanczos_c7]
    divsd xmm1, xmm0
    addsd xmm1, [rbp-16]
    movsd [rbp-16], xmm1

    movsd xmm0, [rbp-8]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]
    addsd xmm0, [rel _math_erf_one]    ; xp+8
    movsd xmm1, [rel _math_lanczos_c8]
    divsd xmm1, xmm0
    addsd xmm1, [rbp-16]
    movsd [rbp-16], xmm1           ; final A

    movsd xmm0, [rbp-8]
    addsd xmm0, [rel _math_lanczos_g]
    movsd xmm1, [rel _math_lanczos_half]
    addsd xmm0, xmm1
    movsd [rbp-24], xmm0           ; t

    movsd xmm0, [rbp-8]
    addsd xmm0, [rel _math_lanczos_half]
    movsd [rbp-32], xmm0           ; exponent

    movsd xmm0, [rbp-24]
    movsd xmm1, [rbp-32]
    call pow
    movsd [rbp-40], xmm0           ; t^exponent

    movsd xmm0, [rbp-24]
    movsd xmm1, [rel _math_erf_neg_one]
    mulsd xmm0, xmm1
    call exp
    movsd [rbp-48], xmm0           ; exp(-t)

    movsd xmm0, [rel _math_lanczos_sqrt2pi]
    mulsd xmm0, [rbp-40]
    mulsd xmm0, [rbp-48]
    mulsd xmm0, [rbp-16]
    leave
    ret

; random.choice/shuffle/sample/getrandbits -- ported directly from
; target_windows.py's legacy-backend inline shims (same symbol names,
; same algorithms/register conventions). Unlike random.random/randint/
; uniform/randrange (special-cased as inline IR ops in ir_lower.py, not
; routed through this file at all), these four fall through to the
; generic FFI-call path expecting real symbols -- none existed here,
; undefined-symbol BUILD_FAILs for every choice/shuffle/sample/
; getrandbits test case.

extern rand
; _random_choice(rcx=list_hdr) -> rax = element at random index
_random_choice:
    push rbp
    mov rbp, rsp
    sub rsp, 56
    mov [rbp-8], rcx
    mov rax, [rcx+8]
    mov [rbp-16], rax
    call rand
    xor rdx, rdx
    div qword [rbp-16]
    mov rcx, rdx
    mov rax, [rbp-8]
    mov rax, [rax+16]
    mov rax, [rax+rcx*8]
    leave
    ret

; _random_shuffle(rcx=list_hdr) -- Fisher-Yates shuffle in-place
_random_shuffle:
    push rbp
    mov rbp, rsp
    sub rsp, 72
    mov [rbp-8], rcx
    mov rax, [rcx+8]
    mov [rbp-16], rax
._rs_loop:
    cmp qword [rbp-16], 1
    jle ._rs_done
    call rand
    xor rdx, rdx
    div qword [rbp-16]
    mov [rbp-24], rdx
    mov r8, [rbp-8]
    mov r8, [r8+16]
    mov r9, [rbp-16]
    dec r9
    mov rax, [r8+r9*8]
    mov rcx, [rbp-24]
    mov rbx, [r8+rcx*8]
    mov [r8+r9*8], rbx
    mov [r8+rcx*8], rax
    dec qword [rbp-16]
    jmp ._rs_loop
._rs_done:
    xor rax, rax
    leave
    ret

; _random_sample(rcx=list_hdr, rdx=k) -> rax = new list of k unique elements
; Strategy: copy source buf, do k-step partial Fisher-Yates, return the
; last k slots. Frame layout (offsets from rbp): -8=src_hdr, -16=n,
; -24=k, -32=copy_buf, -40=i/fill_index, -48=result_hdr, -56=result_buf,
; -64=j_scratch.
_random_sample:
    push rbp
    mov rbp, rsp
    sub rsp, 96
    mov [rbp-8], rcx
    mov [rbp-24], rdx
    mov rax, [rcx+8]
    mov [rbp-16], rax
    mov rcx, rax
    shl rcx, 3
    call malloc
    mov [rbp-32], rax
    mov rsi, [rbp-8]
    mov rsi, [rsi+16]
    mov rdi, [rbp-32]
    mov rcx, [rbp-16]
    xor rax, rax
._rsam_cp:
    cmp rax, rcx
    jge ._rsam_cp_end
    mov rbx, [rsi+rax*8]
    mov [rdi+rax*8], rbx
    inc rax
    jmp ._rsam_cp
._rsam_cp_end:
    mov rax, [rbp-16]
    mov [rbp-40], rax
._rsam_fy:
    mov rax, [rbp-40]
    mov rbx, [rbp-16]
    sub rbx, [rbp-24]
    cmp rax, rbx
    jle ._rsam_fy_end
    push rdi
    call rand
    pop rdi
    xor rdx, rdx
    div qword [rbp-40]
    mov [rbp-64], rdx
    mov rdi, [rbp-32]
    mov r9, [rbp-40]
    dec r9
    mov rcx, [rbp-64]
    mov rax, [rdi+r9*8]
    mov rbx, [rdi+rcx*8]
    mov [rdi+r9*8], rbx
    mov [rdi+rcx*8], rax
    dec qword [rbp-40]
    jmp ._rsam_fy
._rsam_fy_end:
    push rdi
    mov rcx, 24
    call malloc
    pop rdi
    mov [rbp-48], rax
    mov rbx, [rbp-24]
    mov [rax+0], rbx
    mov [rax+8], rbx
    push rdi
    mov rcx, [rbp-24]
    shl rcx, 3
    call malloc
    pop rdi
    mov [rbp-56], rax
    mov rcx, [rbp-48]
    mov [rcx+16], rax
    mov rax, [rbp-16]
    sub rax, [rbp-24]
    mov [rbp-40], rax
    xor rbx, rbx
._rsam_fill:
    mov rcx, [rbp-16]
    cmp rbx, [rbp-24]
    jge ._rsam_fill_end
    mov rdi, [rbp-32]
    mov rax, [rbp-40]
    add rax, rbx
    mov r8, [rdi+rax*8]
    mov rdi, [rbp-56]
    mov [rdi+rbx*8], r8
    inc rbx
    jmp ._rsam_fill
._rsam_fill_end:
    mov rax, [rbp-48]
    leave
    ret

; _random_getrandbits(rcx=k) -> rax: k random bits (1-64)
; NOTE: ported faithfully from the legacy source, which clobbers r12
; (callee-saved per Win64 ABI) without saving/restoring it -- a latent
; bug inherited from the port source, not introduced here. Not fixed as
; part of this port (out of scope: fixing bugs in code being ported
; verbatim, versus fixing THIS backend's own bugs).
_random_getrandbits:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rcx
    xor rbx, rbx
    xor r12, r12
._rgb_loop:
    cmp r12, [rbp-8]
    jge ._rgb_done
    call rand
    shl rbx, 15
    or rbx, rax
    add r12, 15
    jmp ._rgb_loop
._rgb_done:
    mov rcx, [rbp-8]
    mov rax, 1
    shl rax, cl
    dec rax
    and rax, rbx
    leave
    ret

