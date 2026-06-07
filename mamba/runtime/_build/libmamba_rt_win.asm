; mamba runtime library, target = WindowsCodegen
BITS 64
default rel
global main
extern printf
extern fputs
extern fputc
extern puts
extern putchar
extern strlen
extern strcmp
extern _strdup
extern _atoi64
extern atof
extern sprintf
extern fgets
extern exit
extern __acrt_iob_func
extern malloc
extern realloc
extern free
extern memset
extern fmod
global _runtime_dict_contains
global _runtime_dict_get
global _runtime_dict_get_default
global _runtime_dict_grow
global _runtime_dict_lookup_slot
global _runtime_dict_set
global _runtime_exc_msg
global _runtime_handler_top
global _runtime_hash_string
global _runtime_input
global _runtime_list_append
global _runtime_list_pop
global _runtime_longjmp
global _runtime_raise
global _runtime_setjmp
global _runtime_zalloc
global input_buf
global itoa_str_buf
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
section .text
_runtime_zalloc:
    push rbp
    mov rbp, rsp
    sub rsp, 32
    mov [rbp-8], rbx
    mov rax, rbx
    mov rcx, rax
    call malloc
    mov rbx, [rbp-8]
    mov rcx, rax
    xor rdx, rdx
    mov r8, rbx
    call memset
    leave
    ret
_runtime_hash_string:
    mov rcx, rax
    mov rax, 0xcbf29ce484222325
    mov r9, 0x100000001b3
._hs_loop:
    movzx rdx, byte [rcx]
    test rdx, rdx
    jz ._hs_done
    xor rax, rdx
    mul r9
    inc rcx
    jmp ._hs_loop
._hs_done:
    ret
_runtime_dict_lookup_slot:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rax
    mov [rbp-16], rbx
    mov qword [rbp-32], 0
    mov rax, rbx
    call _runtime_hash_string
    mov rcx, [rbp-8]
    mov rcx, [rcx+0]
    dec rcx
    and rax, rcx
    mov [rbp-24], rax
._dl_probe:
    mov r8, [rbp-8]
    mov r8, [r8+24]
    mov r9, [rbp-24]
    shl r9, 4
    add r8, r9
    mov r10, [r8]
    test r10, r10
    jz ._dl_empty
    cmp r10, 1
    jne ._dl_compare
    mov r11, [rbp-32]
    test r11, r11
    jnz ._dl_advance
    mov [rbp-32], r8
    jmp ._dl_advance
._dl_compare:
    mov [rbp-40], r8
    mov rax, r10
    mov rbx, [rbp-16]
    mov rcx, rax
    mov rdx, rbx
    call strcmp
    movsxd rax, eax
    test rax, rax
    jnz ._dl_advance
    mov rax, [rbp-40]
    xor rcx, rcx
    leave
    ret
._dl_advance:
    mov rax, [rbp-24]
    inc rax
    mov rcx, [rbp-8]
    mov rcx, [rcx+0]
    dec rcx
    and rax, rcx
    mov [rbp-24], rax
    jmp ._dl_probe
._dl_empty:
    xor rax, rax
    mov rcx, [rbp-32]
    test rcx, rcx
    jnz ._dl_ret_empty
    mov rcx, r8
._dl_ret_empty:
    leave
    ret
_runtime_dict_set:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rax
    mov [rbp-16], rbx
    mov [rbp-24], rcx
    mov rax, [rbp-8]
    mov rcx, [rax+8]
    add rcx, [rax+16]
    mov rdx, [rax+0]
    mov r9, rdx
    shr rdx, 2
    sub r9, rdx
    cmp rcx, r9
    jl ._ds_no_grow
    call _runtime_dict_grow
._ds_no_grow:
    mov rax, [rbp-8]
    mov rbx, [rbp-16]
    call _runtime_dict_lookup_slot
    test rax, rax
    jz ._ds_new
    mov rcx, [rbp-24]
    mov [rax+8], rcx
    leave
    ret
._ds_new:
    mov r8, [rcx]
    cmp r8, 1
    jne ._ds_no_tomb
    mov r9, [rbp-8]
    dec qword [r9+16]
