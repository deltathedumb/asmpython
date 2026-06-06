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
    sub rsp, 96
    mov rax, 0
    mov [rbp-8], rax
    mov rax, 1
    mov [rbp-16], rax
    mov rax, 11
    mov [rbp-24], rax
    mov rax, 1
    mov [rbp-32], rax
.Lfor_1:
    mov rax, [rbp-32]
    test rax, rax
    jg .Lfor_step_pos_4
    mov rax, [rbp-16]
    mov rbx, [rbp-24]
    cmp rax, rbx
    jle .Lendfor_3
    jmp .Lfor_body_5
.Lfor_step_pos_4:
    mov rax, [rbp-16]
    mov rbx, [rbp-24]
    cmp rax, rbx
    jge .Lendfor_3
.Lfor_body_5:
    mov rax, [rbp-8]
    push rax
    mov rax, [rbp-16]
    mov rbx, rax
    pop rax
    add rax, rbx
    mov [rbp-8], rax
.Lfor_cont_2:
    mov rax, [rbp-16]
    add rax, [rbp-32]
    mov [rbp-16], rax
    jmp .Lfor_1
.Lendfor_3:
    lea rcx, [fmt_str]
    lea rdx, [str_0]
    call printf
    mov rax, [rbp-8]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    lea rcx, [fmt_str]
    lea rdx, [str_1]
    call printf
    mov rax, 5
    mov [rbp-16], rax
    mov rax, 0
    mov [rbp-40], rax
    mov rax, 1
    neg rax
    mov [rbp-48], rax
.Lfor_6:
    mov rax, [rbp-48]
    test rax, rax
    jg .Lfor_step_pos_9
    mov rax, [rbp-16]
    mov rbx, [rbp-40]
    cmp rax, rbx
    jle .Lendfor_8
    jmp .Lfor_body_10
.Lfor_step_pos_9:
    mov rax, [rbp-16]
    mov rbx, [rbp-40]
    cmp rax, rbx
    jge .Lendfor_8
.Lfor_body_10:
    mov rax, [rbp-16]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
.Lfor_cont_7:
    mov rax, [rbp-16]
    add rax, [rbp-48]
    mov [rbp-16], rax
    jmp .Lfor_6
.Lendfor_8:
    lea rcx, [fmt_str]
    lea rdx, [str_2]
    call printf
    mov rax, 7
    mov [rbp-16], rax
.Lwhile_11:
    mov rax, [rbp-16]
    push rax
    mov rax, 100
    mov rbx, rax
    pop rax
    cmp rax, rbx
    setl al
    movzx rax, al
    test rax, rax
    jz .Lendwhile_12
    mov rax, [rbp-16]
    push rax
    mov rax, 2
    mov rbx, rax
    pop rax
    cqo
    idiv rbx
    mov rax, rdx
    push rax
    mov rax, 0
    mov rbx, rax
    pop rax
    cmp rax, rbx
    sete al
    movzx rax, al
    test rax, rax
    jz .Lelse_13
    mov rax, [rbp-16]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    jmp .Lendwhile_12
    jmp .Lendif_14
.Lelse_13:
.Lendif_14:
    mov rax, [rbp-16]
    push rax
    mov rax, 1
    mov rbx, rax
    pop rax
    add rax, rbx
    mov [rbp-16], rax
    jmp .Lwhile_11
.Lendwhile_12:
    lea rcx, [fmt_str]
    lea rdx, [str_3]
    call printf
    mov rax, 1
    mov [rbp-16], rax
    mov rax, 11
    mov [rbp-56], rax
    mov rax, 1
    mov [rbp-64], rax
.Lfor_15:
    mov rax, [rbp-64]
    test rax, rax
    jg .Lfor_step_pos_18
    mov rax, [rbp-16]
    mov rbx, [rbp-56]
    cmp rax, rbx
    jle .Lendfor_17
    jmp .Lfor_body_19
