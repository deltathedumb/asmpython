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
extern _runtime_objalloc
extern _runtime_gc_is_object
extern _tb_depth
extern _tb_exe
extern _tb_frames
extern _runtime_objfree
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
; _scprintf returns the length a printf-family conversion WOULD produce,
; without writing it anywhere -- the size probe _abi_int_fmt/_abi_float_fmt
; need. C99 snprintf(NULL, 0, ...) would do the same job but msvcrt.dll does
; not export it (only the pre-C99 _snprintf, which reports truncation as -1
; rather than the required length); confirmed against the live system DLL via
; ctypes.WinDLL('msvcrt.dll'), the same way the rest of this runtime's msvcrt
; dependencies were.
extern _scprintf
extern printf
extern sprintf
extern putchar
extern strtoll
extern strtod

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
global _abi_new_box
global _abi_gc_is_object
global _abi_tb_push
global _abi_tb_pop
global _abi_new_list
global __chkstk
global _abi_list_append
global _abi_list_pop
global _abi_list_slice
global _abi_list_slice_step
global _abi_str_slice_step
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
global _abi_round_ndigits

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
    mov rdx, 2                   ; kind: dict/instance
    call _runtime_objalloc       ; tracked object (header behind ptr)
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

; rax = new tagged BOX carrying a scalar's runtime kind.
;
; A box is a 24-byte heap cell with a distinctive magic word at offset 0
; that NO other runtime object type ever has there (a list's word-0 is its
; capacity, a dict/instance's is 8, a string's is its length/first bytes --
; none can equal BOX_MAGIC, a large odd sentinel). That single word makes
; "is this pointer a boxed scalar cell?" answerable with ONE fault-safe load
; at offset 0 (every heap object is >= 8 bytes, so the load never reads past
; an allocation), instead of the old dict-shaped probe that dereferenced a
; raw string/list AS A DICT and faulted. Layout:
;   [BOX_MAGIC @0][tag @8][payload @16]
; tag is a BUILTIN_TYPE_IDS id (int/float/bool/str/None); payload is the raw
; scalar bits (float bit-pattern for float, string pointer for str).
;
;   _abi_new_box(tag=rcx, payload=rdx) -> rax
; _abi_gc_is_object(candidate) -> 1 if `candidate` is a payload pointer the
; object registry handed out, else 0.
;
; Exists because the _runtime_gc_* helpers use a private convention -- argument
; and result both in RAX -- while compiled code emits ordinary Win64 calls with
; the first argument in RCX. Calling _runtime_gc_is_object directly from
; ir_lower passed the candidate in rcx and let the helper test whatever rax
; happened to hold, which read as "registered" or not at random: a boxed dict
; value printed as its raw pointer (1794096 instead of 1078200).
;
; This is the whole reason the _abi_* layer exists; the fix is to go through it
; rather than to change the helper's convention, which every other GC caller
; already relies on.
; ---- Traceback frame stack (--embed-tracebacks) --------------------------
; _abi_tb_push(name, file, line_slot, exe)   Win64: rcx, rdx, r8
;
; Records one frame: name, file, the address of the caller's own line slot, and
; the address this frame was entered from (our return address, still at [rsp]
; because nothing has been pushed yet).
;
; Deliberately frameless and call-free -- only volatile registers (rax, r10,
; r11) are touched, so this is a handful of stores on function entry rather
; than a real call sequence. It runs on EVERY call in an --embed-tracebacks
; build, so the cost matters.
;
; Overflow (1024 frames) silently stops recording rather than growing: deep
; recursion would otherwise turn a traceback into a memory problem, and
; CPython truncates too.
_abi_tb_push:
    mov r10, [rel _tb_depth]
    cmp r10, 1024
    jge .tbp_full
    mov r11, r10
    shl r11, 5                      ; * 32 bytes per frame
    lea rax, [rel _tb_frames]
    add rax, r11
    mov [rax+0], rcx                ; name
    mov [rax+8], rdx                ; file
    mov [rax+16], r8                ; line-slot address
    mov r11, [rsp]                  ; entry index (our return address)
    mov [rax+24], r11
    inc r10
    mov [rel _tb_depth], r10
    ; The executable name is program-wide, not per-frame, so it is stashed once
    ; here rather than stored in every frame. Rewriting the same pointer on each
    ; push is cheaper than a compare-and-branch and there is no program-start
    ; hook to set it in: only the legacy targets emit an entry prologue.
    mov [rel _tb_exe], r9
.tbp_full:
    ret

; _abi_tb_pop() -- drop the innermost frame. Clamped at zero so an unbalanced
; pop (an exception unwinding past a push) cannot make the depth negative and
; send the printer walking backwards through memory.
_abi_tb_pop:
    mov rax, [rel _tb_depth]
    test rax, rax
    jle .tbo_done
    dec rax
    mov [rel _tb_depth], rax
.tbo_done:
    ret

_abi_gc_is_object:
    WIN64_RUNTIME_ENTER
    sub rsp, 32
    mov rax, rcx
    call _runtime_gc_is_object
    add rsp, 32
    WIN64_RUNTIME_LEAVE
    ret

_abi_new_box:
    WIN64_RUNTIME_ENTER
    sub rsp, 48
    mov [rsp+32], rcx           ; spill tag across malloc
    mov [rsp+40], rdx           ; spill payload across malloc
    mov rcx, 24                 ; box is 3 words
    xor rdx, rdx                ; kind: plain (payload scanned word-wise)
    call _runtime_objalloc      ; tracked object
    mov rcx, [rsp+32]
    mov rdx, [rsp+40]
    mov rbx, 0xB0BE11EDB0BE11ED ; BOX_MAGIC -- keep in sync with ir_lower.py
    mov qword [rax+0], rbx
    mov qword [rax+8], rcx      ; tag
    mov qword [rax+16], rdx     ; payload
    add rsp, 48
    WIN64_RUNTIME_LEAVE
    ret