._ds_no_tomb:
    mov [rbp-32], rcx
    mov rax, [rbp-16]
    mov rcx, rax
    call _strdup
    mov rcx, [rbp-32]
    mov [rcx], rax
    mov r9, [rbp-24]
    mov [rcx+8], r9
    mov r9, [rbp-8]
    inc qword [r9+8]
    leave
    ret
_runtime_dict_get:
    push rbp
    mov rbp, rsp
    sub rsp, 16
    call _runtime_dict_lookup_slot
    test rax, rax
    jnz ._dg_found
    lea rax, [_runtime_dict_key_error_msg]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rcx, 1
    call exit
._dg_found:
    mov rax, [rax+8]
    leave
    ret
_runtime_dict_get_default:
    push rbp
    mov rbp, rsp
    sub rsp, 16
    mov [rbp-8], rcx
    call _runtime_dict_lookup_slot
    test rax, rax
    jnz ._dgd_found
    mov rax, [rbp-8]
    leave
    ret
._dgd_found:
    mov rax, [rax+8]
    leave
    ret
_runtime_dict_contains:
    push rbp
    mov rbp, rsp
    sub rsp, 16
    call _runtime_dict_lookup_slot
    test rax, rax
    setne al
    movzx rax, al
    leave
    ret
_runtime_dict_grow:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rax
    mov rcx, [rax+0]
    mov [rbp-16], rcx
    mov rcx, [rax+24]
    mov [rbp-24], rcx
    mov rax, [rbp-16]
    shl rax, 1
    mov [rbp-32], rax
    mov rbx, rax
    shl rbx, 4
    call _runtime_zalloc
    mov [rbp-40], rax
    mov r8, [rbp-8]
    mov r9, [rbp-32]
    mov [r8+0], r9
    mov qword [r8+8], 0
    mov qword [r8+16], 0
    mov r9, [rbp-40]
    mov [r8+24], r9
    xor rcx, rcx
._gr_loop:
    cmp rcx, [rbp-16]
    jge ._gr_done
    mov r8, [rbp-24]
    mov rdx, rcx
    shl rdx, 4
    add r8, rdx
    mov r9, [r8]
    cmp r9, 1
    jbe ._gr_next
    mov [rbp-48], rcx
    mov rax, [rbp-8]
    mov rbx, r9
    call _runtime_dict_lookup_slot
    mov r8, [rbp-24]
    mov rdx, [rbp-48]
    shl rdx, 4
    add r8, rdx
    mov r9, [r8]
    mov r10, [r8+8]
    mov [rcx], r9
    mov [rcx+8], r10
    mov rdx, [rbp-8]
    inc qword [rdx+8]
    mov rcx, [rbp-48]
._gr_next:
    inc rcx
    jmp ._gr_loop
._gr_done:
    mov rax, [rbp-24]
    mov rcx, rax
    call free
    leave
    ret
section .rodata
_runtime_dict_key_error_msg: db "KeyError: key not in dict",10,0
section .bss
_runtime_handler_top: resq 1
_runtime_exc_msg:     resq 1
section .rodata
_runtime_unhandled_prefix: db "Unhandled exception: ",0
section .text
_runtime_setjmp:
    mov [rax+0],  rbx
    mov [rax+8],  rbp
    mov [rax+16], r12
    mov [rax+24], r13
    mov [rax+32], r14
    mov [rax+40], r15
    lea rcx, [rsp+8]
    mov [rax+48], rcx
    mov rcx, [rsp]
    mov [rax+56], rcx
    xor rax, rax
    ret
_runtime_longjmp:
    mov rcx, rax
    mov rax, rbx
    mov rbx, [rcx+0]
    mov rbp, [rcx+8]
    mov r12, [rcx+16]
    mov r13, [rcx+24]
    mov r14, [rcx+32]
    mov r15, [rcx+40]
    mov rsp, [rcx+48]
    jmp [rcx+56]
_runtime_raise:
    mov [rel _runtime_exc_msg], rax
    mov rax, [rel _runtime_handler_top]
    test rax, rax
    jnz ._rr_jump
    push rbp
    mov rbp, rsp
    sub rsp, 32
    lea rax, [rel _runtime_unhandled_prefix]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rax, [rel _runtime_exc_msg]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rcx, 10
    call putchar
    mov rcx, 1
    call exit
._rr_jump:
    mov rbx, 1
    call _runtime_longjmp
