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
        self.emitf("call _runtime_str_to_int")

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

    def _emit_strtoll_endptr(self) -> None:
        # In: rax=str, rbx=&endptr_storage, rcx=base. Out: rax=int64, *rbx=endptr.
        # SysV: strtoll(rdi=str, rsi=endptr_addr, rdx=base).
        self.emitf(
            "mov rdx, rcx",  # base
            "mov rsi, rbx",  # endptr address (rbx preserved by callee)
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
        self._emit_float_repr_search()

    def _emit_float_repr_search(self) -> None:
        """In: xmm0 = a double (glibc's sprintf %g already spells NaN/inf
        as "nan"/"inf"/"-inf", matching Python, so unlike the Windows/UCRT
        port there's no special-casing needed before the search below).
        Out: rax = ptr to an OWNED copy of its CPython-repr-style decimal
        string (dup'd via _runtime_str_concat_dup).

        Replaces a plain sprintf(buf, "%g", x): %g's fixed 6 significant
        digits switches to scientific notation far too early compared to
        CPython (starts at 1e6/1e-4; CPython's repr() only switches
        outside [1e-4, 1e16)) -- confirmed via a direct WSL/gcc probe that
        glibc's %g has this identical 1e6 threshold (its exponent padding
        already happens to match Python's minimum-2-digit form, unlike
        MSVC's, so no exponent-compaction pass is needed here). Genuine,
        previously-undiscovered bug affecting every float print above
        ~1e6 or below ~1e-4 -- ported here from the identical fix already
        verified and shipped in the x86-64 backend's _abi_float_to_str
        (asmpython/_runtime/abi_shims.asm) and target_windows.py's own
        port of the same fix; see either for the full derivation.

        CPython's float repr is the SHORTEST decimal string that
        round-trips back to the exact same double -- no fixed sprintf
        precision produces this for every input. Searches precision
        0..17 with %f (fixed notation, when CPython would print fixed:
        abs(x) in [1e-4, 1e16), or x == 0) or %e (scientific, otherwise),
        formatting and parsing back with strtod; the first precision
        whose round-trip reproduces the ORIGINAL BITS exactly (not
        approximately) is used.
        """
        notation_fixed = self.fresh("frs_fixed")
        notation_sci = self.fresh("frs_sci")
        search_loop = self.fresh("frs_loop")
        digits_ready = self.fresh("frs_digits_ready")
        one_digit = self.fresh("frs_one_digit")
        use_fixed_fmt = self.fresh("frs_use_fixed")
        fmt_kind = self.fresh("frs_kind")
        fmt_kind_fixed = self.fresh("frs_kind_fixed")
        fmt_ready = self.fresh("frs_fmt_ready")
        search_done = self.fresh("frs_search_done")
        rfixup_scan = self.fresh("frs_rfixup_scan")
        rfixup_append = self.fresh("frs_rfixup_append")
        rfixup_done = self.fresh("frs_rfixup_done")

        self.emitf(
            "movsd [_float_repr_x], xmm0",
            "xor r12, r12",  # search precision, 0..17
            "movq rax, xmm0",
            "mov r10, 0x7FFFFFFFFFFFFFFF",
            "and rax, r10",
            "movq xmm1, rax",
            "xorpd xmm2, xmm2",
            "ucomisd xmm1, xmm2",
            f"je {notation_fixed}",  # x == 0.0 -> always fixed ("0", not "0e+00")
            "mov r10, 0x3F1A36E2EB1C432D",  # bit pattern of 1e-4
            "movq xmm3, r10",
            "ucomisd xmm1, xmm3",
            f"jb {notation_sci}",  # abs(x) < 1e-4 -> scientific
            "mov r10, 0x4341C37937E08000",  # bit pattern of 1e16
            "movq xmm3, r10",
            "ucomisd xmm1, xmm3",
            f"jae {notation_sci}",  # abs(x) >= 1e16 -> scientific
        )
        self.label(notation_fixed)
        self.emitf("mov qword [_float_repr_notation], 0", f"jmp {search_loop}")
        self.label(notation_sci)
        self.emitf("mov qword [_float_repr_notation], 1")
        self.label(search_loop)
        self.emitf(
            "mov qword [_float_repr_prec], r12",
            # Build "%." + digit(s) + 'f'/'e' + 0 into _float_repr_fmt.
            "cmp qword [_float_repr_notation], 0",
            f"je {use_fixed_fmt}",
            "lea rbx, [_float_repr_fmt]",
            "mov byte [rbx+0], '%'",
            "mov byte [rbx+1], '.'",
            f"jmp {digits_ready}",
        )
        self.label(use_fixed_fmt)
        self.emitf("lea rbx, [_float_repr_fmt]", "mov byte [rbx+0], '%'", "mov byte [rbx+1], '.'")
        self.label(digits_ready)
        self.emitf(
            # r12 is 0..17 -- at most two decimal digits.
            "mov rax, r12",
            "mov r10, 10",
            "xor rdx, rdx",
            "div r10",
            "test rax, rax",
            f"jz {one_digit}",
            "add al, '0'",
            "mov [rbx+2], al",
            "add dl, '0'",
            "mov [rbx+3], dl",
            "lea rcx, [rbx+4]",
            f"jmp {fmt_kind}",
        )
        self.label(one_digit)
        self.emitf("add dl, '0'", "mov [rbx+2], dl", "lea rcx, [rbx+3]")
        self.label(fmt_kind)
        self.emitf("cmp qword [_float_repr_notation], 0", f"je {fmt_kind_fixed}")
        self.emitf("mov byte [rcx], 'e'", "mov byte [rcx+1], 0", f"jmp {fmt_ready}")
        self.label(fmt_kind_fixed)
        self.emitf("mov byte [rcx], 'f'", "mov byte [rcx+1], 0")
        self.label(fmt_ready)
        self.emitf(
            # sprintf(buf, fmt, x): SysV -- rdi=buf, rsi=fmt, xmm0=x (already
            # loaded); al = number of vector regs used for the variadic call.
            "movsd xmm0, [_float_repr_x]",
            "lea rdi, [_float_repr_search_buf]",
            "mov rsi, rbx",
            "mov al, 1",
            "call sprintf",
            # strtod(buf, NULL): rdi=buf, rsi=endptr.
            "lea rdi, [_float_repr_search_buf]",
            "xor esi, esi",
            "call strtod",
            "movq rax, xmm0",
            "movsd xmm1, [_float_repr_x]",
            "movq r10, xmm1",
            "mov r12, [_float_repr_prec]",
            "cmp rax, r10",
            f"je {search_done}",
            "inc r12",
            "cmp r12, 17",
            f"jbe {search_loop}",
        )
        self.label(search_done)
        # glibc's %e already uses a minimum-2-digit, no-leading-zero
        # exponent (confirmed via direct probe), so -- unlike the Windows
        # port -- no exponent-compaction pass is needed here.
        self.emitf("lea rax, [_float_repr_search_buf]", "mov rbx, rax")
        self.label(rfixup_scan)
        self.emitf(
            "mov cl, [rbx]", "test cl, cl", f"jz {rfixup_append}",
            "cmp cl, '.'", f"je {rfixup_done}",
            "cmp cl, 'e'", f"je {rfixup_done}",
            "inc rbx", f"jmp {rfixup_scan}",
        )
        self.label(rfixup_append)
        self.emitf("mov byte [rbx], '.'", "mov byte [rbx+1], '0'", "mov byte [rbx+2], 0")
        self.label(rfixup_done)
        self.emitf("lea rax, [_float_repr_search_buf]", "call _runtime_str_concat_dup")

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

    def _emit_load_library(self) -> None:
        # rax = path (C string) -> rax = handle, or NULL.
        # RTLD_NOW (2): resolve all symbols immediately, surfacing a missing
        # symbol as a load failure rather than deferring it to first use.
        self.emitf("mov rdi, rax", "mov esi, 2", "call dlopen")

    def _emit_get_proc_addr(self) -> None:
        # rax = handle, rbx = name (C string) -> rax = function ptr, or NULL.
        self.emitf("mov rdi, rax", "mov rsi, rbx", "call dlsym")

    def _emit_get_gl_proc_addr(self) -> None:
        # rax = name (C string) -> rax = GL function ptr, or NULL.
        # SDL_GL_GetProcAddress resolves against the current GL context
        # (via glXGetProcAddress under the hood on Linux/GLX), the same
        # reason this is required rather than dlsym against libGL.so.1 --
        # see target_windows.py's _emit_get_gl_proc_addr for the full
        # rationale (identical on both platforms).
        self.emitf("mov rdi, rax", "call SDL_GL_GetProcAddress")

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
            if sym not in self.ffi_called:
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
            # Head of the object registry: a singly-linked list through
            # each tracked object's header. Zero = empty, the right start.
            self.emit("_gc_head:     resq 1")
            # Scratch for _emit_float_repr_search's shortest-round-trip
            # precision search (see that method's docstring).
            self.emit("_float_repr_x:          resq 1")
            self.emit("_float_repr_notation:    resq 1")
            self.emit("_float_repr_prec:        resq 1")
            self.emit("_float_repr_fmt:         resb 8")
            self.emit("_float_repr_search_buf:  resb 40")
        else:
            # Even when linking the runtime library, the helpers use these
            # via `lea rdi, [rel itoa_str_buf]` — they must exist in the
            # library or be externed. The runtime owns them.
            self.emit("extern itoa_str_buf")
            self.emit("extern input_buf")
            self.emit("extern _float_repr_x")
            self.emit("extern _float_repr_notation")
            self.emit("extern _float_repr_prec")
            self.emit("extern _float_repr_fmt")
            self.emit("extern _float_repr_search_buf")

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
        # Raw POSIX socket symbols used directly from socket BINDINGS
        "socket", "bind", "connect", "listen", "accept", "close",
        "send", "recv", "htons", "htonl", "ntohs", "ntohl",
        "inet_addr", "gethostname", "errno", "setsockopt", "getsockopt", "shutdown",
    )
    _GUI_SYMS = (
        "_gui_fill_rect", "_gui_draw_rect", "_gui_poll_event",
        "_gui_wait_event", "_gui_key_scancode",
        "_gui_mouse_x", "_gui_mouse_y", "_gui_mouse_button",
        "_gui_load_bmp",
        "_gui_render_copy", "_gui_query_texture_w", "_gui_query_texture_h",
        "_gui_is_key_down", "_gui_mouse_dx", "_gui_mouse_dy",
        "_gui_render_copy_ex", "_gui_render_copy_region",
        "_gui_joystick_axis", "_gui_joystick_button",
        "_gui_list_buf_addr", "_gui_update_texture", "_gui_create_texture_argb",
        "_gl_shader_source_1",
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
        return (set(self._NET_SYMS) | set(self._GUI_SYMS)
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
        needs_net    = any(s in self.ffi_called for s in self._NET_SYMS)
        needs_gui    = any(s in self.ffi_called for s in self._GUI_SYMS)
        needs_audio  = any(s in self.ffi_called for s in self._AUDIO_SYMS)
        needs_ttf    = any(s in self.ffi_called for s in self._TTF_SYMS)
        needs_math   = any(s in self.ffi_called for s in self._MATH_SYMS)
        needs_random = any(s in self.ffi_called for s in self._RANDOM_SYMS)
        needs_time   = any(s in self.ffi_called for s in self._TIME_SYMS)
        needs_thread = any(s in self.ffi_called for s in self._THREAD_SYMS)
        if not (needs_net or needs_gui or needs_audio or needs_ttf
                or needs_math or needs_random or needs_time or needs_thread):
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
            needs_inf_consts = any(s in self.ffi_called for s in (
                "_math_isinf", "_math_isfinite"))
            if needs_inf_consts:
                self.emit("section .rodata")
                self.emit("_math_inf_bits:  dq 0x7FF0000000000000")
                self.emit("_math_abs_mask:  dq 0x7FFFFFFFFFFFFFFF")
            self.emit("section .text")

            # _math_isnan(xmm0=x) -> rax: 1 if NaN (NaN is the only float where x!=x)
            if "_math_isnan" in self.ffi_called:
                self.label("_math_isnan")
                self.emitf("ucomisd xmm0, xmm0", "setp al", "movzx rax, al", "ret")

            # _math_isinf(xmm0=x) -> rax: 1 if +inf or -inf
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

            # _math_isfinite(xmm0=x) -> rax: 1 if finite
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

            # _math_degrees(xmm0=radians) -> xmm0=degrees
            if "_math_degrees" in self.ffi_called:
                self.label("_math_degrees")
                self.emitf("mulsd xmm0, [rel _math_deg_factor]", "ret")

            # _math_radians(xmm0=degrees) -> xmm0=radians
            if "_math_radians" in self.ffi_called:
                self.label("_math_radians")
                self.emitf("mulsd xmm0, [rel _math_rad_factor]", "ret")

            # _math_gcd(rdi=a, rsi=b) -> rax=gcd  (Euclidean, always positive)
            if "_math_gcd" in self.ffi_called:
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
            if "_math_lcm" in self.ffi_called:
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
            if "_math_factorial" in self.ffi_called:
                self.label("_math_factorial")
                self.emitf("mov rax, 1", "cmp rdi, 1", "jle ._mf_done")
                self.label("._mf_loop")
                self.emitf("imul rax, rdi", "dec rdi", "cmp rdi, 1", "jg ._mf_loop")
                self.label("._mf_done")
                self.emitf("ret")

            # _math_comb(rdi=n, rsi=k) -> rax=C(n,k)
            # C(n,k) = n! / (k! * (n-k)!) computed iteratively to avoid overflow
            if "_math_comb" in self.ffi_called:
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
            if "_math_perm" in self.ffi_called:
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
            if "_math_log_base" in self.ffi_called:
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
            if "_math_modf_frac" in self.ffi_called:
                self.label("_math_modf_frac")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("lea rdi, [rbp-8]", "mov al, 1", "call modf")
                # xmm0 = fractional part (modf returns frac in xmm0, int via ptr)
                self.emitf("leave", "ret")

            # _math_modf_int(xmm0=x) -> xmm0=integer part  (via modf)
            if "_math_modf_int" in self.ffi_called:
                self.label("_math_modf_int")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("lea rdi, [rbp-8]", "mov al, 1", "call modf")
                self.emitf("movsd xmm0, [rbp-8]", "leave", "ret")

            # _math_frexp_m(xmm0=x) -> xmm0=mantissa  (frexp; [0.5,1))
            #
            # Real, pre-existing bug found and fixed while investigating an
            # unrelated float-formatting change: frexp's real signature is
            # `double frexp(double x, int *e)` -- x in xmm0 (first FLOAT
            # arg), e in rdi (first INTEGER arg; SysV tracks the two
            # register sequences independently, so "e is frexp's 2nd
            # parameter" does NOT mean "e goes in the 2nd integer
            # register"). This shim put the pointer in rsi instead --
            # frexp's own write to `*e` then landed on whatever rsi
            # happened to hold, silently corrupting arbitrary caller state
            # that had nothing to do with frexp at all. This went
            # undetected because a single, isolated call to frexp_mantissa
            # often has nothing live in rsi to corrupt; confirmed via a
            # real repro that surfaced it -- adding frexp_mantissa as a
            # 4th float-formatting call in a row (unrelated to this bug in
            # itself) put a live, needed value in rsi at exactly the wrong
            # moment, and frexp's stray write there silently zeroed a
            # saved return address, causing the whole program to exit
            # cleanly without ever reaching its later print() calls.
            if "_math_frexp_m" in self.ffi_called:
                self.label("_math_frexp_m")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("lea rdi, [rbp-8]", "mov al, 1", "call frexp")
                self.emitf("leave", "ret")

            # _math_frexp_e(xmm0=x) -> rax=exponent  (int)
            if "_math_frexp_e" in self.ffi_called:
                self.label("_math_frexp_e")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("lea rdi, [rbp-8]", "mov al, 1", "call frexp")
                self.emitf("movsxd rax, dword [rbp-8]", "leave", "ret")

            # _math_ldexp(xmm0=x, rdi=n) -> xmm0  (just forwards to libc ldexp)
            if "_math_ldexp" in self.ffi_called:
                self.emit("extern ldexp")
                self.label("_math_ldexp")
                # SysV: xmm0=x already, rdi=n already — ldexp(double,int) is exact match
                self.emitf("mov al, 1", "call ldexp", "ret")

            # _math_isqrt(rdi=n) -> rax = floor(sqrt(n))
            if "_math_isqrt" in self.ffi_called:
                self.emit("extern sqrt")
                self.label("_math_isqrt")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("cvtsi2sd xmm0, rdi", "mov al, 1", "call sqrt")
                self.emitf("cvttsd2si rax, xmm0", "leave", "ret")

            # _math_isclose(xmm0=a, xmm1=b, xmm2=rel_tol, xmm3=abs_tol) -> rax: 1 if close
            if "_math_isclose" in self.ffi_called:
                lbl_ic_yes = self.fresh("isclose_yes")
                lbl_ic_no  = self.fresh("isclose_no")
                self.emit("section .rodata")
                self.emit("_ic_abs_mask_l:  dq 0x7FFFFFFFFFFFFFFF")
                self.emit("section .text")
                self.label("_math_isclose")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
                self.emitf("movsd [rbp-8],  xmm0",   # a
                           "movsd [rbp-16], xmm1",   # b
                           "movsd [rbp-24], xmm2",   # rel_tol
                           "movsd [rbp-32], xmm3")   # abs_tol
                # diff = |a - b|
                self.emitf("movsd xmm0, [rbp-8]", "subsd xmm0, [rbp-16]")
                self.emitf("movsd xmm1, [rel _ic_abs_mask_l]", "andpd xmm0, xmm1",
                           "movsd [rbp-40], xmm0")   # diff
                # max_ab = max(|a|, |b|)
                self.emitf("movsd xmm0, [rbp-8]",  "andpd xmm0, xmm1",  # |a|
                           "movsd xmm2, [rbp-16]", "andpd xmm2, xmm1")  # |b|
                self.emitf("maxsd xmm0, xmm2", "movsd [rbp-48], xmm0")  # max_ab
                # tol = max(rel_tol * max_ab, abs_tol)
                self.emitf("movsd xmm1, [rbp-24]", "mulsd xmm1, [rbp-48]",
                           "movsd xmm2, [rbp-32]", "maxsd xmm1, xmm2",
                           "movsd [rbp-56], xmm1")   # tol
                # result = diff <= tol
                self.emitf("movsd xmm0, [rbp-40]", "ucomisd xmm0, [rbp-56]")
                self.emitf(f"ja {lbl_ic_no}")
                self.label(lbl_ic_yes)
                self.emitf("mov rax, 1", "leave", "ret")
                self.label(lbl_ic_no)
                self.emitf("xor rax, rax", "leave", "ret")

        # ---- random helpers (SysV ABI) ----------------------------------------
        if needs_random:
            self.emit("extern rand")
            self.emit("section .rodata")
            self.emit("_rand_inv:  dq 3.0517578125e-05")   # 1.0 / (RAND_MAX+1) = 1/32768
            self.emit("section .text")

            # _random_random() -> xmm0 in [0.0, 1.0)
            if "_random_random" in self.ffi_called:
                self.label("_random_random")
                # `push rbp` is pure 16-byte alignment padding for the `call`
                # below (entry RSP is 8 mod 16 -- see _gui_poll_event's
                # comment for the full explanation); it's never read back as
                # a frame pointer. `pop rbp` doesn't touch rax/xmm0.
                self.emitf("push rbp", "xor eax, eax", "call rand", "pop rbp")
                self.emitf("cvtsi2sd xmm0, rax", "mulsd xmm0, [rel _rand_inv]", "ret")

            # _random_randint(rdi=a, rsi=b) -> rax  in [a, b] inclusive
            if "_random_randint" in self.ffi_called:
                self.label("_random_randint")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi")
                self.emitf("call rand")
                # range = b - a + 1
                self.emitf("mov rcx, [rbp-16]", "sub rcx, [rbp-8]", "inc rcx")
                self.emitf("xor rdx, rdx", "div rcx")       # rdx = rand % range
                self.emitf("add rdx, [rbp-8]", "mov rax, rdx", "leave", "ret")

            # _random_uniform(xmm0=a, xmm1=b) -> xmm0 in [a, b]
            if "_random_uniform" in self.ffi_called:
                self.label("_random_uniform")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("movsd [rbp-8], xmm0", "movsd [rbp-16], xmm1")
                self.emitf("call rand")
                self.emitf("cvtsi2sd xmm0, rax", "mulsd xmm0, [rel _rand_inv]")
                # result = a + r * (b - a)
                self.emitf("movsd xmm1, [rbp-16]", "subsd xmm1, [rbp-8]",
                           "mulsd xmm0, xmm1", "addsd xmm0, [rbp-8]", "leave", "ret")

            # _random_randrange(rdi=stop) -> rax in [0, stop)
            if "_random_randrange" in self.ffi_called:
                self.label("_random_randrange")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("mov [rbp-8], rdi")
                self.emitf("call rand")
                self.emitf("xor rdx, rdx", "div qword [rbp-8]", "mov rax, rdx",
                           "leave", "ret")

            # _random_choice(rdi=list_hdr) -> rax = element at random index
            # List layout: [hdr+0]=cap, [hdr+8]=len, [hdr+16]=buf_ptr
            # Frame: push+40=48 bytes total → 16-byte aligned for rand() call.
            if "_random_choice" in self.ffi_called:
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
            if "_random_shuffle" in self.ffi_called:
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

            # _random_sample(rdi=list_hdr, rsi=k) -> rax = new list of k unique elements
            # SysV: rdi=src_hdr, rsi=k. Frame offsets from rbp:
            # -8=src_hdr, -16=n, -24=k, -32=copy_buf, -40=i, -48=result_hdr, -56=result_buf
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
                self.emitf("mov [rbp-8], rdi")   # src_hdr
                self.emitf("mov [rbp-24], rsi")  # k
                self.emitf("mov rax, [rdi+8]", "mov [rbp-16], rax")  # n = src.len
                # allocate copy buf (n * 8 bytes)
                self.emitf("mov rdi, rax", "shl rdi, 3", "call malloc",
                           "mov [rbp-32], rax")  # copy_buf
                # memcpy source buf into copy_buf
                self.emitf("mov rsi, [rbp-8]", "mov rsi, [rsi+16]")  # src buf
                self.emitf("mov rdi, [rbp-32]")
                self.emitf("mov rcx, [rbp-16]", "xor rax, rax")
                self.label(lbl_s_cp)
                self.emitf(f"cmp rax, rcx", f"jge {lbl_s_cp_end}",
                           "mov rbx, [rsi+rax*8]", "mov [rdi+rax*8], rbx",
                           "inc rax", f"jmp {lbl_s_cp}")
                self.label(lbl_s_cp_end)
                # partial Fisher-Yates: i from n down to n-k+1
                self.emitf("mov rax, [rbp-16]", "mov [rbp-40], rax")  # i = n
                self.label(lbl_s_fy)
                self.emitf("mov rax, [rbp-40]", "mov rbx, [rbp-16]",
                           "sub rbx, [rbp-24]",
                           f"cmp rax, rbx", f"jle {lbl_s_fy_end}")
                self.emitf("call rand")
                self.emitf("xor rdx, rdx", "div qword [rbp-40]")  # rdx = j
                # swap copy_buf[i-1] and copy_buf[j]
                self.emitf("mov rdi, [rbp-32]")
                self.emitf("mov r9, [rbp-40]", "dec r9")   # i-1
                self.emitf("mov rcx, rdx")                  # j
                self.emitf("mov rax, [rdi+r9*8]", "mov rbx, [rdi+rcx*8]",
                           "mov [rdi+r9*8], rbx", "mov [rdi+rcx*8], rax")
                self.emitf("dec qword [rbp-40]", f"jmp {lbl_s_fy}")
                self.label(lbl_s_fy_end)
                # allocate result list header (24 bytes)
                self.emitf("mov rdi, 24", "call malloc", "mov [rbp-48], rax")
                self.emitf("mov rbx, [rbp-24]",
                           "mov [rax+0], rbx", "mov [rax+8], rbx")  # cap=len=k
                # allocate result buf (k * 8)
                self.emitf("mov rdi, [rbp-24]", "shl rdi, 3", "call malloc",
                           "mov [rbp-56], rax")
                self.emitf("mov rcx, [rbp-48]", "mov [rcx+16], rax")  # hdr.buf = result_buf
                # copy selected elements (tail of copy_buf, indices n-k .. n-1)
                self.emitf("mov rax, [rbp-16]", "sub rax, [rbp-24]",
                           "mov [rbp-40], rax")  # start = n-k
                self.emitf("xor rbx, rbx")       # result index
                self.label(lbl_s_fill)
                self.emitf(f"cmp rbx, [rbp-24]", f"jge {lbl_s_fill_end}")
                self.emitf("mov rdi, [rbp-32]")
                self.emitf("mov rax, [rbp-40]", "add rax, rbx",
                           "mov r8, [rdi+rax*8]")
                self.emitf("mov rdi, [rbp-56]", "mov [rdi+rbx*8], r8")
                self.emitf("inc rbx", f"jmp {lbl_s_fill}")
                self.label(lbl_s_fill_end)
                self.emitf("mov rax, [rbp-48]", "leave", "ret")

            # _random_getrandbits(rdi=k) -> rax: k random bits (1-64)
            if "_random_getrandbits" in self.ffi_called:
                lbl_gb_loop = self.fresh("grb_loop")
                lbl_gb_done = self.fresh("grb_done")
                self.label("_random_getrandbits")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rdi")   # k
                self.emitf("xor rbx, rbx")        # result = 0
                self.emitf("xor r12, r12")        # bits_filled = 0
                self.label(lbl_gb_loop)
                self.emitf(f"cmp r12, [rbp-8]", f"jge {lbl_gb_done}")
                self.emitf("call rand")
                self.emitf("shl rbx, 15", "or rbx, rax", "add r12, 15",
                           f"jmp {lbl_gb_loop}")
                self.label(lbl_gb_done)
                # mask to k bits: mask = (1 << k) - 1
                self.emitf("mov rcx, [rbp-8]")
                self.emitf("mov rax, 1", "shl rax, cl", "dec rax")
                self.emitf("and rax, rbx", "leave", "ret")

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
            if "_time_perf_counter" in self.ffi_called:
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
            if "_time_time_ns" in self.ffi_called:
                self.label("_time_time_ns")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("xor edi, edi", "lea rsi, [rel _time_ts]",
                           "call clock_gettime")
                # rax = tv_sec * 1e9 + tv_nsec
                self.emitf("mov rax, [rel _time_ts]")    # tv_sec
                self.emitf("mov rcx, 1000000000", "imul rax, rcx")
                self.emitf("add rax, [rel _time_ts+8]", "leave", "ret")

            # _time_sleep_ms(rdi=ms) — usleep(ms * 1000) on Linux
            if "_time_sleep_ms" in self.ffi_called:
                self.emit("extern usleep")
                self.label("_time_sleep_ms")
                # push rbp: alignment padding, see _gui_poll_event's comment.
                self.emitf("push rbp", "imul rdi, rdi, 1000", "call usleep",
                           "pop rbp", "xor rax, rax", "ret")

        # ---- hardware stubs: ring-0 ops are unavailable in user mode ---------
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
            if "_net_bind" in self.ffi_called:
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
            if "_net_connect" in self.ffi_called:
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
            if "_net_send" in self.ffi_called:
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
            if "_net_recv" in self.ffi_called:
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
            if "_net_send_all" in self.ffi_called:
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
            # push rbp below is pure 16-byte alignment padding for the `call`
            # (entry RSP is 8 mod 16 -- see _gui_poll_event's comment), not a
            # real frame pointer use.
            if "_net_accept" in self.ffi_called:
                self.label("_net_accept")
                self.emitf("push rbp", "xor rsi, rsi", "xor rdx, rdx",
                           "call accept", "pop rbp", "ret")

            # _net_close(rdi=fd) -> rax
            if "_net_close" in self.ffi_called:
                self.label("_net_close")
                self.emitf("push rbp", "call close", "pop rbp", "ret")

            # _net_gethostname() -> rax (ptr to static 256-byte buffer)
            if "_net_gethostname" in self.ffi_called:
                self.emit("section .bss")
                self.emit("_net_hostname_buf: resb 256")
                self.emit("section .text")
                self.label("_net_gethostname")
                self.emitf("push rbp", "lea rdi, [_net_hostname_buf]", "mov esi, 255",
                           "call gethostname", "pop rbp",
                           "lea rax, [_net_hostname_buf]", "ret")

            # _net_errno() -> rax
            if "_net_errno" in self.ffi_called:
                self.label("_net_errno")
                self.emitf("mov rax, [errno]", "ret")

            # _net_setsockopt(rdi=fd, rsi=level, rdx=optname, rcx=value) -> rax
            if "_net_setsockopt" in self.ffi_called:
                self.emit("extern setsockopt")
                self.label("_net_setsockopt")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi",
                           "mov [rbp-24], rdx", "mov [rbp-32], rcx")
                self.emitf("mov dword [rbp-36], ecx")  # int opt_val
                self.emitf("mov rdi, [rbp-8]", "mov rsi, [rbp-16]",
                           "mov rdx, [rbp-24]", "lea rcx, [rbp-36]",
                           "mov r8d, 4",               # optlen=4
                           "call setsockopt", "leave", "ret")

            # _net_getsockopt_int(rdi=fd, rsi=level, rdx=optname) -> rax (int value)
            if "_net_getsockopt_int" in self.ffi_called:
                self.emit("extern getsockopt")
                self.label("_net_getsockopt_int")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 48")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi", "mov [rbp-24], rdx")
                self.emitf("xor rax, rax", "mov [rbp-32], rax")   # opt_val=0
                self.emitf("mov dword [rbp-40], 4")               # optlen=4
                self.emitf("mov rdi, [rbp-8]", "mov rsi, [rbp-16]",
                           "mov rdx, [rbp-24]", "lea rcx, [rbp-32]",
                           "lea r8, [rbp-40]",                    # &optlen
                           "call getsockopt",
                           "mov eax, dword [rbp-32]", "leave", "ret")

        # ---- SDL2 / GUI helpers ----------------------------------------------
        # SDL_Rect is { int x,y,w,h } = 16 bytes.  We keep a static event
        # buffer (56 bytes, covers SDL_Event union) in .bss.
        if needs_gui:
            self.emit("extern SDL_PollEvent")
            self.emit("extern SDL_WaitEvent")
            self.emit("extern SDL_RenderFillRect")
            self.emit("extern SDL_RenderDrawRect")
            self.emit("extern SDL_RenderCopy")
            self.emit("extern SDL_QueryTexture")
            self.emit("extern SDL_UpdateTexture")

            self.emit("section .bss")
            self.emit("_gui_event_buf: resb 56")
            self.emit("_gui_tex_dim: resd 2")
            self.emit("section .text")

            # _gui_load_bmp(rdi=path) -> rax (SDL_Surface* handle, or 0 on failure)
            # SDL2 has no format-agnostic image loader without SDL_image, but
            # BMP loading (SDL_LoadBMP) is always available. SDL_LoadBMP is a
            # macro for SDL_LoadBMP_RW(SDL_RWFromFile(path, "rb"), 1).
            if "_gui_load_bmp" in self.ffi_called:
                self.emit("extern SDL_RWFromFile")
                self.emit("extern SDL_LoadBMP_RW")
                self.emit("section .rodata")
                self.emit('_gui_bmp_mode_rb: db "rb",0')
                self.emit("section .text")
                self.label("_gui_load_bmp")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("mov [rbp-8], rdi",
                           "lea rsi, [rel _gui_bmp_mode_rb]",
                           "mov rdi, [rbp-8]",
                           "call SDL_RWFromFile",
                           "test rax, rax", "jz ._glb_fail",
                           "mov rdi, rax", "mov rsi, 1",
                           "call SDL_LoadBMP_RW",
                           "leave", "ret")
                self.label("._glb_fail")
                self.emitf("xor rax, rax", "leave", "ret")

            # _gui_fill_rect(rdi=renderer, rsi=x, rdx=y, rcx=w, r8=h) -> rax
            if "_gui_fill_rect" in self.ffi_called:
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
            if "_gui_draw_rect" in self.ffi_called:
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
            if "_gui_poll_event" in self.ffi_called:
                self.label("_gui_poll_event")
                # Entry RSP is 8 mod 16 (the `call` reaching this label pushed
                # one return address onto a 16-aligned caller RSP) -- every
                # sibling FFI helper in this file fixes that with `push rbp`
                # before its own stack use so its own `call`s land 16-aligned
                # (see _gui_fill_rect above). This helper called straight into
                # SDL_PollEvent with no adjustment at all, so the call was
                # always made 8 mod 16 instead of 16-aligned. `push rbp` here
                # is pure alignment padding (never read back as a frame
                # pointer), matching the established pattern.
                self.emitf("push rbp",
                           "lea rdi, [_gui_event_buf]", "call SDL_PollEvent",
                           "test rax, rax", "jz ._gpe_none",
                           "mov eax, dword [_gui_event_buf]", "pop rbp", "ret")
                self.label("._gpe_none")
                self.emitf("xor rax, rax", "pop rbp", "ret")

            # _gui_wait_event() -> rax (event type)
            if "_gui_wait_event" in self.ffi_called:
                self.label("_gui_wait_event")
                # Same misalignment fix as _gui_poll_event above.
                self.emitf("push rbp",
                           "lea rdi, [_gui_event_buf]", "call SDL_WaitEvent",
                           "mov eax, dword [_gui_event_buf]", "pop rbp", "ret")

            # _gui_key_scancode() -> rax  (SDL_KeyboardEvent.keysym.scancode at +16)
            if "_gui_key_scancode" in self.ffi_called:
                self.label("_gui_key_scancode")
                self.emitf("movsx rax, dword [_gui_event_buf+16]", "ret")

            # _gui_mouse_x/y/button from SDL_MouseMotionEvent / SDL_MouseButtonEvent
            # SDL_MouseMotionEvent: type(0) windowID(4) which(8) state(12) x(16) y(20)
            if "_gui_mouse_x" in self.ffi_called:
                self.label("_gui_mouse_x")
                self.emitf("movsx rax, dword [_gui_event_buf+16]", "ret")

            if "_gui_mouse_y" in self.ffi_called:
                self.label("_gui_mouse_y")
                self.emitf("movsx rax, dword [_gui_event_buf+20]", "ret")

            if "_gui_mouse_button" in self.ffi_called:
                # SDL_MouseButtonEvent: button at offset 13 (1 byte)
                self.label("_gui_mouse_button")
                self.emitf("movzx rax, byte [_gui_event_buf+13]", "ret")

            # _gui_render_copy(rdi=renderer, rsi=texture, rdx=x, rcx=y, r8=w, r9=h)
            # Builds SDL_Rect{x,y,w,h} on stack, calls SDL_RenderCopy(ren,tex,NULL,&rect).
            if "_gui_render_copy" in self.ffi_called:
                self.label("_gui_render_copy")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi")
                # Build SDL_Rect at [rbp-32]; args: rdx=x, rcx=y, r8=w, r9=h
                self.emitf("mov dword [rbp-32], edx",   # rect.x
                           "mov dword [rbp-28], ecx",   # rect.y
                           "mov dword [rbp-24], r8d",   # rect.w
                           "mov dword [rbp-20], r9d")   # rect.h
                self.emitf("mov rdi, [rbp-8]", "mov rsi, [rbp-16]",
                           "xor rdx, rdx",
                           "lea rcx, [rbp-32]",
                           "call SDL_RenderCopy",
                           "leave", "ret")

            # _gui_update_texture(rdi=texture, rsi=pixels_addr, rdx=pitch) -> rax
            # Calls SDL_UpdateTexture(texture, NULL, pixels_addr, pitch),
            # updating the whole texture in one call from a raw pixel buffer
            # (see PixelBuffer.raw_addr() / Canvas.blit_pixels()).
            if "_gui_update_texture" in self.ffi_called:
                self.label("_gui_update_texture")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("mov rcx, rdx",   # pitch -> 4th arg
                           "mov rdx, rsi",   # pixels_addr -> 3rd arg
                           "xor rsi, rsi",   # rect = NULL
                           "call SDL_UpdateTexture",
                           "leave", "ret")

            # _gui_create_texture_argb(rdi=renderer, rsi=access, rdx=w, rcx=h) -> rax
            # Calls SDL_CreateTexture(renderer, ARGB8888, access, w, h);
            # always uses the ARGB8888 pixel format PixelBuffer's pixel
            # ints are packed as.
            if "_gui_create_texture_argb" in self.ffi_called:
                self.emit("extern SDL_CreateTexture")
                self.label("_gui_create_texture_argb")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("mov r8, rcx",            # h -> 5th arg
                           "mov rcx, rdx",            # w -> 4th arg
                           "mov rdx, rsi",             # access -> 3rd arg
                           "mov esi, 0x16362004",      # SDL_PIXELFORMAT_ARGB8888
                           "call SDL_CreateTexture",
                           "leave", "ret")

            # _gl_shader_source_1(rdi=glShaderSource_fn_ptr, rsi=shader_id,
            #                     rdx=source_str) -> rax
            # Calls glShaderSource(shader, 1, &source_str, NULL) through the
            # dynamically-resolved function pointer in rdi (see gl_import()
            # / _gen_dynamic_call) -- see target_windows.py's identical stub
            # for the full rationale (one-element char*[1] array built on
            # this stub's own stack, since count=1/length=NULL is valid GL
            # usage for a single NUL-terminated source string).
            if "_gl_shader_source_1" in self.ffi_called:
                self.label("_gl_shader_source_1")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("mov [rbp-8], rdi",    # fn ptr
                           "mov [rbp-16], rsi",   # shader id
                           "mov [rbp-24], rdx")   # source str ptr
                self.emitf("mov rax, [rbp-24]", "mov [rbp-32], rax")  # string[0]
                self.emitf("mov rdi, [rbp-16]",   # shader
                           "mov esi, 1",            # count = 1
                           "lea rdx, [rbp-32]",      # &string[0]
                           "xor rcx, rcx",            # length = NULL
                           "call [rbp-8]",
                           "leave", "ret")

            # _gui_query_texture_w/h(rdi=texture) -> rax (width or height)
            # Calls SDL_QueryTexture(tex, NULL, NULL, &w, &h) and returns one dim.
            if "_gui_query_texture_w" in self.ffi_called:
                self.label("_gui_query_texture_w")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("xor rsi, rsi", "xor rdx, rdx",
                           "lea rcx, [_gui_tex_dim]",
                           "lea r8, [_gui_tex_dim+4]",
                           "call SDL_QueryTexture",
                           "movsx rax, dword [_gui_tex_dim]",
                           "leave", "ret")
            if "_gui_query_texture_h" in self.ffi_called:
                self.label("_gui_query_texture_h")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("xor rsi, rsi", "xor rdx, rdx",
                           "lea rcx, [_gui_tex_dim]",
                           "lea r8, [_gui_tex_dim+4]",
                           "call SDL_QueryTexture",
                           "movsx rax, dword [_gui_tex_dim+4]",
                           "leave", "ret")

            # _gui_list_buf_addr(rdi=list_header_ptr) -> rax (buffer address)
            # Every asmpython list is a [cap, len, buf_ptr] header; buf_ptr
            # lives at offset 16. Lets PixelBuffer.raw_addr() hand back a
            # real, stable, externally-writable pointer to its pixel data
            # (e.g. for hardware.mmio_write32-style direct pokes), without
            # exposing the list header layout to .py source.
            if "_gui_list_buf_addr" in self.ffi_called:
                self.label("_gui_list_buf_addr")
                self.emitf("mov rax, [rdi+16]", "ret")

            # _gui_is_key_down(rdi=scancode) -> rax (0 or 1)
            # SDL_GetKeyboardState(NULL) returns Uint8* indexed by scancode.
            if "_gui_is_key_down" in self.ffi_called:
                self.emit("extern SDL_GetKeyboardState")
                self.label("_gui_is_key_down")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("mov [rbp-8], rdi",
                           "xor rdi, rdi",
                           "call SDL_GetKeyboardState",
                           "mov rcx, [rbp-8]",
                           "movzx rax, byte [rax + rcx]",
                           "leave", "ret")

            # _gui_mouse_dx/dy() -> rax (relative motion since last call)
            # SDL_GetRelativeMouseState(&dx, &dy); we cache both ints and
            # return whichever component was asked for.
            if "_gui_mouse_dx" in self.ffi_called or "_gui_mouse_dy" in self.ffi_called:
                self.emit("extern SDL_GetRelativeMouseState")
                self.emit("section .bss")
                self.emit("_gui_rel_dim: resd 2")
                self.emit("section .text")
            if "_gui_mouse_dx" in self.ffi_called:
                self.label("_gui_mouse_dx")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("lea rdi, [_gui_rel_dim]",
                           "lea rsi, [_gui_rel_dim+4]",
                           "call SDL_GetRelativeMouseState",
                           "movsx rax, dword [_gui_rel_dim]",
                           "leave", "ret")
            if "_gui_mouse_dy" in self.ffi_called:
                self.label("_gui_mouse_dy")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("lea rdi, [_gui_rel_dim]",
                           "lea rsi, [_gui_rel_dim+4]",
                           "call SDL_GetRelativeMouseState",
                           "movsx rax, dword [_gui_rel_dim+4]",
                           "leave", "ret")

            # _gui_render_copy_ex(rdi=renderer, rsi=texture, rdx=x, rcx=y,
            #   r8=w, r9=h, [rbp+16]=angle_deg, [rbp+24]=flip) -> rax
            # Incoming: 8 plain ints, first 6 in SysV int regs, last 2 on the
            # caller's stack (no shadow space under SysV). Builds SDL_Rect
            # dstrect on our stack, converts angle to double for xmm0 (SysV
            # counts int/float regs independently, so angle -- the 5th real
            # arg, all-int args before it -- still fits in xmm0; center/flip
            # land in r8/r9, the two int regs left after renderer..dstrect).
            if "_gui_render_copy_ex" in self.ffi_called:
                self.emit("extern SDL_RenderCopyEx")
                self.label("_gui_render_copy_ex")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi")
                # Build SDL_Rect (dstrect) at [rbp-48]
                self.emitf("mov dword [rbp-48], edx",   # rect.x
                           "mov dword [rbp-44], ecx",   # rect.y
                           "mov dword [rbp-40], r8d",   # rect.w
                           "mov dword [rbp-36], r9d")   # rect.h
                self.emitf("mov eax, dword [rbp+16]",   # angle_deg (7th arg, on stack)
                           "cvtsi2sd xmm0, eax")          # angle as double -> xmm0
                self.emitf("mov eax, dword [rbp+24]",   # flip (8th arg, on stack)
                           "mov [rbp-56], rax")
                self.emitf("mov rdi, [rbp-8]", "mov rsi, [rbp-16]",
                           "xor rdx, rdx",                # srcrect = NULL
                           "lea rcx, [rbp-48]",            # dstrect
                           "xor r8, r8",                   # center = NULL
                           "mov r9, [rbp-56]",             # flip
                           "call SDL_RenderCopyEx",
                           "leave", "ret")

            # _gui_render_copy_region(rdi=renderer, rsi=texture, rdx=sx, rcx=sy,
            #   r8=sw, r9=sh, [rbp+16]=dx, [rbp+24]=dy) -> rax
            # Builds SDL_Rect src{sx,sy,sw,sh} and dst{dx,dy,sw,sh}, calls
            # SDL_RenderCopy(ren, tex, &src, &dst) (dest uses src's w/h, no scaling).
            if "_gui_render_copy_region" in self.ffi_called:
                self.label("_gui_render_copy_region")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 64")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi")
                # src rect at [rbp-48]
                self.emitf("mov dword [rbp-48], edx",   # src.x = sx
                           "mov dword [rbp-44], ecx",   # src.y = sy
                           "mov dword [rbp-40], r8d",   # src.w
                           "mov dword [rbp-36], r9d")   # src.h
                # dst rect at [rbp-32]: dx, dy, sw, sh (same w/h as src)
                self.emitf("mov eax, dword [rbp+16]",   # dx (7th arg, on stack)
                           "mov dword [rbp-32], eax",
                           "mov eax, dword [rbp+24]",   # dy (8th arg, on stack)
                           "mov dword [rbp-28], eax",
                           "mov eax, dword [rbp-40]", "mov dword [rbp-24], eax",  # dst.w = src.w
                           "mov eax, dword [rbp-36]", "mov dword [rbp-20], eax")  # dst.h = src.h
                self.emitf("mov rdi, [rbp-8]", "mov rsi, [rbp-16]",
                           "lea rdx, [rbp-48]", "lea rcx, [rbp-32]",
                           "call SDL_RenderCopy",
                           "leave", "ret")

            # _gui_joystick_axis(rdi=joystick, rsi=axis) -> rax (sign-extended Sint16)
            if "_gui_joystick_axis" in self.ffi_called:
                self.emit("extern SDL_JoystickGetAxis")
                self.label("_gui_joystick_axis")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("call SDL_JoystickGetAxis",
                           "movsx rax, ax",
                           "leave", "ret")

            # _gui_joystick_button(rdi=joystick, rsi=button) -> rax (0 or 1)
            if "_gui_joystick_button" in self.ffi_called:
                self.emit("extern SDL_JoystickGetButton")
                self.label("_gui_joystick_button")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("call SDL_JoystickGetButton",
                           "movzx rax, al",
                           "leave", "ret")

        # ---- SDL2_mixer / audio helpers ---------------------------------------
        if needs_audio:
            # _audio_load_wav(rdi=path) -> rax (Mix_Chunk* handle, or 0 on failure)
            # Mix_LoadWAV is a macro for Mix_LoadWAV_RW(SDL_RWFromFile(path, "rb"), 1).
            if "_audio_load_wav" in self.ffi_called:
                self.emit("extern SDL_RWFromFile")
                self.emit("extern Mix_LoadWAV_RW")
                self.emit("section .rodata")
                self.emit('_audio_wav_mode_rb: db "rb",0')
                self.emit("section .text")
                self.label("_audio_load_wav")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("mov [rbp-8], rdi",
                           "lea rsi, [rel _audio_wav_mode_rb]",
                           "mov rdi, [rbp-8]",
                           "call SDL_RWFromFile",
                           "test rax, rax", "jz ._alw_fail",
                           "mov rdi, rax", "mov rsi, 1",
                           "call Mix_LoadWAV_RW",
                           "leave", "ret")
                self.label("._alw_fail")
                self.emitf("xor rax, rax", "leave", "ret")

        # ---- SDL2_ttf font rendering helpers -----------------------------------
        # SDL_Color {r,g,b,a} (4 bytes) is passed by value in a single GP
        # register under SysV; we pack r/g/b (alpha fixed at 255) ourselves.
        if needs_ttf:
            self.emit("extern TTF_RenderText_Blended")
            self.emit("extern TTF_SizeText")
            self.emit("section .bss")
            self.emit("_ttf_size_dim: resd 2")
            self.emit("section .text")

            # _ttf_render_blended(rdi=font, rsi=text, rdx=r, rcx=g, r8=b) -> rax (SDL_Surface*)
            # Real C call is TTF_RenderText_Blended(font, text, SDL_Color fg) --
            # only 3 args; fg is a 4-byte struct passed by value in one register.
            if "_ttf_render_blended" in self.ffi_called:
                self.label("_ttf_render_blended")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32")
                self.emitf("mov [rbp-8], rdi", "mov [rbp-16], rsi")
                # Pack SDL_Color {r,g,b,a=255} into one 32-bit value: r | g<<8 | b<<16 | 255<<24
                self.emitf("movzx rax, dl",      # r
                           "movzx r9, cl",       # g
                           "shl r9, 8", "or rax, r9",
                           "movzx r9, r8b",       # b
                           "shl r9, 16", "or rax, r9",
                           "mov r9, 255",
                           "shl r9, 24", "or rax, r9")
                self.emitf("mov rdi, [rbp-8]", "mov rsi, [rbp-16]",
                           "mov edx, eax",   # fg (3rd real arg)
                           "call TTF_RenderText_Blended",
                           "leave", "ret")

            # _ttf_size_text_w/h(rdi=font, rsi=text) -> rax (pixel width/height)
            if "_ttf_size_text_w" in self.ffi_called:
                self.label("_ttf_size_text_w")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("lea rdx, [_ttf_size_dim]",
                           "lea rcx, [_ttf_size_dim+4]",
                           "call TTF_SizeText",
                           "movsx rax, dword [_ttf_size_dim]",
                           "leave", "ret")
            if "_ttf_size_text_h" in self.ffi_called:
                self.label("_ttf_size_text_h")
                self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 16")
                self.emitf("lea rdx, [_ttf_size_dim]",
                           "lea rcx, [_ttf_size_dim+4]",
                           "call TTF_SizeText",
                           "movsx rax, dword [_ttf_size_dim+4]",
                           "leave", "ret")

        # ---- real pthread threading helpers (SysV AMD64 ABI) ----------------
        # Linked with -lpthread via the gcc driver.
        # Thread objects are asmpython dicts; HANDLE stored as integer under "._handle".
        # Lock objects store a heap-alloc'd pthread_mutex_t* under "._cs".
        if needs_thread:
            self.emit("extern pthread_create")
            self.emit("extern pthread_join")
            self.emit("extern pthread_self")
            self.emit("extern pthread_mutex_init")
            self.emit("extern pthread_mutex_lock")
            self.emit("extern pthread_mutex_unlock")
            self.emit("extern pthread_mutex_destroy")
            self.emit("section .rodata")
            _target_lbl, _ = self.intern_string("target")
            _handle_lbl, _ = self.intern_string("_handle")
            _cs_lbl, _     = self.intern_string("_cs")
            self.emit("section .text")

            # _threading_trampoline(rdi=thread_obj_ptr) -> rax=NULL
            # pthread_create start routine: void* fn(void* arg)
            self.label("_threading_trampoline")
            self.emitf(
                "push rbp", "mov rbp, rsp", "sub rsp, 32",
                "mov [rbp-8], rdi",          # save thread-obj ptr
                "test rdi, rdi",
                "jz ._tt_done",
                "call _threading_bootstrap",
            )
            self.label("._tt_done")
            self.emitf("xor rax, rax", "leave", "ret")

            # _threading_create(rdi=thread_obj_ptr) -> rax=thread_id (pthread_t)
            if "_threading_create" in self.ffi_called:
                self.label("_threading_create")
                self.emitf(
                    "push rbp", "mov rbp, rsp", "sub rsp, 32",
                    "mov [rbp-8], rdi",      # save obj ptr (also trampoline arg)
                    "sub rsp, 8",            # space for pthread_t
                    "mov rdi, rsp",          # &tid
                    "xor rsi, rsi",          # attr = NULL
                    "lea rdx, [_threading_trampoline]",  # start fn
                    "mov rcx, [rbp-8]",      # arg = thread-obj ptr
                    "call pthread_create",
                    "mov rax, [rsp]",        # load tid
                    "add rsp, 8",
                    "leave", "ret",
                )

            # _threading_join(rdi=tid) -> rax=0
            if "_threading_join" in self.ffi_called:
                self.label("_threading_join")
                self.emitf(
                    "push rbp", "mov rbp, rsp", "sub rsp, 16",
                    "xor rsi, rsi",          # retval = NULL
                    "call pthread_join",
                    "xor rax, rax", "leave", "ret",
                )

            # _threading_is_alive: no portable pthread check; return 0 (joined = dead)
            if "_threading_is_alive" in self.ffi_called:
                self.label("_threading_is_alive")
                self.emitf("xor rax, rax", "ret")

            # _threading_get_ident() -> rax = pthread_t (opaque, fits int64)
            if "_threading_get_ident" in self.ffi_called:
                self.label("_threading_get_ident")
                # push rbp: alignment padding, see _gui_poll_event's comment.
                self.emitf("push rbp", "call pthread_self", "pop rbp", "ret")

            # _threading_active_count() -> 1 (no global tracking)
            if "_threading_active_count" in self.ffi_called:
                self.label("_threading_active_count")
                self.emitf("mov rax, 1", "ret")

            # _threading_lock_init(rdi=lock_obj_ptr) -> rax=mutex_ptr
            if "_threading_lock_init" in self.ffi_called:
                self.label("_threading_lock_init")
                self.emitf(
                    "push rbp", "mov rbp, rsp", "sub rsp, 16",
                    "mov rdi, 40",   # sizeof(pthread_mutex_t) >= 40 on Linux x86-64
                    "call malloc",
                    "mov [rbp-8], rax",
                    "mov rdi, rax",
                    "xor rsi, rsi",  # NULL attr = default mutex
                    "call pthread_mutex_init",
                    "mov rax, [rbp-8]",
                    "leave", "ret",
                )

            # _threading_lock_acquire(rdi=mutex_ptr) -> rax=1
            if "_threading_lock_acquire" in self.ffi_called:
                self.label("_threading_lock_acquire")
                self.emitf(
                    "push rbp", "mov rbp, rsp", "sub rsp, 16",
                    "call pthread_mutex_lock",
                    "mov rax, 1", "leave", "ret",
                )

            # _threading_lock_release(rdi=mutex_ptr) -> rax=0
            if "_threading_lock_release" in self.ffi_called:
                self.label("_threading_lock_release")
                self.emitf(
                    "push rbp", "mov rbp, rsp", "sub rsp, 16",
                    "call pthread_mutex_unlock",
                    "xor rax, rax", "leave", "ret",
                )

            # _threading_lock_destroy(rdi=mutex_ptr)
            if "_threading_lock_destroy" in self.ffi_called:
                self.label("_threading_lock_destroy")
                self.emitf(
                    "push rbp", "mov rbp, rsp", "sub rsp, 16",
                    "call pthread_mutex_destroy",
                    "leave", "ret",
                )
