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
    sub rsp, 64
    lea rax, [str_0]
    mov [rbp-8], rax
    mov rax, 42
    mov [rbp-16], rax
    lea rax, [str_1]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rax, [rbp-16]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    lea rax, [str_2]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rax, 1
    push rax
    mov rax, 2
    mov rbx, rax
    pop rax
    add rax, rbx
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    mov rax, 7
    mov [rbp-24], rax
    lea rax, [str_3]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rax, [rbp-8]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    lea rax, [str_4]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rax, [rbp-24]
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    lea rax, [str_5]
    mov rdx, rax
    lea rcx, [fmt_str]
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
str_0: db 67,108,97,117,100,101,0
str_1: db 120,32,61,32,0
str_2: db 49,32,43,32,50,32,61,32,0
str_3: db 110,97,109,101,61,0
str_4: db 44,32,110,61,0
str_5: db 98,114,97,99,101,115,58,32,123,111,107,125,0
