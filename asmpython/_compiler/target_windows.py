"""Windows PE64 codegen (Microsoft x64 calling convention, msvcrt).

Runtime support comes from msvcrt: printf, fputs, strlen, _atoi64, sprintf, fgets.
"""

from __future__ import annotations

from .codegen import Codegen, FuncInfo


MS_ARG_REGS = ["rcx", "rdx", "r8", "r9"]


class WindowsCodegen(Codegen):
    target_name = "WindowsCodegen"
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
            "strtoll",
            "atof",
            "strtod",
            "sprintf",
            "fgets",
            "fopen",
            "fgetc",
            "fclose",
            "access",
            "exit",
            "__acrt_iob_func",
            "malloc",
            "realloc",
            "free",
            "memset",
            "memcpy",
            "fmod",
            "pow",
        ):
            self.emit(f"extern {name}")

    def _arg_reg(self, i: int):
        if i < len(MS_ARG_REGS):
            return MS_ARG_REGS[i]
        return None

    def _int_arg_regs(self):
        return list(MS_ARG_REGS)

    def _assign_arg_regs(self, types: list) -> list:
        # Win64: argument position N always occupies register slot N --
        # either the Nth integer register (rcx, rdx, r8, r9) or xmmN,
        # depending on that argument's type. Positions >= 4 spill to the
        # stack regardless of type.
        result: list = []
        for i, ty in enumerate(types):
            if i < len(MS_ARG_REGS):
                if ty == "float":
                    result.append((f"xmm{i}", True))
                else:
                    result.append((MS_ARG_REGS[i], False))
            else:
                result.append(None)
        return result

    def _needs_xmm_mirror_to_int(self):
        # Win64 variadic ABI puts each float arg in BOTH xmm<i> and the
        # matching int reg. Mirror unconditionally — harmless for non-variadic.
        return True

    def _platform_c_name(self, fn) -> str:
        return getattr(fn, "c_name_windows", None) or fn.c_name

    def _platform_const_value(self, c):
        override = getattr(c, "value_windows", None)
        return override if override is not None else c.value

    # --- entry: main() -------------------------------------------------------
    def emit_entry_prologue(self, info: FuncInfo) -> None:
        # main(argc, argv): Win64 passes these in rcx/rdx. Stash them before
        # they're clobbered so sys.argv can be built from them.
        self.emitf("mov [rel _prog_argc], rcx", "mov [rel _prog_argv], rdx")
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
        self._spill_incoming_args(info)

    def _incoming_stack_arg_offset(self, stack_index: int) -> int:
        # Win64: the caller reserves 32 bytes of shadow ("home") space between
        # the return address and the first stack argument, so stack args start
        # at [rbp+16+32] rather than [rbp+16].
        return 16 + 32 + 8 * stack_index

    def _caller_shadow_space(self) -> int:
        # Win64 requires 32 bytes of shadow space below the stack arguments.
        return 32

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
        self.emitf("call _runtime_str_to_int")

    def _emit_str_to_int_base(self) -> None:
        # Normalize Python's 0b prefix (strtoll base 0 doesn't grok it), then
        # strtoll(str, endptr=NULL, base): Win64 args rcx, rdx, r8.
        self._emit_normalize_0b_prefix()
        self.emitf(
            "mov r8, rbx",  # base
            "xor rdx, rdx",  # endptr = NULL
            "mov rcx, rax",  # str
            "call strtoll",
        )

    def _emit_strtoll_endptr(self) -> None:
        # In: rax=str, rbx=&endptr_storage, rcx=base. Out: rax=int64, *rbx=endptr.
        # Win64: strtoll(rcx=str, rdx=endptr_addr, r8=base).
        self.emitf(
            "mov r8, rcx",   # base
            "mov rdx, rbx",  # endptr address (rbx preserved by callee)
            "mov rcx, rax",  # str
            "call strtoll",
        )

    def _emit_input_line(self) -> None:
        self.emitf("call _runtime_input")

    def _emit_malloc(self, n: int) -> None:
        self.emitf(f"mov rcx, {n}", "call malloc")

    def _emit_print_float_no_newline(self) -> None:
        # Route through _emit_float_to_str (sprintf "%g" + repr fixup) so
        # whole numbers print as "2.0" not "2", matching CPython.
        self._emit_float_to_str()
        self._emit_print_str_ptr_no_newline()

    def _emit_float_to_str(self) -> None:
        # Detect NaN/inf before sprintf: Windows UCRT produces "1.#QNAN" /
        # "1.#INF" instead of "nan"/"inf". We short-circuit to static strings.
        lbl_nan = "_win_str_nan"
        lbl_pinf = "_win_str_pinf"
        lbl_ninf = "_win_str_ninf"
        skip = self.fresh("fts_not_special")
        is_nan = self.fresh("fts_is_nan")
        is_inf = self.fresh("fts_is_inf")
        self.emitf(
            # Test for NaN (ucomisd with itself; parity flag set iff NaN)
            "ucomisd xmm0, xmm0",
            f"jp {is_nan}",
            # Test for +/-inf: abs(xmm0) == inf bits. `cmp r64, imm` only
            # takes a sign-extended 32-bit immediate, so the 64-bit inf
            # pattern must be loaded into a register first (a direct `cmp
            # rax, 0x7FF0000000000000` gets truncated to `cmp rax, 0`, which
            # misidentifies 0.0 as infinity).
            "movq rax, xmm0",
            "and rax, 0x7FFFFFFFFFFFFFFF",
            "mov r10, 0x7FF0000000000000",
            "cmp rax, r10",
            f"jne {skip}",
            f"jmp {is_inf}",
        )
        self.label(is_nan)
        nan_lbl, _ = self.intern_string("nan")
        self.emitf(f"lea rax, [{nan_lbl}]", "call _runtime_str_concat_dup")
        done = self.fresh("fts_done")
        self.emitf(f"jmp {done}")
        self.label(is_inf)
        # Check sign bit to distinguish +inf from -inf
        pinf = self.fresh("fts_pinf")
        self.emitf("movq rax, xmm0", "test rax, rax", f"jns {pinf}")
        ninf_lbl, _ = self.intern_string("-inf")
        self.emitf(f"lea rax, [{ninf_lbl}]", "call _runtime_str_concat_dup", f"jmp {done}")
        self.label(pinf)
        pinf_lbl, _ = self.intern_string("inf")
        self.emitf(f"lea rax, [{pinf_lbl}]", "call _runtime_str_concat_dup", f"jmp {done}")
        self.label(skip)
        # sprintf(buf, "%g", xmm0). xmm0 must also be mirrored to r8 for
        # variadic MS x64 ABI.
        self.emitf(
            "movq r8, xmm0",
            "lea rdx, [fmt_flt_only]",
            "lea rcx, [itoa_str_buf]",
            "call sprintf",
            "lea rax, [itoa_str_buf]",
        )
        self._emit_float_repr_fixup()
        self.label(done)

    def _emit_float_fmt(self, fmt_label: str) -> None:
        # sprintf(buf, fmt, xmm0). MS x64: variadic double also mirrored to r8.
        self.emitf(
            "movq r8, xmm0",
            f"lea rdx, [{fmt_label}]",
            "lea rcx, [itoa_str_buf]",
            "call sprintf",
            "lea rax, [itoa_str_buf]",
        )

    def _emit_int_fmt(self, fmt_label: str) -> None:
        # sprintf(buf, fmt, rax).
        self.emitf(
            "mov r8, rax",
            f"lea rdx, [{fmt_label}]",
            "lea rcx, [itoa_str_buf]",
            "call sprintf",
            "lea rax, [itoa_str_buf]",
        )

    def _emit_str_to_float(self) -> None:
        # strtod(rax, NULL) -> xmm0 (handles "nan"/"inf" unlike atof on UCRT)
        self.emitf("mov rcx, rax", "xor rdx, rdx", "call strtod")

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
        # Use asmpython's hand-rolled _runtime_setjmp. It takes the buf in rax.
        self.emitf(f"lea rax, [rbp{buf_off:+d}]", "call _runtime_setjmp")

    def _emit_call_longjmp_with_buf_in_rax(self) -> None:
        # Unused — _runtime_raise calls _runtime_longjmp directly. Kept for
        # API symmetry in case future code reaches this path.
        self.emitf("mov rbx, 1", "call _runtime_longjmp")

    # ---- os.getcwd / os.listdir (Windows) ------------------------------------

    def _emit_os_getcwd(self) -> None:
        self._needs_cwd_buf = True
        fail_lbl = self.fresh("cwd_fail")
        done_lbl = self.fresh("cwd_done")
        empty_lbl, _ = self.intern_string("")
        self.emitf(
            "sub rsp, 32",
            "lea rcx, [_cwd_buf]",
            "mov edx, 4096",
            "call _getcwd",
            "add rsp, 32",
            "test rax, rax",
            f"jz {fail_lbl}",
            "call _runtime_str_concat_dup",
            f"jmp {done_lbl}",
        )
        self.label(fail_lbl)
        self.emitf(f"lea rax, [{empty_lbl}]")
        self.label(done_lbl)
        self.ffi_externs.add("_getcwd")

    def _emit_os_listdir(self, path_arg, info: FuncInfo) -> None:
        lbl_loop = self.fresh("listdir_loop")
        lbl_done = self.fresh("listdir_done")
        lbl_nl   = self.fresh("listdir_nl")
        if path_arg is not None:
            cmd_pfx_lbl, _ = self.intern_string("dir /b ")
            self.gen_expr(path_arg, info)
            self.emitf("mov rbx, rax", f"lea rax, [{cmd_pfx_lbl}]",
                       "call _runtime_str_concat")
        else:
            cmd_lbl, _ = self.intern_string("dir /b")
            self.emitf(f"lea rax, [{cmd_lbl}]")
        mode_lbl, _ = self.intern_string("r")
        self.emitf(
            "mov rbx, rax",
            "sub rsp, 32",
            f"lea rdx, [{mode_lbl}]",
            "mov rcx, rbx",
            "call _popen",
            "add rsp, 32",
        )
        pipe_slot = info.locals_[f"__listdir_pipe_{id(path_arg)}"]
        acc_slot  = info.locals_[f"__listdir_acc_{id(path_arg)}"]
        line_slot = info.locals_[f"__listdir_line_{id(path_arg)}"]
        char_slot = info.locals_[f"__listdir_char_{id(path_arg)}"]
        empty_lbl, _ = self.intern_string("")
        self.emitf(f"mov [rbp{pipe_slot:+d}], rax")
        # allocate empty list header + buffer (cap=4, len=0)
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            f"mov qword [rax+{self.LIST_CAP_OFF}], 4",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 0",
            f"mov [rbp{acc_slot:+d}], rax",
        )
        self._emit_malloc(32)  # 4 * 8 bytes
        self.emitf(
            f"mov rbx, [rbp{acc_slot:+d}]",
            f"mov [rbx+{self.LIST_BUF_OFF}], rax",
        )
        # current line = empty heap string
        self.emitf(f"lea rax, [{empty_lbl}]", "call _runtime_str_concat_dup",
                   f"mov [rbp{line_slot:+d}], rax")
        self.label(lbl_loop)
        self.emitf(
            f"mov rcx, [rbp{pipe_slot:+d}]",
            "sub rsp, 32", "call fgetc", "add rsp, 32",
            "movsxd rax, eax",
            f"mov [rbp{char_slot:+d}], rax",
            "cmp rax, -1", f"je {lbl_done}",
            "cmp rax, 10", f"je {lbl_nl}",
            "cmp rax, 13", f"je {lbl_loop}",
        )
        # append char to current line (rax already holds char code)
        self.emitf("call _runtime_chr",
                   "mov rbx, rax",
                   f"mov rax, [rbp{line_slot:+d}]",
                   "call _runtime_str_concat",
                   f"mov [rbp{line_slot:+d}], rax",
                   f"jmp {lbl_loop}")
        self.label(lbl_nl)
        skip_lbl = self.fresh("listdir_skip")
        self.emitf(
            f"mov rcx, [rbp{line_slot:+d}]",
            "sub rsp, 32", "call strlen", "add rsp, 32",
            "test rax, rax", f"jz {skip_lbl}",
        )
        self.emitf(
            f"mov rax, [rbp{acc_slot:+d}]",
            f"mov rbx, [rbp{line_slot:+d}]",
            "call _runtime_list_append",
        )
        self.label(skip_lbl)
        self.emitf(f"lea rax, [{empty_lbl}]", "call _runtime_str_concat_dup",
                   f"mov [rbp{line_slot:+d}], rax",
                   f"jmp {lbl_loop}")
        self.label(lbl_done)
        self.emitf(
            f"mov rcx, [rbp{pipe_slot:+d}]",
            "sub rsp, 32", "call _pclose", "add rsp, 32",
            f"mov rax, [rbp{acc_slot:+d}]",
        )
        # Only CRT symbols need extern declarations; _runtime_* are defined inline.
        for sym in ("_popen", "_pclose", "fgetc", "strlen"):
            if sym not in self.ffi_called:
                self.ffi_externs.add(sym)

    # ---- runtime data -------------------------------------------------------

    def emit_print_impls(self) -> None:
        if not self.use_runtime_lib:
            self.emit("section .bss")
            self.emit("itoa_str_buf: resb 32")
            self.emit("input_buf:    resb 256")
        else:
            self.emit("extern itoa_str_buf")
            self.emit("extern input_buf")
        self._emit_cwd_buf_if_needed()

        self.emit("section .rdata")
        # Format strings.
        self.emit('fmt_int:      db "%lld",0')
        self.emit('fmt_str:      db "%s",0')
        self.emit('fmt_int_only: db "%lld",0')
        self.emit('fmt_flt:      db "%g",0')
        self.emit('fmt_flt_only: db "%g",0')

        if self.use_runtime_lib:
            for sym in ("_runtime_input", "_runtime_list_append", "_runtime_list_pop", "_runtime_list_del"):
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

        # list_del(header_in_rax, index_in_rbx): remove element at index,
        # shifting later elements down by one slot and decrementing length.
        # Negative indices are normalized relative to length.
        self.label("_runtime_list_del")
        self.emitf(
            "mov rcx, [rax+8]",  # length
            "test rbx, rbx",
            "jns ._ld_pos",
            "add rbx, rcx",
        )
        self.label("._ld_pos")
        self.emitf("mov rdx, [rax+16]")  # buffer ptr
        self.label("._ld_loop")
        self.emitf(
            "lea r8, [rbx+1]",
            "cmp r8, rcx",
            "jge ._ld_done",
            "mov r9, [rdx+r8*8]",
            "mov [rdx+rbx*8], r9",
            "mov rbx, r8",
            "jmp ._ld_loop",
        )
        self.label("._ld_done")
        self.emitf("dec qword [rax+8]", "ret")

        # Dict runtime (shared across targets).
        self.emit_dict_runtime()
        # String runtime (shared across targets).
        self.emit_string_runtime()
        # Exception runtime (shared across targets).
        self.emit_exception_runtime()
        # asmlib helpers (only emitted when symbols are referenced).
        self.emit_asmlib_runtime()

    # ---- asmlib runtime (Windows / x64 ABI) ---------------------------------
    # All helpers use Windows x64 calling convention: shadow space 32 bytes,
    # args in rcx/rdx/r8/r9, return in rax.
    # Note: asmpython's own codegen calls user-defined functions with SysV regs
    # (rdi/rsi/…); the FFI dispatch path converts to Windows ABI before calling
    # any c_name symbol, so these helpers receive Windows ABI args.

    _HW_STUBS = (
        "_hw_in_byte", "_hw_out_byte", "_hw_in_word", "_hw_out_word",
        "_hw_in_dword", "_hw_out_dword", "_hw_mmio_read8", "_hw_mmio_write8",
        "_hw_mmio_read32", "_hw_mmio_write32", "_hw_rdtsc", "_hw_cpuid",
        "_hw_halt", "_hw_cli", "_hw_sti", "_hw_pic_eoi", "_hw_pic_mask",
        "_hw_pic_unmask", "_hw_pit_set_freq", "_hw_keyboard_read",
        "_hw_keyboard_poll", "_hw_vga_set_color", "_hw_vga_set_cursor",
        "_hw_vga_get_row", "_hw_vga_get_col",
        "_hw_rdrand", "_hw_io_wait", "_hw_read_cr0", "_hw_read_cr2",
        "_hw_read_cr3", "_hw_read_cr4", "_hw_write_cr3", "_hw_read_msr",
        "_hw_write_msr", "_hw_invlpg", "_hw_lidt",
        "_hw_console_clear", "_hw_console_putc", "_hw_console_write",
        "_hw_console_set_color", "_hw_console_set_cursor",
        "_hw_console_get_row", "_hw_console_get_col",
    )
    _HW_CONSOLE_SYMS = (
        "_hw_console_clear", "_hw_console_putc", "_hw_console_write",
        "_hw_console_set_color", "_hw_console_set_cursor",
        "_hw_console_get_row", "_hw_console_get_col",
    )
    _THREAD_SYMS = (
        "_threading_create", "_threading_join", "_threading_is_alive",
        "_threading_lock_init", "_threading_lock_acquire",
        "_threading_lock_release", "_threading_lock_destroy",
        "_threading_get_ident", "_threading_active_count",
    )
    _NET_SYMS = (
        "_net_bind", "_net_connect", "_net_send", "_net_recv",
        "_net_send_all", "_net_accept", "_net_close",
        "_net_gethostname", "_net_errno",
        "_net_setsockopt", "_net_getsockopt_int",
        # Raw Winsock2 symbols used directly from socket BINDINGS
        "socket", "bind", "connect", "listen", "accept", "closesocket",
        "send", "recv", "htons", "htonl", "ntohs", "ntohl",
        "inet_addr", "gethostname", "WSAGetLastError", "WSAStartup",
        "setsockopt", "getsockopt", "shutdown",
    )
    _GUI_SYMS = (
        "_gui_fill_rect", "_gui_draw_rect", "_gui_poll_event",
        "_gui_wait_event", "_gui_key_scancode",
        "_gui_mouse_x", "_gui_mouse_y", "_gui_mouse_button",
        "_gui_load_bmp",
        "_gui_render_copy", "_gui_query_texture_w", "_gui_query_texture_h",
        "_gui_is_key_down", "_gui_mouse_dx", "_gui_mouse_dy",
    )
    _AUDIO_SYMS = (
        "_audio_load_wav",
    )
    _TTF_SYMS = (
        "_ttf_render_blended", "_ttf_size_text_w", "_ttf_size_text_h",
    )
    _MATH_SYMS = (
        "_math_isnan", "_math_isinf", "_math_isfinite",
        "_math_degrees", "_math_radians",
        "_math_gcd", "_math_lcm",
        "_math_factorial", "_math_comb", "_math_perm",
        "_math_log_base",
        "_math_modf_frac", "_math_modf_int",
        "_math_frexp_m", "_math_frexp_e",
        "_math_ldexp",
        "_math_isqrt", "_math_isclose",
    )
    _RANDOM_SYMS = (
        "_random_random", "_random_randint",
        "_random_uniform", "_random_randrange",
        "_random_choice", "_random_shuffle",
        "_random_sample", "_random_getrandbits",
    )
    _TIME_SYMS = (
        "_time_perf_counter", "_time_time_ns", "_time_sleep_ms",
    )

    def _asmlib_inline_syms(self) -> set:
        return (set(self._HW_STUBS) | set(self._NET_SYMS) | set(self._GUI_SYMS)
                | set(self._AUDIO_SYMS) | set(self._TTF_SYMS)
                | set(self._MATH_SYMS) | set(self._RANDOM_SYMS) | set(self._TIME_SYMS)
                | set(self._THREAD_SYMS))

    @property
    def needs_net(self) -> bool:
        return any(s in self.ffi_called for s in self._NET_SYMS)

    @property
    def needs_gui(self) -> bool:
        return (any(s in self.ffi_called for s in self._GUI_SYMS) or
                any(s.startswith("SDL_") for s in self.ffi_called))

    @property
    def needs_audio(self) -> bool:
        return (any(s in self.ffi_called for s in self._AUDIO_SYMS) or
                any(s.startswith("Mix_") for s in self.ffi_called))

    @property
    def needs_ttf(self) -> bool:
        return (any(s in self.ffi_called for s in self._TTF_SYMS) or
                any(s.startswith("TTF_") for s in self.ffi_called))

    def emit_asmlib_runtime(self) -> None:
        needs_hw      = any(s in self.ffi_called for s in self._HW_STUBS)
        needs_net     = any(s in self.ffi_called for s in self._NET_SYMS)
        needs_gui     = any(s in self.ffi_called for s in self._GUI_SYMS)
        needs_audio   = any(s in self.ffi_called for s in self._AUDIO_SYMS)
        needs_ttf     = any(s in self.ffi_called for s in self._TTF_SYMS)
        needs_math    = any(s in self.ffi_called for s in self._MATH_SYMS)
        needs_random  = any(s in self.ffi_called for s in self._RANDOM_SYMS)
        needs_time    = any(s in self.ffi_called for s in self._TIME_SYMS)
        needs_thread  = any(s in self.ffi_called for s in self._THREAD_SYMS)
        if not (needs_hw or needs_net or needs_gui or needs_audio or needs_ttf
                or needs_math or needs_random or needs_time or needs_thread):
            return

        self.emit("")
        self.emit("section .text")

        # ---- math helpers (Windows x64 ABI) ----------------------------------
        # Windows passes floats in xmm0/xmm1; ints in rcx/rdx/r8/r9.
        # Shadow space (32 bytes) required before any call.
        if needs_math:
            self.emit("extern log")
            self.emit("extern modf")
            self.emit("extern frexp")
            self.emit("section .rodata")
            self.emit("_math_deg_factor:  dq 57.29577951308232")
            self.emit("_math_rad_factor:  dq 0.017453292519943295")
            needs_inf_consts = any(s in self.ffi_called for s in (
                "_math_isinf", "_math_isfinite"))
            if needs_inf_consts:
                self.emit("section .rodata")
                self.emit("_math_inf_bits:  dq 0x7FF0000000000000")
                self.emit("_math_abs_mask:  dq 0x7FFFFFFFFFFFFFFF")
            self.emit("section .text")

            if "_math_isnan" in self.ffi_called:
                self.label("_math_isnan")
                self.emitf("ucomisd xmm0, xmm0", "setp al", "movzx rax, al", "ret")

            if "_math_isinf" in self.ffi_called:
                self.label("_math_isinf")
                self.emitf(
                    "movsd xmm1, [rel _math_abs_mask]",
                    "andpd xmm0, xmm1",
                    "movsd xmm1, [rel _math_inf_bits]",
                    "ucomisd xmm0, xmm1",
                    "sete al", "setnp cl",
                    "and al, cl",
                    "movzx rax, al", "ret",
                )

            if "_math_isfinite" in self.ffi_called:
                self.label("_math_isfinite")
                self.emitf(
                    "ucomisd xmm0, xmm0",
                    "jp ._mif_no",
                    "movsd xmm1, [rel _math_abs_mask]",
                    "andpd xmm0, xmm1",
                    "movsd xmm1, [rel _math_inf_bits]",
                    "ucomisd xmm0, xmm1",
                    "jb ._mif_yes",
                )
                self.label("._mif_no")
                self.emitf("xor rax, rax", "ret")
                self.label("._mif_yes")
                self.emitf("mov rax, 1", "ret")

            if "_math_degrees" in self.ffi_called:
                self.label("_math_degrees")
                self.emitf("mulsd xmm0, [rel _math_deg_factor]", "ret")

            if "_math_radians" in self.ffi_called:
                self.label("_math_radians")
                self.emitf("mulsd xmm0, [rel _math_rad_factor]", "ret")

            # _math_gcd(rcx=a, rdx=b) -> rax  (Euclidean, positive result)
            if "_math_gcd" in self.ffi_called:
                self.label("_math_gcd")
                self.emitf("mov rax, rcx", "mov rcx, rdx")
                self.emitf("test rax, rax", "jns ._mg_apos", "neg rax")
                self.label("._mg_apos")
                self.emitf("test rcx, rcx", "jns ._mg_bpos", "neg rcx")
                self.label("._mg_bpos")
                self.label("._mg_loop")
                self.emitf("test rcx, rcx", "jz ._mg_done")
                self.emitf("xor rdx, rdx", "div rcx", "mov rax, rcx", "mov rcx, rdx",
                           "jmp ._mg_loop")
                self.label("._mg_done")
                self.emitf("ret")

            if "_math_lcm" in self.ffi_called:
                self.label("_math_lcm")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx")
                self.emitf("call _math_gcd")
                self.emitf("test rax, rax", "jz ._mlcm_zero")
                self.emitf("mov rcx, rax")
                self.emitf("mov rax, [rbp-8]", "test rax, rax", "jns ._mlcm_apos", "neg rax")
                self.label("._mlcm_apos")
                self.emitf("xor rdx, rdx", "div rcx")
                self.emitf("mov rcx, [rbp-16]", "test rcx, rcx", "jns ._mlcm_bpos", "neg rcx")
                self.label("._mlcm_bpos")
                self.emitf("imul rax, rcx", "leave", "ret")
                self.label("._mlcm_zero")
                self.emitf("xor rax, rax", "leave", "ret")

            if "_math_factorial" in self.ffi_called:
                self.label("_math_factorial")
                self.emitf("mov rax, 1", "cmp rcx, 1", "jle ._mf_done")
                self.label("._mf_loop")
                self.emitf("imul rax, rcx", "dec rcx", "cmp rcx, 1", "jg ._mf_loop")
                self.label("._mf_done")
                self.emitf("ret")

            # _math_comb(rcx=n, rdx=k) -> rax
            if "_math_comb" in self.ffi_called:
                self.label("_math_comb")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx")
                self.emitf("mov rax, rcx", "sub rax, rdx", "cmp rdx, rax", "jle ._mc_kset")
                self.emitf("mov [rbp-16], rax")
                self.label("._mc_kset")
                self.emitf("mov rax, 1", "mov rcx, 1")
                self.label("._mc_loop")
                self.emitf("cmp rcx, [rbp-16]", "jg ._mc_done")
                self.emitf("mov rdx, [rbp-8]", "sub rdx, [rbp-16]", "add rdx, rcx")
                self.emitf("imul rax, rdx", "xor rdx, rdx", "div rcx")
                self.emitf("inc rcx", "jmp ._mc_loop")
                self.label("._mc_done")
                self.emitf("leave", "ret")

            # _math_perm(rcx=n, rdx=k) -> rax
            if "_math_perm" in self.ffi_called:
                self.label("_math_perm")
                self.emitf("push rbp", "mov rbp, rsp")
                self.emitf("mov rax, 1", "mov r8, 0")
                self.label("._mp_loop")
                self.emitf("cmp r8, rdx", "jge ._mp_done")
                self.emitf("mov r9, rcx", "sub r9, r8", "imul rax, r9", "inc r8", "jmp ._mp_loop")
                self.label("._mp_done")
                self.emitf("pop rbp", "ret")

            # _math_log_base(xmm0=x, xmm1=base) -> xmm0
            if "_math_log_base" in self.ffi_called:
                self.label("_math_log_base")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("movsd [rbp-8], xmm1")
                self.emitf("call log")
                self.emitf("movsd [rbp-16], xmm0")
                self.emitf("movsd xmm0, [rbp-8]", "call log")
                self.emitf("movsd xmm1, xmm0", "movsd xmm0, [rbp-16]",
                           "divsd xmm0, xmm1", "leave", "ret")

            # _math_modf_frac(xmm0=x) -> xmm0=fractional
            if "_math_modf_frac" in self.ffi_called:
                self.label("_math_modf_frac")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("lea rdx, [rbp-8]", "call modf")
                self.emitf("leave", "ret")

            # _math_modf_int(xmm0=x) -> xmm0=integer part
            if "_math_modf_int" in self.ffi_called:
                self.label("_math_modf_int")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("lea rdx, [rbp-8]", "call modf")
                self.emitf("movsd xmm0, [rbp-8]", "leave", "ret")

            # _math_frexp_m(xmm0=x) -> xmm0=mantissa
            if "_math_frexp_m" in self.ffi_called:
                self.label("_math_frexp_m")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("lea rdx, [rbp-8]", "call frexp")
                self.emitf("leave", "ret")

            # _math_frexp_e(xmm0=x) -> rax=exponent
            if "_math_frexp_e" in self.ffi_called:
                self.label("_math_frexp_e")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("lea rdx, [rbp-8]", "call frexp")
                self.emitf("movsxd rax, dword [rbp-8]", "leave", "ret")

            # _math_ldexp(xmm0=x, rdx=n) -> xmm0  (Windows: slot1=rdx for the
            # int, per the positional Win64 ABI _gen_ffi_call now follows) --
            # already an exact match for ldexp(double, int), so just forward.
            if "_math_ldexp" in self.ffi_called:
                self.emit("extern ldexp")
                self.label("_math_ldexp")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("call ldexp", "leave", "ret")

            # _math_isqrt(rcx=n) -> rax: integer square root (floor(sqrt(n)))
            if "_math_isqrt" in self.ffi_called:
                self.emit("extern sqrt")
                self.label("_math_isqrt")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("cvtsi2sd xmm0, rcx", "call sqrt")
                self.emitf("cvttsd2si rax, xmm0", "leave", "ret")

            # _math_isclose(xmm0=a, xmm1=b, xmm2=rel_tol, xmm3=abs_tol) -> rax 0/1
            # |a-b| <= max(rel_tol * max(|a|,|b|), abs_tol)
            if "_math_isclose" in self.ffi_called:
                self.emit("extern fabs")
                lbl_ic_yes = self.fresh("isclose_yes")
                lbl_ic_no  = self.fresh("isclose_no")
                lbl_ic_end = self.fresh("isclose_end")
                self.label("_math_isclose")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
                # save all 4 float args
                self.emitf("movsd [rbp-8],  xmm0",   # a
                           "movsd [rbp-16], xmm1",   # b
                           "movsd [rbp-24], xmm2",   # rel_tol
                           "movsd [rbp-32], xmm3")   # abs_tol
                # diff = |a - b|
                self.emitf("movsd xmm0, [rbp-8]", "subsd xmm0, [rbp-16]",
                           "call fabs", "movsd [rbp-40], xmm0")  # diff
                # max_ab = max(|a|, |b|)
                self.emitf("movsd xmm0, [rbp-8]", "call fabs",
                           "movsd [rbp-48], xmm0")   # |a|
                self.emitf("movsd xmm0, [rbp-16]", "call fabs")  # |b|
                self.emitf("movsd xmm1, [rbp-48]",
                           "maxsd xmm0, xmm1",
                           "movsd [rbp-56], xmm0")   # max_ab
                # tol = max(rel_tol * max_ab, abs_tol)
                self.emitf("movsd xmm0, [rbp-24]", "mulsd xmm0, [rbp-56]",  # rel_tol*max_ab
                           "movsd xmm1, [rbp-32]",
                           "maxsd xmm0, xmm1",
                           "movsd [rbp-64], xmm0")   # tol
                # compare diff <= tol
                self.emitf("movsd xmm0, [rbp-40]", "movsd xmm1, [rbp-64]",
                           "ucomisd xmm0, xmm1")
                self.emitf(f"ja {lbl_ic_no}")
                self.label(lbl_ic_yes)
                self.emitf("mov rax, 1", f"jmp {lbl_ic_end}")
                self.label(lbl_ic_no)
                self.emitf("xor rax, rax")
                self.label(lbl_ic_end)
                self.emitf("leave", "ret")

        # ---- random helpers (Windows x64 ABI) --------------------------------
        if needs_random:
            self.emit("extern rand")
            self.emit("section .rodata")
            self.emit("_rand_inv:  dq 3.0517578125e-05")   # 1.0 / 32768
            self.emit("section .text")

            # _random_random() -> xmm0 in [0.0, 1.0)
            if "_random_random" in self.ffi_called:
                self.label("_random_random")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("call rand")
                self.emitf("cvtsi2sd xmm0, rax", "mulsd xmm0, [rel _rand_inv]",
                           "leave", "ret")

            # _random_randint(rcx=a, rdx=b) -> rax in [a, b] inclusive
            if "_random_randint" in self.ffi_called:
                self.label("_random_randint")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx")
                self.emitf("call rand")
                self.emitf("mov rcx, [rbp-16]", "sub rcx, [rbp-8]", "inc rcx")
                self.emitf("xor rdx, rdx", "div rcx")
                self.emitf("add rdx, [rbp-8]", "mov rax, rdx", "leave", "ret")

            # _random_uniform(xmm0=a, xmm1=b) -> xmm0 in [a, b]
            if "_random_uniform" in self.ffi_called:
                self.label("_random_uniform")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("movsd [rbp-8], xmm0", "movsd [rbp-16], xmm1")
                self.emitf("call rand")
                self.emitf("cvtsi2sd xmm0, rax", "mulsd xmm0, [rel _rand_inv]")
                self.emitf("movsd xmm1, [rbp-16]", "subsd xmm1, [rbp-8]",
                           "mulsd xmm0, xmm1", "addsd xmm0, [rbp-8]", "leave", "ret")

            # _random_randrange(rcx=stop) -> rax in [0, stop)
            if "_random_randrange" in self.ffi_called:
                self.label("_random_randrange")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rcx")
                self.emitf("call rand")
                self.emitf("xor rdx, rdx", "div qword [rbp-8]", "mov rax, rdx",
                           "leave", "ret")

            # _random_choice(rcx=list_hdr) -> rax = element at random index
            # List layout: [hdr+0]=cap, [hdr+8]=len, [hdr+16]=buf_ptr
            # Frame: push+56=64 bytes total → 16-byte aligned for rand() call.
            if "_random_choice" in self.ffi_called:
                self.label("_random_choice")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 56")
                self.emitf("mov [rbp-8], rcx")           # save list header
                self.emitf("mov rax, [rcx+8]")           # rax = len (offset 8)
                self.emitf("mov [rbp-16], rax")          # save len
                self.emitf("call rand")                  # rax = rand()
                self.emitf("xor rdx, rdx", "div qword [rbp-16]")  # rdx = rand % len
                self.emitf("mov rcx, rdx")               # rcx = index (save before load)
                self.emitf("mov rax, [rbp-8]")           # rax = list header
                self.emitf("mov rax, [rax+16]")          # rax = buf_ptr (offset 16)
                self.emitf("mov rax, [rax+rcx*8]")       # rax = buf[index]
                self.emitf("leave", "ret")

            # _random_shuffle(rcx=list_hdr) — Fisher-Yates shuffle in-place
            # Frame: push+72=80 bytes total → 16-byte aligned for rand() call.
            if "_random_shuffle" in self.ffi_called:
                lbl_loop = self.fresh("shuffle_loop")
                lbl_done = self.fresh("shuffle_done")
                self.label("_random_shuffle")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 72")
                self.emitf("mov [rbp-8], rcx")           # save list header
                self.emitf("mov rax, [rcx+8]")           # rax = n (len at offset 8)
                self.emitf("mov [rbp-16], rax")          # i = n (count down)
                self.label(lbl_loop)
                self.emitf(f"cmp qword [rbp-16], 1", f"jle {lbl_done}")
                self.emitf("call rand")
                self.emitf("xor rdx, rdx", "div qword [rbp-16]")  # rdx = j
                self.emitf("mov [rbp-24], rdx")           # save j
                # swap buf[i-1] and buf[j]
                self.emitf("mov r8, [rbp-8]", "mov r8, [r8+16]")  # r8 = buf (offset 16)
                self.emitf("mov r9, [rbp-16]", "dec r9")  # r9 = i-1
                self.emitf("mov rax, [r8+r9*8]")          # rax = buf[i-1]
                self.emitf("mov rcx, [rbp-24]")            # rcx = j
                self.emitf("mov rbx, [r8+rcx*8]")          # rbx = buf[j]
                self.emitf("mov [r8+r9*8], rbx")           # buf[i-1] = buf[j]
                self.emitf("mov [r8+rcx*8], rax")          # buf[j] = buf[i-1]
                self.emitf("dec qword [rbp-16]", f"jmp {lbl_loop}")
                self.label(lbl_done)
                self.emitf("xor rax, rax", "leave", "ret")

            # _random_sample(rcx=list_hdr, rdx=k) -> rax = new list of k unique elements
            # Strategy: copy source buf, do k-step partial Fisher-Yates, return first k.
            # Frame layout (offsets from rbp): -8=src_hdr, -16=n, -24=k, -32=copy_buf,
            #   -40=i, -48=result_hdr, -56=result_buf, -64=j_scratch
            if "_random_sample" in self.ffi_called:
                self.emit("extern malloc")
                lbl_s_cp = self.fresh("sample_cp")
                lbl_s_cp_end = self.fresh("sample_cp_end")
                lbl_s_fy = self.fresh("sample_fy")
                lbl_s_fy_end = self.fresh("sample_fy_end")
                lbl_s_fill = self.fresh("sample_fill")
                lbl_s_fill_end = self.fresh("sample_fill_end")
                self.label("_random_sample")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 96")
                self.emitf("mov [rbp-8], rcx")    # src_hdr
                self.emitf("mov [rbp-24], rdx")   # k
                self.emitf("mov rax, [rcx+8]", "mov [rbp-16], rax")  # n = src.len
                # allocate copy buf (n * 8 bytes)
                self.emitf("mov rcx, rax", "shl rcx, 3", "call malloc",
                           "mov [rbp-32], rax")   # copy_buf
                # memcpy source buf into copy_buf
                self.emitf("mov rsi, [rbp-8]", "mov rsi, [rsi+16]")  # src buf
                self.emitf("mov rdi, [rbp-32]")
                self.emitf("mov rcx, [rbp-16]")   # count
                self.emitf("xor rax, rax")
                self.label(lbl_s_cp)
                self.emitf(f"cmp rax, rcx", f"jge {lbl_s_cp_end}",
                           "mov rbx, [rsi+rax*8]", "mov [rdi+rax*8], rbx",
                           "inc rax", f"jmp {lbl_s_cp}")
                self.label(lbl_s_cp_end)
                # partial Fisher-Yates: i from n-1 down to n-k
                self.emitf("mov rax, [rbp-16]", "mov [rbp-40], rax")  # i = n
                self.label(lbl_s_fy)
                self.emitf("mov rax, [rbp-40]", "mov rbx, [rbp-16]",
                           "sub rbx, [rbp-24]",   # n - k
                           f"cmp rax, rbx", f"jle {lbl_s_fy_end}")
                # pick j = rand() % i (i is loop counter = number of candidates left)
                self.emitf("push rdi", "call rand", "pop rdi")
                self.emitf("xor rdx, rdx", "div qword [rbp-40]", "mov [rbp-64], rdx")  # j
                # swap copy_buf[i-1] and copy_buf[j]
                self.emitf("mov rdi, [rbp-32]")
                self.emitf("mov r9, [rbp-40]", "dec r9")              # i-1
                self.emitf("mov rcx, [rbp-64]")                        # j
                self.emitf("mov rax, [rdi+r9*8]", "mov rbx, [rdi+rcx*8]",
                           "mov [rdi+r9*8], rbx", "mov [rdi+rcx*8], rax")
                self.emitf("dec qword [rbp-40]", f"jmp {lbl_s_fy}")
                self.label(lbl_s_fy_end)
                # allocate result list header
                self.emitf("push rdi", "mov rcx, 24", "call malloc", "pop rdi",
                           "mov [rbp-48], rax")   # result_hdr
                self.emitf("mov rbx, [rbp-24]",
                           "mov [rax+0], rbx", "mov [rax+8], rbx")  # cap=len=k
                # allocate result buf (k * 8)
                self.emitf("push rdi", "mov rcx, [rbp-24]", "shl rcx, 3",
                           "call malloc", "pop rdi", "mov [rbp-56], rax")
                self.emitf("mov rcx, [rbp-48]", "mov [rcx+16], rax")  # hdr.buf = result_buf
                # copy first k elements from copy_buf tail (indices n-k..n-1) to result
                # The selected elements end up at positions [n-k .. n-1] after partial FY
                self.emitf("mov rax, [rbp-16]", "sub rax, [rbp-24]")  # start = n-k
                self.emitf("mov [rbp-40], rax")   # reuse as fill index
                self.emitf("xor rbx, rbx")        # result index
                self.label(lbl_s_fill)
                self.emitf("mov rcx, [rbp-16]",   # n
                           f"cmp rbx, [rbp-24]",  # rbx < k
                           f"jge {lbl_s_fill_end}")
                self.emitf("mov rdi, [rbp-32]")   # copy_buf
                self.emitf("mov rax, [rbp-40]", "add rax, rbx")  # index = start + rbx
                self.emitf("mov r8, [rdi+rax*8]")  # val = copy_buf[start+rbx]
                self.emitf("mov rdi, [rbp-56]")    # result_buf
                self.emitf("mov [rdi+rbx*8], r8")  # result_buf[rbx] = val
                self.emitf("inc rbx", f"jmp {lbl_s_fill}")
                self.label(lbl_s_fill_end)
                self.emitf("mov rax, [rbp-48]", "leave", "ret")

            # _random_getrandbits(rcx=k) -> rax: k random bits (1-64)
            if "_random_getrandbits" in self.ffi_called:
                lbl_gb_loop = self.fresh("grb_loop")
                lbl_gb_done = self.fresh("grb_done")
                self.label("_random_getrandbits")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rcx")    # k (bit count)
                self.emitf("xor rbx, rbx")        # result = 0
                self.emitf("xor r12, r12")        # bits_filled = 0
                self.label(lbl_gb_loop)
                self.emitf(f"cmp r12, [rbp-8]", f"jge {lbl_gb_done}")
                self.emitf("call rand")            # rax = 0..32767 (15 bits)
                self.emitf("shl rbx, 15", "or rbx, rax", "add r12, 15",
                           f"jmp {lbl_gb_loop}")
                self.label(lbl_gb_done)
                # mask to k bits
                self.emitf("mov rcx, [rbp-8]")
                self.emitf("mov rax, 1", "shl rax, cl", "dec rax")  # mask = (1<<k)-1
                self.emitf("and rax, rbx", "leave", "ret")

        # ---- time helpers (Windows x64 ABI) ----------------------------------
        # QueryPerformanceCounter / QueryPerformanceFrequency for perf_counter.
        # GetSystemTimeAsFileTime for time_ns. Sleep() for sleep_ms.
        if needs_time:
            self.emit("extern QueryPerformanceCounter")
            self.emit("extern QueryPerformanceFrequency")
            self.emit("section .bss")
            self.emit("_time_qpc_buf:  resq 1")
            self.emit("_time_qpf_buf:  resq 1")
            self.emit("section .rodata")
            self.emit("_time_1e9:  dq 1000000000.0")
            self.emit("_time_1e7:  dq 10000000.0")
            self.emit("_time_116444736e9:  dq 116444736000000000")  # FILETIME epoch offset
            self.emit("section .text")

            # _time_perf_counter() -> xmm0 seconds as float
            if "_time_perf_counter" in self.ffi_called:
                self.label("_time_perf_counter")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("lea rcx, [rel _time_qpc_buf]", "call QueryPerformanceCounter")
                self.emitf("lea rcx, [rel _time_qpf_buf]", "call QueryPerformanceFrequency")
                self.emitf("cvtsi2sd xmm0, qword [rel _time_qpc_buf]")
                self.emitf("cvtsi2sd xmm1, qword [rel _time_qpf_buf]")
                self.emitf("divsd xmm0, xmm1", "leave", "ret")

            # _time_time_ns() -> rax (ns since Unix epoch)
            # GetSystemTimeAsFileTime returns 100ns intervals since 1601-01-01.
            if "_time_time_ns" in self.ffi_called:
                self.emit("extern GetSystemTimeAsFileTime")
                self.label("_time_time_ns")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("lea rcx, [rel _time_qpc_buf]", "call GetSystemTimeAsFileTime")
                # convert 100ns FILETIME to ns since Unix epoch
                self.emitf("mov rax, [rel _time_qpc_buf]")
                self.emitf("sub rax, [rel _time_116444736e9]")
                self.emitf("imul rax, rax, 100", "leave", "ret")

            # _time_sleep_ms(rcx=ms) — Sleep(ms) on Windows
            if "_time_sleep_ms" in self.ffi_called:
                self.emit("extern Sleep")
                self.label("_time_sleep_ms")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32",
                           "call Sleep", "xor rax, rax", "leave", "ret")

        if needs_hw:
            # rdtsc/cpuid/rdrand are unprivileged (ring-3) instructions, so
            # unlike the rest of asmlib.hardware (port I/O, MMIO, MSRs, CRn,
            # PIC/PIT/keyboard/VGA, cli/sti/hlt -- all ring-0-only) they have
            # real implementations on hosted targets too, not just
            # freestanding.
            if "_hw_rdtsc" in self.ffi_called:
                self.label("_hw_rdtsc")
                self.emitf("rdtsc", "shl rdx, 32", "or rax, rdx", "ret")
            if "_hw_cpuid" in self.ffi_called:
                # arg (leaf) arrives in ecx (Win64 ABI); returns EAX after CPUID.
                self.label("_hw_cpuid")
                self.emitf("mov eax, ecx", "push rbx", "cpuid",
                           "pop rbx", "movsx rax, eax", "ret")
            if "_hw_rdrand" in self.ffi_called:
                self.label("_hw_rdrand")
                self.label(".retry")
                self.emitf("rdrand rax", "jnc .retry", "ret")

            if any(s in self.ffi_called for s in self._HW_CONSOLE_SYMS):
                self._emit_console_runtime()

            for sym in self._HW_STUBS:
                if sym in self.ffi_called and sym not in (
                        "_hw_rdtsc", "_hw_cpuid", "_hw_rdrand") and sym not in self._HW_CONSOLE_SYMS:
                    self.label(sym)
                    self.emitf("xor rax, rax", "ret")

        # Windows Winsock2 network helpers.  Args arrive in Windows ABI regs.
        if needs_net:
            for sym in ("socket", "bind", "connect", "listen", "accept",
                        "closesocket", "send", "recv", "htons", "inet_addr",
                        "gethostname", "WSAGetLastError"):
                self.emit(f"extern {sym}")

            # _net_bind(rcx=fd, rdx=addr_cstr, r8=port) -> rax
            if "_net_bind" in self.ffi_called:
                self.label("_net_bind")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx", "mov [rbp-24], r8")
                # Build sockaddr_in at rbp-48 (16 bytes)
                self.emitf("xor rax, rax",
                           "mov [rbp-48], rax", "mov [rbp-56], rax")
                self.emitf("mov word [rbp-48], 2")   # AF_INET
                self.emitf("mov rcx, r8", "call htons",
                           "mov word [rbp-48+2], ax")
                self.emitf("mov rcx, [rbp-16]", "call inet_addr",
                           "mov dword [rbp-48+4], eax")
                self.emitf("mov rcx, [rbp-8]", "lea rdx, [rbp-48]",
                           "mov r8d, 16", "call bind", "leave", "ret")

            # _net_connect(rcx=fd, rdx=addr_cstr, r8=port) -> rax
            if "_net_connect" in self.ffi_called:
                self.label("_net_connect")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx", "mov [rbp-24], r8")
                self.emitf("xor rax, rax",
                           "mov [rbp-48], rax", "mov [rbp-56], rax")
                self.emitf("mov word [rbp-48], 2")
                self.emitf("mov rcx, r8", "call htons",
                           "mov word [rbp-48+2], ax")
                self.emitf("mov rcx, [rbp-16]", "call inet_addr",
                           "mov dword [rbp-48+4], eax")
                self.emitf("mov rcx, [rbp-8]", "lea rdx, [rbp-48]",
                           "mov r8d, 16", "call connect", "leave", "ret")

            # _net_send(rcx=fd, rdx=msg_cstr, r8=flags) -> rax
            if "_net_send" in self.ffi_called:
                self.emit("extern strlen")
                self.label("_net_send")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx", "mov [rbp-24], r8")
                self.emitf("mov rcx, rdx", "call strlen", "mov [rbp-32], rax")
                self.emitf("mov rcx, [rbp-8]", "mov rdx, [rbp-16]",
                           "mov r8, [rbp-32]", "mov r9, [rbp-24]",
                           "call send", "leave", "ret")

            # _net_recv(rcx=fd, rdx=buf_size) -> rax (new string ptr)
            if "_net_recv" in self.ffi_called:
                self.label("_net_recv")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx")
                self.emitf("lea rcx, [rdx+1]", "call malloc", "mov [rbp-24], rax")
                self.emitf("mov rcx, [rbp-8]", "mov rdx, rax",
                           "mov r8, [rbp-16]", "xor r9, r9",
                           "call recv")
                self.emitf("mov rdx, rax", "mov rax, [rbp-24]",
                           "test rdx, rdx", "jl ._recv_err",
                           "mov byte [rax+rdx], 0", "leave", "ret")
                self.label("._recv_err")
                self.emitf("mov byte [rax], 0", "leave", "ret")

            # _net_send_all(rcx=fd, rdx=msg_cstr) -> rax
            if "_net_send_all" in self.ffi_called:
                self.emit("extern strlen")
                self.label("_net_send_all")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx")
                self.emitf("mov rcx, rdx", "call strlen", "mov [rbp-24], rax")
                self.emitf("xor rax, rax", "mov [rbp-32], rax")
                self.label("._sa_loop")
                self.emitf("mov rax, [rbp-32]", "cmp rax, [rbp-24]", "jge ._sa_done")
                self.emitf("mov rcx, [rbp-8]",
                           "mov rdx, [rbp-16]", "add rdx, [rbp-32]",
                           "mov r8, [rbp-24]", "sub r8, [rbp-32]",
                           "xor r9, r9",
                           "call send",
                           "test rax, rax", "jle ._sa_done",
                           "add [rbp-32], rax", "jmp ._sa_loop")
                self.label("._sa_done")
                self.emitf("mov rax, [rbp-32]", "leave", "ret")

            # _net_accept(rcx=fd) -> rax
            if "_net_accept" in self.ffi_called:
                self.label("_net_accept")
                self.emitf("xor rdx, rdx", "xor r8, r8", "call accept", "ret")

            # _net_close(rcx=fd) -> rax
            if "_net_close" in self.ffi_called:
                self.label("_net_close")
                self.emitf("call closesocket", "ret")

            # _net_gethostname() -> rax
            if "_net_gethostname" in self.ffi_called:
                self.emit("section .bss")
                self.emit("_net_hostname_buf: resb 256")
                self.emit("section .text")
                self.label("_net_gethostname")
                self.emitf("lea rcx, [_net_hostname_buf]", "mov edx, 255",
                           "call gethostname",
                           "lea rax, [_net_hostname_buf]", "ret")

            # _net_errno() -> rax (Winsock2 error code)
            if "_net_errno" in self.ffi_called:
                self.label("_net_errno")
                self.emitf("call WSAGetLastError", "ret")

            # _net_setsockopt(rcx=fd, rdx=level, r8=optname, r9=value) -> rax
            if "_net_setsockopt" in self.ffi_called:
                self.emit("extern setsockopt")
                self.label("_net_setsockopt")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx",
                           "mov [rbp-24], r8", "mov [rbp-32], r9")
                self.emitf("mov dword [rbp-40], r9d")  # int opt_val on stack
                self.emitf("mov rcx, [rbp-8]", "mov rdx, [rbp-16]",
                           "mov r8, [rbp-24]", "lea r9, [rbp-40]",
                           "mov dword [rsp+32], 4",   # optlen=4 (5th arg)
                           "call setsockopt", "leave", "ret")

            # _net_getsockopt_int(rcx=fd, rdx=level, r8=optname) -> rax (int value)
            if "_net_getsockopt_int" in self.ffi_called:
                self.emit("extern getsockopt")
                self.label("_net_getsockopt_int")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx", "mov [rbp-24], r8")
                self.emitf("xor rax, rax", "mov [rbp-32], rax")      # opt_val=0
                self.emitf("mov dword [rbp-40], 4")                   # optlen=4
                self.emitf("mov rcx, [rbp-8]", "mov rdx, [rbp-16]",
                           "mov r8, [rbp-24]", "lea r9, [rbp-32]",
                           "lea rax, [rbp-40]", "mov [rsp+32], rax",  # &optlen (5th arg)
                           "call getsockopt",
                           "mov eax, dword [rbp-32]", "leave", "ret")

        # SDL2 GUI helpers (Windows)
        if needs_gui:
            self.emit("extern SDL_PollEvent")
            self.emit("extern SDL_WaitEvent")
            self.emit("extern SDL_RenderFillRect")
            self.emit("extern SDL_RenderDrawRect")
            self.emit("extern SDL_RenderCopy")
            self.emit("extern SDL_QueryTexture")

            self.emit("section .bss")
            self.emit("_gui_event_buf: resb 56")
            self.emit("_gui_tex_dim: resd 2")
            self.emit("section .text")

            # _gui_load_bmp(rcx=path) -> rax (SDL_Surface* handle, or 0 on failure)
            # SDL_LoadBMP is a macro for SDL_LoadBMP_RW(SDL_RWFromFile(path, "rb"), 1).
            if "_gui_load_bmp" in self.ffi_called:
                self.emit("extern SDL_RWFromFile")
                self.emit("extern SDL_LoadBMP_RW")
                self.emit("section .rdata")
                self.emit('_gui_bmp_mode_rb: db "rb",0')
                self.emit("section .text")
                self.label("_gui_load_bmp")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("mov [rbp-8], rcx",
                           "lea rdx, [rel _gui_bmp_mode_rb]",
                           "mov rcx, [rbp-8]",
                           "call SDL_RWFromFile",
                           "test rax, rax", "jz ._glb_fail",
                           "mov rcx, rax", "mov rdx, 1",
                           "call SDL_LoadBMP_RW",
                           "leave", "ret")
                self.label("._glb_fail")
                self.emitf("xor rax, rax", "leave", "ret")

            if "_gui_fill_rect" in self.ffi_called:
                self.label("_gui_fill_rect")
                # rcx=renderer, rdx=x, r8=y, r9=w, [rsp+40]=h (5th arg on stack)
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx",
                           "mov [rbp-24], r8", "mov [rbp-32], r9")
                # 5th arg (h) is at [rbp+48] (shadow(32)+ret(8)+rbp(8) = 48)
                self.emitf("mov rax, [rbp+48]", "mov [rbp-40], rax")
                self.emitf("sub rsp, 16",
                           "mov eax, dword [rbp-16]", "mov dword [rsp], eax",
                           "mov eax, dword [rbp-24]", "mov dword [rsp+4], eax",
                           "mov eax, dword [rbp-32]", "mov dword [rsp+8], eax",
                           "mov eax, dword [rbp-40]", "mov dword [rsp+12], eax",
                           "mov rcx, [rbp-8]", "mov rdx, rsp",
                           "call SDL_RenderFillRect",
                           "add rsp, 16", "leave", "ret")

            if "_gui_draw_rect" in self.ffi_called:
                self.label("_gui_draw_rect")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx",
                           "mov [rbp-24], r8", "mov [rbp-32], r9")
                self.emitf("mov rax, [rbp+48]", "mov [rbp-40], rax")
                self.emitf("sub rsp, 16",
                           "mov eax, dword [rbp-16]", "mov dword [rsp], eax",
                           "mov eax, dword [rbp-24]", "mov dword [rsp+4], eax",
                           "mov eax, dword [rbp-32]", "mov dword [rsp+8], eax",
                           "mov eax, dword [rbp-40]", "mov dword [rsp+12], eax",
                           "mov rcx, [rbp-8]", "mov rdx, rsp",
                           "call SDL_RenderDrawRect",
                           "add rsp, 16", "leave", "ret")

            if "_gui_poll_event" in self.ffi_called:
                self.label("_gui_poll_event")
                self.emitf("sub rsp, 32",
                           "lea rcx, [_gui_event_buf]", "call SDL_PollEvent",
                           "add rsp, 32",
                           "test rax, rax", "jz ._gpe_none",
                           "mov eax, dword [_gui_event_buf]", "ret")
                self.label("._gpe_none")
                self.emitf("xor rax, rax", "ret")

            if "_gui_wait_event" in self.ffi_called:
                self.label("_gui_wait_event")
                self.emitf("sub rsp, 32",
                           "lea rcx, [_gui_event_buf]", "call SDL_WaitEvent",
                           "add rsp, 32",
                           "mov eax, dword [_gui_event_buf]", "ret")

            if "_gui_key_scancode" in self.ffi_called:
                self.label("_gui_key_scancode")
                self.emitf("movsx rax, dword [_gui_event_buf+16]", "ret")

            if "_gui_mouse_x" in self.ffi_called:
                self.label("_gui_mouse_x")
                self.emitf("movsx rax, dword [_gui_event_buf+16]", "ret")

            if "_gui_mouse_y" in self.ffi_called:
                self.label("_gui_mouse_y")
                self.emitf("movsx rax, dword [_gui_event_buf+20]", "ret")

            if "_gui_mouse_button" in self.ffi_called:
                self.label("_gui_mouse_button")
                self.emitf("movzx rax, byte [_gui_event_buf+13]", "ret")

            # _gui_render_copy(rcx=renderer, rdx=texture, r8=x, r9=y,
            #                  [rbp+48]=w, [rbp+56]=h) -> rax
            # Builds SDL_Rect{x,y,w,h} on stack, calls SDL_RenderCopy(ren,tex,NULL,&rect).
            if "_gui_render_copy" in self.ffi_called:
                self.label("_gui_render_copy")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 80")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx")
                # Build SDL_Rect at [rbp-48] (above shadow space [rbp-80..rbp-49])
                self.emitf("mov dword [rbp-48], r8d",   # rect.x
                           "mov dword [rbp-44], r9d",   # rect.y
                           "mov eax, dword [rbp+48]",   # w (5th arg)
                           "mov dword [rbp-40], eax",   # rect.w
                           "mov eax, dword [rbp+56]",   # h (6th arg)
                           "mov dword [rbp-36], eax")   # rect.h
                self.emitf("mov rcx, [rbp-8]", "mov rdx, [rbp-16]",
                           "xor r8, r8",
                           "lea r9, [rbp-48]",
                           "call SDL_RenderCopy",
                           "leave", "ret")

            # _gui_query_texture_w(rcx=texture) -> rax (width as signed 64-bit)
            # _gui_query_texture_h(rcx=texture) -> rax (height as signed 64-bit)
            # Both call SDL_QueryTexture(texture, NULL, NULL, &w, &h) and return one dim.
            if "_gui_query_texture_w" in self.ffi_called or "_gui_query_texture_h" in self.ffi_called:
                self.emitf("")  # blank line separator
            if "_gui_query_texture_w" in self.ffi_called:
                self.label("_gui_query_texture_w")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                # SDL_QueryTexture(texture, format=NULL, access=NULL, &w, &h)
                # rcx already=texture; 5th arg &h goes at [rsp+32]=[rbp-16]
                self.emitf("xor rdx, rdx", "xor r8, r8",
                           "lea r9, [_gui_tex_dim]",
                           "lea rax, [_gui_tex_dim+4]",
                           "mov [rsp+32], rax",
                           "call SDL_QueryTexture",
                           "movsx rax, dword [_gui_tex_dim]",
                           "leave", "ret")
            if "_gui_query_texture_h" in self.ffi_called:
                self.label("_gui_query_texture_h")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("xor rdx, rdx", "xor r8, r8",
                           "lea r9, [_gui_tex_dim]",
                           "lea rax, [_gui_tex_dim+4]",
                           "mov [rsp+32], rax",
                           "call SDL_QueryTexture",
                           "movsx rax, dword [_gui_tex_dim+4]",
                           "leave", "ret")

            # _gui_is_key_down(rcx=scancode) -> rax (0 or 1)
            # SDL_GetKeyboardState(NULL) returns Uint8* indexed by scancode.
            if "_gui_is_key_down" in self.ffi_called:
                self.emit("extern SDL_GetKeyboardState")
                self.label("_gui_is_key_down")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rcx",
                           "xor rcx, rcx",
                           "call SDL_GetKeyboardState",
                           "mov rcx, [rbp-8]",
                           "movzx rax, byte [rax + rcx]",
                           "leave", "ret")

            # _gui_mouse_dx/dy() -> rax (relative motion since last call)
            if "_gui_mouse_dx" in self.ffi_called or "_gui_mouse_dy" in self.ffi_called:
                self.emit("extern SDL_GetRelativeMouseState")
                self.emit("section .bss")
                self.emit("_gui_rel_dim: resd 2")
                self.emit("section .text")
            if "_gui_mouse_dx" in self.ffi_called:
                self.label("_gui_mouse_dx")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("lea rcx, [_gui_rel_dim]",
                           "lea rdx, [_gui_rel_dim+4]",
                           "call SDL_GetRelativeMouseState",
                           "movsx rax, dword [_gui_rel_dim]",
                           "leave", "ret")
            if "_gui_mouse_dy" in self.ffi_called:
                self.label("_gui_mouse_dy")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("lea rcx, [_gui_rel_dim]",
                           "lea rdx, [_gui_rel_dim+4]",
                           "call SDL_GetRelativeMouseState",
                           "movsx rax, dword [_gui_rel_dim+4]",
                           "leave", "ret")

        if needs_audio:
            # _audio_load_wav(rcx=path) -> rax (Mix_Chunk* handle, or 0 on failure)
            # Mix_LoadWAV is a macro for Mix_LoadWAV_RW(SDL_RWFromFile(path, "rb"), 1).
            if "_audio_load_wav" in self.ffi_called:
                self.emit("extern SDL_RWFromFile")
                self.emit("extern Mix_LoadWAV_RW")
                self.emit("section .rdata")
                self.emit('_audio_wav_mode_rb: db "rb",0')
                self.emit("section .text")
                self.label("_audio_load_wav")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("mov [rbp-8], rcx",
                           "lea rdx, [rel _audio_wav_mode_rb]",
                           "mov rcx, [rbp-8]",
                           "call SDL_RWFromFile",
                           "test rax, rax", "jz ._alw_fail",
                           "mov rcx, rax", "mov rdx, 1",
                           "call Mix_LoadWAV_RW",
                           "leave", "ret")
                self.label("._alw_fail")
                self.emitf("xor rax, rax", "leave", "ret")

        # ---- SDL2_ttf font rendering helpers -----------------------------------
        # SDL_Color {r,g,b,a} (4 bytes) is passed by value in a single GP
        # register under Win64; we pack r/g/b (alpha fixed at 255) ourselves.
        if needs_ttf:
            self.emit("extern TTF_RenderText_Blended")
            self.emit("extern TTF_SizeText")
            self.emit("section .bss")
            self.emit("_ttf_size_dim: resd 2")
            self.emit("section .text")

            # _ttf_render_blended(rcx=font, rdx=text, r8=r, r9=g, [rbp+48]=b) -> rax
            # Real C call is TTF_RenderText_Blended(font, text, SDL_Color fg) --
            # only 3 args; fg is a 4-byte struct passed by value in one register.
            if "_ttf_render_blended" in self.ffi_called:
                self.label("_ttf_render_blended")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx")
                self.emitf("mov [rbp-24], r8", "mov [rbp-32], r9")
                self.emitf("mov rax, [rbp+48]", "mov [rbp-40], rax")  # b (5th arg)
                # Pack SDL_Color {r,g,b,a=255} into one 32-bit value: r | g<<8 | b<<16 | 255<<24
                self.emitf("movzx rax, byte [rbp-24]",   # r
                           "movzx r10, byte [rbp-32]",   # g
                           "shl r10, 8", "or rax, r10",
                           "movzx r10, byte [rbp-40]",   # b
                           "shl r10, 16", "or rax, r10",
                           "mov r10, 255",
                           "shl r10, 24", "or rax, r10")
                self.emitf("mov rcx, [rbp-8]", "mov rdx, [rbp-16]",
                           "mov r8d, eax",   # fg (3rd real arg)
                           "call TTF_RenderText_Blended",
                           "leave", "ret")

            # _ttf_size_text_w/h(rcx=font, rdx=text) -> rax (pixel width/height)
            if "_ttf_size_text_w" in self.ffi_called:
                self.label("_ttf_size_text_w")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("lea r8, [_ttf_size_dim]",
                           "lea r9, [_ttf_size_dim+4]",
                           "call TTF_SizeText",
                           "movsx rax, dword [_ttf_size_dim]",
                           "leave", "ret")
            if "_ttf_size_text_h" in self.ffi_called:
                self.label("_ttf_size_text_h")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("lea r8, [_ttf_size_dim]",
                           "lea r9, [_ttf_size_dim+4]",
                           "call TTF_SizeText",
                           "movsx rax, dword [_ttf_size_dim+4]",
                           "leave", "ret")

        # ---- real Win32 threading helpers ------------------------------------
        # All helpers use Win64 ABI: args in rcx/rdx/r8/r9, shadow space 32B.
        # Thread objects are asmpython dicts; we store the HANDLE in "._handle".
        # Lock  objects store a heap-alloc'd CRITICAL_SECTION* in "._cs".
        if needs_thread:
            self.emit("extern CreateThread")
            self.emit("extern WaitForSingleObject")
            self.emit("extern CloseHandle")
            self.emit("extern InitializeCriticalSection")
            self.emit("extern EnterCriticalSection")
            self.emit("extern LeaveCriticalSection")
            self.emit("extern DeleteCriticalSection")
            self.emit("extern GetCurrentThreadId")
            self.emit("section .rodata")
            # Intern the field-name strings needed by the trampoline.
            _target_lbl, _ = self.intern_string("target")
            _handle_lbl, _ = self.intern_string("_handle")
            _cs_lbl, _     = self.intern_string("_cs")
            self.emit("section .text")

            # _threading_trampoline(rcx=fn_ptr) -> rax=0
            # Called by Win32 on the new thread. rcx is the target function pointer.
            # Must match LPTHREAD_START_ROUTINE signature.
            self.label("_threading_trampoline")
            self.emitf(
                "push rbp", "mov rbp, rsp", "sub rsp, 48",
                "mov [rbp-8], rcx",          # save fn ptr
                "test rcx, rcx",
                "jz ._tt_done",
                "call rcx",                  # call target() with no args
            )
            self.label("._tt_done")
            self.emitf("xor rax, rax", "leave", "ret")

            # _threading_create(rcx=fn_ptr) -> rax=handle (HANDLE, 64-bit)
            if "_threading_create" in self.ffi_called:
                self.label("_threading_create")
                self.emitf(
                    "push rbp", "mov rbp, rsp", "sub rsp, 80",
                    "mov [rbp-8], rcx",      # save fn_ptr
                    # CreateThread(NULL, 0, trampoline, fn_ptr, 0, NULL)
                    "xor rcx, rcx",          # lpThreadAttributes = NULL
                    "xor rdx, rdx",          # dwStackSize = 0
                    "lea r8, [_threading_trampoline]",  # lpStartAddress
                    "mov r9, [rbp-8]",       # lpParameter = fn_ptr (passed to trampoline as rcx)
                    "mov qword [rsp+32], 0", # dwCreationFlags = 0
                    "mov qword [rsp+40], 0", # lpThreadId = NULL
                    "call CreateThread",
                    "leave", "ret",
                )

            # _threading_join(rcx=handle) -> rax=0
            if "_threading_join" in self.ffi_called:
                self.label("_threading_join")
                self.emitf(
                    "push rbp", "mov rbp, rsp", "sub rsp, 48",
                    "mov [rbp-8], rcx",
                    "mov edx, 0xFFFFFFFF",   # INFINITE (32-bit, zero-extended to rdx)
                    "call WaitForSingleObject",
                    "mov rcx, [rbp-8]",
                    "call CloseHandle",
                    "xor rax, rax", "leave", "ret",
                )

            # _threading_is_alive(rcx=handle) -> rax: 1 if still running, 0 if done
            if "_threading_is_alive" in self.ffi_called:
                self.emit("extern GetExitCodeThread")
                self.label("_threading_is_alive")
                self.emitf(
                    "push rbp", "mov rbp, rsp", "sub rsp, 48",
                    "mov [rbp-8], rcx",
                    "xor rdx, rdx",
                    "mov qword [rbp-16], 0",
                    "lea rdx, [rbp-16]",
                    "call GetExitCodeThread",
                    "mov rax, [rbp-16]",
                    # STILL_ACTIVE = 259 (0x103); alive if exit code == 259
                    "cmp rax, 259",
                    "sete al", "movzx rax, al",
                    "leave", "ret",
                )

            # _threading_get_ident() -> rax = thread id (DWORD, zero-extended)
            if "_threading_get_ident" in self.ffi_called:
                self.label("_threading_get_ident")
                self.emitf("sub rsp, 32", "call GetCurrentThreadId",
                           "mov eax, eax", "add rsp, 32", "ret")

            # _threading_active_count() -> rax (stub: always 1, no global tracking)
            if "_threading_active_count" in self.ffi_called:
                self.label("_threading_active_count")
                self.emitf("mov rax, 1", "ret")

            # _threading_lock_init(rcx=lock_obj_ptr) -> rax=cs_ptr
            # Allocates a CRITICAL_SECTION (40 bytes on Win64), inits it, returns ptr.
            if "_threading_lock_init" in self.ffi_called:
                self.label("_threading_lock_init")
                self.emitf(
                    "push rbp", "mov rbp, rsp", "sub rsp, 48",
                    "mov rcx, 40",   # sizeof(CRITICAL_SECTION) on Win64
                    "call malloc",
                    "mov [rbp-8], rax",
                    "mov rcx, rax",
                    "call InitializeCriticalSection",
                    "mov rax, [rbp-8]",
                    "leave", "ret",
                )

            # _threading_lock_acquire(rcx=cs_ptr) -> rax=1
            if "_threading_lock_acquire" in self.ffi_called:
                self.label("_threading_lock_acquire")
                self.emitf(
                    "push rbp", "mov rbp, rsp", "sub rsp, 32",
                    "call EnterCriticalSection",
                    "mov rax, 1", "leave", "ret",
                )

            # _threading_lock_release(rcx=cs_ptr) -> rax=0
            if "_threading_lock_release" in self.ffi_called:
                self.label("_threading_lock_release")
                self.emitf(
                    "push rbp", "mov rbp, rsp", "sub rsp, 32",
                    "call LeaveCriticalSection",
                    "xor rax, rax", "leave", "ret",
                )

            # _threading_lock_destroy(rcx=cs_ptr)
            if "_threading_lock_destroy" in self.ffi_called:
                self.label("_threading_lock_destroy")
                self.emitf(
                    "push rbp", "mov rbp, rsp", "sub rsp, 32",
                    "call DeleteCriticalSection",
                    "leave", "ret",
                )
