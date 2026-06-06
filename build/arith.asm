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
    mov rax, 1
    mov [rbp-8], rax
.Lwhile_1:
    mov rax, [rbp-8]
    push rax
    mov rax, 8
    mov rbx, rax
    pop rax
    cmp rax, rbx
    setle al
    movzx rax, al
    test rax, rax
    jz .Lendwhile_2
    mov rax, [rbp-8]
    push rax
    pop rcx
    call fact
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
    lea rcx, [fmt_str]
    lea rdx, [str_1]
    call printf
    mov rax, 42
    neg rax
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rax, 17
    push rax
    mov rax, 5
    mov rbx, rax
    pop rax
    cqo
    idiv rbx
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rax, 17
    push rax
    mov rax, 5
    mov rbx, rax
    pop rax
    cqo
    idiv rbx
    mov rax, rdx
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    lea rcx, [fmt_str]
    lea rdx, [str_2]
    call printf
    mov rax, 3
    push rax
    mov rax, 5
    mov rbx, rax
    pop rax
    cmp rax, rbx
    setl al
    movzx rax, al
    test rax, rax
    jz .Lbool_end_3
    mov rax, 10
    push rax
    mov rax, 2
    mov rbx, rax
    pop rax
    cmp rax, rbx
    setg al
    movzx rax, al
.Lbool_end_3:
    test rax, rax
    setne al
    movzx rax, al
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rax, 3
    push rax
    mov rax, 5
    mov rbx, rax
    pop rax
    cmp rax, rbx
    setl al
    movzx rax, al
    test rax, rax
    jz .Lbool_end_4
    mov rax, 10
    push rax
    mov rax, 2
    mov rbx, rax
    pop rax
    cmp rax, rbx
    setl al
    movzx rax, al
.Lbool_end_4:
    test rax, rax
    setne al
    movzx rax, al
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rax, 3
    push rax
    mov rax, 5
    mov rbx, rax
    pop rax
    cmp rax, rbx
    setg al
    movzx rax, al
    test rax, rax
    jnz .Lbool_end_5
    mov rax, 10
    push rax
    mov rax, 2
    mov rbx, rax
    pop rax
    cmp rax, rbx
    setg al
    movzx rax, al
.Lbool_end_5:
    test rax, rax
    setne al
    movzx rax, al
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    xor rcx, rcx
    call exit
fact:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rcx
    mov rax, [rbp-8]
    push rax
    mov rax, 1
    mov rbx, rax
    pop rax
    cmp rax, rbx
    setle al
    movzx rax, al
    test rax, rax
    jz .Lelse_7
    mov rax, 1
    jmp .Lret_fact_6
    jmp .Lendif_8
.Lelse_7:
.Lendif_8:
    mov rax, [rbp-8]
    push rax
    mov rax, [rbp-8]
    push rax
    mov rax, 1
    mov rbx, rax
    pop rax
    sub rax, rbx
    push rax
    pop rcx
    call fact
    mov rbx, rax
    pop rax
    imul rax, rbx
    jmp .Lret_fact_6
    xor rax, rax
.Lret_fact_6:
    mov rsp, rbp
    pop rbp
    ret
section .rdata
fmt_int: db "%lld",10,0
fmt_str: db "%s",10,0
section .rdata
str_0: db 102,97,99,116,111,114,105,97,108,115,58,0
str_0_len: equ $-str_0-1
str_1: db 110,101,103,97,116,105,118,101,115,32,43,32,109,111,100,117,108,111,58,0
str_1_len: equ $-str_1-1
str_2: db 98,111,111,108,101,97,110,115,32,40,49,61,84,114,117,101,44,32,48,61,70,97,108,115,101,41,58,0
str_2_len: equ $-str_2-1
