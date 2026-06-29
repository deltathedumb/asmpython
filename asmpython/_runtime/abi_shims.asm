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
extern _runtime_str_concat
extern _runtime_zalloc
extern _runtime_list_append
extern _runtime_list_pop
extern _runtime_str_upper
extern _runtime_str_lower
extern _runtime_str_strip
extern _runtime_str_isdigit
extern _runtime_str_index_of
extern _runtime_str_replace
extern _runtime_str_split
extern _runtime_str_join
extern _runtime_str_zfill
extern _runtime_str_starts_with
extern _runtime_str_ends_with
extern _runtime_str_count
extern malloc
extern printf
extern sprintf
extern putchar

global _abi_dict_get_default
global _abi_dict_set
global _abi_dict_contains
global _abi_str_concat
global _abi_new_instance
global _abi_new_list
global _abi_list_append
global _abi_list_pop
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
    push rbx
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_dict_get_default
    pop rbx
    ret

; dict_set(dict=rcx, key=rdx, value=r8) -> void (rax undefined)
_abi_dict_set:
    push rbx
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_dict_set
    pop rbx
    ret

; rax = dict_contains(dict=rcx, key=rdx)
_abi_dict_contains:
    push rbx
    mov rax, rcx
    mov rbx, rdx
    call _runtime_dict_contains
    pop rbx
    ret

; rax = str_concat(left=rcx, right=rdx)
_abi_str_concat:
    push rbx
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_concat
    pop rbx
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
    push rbx
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
    pop rbx
    ret

; rax = new list with initial capacity cap=rcx (elements; clamped to >=1
; to avoid zalloc(0)). Mirrors _abi_new_instance's pattern: LIST_HEADER is
; 24 bytes (cap@0, len@8, buf@16) vs DICT_HEADER's 40, and there's only
; one zalloc (the element buffer) instead of two.
_abi_new_list:
    push rbx
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
    pop rbx
    ret

; list_append(list=rcx, value=rdx) -> void
_abi_list_append:
    push rbx
    mov rax, rcx
    mov rbx, rdx
    call _runtime_list_append
    pop rbx
    ret

; rax = list_pop(list=rcx) -- pops and returns the last element. No
; underflow check (matches codegen.py's own list.pop() -- an empty-list
; pop reads/decrements garbage, same pre-existing behavior, not new here).
_abi_list_pop:
    mov rax, rcx
    call _runtime_list_pop
    ret

; ---- str methods: one-arg (self only) helpers, rax=self -> rax=result.
_abi_str_upper:
    mov rax, rcx
    call _runtime_str_upper
    ret
_abi_str_lower:
    mov rax, rcx
    call _runtime_str_lower
    ret
_abi_str_strip:
    mov rax, rcx
    call _runtime_str_strip
    ret
_abi_str_isdigit:
    mov rax, rcx
    call _runtime_str_isdigit
    ret

; ---- str methods: two-arg (self, arg2) helpers, rax=self/rbx=arg2 ->
; rax=result. RBX saved/restored same as every other 2-arg shim above.
_abi_str_index_of:
    push rbx
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_index_of
    pop rbx
    ret
_abi_str_split:
    push rbx
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
    pop rbx
    ret
_abi_str_join:
    push rbx
    mov rax, rcx                  ; self (the separator string)
    mov rbx, rdx                  ; the list to join
    call _runtime_str_join
    pop rbx
    ret
_abi_str_zfill:
    push rbx
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_zfill
    pop rbx
    ret
_abi_str_starts_with:
    push rbx
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_starts_with
    pop rbx
    ret
_abi_str_ends_with:
    push rbx
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_ends_with
    pop rbx
    ret
_abi_str_count:
    push rbx
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_count
    pop rbx
    ret

; rax = str_replace(self=rcx, old=rdx, new=r8) -> result
_abi_str_replace:
    push rbx
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_str_replace
    pop rbx
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

section .rodata
_con_fmt_clear:  db 27, "[2J", 27, "[H", 0
_con_fmt_color:  db 27, "[%dm", 27, "[%dm", 0
_con_fmt_cursor: db 27, "[%d;%dH", 0
_con_fmt_s:      db "%s", 0

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
