; Linux ABI shim bundle for the built-in x86-64 backend.
;
; The IR import_binary lowering intentionally uses the Win32-style neutral
; symbol names LoadLibraryA/GetProcAddress.  Windows resolves those names
; directly from kernel32.dll; Linux supplies ABI-compatible aliases here and
; forwards them to dlopen/dlsym.  Keeping the translation at the ABI boundary
; avoids baking a target choice into the shared IR.

%include "abi_shims_linux.asm"

extern dlopen
extern dlsym
extern _runtime_setjmp
extern _runtime_raise

global LoadLibraryA
global GetProcAddress
global _abi_setjmp
global _abi_raise

section .text

; void *LoadLibraryA(const char *path)
; SysV entry: rdi=path.  dlopen additionally needs mode in esi.
LoadLibraryA:
    mov esi, 2                  ; RTLD_NOW
    jmp dlopen

; void *GetProcAddress(void *handle, const char *name)
; SysV arguments already match dlsym exactly: rdi=handle, rsi=name.
GetProcAddress:
    jmp dlsym

; int _abi_setjmp(void *jmp_buf)
; SysV entry: rdi=buffer.  The runtime's internal convention takes it in rax.
_abi_setjmp:
    mov rax, rdi
    jmp _runtime_setjmp

; noreturn _abi_raise(char *message, int type_id)
; SysV entry: rdi=message, rsi=type id.  The runtime expects rax/rbx.
_abi_raise:
    mov rax, rdi
    mov rbx, rsi
    jmp _runtime_raise
