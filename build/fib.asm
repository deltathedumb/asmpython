; compyle generated for target = WindowsCodegen
BITS 64
default rel
global main
extern printf
extern exit
section .text
main:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    lea rcx, [fmt_str]
    lea rdx, [str_0]
    call printf
    mov rax, 10
    push rax
    pop rcx
    call fib
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
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
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rax, [rbp-8]
    push rax
    mov rax, 1
    mov rbx, rax
    pop rax
    add rax, rbx
    mov [rbp-8], rax
    jmp .Lwhile_1
.Lendwhile_2:
    xor rcx, rcx
    call exit
fib:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rcx
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
    pop rcx
    call fib
    push rax
    mov rax, [rbp-8]
    push rax
    mov rax, 2
    mov rbx, rax
    pop rax
    sub rax, rbx
    push rax
    pop rcx
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
section .rdata
fmt_int: db "%lld",10,0
fmt_str: db "%s",10,0
section .rdata
str_0: db 102,105,98,40,49,48,41,32,61,0
str_0_len: equ $-str_0-1
