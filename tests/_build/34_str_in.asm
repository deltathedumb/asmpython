; serpent generated for target = WindowsCodegen
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
extern strstr
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
extern memcpy
extern fmod
section .text
main:
    push rbp
    mov rbp, rsp
    sub rsp, 96
    lea rax, [str_0]
    mov [rbp-8], rax
    lea rax, [str_1]
    mov rbx, [rbp-8]
    call _runtime_str_contains
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    lea rax, [str_2]
    mov [rbp-16], rax
    lea rax, [str_3]
    mov rbx, [rbp-16]
    call _runtime_str_contains
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    lea rax, [str_4]
    mov [rbp-24], rax
    lea rax, [str_5]
    mov rbx, [rbp-24]
    call _runtime_str_contains
    xor rax, 1
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    lea rax, [str_6]
    mov [rbp-32], rax
    lea rax, [str_7]
    mov rbx, [rbp-32]
    call _runtime_str_contains
    xor rax, 1
    mov rdx, rax
    lea rcx, [fmt_int]
    call printf
    mov rcx, 10
    call putchar
    lea rax, [str_8]
    mov [rbp-40], rax
    lea rax, [str_9]
    mov [rbp-48], rax
    mov rax, [rbp-40]
    mov rbx, [rbp-48]
    call _runtime_str_contains
    test rax, rax
    jz .Lelse_1
    lea rax, [str_10]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rcx, 10
    call putchar
    jmp .Lendif_2
.Lelse_1:
    lea rax, [str_11]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rcx, 10
    call putchar
.Lendif_2:
    lea rax, [str_12]
    mov [rbp-56], rax
    mov rax, [rbp-40]
    mov rbx, [rbp-56]
    call _runtime_str_contains
    test rax, rax
    jz .Lelse_3
    lea rax, [str_13]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rcx, 10
    call putchar
    jmp .Lendif_4
.Lelse_3:
    lea rax, [str_14]
    mov rdx, rax
    lea rcx, [fmt_str]
    call printf
    mov rcx, 10
    call putchar
.Lendif_4:
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
section .text
_runtime_str_concat:
    push rbp
    mov rbp, rsp
    sub rsp, 96
    mov [rbp-8], rax
    mov [rbp-16], rbx
    mov rcx, rax
    call strlen
    mov [rbp-24], rax
    mov rax, [rbp-16]
    mov rcx, rax
    call strlen
    mov [rbp-32], rax
    mov rax, [rbp-24]
    add rax, [rbp-32]
    inc rax
    mov rcx, rax
    call malloc
    mov [rbp-40], rax
    mov rax, rax
    mov rbx, [rbp-8]
    mov rcx, [rbp-24]
    mov r8, rcx
    mov rdx, rbx
    mov rcx, rax
    call memcpy
    mov rax, [rbp-40]
    add rax, [rbp-24]
    mov rbx, [rbp-16]
    mov rcx, [rbp-32]
    mov r8, rcx
    mov rdx, rbx
    mov rcx, rax
    call memcpy
    mov rax, [rbp-40]
    mov rbx, [rbp-24]
    add rbx, [rbp-32]
    mov byte [rax+rbx], 0
    leave
    ret
_runtime_str_repeat:
    push rbp
    mov rbp, rsp
    sub rsp, 96
    mov [rbp-8], rax
    mov [rbp-16], rbx
    mov rax, [rbp-16]
    test rax, rax
    jg ._sr_compute_len
    mov rax, 1
    mov rcx, rax
    call malloc
    mov byte [rax], 0
    leave
    ret
._sr_compute_len:
    mov rax, [rbp-8]
    mov rcx, rax
    call strlen
    mov [rbp-24], rax
    mov rax, [rbp-24]
    mov rbx, [rbp-16]
    imul rax, rbx
    inc rax
    mov rcx, rax
    call malloc
    mov [rbp-32], rax
    mov qword [rbp-40], 0
._sr_loop:
    mov rax, [rbp-40]
    cmp rax, [rbp-16]
    jge ._sr_done
    mov rax, [rbp-32]
    mov rcx, [rbp-40]
    imul rcx, [rbp-24]
    add rax, rcx
    mov rbx, [rbp-8]
    mov rcx, [rbp-24]
    mov r8, rcx
    mov rdx, rbx
    mov rcx, rax
    call memcpy
    inc qword [rbp-40]
    jmp ._sr_loop
._sr_done:
    mov rax, [rbp-32]
    mov rcx, [rbp-16]
    imul rcx, [rbp-24]
    mov byte [rax+rcx], 0
    leave
    ret