.Lfor_step_pos_18:
    mov rax, [rbp-16]
    mov rbx, [rbp-56]
    cmp rax, rbx
    jge .Lendfor_17
.Lfor_body_19:
    mov rax, [rbp-16]
    push rax
    mov rax, 2
    mov rbx, rax
    pop rax
    cqo
    idiv rbx
    mov rax, rdx
    push rax
    mov rax, 0
    mov rbx, rax
    pop rax
    cmp rax, rbx
    sete al
    movzx rax, al
    test rax, rax
    jz .Lelse_20
    jmp .Lfor_cont_16
    jmp .Lendif_21
.Lelse_20:
.Lendif_21:
    mov rax, [rbp-16]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
.Lfor_cont_16:
    mov rax, [rbp-16]
    add rax, [rbp-64]
    mov [rbp-16], rax
    jmp .Lfor_15
.Lendfor_17:
    lea rcx, [fmt_str]
    lea rdx, [str_4]
    call printf
    mov rax, 0
    push rax
    mov rax, 5
    mov rbx, rax
    pop rax
    cmp rax, rbx
    jge .Lcmp_false_22
    mov rax, rbx
    push rax
    mov rax, 10
    mov rbx, rax
    pop rax
    cmp rax, rbx
    jge .Lcmp_false_22
    mov rax, rbx
    mov rax, 1
    jmp .Lcmp_end_23
.Lcmp_false_22:
    xor rax, rax
.Lcmp_end_23:
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    lea rcx, [fmt_str]
    lea rdx, [str_5]
    call printf
    mov rax, 0
    push rax
    mov rax, 5
    mov rbx, rax
    pop rax
    cmp rax, rbx
    jge .Lcmp_false_24
    mov rax, rbx
    push rax
    mov rax, 3
    mov rbx, rax
    pop rax
    cmp rax, rbx
    jge .Lcmp_false_24
    mov rax, rbx
    mov rax, 1
    jmp .Lcmp_end_25
.Lcmp_false_24:
    xor rax, rax
.Lcmp_end_25:
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    lea rcx, [fmt_str]
    lea rdx, [str_6]
    call printf
    mov rax, 12
    push rax
    mov rax, 10
    mov rbx, rax
    pop rax
    and rax, rbx
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rax, 12
    push rax
    mov rax, 10
    mov rbx, rax
    pop rax
    or rax, rbx
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rax, 12
    push rax
    mov rax, 10
    mov rbx, rax
    pop rax
    xor rax, rbx
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rax, 1
    push rax
    mov rax, 4
    mov rbx, rax
    pop rax
    mov rcx, rbx
    shl rax, cl
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rax, 256
    push rax
    mov rax, 3
    mov rbx, rax
    pop rax
    mov rcx, rbx
    sar rax, cl
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rax, 0
    not rax
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    xor rcx, rcx
    call exit
section .rdata
fmt_int: db "%lld",10,0
fmt_str: db "%s",10,0
section .rdata
str_0: db 115,117,109,32,49,46,46,49,48,32,61,0
str_0_len: equ $-str_0-1
str_1: db 99,111,117,110,116,100,111,119,110,58,0
str_1_len: equ $-str_1-1
str_2: db 102,105,114,115,116,32,101,118,101,110,32,62,61,32,55,58,0
str_2_len: equ $-str_2-1
str_3: db 115,107,105,112,32,101,118,101,110,115,32,49,46,46,49,48,58,0
str_3_len: equ $-str_3-1
str_4: db 99,104,97,105,110,101,100,58,32,48,32,60,32,53,32,60,32,49,48,32,45,62,0
str_4_len: equ $-str_4-1
str_5: db 99,104,97,105,110,101,100,58,32,48,32,60,32,53,32,60,32,51,32,45,62,0
str_5_len: equ $-str_5-1
str_6: db 98,105,116,119,105,115,101,58,0
str_6_len: equ $-str_6-1
