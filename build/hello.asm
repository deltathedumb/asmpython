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
    sub rsp, 32
    lea rcx, [fmt_str]
    lea rdx, [str_0]
    call printf
    xor rcx, rcx
    call exit
section .rdata
fmt_int: db "%lld",10,0
fmt_str: db "%s",10,0
section .rdata
str_0: db 104,101,108,108,111,44,32,119,111,114,108,100,0
str_0_len: equ $-str_0-1
