"""Linux ELF32 codegen (i386 cdecl, links against libc) -- the legacy
NASM-text-emitting Codegen system's own i386 target, parallel to (but
NOT a subclass of) LinuxCodegen.

Subclasses Codegen directly rather than LinuxCodegen: cdecl has no
register-argument-passing convention at all (every argument goes on
the stack, unlike SysV's rdi/rsi/rdx/rcx/r8/r9), so LinuxCodegen's own
_arg_reg/_int_arg_regs/SYSV_ARG_REGS machinery doesn't apply here in
spirit, only in the sense that both eventually answer "how do I get
argument i to the callee" -- following FreestandingCodegen's own
precedent (also a direct Codegen subclass, not a LinuxCodegen one) for
a target whose ABI differs enough from SysV that inheriting from
LinuxCodegen would mean overriding almost everything it defines anyway.

This file exists alongside, and is entirely independent from, the
newer IR-based backend at asmpython/_backends/x86/ (that backend's own
codegen.py emits machine-code BYTES directly from a shared SSA IR;
this file emits NASM TEXT from the legacy AST-walking Codegen base
class, exactly like LinuxCodegen/WindowsCodegen do for x86-64). The two
systems share no code, but this file's own i64-as-register-pair
arithmetic patterns are modeled on that backend's own already-verified
approach (see asmpython/_runtime/abi_shims_x86_32.asm's own docstring)
wherever the same problem (a 64-bit logical value on a 32-bit-register
architecture) comes up here too.

Design decision inherited from that same sibling backend: every
asmpython int/float stays a full 64-bit value in memory (8-byte stack
slots, 8-byte heap-object fields, matching LinuxCodegen's own
_cl_define/_cl_define_bytes conventions unchanged) -- only REGISTER-
level operations on such a value need redesigning as register-PAIR
operations, not the surrounding memory layout. This preserves Python's
int/float semantics exactly and keeps every heap-object-layout
constant (LIST_HEADER, DICT_HEADER, etc.) identical to the x86-64
version, at the cost of every register-literal-bearing runtime
primitive needing its actual instruction sequence redesigned from
first principles rather than a register-name substitution.
"""

from __future__ import annotations

from .codegen import Codegen, FuncInfo


