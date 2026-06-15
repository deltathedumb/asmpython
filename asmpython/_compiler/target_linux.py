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
    target_name = "LinuxCodegen"
    section_text = "section .text"
    section_data = "section .data"
    section_rodata = "section .rodata"
    label_main = "main"  # we let libc's _start call main()

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
            "atoll",
            "strtoll",
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
        # main(argc, argv): SysV passes these in rdi/rsi. Stash them before
        # they're clobbered so sys.argv can be built from them.
        self.emitf("mov [rel _prog_argc], rdi", "mov [rel _prog_argv], rsi")
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
        self.emitf(
            "mov rsi, rax",
            "lea rdi, [fmt_int]",
            "xor rax, rax",  # variadic: zero AL = no XMM args
            "call printf",
        )

    def _emit_print_str_ptr_no_newline(self) -> None:
        self.emitf("mov rsi, rax", "lea rdi, [fmt_str]", "xor rax, rax", "call printf")

    def _emit_print_space(self) -> None:
        self.emitf("mov rdi, 32", "call putchar")

    def _emit_print_newline(self) -> None:
        self.emitf("mov rdi, 10", "call putchar")

    def _emit_strlen(self) -> None:
        self.emitf("mov rdi, rax", "call strlen")

    def _emit_int_to_str(self) -> None:
        # sprintf(buf, "%lld", rax); return buf in rax
        self.emitf(
            "mov rdx, rax",  # third arg
            "lea rsi, [fmt_int]",  # second arg
            "lea rdi, [itoa_str_buf]",  # first arg
            "xor rax, rax",
            "call sprintf",
            "lea rax, [itoa_str_buf]",
        )

    def _emit_str_to_int(self) -> None:
        self.emitf("mov rdi, rax", "call atoll")

    def _emit_str_to_int_base(self) -> None:
        # Normalize Python's 0b prefix (strtoll base 0 doesn't grok it), then
        # strtoll(str, endptr=NULL, base): SysV args rdi, rsi, rdx.
        self._emit_normalize_0b_prefix()
        self.emitf(
            "mov rdx, rbx",  # base
            "xor rsi, rsi",  # endptr = NULL
            "mov rdi, rax",  # str
            "call strtoll",
        )

    def _emit_input_line(self) -> None:
        self.emitf("call _runtime_input")

    def _emit_malloc(self, n: int) -> None:
        self.emitf(f"mov rdi, {n}", "call malloc")

    def _emit_print_float_no_newline(self) -> None:
        # Route through _emit_float_to_str (sprintf "%g" + repr fixup) so
        # whole numbers print as "2.0" not "2", matching CPython.
        self._emit_float_to_str()
        self._emit_print_str_ptr_no_newline()

    def _emit_float_to_str(self) -> None:
        self.emitf(
            "lea rdi, [itoa_str_buf]",
            "lea rsi, [fmt_flt]",
            "mov al, 1",
            "call sprintf",
            "lea rax, [itoa_str_buf]",
        )
        self._emit_float_repr_fixup()

    def _emit_float_fmt(self, fmt_label: str) -> None:
        # sprintf(buf, fmt, xmm0). SysV: al = number of vector regs used (1).
        self.emitf(
            "lea rdi, [itoa_str_buf]",
            f"lea rsi, [{fmt_label}]",
            "mov al, 1",
            "call sprintf",
            "lea rax, [itoa_str_buf]",
        )

    def _emit_int_fmt(self, fmt_label: str) -> None:
        # sprintf(buf, fmt, rax). rax(value) -> rdx (3rd int arg); al = 0 vec regs.
        self.emitf(
            "mov rdx, rax",
            "lea rdi, [itoa_str_buf]",
            f"lea rsi, [{fmt_label}]",
            "xor al, al",
            "call sprintf",
            "lea rax, [itoa_str_buf]",
        )

    def _emit_str_to_float(self) -> None:
        self.emitf("mov rdi, rax", "call atof")

    def _emit_call_libc_double_double(self, fn: str) -> None:
        self.emitf(f"call {fn}")

    def _emit_libc_malloc_size_in_rax(self) -> None:
        self.emitf("mov rdi, rax", "call malloc")

    def _emit_libc_memset_zero(self) -> None:
        self.emitf("mov rdi, rax", "xor rsi, rsi", "mov rdx, rbx", "call memset")

    def _emit_libc_strcmp(self) -> None:
        self.emitf("mov rdi, rax", "mov rsi, rbx", "call strcmp", "movsxd rax, eax")

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
        # Use asmpython's hand-rolled _runtime_setjmp (buf in rax).
        self.emitf(f"lea rax, [rbp{buf_off:+d}]", "call _runtime_setjmp")

    def _emit_call_longjmp_with_buf_in_rax(self) -> None:
        # Unused; kept for API symmetry.
        self.emitf("mov rbx, 1", "call _runtime_longjmp")

    # ---- os.getcwd / os.listdir (Linux) -------------------------------------

    def _emit_os_getcwd(self) -> None:
        self._needs_cwd_buf = True
        fail_lbl = self.fresh("cwd_fail")
        done_lbl = self.fresh("cwd_done")
        empty_lbl, _ = self.intern_string("")
        self.emitf(
            "lea rdi, [rel _cwd_buf]",
            "mov esi, 4096",
            "xor eax, eax",
            "call getcwd",
            "test rax, rax",
            f"jz {fail_lbl}",
            "call _runtime_str_concat_dup",
            f"jmp {done_lbl}",
        )
        self.label(fail_lbl)
        self.emitf(f"lea rax, [rel {empty_lbl}]")
        self.label(done_lbl)
        self.ffi_externs.add("getcwd")

    def _emit_os_listdir(self, path_arg, info: FuncInfo) -> None:
        lbl_loop = self.fresh("listdir_loop")
        lbl_done = self.fresh("listdir_done")
        lbl_nl   = self.fresh("listdir_nl")
        if path_arg is not None:
            cmd_pfx_lbl, _ = self.intern_string("ls -1 ")
            self.gen_expr(path_arg, info)
            self.emitf("mov rbx, rax")
            self.emitf(f"lea rax, [rel {cmd_pfx_lbl}]")
            self.emitf("call _runtime_str_concat")
        else:
            cmd_lbl, _ = self.intern_string("ls -1")
            self.emitf(f"lea rax, [rel {cmd_lbl}]")
        mode_lbl, _ = self.intern_string("r")
        self.emitf(
            "mov rdi, rax",
            f"lea rsi, [rel {mode_lbl}]",
            "xor eax, eax",
            "call popen",
        )
        pipe_slot = info.locals_[f"__listdir_pipe_{id(path_arg)}"]
        acc_slot  = info.locals_[f"__listdir_acc_{id(path_arg)}"]
        line_slot = info.locals_[f"__listdir_line_{id(path_arg)}"]
        char_slot = info.locals_[f"__listdir_char_{id(path_arg)}"]
        empty_lbl, _ = self.intern_string("")
        self.emitf(f"mov [rbp{pipe_slot:+d}], rax")
        # allocate empty list (cap=4, len=0)
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            f"mov qword [rax+{self.LIST_CAP_OFF}], 4",
            f"mov qword [rax+{self.LIST_LEN_OFF}], 0",
            f"mov [rbp{acc_slot:+d}], rax",
        )
        self._emit_malloc(32)
        self.emitf(
            f"mov rbx, [rbp{acc_slot:+d}]",
            f"mov [rbx+{self.LIST_BUF_OFF}], rax",
        )
        self.emitf(f"lea rax, [rel {empty_lbl}]", "call _runtime_str_concat_dup",
                   f"mov [rbp{line_slot:+d}], rax")
        self.label(lbl_loop)
        self.emitf(
            f"mov rdi, [rbp{pipe_slot:+d}]",
            "xor eax, eax",
            "call fgetc",
            "movsxd rax, eax",
            f"mov [rbp{char_slot:+d}], rax",
            "cmp rax, -1",
            f"je {lbl_done}",
            "cmp rax, 10",
            f"je {lbl_nl}",
            "cmp rax, 13",
            f"je {lbl_loop}",
        )
        self.emitf("call _runtime_chr")
        self.emitf(
            "mov rbx, rax",
            f"mov rax, [rbp{line_slot:+d}]",
            "call _runtime_str_concat",
            f"mov [rbp{line_slot:+d}], rax",
            f"jmp {lbl_loop}",
        )
        self.label(lbl_nl)
        skip_lbl = self.fresh("listdir_skip")
        self.emitf(
            f"mov rax, [rbp{line_slot:+d}]",
            "mov rdi, rax",
            "xor eax, eax",
            "call strlen",
            "test rax, rax",
            f"jz {skip_lbl}",
        )
        self.emitf(
            f"mov rax, [rbp{acc_slot:+d}]",
            f"mov rbx, [rbp{line_slot:+d}]",
            "call _runtime_list_append",
        )
        self.label(skip_lbl)
        self.emitf(f"lea rax, [rel {empty_lbl}]", "call _runtime_str_concat_dup",
                   f"mov [rbp{line_slot:+d}], rax",
                   f"jmp {lbl_loop}")
        self.label(lbl_done)
        self.emitf(
            f"mov rdi, [rbp{pipe_slot:+d}]",
            "xor eax, eax",
            "call pclose",
        )
        self.emitf(f"mov rax, [rbp{acc_slot:+d}]")
        for sym in ("popen", "pclose", "fgetc", "strlen"):
            if sym not in self.ffi_externs and sym not in self.asm_pkg_symbols:
                self.ffi_externs.add(sym)

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

        self._emit_cwd_buf_if_needed()

        self.emit("section .rodata")
        self.emit('fmt_int: db "%lld",0')
        self.emit('fmt_str: db "%s",0')
        self.emit('fmt_flt: db "%g",0')

        if self.use_runtime_lib:
            # Skip emitting helper bodies; runtime library provides them.
            for sym in ("_runtime_input", "_runtime_list_append", "_runtime_list_pop", "_runtime_list_del"):
                self.emit(f"extern {sym}")
            # Still emit the shared runtime (dict/exception) references.
            self.emit_dict_runtime()
            self.emit_string_runtime()
            self.emit_exception_runtime()
            return

        self.emit("section .text")
        self.label("_runtime_input")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
        self.emitf(
            "mov rdx, [stdin]", "mov esi, 255", "lea rdi, [input_buf]", "call fgets"
        )
        self.emitf(
            "lea rdi, [input_buf]",
            "call strlen",
            "lea rdi, [input_buf]",
            "test rax, rax",
            "jz ._li_done",
            "mov dl, [rdi+rax-1]",
            "cmp dl, 10",
            "jne ._li_done",
            "dec rax",
            "mov byte [rdi+rax], 0",
        )
        self.label("._li_done")
        self.emitf("lea rax, [input_buf]", "leave", "ret")

        # List runtime: append/pop with stable header + relocatable buffer.
        self.label("_runtime_list_append")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
        self.emitf("mov [rbp-8], rax", "mov [rbp-16], rbx")
        self.emitf("mov rcx, [rax+8]", "cmp rcx, [rax]", "jl ._la_store")
        self.emitf(
            "mov rcx, [rax]", "shl rcx, 1", "cmp rcx, 4", "jge ._la_grow", "mov rcx, 4"
        )
        self.label("._la_grow")
        self.emitf(
            "mov [rbp-24], rcx",
            "shl rcx, 3",
            "mov rsi, rcx",  # 2nd arg = new size
            "mov rax, [rbp-8]",
            "mov rdi, [rax+16]",  # 1st arg = old buf ptr
            "call realloc",
        )
        self.emitf(
            "mov rbx, [rbp-8]",
            "mov [rbx+16], rax",
            "mov rdx, [rbp-24]",
            "mov [rbx], rdx",
        )
        self.label("._la_store")
        self.emitf(
            "mov rax, [rbp-8]",
            "mov rcx, [rax+8]",
            "mov rbx, [rbp-16]",
            "mov rdx, [rax+16]",
            "mov [rdx+rcx*8], rbx",
            "inc qword [rax+8]",
            "leave",
            "ret",
        )

        self.label("_runtime_list_pop")
        self.emitf(
            "mov rcx, [rax+8]",
            "dec rcx",
            "mov [rax+8], rcx",
            "mov rdx, [rax+16]",
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

    # ---- asmlib runtime (Linux / SysV ABI) ----------------------------------

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
    _NET_SYMS = (
        "_net_bind", "_net_connect", "_net_send", "_net_recv",
        "_net_send_all", "_net_accept", "_net_close",
        "_net_gethostname", "_net_errno",
    )
    _GUI_SYMS = (
        "_gui_fill_rect", "_gui_draw_rect", "_gui_poll_event",
        "_gui_wait_event", "_gui_key_scancode",
        "_gui_mouse_x", "_gui_mouse_y", "_gui_mouse_button",
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
    )
    _RANDOM_SYMS = (
        "_random_random", "_random_randint",
        "_random_uniform", "_random_randrange",
        "_random_choice", "_random_shuffle",
    )
    _TIME_SYMS = (
        "_time_perf_counter", "_time_time_ns", "_time_sleep_ms",
    )

    def _asmlib_inline_syms(self) -> set:
        return (set(self._HW_STUBS) | set(self._NET_SYMS) | set(self._GUI_SYMS)
                | set(self._MATH_SYMS) | set(self._RANDOM_SYMS) | set(self._TIME_SYMS))

    def emit_asmlib_runtime(self) -> None:
        needs_hw   = any(s in self.ffi_externs for s in self._HW_STUBS)
        needs_net  = any(s in self.ffi_externs for s in self._NET_SYMS)
        needs_gui  = any(s in self.ffi_externs for s in self._GUI_SYMS)
        needs_math   = any(s in self.ffi_externs for s in self._MATH_SYMS)
        needs_random = any(s in self.ffi_externs for s in self._RANDOM_SYMS)
        needs_time   = any(s in self.ffi_externs for s in self._TIME_SYMS)
        if not (needs_hw or needs_net or needs_gui or needs_math or needs_random or needs_time):
            return

        self.emit("")
        self.emit("section .text")

        # ---- math helpers (ABI-neutral x87/SSE2 implementations) ------------
        if needs_math:
            self.emit("extern log")
            self.emit("extern modf")
            self.emit("extern frexp")
            self.emit("section .rodata")
            self.emit("_math_deg_factor:  dq 57.29577951308232")   # 180/pi
            self.emit("_math_rad_factor:  dq 0.017453292519943295")  # pi/180
            needs_inf_consts = any(s in self.ffi_externs for s in (
                "_math_isinf", "_math_isfinite"))
            if needs_inf_consts:
                self.emit("section .rodata")
                self.emit("_math_inf_bits:  dq 0x7FF0000000000000")
                self.emit("_math_abs_mask:  dq 0x7FFFFFFFFFFFFFFF")
            self.emit("section .text")

            # _math_isnan(xmm0=x) -> rax: 1 if NaN (NaN is the only float where x!=x)
            if "_math_isnan" in self.ffi_externs:
                self.label("_math_isnan")
                self.emitf("ucomisd xmm0, xmm0", "setp al", "movzx rax, al", "ret")

            # _math_isinf(xmm0=x) -> rax: 1 if +inf or -inf
            if "_math_isinf" in self.ffi_externs:
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

            # _math_isfinite(xmm0=x) -> rax: 1 if finite
            if "_math_isfinite" in self.ffi_externs:
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

            # _math_degrees(xmm0=radians) -> xmm0=degrees
            if "_math_degrees" in self.ffi_externs:
                self.label("_math_degrees")
                self.emitf("mulsd xmm0, [rel _math_deg_factor]", "ret")

            # _math_radians(xmm0=degrees) -> xmm0=radians
            if "_math_radians" in self.ffi_externs:
                self.label("_math_radians")
                self.emitf("mulsd xmm0, [rel _math_rad_factor]", "ret")

            # _math_gcd(rdi=a, rsi=b) -> rax=gcd  (Euclidean, always positive)
            if "_math_gcd" in self.ffi_externs:
                self.label("_math_gcd")
                self.emitf(
                    "mov rax, rdi", "mov rcx, rsi",
                    # abs(a)
                    "test rax, rax", "jns ._mg_apos", "neg rax",
                )
                self.label("._mg_apos")
                self.emitf("test rcx, rcx", "jns ._mg_bpos", "neg rcx")
                self.label("._mg_bpos")
                # Euclid: while b != 0: a, b = b, a % b
                self.label("._mg_loop")
                self.emitf("test rcx, rcx", "jz ._mg_done")
                self.emitf("xor rdx, rdx", "div rcx", "mov rax, rcx", "mov rcx, rdx",
                           "jmp ._mg_loop")
                self.label("._mg_done")
                self.emitf("ret")

            # _math_lcm(rdi=a, rsi=b) -> rax=lcm  (|a*b| / gcd(a,b))
            if "_math_lcm" in self.ffi_externs:
                self.label("_math_lcm")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi")
                self.emitf("call _math_gcd")
                self.emitf("test rax, rax", "jz ._mlcm_zero")
                self.emitf("mov rcx, rax")
                self.emitf("mov rax, [rbp-8]", "test rax, rax", "jns ._mlcm_apos", "neg rax")
                self.label("._mlcm_apos")
                self.emitf("xor rdx, rdx", "div rcx")  # a/gcd
                self.emitf("mov rcx, [rbp-16]", "test rcx, rcx", "jns ._mlcm_bpos", "neg rcx")
                self.label("._mlcm_bpos")
                self.emitf("imul rax, rcx", "leave", "ret")
                self.label("._mlcm_zero")
                self.emitf("xor rax, rax", "leave", "ret")

            # _math_factorial(rdi=n) -> rax=n!
            if "_math_factorial" in self.ffi_externs:
                self.label("_math_factorial")
                self.emitf("mov rax, 1", "cmp rdi, 1", "jle ._mf_done")
                self.label("._mf_loop")
                self.emitf("imul rax, rdi", "dec rdi", "cmp rdi, 1", "jg ._mf_loop")
                self.label("._mf_done")
                self.emitf("ret")

            # _math_comb(rdi=n, rsi=k) -> rax=C(n,k)
            # C(n,k) = n! / (k! * (n-k)!) computed iteratively to avoid overflow
            if "_math_comb" in self.ffi_externs:
                self.label("_math_comb")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi")
                # if k > n-k: k = n-k
                self.emitf("mov rax, rdi", "sub rax, rsi", "cmp rsi, rax", "jle ._mc_kset")
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

            # _math_perm(rdi=n, rsi=k) -> rax=P(n,k) = n! / (n-k)!
            if "_math_perm" in self.ffi_externs:
                self.label("_math_perm")
                self.emitf("push rbp", "mov rbp, rsp")
                # product of n * (n-1) * ... * (n-k+1)
                self.emitf("mov rax, 1", "mov rcx, 0")
                self.label("._mp_loop")
                self.emitf("cmp rcx, rsi", "jge ._mp_done")
                self.emitf("mov rdx, rdi", "sub rdx, rcx", "imul rax, rdx", "inc rcx",
                           "jmp ._mp_loop")
                self.label("._mp_done")
                self.emitf("pop rbp", "ret")

            # _math_log_base(xmm0=x, xmm1=base) -> xmm0=log(x)/log(base)
            if "_math_log_base" in self.ffi_externs:
                self.label("_math_log_base")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("movsd [rbp-8], xmm1")   # save base
                # log(x) -> xmm0
                self.emitf("mov al, 1", "call log")
                self.emitf("movsd [rbp-16], xmm0")  # save log(x)
                # log(base) -> xmm0
                self.emitf("movsd xmm0, [rbp-8]", "mov al, 1", "call log")
                # log(x) / log(base)
                self.emitf("movsd xmm1, xmm0", "movsd xmm0, [rbp-16]",
                           "divsd xmm0, xmm1", "leave", "ret")

            # _math_modf_frac(xmm0=x) -> xmm0=fractional part  (via modf)
            if "_math_modf_frac" in self.ffi_externs:
                self.label("_math_modf_frac")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("lea rdi, [rbp-8]", "mov al, 1", "call modf")
                # xmm0 = fractional part (modf returns frac in xmm0, int via ptr)
                self.emitf("leave", "ret")

            # _math_modf_int(xmm0=x) -> xmm0=integer part  (via modf)
            if "_math_modf_int" in self.ffi_externs:
                self.label("_math_modf_int")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("lea rdi, [rbp-8]", "mov al, 1", "call modf")
                self.emitf("movsd xmm0, [rbp-8]", "leave", "ret")

            # _math_frexp_m(xmm0=x) -> xmm0=mantissa  (frexp; [0.5,1))
            if "_math_frexp_m" in self.ffi_externs:
                self.label("_math_frexp_m")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("lea rsi, [rbp-8]", "mov al, 1", "call frexp")
                self.emitf("leave", "ret")

            # _math_frexp_e(xmm0=x) -> rax=exponent  (int)
            if "_math_frexp_e" in self.ffi_externs:
                self.label("_math_frexp_e")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("lea rsi, [rbp-8]", "mov al, 1", "call frexp")
                self.emitf("movsxd rax, dword [rbp-8]", "leave", "ret")

            # _math_ldexp(xmm0=x, rdi=n) -> xmm0  (just forwards to libc ldexp)
            if "_math_ldexp" in self.ffi_externs:
                self.emit("extern ldexp")
                self.label("_math_ldexp")
                # SysV: xmm0=x already, rdi=n already — ldexp(double,int) is exact match
                self.emitf("mov al, 1", "call ldexp", "ret")

        # ---- random helpers (SysV ABI) ----------------------------------------
        if needs_random:
            self.emit("extern rand")
            self.emit("section .rodata")
            self.emit("_rand_inv:  dq 3.0517578125e-05")   # 1.0 / (RAND_MAX+1) = 1/32768
            self.emit("section .text")

            # _random_random() -> xmm0 in [0.0, 1.0)
            if "_random_random" in self.ffi_externs:
                self.label("_random_random")
                self.emitf("xor eax, eax", "call rand")
                self.emitf("cvtsi2sd xmm0, rax", "mulsd xmm0, [rel _rand_inv]", "ret")

            # _random_randint(rdi=a, rsi=b) -> rax  in [a, b] inclusive
            if "_random_randint" in self.ffi_externs:
                self.label("_random_randint")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi")
                self.emitf("call rand")
                # range = b - a + 1
                self.emitf("mov rcx, [rbp-16]", "sub rcx, [rbp-8]", "inc rcx")
                self.emitf("xor rdx, rdx", "div rcx")       # rdx = rand % range
                self.emitf("add rdx, [rbp-8]", "mov rax, rdx", "leave", "ret")

            # _random_uniform(xmm0=a, xmm1=b) -> xmm0 in [a, b]
            if "_random_uniform" in self.ffi_externs:
                self.label("_random_uniform")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("movsd [rbp-8], xmm0", "movsd [rbp-16], xmm1")
                self.emitf("call rand")
                self.emitf("cvtsi2sd xmm0, rax", "mulsd xmm0, [rel _rand_inv]")
                # result = a + r * (b - a)
                self.emitf("movsd xmm1, [rbp-16]", "subsd xmm1, [rbp-8]",
                           "mulsd xmm0, xmm1", "addsd xmm0, [rbp-8]", "leave", "ret")

            # _random_randrange(rdi=stop) -> rax in [0, stop)
            if "_random_randrange" in self.ffi_externs:
                self.label("_random_randrange")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("mov [rbp-8], rdi")
                self.emitf("call rand")
                self.emitf("xor rdx, rdx", "div qword [rbp-8]", "mov rax, rdx",
                           "leave", "ret")

            # _random_choice(rdi=list_hdr) -> rax = element at random index
            # List layout: [hdr+0]=cap, [hdr+8]=len, [hdr+16]=buf_ptr
            # Frame: push+40=48 bytes total → 16-byte aligned for rand() call.
            if "_random_choice" in self.ffi_externs:
                self.label("_random_choice")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 40")
                self.emitf("mov [rbp-8], rdi")           # save list header
                self.emitf("mov rax, [rdi+8]")           # rax = len (offset 8)
                self.emitf("mov [rbp-16], rax")          # save len
                self.emitf("call rand")                  # rax = rand()
                self.emitf("xor rdx, rdx", "div qword [rbp-16]")  # rdx = rand % len
                self.emitf("mov rcx, rdx")               # rcx = index
                self.emitf("mov rax, [rbp-8]")           # rax = list header
                self.emitf("mov rax, [rax+16]")          # rax = buf_ptr (offset 16)
                self.emitf("mov rax, [rax+rcx*8]")       # rax = buf[index]
                self.emitf("leave", "ret")

            # _random_shuffle(rdi=list_hdr) — Fisher-Yates shuffle in-place
            # Frame: push+56=64 bytes total → 16-byte aligned for rand() call.
            if "_random_shuffle" in self.ffi_externs:
                lbl_loop = self.fresh("shuffle_loop")
                lbl_done = self.fresh("shuffle_done")
                self.label("_random_shuffle")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 56")
                self.emitf("mov [rbp-8], rdi")           # save list header
                self.emitf("mov rax, [rdi+8]")           # rax = n (len at offset 8)
                self.emitf("mov [rbp-16], rax")          # i = n (count down)
                self.label(lbl_loop)
                self.emitf(f"cmp qword [rbp-16], 1", f"jle {lbl_done}")
                self.emitf("call rand")
                self.emitf("xor rdx, rdx", "div qword [rbp-16]")  # rdx = j
                self.emitf("mov [rbp-24], rdx")           # save j
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

        # ---- time helpers (Linux / SysV ABI) ---------------------------------
        # clock_gettime(CLOCK_MONOTONIC=1, &timespec) gives ns resolution.
        if needs_time:
            self.emit("extern clock_gettime")
            self.emit("section .bss")
            self.emit("_time_ts: resq 2")   # struct timespec: tv_sec(8) + tv_nsec(8)
            self.emit("section .rodata")
            self.emit("_time_1e9:  dq 1000000000.0")
            self.emit("section .text")

            # _time_perf_counter() -> xmm0 seconds as float
            if "_time_perf_counter" in self.ffi_externs:
                self.label("_time_perf_counter")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("mov edi, 1", "lea rsi, [rel _time_ts]",
                           "call clock_gettime")
                # result = tv_sec + tv_nsec / 1e9
                self.emitf("cvtsi2sd xmm0, qword [rel _time_ts]")   # tv_sec
                self.emitf("cvtsi2sd xmm1, qword [rel _time_ts+8]")  # tv_nsec
                self.emitf("divsd xmm1, [rel _time_1e9]", "addsd xmm0, xmm1",
                           "leave", "ret")

            # _time_time_ns() -> rax (ns since epoch via clock_gettime REALTIME=0)
            if "_time_time_ns" in self.ffi_externs:
                self.label("_time_time_ns")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("xor edi, edi", "lea rsi, [rel _time_ts]",
                           "call clock_gettime")
                # rax = tv_sec * 1e9 + tv_nsec
                self.emitf("mov rax, [rel _time_ts]")    # tv_sec
                self.emitf("mov rcx, 1000000000", "imul rax, rcx")
                self.emitf("add rax, [rel _time_ts+8]", "leave", "ret")

            # _time_sleep_ms(rdi=ms) — usleep(ms * 1000) on Linux
            if "_time_sleep_ms" in self.ffi_externs:
                self.emit("extern usleep")
                self.label("_time_sleep_ms")
                self.emitf("imul rdi, rdi, 1000", "call usleep", "xor rax, rax", "ret")

        # ---- hardware stubs: ring-0 ops are unavailable in user mode ---------
        if needs_hw:
            # rdtsc/cpuid/rdrand are unprivileged (ring-3) instructions, so
            # unlike the rest of asmlib.hardware (port I/O, MMIO, MSRs, CRn,
            # PIC/PIT/keyboard/VGA, cli/sti/hlt -- all ring-0-only) they have
            # real implementations on hosted targets too, not just
            # freestanding.
            if "_hw_rdtsc" in self.ffi_externs:
                self.label("_hw_rdtsc")
                self.emitf("rdtsc", "shl rdx, 32", "or rax, rdx", "ret")
            if "_hw_cpuid" in self.ffi_externs:
                # arg (leaf) arrives in edi (SysV ABI); returns EAX after CPUID.
                self.label("_hw_cpuid")
                self.emitf("mov eax, edi", "push rbx", "cpuid",
                           "pop rbx", "movsx rax, eax", "ret")
            if "_hw_rdrand" in self.ffi_externs:
                self.label("_hw_rdrand")
                self.label(".retry")
                self.emitf("rdrand rax", "jnc .retry", "ret")

            if any(s in self.ffi_externs for s in self._HW_CONSOLE_SYMS):
                self._emit_console_runtime()

            for sym in self._HW_STUBS:
                if sym in self.ffi_externs and sym not in (
                        ("_hw_rdtsc", "_hw_cpuid", "_hw_rdrand")
                        + self._HW_CONSOLE_SYMS):
                    self.label(sym)
                    self.emitf("xor rax, rax", "ret")

        # ---- network helpers (SysV ABI): build sockaddr_in on stack ----------
        # The raw POSIX symbols (socket, bind, connect, listen, accept, close,
        # send, recv, htons, htonl, ntohs, ntohl, inet_addr) are libc and
        # already externed.  These wrappers accept (fd, addr_cstr, port) instead
        # of a raw sockaddr pointer, which is easier to call from asmpython code.
        if needs_net:
            self.emit("extern socket")
            self.emit("extern bind")
            self.emit("extern connect")
            self.emit("extern listen")
            self.emit("extern accept")
            self.emit("extern close")
            self.emit("extern send")
            self.emit("extern recv")
            self.emit("extern htons")
            self.emit("extern inet_addr")
            self.emit("extern gethostname")
            self.emit("extern errno")

            # _net_bind(rdi=fd, rsi=addr_cstr, rdx=port) -> rax
            if "_net_bind" in self.ffi_externs:
                self.label("_net_bind")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                # Save args
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi", "mov [rbp-24], rdx")
                # Build sockaddr_in on stack: sin_family=2, sin_port=htons(port), sin_addr, zeros
                self.emitf("xor rax, rax", "mov [rbp-40], rax", "mov [rbp-48], rax")
                self.emitf("mov word [rbp-40], 2")        # AF_INET
                self.emitf("mov rdi, rdx", "call htons",
                           "mov word [rbp-40+2], ax")     # sin_port
                self.emitf("mov rdi, [rbp-16]", "call inet_addr",
                           "mov dword [rbp-40+4], eax")   # sin_addr
                # bind(fd, &sockaddr, 16)
                self.emitf("mov edi, [rbp-8]", "lea rsi, [rbp-40]", "mov edx, 16",
                           "call bind", "leave", "ret")

            # _net_connect(rdi=fd, rsi=addr_cstr, rdx=port) -> rax
            if "_net_connect" in self.ffi_externs:
                self.label("_net_connect")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi", "mov [rbp-24], rdx")
                self.emitf("xor rax, rax", "mov [rbp-40], rax", "mov [rbp-48], rax")
                self.emitf("mov word [rbp-40], 2")
                self.emitf("mov rdi, rdx", "call htons", "mov word [rbp-40+2], ax")
                self.emitf("mov rdi, [rbp-16]", "call inet_addr",
                           "mov dword [rbp-40+4], eax")
                self.emitf("mov edi, [rbp-8]", "lea rsi, [rbp-40]", "mov edx, 16",
                           "call connect", "leave", "ret")

            # _net_send(rdi=fd, rsi=msg_cstr, rdx=flags) -> rax (bytes sent)
            if "_net_send" in self.ffi_externs:
                self.emit("extern strlen")
                self.label("_net_send")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi", "mov [rbp-24], rdx")
                # len = strlen(msg)
                self.emitf("mov rdi, rsi", "call strlen", "mov rdx, rax")  # rdx=len
                # send(fd, msg, len, flags)
                self.emitf("mov rdi, [rbp-8]", "mov rsi, [rbp-16]",
                           "mov rcx, [rbp-24]",  # flags
                           "call send", "leave", "ret")

            # _net_recv(rdi=fd, rsi=buf_size) -> rax (new string ptr)
            if "_net_recv" in self.ffi_externs:
                self.label("_net_recv")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi")
                # malloc(buf_size + 1)
                self.emitf("lea rdi, [rsi+1]", "call malloc", "mov [rbp-24], rax")
                # recv(fd, buf, buf_size, 0)
                self.emitf("mov rdi, [rbp-8]", "mov rsi, rax",
                           "mov rdx, [rbp-16]", "xor rcx, rcx",
                           "call recv")
                # nul-terminate (rax = bytes received; -1 on error)
                self.emitf("mov rdx, rax", "mov rax, [rbp-24]",
                           "test rdx, rdx", "jl ._recv_err",
                           "mov byte [rax+rdx], 0", "leave", "ret")
                self.label("._recv_err")
                self.emitf("mov byte [rax], 0", "leave", "ret")

            # _net_send_all(rdi=fd, rsi=msg_cstr) -> rax (total bytes sent)
            if "_net_send_all" in self.ffi_externs:
                self.emit("extern strlen")
                self.label("_net_send_all")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi")
                self.emitf("mov rdi, rsi", "call strlen", "mov [rbp-24], rax")
                self.emitf("xor rax, rax", "mov [rbp-32], rax")  # sent=0
                self.label("._sa_loop")
                self.emitf("mov rax, [rbp-32]", "cmp rax, [rbp-24]", "jge ._sa_done")
                self.emitf("mov rdi, [rbp-8]",
                           "mov rsi, [rbp-16]", "add rsi, [rbp-32]",
                           "mov rdx, [rbp-24]", "sub rdx, [rbp-32]",
                           "xor rcx, rcx",
                           "call send",
                           "test rax, rax", "jle ._sa_done",
                           "add [rbp-32], rax", "jmp ._sa_loop")
                self.label("._sa_done")
                self.emitf("mov rax, [rbp-32]", "leave", "ret")

            # _net_accept(rdi=fd) -> rax (new fd)
            if "_net_accept" in self.ffi_externs:
                self.label("_net_accept")
                self.emitf("xor rsi, rsi", "xor rdx, rdx", "call accept", "ret")

            # _net_close(rdi=fd) -> rax
            if "_net_close" in self.ffi_externs:
                self.label("_net_close")
                self.emitf("call close", "ret")

            # _net_gethostname() -> rax (ptr to static 256-byte buffer)
            if "_net_gethostname" in self.ffi_externs:
                self.emit("section .bss")
                self.emit("_net_hostname_buf: resb 256")
                self.emit("section .text")
                self.label("_net_gethostname")
                self.emitf("lea rdi, [_net_hostname_buf]", "mov esi, 255",
                           "call gethostname",
                           "lea rax, [_net_hostname_buf]", "ret")

            # _net_errno() -> rax
            if "_net_errno" in self.ffi_externs:
                self.label("_net_errno")
                self.emitf("mov rax, [errno]", "ret")

        # ---- SDL2 / GUI helpers ----------------------------------------------
        # SDL_Rect is { int x,y,w,h } = 16 bytes.  We keep a static event
        # buffer (56 bytes, covers SDL_Event union) in .bss.
        if needs_gui:
            self.emit("extern SDL_PollEvent")
            self.emit("extern SDL_WaitEvent")
            self.emit("extern SDL_RenderFillRect")
            self.emit("extern SDL_RenderDrawRect")

            # Static state: last SDL_Event (56 bytes) + SDL_Rect scratch (16 bytes)
            self.emit("section .bss")
            self.emit("_gui_event_buf: resb 56")
            self.emit("section .text")

            # _gui_fill_rect(rdi=renderer, rsi=x, rdx=y, rcx=w, r8=h) -> rax
            if "_gui_fill_rect" in self.ffi_externs:
                self.label("_gui_fill_rect")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                # Build SDL_Rect on stack: x=rsi, y=rdx, w=rcx, h=r8
                self.emitf("push r8",
                           "sub rsp, 16",
                           "mov dword [rsp],    esi",    # x
                           "mov dword [rsp+4],  edx",    # y
                           "mov dword [rsp+8],  ecx",    # w
                           "pop rax", "mov dword [rsp+8], eax",  # h (was r8)
                           "mov rsi, rsp",  # &rect
                           "call SDL_RenderFillRect",
                           "add rsp, 16", "leave", "ret")

            # _gui_draw_rect(rdi=renderer, rsi=x, rdx=y, rcx=w, r8=h) -> rax
            if "_gui_draw_rect" in self.ffi_externs:
                self.label("_gui_draw_rect")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("push r8",
                           "sub rsp, 16",
                           "mov dword [rsp],    esi",
                           "mov dword [rsp+4],  edx",
                           "mov dword [rsp+8],  ecx",
                           "pop rax", "mov dword [rsp+8], eax",
                           "mov rsi, rsp",
                           "call SDL_RenderDrawRect",
                           "add rsp, 16", "leave", "ret")

            # _gui_poll_event() -> rax (event type; 0 = none)
            if "_gui_poll_event" in self.ffi_externs:
                self.label("_gui_poll_event")
                self.emitf("lea rdi, [_gui_event_buf]", "call SDL_PollEvent",
                           "test rax, rax", "jz ._gpe_none",
                           "mov eax, dword [_gui_event_buf]", "ret")
                self.label("._gpe_none")
                self.emitf("xor rax, rax", "ret")

            # _gui_wait_event() -> rax (event type)
            if "_gui_wait_event" in self.ffi_externs:
                self.label("_gui_wait_event")
                self.emitf("lea rdi, [_gui_event_buf]", "call SDL_WaitEvent",
                           "mov eax, dword [_gui_event_buf]", "ret")

            # _gui_key_scancode() -> rax  (SDL_KeyboardEvent.keysym.scancode at +16)
            if "_gui_key_scancode" in self.ffi_externs:
                self.label("_gui_key_scancode")
                self.emitf("movsx rax, dword [_gui_event_buf+16]", "ret")

            # _gui_mouse_x/y/button from SDL_MouseMotionEvent / SDL_MouseButtonEvent
            # SDL_MouseMotionEvent: type(0) windowID(4) which(8) state(12) x(16) y(20)
            if "_gui_mouse_x" in self.ffi_externs:
                self.label("_gui_mouse_x")
                self.emitf("movsx rax, dword [_gui_event_buf+16]", "ret")

            if "_gui_mouse_y" in self.ffi_externs:
                self.label("_gui_mouse_y")
                self.emitf("movsx rax, dword [_gui_event_buf+20]", "ret")

            if "_gui_mouse_button" in self.ffi_externs:
                # SDL_MouseButtonEvent: button at offset 13 (1 byte)
                self.label("_gui_mouse_button")
                self.emitf("movzx rax, byte [_gui_event_buf+13]", "ret")