; Win64 stack-probe routine the x86-64 backend emits for any function whose
; frame exceeds one page (4096 B): `mov rax, frame_bytes; call __chkstk; sub
; rsp, frame_bytes` (see _backends/x86_64/codegen.py). A large `sub rsp`
; can skip past the stack's guard page without touching it, so the OS never
; grows the committed stack and the first deep access faults; __chkstk walks
; DOWN the to-be-allocated range one page at a time, touching each so the
; guard page moves with it. Contract here: RAX = byte count to probe; must
; NOT modify RSP (the caller's own `sub rsp` does that) and must preserve
; every register including RAX. gcc's linker left this symbol undefined
; because w64devkit's libs don't export the MSVC-style __chkstk this backend
; targets -- provide it directly.
__chkstk:
    push rax
    push rcx
    mov rcx, rax                ; rcx = bytes remaining to probe
    ; The return address occupies [rsp+16] now (after the two pushes); probe
    ; relative to the caller's rsp, which is rsp+24 (2 pushes + return addr).
    lea rax, [rsp+24]           ; rax = caller's RSP (top of the new frame)
.probe_loop:
    cmp rcx, 4096
    jbe .last_page
    sub rax, 4096
    mov byte [rax], 0           ; touch the guard page
    sub rcx, 4096
    jmp .probe_loop
.last_page:
    sub rax, rcx
    mov byte [rax], 0           ; touch the final (partial) page
    pop rcx
    pop rax
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
    mov rdx, 1                   ; kind: list-shaped
    call _runtime_objalloc       ; tracked object
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
; Mutable copies of _abi_fmt_fixed/_abi_fmt_sci -- .rodata can't be
; patched in place, so _abi_float_to_str's precision-search loop edits
; the digit(s) here before each sprintf call.
_abi_fmt_fixed_buf: resb 8
_abi_fmt_sci_buf:   resb 8
; Scratch buffer strtod's round-trip check writes each candidate string's
; parsed value into, and a second buffer the search loop's sprintf calls
; write their candidate string into before it's known to be the winner
; (kept separate from _abi_float_to_str_buf so a losing candidate never
; clobbers a still-needed value mid-search).
_abi_float_search_buf: resb 40

; --------------------------------------------------------------------------
; dtoa scratch -- see the "Exact decimal conversion" block in .text.
;
; A SLOT is one exact non-negative decimal number, laid out as
;
;     [+0]  n      qword, count of stored digits (>= 1)
;     [+8]  f      qword, SIGNED count of fractional digits
;     [+16] d[]    bytes, one decimal digit each, LITTLE-endian (d[0] is
;                  the least significant)
;
; and denotes the exact rational  int(d) / 10**f.  f may be negative, which
; scales UP by 10**-f; that is what makes round(x, -2) fall out of the same
; code as round(x, 2).
;
; Capacity: the widest value any finite double produces is (4m-1) * 5**1076
; for the midpoint below the smallest normal, which is 769 digits. 1200
; leaves better than 50% headroom, and every writer additionally guards
; against running past it rather than trusting that arithmetic.
;
; These are static, not stack or heap, matching _abi_float_to_str's existing
; single-threaded static-buffer convention (the results are copied out via
; _runtime_str_concat_dup / malloc before returning, so a caller never holds
; a pointer into them).
; --------------------------------------------------------------------------
alignb 16
_dtoa_src: resb 16 + 1200      ; pristine expansion of |x| (repr re-reads it)
_dtoa_val: resb 16 + 1200      ; the value being rendered / rounded
_dtoa_tmp: resb 16 + 1200      ; expansion of a candidate double, for compare
_dtoa_repr_buf: resb 64        ; repr output (bounded: <=17 digits + exponent)

section .rodata
_abi_fmt_lld:    db "%lld", 0
_con_fmt_clear:  db 27, "[2J", 27, "[H", 0
_con_fmt_color:  db 27, "[%dm", 27, "[%dm", 0
_con_fmt_cursor: db 27, "[%d;%dH", 0
_con_fmt_s:      db "%s", 0
_abi_fmt_g:      db "%g", 0
; Runtime-built format strings for _abi_float_to_str's precision search
; (see that routine): "%.NNf" / "%.NNe", N patched in as one or two ASCII
; digit bytes before each sprintf call. Sized for the largest N (17) plus
; nul.
_abi_fmt_fixed:  db "%.17f", 0
_abi_fmt_sci:    db "%.17e", 0
_abi_str_nan:    db "nan", 0
_abi_str_pinf:   db "inf", 0
_abi_str_ninf:   db "-inf", 0
; Uppercase spellings for %F/%E/%G, matching C's own case rule (the
; conversion's case selects the case of inf/nan too, not just of the
; exponent marker). CPython's %-operator inherits this from C.
_abi_str_NAN:    db "NAN", 0
_abi_str_PINF:   db "INF", 0
_abi_str_NINF:   db "-INF", 0
_dtoa_zero_str:  db "0.0", 0
_dtoa_negzero_str: db "-0.0", 0
; 5**0 .. 5**12 -- the remainder step of the x5 chunking below.
align 8
_dtoa_pow5:
    dq 1, 5, 25, 125, 625, 3125, 15625, 78125, 390625, 1953125
    dq 9765625, 48828125, 244140625
; 10**0 .. 10**22 as doubles. Every one of these is EXACTLY representable
; (10**23 is the first that is not), which is what makes the seed guess in
; _dtoa_guess a single correctly-rounded operation in the common case.
_dtoa_pow10d:
    dq 1.0, 10.0, 100.0, 1000.0, 10000.0, 100000.0, 1000000.0
    dq 10000000.0, 100000000.0, 1000000000.0, 10000000000.0
    dq 100000000000.0, 1000000000000.0, 10000000000000.0
    dq 100000000000000.0, 1000000000000000.0, 10000000000000000.0
    dq 100000000000000000.0, 1000000000000000000.0
    dq 10000000000000000000.0, 100000000000000000000.0
    dq 1000000000000000000000.0, 10000000000000000000000.0
_dtoa_ten:   dq 10.0
_dtoa_tenth: dq 0.1
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
;
; This one is already exact and stays as-is: ROUNDSD rounds the double's
; own EXACT value, so the 0-decimal case has no representation error to
; get wrong. It is round(x, ndigits) -- where 10**n scaling introduces
; one -- that needs _abi_round_ndigits below.
_abi_round_f64:
    roundsd xmm0, xmm0, 0
    ret

; ==========================================================================
; Exact decimal conversion (dtoa)
; ==========================================================================
;
; WHY THIS EXISTS. Three separate defects share one root cause: msvcrt's
; printf cannot state a double's real value, and its strtod cannot read one
; back.
;
;   1. round(x, ndigits) scaled by 10**n, applied ROUNDSD, and unscaled.
;      2.55*10 is exactly 25.5 in binary, so ties-to-even gave 26 -> 2.6
;      where CPython gives 2.5. The error is in the SCALING, not the
;      rounding: 2.55 is really 2.5499999999999998..., so it must round
;      DOWN, and multiplying by 10 destroys exactly the digits that say so.
;   2. "%f" % 1e100 produced 108 characters of correct LENGTH and wrong
;      DIGITS -- msvcrt carries ~17 significant digits and zero-fills the
;      rest, where CPython prints the exact expansion.
;   3. float repr past 17 digits, same cause via the same printf.
;
; WHY sprintf + strtod IS NOT THE FIX (measured on this box, not assumed):
;
;      %.2f of 0.125   msvcrt "0.13"   CPython "0.12"
;      %.0f of 2.5     msvcrt "3"      CPython "2"
;
;   msvcrt rounds halfway cases AWAY FROM ZERO; CPython rounds HALF-TO-EVEN.
;   So a sprintf/strtod round-trip fixes 2.55 and breaks every exact tie,
;   and since msvcrt cannot emit the exact expansion the tie cannot even be
;   DETECTED from its output. msvcrt's strtod is separately unusable as the
;   decimal->double direction: it came back 1 ULP low on 461/200000 random
;   decimals and on 37/200000 repr()-shaped strings, and it truncated at
;   every one of 12 exact midpoints instead of rounding to even.
;
; WHAT MAKES THIS TRACTABLE. Every double is m * 2**e with m a 53-bit
; integer -- a dyadic rational -- so its decimal expansion TERMINATES and is
; computable with integer arithmetic alone:
;
;      e >= 0:  value = m * 2**e             (an integer)
;      e <  0:  value = m * 5**-e / 10**-e   (since 1/2**k == 5**k / 10**k)
;
; That is the whole idea. The rest is bookkeeping: a little-endian decimal
; digit array (a SLOT, laid out in .bss above), multiply-by-small with
; carry, half-to-even rounding at a digit position, and an exact comparison.
;
; Nothing here calls printf or strtod, so nothing here inherits their bugs,
; and the same code is correct on any target -- no new libc dependency was
; added for it.
;
; The algorithm was written and validated as a Python reference FIRST,
; against real CPython, before a line of this was written: repr over 100000
; random bit patterns, %.Nf/%.Ne/%.Ng over 8 precisions each, and
; round(x, n) over 19 values of n -- zero mismatches. This is a port of a
; checked specification, not a fresh derivation.

DTOA_CAP  equ 1200
DT_N      equ 0
DT_F      equ 8
DT_D      equ 16

