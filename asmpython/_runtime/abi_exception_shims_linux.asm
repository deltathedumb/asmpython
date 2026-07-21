; SysV exception ABI shims for the built-in x86-64 backend.
;
; The runtime helpers use asmpython's internal rax/rbx convention, while the
; IR backend emits ordinary SysV calls with arguments in rdi/rsi.

extern _runtime_setjmp
extern _runtime_raise

global _abi_setjmp
global _abi_raise

section .text

; setjmp(buffer=rdi) -> runtime result
_abi_setjmp:
    mov rax, rdi
    jmp _runtime_setjmp

; raise(message=rdi, exception_type_id=rsi) -> does not normally return
_abi_raise:
    mov rax, rdi
    mov rbx, rsi
    jmp _runtime_raise
