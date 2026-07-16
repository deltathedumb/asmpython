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
extern _runtime_list_insert
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
global _abi_list_insert
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

; rax = strtoll(str=rcx, NULL, 10)
_abi_str_to_int:
    sub rsp, 40
    xor rdx, rdx
    mov r8, 10
    call strtoll
    add rsp, 40
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
; list_insert(list=rcx, index=rdx, value=r8) -> void
_abi_list_insert:
    WIN64_RUNTIME_ENTER
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_list_insert
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