; rax /= 10 -> quotient in rax, remainder in rdx. Clobbers r10, r11.
;
; The reciprocal form rather than DIV: this runs once per digit per
; multiply pass, so a 20-40 cycle hardware divide would dominate the whole
; conversion. 0xCCCCCCCCCCCCCCCD is ceil(2**67 / 10), and >>67 (the implicit
; >>64 of MUL's high half, then >>3) is exact across the entire u64 range.
%macro UDIV10 0
    mov r10, rax
    mov r11, 0xCCCCCCCCCCCCCCCD
    mul r11                    ; rdx:rax = rax * magic
    shr rdx, 3                 ; rdx = rax / 10
    mov rax, rdx
    lea rdx, [rax+rax*4]
    add rdx, rdx               ; q * 10
    sub r10, rdx               ; remainder
    mov rdx, r10
%endmacro

; %1 = dest reg <- the digit of slot %2 at decimal position %3 (the 10**%3
; place), or 0 if that position is outside the stored digits.
;
; Reading out-of-range positions as zero is what lets every consumer work
; in absolute decimal positions and ignore where the stored window happens
; to sit -- padding, leading zeros and trailing zeros all fall out of it.
;
; The index scratch is R11, not RAX, and %1 must not be R11. An earlier
; version scratched in RAX, which silently broke the one caller that also
; wanted RAX as the destination: the out-of-range path fell through with
; the INDEX still in the destination instead of a zero, so "%e" % 0.0
; printed "0.a`_^]\e+00" (the index 1073 masked to a byte, plus '0').
%macro DT_DIGIT 3
    xor %1, %1
    mov r11, [%2+DT_F]
    add r11, %3                ; index = pos + f
    js %%zero
    cmp r11, [%2+DT_N]
    jge %%zero
    movzx %1, byte [%2+r11+DT_D]
%%zero:
%endmacro

; _dtoa_set_u64(rcx=slot, rdx=value) -- slot := value, frac := 0.
_dtoa_set_u64:
    mov qword [rcx+DT_F], 0
    xor r8, r8
    mov rax, rdx
    test rax, rax
    jnz .loop
    mov byte [rcx+DT_D], 0
    mov qword [rcx+DT_N], 1
    ret
.loop:
    test rax, rax
    jz .done
    UDIV10
    mov [rcx+r8+DT_D], dl
    inc r8
    jmp .loop
.done:
    mov [rcx+DT_N], r8
    ret

; _dtoa_mul_u32(rcx=slot, rdx=multiplier) -- slot *= multiplier.
;
; The multiplier is bounded by 2**30 so that digit*mult + carry stays well
; inside 64 bits: the carry settles below the multiplier, so the running
; product never exceeds 10*mult (~1.2e10).
_dtoa_mul_u32:
    push rbx
    push rsi
    mov rbx, rcx               ; slot
    mov rsi, rdx               ; multiplier
    mov r9, [rbx+DT_N]         ; original digit count
    xor rcx, rcx               ; i
    xor r8, r8                 ; carry
.loop:
    cmp rcx, r9
    jae .flush
    movzx rax, byte [rbx+rcx+DT_D]
    imul rax, rsi
    add rax, r8
    UDIV10
    mov [rbx+rcx+DT_D], dl
    mov r8, rax                ; carry = quotient
    inc rcx
    jmp .loop
.flush:
    test r8, r8
    jz .done
    cmp rcx, DTOA_CAP
    jae .done                  ; capacity guard: unreachable for any finite
                               ; double (769 digits worst case vs 1200), but
                               ; clamping beats silently writing past the slot
    mov rax, r8
    UDIV10
    mov [rbx+rcx+DT_D], dl
    mov r8, rax
    inc rcx
    jmp .flush
.done:
    mov [rbx+DT_N], rcx
    pop rsi
    pop rbx
    ret

; _dtoa_from_mant(rcx=slot, rdx=m, r8=e) -- slot := the EXACT decimal
; expansion of m * 2**e.
;
; Chunked rather than one factor at a time: x2**30 and x5**13 are the
; largest powers that keep the per-digit product inside the UDIV10 bound,
; and they cut the pass count by 30x/13x. That matters -- the subnormal
; worst case is e = -1074, which is 83 chunked passes instead of 1074.
_dtoa_from_mant:
    push rbx
    push rsi
    push rdi
    sub rsp, 32
    mov rbx, rcx               ; slot
    mov rsi, r8                ; e
    mov rcx, rbx               ; (rdx still holds m)
    call _dtoa_set_u64
    test rsi, rsi
    jz .done
    js .neg
.pos:                          ; e > 0: multiply by 2**e, frac stays 0
    cmp rsi, 30
    jl .pos_tail
    mov rcx, rbx
    mov rdx, 1073741824        ; 2**30
    call _dtoa_mul_u32
    sub rsi, 30
    jmp .pos
.pos_tail:
    test rsi, rsi
    jz .done
    mov rdx, 1
    mov rcx, rsi
    shl rdx, cl                ; 2**e for the leftover e < 30
    mov rcx, rbx
    call _dtoa_mul_u32
    jmp .done
.neg:                          ; e < 0: multiply by 5**k and set frac = k,
    neg rsi                    ; because m / 2**k == m * 5**k / 10**k
    mov [rbx+DT_F], rsi
    mov rdi, rsi
.nloop:
    cmp rdi, 13
    jl .ntail
    mov rcx, rbx
    mov rdx, 1220703125        ; 5**13
    call _dtoa_mul_u32
    sub rdi, 13
    jmp .nloop
.ntail:
    test rdi, rdi
    jz .done
    lea rax, [_dtoa_pow5]
    mov rdx, [rax+rdi*8]
    mov rcx, rbx
    call _dtoa_mul_u32
.done:
    add rsp, 32
    pop rdi
    pop rsi
    pop rbx
    ret

; _dtoa_expand_bits(rcx=slot, rdx=bits) -- slot := the exact expansion of
; the finite, non-negative double with this bit pattern.
_dtoa_expand_bits:
    push rbx
    push rsi
    push rdi
    sub rsp, 32
    mov rbx, rcx
    mov rsi, rdx
    mov rax, rsi
    shr rax, 52
    and rax, 0x7FF             ; biased exponent
    mov rdi, rsi
    mov r10, 0x000FFFFFFFFFFFFF
    and rdi, r10               ; mantissa field
    test rax, rax
    jnz .norm
    mov r8, -1074              ; subnormal (and zero): no implicit bit
    jmp .go
.norm:
    mov r10, 1
    shl r10, 52
    or rdi, r10                ; restore the implicit leading 1
    lea r8, [rax-1075]         ; unbias, and account for the 52-bit shift
.go:
    mov rcx, rbx
    mov rdx, rdi
    call _dtoa_from_mant
    add rsp, 32
    pop rdi
    pop rsi
    pop rbx
    ret

; _dtoa_round_at(rcx=slot, rdx=p) -- round the slot to p fractional digits,
; HALF-TO-EVEN, in place. p may be negative (round to tens, hundreds, ...).
;
; When f <= p the value already has no digits past position p and is
; returned untouched -- that case is not just an optimisation, it is what
; makes round(1e100, 5) and round(1234.5, 2) exact identities rather than
; round-trips through a conversion.
;
; The tie test is decidable here precisely because the expansion is exact:
; "exactly half" means the first dropped digit is 5 AND every digit below it
; is 0. printf cannot answer that question, which is the whole reason this
; file no longer asks it.
_dtoa_round_at:
    push rbx
    push rsi
    push rdi
    push r12
    mov rbx, rcx               ; slot
    mov rsi, rdx               ; p
    mov rax, [rbx+DT_F]
    cmp rax, rsi
    jle .done                  ; f <= p: already exact at this position
    mov rdi, rax
    sub rdi, rsi               ; t = f - p, digits to drop (>= 1)
    mov r12, [rbx+DT_N]        ; n
    ; hd = the highest dropped digit = d[t-1], or 0 if that is past the end
    lea rax, [rdi-1]
    xor r8, r8
    cmp rax, r12
    jae .no_hd
    movzx r8, byte [rbx+rax+DT_D]
.no_hd:
    ; rest = is any digit BELOW d[t-1] nonzero?
    xor r9, r9
    lea rax, [rdi-1]
    cmp rax, r12
    jbe .rest_ok
    mov rax, r12               ; clamp the scan to the stored digits
.rest_ok:
    xor rcx, rcx
.rest_loop:
    cmp rcx, rax
    jae .rest_done
    cmp byte [rbx+rcx+DT_D], 0
    jne .rest_yes
    inc rcx
    jmp .rest_loop
.rest_yes:
    mov r9, 1
.rest_done:
    ; drop the low t digits
    cmp rdi, r12
    jb .shift
    mov byte [rbx+DT_D], 0     ; everything dropped -> 0
    mov qword [rbx+DT_N], 1
    jmp .shifted
.shift:
    mov rdx, r12
    sub rdx, rdi               ; new n
    xor rcx, rcx
.shift_loop:
    cmp rcx, rdx
    jae .shift_end
    lea rax, [rcx+rdi]
    movzx r10, byte [rbx+rax+DT_D]
    mov [rbx+rcx+DT_D], r10b
    inc rcx
    jmp .shift_loop
.shift_end:
    mov [rbx+DT_N], rdx
.shifted:
    mov [rbx+DT_F], rsi        ; f = p
    cmp r8, 5
    ja .up                     ; > half: up
    jb .trim                   ; < half: down
    test r9, r9
    jnz .up                    ; > half by the tail: up
    movzx rax, byte [rbx+DT_D]
    test rax, 1
    jz .trim                   ; exactly half and already even: stay
.up:
    xor rcx, rcx
    mov rdx, [rbx+DT_N]
.up_loop:
    cmp rcx, rdx
    jae .up_grow
    movzx rax, byte [rbx+rcx+DT_D]
    inc rax
    cmp rax, 10
    jae .up_carry
    mov [rbx+rcx+DT_D], al
    jmp .trim
.up_carry:
    mov byte [rbx+rcx+DT_D], 0
    inc rcx
    jmp .up_loop
.up_grow:
    cmp rcx, DTOA_CAP
    jae .trim
    mov byte [rbx+rcx+DT_D], 1 ; carried off the top: 999 -> 1000
    inc rcx
    mov [rbx+DT_N], rcx
.trim:
    mov rcx, [rbx+DT_N]
.trim_loop:
    cmp rcx, 1
    jbe .trim_end
    cmp byte [rbx+rcx+DT_D-1], 0
    jne .trim_end
    dec rcx
    jmp .trim_loop
.trim_end:
    mov [rbx+DT_N], rcx
.done:
    pop r12
    pop rdi
    pop rsi
    pop rbx
    ret

; rax = _dtoa_cmp(rcx=slotA, rdx=slotB) -> -1 / 0 / 1, exact.
;
; Compares by absolute decimal POSITION rather than by aligning the two
; digit arrays into a common frame. Alignment would mean materialising up
; to ~1100 leading zeros; walking positions costs nothing and cannot
; overflow a slot.
_dtoa_cmp:
    push rbx
    push rsi
    push r12
    push r13
    push r14
    push r15
    mov rbx, rcx
    mov rsi, rdx
    mov rax, [rbx+DT_N]
    dec rax
    sub rax, [rbx+DT_F]        ; highest position of A
    mov r12, rax
    mov rax, [rsi+DT_N]
    dec rax
    sub rax, [rsi+DT_F]        ; highest position of B
    cmp r12, rax
    jge .hi_ok
    mov r12, rax
.hi_ok:
    mov rax, [rbx+DT_F]
    neg rax                    ; lowest position of A
    mov r13, rax
    mov rax, [rsi+DT_F]
    neg rax                    ; lowest position of B
    cmp r13, rax
    jle .lo_ok
    mov r13, rax
.lo_ok:
.loop:
    cmp r12, r13
    jl .equal
    DT_DIGIT r14, rbx, r12
    DT_DIGIT r15, rsi, r12
    cmp r14, r15
    jb .less
    ja .greater
    dec r12
    jmp .loop
.equal:
    xor eax, eax
    jmp .out
.less:
    mov rax, -1
    jmp .out
.greater:
    mov eax, 1
.out:
    pop r15
    pop r14
    pop r13
    pop r12
    pop rsi
    pop rbx
    ret

; rax = _dtoa_cmp_val_bits(rdx=bits) -> compare _dtoa_val against the double
; with this bit pattern. Uses _dtoa_tmp as the candidate's expansion.
_dtoa_cmp_val_bits:
    sub rsp, 40
    lea rcx, [_dtoa_tmp]
    call _dtoa_expand_bits
    lea rcx, [_dtoa_val]
    lea rdx, [_dtoa_tmp]
    call _dtoa_cmp
    add rsp, 40
    ret

; xmm0 = _dtoa_nearest(xmm0=seed) -> the double nearest to _dtoa_val,
; which must hold a non-negative decimal.
;
; Positive doubles are monotonically ordered by their bit patterns, so this
; brackets the value by doubling outward from the seed, bisects down to the
; adjacent pair that straddles it, and picks by an exact midpoint compare
; with ties-to-even -- i.e. it IS a correctly-rounded strtod, built from the
; exact comparator rather than borrowed from a libc that gets it wrong.
;
; The seed only has to be finite and positive. A bad one costs a few extra
; comparisons, never a wrong answer. That robustness is what lets repr reuse
; this with x itself as the seed (where the bracket collapses immediately)
; and round() reuse it with a crude power-of-ten estimate.
;
; The midpoint of two ADJACENT doubles lo, hi is always (2*m+1) * 2**(e-1)
; for lo = m * 2**e -- true across every exponent step and across the
; subnormal boundary, because adjacent doubles always differ by exactly
; 2**e for lo's own e. That is why no special case is needed here for
; powers of two, unlike the classic asymmetric-gap formulation.
_dtoa_nearest:
    WIN64_RUNTIME_ENTER
    sub rsp, 48
    movq rax, xmm0
    ucomisd xmm0, xmm0
    jp .bad                    ; NaN seed
    xorpd xmm1, xmm1
    ucomisd xmm0, xmm1
    jbe .bad                   ; <= 0 seed
    mov r10, 0x7FF0000000000000
    mov r11, rax
    and r11, r10
    cmp r11, r10
    jne .have_g                ; finite
.bad:
    mov rax, 0x3FF0000000000000 ; fall back to 1.0
.have_g:
    mov rbx, rax               ; g = seed bits
    mov rdx, rbx
    call _dtoa_cmp_val_bits
    test rax, rax
    jz .exact
    jl .below
    ; ---- value is above the seed: bracket upward
    mov r12, rbx               ; lo
    mov r14, 1                 ; step
    ; The ceiling is the INFINITY pattern, not DBL_MAX. Expanding it as an
    ; ordinary (mantissa, exponent) pair gives 2**1024 -- exactly one ulp
    ; above DBL_MAX -- so the generic midpoint below computes the true IEEE
    ; overflow threshold 2**1024 - 2**970 with no special case, and an
    ; overflowing decimal correctly reads back as +inf. Stopping at DBL_MAX
    ; instead made it read back AS DBL_MAX, which let repr accept the
    ; 1-digit candidate "2e+308" for 1.7976931348623157e+308. That was a
    ; real bug, caught by the reference sweep before this was written.
    mov r15, 0x7FF0000000000000
.up_loop:
    mov r13, rbx
    add r13, r14
    jc .up_clamp
    cmp r13, r15
    jbe .up_have
.up_clamp:
    mov r13, r15
.up_have:
    mov rdx, r13
    call _dtoa_cmp_val_bits
    test rax, rax
    jle .bracketed
    cmp r13, r15
    je .bracketed              ; saturated: value is at/above DBL_MAX
    mov r12, r13
    add r14, r14
    jmp .up_loop
.below:
    ; ---- value is below the seed: bracket downward
    mov r13, rbx               ; hi
    mov r14, 1                 ; step
.dn_loop:
    xor r12, r12
    cmp rbx, r14
    jbe .dn_have               ; would go below zero: clamp to +0.0
    mov r12, rbx
    sub r12, r14
.dn_have:
    mov rdx, r12
    call _dtoa_cmp_val_bits
    test rax, rax
    jge .bracketed
    test r12, r12
    jz .bracketed
    mov r13, r12
    add r14, r14
    jmp .dn_loop
.bracketed:
    ; r12 = lo, r13 = hi, with double(lo) <= value <= double(hi)
.bisect:
    mov rax, r13
    sub rax, r12
    cmp rax, 1
    jbe .adjacent
    shr rax, 1
    add rax, r12               ; mid
    mov rsi, rax
    mov rdx, rsi
    call _dtoa_cmp_val_bits
    test rax, rax
    jl .bis_hi
    mov r12, rsi
    jmp .bisect
.bis_hi:
    mov r13, rsi
    jmp .bisect
.adjacent:
    cmp r12, r13
    je .pick_lo
    ; midpoint = (2*m + 1) * 2**(e-1) for lo = m * 2**e
    mov rax, r12
    mov rsi, rax
    shr rsi, 52
    and rsi, 0x7FF
    mov rdi, rax
    mov r10, 0x000FFFFFFFFFFFFF
    and rdi, r10
    test rsi, rsi
    jnz .mp_norm
    mov r8, -1074
    jmp .mp_go
.mp_norm:
    mov r10, 1
    shl r10, 52
    or rdi, r10
    lea r8, [rsi-1075]
.mp_go:
    lea rdi, [rdi+rdi+1]       ; 2*m + 1
    dec r8                     ; e - 1
    lea rcx, [_dtoa_tmp]
    mov rdx, rdi
    call _dtoa_from_mant
    lea rcx, [_dtoa_val]
    lea rdx, [_dtoa_tmp]
    call _dtoa_cmp
    test rax, rax
    jl .pick_lo
    jg .pick_hi
    mov rax, r12               ; exact tie -> the even mantissa. The low bit
    test rax, 1                ; of the bit pattern IS the mantissa parity,
    jz .pick_lo                ; for normals and subnormals alike.
.pick_hi:
    mov r12, r13
.pick_lo:
    movq xmm0, r12
    jmp .out
.exact:
    movq xmm0, rbx
.out:
    add rsp, 48
    WIN64_RUNTIME_LEAVE
    ret

; xmm0 = _dtoa_guess() -> a rough double for _dtoa_val, as a seed for
; _dtoa_nearest.
;
; Takes the leading <= 18 digits as a u64 and scales by a power of ten.
; Through 10**22 both operands are exactly representable, so the single
; multiply/divide is already correctly rounded and _dtoa_nearest confirms
; it in one comparison. Beyond that the repeated-scaling fallback drifts a
; few ULPs, which the bracket step absorbs.
_dtoa_guess:
    push rbx
    push rsi
    push rdi
    push r12
    lea rbx, [_dtoa_val]
    mov rsi, [rbx+DT_N]
    mov rdi, rsi
    cmp rdi, 18
    jbe .take
    mov rdi, 18
.take:
    mov r8, rsi
    sub r8, rdi                ; index of the lowest digit taken
    xor rax, rax
    mov rcx, rsi
.dloop:
    cmp rcx, r8
    jbe .ddone
    dec rcx
    lea rax, [rax+rax*4]
    add rax, rax               ; *= 10
    movzx r9, byte [rbx+rcx+DT_D]
    add rax, r9
    jmp .dloop
.ddone:
    mov r12, rsi
    sub r12, rdi
    sub r12, [rbx+DT_F]        ; q: value ~= rax * 10**q
    cvtsi2sd xmm0, rax         ; < 10**18, so the signed convert is fine
    test r12, r12
    jz .done
    js .neg
    cmp r12, 22
    jg .pos_big
    lea rcx, [_dtoa_pow10d]
    mulsd xmm0, [rcx+r12*8]
    jmp .done
.pos_big:
    mulsd xmm0, [_dtoa_ten]
    dec r12
    jnz .pos_big
    jmp .done
.neg:
    neg r12
    cmp r12, 22
    jg .neg_big
    lea rcx, [_dtoa_pow10d]
    divsd xmm0, [rcx+r12*8]
    jmp .done
.neg_big:
    mulsd xmm0, [_dtoa_tenth]
    dec r12
    jnz .neg_big
.done:
    pop r12
    pop rdi
    pop rsi
    pop rbx
    ret

; _dtoa_copy_src_to_val() -- _dtoa_val := _dtoa_src. repr rounds a fresh
; copy for each candidate precision, so the pristine expansion has to
; survive the search.
_dtoa_copy_src_to_val:
    push rbx
    lea rbx, [_dtoa_src]
    lea rdx, [_dtoa_val]
    mov rax, [rbx+DT_N]
    mov [rdx+DT_N], rax
    mov r8, [rbx+DT_F]
    mov [rdx+DT_F], r8
    add rax, 15
    shr rax, 3                 ; qwords covering n digits, rounded up
    xor rcx, rcx
.loop:
    cmp rcx, rax
    jae .done
    mov r9, [rbx+rcx*8+DT_D]
    mov [rdx+rcx*8+DT_D], r9
    inc rcx
    jmp .loop
.done:
    pop rbx
    ret

; rax = _dtoa_low_pos(rcx=slot) -> the decimal position of the lowest
; NONZERO digit, i.e. where the value stops once trailing zeros are
; dropped.
;
; An all-zero slot returns position 0, NOT -f. Zero has no significant
; digits at all, and 0 is the answer that makes both consumers degenerate
; correctly -- %g renders "0" rather than padding out to its precision, and
; repr never reaches here (it handles zero before expanding). Returning -f
; instead made "%g" % 0.0 print "0.00000", because the exact expansion of
; 0.0 carries f = 1074 from the subnormal exponent, so -f asked for 1074
; fractional digits' worth of stripping room.
_dtoa_low_pos:
    mov r8, [rcx+DT_N]
    xor rax, rax
.loop:
    cmp rax, r8
    jae .zero
    cmp byte [rcx+rax+DT_D], 0
    jne .found
    inc rax
    jmp .loop
.found:
    sub rax, [rcx+DT_F]
    ret
.zero:
    xor eax, eax
    ret

; rax = _dtoa_sig_round(rcx=slot, rdx=nsig) -> round the slot to nsig
; SIGNIFICANT digits, returning the decimal exponent of its leading digit
; afterwards (0 for a zero value).
;
; A carry out of the top (9.99 -> 10.0 at 2 significant digits) shifts that
; exponent by one. No redo is needed for it: the rounded value is already
; correct, only its leading position moved, and the caller emits from the
; returned exponent.
_dtoa_sig_round:
    push rbx
    push rsi
    push rdi
    sub rsp, 32
    mov rbx, rcx
    mov rsi, rdx
    cmp qword [rbx+DT_N], 1
    jne .nonzero
    cmp byte [rbx+DT_D], 0
    jne .nonzero
    xor rax, rax
    jmp .out
.nonzero:
    mov rax, [rbx+DT_N]
    dec rax
    sub rax, [rbx+DT_F]        ; dexp
    mov rdi, rsi
    dec rdi
    sub rdi, rax               ; p = nsig - 1 - dexp
    mov rcx, rbx
    mov rdx, rdi
    call _dtoa_round_at
    cmp qword [rbx+DT_N], 1
    jne .nz2
    cmp byte [rbx+DT_D], 0
    jne .nz2
    xor rax, rax
    jmp .out
.nz2:
    mov rax, [rbx+DT_N]
    dec rax
    sub rax, [rbx+DT_F]
.out:
    add rsp, 32
    pop rdi
    pop rsi
    pop rbx
    ret

; rax = _dtoa_emit_fixed(rcx=slot, rdx=prec, r8=buf) -> writes the value in
; fixed notation with exactly prec digits after the point (no sign, no NUL)
; and returns the end pointer.
;
; Digits come from absolute positions, so zero padding on either side is
; automatic: "%.100f" of 0.1 prints the 55 real digits of its exact
; expansion and then genuine zeros, which is precisely what CPython does
; and what msvcrt could not.
_dtoa_emit_fixed:
    push rbx
    push rsi
    push rdi
    push r12
    push r13
    push r14
    mov rbx, rcx
    mov r12, rdx               ; prec
    mov r13, r8                ; out
    mov rax, [rbx+DT_N]
    dec rax
    sub rax, [rbx+DT_F]        ; highest stored position
    test rax, rax
    jns .hi_ok
    xor rax, rax               ; always emit at least one integer digit
.hi_ok:
    mov rsi, rax
.int_loop:
    DT_DIGIT r14, rbx, rsi
    add r14b, '0'
    mov [r13], r14b
    inc r13
    dec rsi
    jns .int_loop
    test r12, r12
    jle .done
    mov byte [r13], '.'
    inc r13
    mov rsi, -1
    mov rdi, r12
    neg rdi
.frac_loop:
    cmp rsi, rdi
    jl .done
    DT_DIGIT r14, rbx, rsi
    add r14b, '0'
    mov [r13], r14b
    inc r13
    dec rsi
    jmp .frac_loop
.done:
    mov rax, r13
    pop r14
    pop r13
    pop r12
    pop rdi
    pop rsi
    pop rbx
    ret

; rax = _dtoa_emit_sig(rcx=slot, rdx=dexp, r8=lopos, r9=buf) -> writes the
; mantissa in scientific layout: one digit, then '.' and the digits down to
; lopos if there are any. Returns the end pointer.
_dtoa_emit_sig:
    push rbx
    push rsi
    push rdi
    push r12
    push r13
    mov rbx, rcx
    mov r12, rdx               ; dexp
    mov r13, r9                ; out
    mov rdi, r8                ; lopos
    DT_DIGIT rsi, rbx, r12
    add sil, '0'
    mov [r13], sil
    inc r13
    cmp rdi, r12
    jge .done                  ; a single significant digit: no point at all
    mov byte [r13], '.'
    inc r13
    mov rsi, r12
    dec rsi
.loop:
    cmp rsi, rdi
    jl .done
    DT_DIGIT rax, rbx, rsi
    add al, '0'
    mov [r13], al
    inc r13
    dec rsi
    jmp .loop
.done:
    mov rax, r13
    pop r13
    pop r12
    pop rdi
    pop rsi
    pop rbx
    ret

; rax = _dtoa_emit_exp(rcx=dexp, rdx=buf, r8=expchar) -> writes the
; exponent suffix and returns the end pointer.
;
; Always at least two digits and never more than needed -- C's minimum-two
; rule, which is also CPython's. MSVC's own %e pads to three ("1e+010"),
; which is where the corpus's e+003 mismatches came from.
_dtoa_emit_exp:
    push rbx
    mov rbx, rdx
    mov [rbx], r8b
    inc rbx
    mov rax, rcx
    test rax, rax
    jns .pos
    mov byte [rbx], '-'
    neg rax
    jmp .sgn
.pos:
    mov byte [rbx], '+'
.sgn:
    inc rbx
    sub rsp, 32
    xor r9, r9
.dig:
    UDIV10
    mov [rsp+r9], dl
    inc r9
    test rax, rax
    jnz .dig
    cmp r9, 2
    jae .emit
    mov byte [rsp+r9], 0       ; pad to the two-digit minimum
    inc r9
.emit:
    dec r9
.eloop:
    movzx rax, byte [rsp+r9]
    add rax, '0'
    mov [rbx], al
    inc rbx
    dec r9
    jns .eloop
    add rsp, 32
    mov rax, rbx
    pop rbx
    ret

; rax = float_to_str(xmm0) -> ptr to a nul-terminated CPython-repr-style
; float string.
;
; CPython's float repr is the SHORTEST decimal string that reads back as
; exactly the same double, rendered fixed for 1e-4 <= |x| < 1e16 and
; scientific outside that. This finds it exactly:
;
;   for nsig = 1 .. 17:
;       round the exact expansion of |x| to nsig significant digits
;       if reading that back (correctly rounded) gives x, stop
;
; and the first nsig that works is the answer, because rounding to nsig
; digits produces the CLOSEST nsig-digit decimal to x -- so if any decimal
; of that length round-trips, this one does.
;
; The read-back is _dtoa_nearest, seeded with x itself, so the usual case
; costs one bracket step and one midpoint compare. Seventeen significant
; digits always suffice for a double, and the loop falls out at 17 having
; already produced that rendering.
;
; The previous implementation searched sprintf precisions and checked each
; with strtod. That was defensible before the exact machinery existed, but
; it inherited both of msvcrt's defects: the candidates could not carry
; more than ~17 significant digits, and the acceptance test used a strtod
; that is measurably 1 ULP low on 37 out of 200000 repr-shaped strings.
; Neither approximation is present here.
_abi_float_to_str:
    WIN64_RUNTIME_ENTER
    sub rsp, 64
    ucomisd xmm0, xmm0
    jp .nan
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
.nan:
    lea rax, [_abi_str_nan]
    jmp .done
.finite:
    movq rax, xmm0
    mov rbx, rax
    shr rbx, 63                ; sign
    mov r10, 0x7FFFFFFFFFFFFFFF
    and rax, r10
    mov r13, rax               ; |x| as a bit pattern
    test rax, rax
    jnz .nonzero
    lea rax, [_dtoa_zero_str]  ; 118_float_repr.py pins -0.0 rendering as
    test rbx, rbx              ; "-0.0", so the sign bit is read directly
    jz .done                   ; rather than inferred from a comparison
    lea rax, [_dtoa_negzero_str]
    jmp .done
.nonzero:
    lea rcx, [_dtoa_src]
    mov rdx, r13
    call _dtoa_expand_bits
    mov r12, 1                 ; nsig
.search:
    call _dtoa_copy_src_to_val
    lea rcx, [_dtoa_val]
    mov rdx, r12
    call _dtoa_sig_round
    mov r14, rax               ; decimal exponent of the leading digit
    movq xmm0, r13
    call _dtoa_nearest
    movq rax, xmm0
    cmp rax, r13
    je .found
    inc r12
    cmp r12, 17
    jbe .search
    ; Not reached for a finite double (17 significant digits always
    ; round-trip); if it ever were, _dtoa_val already holds the 17-digit
    ; rendering, which is the right thing to print anyway.
.found:
    lea rcx, [_dtoa_val]
    call _dtoa_low_pos
    mov r15, rax               ; lowest nonzero position (trailing zeros gone)
    lea rdi, [_dtoa_repr_buf]
    test rbx, rbx
    jz .no_sign
    mov byte [rdi], '-'
    inc rdi
.no_sign:
    ; CPython switches to scientific outside [1e-4, 1e16); inside it, fixed.
    cmp r14, -4
    jl .sci
    cmp r14, 16
    jge .sci
    ; Fixed. Emit down to the lowest nonzero position, but never fewer than
    ; one fractional digit -- repr always shows a point ("2.0", not "2").
    mov rdx, r15
    neg rdx
    cmp rdx, 1
    jge .prec_ok
    mov rdx, 1
.prec_ok:
    lea rcx, [_dtoa_val]
    mov r8, rdi
    call _dtoa_emit_fixed
    mov rdi, rax
    jmp .terminate
.sci:
    lea rcx, [_dtoa_val]
    mov rdx, r14
    mov r8, r15
    mov r9, rdi
    call _dtoa_emit_sig
    mov rdi, rax
    mov rcx, r14
    mov rdx, rdi
    mov r8, 'e'
    call _dtoa_emit_exp
    mov rdi, rax
.terminate:
    mov byte [rdi], 0
    lea rax, [_dtoa_repr_buf]
.done:
    call _runtime_str_concat_dup
    add rsp, 64
    WIN64_RUNTIME_LEAVE
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
    ; Size from the format, not a guess -- see _abi_float_fmt for the full
    ; rationale. 64 bytes holds any bare %lld (20 digits), but a width makes
    ; the result arbitrarily long: `"%500d" % 5` writes 500 bytes into it.
    mov rcx, [rsp+48]          ; fmt
    mov rdx, [rsp+40]          ; value
    xor eax, eax
    call _scprintf
    movsxd rax, eax            ; int return -- sign-extend before testing
    test rax, rax
    jns ._aif_sized            ; non-negative => the true length
    mov rax, 1152              ; non-conforming libc => worst-case bound
._aif_sized:
    lea rcx, [rax+1]           ; + NUL
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
;
; RETAINED AS THE FALLBACK. _abi_float_fmt below handles e/E/f/F/g/G
; exactly and hands anything else to this routine unchanged, so an
; unrecognised conversion still formats exactly as it always did rather
; than failing or silently dropping flags.
_abi_float_fmt_sprintf:
    ; See _abi_int_fmt's comment: locals must live at/above +32, clear of
    ; the [0,32) shadow space malloc/sprintf are free to scribble on.
    sub rsp, 56
    mov [rsp+40], rdx          ; fmt_ptr
    movsd [rsp+48], xmm0       ; value
    ; Size the allocation from the format rather than assuming it fits.
    ;
    ; This was `mov rcx, 64` unconditionally. sprintf does not bound its
    ; output, so any conversion wider than 63 characters ran off the end of
    ; the heap block: `print("%f" % 1e100)` needs 108 bytes and killed the
    ; process with STATUS_HEAP_CORRUPTION (0xC0000374), and DBL_MAX under
    ; `%f` needs 316. snprintf(NULL, 0, ...) returns exactly the length the
    ; result requires and writes nothing, so one probe pass gives the true
    ; size for any precision.
    mov rcx, [rsp+40]          ; fmt
    mov rdx, [rsp+48]          ; value: GP slot for the vararg...
    movq xmm1, [rsp+48]        ; ...and its XMM twin (Win64 varargs pass both)
    xor eax, eax
    call _scprintf
    movsxd rax, eax            ; int return -- sign-extend before testing
    ; A pre-C99 snprintf reports truncation as a negative value instead of
    ; the required length. Only a negative result is untrustworthy -- a small
    ; non-negative one is the real (short) length and must be used as-is, or
    ; every format would allocate the worst-case block. On a negative result
    ; fall back to a bound covering every conversion the compiler emits, so a
    ; non-conforming libc over-allocates instead of overflowing.
    test rax, rax
    jns ._aff_sized
    mov rax, 1152
._aff_sized:
    lea rcx, [rax+1]           ; + NUL
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

; rax = float_fmt(value=xmm0, fmt_ptr=rdx) -- exact printf-style float
; formatting for e/E/f/F/g/G. See _abi_float_fmt_sprintf above for the
; register convention (arg0 float -> xmm0, arg1 -> RDX, not RCX).
;
; This parses the format itself and generates digits from the exact decimal
; expansion, because msvcrt's printf cannot: it carries ~17 significant
; digits and zero-fills the rest, so "%f" % 1e100 printed 108 characters of
; correct LENGTH and wrong DIGITS, and it rounds halfway cases away from
; zero where CPython rounds half-to-even ("%.2f" % 0.125 -> "0.13", CPython
; "0.12"). Both are fixed here at once, since the exact expansion answers
; "is this exactly half?" definitively.
;
; It also fixes the exponent width. MSVC's %e always emits three exponent
; digits ("1.23e+004"); C and CPython emit a minimum of two ("1.23e+04").
; That single divergence was four of the corpus's failing cases.
;
; Anything this does not recognise -- a conversion outside e/E/f/F/g/G, a
; length modifier, an absurd width -- is handed to _abi_float_fmt_sprintf
; unchanged, so no format that worked before can start failing.
_abi_float_fmt:
    WIN64_RUNTIME_ENTER
    sub rsp, 96
    ; [rsp+32] value bits   [rsp+40] fmt      [rsp+48] malloc'd buffer
    ; [rsp+56] body start   [rsp+64] sign chr [rsp+72] uppercase?
    ; [rsp+80] dexp         [rsp+88] lopos / special-value string
    movsd [rsp+32], xmm0
    mov [rsp+40], rdx
    ; ---- parse "%[flags][width][.prec]conv" --------------------------
    mov rbx, rdx
    cmp byte [rbx], '%'
    jne .fallback
    inc rbx
    xor r12, r12               ; flags: 1 '-'  2 '0'  4 '+'  8 ' '  16 '#'
.flag_loop:
    movzx eax, byte [rbx]
    cmp al, '-'
    je .f_minus
    cmp al, '0'
    je .f_zero
    cmp al, '+'
    je .f_plus
    cmp al, ' '
    je .f_space
    cmp al, '#'
    je .f_hash
    jmp .flags_done
.f_minus:
    or r12, 1
    inc rbx
    jmp .flag_loop
.f_zero:
    or r12, 2
    inc rbx
    jmp .flag_loop
.f_plus:
    or r12, 4
    inc rbx
    jmp .flag_loop
.f_space:
    or r12, 8
    inc rbx
    jmp .flag_loop
.f_hash:
    or r12, 16
    inc rbx
    jmp .flag_loop
.flags_done:
    xor r13, r13               ; width
.w_loop:
    movzx eax, byte [rbx]
    cmp al, '0'
    jb .w_done
    cmp al, '9'
    ja .w_done
    sub al, '0'
    imul r13, r13, 10
    movzx eax, al
    add r13, rax
    inc rbx
    cmp r13, 100000
    ja .fallback               ; absurd width: not worth a bespoke path
    jmp .w_loop
.w_done:
    mov r14, 6                 ; C's (and Python's) default precision
    cmp byte [rbx], '.'
    jne .p_done
    inc rbx
    xor r14, r14               ; a bare "." means precision 0
