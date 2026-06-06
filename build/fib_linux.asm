; compyle generated for target = LinuxCodegen
BITS 64
default rel
global _start
section .text
_start:
    xor rbp, rbp
    mov rbp, rsp
    sub rsp, 16
    mov rax, 1
    mov rdi, 1
    lea rsi, [str_0]
    mov rdx, 9
    syscall
    mov rax, 1
    mov rdi, 1
    lea rsi, [newline]
    mov rdx, 1
    syscall
    mov rax, 10
    push rax
    pop rdi
    call fib
    call _compyle_print_int
    mov rax, 0
    mov [rbp-8], rax
.Lwhile_1:
    mov rax, [rbp-8]
    push rax
    mov rax, 5
    mov rbx, rax
    pop rax
    cmp rax, rbx
    setl al
    movzx rax, al
    test rax, rax
    jz .Lendwhile_2
    mov rax, [rbp-8]
    push rax
    mov rax, [rbp-8]
    mov rbx, rax
    pop rax
    imul rax, rbx
    call _compyle_print_int
    mov rax, [rbp-8]
    push rax
    mov rax, 1
    mov rbx, rax
    pop rax
    add rax, rbx
    mov [rbp-8], rax
    jmp .Lwhile_1
.Lendwhile_2:
    mov rax, 60
    xor rdi, rdi
    syscall
fib:
    push rbp
    mov rbp, rsp
    sub rsp, 16
    mov [rbp-8], rdi
    mov rax, [rbp-8]
    push rax
    mov rax, 2
    mov rbx, rax
    pop rax
    cmp rax, rbx
    setl al
    movzx rax, al
    test rax, rax
    jz .Lelse_4
    mov rax, [rbp-8]
    jmp .Lret_fib_3
    jmp .Lendif_5
.Lelse_4:
.Lendif_5:
    mov rax, [rbp-8]
    push rax
    mov rax, 1
    mov rbx, rax
    pop rax
    sub rax, rbx
    push rax
    pop rdi
    call fib
    push rax
    mov rax, [rbp-8]
    push rax
    mov rax, 2
    mov rbx, rax
    pop rax
    sub rax, rbx
    push rax
    pop rdi
    call fib
    mov rbx, rax
    pop rax
    add rax, rbx
    jmp .Lret_fib_3
    xor rax, rax
.Lret_fib_3:
    mov rsp, rbp
    pop rbp
    ret
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
str_0: db 102,105,98,40,49,48,41,32,61,0
str_0_len: equ $-str_0-1
