; compyle generated for target = LinuxCodegen
BITS 64
default rel
global _start
section .text
_start:
    xor rbp, rbp
    mov rbp, rsp
    mov rax, 1
    mov rdi, 1
    lea rsi, [str_0]
    mov rdx, 12
    syscall
    mov rax, 1
    mov rdi, 1
    lea rsi, [newline]
    mov rdx, 1
    syscall
    mov rax, 60
    xor rdi, rdi
    syscall
section .bss
itoa_buf: resb 24
section .data
newline: db 10
section .text
_compyle_print_int:
    push rbp
    mov rbp, rsp
    sub rsp, 32
    mov r9, rax
    lea rdi, [itoa_buf+23]
    mov rcx, 0
    test rax, rax
    jnz .nonzero
    mov byte [rdi], '0'
    inc rcx
    dec rdi
    jmp .done_digits
.nonzero:
    test rax, rax
    jns .loop
    neg rax
.loop:
    test rax, rax
    jz .done_digits
    xor rdx, rdx
    mov rbx, 10
    div rbx
    add dl, '0'
    mov [rdi], dl
    dec rdi
    inc rcx
    jmp .loop
.done_digits:
    test r9, r9
    jns .write
    mov byte [rdi], '-'
    dec rdi
    inc rcx
.write:
    inc rdi
    mov rsi, rdi
    mov rdx, rcx
    mov rax, 1
    mov rdi, 1
    syscall
    mov rax, 1
    mov rdi, 1
    lea rsi, [newline]
    mov rdx, 1
    syscall
    leave
    ret
section .rodata
str_0: db 104,101,108,108,111,44,32,119,111,114,108,100,0
str_0_len: equ $-str_0-1
