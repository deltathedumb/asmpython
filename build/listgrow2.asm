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
extern sprintf
extern fgets
extern exit
extern __acrt_iob_func
extern malloc
extern realloc
extern free
section .text
main:
    push rbp
    mov rbp, rsp
    sub rsp, 176
    mov rcx, 24
    call malloc
    mov qword [rax+0], 4
    mov qword [rax+8], 0
    mov [rbp-16], rax
    mov rcx, 32
    call malloc
    mov rbx, [rbp-16]
    mov [rbx+16], rax
    mov rax, [rbp-16]
    mov [rbp-8], rax
    mov rax, 0
    mov [rbp-24], rax
    mov rax, 10
    mov [rbp-32], rax
    mov rax, 1
    mov [rbp-40], rax
.Lfor_1:
    mov rax, [rbp-40]
    test rax, rax
    jg .Lfor_step_pos_4
    mov rax, [rbp-24]
    mov rbx, [rbp-32]
    cmp rax, rbx
    jle .Lendfor_3
    jmp .Lfor_body_5
.Lfor_step_pos_4:
    mov rax, [rbp-24]
    mov rbx, [rbp-32]
    cmp rax, rbx
    jge .Lendfor_3
.Lfor_body_5:
    mov rax, [rbp-24]
    push rax
    mov rax, [rbp-8]
    pop rbx
    call _runtime_list_append
.Lfor_cont_2:
    mov rax, [rbp-24]
    add rax, [rbp-40]
    mov [rbp-24], rax
    jmp .Lfor_1
.Lendfor_3:
    mov rax, [rbp-8]
    mov [rbp-72], rax
    mov rbx, [rax+8]
    mov [rbp-56], rbx
    mov qword [rbp-64], 0
.Lfor_list_6:
    mov rax, [rbp-64]
    cmp rax, [rbp-56]
    jge .Lendfor_list_8
    mov rbx, [rbp-72]
    mov rbx, [rbx+16]
    mov rcx, [rbp-64]
    mov rax, [rbx+rcx*8]
    mov [rbp-48], rax
    mov rax, [rbp-48]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
.Lfor_list_cont_7:
    inc qword [rbp-64]
    jmp .Lfor_list_6
.Lendfor_list_8:
    lea rax, [str_0]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rcx, 32
    call putchar
    lea rax, [str_1]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rcx, 32
    call putchar
    mov rax, [rbp-8]
    mov rax, [rax+8]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    lea rax, [str_2]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rcx, 10
    call putchar
    mov rcx, 24
    call malloc
    mov qword [rax+0], 4
    mov qword [rax+8], 0
    mov [rbp-88], rax
    mov rcx, 32
    call malloc
    mov rbx, [rbp-88]
    mov [rbx+16], rax
    mov rax, [rbp-88]
    mov [rbp-80], rax
    mov rax, 0
    mov [rbp-24], rax
    mov rax, 5
    mov [rbp-96], rax
    mov rax, 1
    mov [rbp-104], rax
.Lfor_9:
    mov rax, [rbp-104]
    test rax, rax
    jg .Lfor_step_pos_12
    mov rax, [rbp-24]
    mov rbx, [rbp-96]
    cmp rax, rbx
    jle .Lendfor_11
    jmp .Lfor_body_13
.Lfor_step_pos_12:
    mov rax, [rbp-24]
    mov rbx, [rbp-96]
    cmp rax, rbx
    jge .Lendfor_11
.Lfor_body_13:
    mov rax, [rbp-24]
    push rax
    mov rax, [rbp-24]
    mov rbx, rax
    pop rax
    imul rax, rbx
    push rax
    mov rax, [rbp-80]
    pop rbx
    call _runtime_list_append
.Lfor_cont_10:
    mov rax, [rbp-24]
    add rax, [rbp-104]
    mov [rbp-24], rax
    jmp .Lfor_9
.Lendfor_11:
    mov rax, [rbp-80]
    mov [rbp-136], rax
    mov rbx, [rax+8]
    mov [rbp-120], rbx
    mov qword [rbp-128], 0
.Lfor_list_14:
    mov rax, [rbp-128]
    cmp rax, [rbp-120]
    jge .Lendfor_list_16
    mov rbx, [rbp-136]
    mov rbx, [rbx+16]
    mov rcx, [rbp-128]
    mov rax, [rbx+rcx*8]
    mov [rbp-112], rax
    mov rax, [rbp-112]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
.Lfor_list_cont_15:
    inc qword [rbp-128]
    jmp .Lfor_list_14
.Lendfor_list_16:
    xor rcx, rcx
    call exit
section .bss
itoa_str_buf: resb 32
input_buf:    resb 256
section .rdata
fmt_int:      db "%lld",0
fmt_str:      db "%s",0
fmt_int_only: db "%lld",0
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
    mov r8, rcx
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
section .rdata
str_0: db 108,101,110,0
str_1: db 61,0
str_2: db 115,113,117,97,114,101,115,58,0