.p_loop:
    movzx eax, byte [rbx]
    cmp al, '0'
    jb .p_done
    cmp al, '9'
    ja .p_done
    sub al, '0'
    imul r14, r14, 10
    movzx eax, al
    add r14, rax
    inc rbx
    cmp r14, 100000
    ja .fallback
    jmp .p_loop
.p_done:
    movzx eax, byte [rbx]
    inc rbx
    cmp byte [rbx], 0
    jne .fallback              ; trailing junk, e.g. a length modifier
    xor r15, r15               ; kind: 0 = f, 1 = e, 2 = g
    mov qword [rsp+72], 0
    cmp al, 'f'
    je .k_ok
    cmp al, 'F'
    je .k_F
    cmp al, 'e'
    je .k_e
    cmp al, 'E'
    je .k_E
    cmp al, 'g'
    je .k_g
    cmp al, 'G'
    je .k_G
    jmp .fallback
.k_F:
    mov qword [rsp+72], 1
    jmp .k_ok
.k_e:
    mov r15, 1
    jmp .k_ok
.k_E:
    mov r15, 1
    mov qword [rsp+72], 1
    jmp .k_ok
.k_g:
    mov r15, 2
    jmp .k_ok
.k_G:
    mov r15, 2
    mov qword [rsp+72], 1
