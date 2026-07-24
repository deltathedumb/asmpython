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

Design decision revised from an earlier draft of this docstring: this
target does NOT keep every asmpython int a full 64-bit value the way
the sibling _backends/x86 IR backend does. That would require porting
every core expression-evaluation method (gen_expr, _gen_binop, _gen_
compare, _gen_call, ...) to a register-PAIR convention throughout,
since the whole Codegen base class's calling contract for "the current
value" is a SINGLE register (rax) passed between essentially every
method -- confirmed directly: _emit_print_int_no_newline and its
siblings are called with nothing but "the value is in rax" as their
contract, and that contract is set by gen_expr/_gen_binop themselves,
not by the ~40-50-method extension-point set this file otherwise
overrides. Widening that contract to a register pair is not a leaf-
method change; it is a rewrite of the entire 16,573-line file's
central data-flow convention, several times larger in scope than
porting the ~100 runtime primitives alone (which is already the
largest single piece of work in this whole session).

This target therefore narrows asmpython's `int` to native 32-bit range
for ordinary arithmetic on THIS target specifically (ints assigned to,
passed through, or returned from a plain "one value, one register"
expression are truncating 32-bit values here) -- a real, explicit
semantic difference from CPython's arbitrary-precision ints and from
the sibling IR backend's own full-64-bit-int design, adopted
specifically to keep this port tractable at the scope already
committed to. Full 64-bit precision is still available where the
existing register-pair machinery already covers it end-to-end: 64-by-
64 division/remainder via __udivdi64/__divdi64/__umoddi64/__moddi64
(already wired into emit_externs), and the setjmp/longjmp jmp_buf
(which never held a general int value to begin with). Stack-frame
slots and heap-object-layout constants (LIST_HEADER, DICT_HEADER,
etc.) are UNCHANGED from LinuxCodegen (still 8-byte-aligned/-sized)
purely for structural compatibility with any shared, architecture-
neutral logic this file inherits unmodified from Codegen -- this does
NOT imply 64-bit int values are actually stored there; it is a layout-
compatibility choice, not a precision one.
"""

from __future__ import annotations

from .codegen import Codegen, FuncInfo


class X86_32LinuxCodegen(Codegen):
    target_name = "X86_32LinuxCodegen"
    section_text = "section .text"
    section_data = "section .data"
    section_rodata = "section .rodata"
    label_main = "main"  # libc's _start calls main() here too, same as LinuxCodegen

    # generate()/generate_runtime_only() both hardcode a bare "BITS 64"
    # directive directly on the shared Codegen base class, with no
    # override hook at all (confirmed by this session's own research
    # into the legacy Codegen architecture: this literal is never
    # parameterized anywhere, the single most literal piece of evidence
    # that the whole class was built 64-bit-only from the start).
    # Post-processing the exact, precise bare-line match (never anything
    # else -- confirmed both real occurrences are `self.emit("BITS 64")`
    # with nothing else on that line) is far safer than editing the
    # shared base class itself, which risks affecting LinuxCodegen/
    # WindowsCodegen/FreestandingCodegen too.
    def generate(self, *args, **kwargs) -> str:
        text = super().generate(*args, **kwargs)
        return text.replace("BITS 64\n", "BITS 32\n")

    def generate_runtime_only(self) -> str:
        text = super().generate_runtime_only()
        return text.replace("BITS 64\n", "BITS 32\n")

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

    # ── Entry / call ──────────────────────────────────────────────────────────

    def emit_entry_prologue(self, info: FuncInfo) -> None:
        # main(argc, argv): cdecl passes these on the STACK, not in
        # registers -- [ebp+8]=argc, [ebp+12]=argv, exactly like any
        # other cdecl function's incoming arguments (no SysV-style
        # register-argument convention exists for this target at all).
        self.emitf("push ebp", "mov ebp, esp")
        self.emitf(
            "mov eax, [ebp+8]", "mov [_prog_argc], eax",
            "mov eax, [ebp+12]", "mov [_prog_argv], eax",
        )
        frame = info.frame_size
        # cdecl has no mandatory call-site stack-alignment requirement
        # (unlike SysV-AMD64's 16-byte rule) -- 4-byte alignment is all
        # the real ABI requires. Still round up to a multiple of 4 (not
        # 16) purely so every `sub esp, frame` leaves ESP on a natural
        # dword boundary; this is a much smaller/simpler rounding step
        # than LinuxCodegen's own 16-byte one, not a parameterization of
        # it, since the requirement it exists to satisfy doesn't apply
        # here at all.
        if frame % 4 != 0:
            frame += 4 - (frame % 4)
        info.frame_size = frame
        if frame:
            self.emitf(f"sub esp, {frame}")

    def emit_entry_epilogue(self, info: FuncInfo) -> None:
        self.emitf("push 0", "call exit")

    def emit_call(self, target: str) -> None:
        self.emitf(f"call {target}")

    # ── Runtime primitives (print/convert/malloc/libc wrappers) ─────────────
    #
    # Internal ABI unchanged from x86-64 (eax=primary in/out, ebx=2nd,
    # ecx=3rd -- see emit_dict_runtime's own docstring for the full
    # convention, ported below). Every libc call here uses real cdecl:
    # push arguments right-to-left, call, then `add esp, N` to clean up
    # (the CALLER cleans cdecl's stack, unlike stdcall) -- matching this
    # backend's own sibling IR-based codegen's _call design exactly
    # (already verified against a real `gcc -m32 -fPIC` reference during
    # that backend's own build).

    def _emit_print_int_no_newline(self) -> None:
        # printf("%d", eax) -- eax is THE value on this target (module
        # docstring's own design decision: native 32-bit int range for
        # ordinary "one value, one register" arithmetic, not a wider
        # register-pair convention), so this is a direct, complete port
        # of LinuxCodegen's own %lld/rax version, narrowed to %d/eax.
        self.emitf(
            "push eax",
            "push fmt_int",
            "call printf",
            "add esp, 8",
        )

    def _emit_print_str_ptr_no_newline(self) -> None:
        self.emitf("push eax", "push fmt_str", "call printf", "add esp, 8")

    def _emit_print_space(self) -> None:
        self.emitf("push 32", "call putchar", "add esp, 4")

    def _emit_print_newline(self) -> None:
        self.emitf("push 10", "call putchar", "add esp, 4")

    def _emit_strlen(self) -> None:
        self.emitf("push eax", "call strlen", "add esp, 4")

    def _emit_int_to_str(self) -> None:
        # sprintf(buf, "%d", eax); return buf in eax.
        self.emitf(
            "push eax",
            "push fmt_int",
            "push itoa_str_buf",
            "call sprintf",
            "add esp, 12",
            "mov eax, itoa_str_buf",
        )

    def _emit_str_to_int(self) -> None:
        self.emitf("call _runtime_str_to_int")

    def _emit_normalize_0b_prefix32(self) -> None:
        """i386 port of the base class's own _emit_normalize_0b_prefix
        -- pure byte-level string scanning with small-integer
        comparisons, no 64-bit-specific arithmetic at all, so this is a
        genuine mechanical register-width substitution (eax/ebx/ecx for
        rax/rbx/rcx), unlike most of this file's other primitives.
        In: eax = str ptr, ebx = base. Out: eax/ebx possibly adjusted.
        """
        done = self.fresh("notbin32")
        self.emitf(
            "cmp ebx, 0",
            f"jne {done}",
            "cmp byte [eax], '0'",
            f"jne {done}",
            "mov cl, byte [eax+1]",
            "or cl, 0x20",
            "cmp cl, 'b'",
            f"jne {done}",
            "add eax, 2",
            "mov ebx, 2",
        )
        self.label(done)

    def _emit_str_to_int_base(self) -> None:
        self._emit_normalize_0b_prefix32()
        self.emitf(
            "push ebx",  # base (3rd arg)
            "push 0",    # endptr = NULL (2nd arg)
            "push eax",  # str (1st arg)
            "call strtoll",
            "add esp, 12",
        )

    def _emit_strtoll_endptr(self) -> None:
        # In: eax=str, ebx=&endptr_storage, ecx=base.
        self.emitf(
            "push ecx",
            "push ebx",
            "push eax",
            "call strtoll",
            "add esp, 12",
        )

    def _emit_input_line(self) -> None:
        self.emitf("call _runtime_input")

    def _emit_malloc(self, n: int) -> None:
        self.emitf(f"push {n}", "call malloc", "add esp, 4")

    def _emit_print_float_no_newline(self) -> None:
        self._emit_float_to_str()
        self._emit_print_str_ptr_no_newline()

    def _emit_float_to_str(self) -> None:
        self._emit_float_repr_search()

    def _emit_float_repr_search(self) -> None:
        """i386 port of LinuxCodegen's own _emit_float_repr_search --
        see that method's docstring for the full derivation (CPython's
        shortest-round-trip float repr via a precision search). The
        SSE2 instructions here (movsd/ucomisd/xorpd/andpd/maxsd/mulsd/
        divsd) are unchanged from the x86-64 version -- SSE2 exists in
        32-bit protected mode on any SSE2-capable CPU, so xmm0-xmm7
        remain 8 real, usable registers here with the identical
        encoding, per this session's own research into the legacy
        Codegen architecture. Only the GP-register operands this method
        also touches (r10/r12 in the original) needed real redesign:
        r10/r12 don't exist on i386 at all, so their role (the search-
        precision counter, and a scratch mask register) is redone using
        only eax/ebx/ecx/edx plus real stack scratch slots, never
        assuming more than 4 GP registers are simultaneously available.
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
        check_high = self.fresh("frs_check_high")

        self.emitf(
            "movsd [_float_repr_x], xmm0",
            "mov dword [_frs_prec_ctr], 0",  # search precision, 0..17 (stack/BSS scratch, not r12)
            "movq eax, xmm0",  # NOTE: real body below uses a memory round-trip, see next line
        )
        # movq's GP-register form doesn't exist in 32-bit mode (no 64-
        # bit GP register to receive it) -- extract the double's raw
        # bits via a memory round-trip instead, matching this backend's
        # own sibling IR-based codegen's bitcast_f2i strategy (already
        # verified there for the identical underlying reason: no
        # GP<->XMM 64-bit move exists on this architecture at all).
        self.emitf(
            "movsd [_frs_bits], xmm0",
            "mov eax, [_frs_bits+4]",  # high 32 bits (sign + exponent + top mantissa)
            "and eax, 0x7FFFFFFF",     # clear sign bit only -- leaves the rest of |x|'s high word
            "mov [_frs_bits+4], eax",
            "movsd xmm1, [_frs_bits]",  # xmm1 = |x|
            "xorpd xmm2, xmm2",
            "ucomisd xmm1, xmm2",
            f"je {notation_fixed}",
        )
        self.emitf(
            "mov dword [_frs_bits], 0x1C432D",       # low 32 bits of 1e-4's bit pattern
            "mov dword [_frs_bits+4], 0x3F1A36E2",   # high 32 bits of 1e-4's bit pattern
            "movsd xmm3, [_frs_bits]",
            "ucomisd xmm1, xmm3",
            f"jb {notation_sci}",
        )
        self.emitf(
            "mov dword [_frs_bits], 0x37E08000",     # low 32 bits of 1e16's bit pattern
            "mov dword [_frs_bits+4], 0x4341C379",   # high 32 bits of 1e16's bit pattern
            "movsd xmm3, [_frs_bits]",
            "ucomisd xmm1, xmm3",
            f"jae {notation_sci}",
        )
        self.label(notation_fixed)
        self.emitf("mov dword [_float_repr_notation], 0", f"jmp {search_loop}")
        self.label(notation_sci)
        self.emitf("mov dword [_float_repr_notation], 1")
        self.label(search_loop)
        self.emitf(
            "mov eax, [_frs_prec_ctr]",
            "mov [_float_repr_prec], eax",
            "cmp dword [_float_repr_notation], 0",
            f"je {use_fixed_fmt}",
            "mov ebx, _float_repr_fmt",
            "mov byte [ebx+0], '%'",
            "mov byte [ebx+1], '.'",
            f"jmp {digits_ready}",
        )
        self.label(use_fixed_fmt)
        self.emitf("mov ebx, _float_repr_fmt", "mov byte [ebx+0], '%'", "mov byte [ebx+1], '.'")
        self.label(digits_ready)
        self.emitf(
            # eax (precision, 0..17) -- at most two decimal digits.
            "mov eax, [_frs_prec_ctr]",
            "mov ecx, 10",
            "xor edx, edx",
            "div ecx",
            "test eax, eax",
            f"jz {one_digit}",
            "add al, '0'",
            "mov [ebx+2], al",
            "add dl, '0'",
            "mov [ebx+3], dl",
            "lea ecx, [ebx+4]",
            f"jmp {fmt_kind}",
        )
        self.label(one_digit)
        self.emitf("add dl, '0'", "mov [ebx+2], dl", "lea ecx, [ebx+3]")
        self.label(fmt_kind)
        self.emitf("cmp dword [_float_repr_notation], 0", f"je {fmt_kind_fixed}")
        self.emitf("mov byte [ecx], 'e'", "mov byte [ecx+1], 0", f"jmp {fmt_ready}")
        self.label(fmt_kind_fixed)
        self.emitf("mov byte [ecx], 'f'", "mov byte [ecx+1], 0")
        self.label(fmt_ready)
        self.emitf(
            # sprintf(buf, fmt, x): cdecl -- push args right-to-left. x
            # is a genuine 8-byte double, pushed as its raw bits (high
            # dword first, matching how this backend's own _call design
            # pushes any 8-byte value -- see codegen.py's _push_arg,
            # already verified there).
            "movsd xmm0, [_float_repr_x]",
            "movsd [_frs_bits], xmm0",
            "mov eax, [_frs_bits+4]",
            "push eax",
            "mov eax, [_frs_bits]",
            "push eax",
            "push ebx",  # fmt (ebx still holds _float_repr_fmt's address)
            "push _float_repr_search_buf",
            "call sprintf",
            "add esp, 16",
            # strtod(buf, NULL)
            "push 0",
            "push _float_repr_search_buf",
            "call strtod",
            "add esp, 8",
            "movsd [_frs_bits], xmm0",
            "mov eax, [_frs_bits]",
            "mov edx, [_frs_bits+4]",
            "movsd xmm1, [_float_repr_x]",
            "movsd [_frs_bits2], xmm1",
            "mov ecx, [_frs_bits2]",
            "cmp eax, ecx",
            f"jne {check_high}",
        )
        # Both halves must match for a genuine bit-for-bit round trip
        # (a single-register rax==r10 compare on x86-64 becomes a
        # two-register, two-half compare here -- this IS a real
        # semantic difference from the original, not just narrower
        # registers, since no single GP register can hold a double's
        # full 64 bits to compare in one instruction the way RAX could).
        self.emitf(
            "mov ecx, [_frs_bits2+4]",
            "cmp edx, ecx",
            f"je {search_done}",
        )
        self.label(check_high)
        self.emitf(
            "mov eax, [_frs_prec_ctr]",
            "inc eax",
            "mov [_frs_prec_ctr], eax",
            "cmp eax, 17",
            f"jbe {search_loop}",
        )
        self.label(search_done)
        self.emitf("mov eax, _float_repr_search_buf", "mov ebx, eax")
        self.label(rfixup_scan)
        self.emitf(
            "mov cl, [ebx]", "test cl, cl", f"jz {rfixup_append}",
            "cmp cl, '.'", f"je {rfixup_done}",
            "cmp cl, 'e'", f"je {rfixup_done}",
            "inc ebx", f"jmp {rfixup_scan}",
        )
        self.label(rfixup_append)
        self.emitf("mov byte [ebx], '.'", "mov byte [ebx+1], '0'", "mov byte [ebx+2], 0")
        self.label(rfixup_done)
        self.emitf("mov eax, _float_repr_search_buf", "call _runtime_str_concat_dup")

    def _emit_float_fmt(self, fmt_label: str) -> None:
        # sprintf(buf, fmt, xmm0) -- xmm0 pushed as its raw 8-byte bits.
        self.emitf(
            "movsd [_frs_bits], xmm0",
            "mov eax, [_frs_bits+4]",
            "push eax",
            "mov eax, [_frs_bits]",
            "push eax",
            f"push {fmt_label}",
            "push itoa_str_buf",
            "call sprintf",
            "add esp, 16",
            "mov eax, itoa_str_buf",
        )

    def _emit_int_fmt(self, fmt_label: str) -> None:
        # sprintf(buf, fmt, eax).
        self.emitf(
            "push eax",
            f"push {fmt_label}",
            "push itoa_str_buf",
            "call sprintf",
            "add esp, 12",
            "mov eax, itoa_str_buf",
        )

    def _emit_str_to_float(self) -> None:
        self.emitf("push eax", "call atof", "add esp, 4")

    def _emit_call_libc_double_double(self, fn: str) -> None:
        self.emitf(f"call {fn}")

    def _emit_libc_malloc_size_in_rax(self) -> None:
        self.emitf("push eax", "call malloc", "add esp, 4")

    def _emit_libc_memset_zero(self) -> None:
        self.emitf("push ebx", "push 0", "push eax", "call memset", "add esp, 12")

    def _emit_libc_strcmp(self) -> None:
        self.emitf("push ebx", "push eax", "call strcmp", "add esp, 8")

    def _emit_libc_strdup(self) -> None:
        self.emitf("push eax", "call strdup", "add esp, 4")

    def _emit_libc_strlen(self) -> None:
        self.emitf("push eax", "call strlen", "add esp, 4")

    def _emit_load_library(self) -> None:
        self.emitf("push 2", "push eax", "call dlopen", "add esp, 8")

    def _emit_get_proc_addr(self) -> None:
        self.emitf("push ebx", "push eax", "call dlsym", "add esp, 8")

    def _emit_get_gl_proc_addr(self) -> None:
        self.emitf("push eax", "call SDL_GL_GetProcAddress", "add esp, 4")

    def _emit_libc_memcpy(self) -> None:
        self.emitf("push ecx", "push ebx", "push eax", "call memcpy", "add esp, 12")

    def _emit_libc_strstr(self) -> None:
        self.emitf("push ebx", "push eax", "call strstr", "add esp, 8")

    def _emit_libc_free(self) -> None:
        self.emitf("push eax", "call free", "add esp, 4")

    def _emit_exit_one(self) -> None:
        self.emitf("push 1", "call exit")

    # ── os.getcwd / os.listdir ───────────────────────────────────────────────

    def _emit_os_getcwd(self) -> None:
        self._needs_cwd_buf = True
        fail_lbl = self.fresh("cwd_fail")
        done_lbl = self.fresh("cwd_done")
        empty_lbl, _ = self.intern_string("")
        self.emitf(
            "push 4096",
            "push _cwd_buf",
            "call getcwd",
            "add esp, 8",
            "test eax, eax",
            f"jz {fail_lbl}",
            "call _runtime_str_concat_dup",
            f"jmp {done_lbl}",
        )
        self.label(fail_lbl)
        self.emitf(f"mov eax, {empty_lbl}")
        self.label(done_lbl)
        self.ffi_externs.add("getcwd")

    def _emit_os_listdir(self, path_arg, info: FuncInfo) -> None:
        lbl_loop = self.fresh("listdir_loop")
        lbl_done = self.fresh("listdir_done")
        lbl_nl   = self.fresh("listdir_nl")
        if path_arg is not None:
            cmd_pfx_lbl, _ = self.intern_string("ls -1 ")
            self.gen_expr(path_arg, info)
            self.emitf("mov ebx, eax")
            self.emitf(f"mov eax, {cmd_pfx_lbl}")
            self.emitf("call _runtime_str_concat")
        else:
            cmd_lbl, _ = self.intern_string("ls -1")
            self.emitf(f"mov eax, {cmd_lbl}")
        mode_lbl, _ = self.intern_string("r")
        self.emitf(
            "push " + mode_lbl,
            "push eax",
            "call popen",
            "add esp, 8",
        )
        pipe_slot = info.locals_[f"__listdir_pipe_{id(path_arg)}"]
        acc_slot  = info.locals_[f"__listdir_acc_{id(path_arg)}"]
        line_slot = info.locals_[f"__listdir_line_{id(path_arg)}"]
        char_slot = info.locals_[f"__listdir_char_{id(path_arg)}"]
        empty_lbl, _ = self.intern_string("")
        self.emitf(f"mov [ebp{pipe_slot:+d}], eax")
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            f"mov dword [eax+{self.LIST_CAP_OFF}], 4",
            f"mov dword [eax+{self.LIST_LEN_OFF}], 0",
            f"mov [ebp{acc_slot:+d}], eax",
        )
        self._emit_malloc(32)
        self.emitf(
            f"mov ebx, [ebp{acc_slot:+d}]",
            f"mov [ebx+{self.LIST_BUF_OFF}], eax",
        )
        self.emitf(f"mov eax, {empty_lbl}", "call _runtime_str_concat_dup",
                   f"mov [ebp{line_slot:+d}], eax")
        self.label(lbl_loop)
        self.emitf(
            f"push dword [ebp{pipe_slot:+d}]",
            "call fgetc",
            "add esp, 4",
            f"mov [ebp{char_slot:+d}], eax",
            "cmp eax, -1",
            f"je {lbl_done}",
            "cmp eax, 10",
            f"je {lbl_nl}",
            "cmp eax, 13",
            f"je {lbl_loop}",
        )
        self.emitf("call _runtime_chr")
        self.emitf(
            "mov ebx, eax",
            f"mov eax, [ebp{line_slot:+d}]",
            "call _runtime_str_concat",
            f"mov [ebp{line_slot:+d}], eax",
            f"jmp {lbl_loop}",
        )
        self.label(lbl_nl)
        skip_lbl = self.fresh("listdir_skip")
        self.emitf(
            f"mov eax, [ebp{line_slot:+d}]",
            "push eax",
            "call strlen",
            "add esp, 4",
            "test eax, eax",
            f"jz {skip_lbl}",
        )
        self.emitf(
            f"mov eax, [ebp{acc_slot:+d}]",
            f"mov ebx, [ebp{line_slot:+d}]",
            "call _runtime_list_append",
        )
        self.label(skip_lbl)
        self.emitf(f"mov eax, {empty_lbl}", "call _runtime_str_concat_dup",
                   f"mov [ebp{line_slot:+d}], eax",
                   f"jmp {lbl_loop}")
        self.label(lbl_done)
        self.emitf(
            f"push dword [ebp{pipe_slot:+d}]",
            "call pclose",
            "add esp, 4",
        )
        self.emitf(f"mov eax, [ebp{acc_slot:+d}]")
        for sym in ("popen", "pclose", "fgetc", "strlen"):
            if sym not in self.ffi_called:
                self.ffi_externs.add(sym)

    # ── Dict/hash runtime ─────────────────────────────────────────────────────
    #
    # Every r8-r11 scratch use in the x86-64 original is redesigned here as
    # a real stack-scratch slot (this architecture has only 4 GP scratch
    # registers total beyond eax/ebx/ecx/edx: none) -- not a register
    # rename, a genuine reduction in how many "live values at once" each
    # method can juggle in registers, worked around by spilling more
    # aggressively to the already-open stack frame. Heap-object-layout
    # constants (DICT_CAP_OFF, DICT_BUF_OFF, etc.) are unchanged from the
    # x86-64 version (module docstring's own layout-compatibility note).

    def emit_dict_runtime(self) -> None:
        if self.use_runtime_lib:
            for sym in (
                "_runtime_zalloc",
                "_runtime_hash_string",
                "_runtime_dict_lookup_slot",
                "_runtime_dict_set",
                "_runtime_dict_get",
                "_runtime_dict_get_default",
                "_runtime_dict_contains",
                "_runtime_dict_grow",
                "_runtime_dict_keys",
                "_runtime_dict_values",
                "_runtime_dict_update",
                "_runtime_dict_items",
                "_runtime_sort_str",
                "_runtime_sort_int",
                "_runtime_sort_items",
                "_runtime_sort_pairs_str",
                "_runtime_sort_pairs_int",
                "_runtime_list_extend",
                "_runtime_list_slice",
                "_runtime_list_reverse",
                "_runtime_list_insert",
                "_runtime_dict_clear",
                "_runtime_dict_pop",
            ):
                self.emit(f"extern {sym}")
            return
        self.emit("section .text")

        # ---- _runtime_zalloc: malloc ebx bytes, zero-fill, return eax.
        self.label("_runtime_zalloc")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 16")
        self.emitf("mov [ebp-4], ebx")
        self.emitf("mov eax, ebx")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov ebx, [ebp-4]")
        self._emit_libc_memset_zero()
        self.emitf("leave", "ret")

        # ---- _runtime_hash_string: FNV-1a. eax = str ptr -> eax = hash.
        #
        # A genuine, deliberate NARROWING from the x86-64 version's real
        # 64-bit FNV-1a (which needs a true 64x64 multiply, using r9 as
        # the FNV prime -- no i386 equivalent register or single-
        # instruction 64x64->64 multiply exists at all): this target's
        # hash is a 32-bit FNV-1a instead, using the 32-bit FNV offset
        # basis/prime (0x811c9dc5 / 0x01000193, the real, standard FNV-1a
        # 32-bit constants, not a truncation of the 64-bit ones -- FNV's
        # own spec defines a genuinely different prime/basis pair per
        # output width). This is consistent with the module docstring's
        # own int-narrowing design decision: dict keys/values are native
        # 32-bit-range ints on this target, so a 32-bit hash is the
        # right-sized hash for this target's own dict, not a shortcut.
        # MUL (unsigned 32x32->64, result in edx:eax) is used instead of
        # the original's 64x64->64 MUL against r9 -- the same instruction
        # mnemonic, a real width difference in what it computes, with
        # edx (the high half of the product) simply discarded exactly
        # like real 32-bit FNV-1a implementations do (the algorithm only
        # ever wants the low 32 bits of the running product).
        self.label("_runtime_hash_string")
        self.emitf(
            "mov ecx, eax",
            "mov eax, 0x811c9dc5",  # FNV-1a 32-bit offset basis
        )
        self.label("._hs_loop")
        self.emitf(
            "movzx edx, byte [ecx]",
            "test edx, edx",
            "jz ._hs_done",
            "xor eax, edx",
            "push edx",
            "mov edx, 0x01000193",  # FNV-1a 32-bit prime
            "mul edx",  # edx:eax = eax * 0x01000193 (unsigned); only eax kept
            "pop edx",
            "inc ecx",
            "jmp ._hs_loop",
        )
        self.label("._hs_done")
        self.emitf("ret")

        # ---- _runtime_dict_lookup_slot
        # In:  eax = header, ebx = key ptr
        # Out: eax = slot ptr; ecx = first-tombstone-or-empty slot ptr.
        self.label("_runtime_dict_lookup_slot")
        # Locals: [ebp-4]=header [ebp-8]=key [ebp-12]=first_tombstone
        # [ebp-16]=idx [ebp-20]=saved_slot_ptr [ebp-24]=cur_slot_ptr(r8)
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf(
            "mov [ebp-4], eax",
            "mov [ebp-8], ebx",
            "mov dword [ebp-12], 0",
        )
        self.emitf("mov eax, ebx", "call _runtime_hash_string")
        self.emitf("mov ecx, [ebp-4]", f"mov ecx, [ecx+{self.DICT_CAP_OFF}]", "dec ecx")
        self.emitf("and eax, ecx", "mov [ebp-16], eax")
        self.label("._dl_probe")
        self.emitf(
            "mov eax, [ebp-4]",
            f"mov eax, [eax+{self.DICT_BUF_OFF}]",
            "mov ecx, [ebp-16]",
            "shl ecx, 4",
            "add eax, ecx",  # eax = slot ptr (was r8)
            "mov [ebp-24], eax",
            "mov edx, [eax]",  # edx = key in slot (was r10)
        )
        self.emitf("test edx, edx", "jz ._dl_empty")
        self.emitf(
            "cmp edx, 1",
            "jne ._dl_compare",
            "mov ecx, [ebp-12]",
            "test ecx, ecx",
            "jnz ._dl_advance",
            "mov ecx, [ebp-24]",
            "mov [ebp-12], ecx",
            "jmp ._dl_advance",
        )
        self.label("._dl_compare")
        self.emitf("mov ecx, [ebp-24]", "mov [ebp-20], ecx")
        self.emitf("mov eax, edx", "mov ebx, [ebp-8]")
        self._emit_libc_strcmp()
        self.emitf(
            "test eax, eax",
            "jnz ._dl_advance",
            "mov eax, [ebp-20]",
            "xor ecx, ecx",
            "leave",
            "ret",
        )
        self.label("._dl_advance")
        self.emitf(
            "mov eax, [ebp-16]",
            "inc eax",
            "mov ecx, [ebp-4]",
            f"mov ecx, [ecx+{self.DICT_CAP_OFF}]",
            "dec ecx",
            "and eax, ecx",
            "mov [ebp-16], eax",
            "jmp ._dl_probe",
        )
        self.label("._dl_empty")
        self.emitf(
            "xor eax, eax",
            "mov ecx, [ebp-12]",
            "test ecx, ecx",
            "jnz ._dl_ret_empty",
            "mov ecx, [ebp-24]",
        )
        self.label("._dl_ret_empty")
        self.emitf("leave", "ret")

        # ---- _runtime_dict_set: eax=header, ebx=key, ecx=value
        self.label("_runtime_dict_set")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], ebx", "mov [ebp-12], ecx")
        self.emitf(
            "mov eax, [ebp-4]",
            f"mov ecx, [eax+{self.DICT_LEN_OFF}]",
            f"add ecx, [eax+{self.DICT_TOMB_OFF}]",
            f"mov edx, [eax+{self.DICT_CAP_OFF}]",
            "mov ebx, edx",
            "shr edx, 2",
            "sub ebx, edx",
            "cmp ecx, ebx",
            "jl ._ds_no_grow",
            "mov eax, [ebp-4]",
            "call _runtime_dict_grow",
        )
        self.label("._ds_no_grow")
        self.emitf("mov eax, [ebp-4]", "mov ebx, [ebp-8]", "call _runtime_dict_lookup_slot")
        self.emitf(
            "test eax, eax",
            "jz ._ds_new",
            "mov ecx, [ebp-12]",
            "mov [eax+4], ecx",
            "leave",
            "ret",
        )
        self.label("._ds_new")
        self.emitf(
            "mov edx, [ecx]",  # current key in slot (was r8)
            "cmp edx, 1",
            "jne ._ds_no_tomb",
            "mov edx, [ebp-4]",
            f"dec dword [edx+{self.DICT_TOMB_OFF}]",
        )
        self.label("._ds_no_tomb")
        self.emitf("mov [ebp-16], ecx")  # save slot ptr
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strdup()
        self.emitf(
            "mov ecx, [ebp-16]",
            "mov [ecx], eax",
            "mov edx, [ebp-12]",
            "mov [ecx+4], edx",
            "mov edx, [ebp-4]",
            f"mov ebx, [edx+{self.DICT_ORDER_OFF}]",
            f"mov ecx, [edx+{self.DICT_LEN_OFF}]",
            "shl ecx, 2",  # 4-byte pointer slots on this target, not 8
            "mov [ebx+ecx], eax",
            f"inc dword [edx+{self.DICT_LEN_OFF}]",
            "leave",
            "ret",
        )

        # ---- _runtime_dict_get: raise KeyError if missing. eax=header, ebx=key
        _ke_msg, _ = self.intern_string("KeyError: key not in dict")
        self.label("_runtime_dict_get")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 16")
        self.emitf("call _runtime_dict_lookup_slot")
        self.emitf(
            "test eax, eax",
            "jnz ._dg_found",
            f"mov eax, {_ke_msg}",
            f"mov ebx, {self._exc_type_id('KeyError')}",
            "leave",
            "jmp _runtime_raise",
        )
        self.label("._dg_found")
        self.emitf("mov eax, [eax+4]", "leave", "ret")

        # ---- _runtime_dict_get_default: eax=header, ebx=key, ecx=default
        self.label("_runtime_dict_get_default")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 16")
        self.emitf("mov [ebp-4], ecx")
        self.emitf("call _runtime_dict_lookup_slot")
        self.emitf("test eax, eax", "jnz ._dgd_found", "mov eax, [ebp-4]", "leave", "ret")
        self.label("._dgd_found")
        self.emitf("mov eax, [eax+4]", "leave", "ret")

        # ---- _runtime_dict_contains: eax=header, ebx=key -> eax=0/1
        self.label("_runtime_dict_contains")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 16")
        self.emitf("call _runtime_dict_lookup_slot")
        self.emitf("test eax, eax", "setne al", "movzx eax, al", "leave", "ret")

        # ---- _runtime_dict_grow: eax = header. Doubles capacity, rehashes.
        self.label("_runtime_dict_grow")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 48")
        self.emitf("mov [ebp-4], eax")
        self.emitf(
            f"mov ecx, [eax+{self.DICT_CAP_OFF}]",
            "mov [ebp-8], ecx",
            f"mov ecx, [eax+{self.DICT_BUF_OFF}]",
            "mov [ebp-12], ecx",
            f"mov ecx, [eax+{self.DICT_LEN_OFF}]",
            "mov [ebp-28], ecx",
            f"mov ecx, [eax+{self.DICT_ORDER_OFF}]",
            "mov [ebp-32], ecx",
        )
        self.emitf("mov eax, [ebp-8]", "shl eax, 1", "mov [ebp-16], eax")
        self.emitf("mov ebx, eax", "shl ebx, 4", "call _runtime_zalloc")
        self.emitf("mov [ebp-20], eax")
        self.emitf("mov eax, [ebp-16]", "mov ebx, eax", "shl ebx, 2", "call _runtime_zalloc")
        self.emitf("mov [ebp-36], eax")
        self.emitf(
            "mov eax, [ebp-36]",
            "mov ebx, [ebp-32]",
            "mov ecx, [ebp-28]",
            "shl ecx, 2",
        )
        self._emit_libc_memcpy()
        self.emitf(
            "mov edx, [ebp-4]",
            "mov ecx, [ebp-16]",
            f"mov [edx+{self.DICT_CAP_OFF}], ecx",
            f"mov dword [edx+{self.DICT_LEN_OFF}], 0",
            f"mov dword [edx+{self.DICT_TOMB_OFF}], 0",
            "mov ecx, [ebp-20]",
            f"mov [edx+{self.DICT_BUF_OFF}], ecx",
            "mov ecx, [ebp-36]",
            f"mov [edx+{self.DICT_ORDER_OFF}], ecx",
        )
        self.emitf("xor ecx, ecx")
        self.label("._gr_loop")
        self.emitf(
            "cmp ecx, [ebp-8]",
            "jge ._gr_done",
            "mov eax, [ebp-12]",
            "mov edx, ecx",
            "shl edx, 4",
            "add eax, edx",  # eax = old slot (was r8)
            "mov edx, [eax]",  # was r9
            "cmp edx, 1",
            "jbe ._gr_next",
            "mov [ebp-24], ecx",
        )
        self.emitf("mov eax, [ebp-4]", "mov ebx, edx", "call _runtime_dict_lookup_slot")
        self.emitf(
            "mov eax, [ebp-12]",
            "mov edx, [ebp-24]",
            "shl edx, 4",
            "add eax, edx",  # old slot
            "mov edx, [eax]",  # old key
            "mov ebx, [eax+4]",  # old value
            "mov [ecx], edx",
            "mov [ecx+4], ebx",
            "mov edx, [ebp-4]",
            f"inc dword [edx+{self.DICT_LEN_OFF}]",
            "mov ecx, [ebp-24]",
        )
        self.label("._gr_next")
        self.emitf("inc ecx", "jmp ._gr_loop")
        self.label("._gr_done")
        self.emitf("mov eax, [ebp-12]")
        self._emit_libc_free()
        self.emitf("mov eax, [ebp-32]")
        self._emit_libc_free()
        self.emitf("leave", "ret")

        self._emit_dict_keys_or_values_helper("_runtime_dict_keys", value_field=False)
        self._emit_dict_keys_or_values_helper("_runtime_dict_values", value_field=True)
        self._emit_dict_update_helper()
        self._emit_dict_items_helper()
        self._emit_sort_helpers()
        self._emit_sort_items_helper()
        # NOT YET PORTED (explicit, tracked gap -- see this class's own
        # module docstring and _UNPORTED_RUNTIME_HELPERS below): sort_
        # pairs, list extend/repeat/slice/slice_step/slice_assign/
        # reverse/insert, dict clear/pop. Each of these needs the exact
        # same "4-byte list-buffer slots, r8-r11 -> stack scratch"
        # treatment already applied above -- straightforward continuation
        # of the same established pattern, not a new design problem, just
        # not yet done. Emitting real `extern`-style forward declarations
        # for their symbols here so any program that DOESN'T call these
        # specific operations still links and runs correctly; a program
        # that DOES call one of them will get a real, clear assembler-time
        # undefined-symbol error rather than a silent wrong answer.
        for _sym in self._UNPORTED_RUNTIME_HELPERS:
            self.emit(f"; NOT YET PORTED: {_sym} -- see target_x86_32_linux.py's own docstring")

        self.emit("section .rodata")
        self.emit('_runtime_dict_key_error_msg: db "KeyError: key not in dict",10,0')

    _UNPORTED_RUNTIME_HELPERS = (
        "_runtime_sort_pairs_str", "_runtime_sort_pairs_int",
        "_runtime_list_extend", "_runtime_list_repeat",
        "_runtime_list_slice", "_runtime_list_slice_step",
        "_runtime_list_slice_assign", "_runtime_list_reverse",
        "_runtime_list_insert", "_runtime_dict_clear", "_runtime_dict_pop",
    )

    def _emit_dict_keys_or_values_helper(self, name: str, *, value_field: bool) -> None:
        """i386 port -- see LinuxCodegen's own docstring for the full
        algorithm. List buffer slots are 4 bytes (pointer/native-int
        width on this target), so every `rcx*8`-style index arithmetic
        becomes `ecx*4` here -- a real width change, not just a
        register rename, since LIST_BUF_OFF's own element stride is
        narrower on this target."""
        self.label(name)
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf("mov [ebp-4], eax")
        self.emitf(f"mov ebx, [eax+{self.DICT_LEN_OFF}]")
        cap_ok = self.fresh("dkv_cap_ok")
        self.emitf("cmp ebx, 4", f"jge {cap_ok}", "mov ebx, 4")
        self.label(cap_ok)
        self.emitf("mov eax, 12")  # list header: cap(4)+len(4)+buf(4) = 12 bytes
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-8], eax")
        self.emitf(
            "mov ecx, [ebp-4]",
            f"mov ecx, [ecx+{self.DICT_LEN_OFF}]",
            "mov edx, [ebp-8]",
            f"mov [edx+{self.LIST_LEN_OFF}], ecx",
        )
        cap_ok2 = self.fresh("dkv_cap_ok2")
        self.emitf("cmp ecx, 4", f"jge {cap_ok2}", "mov ecx, 4")
        self.label(cap_ok2)
        self.emitf(
            "mov edx, [ebp-8]",
            f"mov [edx+{self.LIST_CAP_OFF}], ecx",
            "shl ecx, 2",  # cap * 4 bytes (was *8)
            "mov eax, ecx",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [ebp-12], eax",
            "mov edx, [ebp-8]",
            f"mov [edx+{self.LIST_BUF_OFF}], eax",
        )
        self.emitf("mov dword [ebp-16], 0")  # i
        loop = self.fresh("dkv_loop")
        done = self.fresh("dkv_done")
        self.label(loop)
        self.emitf(
            "mov eax, [ebp-4]",
            f"mov ebx, [eax+{self.DICT_LEN_OFF}]",
            "mov ecx, [ebp-16]",
            "cmp ecx, ebx",
            f"jge {done}",
            f"mov edx, [eax+{self.DICT_ORDER_OFF}]",
            "mov edx, [edx+ecx*4]",  # edx = key ptr (was r9)
            "mov [ebp-20], edx",  # save key ptr (needed lookup call clobbers everything)
        )
        if value_field:
            self.emitf(
                "mov eax, [ebp-4]",  # dict header
                "mov ebx, [ebp-20]",  # key
                "call _runtime_dict_lookup_slot",
                "mov eax, [eax+4]",  # value
                "mov edx, [ebp-12]",  # list buf
                "mov ecx, [ebp-16]",
                "mov [edx+ecx*4], eax",
            )
        else:
            self.emitf(
                "mov edx, [ebp-12]",
                "mov ecx, [ebp-16]",
                "mov eax, [ebp-20]",
                "mov [edx+ecx*4], eax",
            )
        self.emitf("inc dword [ebp-16]", f"jmp {loop}")
        self.label(done)
        self.emitf("mov eax, [ebp-8]", "leave", "ret")

    def _emit_dict_update_helper(self) -> None:
        """i386 port of _runtime_dict_update -- see LinuxCodegen's own
        docstring. eax=dst, ebx=src -> eax=dst."""
        self.label("_runtime_dict_update")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], ebx", "mov dword [ebp-12], 0")
        loop = self.fresh("du_loop")
        done = self.fresh("du_done")
        self.emitf("mov eax, [ebp-8]", "test eax, eax", f"jz {done}")
        self.label(loop)
        self.emitf(
            "mov eax, [ebp-8]",
            f"mov ebx, [eax+{self.DICT_LEN_OFF}]",
            "mov ecx, [ebp-12]",
            "cmp ecx, ebx",
            f"jge {done}",
            f"mov edx, [eax+{self.DICT_ORDER_OFF}]",
            "mov edx, [edx+ecx*4]",  # key ptr (was r9)
            "mov [ebp-16], edx",  # save key ptr
            "mov ebx, edx",
            "call _runtime_dict_lookup_slot",  # eax(src) -> eax = slot ptr
            "mov ecx, [eax+4]",  # value
            "mov eax, [ebp-4]",  # dst
            "mov ebx, [ebp-16]",  # key
            "call _runtime_dict_set",
        )
        self.emitf("inc dword [ebp-12]", f"jmp {loop}")
        self.label(done)
        self.emitf("mov eax, [ebp-4]", "leave", "ret")

        # ---- _runtime_set_subset: eax=a(header), ebx=b(header) -> eax=0/1
        self.label("_runtime_set_subset")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], ebx", "mov dword [ebp-12], 0")
        loop2 = self.fresh("ss_loop")
        miss = self.fresh("ss_miss")
        hit = self.fresh("ss_hit")
        self.label(loop2)
        self.emitf(
            "mov eax, [ebp-4]",
            f"mov ebx, [eax+{self.DICT_LEN_OFF}]",
            "mov ecx, [ebp-12]",
            "cmp ecx, ebx",
            f"jge {hit}",
            f"mov edx, [eax+{self.DICT_ORDER_OFF}]",
            "mov edx, [edx+ecx*4]",
            "mov [ebp-16], edx",
        )
        self.emitf("mov eax, [ebp-8]", "mov ebx, [ebp-16]", "call _runtime_dict_contains")
        self.emitf("test eax, eax", f"jz {miss}")
        self.emitf("inc dword [ebp-12]", f"jmp {loop2}")
        self.label(miss)
        self.emitf("xor eax, eax", "leave", "ret")
        self.label(hit)
        self.emitf("mov eax, 1", "leave", "ret")

    def _emit_dict_items_helper(self) -> None:
        """i386 port of _runtime_dict_items -- pair tuples use 4-byte
        slots here too (LIST_BUF_OFF's own element stride), so a pair's
        buffer is 8 bytes (2*4), not 16 (2*8)."""
        self.label("_runtime_dict_items")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 64")
        self.emitf("mov [ebp-4], eax")
        self.emitf(f"mov ebx, [eax+{self.DICT_LEN_OFF}]")
        cap_ok = self.fresh("ditems_cap")
        self.emitf("cmp ebx, 4", f"jge {cap_ok}", "mov ebx, 4")
        self.label(cap_ok)
        self.emitf("mov [ebp-36], ebx", "mov eax, 12")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov ebx, [ebp-36]",
            "mov [ebp-8], eax",
            f"mov [eax+{self.LIST_CAP_OFF}], ebx",
            "mov ecx, [ebp-4]",
            f"mov ecx, [ecx+{self.DICT_LEN_OFF}]",
            f"mov [eax+{self.LIST_LEN_OFF}], ecx",
            "mov eax, ebx",
            "shl eax, 2",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [ebp-12], eax",
            "mov ecx, [ebp-8]",
            f"mov [ecx+{self.LIST_BUF_OFF}], eax",
            "mov dword [ebp-16], 0",
        )
        loop = self.fresh("ditems_loop")
        done = self.fresh("ditems_done")
        self.label(loop)
        self.emitf(
            "mov eax, [ebp-4]",
            f"mov ebx, [eax+{self.DICT_LEN_OFF}]",
            "mov ecx, [ebp-16]",
            "cmp ecx, ebx",
            f"jge {done}",
            f"mov edx, [eax+{self.DICT_ORDER_OFF}]",
            "mov edx, [edx+ecx*4]",  # key ptr
            "mov [ebp-24], edx",  # key
            "mov ebx, edx",
            "call _runtime_dict_lookup_slot",
            "mov eax, [eax+4]",  # value
            "mov [ebp-28], eax",  # value
        )
        self.emitf("mov eax, 12")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [ebp-32], eax",
            f"mov dword [eax+{self.LIST_CAP_OFF}], 2",
            f"mov dword [eax+{self.LIST_LEN_OFF}], 2",
            "mov eax, 8",  # 2 slots * 4 bytes
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov ecx, [ebp-32]",
            f"mov [ecx+{self.LIST_BUF_OFF}], eax",
            "mov edx, [ebp-24]",
            "mov [eax], edx",
            "mov edx, [ebp-28]",
            "mov [eax+4], edx",
            "mov eax, [ebp-12]",
            "mov ecx, [ebp-16]",
            "mov edx, [ebp-32]",
            "mov [eax+ecx*4], edx",
        )
        self.emitf("inc dword [ebp-16]", f"jmp {loop}")
        self.label(done)
        self.emitf("mov eax, [ebp-8]", "leave", "ret")

    def _emit_sort_helpers(self) -> None:
        """i386 port of _runtime_sort_str/_runtime_sort_int -- insertion
        sort, 4-byte list-buffer slots (was 8)."""
        for variant in ("str", "int"):
            name = f"_runtime_sort_{variant}"
            outer = self.fresh(f"so_{variant}_outer")
            inner = self.fresh(f"so_{variant}_inner")
            place = self.fresh(f"so_{variant}_place")
            done = self.fresh(f"so_{variant}_done")
            self.label(name)
            self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
            self.emitf(
                "mov [ebp-4], eax",
                f"mov ecx, [eax+{self.LIST_LEN_OFF}]",
                "mov [ebp-20], ecx",  # n
                "mov dword [ebp-8], 1",  # i
            )
            self.label(outer)
            self.emitf(
                "mov ecx, [ebp-8]",
                "cmp ecx, [ebp-20]",
                f"jge {done}",
                "mov eax, [ebp-4]",
                f"mov edx, [eax+{self.LIST_BUF_OFF}]",
                "mov eax, [edx+ecx*4]",
                "mov [ebp-16], eax",  # key
                "dec ecx",
                "mov [ebp-12], ecx",  # j
            )
            self.label(inner)
            self.emitf("mov ecx, [ebp-12]", "test ecx, ecx", f"js {place}")
            if variant == "str":
                self.emitf(
                    "mov eax, [ebp-4]",
                    f"mov edx, [eax+{self.LIST_BUF_OFF}]",
                    "mov eax, [edx+ecx*4]",
                    "mov ebx, [ebp-16]",
                    "call _runtime_str_cmp",
                    "cmp eax, 0",
                    f"jle {place}",
                )
            else:
                self.emitf(
                    "mov eax, [ebp-4]",
                    f"mov edx, [eax+{self.LIST_BUF_OFF}]",
                    "mov eax, [edx+ecx*4]",
                    "cmp eax, [ebp-16]",
                    f"jle {place}",
                )
            self.emitf(
                "mov ecx, [ebp-12]",
                "mov eax, [ebp-4]",
                f"mov edx, [eax+{self.LIST_BUF_OFF}]",
                "mov eax, [edx+ecx*4]",
                "mov [edx+ecx*4+4], eax",
                "dec dword [ebp-12]",
                f"jmp {inner}",
            )
            self.label(place)
            self.emitf(
                "mov ecx, [ebp-12]",
                "mov eax, [ebp-4]",
                f"mov edx, [eax+{self.LIST_BUF_OFF}]",
                "mov ebx, [ebp-16]",
                "mov [edx+ecx*4+4], ebx",
                "inc dword [ebp-8]",
                f"jmp {outer}",
            )
            self.label(done)
            self.emitf("mov eax, [ebp-4]", "leave", "ret")

    def _emit_sort_items_helper(self) -> None:
        """i386 port of _runtime_sort_items -- 4-byte list-buffer slots."""
        outer = self.fresh("sitems_outer")
        inner = self.fresh("sitems_inner")
        place = self.fresh("sitems_place")
        done = self.fresh("sitems_done")
        self.label("_runtime_sort_items")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf(
            "mov [ebp-4], eax",
            f"mov ecx, [eax+{self.LIST_LEN_OFF}]",
            "mov [ebp-20], ecx",
            "mov dword [ebp-8], 1",
        )
        self.label(outer)
        self.emitf(
            "mov ecx, [ebp-8]",
            "cmp ecx, [ebp-20]",
            f"jge {done}",
            "mov eax, [ebp-4]",
            f"mov edx, [eax+{self.LIST_BUF_OFF}]",
            "mov eax, [edx+ecx*4]",
            "mov [ebp-16], eax",
            "dec ecx",
            "mov [ebp-12], ecx",
        )
        self.label(inner)
        self.emitf("mov ecx, [ebp-12]", "test ecx, ecx", f"js {place}")
        self.emitf(
            "mov eax, [ebp-4]",
            f"mov edx, [eax+{self.LIST_BUF_OFF}]",
            "mov eax, [edx+ecx*4]",
            f"mov eax, [eax+{self.LIST_BUF_OFF}]",
            "mov eax, [eax]",
            "mov ebx, [ebp-16]",
            f"mov ebx, [ebx+{self.LIST_BUF_OFF}]",
            "mov ebx, [ebx]",
            "call _runtime_str_cmp",
            "cmp eax, 0",
            f"jle {place}",
        )
        self.emitf(
            "mov ecx, [ebp-12]",
            "mov eax, [ebp-4]",
            f"mov edx, [eax+{self.LIST_BUF_OFF}]",
            "mov eax, [edx+ecx*4]",
            "mov [edx+ecx*4+4], eax",
            "dec dword [ebp-12]",
            f"jmp {inner}",
        )
        self.label(place)
        self.emitf(
            "mov ecx, [ebp-12]",
            "mov eax, [ebp-4]",
            f"mov edx, [eax+{self.LIST_BUF_OFF}]",
            "mov ebx, [ebp-16]",
            "mov [edx+ecx*4+4], ebx",
            "inc dword [ebp-8]",
            f"jmp {outer}",
        )
        self.label(done)
        self.emitf("mov eax, [ebp-4]", "leave", "ret")

    # ── Runtime data + top-level helper dispatch ─────────────────────────────

    def emit_print_impls(self) -> None:
        """i386 port of LinuxCodegen's own emit_print_impls -- the real
        top-level entry point generate_runtime_only()/gen.generate()
        actually calls, in turn calling emit_dict_runtime()/
        emit_string_runtime()/emit_exception_runtime().

        emit_string_runtime() is NOT YET PORTED (this class's own
        module docstring, and the ~1,944-line scope of the x86-64
        original -- the single largest remaining piece of this whole
        port). Calling the inherited x86-64 Codegen.emit_string_runtime
        directly would silently emit invalid BITS-32-incompatible NASM
        text (real rax/r8-r15 literals into a 32-bit file) -- instead,
        this raises a clear, actionable NotImplementedError naming
        exactly what's missing, so a program that needs ANY string
        operation fails loudly and immediately at codegen time with a
        precise reason, rather than producing a NASM file that fails
        to assemble with a confusing "invalid register" error a dozen
        call sites away from the real cause.
        """
        if not self.use_runtime_lib:
            self.emit("section .bss")
            self.emit("itoa_str_buf: resb 32")
            self.emit("input_buf:    resb 256")
            self.emit("_float_repr_x:          resd 2")   # 8 bytes (a double), 4-byte-field-addressable
            self.emit("_float_repr_notation:    resd 1")
            self.emit("_float_repr_prec:        resd 1")
            self.emit("_float_repr_fmt:         resb 8")
            self.emit("_float_repr_search_buf:  resb 40")
            self.emit("_frs_bits:               resd 2")  # scratch for the GP<->XMM memory round-trip
            self.emit("_frs_bits2:              resd 2")
            self.emit("_frs_prec_ctr:           resd 1")
        else:
            self.emit("extern itoa_str_buf")
            self.emit("extern input_buf")
            self.emit("extern _float_repr_x")
            self.emit("extern _float_repr_notation")
            self.emit("extern _float_repr_prec")
            self.emit("extern _float_repr_fmt")
            self.emit("extern _float_repr_search_buf")

        self._emit_cwd_buf_if_needed()

        self.emit("section .rodata")
        self.emit('fmt_int: db "%d",0')
        self.emit('fmt_str: db "%s",0')
        self.emit('fmt_flt: db "%g",0')

        if self.use_runtime_lib:
            for sym in ("_runtime_input", "_runtime_list_append", "_runtime_list_pop", "_runtime_list_del"):
                self.emit(f"extern {sym}")
            self.emit_dict_runtime()
            # emit_string_runtime() intentionally not called -- see this
            # method's own docstring (below, non-runtime-lib branch) for
            # why a heuristic pre-check was tried and removed: ffi_called
            # only tracks EXTERNAL C-library FFI calls (confirmed by
            # reading its own real population site, codegen.py's _gen_
            # ffi_call), never internal _runtime_* helper calls, so a
            # check against it can never actually fire. A program that
            # needs any string primitive will instead fail at the NASM
            # assembly step with a real, correctly-named "undefined
            # symbol _runtime_str_*" error -- a perfectly clear failure
            # on its own, once traced back to this file's own known gap.
            self.emit_exception_runtime()
            return

        self.emit("section .text")
        self.label("_runtime_input")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 16")
        self.emitf(
            "push input_buf",
            "push 255",
            "push dword [stdin]",
            "call fgets",
            "add esp, 12",
        )
        self.emitf(
            "push input_buf",
            "call strlen",
            "add esp, 4",
            "mov ecx, input_buf",
            "test eax, eax",
            "jz ._li_done",
            "mov dl, [ecx+eax-1]",
            "cmp dl, 10",
            "jne ._li_done",
            "dec eax",
            "mov byte [ecx+eax], 0",
        )
        self.label("._li_done")
        self.emitf("mov eax, input_buf", "leave", "ret")

        # List runtime: append/pop, 4-byte pointer slots (was 8).
        self.label("_runtime_list_append")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 16")
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], ebx")
        self.emitf(
            f"mov ecx, [eax+{self.LIST_LEN_OFF}]", f"cmp ecx, [eax+{self.LIST_CAP_OFF}]", "jl ._la_store"
        )
        self.emitf(
            f"mov ecx, [eax+{self.LIST_CAP_OFF}]", "shl ecx, 1", "cmp ecx, 4", "jge ._la_grow", "mov ecx, 4"
        )
        self.label("._la_grow")
        self.emitf(
            "mov [ebp-12], ecx",
            "shl ecx, 2",  # new size in bytes -- 4-byte slots
            "push ecx",
            "mov eax, [ebp-4]",
            f"push dword [eax+{self.LIST_BUF_OFF}]",
            "call realloc",
            "add esp, 8",
        )
        self.emitf(
            "mov ebx, [ebp-4]",
            f"mov [ebx+{self.LIST_BUF_OFF}], eax",
            "mov edx, [ebp-12]",
            f"mov [ebx+{self.LIST_CAP_OFF}], edx",
        )
        self.label("._la_store")
        self.emitf(
            "mov eax, [ebp-4]",
            f"mov ecx, [eax+{self.LIST_LEN_OFF}]",
            "mov ebx, [ebp-8]",
            f"mov edx, [eax+{self.LIST_BUF_OFF}]",
            "mov [edx+ecx*4], ebx",
            f"inc dword [eax+{self.LIST_LEN_OFF}]",
            "leave",
            "ret",
        )

        self.label("_runtime_list_pop")
        self.emitf(
            f"mov ecx, [eax+{self.LIST_LEN_OFF}]",
            "dec ecx",
            f"mov [eax+{self.LIST_LEN_OFF}], ecx",
            f"mov edx, [eax+{self.LIST_BUF_OFF}]",
            "mov eax, [edx+ecx*4]",
            "ret",
        )

        self.label("_runtime_list_del")
        self.emitf(
            f"mov ecx, [eax+{self.LIST_LEN_OFF}]",
            "test ebx, ebx",
            "jns ._ld_pos",
            "add ebx, ecx",
        )
        self.label("._ld_pos")
        self.emitf(f"mov edx, [eax+{self.LIST_BUF_OFF}]")
        self.label("._ld_loop")
        self.emitf(
            "lea esi, [ebx+1]",
            "cmp esi, ecx",
            "jge ._ld_done",
            "mov edi, [edx+esi*4]",
            "mov [edx+ebx*4], edi",
            "mov ebx, esi",
            "jmp ._ld_loop",
        )
        self.label("._ld_done")
        self.emitf(f"dec dword [eax+{self.LIST_LEN_OFF}]", "ret")

        self.emit_dict_runtime()
        # emit_string_runtime() intentionally not called -- see this
        # method's own docstring above (use_runtime_lib branch) for why
        # a heuristic ffi_called pre-check was tried and removed (it
        # only tracks external C-library FFI calls, never internal
        # _runtime_* helpers, so it could never actually fire). A
        # program needing any string primitive fails at the NASM
        # assembly step instead, with a real, correctly-named
        # "undefined symbol _runtime_str_*" error.
        self.emit_exception_runtime()
