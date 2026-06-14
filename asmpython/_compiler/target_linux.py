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

        # ---- hardware stubs: ring-0 ops are unavailable in user mode ---------
        if needs_hw:
            for sym in self._HW_STUBS:
                if sym in self.ffi_externs:
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
