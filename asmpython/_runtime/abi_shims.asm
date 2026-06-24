; ABI shim layer: thin wrappers exposing asmpython's runtime helpers (which
; use codegen.py's own ad-hoc internal calling convention -- rax/rbx/rcx for
; most 2-3 arg helpers) under the standard Win64 ABI (rcx/rdx/r8), so the new
; uasm-backed SSA IR pipeline (whose `call` op always marshals args into the
; standard ABI registers) can call them directly. Zero changes to the
; existing, tested runtime internals -- one small wrapper per helper needed.
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
extern malloc

global _abi_dict_get_default
global _abi_dict_set
global _abi_dict_contains
global _abi_str_concat
global _abi_new_instance

section .text

; rax = dict_get_default(dict=rcx, key=rdx, default=r8)
_abi_dict_get_default:
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_dict_get_default
    ret

; dict_set(dict=rcx, key=rdx, value=r8) -> void (rax undefined)
_abi_dict_set:
    mov rax, rcx
    mov rbx, rdx
    mov rcx, r8
    call _runtime_dict_set
    ret

; rax = dict_contains(dict=rcx, key=rdx)
_abi_dict_contains:
    mov rax, rcx
    mov rbx, rdx
    call _runtime_dict_contains
    ret

; rax = str_concat(left=rcx, right=rdx)
_abi_str_concat:
    mov rax, rcx
    mov rbx, rdx
    call _runtime_str_concat
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
