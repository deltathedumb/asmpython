; threading.* ABI shim layer (Win64) -- a SEPARATE object from abi_shims.asm,
; built and linked in ONLY when a program actually imports threading (see
; asmpython/_runtime/build.py's build_threading_shims and driver.py's
; conditional link-object list). Unlike everything in abi_shims.asm (which
; is shared unconditionally across every program build), _threading_
; trampoline here references _threading_bootstrap -- a real user-program-
; level Python function (stdlib/threading.py's Thread._bootstrap target)
; that only exists in programs that import threading. Declaring that
; extern in the always-linked abi_shims.asm broke EVERY program (an
; unresolved relocation at link time, regardless of whether the
; referencing code path ever runs) -- confirmed via a real build failure
; on a plain "hello world" program once threading shims were added there.
; This file exists specifically so that reference is only ever linked
; into a program that actually provides _threading_bootstrap.
;
; Ported directly from target_windows.py's legacy-backend inline shims
; (same symbol names, same algorithms/register conventions, all-kernel32
; externs already registered in pe_linker.py's _DLL_FOR_SYMBOL).
; stdlib/_threadingffi.py's Func bindings point straight at these C names.

BITS 64
default rel

extern malloc
extern CreateThread
extern WaitForSingleObject
extern CloseHandle
extern InitializeCriticalSection
extern EnterCriticalSection
extern LeaveCriticalSection
extern DeleteCriticalSection
extern GetCurrentThreadId
extern GetExitCodeThread
extern _threading_bootstrap

global _threading_trampoline
global _threading_create
global _threading_join
global _threading_is_alive
global _threading_get_ident
global _threading_active_count
global _threading_lock_init
global _threading_lock_acquire
global _threading_lock_release
global _threading_lock_destroy

section .text

; _threading_trampoline(rcx=thread_obj_ptr) -> rax=0
; Called by Win32 on the new thread; must match LPTHREAD_START_ROUTINE.
_threading_trampoline:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rcx
    test rcx, rcx
    jz ._tt_done
    call _threading_bootstrap
._tt_done:
    xor rax, rax
    leave
    ret

; _threading_create(rcx=thread_obj_ptr) -> rax=handle (HANDLE, 64-bit)
_threading_create:
    push rbp
    mov rbp, rsp
    sub rsp, 80
    mov [rbp-8], rcx
    xor rcx, rcx
    xor rdx, rdx
    lea r8, [rel _threading_trampoline]
    mov r9, [rbp-8]
    mov qword [rsp+32], 0
    mov qword [rsp+40], 0
    call CreateThread
    leave
    ret

; _threading_join(rcx=handle) -> rax=0
_threading_join:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rcx
    mov edx, 0xFFFFFFFF
    call WaitForSingleObject
    mov rcx, [rbp-8]
    call CloseHandle
    xor rax, rax
    leave
    ret

; _threading_is_alive(rcx=handle) -> rax: 1 if still running, 0 if done
_threading_is_alive:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov [rbp-8], rcx
    xor rdx, rdx
    mov qword [rbp-16], 0
    lea rdx, [rbp-16]
    call GetExitCodeThread
    mov rax, [rbp-16]
    cmp rax, 259
    sete al
    movzx rax, al
    leave
    ret

; _threading_get_ident() -> rax = thread id (DWORD, zero-extended)
_threading_get_ident:
    sub rsp, 40
    call GetCurrentThreadId
    mov eax, eax
    add rsp, 40
    ret

; _threading_active_count() -> rax (stub: always 1, no global tracking)
_threading_active_count:
    mov rax, 1
    ret

; _threading_lock_init(rcx=lock_obj_ptr) -> rax=cs_ptr
; Allocates a CRITICAL_SECTION (40 bytes on Win64), inits it, returns ptr.
_threading_lock_init:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    mov rcx, 40
    call malloc
    mov [rbp-8], rax
    mov rcx, rax
    call InitializeCriticalSection
    mov rax, [rbp-8]
    leave
    ret

; _threading_lock_acquire(rcx=cs_ptr) -> rax=1
_threading_lock_acquire:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    call EnterCriticalSection
    mov rax, 1
    leave
    ret

; _threading_lock_release(rcx=cs_ptr) -> rax=0
_threading_lock_release:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    call LeaveCriticalSection
    xor rax, rax
    leave
    ret

; _threading_lock_destroy(rcx=cs_ptr)
_threading_lock_destroy:
    push rbp
    mov rbp, rsp
    sub rsp, 48
    call DeleteCriticalSection
    leave
    ret