_runtime_str_eq:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov rcx, rax
    mov rdx, rbx
    call strcmp
    movsxd rax, eax
    test rax, rax
    sete al
    movzx rax, al
    leave
    ret
_runtime_str_cmp:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov rcx, rax
    mov rdx, rbx
    call strcmp
    movsxd rax, eax
    test rax, rax
    jz ._sc_zero
    js ._sc_neg
    mov rax, 1
    jmp ._sc_done
._sc_zero:
    xor rax, rax
    jmp ._sc_done
._sc_neg:
    mov rax, -1
._sc_done:
    leave
    ret
_runtime_str_char_at:
    push rbp
    mov rbp, rsp
    sub rsp, 64
    mov [rbp-8], rax
    mov [rbp-16], rbx
    mov rcx, rax
    call strlen
    mov [rbp-24], rax
    mov rax, [rbp-16]
    test rax, rax
    jns ._sca_check
    add rax, [rbp-24]
    mov [rbp-16], rax
._sca_check:
    mov rax, [rbp-16]
    test rax, rax
    js ._sca_oob
    cmp rax, [rbp-24]
    jge ._sca_oob
    mov rax, 2
    mov rcx, rax
    call malloc
    mov rbx, [rbp-8]
    mov rcx, [rbp-16]
    mov dl, [rbx+rcx]
    mov [rax], dl
    mov byte [rax+1], 0
    leave
    ret
._sca_oob:
    lea rax, [rel _runtime_str_oob_msg]
    call _runtime_raise
    leave
    ret
_runtime_str_slice:
    push rbp
    mov rbp, rsp
    sub rsp, 96
    mov [rbp-8], rax
    mov [rbp-16], rbx
    mov [rbp-24], rcx
    mov rcx, rax
    call strlen
    mov [rbp-32], rax
    mov rax, [rbp-16]
    test rax, rax
    jns ._sl_start_pos
    add rax, [rbp-32]
._sl_start_pos:
    test rax, rax
    jns ._sl_start_ok
    xor rax, rax
._sl_start_ok:
    cmp rax, [rbp-32]
    jle ._sl_start_done
    mov rax, [rbp-32]
._sl_start_done:
    mov [rbp-16], rax
    mov rax, [rbp-24]
    test rax, rax
    jns ._sl_stop_pos
    add rax, [rbp-32]
._sl_stop_pos:
    test rax, rax
    jns ._sl_stop_ok
    xor rax, rax
._sl_stop_ok:
    cmp rax, [rbp-32]
    jle ._sl_stop_done
    mov rax, [rbp-32]
._sl_stop_done:
    mov [rbp-24], rax
    mov rax, [rbp-24]
    sub rax, [rbp-16]
    test rax, rax
    jg ._sl_alloc
    mov rax, 1
    mov rcx, rax
    call malloc
    mov byte [rax], 0
    leave
    ret
._sl_alloc:
    mov [rbp-40], rax
    inc rax
    mov rcx, rax
    call malloc
    mov [rbp-48], rax
    mov rax, [rbp-48]
    mov rbx, [rbp-8]
    add rbx, [rbp-16]
    mov rcx, [rbp-40]
    mov r8, rcx
    mov rdx, rbx
    mov rcx, rax
    call memcpy
    mov rax, [rbp-48]
    mov rcx, [rbp-40]
    mov byte [rax+rcx], 0
    leave
    ret
_runtime_str_contains:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov rdx, rbx
    mov rcx, rax
    call strstr
    test rax, rax
    setne al
    movzx rax, al
    leave
    ret
_runtime_str_index_of:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rax
    mov rdx, rbx
    mov rcx, rax
    call strstr
    test rax, rax
    jz ._sio_notfound
    sub rax, [rbp-8]
    leave
    ret
._sio_notfound:
    mov rax, -1
    leave
    ret
_runtime_str_count:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rax
    mov [rbp-16], rbx
    xor rcx, rcx
    mov [rbp-24], rcx
    mov rax, [rbp-16]
    mov rcx, rax
    call strlen
    mov [rbp-32], rax
    test rax, rax
    jz ._sco_done
._sco_loop:
    mov rax, [rbp-8]
    mov rbx, [rbp-16]
    mov rdx, rbx
    mov rcx, rax
    call strstr
    test rax, rax
    jz ._sco_done
    mov rcx, [rbp-24]
    inc rcx
    mov [rbp-24], rcx
    add rax, [rbp-32]
    mov [rbp-8], rax
    jmp ._sco_loop
._sco_done:
    mov rax, [rbp-24]
    leave
    ret
