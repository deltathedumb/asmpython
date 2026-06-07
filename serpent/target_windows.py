"""Windows PE64 codegen (Microsoft x64 calling convention, msvcrt).

Runtime support comes from msvcrt: printf, fputs, strlen, _atoi64, sprintf, fgets.
"""

from __future__ import annotations

from .codegen import Codegen, FuncInfo


MS_ARG_REGS = ["rcx", "rdx", "r8", "r9"]


class WindowsCodegen(Codegen):
    section_text = "section .text"
    section_data = "section .data"
    section_rodata = "section .rdata"
    label_main = "main"

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
            "_strdup",
            "_atoi64",
            "atof",
            "sprintf",
            "fgets",
            "exit",
            "__acrt_iob_func",
            "malloc",
            "realloc",
            "free",
            "memset",
            "memcpy",
            "fmod",
        ):
            self.emit(f"extern {name}")

    def _arg_reg(self, i: int):
        if i < len(MS_ARG_REGS):
            return MS_ARG_REGS[i]
        return None

    def _int_arg_regs(self):
        return list(MS_ARG_REGS)

    def _needs_xmm_mirror_to_int(self):
        # Win64 variadic ABI puts each float arg in BOTH xmm<i> and the
        # matching int reg. Mirror unconditionally — harmless for non-variadic.
        return True

    # --- entry: main() -------------------------------------------------------
    def emit_entry_prologue(self, info: FuncInfo) -> None:
        self.emitf("push rbp", "mov rbp, rsp")
        frame = info.frame_size + 32  # shadow space for child calls
        if frame % 16 != 0:
            frame += 16 - (frame % 16)
        info.frame_size = frame
        self.emitf(f"sub rsp, {frame}")

    def emit_entry_epilogue(self, info: FuncInfo) -> None:
        self.emitf("xor rcx, rcx", "call exit")

    def emit_func_prologue(self, info: FuncInfo) -> None:
        self.emitf("push rbp", "mov rbp, rsp")
        frame = info.frame_size + 32
        if frame % 16 != 0:
            frame += 16 - (frame % 16)
        info.frame_size = frame
        if frame:
            self.emitf(f"sub rsp, {frame}")
        for i, p in enumerate(info.params):
            off = info.locals_[p]
            reg = self._arg_reg(i)
            if reg is None:
                raise NotImplementedError("too many parameters")
            self.emitf(f"mov [rbp{off:+d}], {reg}")

    def emit_func_epilogue(self, info: FuncInfo) -> None:
        self.emitf("mov rsp, rbp", "pop rbp", "ret")

    def emit_call(self, target: str) -> None:
        self.emitf(f"call {target}")

    # ---- runtime primitives -------------------------------------------------

    def _emit_print_int_no_newline(self) -> None:
        # printf("%lld", rax)
        self.emitf("mov rdx, rax", "lea rcx, [fmt_int]", "call printf")

    def _emit_print_str_ptr_no_newline(self) -> None:
        # printf("%s", rax)
        self.emitf("mov rdx, rax", "lea rcx, [fmt_str]", "call printf")

    def _emit_print_space(self) -> None:
        self.emitf("mov rcx, 32", "call putchar")

    def _emit_print_newline(self) -> None:
        self.emitf("mov rcx, 10", "call putchar")

    def _emit_strlen(self) -> None:
        # strlen takes rcx, returns rax
        self.emitf("mov rcx, rax", "call strlen")

    def _emit_int_to_str(self) -> None:
        # sprintf(itoa_str_buf, "%lld", val). Returns ptr in rax.
        self.emitf(
            "mov r8, rax",  # third arg = value
            "lea rdx, [fmt_int_only]",  # second arg = format
            "lea rcx, [itoa_str_buf]",  # first  arg = buffer
            "call sprintf",
            "lea rax, [itoa_str_buf]",
        )

    def _emit_str_to_int(self) -> None:
        self.emitf("mov rcx, rax", "call _atoi64")

    def _emit_input_line(self) -> None:
        self.emitf("call _runtime_input")

    def _emit_malloc(self, n: int) -> None:
        self.emitf(f"mov rcx, {n}", "call malloc")

    def _emit_print_float_no_newline(self) -> None:
        # printf("%g", value). MS x64 variadic ABI: each float arg lives in
        # BOTH the corresponding XMM register and the general-purpose register
        # at the same position. So we also mirror xmm0 -> rdx.
        self.emitf("movq rdx, xmm0", "lea rcx, [fmt_flt]", "call printf")

    def _emit_float_to_str(self) -> None:
        # sprintf(buf, "%g", xmm0). xmm0 must also be mirrored to r8 for
        # variadic MS x64 ABI.
        self.emitf(
            "movq r8, xmm0",
            "lea rdx, [fmt_flt_only]",
            "lea rcx, [itoa_str_buf]",
            "call sprintf",
            "lea rax, [itoa_str_buf]",
        )

    def _emit_str_to_float(self) -> None:
        # atof(rax) -> xmm0
        self.emitf("mov rcx, rax", "call atof")

    def _emit_call_libc_double_double(self, fn: str) -> None:
        # xmm0, xmm1 already hold args; result in xmm0. Need 32 bytes shadow.
        self.emitf(f"call {fn}")

    def _emit_libc_malloc_size_in_rax(self) -> None:
        self.emitf("mov rcx, rax", "call malloc")

    def _emit_libc_memset_zero(self) -> None:
        # rax = ptr, rbx = size -> memset(ptr, 0, size)
        self.emitf("mov rcx, rax", "xor rdx, rdx", "mov r8, rbx", "call memset")

    def _emit_libc_strcmp(self) -> None:
        # rax = a, rbx = b -> rax = signed cmp
        self.emitf("mov rcx, rax", "mov rdx, rbx", "call strcmp", "movsxd rax, eax")

    def _emit_libc_strdup(self) -> None:
        # rax = src -> rax = owned copy
        self.emitf("mov rcx, rax", "call _strdup")

    def _emit_libc_strlen(self) -> None:
        # rax = ptr -> rax = length
        self.emitf("mov rcx, rax", "call strlen")

    def _emit_libc_memcpy(self) -> None:
        # rax = dst, rbx = src, rcx = n
        self.emitf("mov r8, rcx", "mov rdx, rbx", "mov rcx, rax", "call memcpy")

    def _emit_libc_strstr(self) -> None:
        # rax = haystack, rbx = needle -> rax = ptr or NULL
        self.emitf("mov rdx, rbx", "mov rcx, rax", "call strstr")

    def _emit_libc_free(self) -> None:
        # rax = ptr
        self.emitf("mov rcx, rax", "call free")

    def _emit_exit_one(self) -> None:
        self.emitf("mov rcx, 1", "call exit")

    def _emit_call_setjmp(self, buf_off: int) -> None:
        # Use mamba's hand-rolled _runtime_setjmp. It takes the buf in rax.
        self.emitf(f"lea rax, [rbp{buf_off:+d}]", "call _runtime_setjmp")

    def _emit_call_longjmp_with_buf_in_rax(self) -> None:
        # Unused — _runtime_raise calls _runtime_longjmp directly. Kept for
        # API symmetry in case future code reaches this path.
        self.emitf("mov rbx, 1", "call _runtime_longjmp")

    # ---- runtime data -------------------------------------------------------

    def emit_print_impls(self) -> None:
        if not self.use_runtime_lib:
            self.emit("section .bss")
            self.emit("itoa_str_buf: resb 32")
            self.emit("input_buf:    resb 256")
        else:
            self.emit("extern itoa_str_buf")
            self.emit("extern input_buf")

        self.emit("section .rdata")
        # Format strings.
        self.emit('fmt_int:      db "%lld",0')
        self.emit('fmt_str:      db "%s",0')
        self.emit('fmt_int_only: db "%lld",0')
        self.emit('fmt_flt:      db "%g",0')
        self.emit('fmt_flt_only: db "%g",0')

        if self.use_runtime_lib:
            for sym in ("_runtime_input", "_runtime_list_append", "_runtime_list_pop"):
                self.emit(f"extern {sym}")
            self.emit_dict_runtime()
            self.emit_string_runtime()
            self.emit_exception_runtime()
            return

        # Runtime helper: read one line into input_buf, strip trailing '\n',
        # return rax = ptr.
        self.emit("section .text")
        self.label("_runtime_input")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
        self.emitf(
            "mov ecx, 0",  # stdin
            "call __acrt_iob_func",
            "mov r8, rax",
            "mov edx, 255",
            "lea rcx, [input_buf]",
            "call fgets",
        )
        self.emitf(
            "lea rcx, [input_buf]",
            "call strlen",
            "lea rdi, [input_buf]",
            "test rax, rax",
            "jz ._wi_done",
            "mov dl, [rdi+rax-1]",
            "cmp dl, 10",
            "jne ._wi_done",
            "dec rax",
            "mov byte [rdi+rax], 0",
        )
        self.label("._wi_done")
        self.emitf("lea rax, [input_buf]", "leave", "ret")

        # List runtime: append + pop. Layout: header [cap, len, buf_ptr];
        # buffer holds the int64 elements. Growth re-allocates the buffer
        # but the header (and the user's local) keeps the same address.
        #
        # list_append(header_in_rax, value_in_rbx):
        self.label("_runtime_list_append")
        # 32 bytes of locals + 32 shadow space; 16-aligned.
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
        # [rbp-8]  = header ptr
        # [rbp-16] = value
        # [rbp-24] = saved new capacity
        self.emitf("mov [rbp-8], rax", "mov [rbp-16], rbx")
        self.emitf(
            "mov rcx, [rax+8]",  # length
            "cmp rcx, [rax]",  # vs capacity
            "jl ._la_store",
        )
        # Grow buffer: new_cap = max(cap*2, 4).
        self.emitf(
            "mov rcx, [rax]", "shl rcx, 1", "cmp rcx, 4", "jge ._la_grow", "mov rcx, 4"
        )
        self.label("._la_grow")
        # realloc(old_buf, new_cap * 8). Win64: 1st arg = rcx, 2nd = rdx.
        self.emitf(
            "mov [rbp-24], rcx",  # save new cap
            "shl rcx, 3",
            "mov rdx, rcx",  # 2nd arg = new size
            "mov rax, [rbp-8]",
            "mov rcx, [rax+16]",  # 1st arg = old buf ptr
            "call realloc",
        )
        # Update header.
        self.emitf(
            "mov rbx, [rbp-8]",
            "mov [rbx+16], rax",  # new buf ptr
            "mov rdx, [rbp-24]",
            "mov [rbx], rdx",
        )  # new capacity
        self.label("._la_store")
        self.emitf(
            "mov rax, [rbp-8]",
            "mov rcx, [rax+8]",  # length
            "mov rbx, [rbp-16]",
            "mov rdx, [rax+16]",  # buffer ptr
            "mov [rdx+rcx*8], rbx",
            "inc qword [rax+8]",
            "leave",
            "ret",
        )

        # list_pop(header_in_rax) -> value in rax
        self.label("_runtime_list_pop")
        self.emitf(
            "mov rcx, [rax+8]",  # length
            "dec rcx",
            "mov [rax+8], rcx",
            "mov rdx, [rax+16]",  # buffer ptr
            "mov rax, [rdx+rcx*8]",
            "ret",
        )

        # Dict runtime (shared across targets).
        self.emit_dict_runtime()
        # String runtime (shared across targets).
        self.emit_string_runtime()
        # Exception runtime (shared across targets).
        self.emit_exception_runtime()