class X86_32LinuxCodegen(Codegen):
    target_name = "X86_32LinuxCodegen"
    section_text = "section .text"
    section_data = "section .data"
    section_rodata = "section .rodata"
    label_main = "main"  # libc's _start calls main() here too, same as LinuxCodegen

    def emit_externs(self) -> None:
        self.emit("global main")
        for name in (
            "printf",
            "fputs",
            "fputc",
            "puts",
            "putchar",
            "strlen",
            "strcmp",
            "strstr",
            "strdup",
            "strtoll",
            "strtod",
            "atof",
            "sprintf",
            "fgets",
            "fopen",
            "fgetc",
            "fclose",
            "access",
            "stdin",
            "malloc",
            "realloc",
            "free",
            "memcpy",
            "memset",
            "exit",
            "fmod",
            "pow",
            "dlopen",
            "dlsym",
            # This target's own 64-bit-as-register-pair division helpers
            # (asmpython/_runtime/abi_shims_x86_32.asm), NOT a libc
            # function -- every runtime primitive doing 64-by-64 integer
            # division/remainder calls these instead of a bare IDIV/DIV
            # (which only ever does 64-by-32 on real hardware).
            "__udivdi64",
            "__divdi64",
            "__umoddi64",
            "__moddi64",
        ):
            self.emit(f"extern {name}")

    # cdecl has no register-argument-passing convention at all -- every
    # argument lives on the stack. Returning None unconditionally here
    # (rather than raising, the base class's own default) tells every
    # call site "argument i is not in a register," matching cdecl
    # exactly; _int_arg_regs() mirrors this with an empty list.
    def _arg_reg(self, i: int):
        return None

    def _int_arg_regs(self):
        return []

    def _sysv_needs_al_count(self):
        # cdecl variadic calls (printf/sprintf) have no AL-count
        # convention at all -- that's a SysV-AMD64-specific rule for
        # the XMM-argument count, and cdecl passes every float argument
        # on the stack (as its raw 8-byte bits, matching this backend's
        # sibling _backends/x86/codegen.py's own _call design, already
        # verified against a real `gcc -m32` reference), so there is no
        # register-resident float-argument count for AL to report.
        return False

    # ── Exception machinery (setjmp/longjmp) ─────────────────────────────────

    def emit_exception_runtime(self) -> None:
        """i386 cdecl setjmp/longjmp exception handling.

        Buffer layout (24 bytes, all 4 bytes each) -- matches real
        glibc's own i386 __jmp_buf convention exactly (verified against
        glibc's documented jmp_buf offsets, not invented): ebx(0)/
        esi(4)/edi(8)/ebp(12)/esp(16)/eip(20). This is a GENUINE
        redesign from the x86-64 version's 80-byte/8-non-volatile-
        register buffer, not a mechanical narrowing -- i386 cdecl's
        non-volatile (callee-saved) GP register set is only EBX/ESI/
        EDI/EBP (4 registers total; there is no r8-r15 equivalent at
        all), so the buffer is both smaller AND holds a different set
        of registers, not just narrower copies of the same eight.
        Comfortably fits within _gen_try's existing 200-byte
        __try_buf_* allocation (codegen.py's own _cl_define_bytes call
        for the try-buffer), so no change needed there.
        """
        if self.use_runtime_lib:
            for sym in (
                "_runtime_setjmp",
                "_runtime_longjmp",
                "_runtime_raise",
                "_runtime_handler_top",
                "_runtime_exc_msg",
                "_runtime_exc_type",
            ):
                self.emit(f"extern {sym}")
            return
        # BSS globals -- 4 bytes each here (a genuine pointer/int-sized
        # global on this target), not 8: this backend's own memory-
        # layout design decision (module docstring) keeps asmpython
        # int/float VALUES 8 bytes wide, but _runtime_exc_msg/_exc_type
        # hold either a raw string pointer or a small integer type-id,
        # never a full asmpython int needing 64-bit range -- matching
        # how the sibling IR backend's own PTR type is 4 bytes on this
        # architecture (_backends/x86/elf.py's own _TYPE_SIZES: "ptr": 4).
        self.emit("section .bss")
        self.emit("_runtime_handler_top: resd 1")
        self.emit("_runtime_exc_msg:     resd 1")
        self.emit("_runtime_exc_type:    resd 1")

        self.emit("section .rodata")
        self.emit('_runtime_unhandled_prefix: db "Unhandled exception: ",0')

        self.emit("section .text")

        # ---- _runtime_setjmp -------------------------------------------------
        # Internal ABI: eax = jmp_buf pointer (in), eax = 0 initially /
        # nonzero after longjmp (out) -- same convention as x86-64,
        # narrower register.
        self.label("_runtime_setjmp")
        self.emitf(
            "mov [eax+0],  ebx",
            "mov [eax+4],  esi",
            "mov [eax+8],  edi",
            "mov [eax+12], ebp",
        )
        # Save esp at the point just after our caller did `call
        # _runtime_setjmp`: the call instruction pushed a 4-byte return
        # address, so esp now points at it -- esp+4 is the caller's own
        # esp from just before the call.
        self.emitf("lea ecx, [esp+4]", "mov [eax+16], ecx")
        # Save return address (the instruction after the call in the caller).
        self.emitf("mov ecx, [esp]", "mov [eax+20], ecx")
        self.emitf("xor eax, eax", "ret")

        # ---- _runtime_longjmp ------------------------------------------------
        # Input: eax = jmp_buf pointer, ebx = return value (nonzero).
        self.label("_runtime_longjmp")
        # Move buf to a scratch reg first -- we're about to overwrite ebx.
        self.emitf(
            "mov ecx, eax",
            "mov eax, ebx",  # return value
            "mov ebx, [ecx+0]",
            "mov esi, [ecx+4]",
            "mov edi, [ecx+8]",
            "mov ebp, [ecx+12]",
            "mov esp, [ecx+16]",
            "jmp [ecx+20]",
        )

        # ---- _runtime_raise --------------------------------------------------
        self.label("_runtime_raise")
        # eax = exception message (string ptr), ebx = exception type id.
        self.emitf(
            "mov [_runtime_exc_msg], eax", "mov [_runtime_exc_type], ebx"
        )
        self.emitf(
            "mov eax, [_runtime_handler_top]", "test eax, eax", "jnz ._rr_jump"
        )
        # Unhandled path.
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 32",
        )
        self._emit_set_error_color()
        self.emitf("mov eax, _runtime_unhandled_prefix")
        self._emit_print_str_ptr_no_newline()
        self.emitf("mov eax, [_runtime_exc_msg]")
        self._emit_print_str_ptr_no_newline()
        self._emit_print_newline()
        self._emit_exit_one()
        # Handler path: hand-rolled longjmp(handler, 1).
        self.label("._rr_jump")
        self.emitf(
            "mov ebx, 1",  # longjmp value
            "call _runtime_longjmp",
        )
