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
    sub rsp, 80
    movsd xmm0, [flt_0]
    movsd [rbp-8], xmm0
    movsd xmm0, [flt_1]
    movsd xmm1, xmm0
    movsd xmm0, [rbp-8]
    addsd xmm0, xmm1
    movsd [rbp-8], xmm0
    movsd xmm0, [rbp-8]
    movq rdx, xmm0
    lea rcx, [fmt_flt]
    call printf
    mov rcx, 10
    call putchar
    mov rax, 4
    cvtsi2sd xmm0, rax
    movsd xmm1, xmm0
    movsd xmm0, [rbp-8]
    addsd xmm0, xmm1
    movsd [rbp-8], xmm0
    movsd xmm0, [rbp-8]
    movq rdx, xmm0
    lea rcx, [fmt_flt]
    call printf
    mov rcx, 10
    call putchar
    movsd xmm0, [flt_2]
    movsd xmm1, xmm0
    movsd xmm0, [rbp-8]
    mulsd xmm0, xmm1
    movsd [rbp-8], xmm0
    movsd xmm0, [rbp-8]
    cvttsd2si rax, xmm0
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    movsd xmm0, [flt_3]
    sub rsp, 8
    movsd [rsp], xmm0
    mov rax, 2
    cvtsi2sd xmm0, rax
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    ucomisd xmm0, xmm1
    jae .Lcmp_false_3
    movsd xmm0, xmm1
    sub rsp, 8
    movsd [rsp], xmm0
    movsd xmm0, [flt_4]
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    ucomisd xmm0, xmm1
    jae .Lcmp_false_3
    movsd xmm0, xmm1
    mov rax, 1
    jmp .Lcmp_end_4
.Lcmp_false_3:
    xor rax, rax
.Lcmp_end_4:
    test rax, rax
    jz .Lelse_1
    lea rax, [str_0]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rcx, 10
    call putchar
    jmp .Lendif_2
.Lelse_1:
    lea rax, [str_1]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rcx, 10
    call putchar
.Lendif_2:
    mov rax, 0
    mov [rbp-16], rax
    mov rax, 0
    mov [rbp-24], rax
    mov rax, [rbp-16]
    push rax
    mov rax, 10
    mov rbx, rax
    pop rax
    add rax, rbx
    mov [rbp-16], rax
    mov rax, [rbp-24]
    push rax
    mov rax, 1
    mov rbx, rax
    pop rax
    add rax, rbx
    mov [rbp-24], rax
    mov rax, [rbp-16]
    push rax
    mov rax, 20
    mov rbx, rax
    pop rax
    add rax, rbx
    mov [rbp-16], rax
    mov rax, [rbp-24]
    push rax
    mov rax, 1
    mov rbx, rax
    pop rax
    add rax, rbx
    mov [rbp-24], rax
    mov rax, [rbp-16]
    push rax
    mov rax, 30
    mov rbx, rax
    pop rax
    add rax, rbx
    mov [rbp-16], rax
    mov rax, [rbp-24]
    push rax
    mov rax, 1
    mov rbx, rax
    pop rax
    add rax, rbx
    mov [rbp-24], rax
    mov rax, [rbp-16]
    cvtsi2sd xmm0, rax
    sub rsp, 8
    movsd [rsp], xmm0
    mov rax, [rbp-24]
    cvtsi2sd xmm0, rax
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    divsd xmm0, xmm1
    movsd [rbp-32], xmm0
    lea rax, [str_2]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rcx, 32
    call putchar
    lea rax, [str_3]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rcx, 32
    call putchar
    movsd xmm0, [rbp-32]
    sub rsp, 8
    movsd [rsp], xmm0
    mov rax, 5
    cvtsi2sd xmm0, rax
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    addsd xmm0, xmm1
    cvttsd2si rax, xmm0
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    movsd xmm0, [flt_4]
    movsd xmm1, [flt_0]
    subsd xmm1, xmm0
    movsd xmm0, xmm1
    sub rsp, 8
    movsd [rsp], xmm0
    movsd xmm0, [flt_5]
    movsd xmm1, xmm0
    movsd xmm0, [rsp]
    add rsp, 8
    divsd xmm0, xmm1
    roundsd xmm0, xmm0, 1
    cvttsd2si rax, xmm0
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    movsd xmm0, [flt_4]
    movq r8, xmm0
    lea rdx, [fmt_flt_only]
    lea rcx, [itoa_str_buf]
    call sprintf
    lea rax, [itoa_str_buf]
    mov [rbp-40], rax
    mov rax, [rbp-40]
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
str_0: db 49,46,53,32,60,32,50,32,60,32,50,46,53,0
str_1: db 110,111,112,101,0
str_2: db 97,118,103,0
str_3: db 61,0
flt_0: dq 0.0
flt_1: dq 5.0
flt_2: dq 1.2222222
flt_3: dq 1.5
flt_4: dq 2.5
flt_5: dq 1.0
