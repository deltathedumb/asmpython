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
    sub rsp, 48
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
    mov rax, 1
    push rax
    mov rax, [rbp-8]
    pop rbx
    call _runtime_list_append
    mov rax, 2
    push rax
    mov rax, [rbp-8]
    pop rbx
    call _runtime_list_append
    mov rax, 3
    push rax
    mov rax, [rbp-8]
    pop rbx
    call _runtime_list_append
    mov rax, 4
    push rax
    mov rax, [rbp-8]
    pop rbx
    call _runtime_list_append
    mov rax, 5
    push rax
    mov rax, [rbp-8]
    pop rbx
    call _runtime_list_append
    mov rax, [rbp-8]
    mov rax, [rax+8]
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
