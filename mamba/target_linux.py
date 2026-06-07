"""Linux ELF64 codegen (System V AMD64 ABI, links against libc).

Previously this target was libc-free and used raw syscalls. That was nice for
hello-world but painful for everything else: malloc/realloc, printf with %lld,
fgets, atoll, sin/cos/sqrt for math. We now link against libc through gcc as
the linker driver, mirroring the Windows path.
"""
from __future__ import annotations

from .codegen import Codegen, FuncInfo


SYSV_ARG_REGS = ["rdi", "rsi", "rdx", "rcx", "r8", "r9"]


class LinuxCodegen(Codegen):
    section_text = "section .text"
    section_data = "section .data"
    section_rodata = "section .rodata"
    label_main = "main"     # we let libc's _start call main()

    def emit_externs(self) -> None:
        self.emit("global main")
        for name in (
            "printf", "fputs", "fputc", "puts", "putchar",
            "strlen", "strcmp", "strstr", "strdup",
            "atoll", "atof", "sprintf", "fgets", "stdin",
            "malloc", "realloc", "free", "memcpy", "memset", "exit",
            "fmod",
        ):
            self.emit(f"extern {name}")

    def _arg_reg(self, i: int):
        if i < len(SYSV_ARG_REGS):
            return SYSV_ARG_REGS[i]
        return None

    def _int_arg_regs(self):
        return list(SYSV_ARG_REGS)

    def _sysv_needs_al_count(self):
        # Variadic libc functions check AL for the XMM arg count. Math libm
        # functions are non-variadic and ignore AL — but setting it is free.
        return True

    # --- entry ---------------------------------------------------------------

    def emit_entry_prologue(self, info: FuncInfo) -> None:
        self.emitf("push rbp", "mov rbp, rsp")
        frame = info.frame_size
        if frame % 16 != 0:
            frame += 16 - (frame % 16)
        info.frame_size = frame
        if frame:
            self.emitf(f"sub rsp, {frame}")

    def emit_entry_epilogue(self, info: FuncInfo) -> None:
        # exit(0) via libc
        self.emitf("xor rdi, rdi", "call exit")

    def emit_call(self, target: str) -> None:
        self.emitf(f"call {target}")

    # ---- runtime primitives -------------------------------------------------

    def _emit_print_int_no_newline(self) -> None:
        # printf("%lld", rax)
        self.emitf("mov rsi, rax", "lea rdi, [fmt_int]",
                   "xor rax, rax",   # variadic: zero AL = no XMM args
                   "call printf")

    def _emit_print_str_ptr_no_newline(self) -> None:
        self.emitf("mov rsi, rax", "lea rdi, [fmt_str]",
                   "xor rax, rax", "call printf")

    def _emit_print_space(self) -> None:
        self.emitf("mov rdi, 32", "call putchar")

    def _emit_print_newline(self) -> None:
        self.emitf("mov rdi, 10", "call putchar")

    def _emit_strlen(self) -> None:
        self.emitf("mov rdi, rax", "call strlen")

    def _emit_int_to_str(self) -> None:
        # sprintf(buf, "%lld", rax); return buf in rax
        self.emitf("mov rdx, rax",                # third arg
                   "lea rsi, [fmt_int]",          # second arg
                   "lea rdi, [itoa_str_buf]",     # first arg
                   "xor rax, rax",
                   "call sprintf",
                   "lea rax, [itoa_str_buf]")

    def _emit_str_to_int(self) -> None:
        self.emitf("mov rdi, rax", "call atoll")

    def _emit_input_line(self) -> None:
        self.emitf("call _runtime_input")

    def _emit_malloc(self, n: int) -> None:
        self.emitf(f"mov rdi, {n}", "call malloc")

    def _emit_print_float_no_newline(self) -> None:
        # printf("%g", xmm0). System V: float arg already in xmm0;
        # AL = number of XMM args used = 1.
        self.emitf("lea rdi, [fmt_flt]",
                   "mov al, 1",
                   "call printf")

    def _emit_float_to_str(self) -> None:
        self.emitf("lea rdi, [itoa_str_buf]",
                   "lea rsi, [fmt_flt]",
                   "mov al, 1",
                   "call sprintf",
                   "lea rax, [itoa_str_buf]")

    def _emit_str_to_float(self) -> None:
        self.emitf("mov rdi, rax", "call atof")

    def _emit_call_libc_double_double(self, fn: str) -> None:
        self.emitf(f"call {fn}")

    def _emit_libc_malloc_size_in_rax(self) -> None:
        self.emitf("mov rdi, rax", "call malloc")

    def _emit_libc_memset_zero(self) -> None:
        self.emitf("mov rdi, rax", "xor rsi, rsi", "mov rdx, rbx", "call memset")

    def _emit_libc_strcmp(self) -> None:
        self.emitf("mov rdi, rax", "mov rsi, rbx", "call strcmp",
                   "movsxd rax, eax")

    def _emit_libc_strdup(self) -> None:
        self.emitf("mov rdi, rax", "call strdup")

    def _emit_libc_strlen(self) -> None:
        self.emitf("mov rdi, rax", "call strlen")

    def _emit_libc_memcpy(self) -> None:
        self.emitf("mov rdx, rcx", "mov rsi, rbx", "mov rdi, rax", "call memcpy")

    def _emit_libc_strstr(self) -> None:
        self.emitf("mov rsi, rbx", "mov rdi, rax", "call strstr")

    def _emit_libc_free(self) -> None:
        self.emitf("mov rdi, rax", "call free")

    def _emit_exit_one(self) -> None:
        self.emitf("mov rdi, 1", "call exit")

    def _emit_call_setjmp(self, buf_off: int) -> None:
        # Use mamba's hand-rolled _runtime_setjmp (buf in rax).
        self.emitf(f"lea rax, [rbp{buf_off:+d}]", "call _runtime_setjmp")

    def _emit_call_longjmp_with_buf_in_rax(self) -> None:
        # Unused; kept for API symmetry.
        self.emitf("mov rbx, 1", "call _runtime_longjmp")

    # ---- runtime data + helpers --------------------------------------------

    def emit_print_impls(self) -> None:
        # Data buffers and format strings are always emitted per-program
        # because they're referenced directly by inlined printf calls.
        # itoa_str_buf and input_buf are runtime scratch the helpers use.
        if not self.use_runtime_lib:
            self.emit("section .bss")
            self.emit("itoa_str_buf: resb 32")
            self.emit("input_buf:    resb 256")
        else:
            # Even when linking the runtime library, the helpers use these
            # via `lea rdi, [rel itoa_str_buf]` — they must exist in the
            # library or be externed. The runtime owns them.
            self.emit("extern itoa_str_buf")
            self.emit("extern input_buf")

        self.emit("section .rodata")
        self.emit('fmt_int: db "%lld",0')
        self.emit('fmt_str: db "%s",0')
        self.emit('fmt_flt: db "%g",0')

        if self.use_runtime_lib:
            # Skip emitting helper bodies; runtime library provides them.
            for sym in ("_runtime_input", "_runtime_list_append",
                        "_runtime_list_pop"):
                self.emit(f"extern {sym}")
            # Still emit the shared runtime (dict/exception) references.
            self.emit_dict_runtime()
            self.emit_string_runtime()
            self.emit_exception_runtime()
            return

        self.emit("section .text")
        self.label("_runtime_input")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
        self.emitf("mov rdx, [stdin]",
                   "mov esi, 255",
                   "lea rdi, [input_buf]",
                   "call fgets")
        self.emitf("lea rdi, [input_buf]",
                   "call strlen",
                   "lea rdi, [input_buf]",
                   "test rax, rax", "jz ._li_done",
                   "mov dl, [rdi+rax-1]",
                   "cmp dl, 10", "jne ._li_done",
                   "dec rax",
                   "mov byte [rdi+rax], 0")
        self.label("._li_done")
        self.emitf("lea rax, [input_buf]", "leave", "ret")

        # List runtime: append/pop with stable header + relocatable buffer.
        self.label("_runtime_list_append")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
        self.emitf("mov [rbp-8], rax", "mov [rbp-16], rbx")
        self.emitf("mov rcx, [rax+8]",
                   "cmp rcx, [rax]",
                   "jl ._la_store")
        self.emitf("mov rcx, [rax]",
                   "shl rcx, 1",
                   "cmp rcx, 4", "jge ._la_grow",
                   "mov rcx, 4")
        self.label("._la_grow")
        self.emitf("mov [rbp-24], rcx",
                   "shl rcx, 3",
                   "mov rsi, rcx",                # 2nd arg = new size
                   "mov rax, [rbp-8]",
                   "mov rdi, [rax+16]",           # 1st arg = old buf ptr
                   "call realloc")
        self.emitf("mov rbx, [rbp-8]",
                   "mov [rbx+16], rax",
                   "mov rdx, [rbp-24]",
                   "mov [rbx], rdx")
        self.label("._la_store")
        self.emitf("mov rax, [rbp-8]",
                   "mov rcx, [rax+8]",
                   "mov rbx, [rbp-16]",
                   "mov rdx, [rax+16]",
                   "mov [rdx+rcx*8], rbx",
                   "inc qword [rax+8]",
                   "leave", "ret")

        self.label("_runtime_list_pop")
        self.emitf("mov rcx, [rax+8]",
                   "dec rcx",
                   "mov [rax+8], rcx",
                   "mov rdx, [rax+16]",
                   "mov rax, [rdx+rcx*8]",
                   "ret")

        # Dict runtime (shared across targets).
        self.emit_dict_runtime()
        # String runtime (shared across targets).
        self.emit_string_runtime()
        # Exception runtime (shared across targets).
        self.emit_exception_runtime()
