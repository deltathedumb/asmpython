; ABI shim layer (SysV / Linux variant of abi_shims.asm -- see that file's
; header comment for the full rationale). Thin wrappers exposing
; asmpython's runtime helpers (which use codegen.py's own ad-hoc internal
; calling convention -- rax/rbx/rcx for most 2-3 arg helpers) under the
; standard SysV ABI (rdi/rsi/rdx/rcx), so the built-in x86-64 backend's
; SSA IR pipeline can call them directly on Linux. SysV needs no shadow
; space at call sites (unlike Win64), but the stack reservations below
; are kept the same size anyway -- harmless to over-allocate, and it
; keeps this file a near-mechanical arg-register swap of the Windows one
; rather than a second design to maintain.
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
extern strtoll
extern malloc
extern printf
extern sprintf
extern putchar

global _abi_dict_get_default
global _abi_dict_set
global _abi_dict_contains
global _abi_dict_keys
global _abi_dict_update
global _abi_str_concat
global _abi_str_rsplit
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

; asmlib.hardware's _hw_* symbols, hosted-target bodies -- see
; abi_shims.asm's matching comment block; identical behavior, SysV args.
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

; rax = dict_get_default(dict=rdi, key=rsi, default=rdx)
;
; Each shim below uses RBX as scratch (the underlying _runtime_* helper's
; ad-hoc 2nd-argument register) but RBX is callee-saved per the SysV ABI
; -- regalloc legitimately keeps a caller's own live value parked there
; across this call, same as any other external function, and clobbering
; it without saving/restoring silently destroys that value. This was a
; real bug: a dict's pointer or a string's address held in RBX across a
; call to one of these shims would read back as garbage immediately
; after, corrupting whatever used it next (confirmed via a real segfault
; that only appeared once two calls into the same dict were chained).
; `push rbx` / `pop rbx` fixes that *and* the stack alignment (entry
; rsp % 16 == 8, the standard post-`call` invariant; one push lands on
; 16-aligned before this shim's own `call`).
_abi_dict_get_default:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    mov rcx, rdx
    call _runtime_dict_get_default
    pop rbx
    ret

; dict_set(dict=rdi, key=rsi, value=rdx) -> void (rax undefined)
_abi_dict_set:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    mov rcx, rdx
    call _runtime_dict_set
    pop rbx
    ret

; rax = dict_contains(dict=rdi, key=rsi)
_abi_dict_contains:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_dict_contains
    pop rbx
    ret

; rax = dict_keys(dict=rdi)
_abi_dict_keys:
    mov rax, rdi
    call _runtime_dict_keys
    ret

; dict_update(dst=rdi, src=rsi) -> void
_abi_dict_update:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_dict_update
    pop rbx
    ret

; rax = str_concat(left=rdi, right=rsi)
_abi_str_concat:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_str_concat
    pop rbx
    ret

; rax = int_to_base(n=rdi, base=rsi, prefix=rdx)
_abi_int_to_base:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    mov rcx, rdx
    call _runtime_int_to_base
    pop rbx
    ret

; rax = fmt_elem(value=rdi, kind=rsi)
_abi_fmt_elem:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_fmt_elem
    pop rbx
    ret

; rax = list_repr(list=rdi, elem_kind=rsi)
_abi_list_repr:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_list_repr
    pop rbx
    ret

; rax = dict_repr(dict=rdi, key_kind=rsi, value_kind=rdx)
_abi_dict_repr:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    mov rcx, rdx
    call _runtime_dict_repr
    pop rbx
    ret

; rax = set_repr(set=rdi, elem_kind=rsi)
_abi_set_repr:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_set_repr
    pop rbx
    ret

; rax = str_char_at(str=rdi, index=rsi)
_abi_str_char_at:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_str_char_at
    pop rbx
    ret

; rax = str_slice(str=rdi, start=rsi, stop=rdx)
_abi_str_slice:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    mov rcx, rdx
    call _runtime_str_slice
    pop rbx
    ret

; rax = new empty instance dict (no args). See abi_shims.asm's matching
; function for the full layout rationale -- identical here, just SysV.
_abi_new_instance:
    push rbx
    sub rsp, 48
    mov rdi, 40                  ; DICT_HEADER
    call malloc
    mov qword [rax+0], 8         ; DICT_CAP_OFF = 8 initial slots
    mov qword [rax+8], 0         ; DICT_LEN_OFF
    mov qword [rax+16], 0        ; DICT_TOMB_OFF
    mov [rsp+32], rax            ; spill header ptr
    mov rbx, 128                 ; 8 * DICT_SLOT_SIZE(16)
    call _runtime_zalloc
    mov rdi, [rsp+32]
    mov [rdi+24], rax            ; DICT_BUF_OFF
    mov rbx, 64
    call _runtime_zalloc
    mov rdi, [rsp+32]
    mov [rdi+32], rax            ; DICT_ORDER_OFF
    mov rax, rdi
    add rsp, 48
    pop rbx
    ret

; rax = new list with initial capacity cap=rdi (elements; clamped to >=1
; to avoid zalloc(0)) -- see abi_shims.asm's matching comment.
_abi_new_list:
    push rbx
    sub rsp, 48
    mov rbx, rdi
    cmp rbx, 1
    jge .cap_ok
    mov rbx, 1
.cap_ok:
    mov rdi, 24                  ; LIST_HEADER
    call malloc
    mov [rax+0], rbx             ; LIST_CAP_OFF = cap
    mov qword [rax+8], 0         ; LIST_LEN_OFF
    mov [rsp+32], rax            ; spill header ptr
    mov rdi, rbx
    shl rdi, 3                   ; bytes = cap * 8
    mov rbx, rdi
    call _runtime_zalloc
    mov rdi, [rsp+32]
    mov [rdi+16], rax            ; LIST_BUF_OFF
    mov rax, rdi
    add rsp, 48
    pop rbx
    ret

; rax = strtoll(str=rdi, NULL, 10)
_abi_str_to_int:
    push rbx
    xor rsi, rsi
    mov rdx, 10
    call strtoll
    pop rbx
    ret

; rax = strtoll(str=rdi, NULL, base=rsi)
_abi_str_to_int_base:
    push rbx
    mov rdx, rsi
    xor rsi, rsi
    call strtoll
    pop rbx
    ret

; rax = chr(n=rdi)
_abi_chr:
    push rbx
    mov rax, rdi
    call _runtime_chr
    pop rbx
    ret

; sort_str(list=rdi) -> void
_abi_sort_str:
    push rbx
    mov rax, rdi
    call _runtime_sort_str
    pop rbx
    ret

; sort_int(list=rdi) -> void
_abi_sort_int:
    push rbx
    mov rax, rdi
    call _runtime_sort_int
    pop rbx
    ret

; sort_items(list=rdi) -> void
_abi_sort_items:
    push rbx
    mov rax, rdi
    call _runtime_sort_items
    pop rbx
    ret

; sort_pairs_str(elems=rdi, keys=rsi) -> void
_abi_sort_pairs_str:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_sort_pairs_str
    pop rbx
    ret

; sort_pairs_int(elems=rdi, keys=rsi) -> void
_abi_sort_pairs_int:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_sort_pairs_int
    pop rbx
    ret

; list_append(list=rdi, value=rsi) -> void
_abi_list_append:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_list_append
    pop rbx
    ret

; rax = list_pop(list=rdi) -- see abi_shims.asm's matching comment.
_abi_list_pop:
    mov rax, rdi
    call _runtime_list_pop
    ret

; rax = list_slice(src=rdi, start=rsi, stop=rdx)
_abi_list_slice:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    mov rcx, rdx
    call _runtime_list_slice
    pop rbx
    ret

; list_slice_assign(dst=rdi, start=rsi, stop=rdx, src=rcx) -> void
_abi_list_slice_assign:
    push rbx
    mov rax, rdi
    mov rbx, rcx
    mov rcx, rsi
    call _runtime_list_slice_assign
    pop rbx
    ret

; ---- str methods -- see abi_shims.asm's matching block for the full
; rationale; SysV args (rdi/rsi/rdx) instead of Win64's (rcx/rdx/r8).
_abi_str_eq:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_str_eq
    pop rbx
    ret
_abi_str_cmp:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_str_cmp
    pop rbx
    ret
_abi_str_upper:
    mov rax, rdi
    call _runtime_str_upper
    ret
_abi_str_lower:
    mov rax, rdi
    call _runtime_str_lower
    ret
_abi_str_strip:
    mov rax, rdi
    call _runtime_str_strip
    ret
_abi_str_isdigit:
    mov rax, rdi
    call _runtime_str_isdigit
    ret
_abi_str_index_of:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_str_index_of
    pop rbx
    ret
_abi_str_split:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    mov rcx, 0                    ; maxsplit=0 is _runtime_str_split's own
                                   ; "unlimited" sentinel -- see abi_shims.asm's
                                   ; matching comment. Always RCX regardless of
                                   ; host OS (the helper's own ad-hoc convention).
    call _runtime_str_split
    pop rbx
    ret
_abi_str_rsplit:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    mov rcx, 1
    call _runtime_str_rsplit
    pop rbx
    ret
_abi_str_join:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_str_join
    pop rbx
    ret
_abi_str_zfill:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_str_zfill
    pop rbx
    ret
_abi_str_starts_with:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_str_starts_with
    pop rbx
    ret
_abi_str_ends_with:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_str_ends_with
    pop rbx
    ret
_abi_str_count:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_str_count
    pop rbx
    ret

; rax = str_replace(self=rdi, old=rsi, new=rdx) -> result. The 3rd arg
; must land in RCX -- _runtime_str_replace's own ad-hoc rax/rbx/rcx
; convention, independent of the host OS's ABI argument registers.
_abi_str_replace:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    mov rcx, rdx
    call _runtime_str_replace
    pop rbx
    ret

; ---- more str methods: one-arg (self only), rax=self -> rax=result.
_abi_str_capitalize:
    mov rax, rdi
    call _runtime_str_capitalize
    ret
_abi_str_isalpha:
    mov rax, rdi
    call _runtime_str_isalpha
    ret
_abi_str_isalnum:
    mov rax, rdi
    call _runtime_str_isalnum
    ret
_abi_str_islower:
    mov rax, rdi
    call _runtime_str_islower
    ret
_abi_str_isupper:
    mov rax, rdi
    call _runtime_str_isupper
    ret
_abi_str_isspace:
    mov rax, rdi
    call _runtime_str_isspace
    ret
_abi_str_lstrip:
    mov rax, rdi
    call _runtime_str_lstrip
    ret
_abi_str_rstrip:
    mov rax, rdi
    call _runtime_str_rstrip
    ret
_abi_str_swapcase:
    mov rax, rdi
    call _runtime_str_swapcase
    ret
_abi_str_title:
    mov rax, rdi
    call _runtime_str_title
    ret
_abi_str_splitlines:
    mov rax, rdi
    call _runtime_str_splitlines
    ret
_abi_str_split_ws:
    mov rax, rdi
    call _runtime_str_split_ws
    ret

; ---- more str methods: two-arg (self, arg2), rax=self/rbx=arg2 -> rax=result.
_abi_str_removeprefix:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_str_removeprefix
    pop rbx
    ret
_abi_str_removesuffix:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_str_removesuffix
    pop rbx
    ret

; str padding: self=rdi, width=rsi, fillstr=rdx. Runtime wants
; rax=self, rbx=width, rcx=first byte of fillstr.
_abi_str_ljust:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    movzx rcx, byte [rdx]
    call _runtime_str_ljust
    pop rbx
    ret
_abi_str_rjust:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    movzx rcx, byte [rdx]
    call _runtime_str_rjust
    pop rbx
    ret
_abi_str_center:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    movzx rcx, byte [rdx]
    call _runtime_str_center
    pop rbx
    ret

; ---- list methods.
_abi_list_reverse:
    mov rax, rdi
    call _runtime_list_reverse
    ret
_abi_list_extend:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    call _runtime_list_extend
    pop rbx
    ret
_abi_list_insert:
    push rbx
    mov rax, rdi
    mov rbx, rsi
    mov rcx, rdx
    call _runtime_list_insert
    pop rbx
    ret

; ---- asmlib.hardware: ring-0-only ops, stubbed (unavailable to ring-3
; hosted code) -- see abi_shims.asm's matching block.
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

_hw_rdtsc:
    rdtsc
    shl rdx, 32
    or rax, rdx
    ret

; rax = cpuid(leaf=rdi) -- EAX after CPUID with EAX=leaf.
_hw_cpuid:
    mov eax, edi
    push rbx
    cpuid
    pop rbx
    movsx rax, eax
    ret

_hw_rdrand:
.retry:
    rdrand rax
    jnc .retry
    ret

; ---- asmlib.hardware: high-level console -- see abi_shims.asm's matching
; block for the full rationale (ANSI/VT100 escapes over printf).

section .bss
_con_row:   resq 1
_con_col:   resq 1
_con_ch:    resq 1
_con_ansi1: resq 1
_con_ansi2: resq 1
_con_buf:   resb 32

section .rodata
_con_fmt_clear:  db 27, "[2J", 27, "[H", 0
_con_fmt_color:  db 27, "[%dm", 27, "[%dm", 0
_con_fmt_cursor: db 27, "[%d;%dH", 0
_con_fmt_s:      db "%s", 0

section .text

_hw_console_clear:
    sub rsp, 40
    lea rdi, [_con_fmt_clear]
    xor eax, eax
    call printf
    xor eax, eax
    mov [_con_row], rax
    mov [_con_col], rax
    add rsp, 40
    ret

; console_putc(ch=rdi)
_hw_console_putc:
    sub rsp, 40
    mov [_con_ch], rdi
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

; console_write(s=rdi)
_hw_console_write:
    push rbx
    sub rsp, 32
    mov rbx, rdi
    mov rsi, rdi
    lea rdi, [_con_fmt_s]
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

; console_set_color(fg=rdi, bg=rsi)
_hw_console_set_color:
    sub rsp, 40
    mov rax, rdi
    cmp rax, 8
    jl .fg_lo
    add rax, 82
    jmp .fg_done
.fg_lo:
    add rax, 30
.fg_done:
    mov [_con_ansi1], rax
    mov rax, rsi
    cmp rax, 8
    jl .bg_lo
    add rax, 92
    jmp .bg_done
.bg_lo:
    add rax, 40
.bg_done:
    mov [_con_ansi2], rax
    lea rdi, [_con_buf]
    lea rsi, [_con_fmt_color]
    mov rdx, [_con_ansi1]
    mov rcx, [_con_ansi2]
    xor eax, eax
    call sprintf
    lea rdi, [_con_fmt_s]
    lea rsi, [_con_buf]
    xor eax, eax
    call printf
    xor eax, eax
    add rsp, 40
    ret

; console_set_cursor(row=rdi, col=rsi)
_hw_console_set_cursor:
    sub rsp, 40
    mov rax, rdi
    mov [_con_row], rax
    inc rax
    mov [_con_ansi1], rax
    mov rax, rsi
    mov [_con_col], rax
    inc rax
    mov [_con_ansi2], rax
    lea rdi, [_con_buf]
    lea rsi, [_con_fmt_cursor]
    mov rdx, [_con_ansi1]
    mov rcx, [_con_ansi2]
    xor eax, eax
    call sprintf
    lea rdi, [_con_fmt_s]
    lea rsi, [_con_buf]
    xor eax, eax
    call printf
    xor eax, eax
    add rsp, 40
    ret

_hw_console_get_row:
    mov rax, [_con_row]
    ret
_hw_console_get_col:
    mov rax, [_con_col]
    ret