_runtime_str_starts_with:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rax
    mov [rbp-16], rbx
    mov rax, [rbp-16]
    mov rcx, rax
    call strlen
    mov [rbp-24], rax
    mov rax, [rbp-8]
    mov rcx, rax
    call strlen
    cmp rax, [rbp-24]
    jl ._ssw_no
    mov rax, [rbp-8]
    mov rbx, [rbp-16]
    mov rcx, [rbp-24]
    xor rdx, rdx
._ssw_loop:
    test rcx, rcx
    jz ._ssw_yes
    mov dl, [rax]
    cmp dl, [rbx]
    jne ._ssw_no
    inc rax
    inc rbx
    dec rcx
    jmp ._ssw_loop
._ssw_yes:
    mov rax, 1
    leave
    ret
._ssw_no:
    xor rax, rax
    leave
    ret
_runtime_str_ends_with:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rax
    mov [rbp-16], rbx
    mov rax, [rbp-16]
    mov rcx, rax
    call strlen
    mov [rbp-24], rax
    mov rax, [rbp-8]
    mov rcx, rax
    call strlen
    mov [rbp-32], rax
    cmp rax, [rbp-24]
    jl ._sew_no
    mov rax, [rbp-32]
    sub rax, [rbp-24]
    add rax, [rbp-8]
    mov rbx, [rbp-16]
    mov rcx, [rbp-24]
    xor rdx, rdx
._sew_loop:
    test rcx, rcx
    jz ._sew_yes
    mov dl, [rax]
    cmp dl, [rbx]
    jne ._sew_no
    inc rax
    inc rbx
    dec rcx
    jmp ._sew_loop
._sew_yes:
    mov rax, 1
    leave
    ret
._sew_no:
    xor rax, rax
    leave
    ret
_runtime_str_upper:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rax
    mov rcx, rax
    call strlen
    mov [rbp-16], rax
    inc rax
    mov rcx, rax
    call malloc
    mov [rbp-24], rax
    mov rcx, [rbp-16]
    mov rsi, [rbp-8]
    mov rdi, [rbp-24]
    xor rdx, rdx
._sup_loop:
    test rcx, rcx
    jz ._sup_done
    mov dl, [rsi]
    cmp dl, 97
    jl ._sup_keep
    cmp dl, 122
    jg ._sup_keep
    sub dl, 32
._sup_keep:
    mov [rdi], dl
    inc rsi
    inc rdi
    dec rcx
    jmp ._sup_loop
._sup_done:
    mov byte [rdi], 0
    mov rax, [rbp-24]
    leave
    ret
_runtime_str_lower:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rax
    mov rcx, rax
    call strlen
    mov [rbp-16], rax
    inc rax
    mov rcx, rax
    call malloc
    mov [rbp-24], rax
    mov rcx, [rbp-16]
    mov rsi, [rbp-8]
    mov rdi, [rbp-24]
    xor rdx, rdx
._slo_loop:
    test rcx, rcx
    jz ._slo_done
    mov dl, [rsi]
    cmp dl, 65
    jl ._slo_keep
    cmp dl, 90
    jg ._slo_keep
    add dl, 32
._slo_keep:
    mov [rdi], dl
    inc rsi
    inc rdi
    dec rcx
    jmp ._slo_loop
._slo_done:
    mov byte [rdi], 0
    mov rax, [rbp-24]
    leave
    ret
_runtime_str_lstrip:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rax
    mov rsi, [rbp-8]
._slst_skip:
    mov dl, [rsi]
    cmp dl, 32
    je ._slst_adv
    cmp dl, 9
    je ._slst_adv
    cmp dl, 10
    je ._slst_adv
    cmp dl, 13
    je ._slst_adv
    jmp ._slst_copy
._slst_adv:
    inc rsi
    jmp ._slst_skip
._slst_copy:
    mov [rbp-16], rsi
    mov rax, rsi
    mov rcx, rax
    call strlen
    mov [rbp-24], rax
    inc rax
    mov rcx, rax
    call malloc
    mov [rbp-32], rax
    mov rbx, [rbp-16]
    mov rcx, [rbp-24]
    mov r8, rcx
    mov rdx, rbx
    mov rcx, rax
    call memcpy
    mov rax, [rbp-32]
    mov rcx, [rbp-24]
    mov byte [rax+rcx], 0
    leave
    ret
_runtime_str_rstrip:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rax
    mov rcx, rax
    call strlen
    mov [rbp-16], rax