.k_ok:
    ; ---- non-finite --------------------------------------------------
    movsd xmm0, [rsp+32]
    ucomisd xmm0, xmm0
    jp .nan
    mov rax, [rsp+32]
    mov r10, 0x7FFFFFFFFFFFFFFF
    and rax, r10
    mov r11, 0x7FF0000000000000
    cmp rax, r11
    je .inf
    ; ---- sign --------------------------------------------------------
    mov rax, [rsp+32]
    xor rcx, rcx
    test rax, rax
    jns .sgn_pos               ; the sign BIT, so -0.0 keeps its '-'
    mov rcx, '-'
    jmp .sgn_done
.sgn_pos:
    test r12, 4
    jz .sgn_sp
    mov rcx, '+'
    jmp .sgn_done
.sgn_sp:
    test r12, 8
    jz .sgn_done
    mov rcx, ' '
.sgn_done:
    mov [rsp+64], rcx
    ; ---- exact expansion of |value| ----------------------------------
    mov rax, [rsp+32]
    mov r10, 0x7FFFFFFFFFFFFFFF
    and rax, r10
    lea rcx, [_dtoa_val]
    mov rdx, rax
    call _dtoa_expand_bits
    test r15, r15
    jz .kind_f
    cmp r15, 1
    je .kind_e
    jmp .kind_g
