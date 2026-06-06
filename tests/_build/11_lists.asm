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
    sub rsp, 128
    mov rcx, 24
    call malloc
    mov qword [rax+0], 4
    mov qword [rax+8], 3
    mov [rbp-16], rax
    mov rcx, 32
    call malloc
    mov rbx, [rbp-16]
    mov [rbx+16], rax
    mov rax, 10
    mov rbx, [rbp-16]
    mov rcx, [rbx+16]
    mov [rcx+0], rax
    mov rax, 20
    mov rbx, [rbp-16]
    mov rcx, [rbx+16]
    mov [rcx+8], rax
    mov rax, 30
    mov rbx, [rbp-16]
    mov rcx, [rbx+16]
    mov [rcx+16], rax
    mov rax, [rbp-16]
    mov [rbp-8], rax
    mov rax, [rbp-8]
    mov rax, [rax+8]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    mov rax, [rbp-8]
    mov [rbp-48], rax
    mov rbx, [rax+8]
    mov [rbp-32], rbx
    mov qword [rbp-40], 0
.Lfor_list_1:
    mov rax, [rbp-40]
    cmp rax, [rbp-32]
    jge .Lendfor_list_3
    mov rbx, [rbp-48]
    mov rbx, [rbx+16]
    mov rcx, [rbp-40]
    mov rax, [rbx+rcx*8]
    mov [rbp-24], rax
    mov rax, [rbp-24]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
.Lfor_list_cont_2:
    inc qword [rbp-40]
    jmp .Lfor_list_1
.Lendfor_list_3:
    mov rax, 0
    push rax
    mov rax, 5
    push rax
    mov rax, [rbp-8]
    pop rbx
    pop rcx
    mov rax, [rax+16]
    mov [rax+rcx*8], rbx
    mov rax, 0
    push rax
    mov rax, [rbp-8]
    pop rcx
    mov rax, [rax+16]
    mov rax, [rax+rcx*8]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    mov rax, 99
    push rax
    mov rax, [rbp-8]
    pop rbx
    call _runtime_list_append
    mov rax, [rbp-8]
    call _runtime_list_pop
    mov [rbp-56], rax
    mov rax, [rbp-56]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    mov rax, 1
    push rax
    mov rax, [rbp-8]
    pop rcx
    mov rax, [rax+16]
    mov rax, [rax+rcx*8]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    mov rax, 2
    push rax
    mov rax, [rbp-8]
    pop rcx
    mov rax, [rax+16]
    mov rax, [rax+rcx*8]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    mov rax, 0
    mov [rbp-64], rax
    mov rax, [rbp-8]
    mov [rbp-88], rax
    mov rbx, [rax+8]
    mov [rbp-72], rbx
    mov qword [rbp-80], 0
.Lfor_list_4:
    mov rax, [rbp-80]
    cmp rax, [rbp-72]
    jge .Lendfor_list_6
    mov rbx, [rbp-88]
    mov rbx, [rbx+16]
    mov rcx, [rbp-80]
    mov rax, [rbx+rcx*8]
    mov [rbp-24], rax
    mov rax, [rbp-64]
    push rax
    mov rax, [rbp-24]
    mov rbx, rax
    pop rax
    add rax, rbx
    mov [rbp-64], rax
.Lfor_list_cont_5:
    inc qword [rbp-80]
    jmp .Lfor_list_4
.Lendfor_list_6:
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
    mov rax, [rbp-64]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
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
section .rdata
str_0: db 115,117,109,0
str_1: db 61,0
