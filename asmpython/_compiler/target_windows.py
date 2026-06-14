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
        self.emitf("mov rcx, rax", "call _atoi64")

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
        # Use asmpython's hand-rolled _runtime_setjmp. It takes the buf in rax.
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

    def emit_asmlib_runtime(self) -> None:
        needs_hw  = any(s in self.ffi_externs for s in self._HW_STUBS)
        needs_net = any(s in self.ffi_externs for s in self._NET_SYMS)
        needs_gui = any(s in self.ffi_externs for s in self._GUI_SYMS)
        if not (needs_hw or needs_net or needs_gui):
            return

        self.emit("")
        self.emit("section .text")

        if needs_hw:
            for sym in self._HW_STUBS:
                if sym in self.ffi_externs:
                    self.label(sym)
                    self.emitf("xor rax, rax", "ret")

        # Windows Winsock2 network helpers.  Args arrive in Windows ABI regs.
        if needs_net:
            for sym in ("socket", "bind", "connect", "listen", "accept",
                        "closesocket", "send", "recv", "htons", "inet_addr",
                        "gethostname", "WSAGetLastError"):
                self.emit(f"extern {sym}")

            # _net_bind(rcx=fd, rdx=addr_cstr, r8=port) -> rax
            if "_net_bind" in self.ffi_externs:
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
            if "_net_connect" in self.ffi_externs:
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
            if "_net_send" in self.ffi_externs:
                self.emit("extern strlen")
                self.label("_net_send")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
                self.emitf("mov [rbp-8], rcx", "mov [rbp-16], rdx", "mov [rbp-24], r8")
                self.emitf("mov rcx, rdx", "call strlen", "mov [rbp-32], rax")
                self.emitf("mov rcx, [rbp-8]", "mov rdx, [rbp-16]",
                           "mov r8, [rbp-32]", "mov r9, [rbp-24]",
                           "call send", "leave", "ret")

            # _net_recv(rcx=fd, rdx=buf_size) -> rax (new string ptr)
            if "_net_recv" in self.ffi_externs:
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
            if "_net_send_all" in self.ffi_externs:
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
            if "_net_accept" in self.ffi_externs:
                self.label("_net_accept")
                self.emitf("xor rdx, rdx", "xor r8, r8", "call accept", "ret")

            # _net_close(rcx=fd) -> rax
            if "_net_close" in self.ffi_externs:
                self.label("_net_close")
                self.emitf("call closesocket", "ret")

            # _net_gethostname() -> rax
            if "_net_gethostname" in self.ffi_externs:
                self.emit("section .bss")
                self.emit("_net_hostname_buf: resb 256")
                self.emit("section .text")
                self.label("_net_gethostname")
                self.emitf("lea rcx, [_net_hostname_buf]", "mov edx, 255",
                           "call gethostname",
                           "lea rax, [_net_hostname_buf]", "ret")

            # _net_errno() -> rax (Winsock2 error code)
            if "_net_errno" in self.ffi_externs:
                self.label("_net_errno")
                self.emitf("call WSAGetLastError", "ret")

        # SDL2 GUI helpers (Windows)
        if needs_gui:
            self.emit("extern SDL_PollEvent")
            self.emit("extern SDL_WaitEvent")
            self.emit("extern SDL_RenderFillRect")
            self.emit("extern SDL_RenderDrawRect")

            self.emit("section .bss")
            self.emit("_gui_event_buf: resb 56")
            self.emit("section .text")

            if "_gui_fill_rect" in self.ffi_externs:
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

            if "_gui_draw_rect" in self.ffi_externs:
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

            if "_gui_poll_event" in self.ffi_externs:
                self.label("_gui_poll_event")
                self.emitf("sub rsp, 32",
                           "lea rcx, [_gui_event_buf]", "call SDL_PollEvent",
                           "add rsp, 32",
                           "test rax, rax", "jz ._gpe_none",
                           "mov eax, dword [_gui_event_buf]", "ret")
                self.label("._gpe_none")
                self.emitf("xor rax, rax", "ret")

            if "_gui_wait_event" in self.ffi_externs:
                self.label("_gui_wait_event")
                self.emitf("sub rsp, 32",
                           "lea rcx, [_gui_event_buf]", "call SDL_WaitEvent",
                           "add rsp, 32",
                           "mov eax, dword [_gui_event_buf]", "ret")

            if "_gui_key_scancode" in self.ffi_externs:
                self.label("_gui_key_scancode")
                self.emitf("movsx rax, dword [_gui_event_buf+16]", "ret")

            if "_gui_mouse_x" in self.ffi_externs:
                self.label("_gui_mouse_x")
                self.emitf("movsx rax, dword [_gui_event_buf+16]", "ret")

            if "_gui_mouse_y" in self.ffi_externs:
                self.label("_gui_mouse_y")
                self.emitf("movsx rax, dword [_gui_event_buf+20]", "ret")

            if "_gui_mouse_button" in self.ffi_externs:
                self.label("_gui_mouse_button")
                self.emitf("movzx rax, byte [_gui_event_buf+13]", "ret")
