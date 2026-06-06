; compyle generated for target = WindowsCodegen
BITS 64
default rel
global main
extern printf
extern fputs
extern fputc
extern puts
extern putchar
extern strlen
extern _atoi64
extern atof
extern sprintf
extern fgets
extern exit
extern __acrt_iob_func
extern malloc
extern realloc
extern free
extern fmod
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
    mov rax, [rbp-8]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
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
    mov rcx, 10
    call putchar
.Lfor_cont_7:
    mov rax, [rbp-16]
    add rax, [rbp-48]
    mov [rbp-16], rax
    jmp .Lfor_6
.Lendfor_8:
    mov rax, 1
    mov [rbp-16], rax
    mov rax, 11
    mov [rbp-56], rax
    mov rax, 1
    mov [rbp-64], rax
.Lfor_11:
    mov rax, [rbp-64]
    test rax, rax
    jg .Lfor_step_pos_14
    mov rax, [rbp-16]
    mov rbx, [rbp-56]
    cmp rax, rbx
    jle .Lendfor_13
    jmp .Lfor_body_15
.Lfor_step_pos_14:
    mov rax, [rbp-16]
    mov rbx, [rbp-56]
    cmp rax, rbx
    jge .Lendfor_13
.Lfor_body_15:
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
    jz .Lelse_16
    jmp .Lfor_cont_12
    jmp .Lendif_17
.Lelse_16:
.Lendif_17:
    mov rax, [rbp-16]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
.Lfor_cont_12:
    mov rax, [rbp-16]
    add rax, [rbp-64]
    mov [rbp-16], rax
    jmp .Lfor_11
.Lendfor_13:
    xor rcx, rcx
    call exit
section .bss
itoa_str_buf: resb 32
input_buf:    resb 256
section .rdata
fmt_int:      db "%lld",0
fmt_str:      db "%s",0
fmt_int_only: db "%lld",0
fmt_flt:      db "%g",0
fmt_flt_only: db "%g",0
section .text
_runtime_input:
    push rbp
    mov rbp, rsp
    sub rsp, 32
    mov ecx, 0
    call __acrt_iob_func
    mov r8, rax
    mov edx, 255
    lea rcx, [input_buf]
    call fgets
    lea rcx, [input_buf]
    call strlen
    lea rdi, [input_buf]
    test rax, rax
    jz ._wi_done
    mov dl, [rdi+rax-1]
    cmp dl, 10
    jne ._wi_done
    dec rax
    mov byte [rdi+rax], 0
._wi_done:
    lea rax, [input_buf]
    leave
    ret
_runtime_list_append:
    push rbp
    mov rbp, rsp
    sub rsp, 64
    mov [rbp-8], rax
    mov [rbp-16], rbx
    mov rcx, [rax+8]
    cmp rcx, [rax]
    jl ._la_store
    mov rcx, [rax]
    shl rcx, 1
    cmp rcx, 4
    jge ._la_grow
    mov rcx, 4
._la_grow:
    mov [rbp-24], rcx
    shl rcx, 3
    mov rdx, rcx
    mov rax, [rbp-8]
    mov rcx, [rax+16]
    call realloc
    mov rbx, [rbp-8]
    mov [rbx+16], rax
    mov rdx, [rbp-24]
    mov [rbx], rdx
._la_store:
    mov rax, [rbp-8]
    mov rcx, [rax+8]
    mov rbx, [rbp-16]
    mov rdx, [rax+16]
    mov [rdx+rcx*8], rbx
    inc qword [rax+8]
    leave
    ret
_runtime_list_pop:
    mov rcx, [rax+8]
    dec rcx
    mov [rax+8], rcx
    mov rdx, [rax+16]
    mov rax, [rdx+rcx*8]
    ret