.kind_f:
    lea rcx, [_dtoa_val]
    mov rdx, r14
    call _dtoa_round_at
    xor r15, r15               ; render fixed
    jmp .emit
.kind_e:
    lea rcx, [_dtoa_val]
    lea rdx, [r14+1]           ; nsig = prec + 1
    call _dtoa_sig_round
    mov [rsp+80], rax
    sub rax, r14
    mov [rsp+88], rax          ; lopos = dexp - prec: %e never strips
    mov r15, 1
    jmp .emit
.kind_g:
    mov rdx, r14
    test rdx, rdx
    jnz .g_p
    mov rdx, 1                 ; C: precision 0 behaves as 1 for %g
.g_p:
    mov r14, rdx               ; P = significant digits
    lea rcx, [_dtoa_val]
    call _dtoa_sig_round
    mov [rsp+80], rax
    lea rcx, [_dtoa_val]
    call _dtoa_low_pos
    mov r9, rax                ; lowest nonzero position
    mov rax, [rsp+80]
    cmp rax, -4
    jl .g_sci
    cmp rax, r14
    jge .g_sci
    ; fixed, with P-1-dexp fractional digits
    mov rcx, r14
    dec rcx
    sub rcx, rax
    test r12, 16
    jnz .g_fixed_keep          ; '#' keeps trailing zeros
    mov rdx, r9
    neg rdx                    ; digits actually needed below the point
    test rdx, rdx
    jns .g_f1
    xor rdx, rdx
