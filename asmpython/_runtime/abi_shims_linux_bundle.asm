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

global LoadLibraryA
global GetProcAddress

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
