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
    sub rsp, 48
    movsd xmm0, [flt_0]
    movsd [rbp-8], xmm0
    movsd xmm0, [rbp-8]
    movq rdx, xmm0
    lea rcx, [fmt_flt]
    call printf
    mov rcx, 10
    call putchar
    movsd xmm0, [rbp-8]
    sub rsp, 8
    movsd [rsp], xmm0
    mov rax, 2
    cvtsi2sd xmm0, rax
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    mulsd xmm0, xmm1
    movq rdx, xmm0
    lea rcx, [fmt_flt]
    call printf
    mov rcx, 10
    call putchar
    movsd xmm0, [rbp-8]
    sub rsp, 8
    movsd [rsp], xmm0
    mov rax, 2
    cvtsi2sd xmm0, rax
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    divsd xmm0, xmm1
    movq rdx, xmm0
    lea rcx, [fmt_flt]
    call printf
    mov rcx, 10
    call putchar
    movsd xmm0, [flt_1]
    sub rsp, 8
    movsd [rsp], xmm0
    movsd xmm0, [flt_2]
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    divsd xmm0, xmm1
    movq rdx, xmm0
    lea rcx, [fmt_flt]
    call printf
    mov rcx, 10
    call putchar
    lea rax, [str_0]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    movsd xmm0, [rbp-8]
    sub rsp, 8
    movsd [rsp], xmm0
    mov rax, 2
    cvtsi2sd xmm0, rax
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    mulsd xmm0, xmm1
    movq rdx, xmm0
    lea rcx, [fmt_flt]
    call printf
    mov rcx, 10
    call putchar
    mov rax, 3
    cvtsi2sd xmm0, rax
    sub rsp, 8
    movsd [rsp], xmm0
    movsd xmm0, [flt_3]
    sub rsp, 8
    movsd [rsp], xmm0
    mov rax, 12
    cvtsi2sd xmm0, rax
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    mulsd xmm0, xmm1
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    addsd xmm0, xmm1
    movsd [rbp-16], xmm0
    movsd xmm0, [rbp-16]
    movq rdx, xmm0
    lea rcx, [fmt_flt]
    call printf
    mov rcx, 10
    call putchar
    movsd xmm0, [flt_4]
    cvttsd2si rax, xmm0
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    mov rax, 1
    cvtsi2sd xmm0, rax
    sub rsp, 8
    movsd [rsp], xmm0
    mov rax, 2
    cvtsi2sd xmm0, rax
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    divsd xmm0, xmm1
    movq rdx, xmm0
    lea rcx, [fmt_flt]
    call printf
    mov rcx, 10
    call putchar
    movsd xmm0, [flt_5]
    sub rsp, 8
    movsd [rsp], xmm0
    movsd xmm0, [flt_3]
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    ucomisd xmm0, xmm1
    seta al
    movzx rax, al
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    movsd xmm0, [flt_6]
    sub rsp, 8
    movsd [rsp], xmm0
    movsd xmm0, [flt_6]
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    ucomisd xmm0, xmm1
    setb al
    movzx rax, al
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    movsd xmm0, [flt_7]
    movsd xmm1, [flt_6]
    subsd xmm1, xmm0
    movsd xmm0, xmm1
    sub rsp, 8
    movsd [rsp], xmm0
    movsd xmm0, [flt_7]
    movsd xmm1, [flt_6]
    subsd xmm1, xmm0
    movsd xmm0, xmm1
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    ucomisd xmm0, xmm1
    sete al
    movzx rax, al
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
str_0: db 112,105,42,50,32,61,32,0
flt_0: dq 3.14
flt_1: dq 5.0
flt_2: dq 2.0
flt_3: dq 0.5
flt_4: dq 3.7
flt_5: dq 1.5
flt_6: dq 0.0
flt_7: dq 1.0