.g_f1:
    cmp rdx, rcx
    jle .g_f2
    mov rdx, rcx
.g_f2:
    mov rcx, rdx
.g_fixed_keep:
    mov r14, rcx
    xor r15, r15
    jmp .emit
.g_sci:
    mov rcx, rax
    sub rcx, r14
    inc rcx                    ; dexp - (P-1)
    test r12, 16
    jnz .g_sci_keep
    cmp r9, rcx
    jle .g_sci_keep
    mov rcx, r9                ; raise the floor to the last nonzero digit
.g_sci_keep:
    mov [rsp+88], rcx
    mov r15, 1
    ; ---- render ------------------------------------------------------
.emit:
    mov rcx, r13
    add rcx, r14
    add rcx, 512               ; 309 integer digits + sign + point +
    call malloc                ; exponent + NUL, with room to spare
    mov [rsp+48], rax
    mov rdi, rax
    add rdi, r13               ; leave `width` bytes of room for left padding
    mov [rsp+56], rdi
    mov rcx, [rsp+64]
    test rcx, rcx
    jz .no_sign
    mov [rdi], cl
    inc rdi
.no_sign:
    test r15, r15
    jnz .emit_sci
    lea rcx, [_dtoa_val]
    mov rdx, r14
    mov r8, rdi
    call _dtoa_emit_fixed
    mov rdi, rax
    test r12, 16
    jz .emitted
    test r14, r14
    jnz .emitted
    mov byte [rdi], '.'        ; '#' keeps the point at precision 0
    inc rdi
    jmp .emitted
.emit_sci:
    lea rcx, [_dtoa_val]
    mov rdx, [rsp+80]
    mov r8, [rsp+88]
    mov r9, rdi
    call _dtoa_emit_sig
    mov rdi, rax
    test r12, 16
    jz .sci_exp
    mov rax, [rsp+88]
    cmp rax, [rsp+80]
    jl .sci_exp
    mov byte [rdi], '.'
    inc rdi