._srst_back:
    mov rcx, [rbp-16]
    test rcx, rcx
    jz ._srst_alloc
    mov rsi, [rbp-8]
    dec rcx
    mov dl, [rsi+rcx]
    cmp dl, 32
    je ._srst_dec
    cmp dl, 9
    je ._srst_dec
    cmp dl, 10
    je ._srst_dec
    cmp dl, 13
    je ._srst_dec
    jmp ._srst_alloc
._srst_dec:
    mov [rbp-16], rcx
    jmp ._srst_back
._srst_alloc:
    mov rax, [rbp-16]
    inc rax
    mov rcx, rax
    call malloc
    mov [rbp-24], rax
    mov rbx, [rbp-8]
    mov rcx, [rbp-16]
    mov r8, rcx
    mov rdx, rbx
    mov rcx, rax
    call memcpy
    mov rax, [rbp-24]
    mov rcx, [rbp-16]
    mov byte [rax+rcx], 0
    leave
    ret
_runtime_str_strip:
    push rbp
    mov rbp, rsp
    sub rsp, 32
    call _runtime_str_rstrip
    call _runtime_str_lstrip
    leave
    ret
_runtime_str_replace:
    push rbp
    mov rbp, rsp
    sub rsp, 96
    mov [rbp-8], rax
    mov [rbp-16], rbx
    mov [rbp-24], rcx
    mov rax, [rbp-8]
    mov rcx, rax
    call strlen
    mov [rbp-32], rax
    mov rax, [rbp-16]
    mov rcx, rax
    call strlen
    mov [rbp-40], rax
    test rax, rax
    jz ._srep_dup
    mov rax, [rbp-24]
    mov rcx, rax
    call strlen
    mov [rbp-48], rax
    mov rax, [rbp-8]
    mov rbx, [rbp-16]
    call _runtime_str_count
    mov [rbp-56], rax
    mov rax, [rbp-48]
    sub rax, [rbp-40]
    imul rax, [rbp-56]
    add rax, [rbp-32]
    mov [rbp-64], rax
    inc rax
    mov rcx, rax
    call malloc
    mov [rbp-72], rax
    mov rax, [rbp-8]
    mov [rbp-80], rax
    mov rax, [rbp-72]
    mov [rbp-88], rax
._srep_loop:
    mov rax, [rbp-80]
    mov rbx, [rbp-16]
    mov rdx, rbx
    mov rcx, rax
    call strstr
    test rax, rax
    jz ._srep_tail
    mov [rbp-96], rax
    sub rax, [rbp-80]
    test rax, rax
    jz ._srep_no_chunk
    mov rcx, rax
    mov rbx, [rbp-80]
    mov rax, [rbp-88]
    mov r8, rcx
    mov rdx, rbx
    mov rcx, rax
    call memcpy
    mov rax, [rbp-96]
    sub rax, [rbp-80]
    add rax, [rbp-88]
    mov [rbp-88], rax
._srep_no_chunk:
    mov rax, [rbp-88]
    mov rbx, [rbp-24]
    mov rcx, [rbp-48]
    mov r8, rcx
    mov rdx, rbx
    mov rcx, rax
    call memcpy
    mov rax, [rbp-88]
    add rax, [rbp-48]
    mov [rbp-88], rax
    mov rax, [rbp-96]
    add rax, [rbp-40]
    mov [rbp-80], rax
    jmp ._srep_loop
._srep_tail:
    mov rax, [rbp-72]
    add rax, [rbp-64]
    sub rax, [rbp-88]
    test rax, rax
    jz ._srep_term
    mov rcx, rax
    mov rbx, [rbp-80]
    mov rax, [rbp-88]
    mov r8, rcx
    mov rdx, rbx
    mov rcx, rax
    call memcpy
._srep_term:
    mov rax, [rbp-72]
    mov rcx, [rbp-64]
    mov byte [rax+rcx], 0
    leave
    ret
._srep_dup:
    mov rax, [rbp-8]
    mov rcx, rax
    call _strdup
    leave
    ret
section .rodata
_runtime_str_oob_msg: db "string index out of range",0
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
section .rdata
str_0: db 102,111,111,0
str_1: db 102,111,111,98,97,114,0
str_2: db 98,97,122,0
str_3: db 102,111,111,98,97,114,0
str_4: db 102,111,111,0
str_5: db 102,111,111,98,97,114,0
str_6: db 98,97,122,0
str_7: db 102,111,111,98,97,114,0
str_8: db 104,101,108,108,111,32,119,111,114,108,100,0
str_9: db 119,111,114,108,100,0
str_10: db 109,97,116,99,104,0
str_11: db 110,111,0
str_12: db 120,121,122,0
str_13: db 121,101,115,0
str_14: db 110,111,0