.sci_exp:
    mov rcx, [rsp+80]
    mov rdx, rdi
    mov r8, 'e'
    cmp qword [rsp+72], 0
    je .sci_e
    mov r8, 'E'
.sci_e:
    call _dtoa_emit_exp
    mov rdi, rax
.emitted:
    mov byte [rdi], 0
    jmp .pad
    ; ---- inf / nan ---------------------------------------------------
.nan:
    lea rax, [_abi_str_nan]
    cmp qword [rsp+72], 0
    je .nan_ok
    lea rax, [_abi_str_NAN]
.nan_ok:
    mov [rsp+88], rax
    mov qword [rsp+64], 0      ; a NaN never carries '-', even when signed
    test r12, 4
    jz .nan_sp
    mov qword [rsp+64], '+'
    jmp .special
.nan_sp:
    test r12, 8
    jz .special
    mov qword [rsp+64], ' '
    jmp .special
.inf:
    mov rax, [rsp+32]
    test rax, rax
    js .inf_neg
    lea rax, [_abi_str_pinf]
    cmp qword [rsp+72], 0
    je .inf_p_ok
    lea rax, [_abi_str_PINF]
.inf_p_ok:
    mov [rsp+88], rax
    mov qword [rsp+64], 0
    test r12, 4
    jz .inf_sp
    mov qword [rsp+64], '+'
    jmp .special
.inf_sp:
    test r12, 8
    jz .special
    mov qword [rsp+64], ' '
    jmp .special
.inf_neg:
    lea rax, [_abi_str_ninf]
    cmp qword [rsp+72], 0
    je .inf_n_ok
    lea rax, [_abi_str_NINF]
.inf_n_ok:
    mov [rsp+88], rax
    mov qword [rsp+64], 0      ; the '-' is already inside the string
.special:
    and r12, -3                ; C never zero-pads inf/nan
    mov rcx, r13
    add rcx, 32
    call malloc
    mov [rsp+48], rax
    mov rdi, rax
    add rdi, r13
    mov [rsp+56], rdi
    mov rcx, [rsp+64]
    test rcx, rcx
    jz .sp_nosign
    mov [rdi], cl
    inc rdi
.sp_nosign:
    mov rsi, [rsp+88]
.sp_copy:
    mov al, [rsi]
    test al, al
    jz .sp_done
    mov [rdi], al
    inc rdi
    inc rsi
    jmp .sp_copy
.sp_done:
    mov byte [rdi], 0
    ; ---- width and padding -------------------------------------------
    ; The body was written at buf+width so that left padding always has
    ; somewhere to go; every copy below moves it DOWN toward the base, so a
    ; simple forward byte loop is always safe (dest <= src).
.pad:
    mov rsi, [rsp+56]
    mov rax, rdi
    sub rax, rsi               ; body length
    mov rbx, [rsp+48]          ; base
    cmp rax, r13
    jge .pad_none
    mov rcx, r13
    sub rcx, rax               ; pad count
    test r12, 1
    jnz .pad_ljust
    test r12, 2
    jnz .pad_zeros
    mov r8, rbx
    mov r9, rcx
.psp:
    test r9, r9
    jz .psp_done
    mov byte [r8], ' '
    inc r8
    dec r9
    jmp .psp
.psp_done:
    mov r9, rax
.pcp:
    test r9, r9
    jz .pcp_done
    mov r10b, [rsi]
    mov [r8], r10b
    inc rsi
    inc r8
    dec r9
    jmp .pcp
.pcp_done:
    mov byte [r8], 0
    mov rax, rbx
    jmp .fin
.pad_zeros:
    ; zero padding goes AFTER any sign, not before it
    mov r8, rbx
    mov r11, [rsp+64]
    test r11, r11
    jz .pz_nosign
    mov [r8], r11b
    inc r8
    inc rsi                    ; the body's own copy of the sign is consumed
    dec rax
.pz_nosign:
    mov r9, rcx
.pz:
    test r9, r9
    jz .pz_done
    mov byte [r8], '0'
    inc r8
    dec r9
    jmp .pz
.pz_done:
    mov r9, rax
.pzc:
    test r9, r9
    jz .pzc_done
    mov r10b, [rsi]
    mov [r8], r10b
    inc rsi
    inc r8
    dec r9
    jmp .pzc
.pzc_done:
    mov byte [r8], 0
    mov rax, rbx
    jmp .fin
.pad_ljust:
    mov r8, rbx
    mov r9, rax
.plj:
    test r9, r9
    jz .plj_done
    mov r10b, [rsi]
    mov [r8], r10b
    inc rsi
    inc r8
    dec r9
    jmp .plj
.plj_done:
    mov r9, rcx
.plt:
    test r9, r9
    jz .plt_done
    mov byte [r8], ' '
    inc r8
    dec r9
    jmp .plt
.plt_done:
    mov byte [r8], 0
    mov rax, rbx
    jmp .fin
.pad_none:
    mov r8, rbx
    mov r9, rax
.pn:
    test r9, r9
    jz .pn_done
    mov r10b, [rsi]
    mov [r8], r10b
    inc rsi
    inc r8
    dec r9
    jmp .pn
.pn_done:
    mov byte [r8], 0
    mov rax, rbx
.fin:
    add rsp, 96
    WIN64_RUNTIME_LEAVE
    ret
.fallback:
    movsd xmm0, [rsp+32]
    mov rdx, [rsp+40]
    add rsp, 96
    WIN64_RUNTIME_LEAVE
    jmp _abi_float_fmt_sprintf

; xmm0 = round_ndigits(xmm0 = x, rdx = ndigits) -> CPython's round(x, n)
; for a float x. Same register convention as _abi_float_fmt (arg0 float ->
; xmm0, arg1 -> RDX).
;
; The old lowering computed roundsd(x * 10**n) / 10**n. That is wrong for a
; reason no amount of care in the rounding step can fix: 2.55 is really
; 2.5499999999999998..., so it must round DOWN to 2.5, but 2.55 * 10 is
; exactly 25.5 as a double -- the multiply has already destroyed the digits
; that decide the question, and ties-to-even then answers 26.
;
; So the decision is made on the exact decimal expansion, where "2.55" still
; knows it is below the halfway point, and only then is the result converted
; back to a double. Both halves are exact, which is what CPython does too
; (dtoa mode 3 followed by a correctly-rounded strtod).
;
; Two fast exits carry most real calls: a non-finite or zero x, and -- more
; usefully -- a value that has no digits past position ndigits at all, which
; is returned UNCHANGED rather than round-tripped. That is what keeps
; round(1e100, 5) and round(1234.5, 2) exact identities.
_abi_round_ndigits:
    WIN64_RUNTIME_ENTER
    sub rsp, 64
    movsd [rsp+32], xmm0
    mov [rsp+40], rdx
    ucomisd xmm0, xmm0
    jp .ret_x                  ; NaN
    movq rax, xmm0
    mov r10, 0x7FFFFFFFFFFFFFFF
    and rax, r10
    mov r11, 0x7FF0000000000000
    cmp rax, r11
    je .ret_x                  ; +-inf
    mov r12, rax               ; |x| bits
    movq rax, xmm0
    shr rax, 63
    mov r13, rax               ; sign
    test r12, r12
    jz .ret_x                  ; +-0.0 rounds to itself, sign intact
    lea rcx, [_dtoa_val]
    mov rdx, r12
    call _dtoa_expand_bits
    mov rax, [_dtoa_val+DT_F]
    cmp rax, [rsp+40]
    jle .ret_x                 ; nothing past position ndigits: already done
    lea rcx, [_dtoa_val]
    mov rdx, [rsp+40]
    call _dtoa_round_at
    cmp qword [_dtoa_val+DT_N], 1
    jne .nonzero
    cmp byte [_dtoa_val+DT_D], 0
    jne .nonzero
    xorpd xmm0, xmm0           ; rounded away to zero; CPython keeps the
    test r13, r13              ; sign here, so round(-0.4) is -0.0
    jz .out
    mov r10, 0x8000000000000000
    movq xmm0, r10
    jmp .out
.nonzero:
    call _dtoa_guess
    call _dtoa_nearest
    test r13, r13
    jz .out
    movq rax, xmm0
    mov r10, 0x8000000000000000
    xor rax, r10
    movq xmm0, rax
.out:
    add rsp, 64
    WIN64_RUNTIME_LEAVE
    ret
.ret_x:
    movsd xmm0, [rsp+32]
    jmp .out

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

