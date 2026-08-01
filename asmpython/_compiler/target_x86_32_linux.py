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
        self._emit_sort_pairs_helpers()
        self._emit_list_extend_helper()
        self._emit_list_repeat_helper()
        self._emit_list_slice_helper()
        self._emit_list_slice_step_helper()
        self._emit_list_slice_assign_helper()
        self._emit_list_reverse_helper()
        self._emit_list_insert_helper()
        self._emit_dict_clear_helper()
        self._emit_dict_pop_helper()
        # The string runtime (_runtime_str_*, _runtime_int_to_base/
        # _int_to_binary/_group_digits*/_divmod/_chr, and the container-
        # repr helpers _runtime_fmt_elem/_list_repr/_dict_repr/_set_repr/
        # _range_list/_str_concat_dup) is now ported -- see
        # emit_string_runtime() below, called from emit_print_impls().
        # _UNPORTED_RUNTIME_HELPERS is kept as a real, honest, visible
        # tracking mechanism (see this class's own module docstring) for
        # anything genuinely still missing; it is empty right now.
        for _sym in self._UNPORTED_RUNTIME_HELPERS:
            self.emit(f"; NOT YET PORTED: {_sym} -- see target_x86_32_linux.py's own docstring")

        self.emit("section .rodata")
        self.emit('_runtime_dict_key_error_msg: db "KeyError: key not in dict",10,0')

    _UNPORTED_RUNTIME_HELPERS: tuple[str, ...] = ()

    def _emit_sort_pairs_helpers(self) -> None:
        """i386 port of the x86-64 original (codegen.py's own
        _emit_sort_pairs_helpers): in-place insertion sort of an "elems"
        list, ordered by a parallel "keys" list of equal length.

        In:  eax = elems list header, ebx = keys list header.
        Out: eax = elems header (sorted; keys is left sorted too).

        4-byte list-buffer slots (this target's own element stride, not
        the x86-64 original's 8) throughout; cdecl _emit_libc_* calls
        replace the direct `call _runtime_str_cmp` (still a bare call --
        _runtime_str_cmp isn't a libc function, it's this target's own
        runtime primitive, so it keeps eax/ebx args unchanged once ported).
        """
        for variant in ("str", "int"):
            name = f"_runtime_sort_pairs_{variant}"
            outer = self.fresh(f"sp_{variant}_outer")
            inner = self.fresh(f"sp_{variant}_inner")
            place = self.fresh(f"sp_{variant}_place")
            done = self.fresh(f"sp_{variant}_done")
            self.label(name)
            self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
            self.emitf(
                "mov [ebp-4], eax",  # elems header
                "mov [ebp-8], ebx",  # keys header
                f"mov ecx, [eax+{self.LIST_LEN_OFF}]",
                "mov [ebp-24], ecx",  # n
                "mov dword [ebp-12], 1",  # i
            )
            self.label(outer)
            self.emitf(
                "mov ecx, [ebp-12]",
                "cmp ecx, [ebp-24]",
                f"jge {done}",
                # key_elem = elems_buf[i]; key_key = keys_buf[i]; j = i - 1
                "mov eax, [ebp-4]",
                f"mov edx, [eax+{self.LIST_BUF_OFF}]",
                "mov eax, [edx+ecx*4]",
                "mov [ebp-20], eax",  # key_elem
                "mov eax, [ebp-8]",
                f"mov edx, [eax+{self.LIST_BUF_OFF}]",
                "mov eax, [edx+ecx*4]",
                "mov [ebp-16], eax",  # key_key
                "dec ecx",
                "mov [ebp-28], ecx",  # j
            )
            self.label(inner)
            self.emitf("mov ecx, [ebp-28]", "test ecx, ecx", f"js {place}")
            if variant == "str":
                self.emitf(
                    "mov eax, [ebp-8]",
                    f"mov edx, [eax+{self.LIST_BUF_OFF}]",
                    "mov eax, [edx+ecx*4]",  # keys_buf[j]
                    "mov ebx, [ebp-16]",  # key_key
                    "call _runtime_str_cmp",
                    "cmp eax, 0",
                    f"jle {place}",
                )
            else:
                self.emitf(
                    "mov eax, [ebp-8]",
                    f"mov edx, [eax+{self.LIST_BUF_OFF}]",
                    "mov eax, [edx+ecx*4]",  # keys_buf[j]
                    "cmp eax, [ebp-16]",
                    f"jle {place}",
                )
            # shift: elems_buf[j+1] = elems_buf[j]; keys_buf[j+1] = keys_buf[j]; j--
            self.emitf(
                "mov ecx, [ebp-28]",
                "mov eax, [ebp-4]",
                f"mov edx, [eax+{self.LIST_BUF_OFF}]",
                "mov eax, [edx+ecx*4]",
                "mov [edx+ecx*4+4], eax",
                "mov eax, [ebp-8]",
                f"mov edx, [eax+{self.LIST_BUF_OFF}]",
                "mov eax, [edx+ecx*4]",
                "mov [edx+ecx*4+4], eax",
                "dec dword [ebp-28]",
                f"jmp {inner}",
            )
            self.label(place)
            self.emitf(
                "mov ecx, [ebp-28]",
                "mov eax, [ebp-4]",
                f"mov edx, [eax+{self.LIST_BUF_OFF}]",
                "mov ebx, [ebp-20]",
                "mov [edx+ecx*4+4], ebx",  # elems_buf[j+1] = key_elem
                "mov eax, [ebp-8]",
                f"mov edx, [eax+{self.LIST_BUF_OFF}]",
                "mov ebx, [ebp-16]",
                "mov [edx+ecx*4+4], ebx",  # keys_buf[j+1] = key_key
                "inc dword [ebp-12]",
                f"jmp {outer}",
            )
            self.label(done)
            self.emitf("mov eax, [ebp-4]", "leave", "ret")

    def _emit_list_extend_helper(self) -> None:
        """`_runtime_list_extend`: dst.extend(src). In: eax=dst, ebx=src.
        Out: eax=dst. i386 port of the x86-64 original -- 4-byte slots."""
        self.label("_runtime_list_extend")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf(
            "mov [ebp-4], eax",  # dst
            "mov [ebp-8], ebx",  # src
            f"mov ecx, [ebx+{self.LIST_LEN_OFF}]",
            "mov [ebp-12], ecx",  # n
            "mov dword [ebp-16], 0",  # i
        )
        loop = self.fresh("lext")
        done = self.fresh("lext_done")
        self.label(loop)
        self.emitf(
            "mov ecx, [ebp-16]",
            "cmp ecx, [ebp-12]",
            f"jge {done}",
            "mov ebx, [ebp-8]",
            f"mov ebx, [ebx+{self.LIST_BUF_OFF}]",
            "mov ebx, [ebx+ecx*4]",  # src element
            "mov eax, [ebp-4]",  # dst header
            "call _runtime_list_append",
            "mov [ebp-4], eax",  # update dst (may have reallocated)
            "inc dword [ebp-16]",
            f"jmp {loop}",
        )
        self.label(done)
        self.emitf("mov eax, [ebp-4]", "leave", "ret")

    def _emit_list_repeat_helper(self) -> None:
        """`_runtime_list_repeat`: [x,y]*n. In: eax=src, ebx=count.
        Out: eax=new list header. i386 port -- LIST_HEADER stays 24
        bytes (structural layout compatibility, see module docstring),
        initial buffer is cap(4)*4 bytes (this target's element stride)."""
        self.label("_runtime_list_repeat")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf(
            "mov [ebp-4], eax",  # src header
            "mov [ebp-8], ebx",  # count
        )
        self._emit_malloc(24)
        self.emitf(
            f"mov dword [eax+{self.LIST_CAP_OFF}], 4",
            f"mov dword [eax+{self.LIST_LEN_OFF}], 0",
            "mov [ebp-12], eax",  # result
        )
        self._emit_malloc(16)  # 4 * 4 = 16 bytes initial buffer
        self.emitf(
            "mov ebx, [ebp-12]",
            f"mov [ebx+{self.LIST_BUF_OFF}], eax",
        )
        self.emitf("mov dword [ebp-16], 0")  # i = 0
        _lrep_top = self.fresh("lrep_top")
        _lrep_done = self.fresh("lrep_done")
        self.label(_lrep_top)
        self.emitf(
            "mov eax, [ebp-16]",
            "cmp eax, [ebp-8]",
            f"jge {_lrep_done}",
            "mov eax, [ebp-12]",  # dst
            "mov ebx, [ebp-4]",  # src
            "call _runtime_list_extend",
            "mov [ebp-12], eax",  # update result (may have reallocated)
            "inc dword [ebp-16]",
            f"jmp {_lrep_top}",
        )
        self.label(_lrep_done)
        self.emitf("mov eax, [ebp-12]", "leave", "ret")

    def _emit_list_slice_helper(self) -> None:
        """`_runtime_list_slice`: xs[start:stop] (no step). In: eax=src,
        ebx=start(sentinel INT32_MIN=omit), ecx=stop(sentinel
        INT32_MAX=omit). Out: eax=new list header. i386 port: sentinels
        narrowed to 32-bit (this target's own int range, see module
        docstring) from the x86-64 original's INT64_MIN/MAX; 4-byte
        element stride throughout."""
        INT32_MIN = "0x80000000"
        INT32_MAX = "0x7fffffff"

        self.label("_runtime_list_slice")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 48")
        self.emitf(
            "mov [ebp-4], eax",  # src header
            "mov [ebp-8], ebx",  # start raw
            "mov [ebp-12], ecx",  # stop raw
        )
        self.emitf(f"mov eax, [eax+{self.LIST_LEN_OFF}]", "mov [ebp-16], eax")
        have_start = self.fresh("ls_start_have")
        s_pos = self.fresh("ls_s_pos")
        s_ge0 = self.fresh("ls_s_ge0")
        s_lel = self.fresh("ls_s_lel")
        self.emitf("mov eax, [ebp-8]", f"cmp eax, {INT32_MIN}", f"jne {have_start}", "xor eax, eax")
        self.label(have_start)
        self.emitf("test eax, eax", f"jns {s_pos}", "add eax, [ebp-16]")
        self.label(s_pos)
        self.emitf("test eax, eax", f"jns {s_ge0}", "xor eax, eax")
        self.label(s_ge0)
        self.emitf("cmp eax, [ebp-16]", f"jle {s_lel}", "mov eax, [ebp-16]")
        self.label(s_lel)
        self.emitf("mov [ebp-20], eax")  # effective start
        have_stop = self.fresh("ls_stop_have")
        t_pos = self.fresh("ls_t_pos")
        t_ge0 = self.fresh("ls_t_ge0")
        t_lel = self.fresh("ls_t_lel")
        self.emitf("mov eax, [ebp-12]", f"cmp eax, {INT32_MAX}", f"jne {have_stop}", "mov eax, [ebp-16]")
        self.label(have_stop)
        self.emitf("test eax, eax", f"jns {t_pos}", "add eax, [ebp-16]")
        self.label(t_pos)
        self.emitf("test eax, eax", f"jns {t_ge0}", "xor eax, eax")
        self.label(t_ge0)
        self.emitf("cmp eax, [ebp-16]", f"jle {t_lel}", "mov eax, [ebp-16]")
        self.label(t_lel)
        self.emitf("mov [ebp-24], eax")  # effective stop
        nle = self.fresh("ls_n_le")
        self.emitf("mov eax, [ebp-24]", "sub eax, [ebp-20]", f"jg {nle}", "xor eax, eax")
        self.label(nle)
        self.emitf("mov [ebp-28], eax")  # n
        cap_ok = self.fresh("ls_cap_ok")
        self.emitf("cmp eax, 4", f"jge {cap_ok}", "mov eax, 4")
        self.label(cap_ok)
        self.emitf("mov [ebp-32], eax")  # cap
        self._emit_malloc(24)
        self.emitf("mov [ebp-36], eax")
        self.emitf(
            "mov edx, [ebp-36]",
            "mov eax, [ebp-32]",
            f"mov [edx+{self.LIST_CAP_OFF}], eax",
            "mov eax, [ebp-28]",
            f"mov [edx+{self.LIST_LEN_OFF}], eax",
        )
        self.emitf("mov eax, [ebp-32]", "shl eax, 2")  # cap * 4 bytes
        self.emitf("push eax", "call malloc", "add esp, 4")
        self.emitf(
            "mov edx, [ebp-36]",
            f"mov [edx+{self.LIST_BUF_OFF}], eax",
        )
        skip_copy = self.fresh("ls_skip_copy")
        self.emitf("mov ecx, [ebp-28]", "test ecx, ecx", f"jz {skip_copy}")
        self.emitf(
            "shl ecx, 2",  # n * 4
            "mov ebx, [ebp-4]",
            f"mov ebx, [ebx+{self.LIST_BUF_OFF}]",
            "mov edx, [ebp-20]",
            "shl edx, 2",
            "add ebx, edx",  # src start ptr
            "mov edx, [ebp-36]",
            f"mov eax, [edx+{self.LIST_BUF_OFF}]",  # dst buf ptr
        )
        self._emit_libc_memcpy()
        self.label(skip_copy)
        self.emitf("mov eax, [ebp-36]", "leave", "ret")

    def _emit_list_slice_step_helper(self) -> None:
        """`_runtime_list_slice_step`: xs[start:stop:step]. In: eax=src,
        ebx=start(sentinel INT32_MIN=omit), ecx=stop(sentinel
        INT32_MAX=omit), edx=step (non-zero). Out: eax=new list header.
        i386 port -- every extra scratch slot the x86-64 original held in
        r8/r9/... lives in [ebp-N] here instead (this target has no
        r8-r15 at all); 4-byte element stride throughout."""
        INT32_MIN = "0x80000000"
        INT32_MAX = "0x7fffffff"
        self.label("_runtime_list_slice_step")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 64")
        self.emitf(
            "mov [ebp-4], eax",
            "mov [ebp-8], ebx",
            "mov [ebp-12], ecx",
            "mov [ebp-16], edx",
        )
        self.emitf(f"mov eax, [eax+{self.LIST_LEN_OFF}]", "mov [ebp-20], eax")  # slen

        # -- Normalize start -----------------------------------------------
        has_start = self.fresh("lss_has_start")
        s_pos = self.fresh("lss_s_pos")
        s_nn = self.fresh("lss_s_nn")
        s_lt = self.fresh("lss_s_lt")
        self.emitf("mov eax, [ebp-8]", f"cmp eax, {INT32_MIN}", f"jne {has_start}")
        # default: step<0 -> len-1, else -> 0
        self.emitf(
            "mov ecx, [ebp-16]",
            "test ecx, ecx",
            f"jns {has_start}",
            "mov eax, [ebp-20]",
            "dec eax",
            f"jmp {s_nn}",
        )
        self.label(has_start)
        self.emitf("test eax, eax", f"jns {s_pos}", "add eax, [ebp-20]")
        self.label(s_pos)
        self.emitf("test eax, eax", f"jns {s_nn}", "xor eax, eax")
        self.label(s_nn)
        self.emitf("mov ecx, [ebp-16]", "test ecx, ecx", f"jns {s_lt}")
        self.emitf("cmp eax, [ebp-20]", f"jl {s_lt}", "mov eax, [ebp-20]", "dec eax")
        self.label(s_lt)
        self.emitf("mov [ebp-24], eax")  # eff_start

        # -- Normalize stop -------------------------------------------------
        has_stop = self.fresh("lss_has_stop")
        t_pos = self.fresh("lss_t_pos")
        t_nn = self.fresh("lss_t_nn")
        t_lt = self.fresh("lss_t_lt")
        t_neg1ok = self.fresh("lss_t_neg1ok")
        self.emitf("mov eax, [ebp-12]", f"cmp eax, {INT32_MAX}", f"jne {has_stop}")
        # default: step<0 -> -1 (exclusive before 0), else -> len
        self.emitf(
            "mov ecx, [ebp-16]",
            "test ecx, ecx",
            f"jns {has_stop}",
            "mov eax, -1",
            f"jmp {t_neg1ok}",
        )
        self.label(has_stop)
        self.emitf("mov ecx, [ebp-16]", "test ecx, ecx", f"jns {t_pos}")
        self.emitf("cmp eax, -1", f"je {t_neg1ok}")
        self.label(t_pos)
        self.emitf("test eax, eax", f"jns {t_nn}", "add eax, [ebp-20]")
        self.label(t_nn)
        self.emitf("test eax, eax", f"jns {t_lt}", "xor eax, eax")
        self.label(t_lt)
        self.emitf("cmp eax, [ebp-20]", f"jle {t_neg1ok}", "mov eax, [ebp-20]")
        self.label(t_neg1ok)
        self.emitf("mov [ebp-28], eax")  # eff_stop

        # -- Count n (pass 1) -----------------------------------------------
        cnt_loop = self.fresh("lss_cnt_loop")
        cnt_done = self.fresh("lss_cnt_done")
        cnt_pos = self.fresh("lss_cnt_pos")
        cnt_neg = self.fresh("lss_cnt_neg")
        cnt_body = self.fresh("lss_cnt_body")
        self.emitf("mov eax, [ebp-24]", "mov [ebp-44], eax")  # i = start
        self.emitf("xor eax, eax", "mov [ebp-32], eax")  # n = 0
        self.label(cnt_loop)
        self.emitf("mov ecx, [ebp-16]", "test ecx, ecx", f"js {cnt_neg}")
        self.label(cnt_pos)
        self.emitf("mov eax, [ebp-44]", "cmp eax, [ebp-28]", f"jge {cnt_done}")
        self.emitf(f"jmp {cnt_body}")
        self.label(cnt_neg)
        self.emitf("mov eax, [ebp-44]", "cmp eax, [ebp-28]", f"jle {cnt_done}")
        self.label(cnt_body)
        self.emitf(
            "inc dword [ebp-32]",
            "mov eax, [ebp-44]",
            "add eax, [ebp-16]",
            "mov [ebp-44], eax",
            f"jmp {cnt_loop}",
        )
        self.label(cnt_done)
        # n = [ebp-32]

        # -- Allocate header + buffer (pass 2) --------------------------------
        cap_ok = self.fresh("lss_cap_ok")
        self.emitf("mov eax, [ebp-32]", "cmp eax, 4", f"jge {cap_ok}", "mov eax, 4")
        self.label(cap_ok)
        self.emitf("mov [ebp-36], eax")  # cap
        self._emit_malloc(24)
        self.emitf("mov [ebp-40], eax")  # hdr
        self.emitf(
            "mov edx, [ebp-40]",
            "mov eax, [ebp-36]",
            f"mov [edx+{self.LIST_CAP_OFF}], eax",
            "mov eax, [ebp-32]",
            f"mov [edx+{self.LIST_LEN_OFF}], eax",
        )
        self.emitf("mov eax, [ebp-36]", "shl eax, 2")  # cap * 4
        self.emitf("push eax", "call malloc", "add esp, 4")
        self.emitf("mov edx, [ebp-40]", f"mov [edx+{self.LIST_BUF_OFF}], eax")

        # -- Fill loop (pass 2) -----------------------------------------------
        fill_loop = self.fresh("lss_fill_loop")
        fill_done = self.fresh("lss_fill_done")
        fill_neg = self.fresh("lss_fill_neg")
        fill_body = self.fresh("lss_fill_body")
        self.emitf("mov eax, [ebp-24]", "mov [ebp-44], eax")  # i = eff_start
        self.emitf("xor eax, eax", "mov [ebp-48], eax")  # out_idx = 0
        self.label(fill_loop)
        self.emitf("mov ecx, [ebp-16]", "test ecx, ecx", f"js {fill_neg}")
        self.emitf("mov eax, [ebp-44]", "cmp eax, [ebp-28]", f"jge {fill_done}")
        self.emitf(f"jmp {fill_body}")
        self.label(fill_neg)
        self.emitf("mov eax, [ebp-44]", "cmp eax, [ebp-28]", f"jle {fill_done}")
        self.label(fill_body)
        self.emitf(
            "mov ebx, [ebp-4]",
            f"mov ebx, [ebx+{self.LIST_BUF_OFF}]",
            "mov ecx, [ebp-44]",
            "mov eax, [ebx+ecx*4]",
            "mov ebx, [ebp-40]",
            f"mov ebx, [ebx+{self.LIST_BUF_OFF}]",
            "mov ecx, [ebp-48]",
            "mov [ebx+ecx*4], eax",
        )
        self.emitf("inc dword [ebp-48]")
        self.emitf("mov eax, [ebp-44]", "add eax, [ebp-16]", "mov [ebp-44], eax")
        self.emitf(f"jmp {fill_loop}")
        self.label(fill_done)
        self.emitf("mov eax, [ebp-40]", "leave", "ret")

    def _emit_list_slice_assign_helper(self) -> None:
        """`_runtime_list_slice_assign`: dst[start:stop] = src (in-place,
        no resize). In: eax=dst, ebx=src, ecx=start(sentinel
        INT32_MIN=0), edx=stop(sentinel INT32_MAX=len(dst)). Out: nothing.
        i386 port -- 4-byte element stride, 32-bit sentinels."""
        INT32_MIN = "0x80000000"
        INT32_MAX = "0x7fffffff"
        self.label("_runtime_list_slice_assign")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 48")
        self.emitf(
            "mov [ebp-4], eax",  # dst
            "mov [ebp-8], ebx",  # src
        )
        self.emitf(f"mov eax, [eax+{self.LIST_LEN_OFF}]", "mov [ebp-16], eax")  # dstlen
        self.emitf("mov eax, [ebp-8]", f"mov eax, [eax+{self.LIST_LEN_OFF}]")  # srclen
        ns_have = self.fresh("lsa_s_have")
        ns_pos = self.fresh("lsa_s_pos")
        ns_ge0 = self.fresh("lsa_s_ge0")
        ns_lel = self.fresh("lsa_s_lel")
        self.emitf(f"cmp ecx, {INT32_MIN}", f"jne {ns_have}", "xor ecx, ecx")
        self.label(ns_have)
        self.emitf("test ecx, ecx", f"jns {ns_pos}", "add ecx, [ebp-16]")
        self.label(ns_pos)
        self.emitf("test ecx, ecx", f"jns {ns_ge0}", "xor ecx, ecx")
        self.label(ns_ge0)
        self.emitf("cmp ecx, [ebp-16]", f"jle {ns_lel}", "mov ecx, [ebp-16]")
        self.label(ns_lel)
        self.emitf("mov [ebp-12], ecx")  # eff_start
        nt_have = self.fresh("lsa_t_have")
        nt_pos = self.fresh("lsa_t_pos")
        nt_ge0 = self.fresh("lsa_t_ge0")
        nt_lel = self.fresh("lsa_t_lel")
        self.emitf(f"cmp edx, {INT32_MAX}", f"jne {nt_have}", "mov edx, [ebp-16]")
        self.label(nt_have)
        self.emitf("test edx, edx", f"jns {nt_pos}", "add edx, [ebp-16]")
        self.label(nt_pos)
        self.emitf("test edx, edx", f"jns {nt_ge0}", "xor edx, edx")
        self.label(nt_ge0)
        self.emitf("cmp edx, [ebp-16]", f"jle {nt_lel}", "mov edx, [ebp-16]")
        self.label(nt_lel)
        self.emitf("sub edx, [ebp-12]")  # stop - start
        lo_lbl = self.fresh("lsa_lo")
        nonneg = self.fresh("lsa_nn")
        self.emitf("test edx, edx", f"jns {nonneg}", "xor edx, edx")
        self.label(nonneg)
        self.emitf("cmp edx, eax", f"jle {lo_lbl}", "mov edx, eax")  # eax=srclen
        self.label(lo_lbl)
        self.emitf("mov [ebp-20], edx")  # count
        self.emitf("xor eax, eax", "mov [ebp-24], eax")  # i = 0
        lp = self.fresh("lsa_loop")
        le = self.fresh("lsa_done")
        self.label(lp)
        self.emitf("mov eax, [ebp-24]", "cmp eax, [ebp-20]", f"jge {le}")
        self.emitf(
            "mov ecx, eax",
            "mov ebx, [ebp-8]",
            f"mov ebx, [ebx+{self.LIST_BUF_OFF}]",
            "mov ebx, [ebx+ecx*4]",  # src.buf[i]
        )
        self.emitf(
            "mov edx, [ebp-12]",  # start
            "add edx, ecx",  # start + i
            "mov ecx, [ebp-4]",
            f"mov ecx, [ecx+{self.LIST_BUF_OFF}]",
            "mov [ecx+edx*4], ebx",
        )
        self.emitf("inc dword [ebp-24]", f"jmp {lp}")
        self.label(le)
        self.emitf("leave", "ret")

    def _emit_list_reverse_helper(self) -> None:
        """`_runtime_list_reverse`: in-place reverse. In: eax=header.
        Out: eax=same header. i386 port -- the two swap-scratch slots
        the x86-64 original held in r8/r9 live in edx/esi here (both
        genuinely free at this point: edx isn't otherwise live, and esi
        is caller's-responsibility-only under cdecl but this whole
        function is a leaf w.r.t. the caller's expectations around it --
        actually saved/restored below since esi IS callee-saved under
        cdecl, unlike the x86-64 original's r8/r9 which are caller-saved
        there too, so this is a real, deliberate difference: esi must be
        pushed/popped here or a live caller value in esi is corrupted)."""
        done = self.fresh("lrev_done")
        loop = self.fresh("lrev_loop")
        self.label("_runtime_list_reverse")
        self.emitf("push ebp", "mov ebp, esp", "push esi", "sub esp, 16")
        self.emitf(
            "mov [ebp-4], eax",
            f"mov ecx, [eax+{self.LIST_LEN_OFF}]",
            "test ecx, ecx",
            f"jz {done}",
            f"mov edx, [eax+{self.LIST_BUF_OFF}]",
            "xor ebx, ebx",  # lo = 0
            "dec ecx",  # hi = len-1
        )
        self.label(loop)
        self.emitf(
            "cmp ebx, ecx",
            f"jge {done}",
            "mov eax, [edx+ebx*4]",
            "mov esi, [edx+ecx*4]",
            "mov [edx+ebx*4], esi",
            "mov [edx+ecx*4], eax",
            "inc ebx",
            "dec ecx",
            f"jmp {loop}",
        )
        self.label(done)
        self.emitf("mov eax, [ebp-4]", "add esp, 16", "pop esi", "leave", "ret")

    def _emit_list_insert_helper(self) -> None:
        """`_runtime_list_insert`: insert value at index i. In: eax=header,
        ebx=index(clipped to [0,len]), ecx=value. Out: eax=same header.
        i386 port -- appends dummy 0 first (grows capacity if needed via
        the already-ported _runtime_list_append), then shifts right, then
        writes. 4-byte element stride."""
        shift_loop = self.fresh("lins_shift")
        shift_done = self.fresh("lins_done")
        nonneg = self.fresh("lins_nonneg")
        clip_ok = self.fresh("lins_clip_ok")
        self.label("_runtime_list_insert")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 16")
        self.emitf(
            "mov [ebp-4], eax",  # header
            "mov [ebp-8], ebx",  # index
            "mov [ebp-12], ecx",  # value
            "xor ebx, ebx",
            "call _runtime_list_append",
        )
        self.emitf(
            "mov eax, [ebp-4]",
            f"mov ecx, [eax+{self.LIST_LEN_OFF}]",
            "dec ecx",  # old_len = new_len - 1
            "mov ebx, [ebp-8]",
            "test ebx, ebx",
            f"jns {nonneg}",
            "xor ebx, ebx",
        )
        self.label(nonneg)
        self.emitf("cmp ebx, ecx", f"jle {clip_ok}", "mov ebx, ecx")
        self.label(clip_ok)
        self.emitf(
            "mov [ebp-8], ebx",  # save clipped index
            f"mov edx, [eax+{self.LIST_BUF_OFF}]",
            "dec ecx",  # i = old_len - 1
        )
        self.label(shift_loop)
        self.emitf(
            "cmp ecx, [ebp-8]",
            f"jl {shift_done}",
            "mov eax, [edx+ecx*4]",
            "mov [edx+ecx*4+4], eax",
            "dec ecx",
            f"jmp {shift_loop}",
        )
        self.label(shift_done)
        self.emitf(
            "mov ecx, [ebp-8]",
            "mov eax, [ebp-12]",
            "mov [edx+ecx*4], eax",
            "mov eax, [ebp-4]",
            "leave",
            "ret",
        )

    def _emit_dict_clear_helper(self) -> None:
        """`_runtime_dict_clear`: remove all entries. In: eax=header.
        Out: eax=same header. The slot ARRAY keeps its 16-byte stride
        (DICT_SLOT_SIZE, unchanged -- see module docstring's own note on
        why DICT_* layout constants stay structurally 8-byte-aligned),
        but each individual slot's key/value FIELDS are 4-byte pointer-
        width on this target (key@+0, value@+4 -- confirmed against
        _runtime_dict_set/_runtime_dict_get's own already-verified
        `[eax+4]` value-field convention above), so only two dwords per
        slot need zeroing, not three -- a slot is 16 bytes of stride but
        only 8 of those bytes are ever read/written as key/value."""
        loop = self.fresh("dcl_loop")
        done = self.fresh("dcl_done")
        self.label("_runtime_dict_clear")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 16")
        self.emitf(
            "mov [ebp-4], eax",
            f"mov dword [eax+{self.DICT_LEN_OFF}], 0",
            f"mov dword [eax+{self.DICT_TOMB_OFF}], 0",
            f"mov ecx, [eax+{self.DICT_CAP_OFF}]",
            f"mov edx, [eax+{self.DICT_BUF_OFF}]",
            "xor ebx, ebx",  # slot index
        )
        self.label(loop)
        self.emitf(
            "cmp ebx, ecx",
            f"jge {done}",
            "mov eax, ebx",
            "shl eax, 4",  # DICT_SLOT_SIZE = 16 bytes (array stride, unchanged)
            "mov dword [edx+eax], 0",  # key field (slot+0)
            "mov dword [edx+eax+4], 0",  # value field (slot+4)
            "inc ebx",
            f"jmp {loop}",
        )
        self.label(done)
        self.emitf("mov eax, [ebp-4]", "leave", "ret")

    def _emit_dict_pop_helper(self) -> None:
        """`_runtime_dict_pop`: remove and return the value for a key.
        In: eax=header, ebx=key ptr. Out: eax=value (or raises KeyError).
        i386 port -- marks tombstone, decrements len, increments
        tombstones, compacts the removed key out of the insertion-order
        array. 4-byte order-array element stride (this target's own
        pointer width) replaces the x86-64 original's *8."""
        found = self.fresh("dpop_found")
        self.label("_runtime_dict_pop")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf(
            "mov [ebp-4], eax",
            "mov [ebp-8], ebx",
            "call _runtime_dict_lookup_slot",
            "test eax, eax",
            f"jnz {found}",
        )
        msg, _ = self.intern_string("KeyError: key not in dict")
        self.emitf(
            f"mov eax, {msg}",
            f"mov ebx, {self._exc_type_id('KeyError')}",
            "call _runtime_raise",
        )
        self.label(found)
        self.emitf(
            "mov ecx, [eax+4]",  # saved value
            "mov [ebp-12], ecx",
            "mov ecx, [eax]",  # key ptr being removed
            "mov [ebp-16], ecx",
            "mov dword [eax], 1",  # key = tombstone
            "mov dword [eax+4], 0",
            "mov edx, [ebp-4]",
            f"mov ecx, [edx+{self.DICT_LEN_OFF}]",
            "mov [ebp-24], ecx",  # old_len
            f"dec dword [edx+{self.DICT_LEN_OFF}]",
            f"inc dword [edx+{self.DICT_TOMB_OFF}]",
            f"mov ecx, [edx+{self.DICT_ORDER_OFF}]",
            "mov [ebp-20], ecx",  # order_buf
            "mov dword [ebp-28], 0",  # i = 0
        )
        find_loop = self.fresh("dpop_find")
        shift_loop = self.fresh("dpop_shift")
        shift_done = self.fresh("dpop_shift_done")
        self.label(find_loop)
        self.emitf(
            "mov eax, [ebp-28]",
            "cmp eax, [ebp-24]",
            f"jge {shift_done}",  # not found (shouldn't happen) -> done
            "mov ecx, [ebp-20]",
            "mov edx, [ebp-16]",
            "cmp [ecx+eax*4], edx",
            f"je {shift_loop}",
            "inc dword [ebp-28]",
            f"jmp {find_loop}",
        )
        self.label(shift_loop)
        self.emitf(
            "mov eax, [ebp-28]",
            "lea eax, [eax+1]",
            "cmp eax, [ebp-24]",
            f"jge {shift_done}",
            "mov ecx, [ebp-20]",
            "mov edx, [ecx+eax*4]",  # order_buf[i+1]
            "mov eax, [ebp-28]",
            "mov [ecx+eax*4], edx",  # order_buf[i] = order_buf[i+1]
            "inc dword [ebp-28]",
            f"jmp {shift_loop}",
        )
        self.label(shift_done)
        self.emitf("mov eax, [ebp-12]", "leave", "ret")

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

    # ── String runtime ────────────────────────────────────────────────────────
    #
    # i386 port of codegen.py's own emit_string_runtime (the x86-64 original,
    # ~3,224 lines) -- string concat/slice/case/strip/split/join/replace/
    # search/classification, plus the container-repr helpers
    # (_runtime_fmt_elem/_runtime_list_repr/_runtime_dict_repr/
    # _runtime_set_repr/_runtime_range_list/_runtime_str_concat_dup) that
    # live inside the same x86-64 method body. Internal ABI unchanged from
    # x86-64 (eax=primary in/out, ebx=2nd, ecx=3rd, edx=4th where a helper
    # genuinely needs one -- see emit_dict_runtime's own docstring for the
    # convention this file established first; _runtime_list_slice_step's
    # already-ported edx-as-4th-arg precedent is followed here for
    # _runtime_str_slice_step too). Every r8-r15 scratch register in the
    # x86-64 original becomes a real [ebp-N] stack slot here (this
    # architecture has no r8-r15 at all) -- never a register rename.
    # String bytes are unaffected by the pointer-width narrowing (module
    # docstring's own point 3): `db`/byte-at-a-time scanning is identical
    # bit-for-bit to the x86-64 version. Labels are referenced bare (e.g.
    # `mov eax, some_label`), matching every other method in this file --
    # NOT `[rel some_label]`, which is an x86-64 `default rel` RIP-relative
    # idiom that plays no role here (this target's generate()/
    # generate_runtime_only() still emit a `default rel` directive
    # unconditionally, inherited from the base class, but it only changes
    # the default addressing mode for a bare `[label]` memory operand,
    # which this file never writes -- every memory reference here is
    # either register-relative ([eax+N]) or a direct absolute symbol used
    # as an immediate, both unaffected by `default rel`).
    def emit_string_runtime(self) -> None:
        if self.use_runtime_lib:
            for sym in (
                "_runtime_str_concat",
                "_runtime_int_to_base",
                "_runtime_int_to_binary",
                "_runtime_group_digits",
                "_runtime_group_digits_zeropad",
                "_runtime_divmod",
                "_runtime_str_repeat",
                "_runtime_str_eq",
                "_runtime_str_cmp",
                "_runtime_str_char_at",
                "_runtime_str_slice",
                "_runtime_str_slice_step",
                "_runtime_str_contains",
                "_runtime_str_index_of",
                "_runtime_str_index_of_start",
                "_runtime_str_rindex_of",
                "_runtime_str_expandtabs",
                "_runtime_str_count",
                "_runtime_str_starts_with",
                "_runtime_str_ends_with",
                "_runtime_str_removeprefix",
                "_runtime_str_removesuffix",
                "_runtime_str_upper",
                "_runtime_str_lower",
                "_runtime_str_capitalize",
                "_runtime_str_swapcase",
                "_runtime_str_title",
                "_runtime_str_strip",
                "_runtime_str_lstrip",
                "_runtime_str_rstrip",
                "_runtime_str_zfill",
                "_runtime_str_ljust",
                "_runtime_str_rjust",
                "_runtime_str_center",
                "_runtime_str_truncate",
                "_runtime_str_replace",
                "_runtime_str_split",
                "_runtime_str_split_ws",
                "_runtime_str_splitlines",
                "_runtime_str_join",
                "_runtime_str_partition",
                "_runtime_str_rpartition",
                "_runtime_str_rsplit",
                "_runtime_chr",
                "_runtime_str_isdigit",
                "_runtime_str_isalpha",
                "_runtime_str_isalnum",
                "_runtime_str_isspace",
                "_runtime_str_isupper",
                "_runtime_str_islower",
                "_runtime_fmt_elem",
                "_runtime_str_concat_dup",
                "_runtime_list_repr",
                "_runtime_dict_repr",
                "_runtime_set_repr",
                "_runtime_range_list",
            ):
                self.emit(f"extern {sym}")
            return
        self.emit("section .rodata")
        self.emit(
            '_runtime_str_to_int_err: db "invalid literal for int() with base 10",0'
        )
        self.emit("section .text")

        # ---- _runtime_str_to_int ---------------------------------------------
        # eax = str ptr -> eax = int32, or raises ValueError if not a valid
        # decimal integer literal. Leading/trailing whitespace is stripped
        # (matching Python's int() semantics). Uses strtoll with an endptr to
        # detect leftover characters after the number (this target's own
        # int-narrowing design decision truncates the strtoll result to 32
        # bits implicitly via the eax return path -- strtoll itself is still
        # the real 64-bit libc function, called via _emit_strtoll_endptr).
        self.label("_runtime_str_to_int")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        _sti_skip_ws = self.fresh("sti_skip_ws")
        _sti_adv_ws = self.fresh("sti_adv_ws")
        _sti_ws_done = self.fresh("sti_ws_done")
        _sti_trail = self.fresh("sti_trail")
        _sti_adv_trail = self.fresh("sti_adv_trail")
        _sti_trail_ok = self.fresh("sti_trail_ok")
        _sti_ok = self.fresh("sti_ok")
        # Skip leading whitespace
        self.label(_sti_skip_ws)
        self.emitf("movzx ecx, byte [eax]")
        self.emitf("cmp ecx, ' '", f"je {_sti_adv_ws}")
        self.emitf("cmp ecx, 9", f"je {_sti_adv_ws}")
        self.emitf("cmp ecx, 10", f"je {_sti_adv_ws}")
        self.emitf("cmp ecx, 13", f"je {_sti_adv_ws}")
        self.emitf(f"jmp {_sti_ws_done}")
        self.label(_sti_adv_ws)
        self.emitf("inc eax", f"jmp {_sti_skip_ws}")
        self.label(_sti_ws_done)
        # Empty or all-whitespace -> raise
        self.emitf("test ecx, ecx")
        _sti_raise = self.fresh("sti_raise")
        self.emitf(f"jz {_sti_raise}")
        # strtoll(eax, &[ebp-16], 10)
        self.emitf("lea ebx, [ebp-16]", "mov ecx, 10")
        self._emit_strtoll_endptr()
        self.emitf("mov [ebp-8], eax")
        # Skip trailing whitespace in endptr
        self.emitf("mov eax, [ebp-16]")
        self.label(_sti_trail)
        self.emitf("movzx ecx, byte [eax]")
        self.emitf("cmp ecx, ' '", f"je {_sti_adv_trail}")
        self.emitf("cmp ecx, 9", f"je {_sti_adv_trail}")
        self.emitf("cmp ecx, 10", f"je {_sti_adv_trail}")
        self.emitf("cmp ecx, 13", f"je {_sti_adv_trail}")
        self.emitf(f"jmp {_sti_trail_ok}")
        self.label(_sti_adv_trail)
        self.emitf("inc eax", f"jmp {_sti_trail}")
        self.label(_sti_trail_ok)
        # *endptr == '\0' -> valid; else raise
        self.emitf("test ecx, ecx", f"jz {_sti_ok}")
        self.label(_sti_raise)
        self.emitf(
            "mov eax, _runtime_str_to_int_err",
            f"mov ebx, {self._exc_type_id('ValueError')}",
            "leave",
            "jmp _runtime_raise",
        )
        self.label(_sti_ok)
        self.emitf("mov eax, [ebp-8]", "leave", "ret")

        # ---- _runtime_str_concat ---------------------------------------------
        # eax = a (str ptr), ebx = b (str ptr) -> eax = newly-allocated concat.
        # Layout of work: strlen(a), strlen(b), malloc(la+lb+1), memcpy each,
        # store NUL.
        self.label("_runtime_str_concat")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        # [ebp-4] = a, [ebp-8] = b, [ebp-12] = la, [ebp-16] = lb, [ebp-20] = new ptr
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], ebx")
        # la = strlen(a)
        self._emit_libc_strlen()
        self.emitf("mov [ebp-12], eax")
        # lb = strlen(b)
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-16], eax")
        # total = la + lb + 1
        self.emitf("mov eax, [ebp-12]", "add eax, [ebp-16]", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-20], eax")
        # memcpy(new, a, la)
        self.emitf(
            "mov eax, eax",  # dst
            "mov ebx, [ebp-4]",  # src
            "mov ecx, [ebp-12]",
        )  # n
        self._emit_libc_memcpy()
        # memcpy(new+la, b, lb)
        self.emitf(
            "mov eax, [ebp-20]",
            "add eax, [ebp-12]",
            "mov ebx, [ebp-8]",
            "mov ecx, [ebp-16]",
        )
        self._emit_libc_memcpy()
        # nul-terminate at new[la+lb]
        self.emitf(
            "mov eax, [ebp-20]",
            "mov ebx, [ebp-12]",
            "add ebx, [ebp-16]",
            "mov byte [eax+ebx], 0",
            "leave",
            "ret",
        )

        # ---- _runtime_int_to_base ----------------------------------------------
        # eax = n (signed int), ebx = base (16, 8, or 2), ecx = prefix string
        # ptr (e.g. "0x", with no sign) -> eax = "0x1a" / "-0x1a"-style string
        # (Python hex()/oct()/bin() semantics). 0 -> prefix + "0".
        digits_label, _ = self.intern_string("0123456789abcdef")
        minus_label, _ = self.intern_string("-")
        self.label("_runtime_int_to_base")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf(
            "mov [ebp-4], eax",  # n
            "mov [ebp-8], ebx",  # base
            "mov [ebp-12], ecx",  # prefix
            "mov dword [ebp-16], 0",  # neg flag (was r15)
        )
        self.emitf(
            "cmp dword [ebp-4], 0",
            "jge ._itb_nonneg",
            "mov dword [ebp-16], 1",
            "neg dword [ebp-4]",
        )
        self.label("._itb_nonneg")
        # 36-byte scratch digit buffer; nul-terminator fixed at offset 35
        # (i386 port: half the x86-64 original's 72 bytes -- a 32-bit int's
        # widest base-2 representation is 32 digits + sign, comfortably
        # under 35, so this is a genuine size reduction consistent with the
        # module docstring's own 32-bit int-narrowing design, not an
        # arbitrary shrink).
        self._emit_malloc(36)
        self.emitf(
            "mov [ebp-20], eax", "add eax, 35", "mov byte [eax], 0", "mov edi, eax"
        )
        self.emitf(
            "mov eax, [ebp-4]", "mov ebx, [ebp-8]", "test eax, eax", "jnz ._itb_loop"
        )
        self.emitf("dec edi", "mov byte [edi], 48", "jmp ._itb_done")  # n == 0 -> "0"
        self.label("._itb_loop")
        self.emitf("test eax, eax", "jz ._itb_done")
        self.emitf("xor edx, edx", "div ebx", "dec edi")
        self.emitf(
            f"mov ecx, {digits_label}",
            "mov dl, [ecx+edx]",
            "mov [edi], dl",
            "jmp ._itb_loop",
        )
        self.label("._itb_done")
        self.emitf("mov [ebp-24], edi")  # digits start (nul-terminated)
        # with_prefix = concat(prefix, digits)
        self.emitf("mov eax, [ebp-12]", "mov ebx, [ebp-24]", "call _runtime_str_concat")
        self.emitf("cmp dword [ebp-16], 0", "jz ._itb_ret")
        self.emitf(
            "mov ebx, eax", f"mov eax, {minus_label}", "call _runtime_str_concat"
        )
        self.label("._itb_ret")
        self.emitf("leave", "ret")

        # ---- _runtime_int_to_binary ---------------------------------------------
        # eax = n (signed int), ebx = min total width (0 = none), ecx = 1 to
        # prepend "0b" else 0 -> eax = binary string for f-string `b`/`#b`
        # format specs, e.g. f"{42:b}" -> "101010", f"{42:#010b}" ->
        # "0b00101010", f"{-5:08b}" -> "-0000101". Zero-padding (from `ebx`)
        # is applied to the digits only, after accounting for the sign and
        # "0b" prefix (matching CPython's width semantics).
        zerob_label, _ = self.intern_string("0b")
        self.label("_runtime_int_to_binary")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 40")
        self.emitf(
            "mov [ebp-4], eax",  # n
            "mov [ebp-8], ebx",  # width
            "mov [ebp-12], ecx",  # prefix flag
            "mov dword [ebp-16], 0",  # neg flag (was r15)
        )
        self.emitf(
            "cmp dword [ebp-4], 0",
            "jge ._itbin_nonneg",
            "mov dword [ebp-16], 1",
            "neg dword [ebp-4]",
        )
        self.label("._itbin_nonneg")
        # 36-byte scratch digit buffer (i386 port: half of the x86-64
        # original's 72 -- see _runtime_int_to_base's own comment for why).
        self._emit_malloc(36)
        self.emitf(
            "mov [ebp-20], eax", "add eax, 35", "mov byte [eax], 0", "mov edi, eax"
        )
        # avail = max(0, width - (neg ? 1 : 0) - (prefix ? 2 : 0)): minimum
        # number of digits to emit (zero-padding the rest).
        self.emitf("mov eax, [ebp-8]", "sub eax, [ebp-16]")
        self.emitf("mov ecx, [ebp-12]", "add ecx, ecx", "sub eax, ecx")
        self.emitf("test eax, eax", "jge ._itbin_avail_ok", "xor eax, eax")
        self.label("._itbin_avail_ok")
        self.emitf("mov [ebp-24], eax")  # avail (remaining min-digit count)
        self.emitf("mov eax, [ebp-4]", "test eax, eax", "jnz ._itbin_loop")
        self.emitf(
            "dec edi", "mov byte [edi], 48", "dec dword [ebp-24]", "jmp ._itbin_pad"
        )
        self.label("._itbin_loop")
        self.emitf("test eax, eax", "jz ._itbin_pad")
        self.emitf("mov edx, eax", "and edx, 1", "shr eax, 1", "add dl, 48")
        self.emitf("dec edi", "mov [edi], dl", "dec dword [ebp-24]", "jmp ._itbin_loop")
        self.label("._itbin_pad")
        self.emitf("cmp dword [ebp-24], 0", "jle ._itbin_digits_done")
        self.label("._itbin_pad_loop")
        self.emitf("dec edi", "mov byte [edi], 48", "dec dword [ebp-24]")
        self.emitf("cmp dword [ebp-24], 0", "jg ._itbin_pad_loop")
        self.label("._itbin_digits_done")
        self.emitf("mov [ebp-28], edi")  # digits start (nul-terminated)
        self.emitf("cmp dword [ebp-12], 0", "je ._itbin_no_prefix")
        self.emitf(
            f"mov eax, {zerob_label}",
            "mov ebx, [ebp-28]",
            "call _runtime_str_concat",
            "jmp ._itbin_have_body",
        )
        self.label("._itbin_no_prefix")
        self.emitf("mov eax, [ebp-28]")
        self.label("._itbin_have_body")
        self.emitf("cmp dword [ebp-16], 0", "jz ._itbin_ret")
        self.emitf(
            "mov ebx, eax", f"mov eax, {minus_label}", "call _runtime_str_concat"
        )
        self.label("._itbin_ret")
        self.emitf("leave", "ret")

        # ---- _runtime_group_digits -----------------------------------------------
        # eax = numeric string ptr, ebx = separator byte (',' or '_') -> eax =
        # newly-allocated string with `sep` inserted every 3 digits in the
        # integer part (PEP 378/515 thousands separators), e.g.
        # "1234567" -> "1,234,567", "-1234567.89" -> "-1,234,567.89". An
        # optional leading '-' is preserved as-is; everything from the first
        # non-digit char onward (a '.' and fraction digits, for floats) is
        # copied verbatim after the grouped integer part. i386 port -- every
        # r8-r11 scratch use in the x86-64 original becomes a real [ebp-N]
        # stack slot here (this architecture has no r8-r15 at all).
        self.label("_runtime_group_digits")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 48")
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], ebx")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-12], eax")  # L
        # sign_len = (src[0] == '-') ? 1 : 0
        self.emitf(
            "mov esi, [ebp-4]",
            "xor ecx, ecx",
            "cmp byte [esi], 45",
            "jne ._gd_no_sign",
            "mov ecx, 1",
        )
        self.label("._gd_no_sign")
        self.emitf("mov [ebp-16], ecx")  # sign_len
        # intpart_len = count of ASCII-digit chars starting at sign_len
        self.emitf("mov edx, ecx", "mov dword [ebp-20], 0")  # was r8 -> [ebp-20]
        self.label("._gd_scan_loop")
        self.emitf(
            "mov al, [esi+edx]",
            "cmp al, 48",
            "jl ._gd_scan_done",
            "cmp al, 57",
            "jg ._gd_scan_done",
        )
        self.emitf("inc dword [ebp-20]", "inc edx", "jmp ._gd_scan_loop")
        self.label("._gd_scan_done")
        self.emitf("mov eax, [ebp-20]", "mov [ebp-24], eax")  # intpart_len
        # num_seps = (intpart_len - 1) // 3, or 0 if intpart_len == 0
        self.emitf("test eax, eax", "jz ._gd_have_seps")
        self.emitf("dec eax", "mov ecx, 3", "xor edx, edx", "div ecx")
        self.label("._gd_have_seps")
        self.emitf("mov [ebp-28], eax")  # num_seps
        # first_group_len = intpart_len - num_seps*3
        self.emitf("mov ecx, eax", "imul ecx, 3", "mov edx, [ebp-24]", "sub edx, ecx")
        self.emitf("mov [ebp-32], edx")  # first_group_len
        # allocate L + num_seps + 1 bytes
        self.emitf("mov eax, [ebp-12]", "add eax, [ebp-28]", "add eax, 1")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-36], eax")  # dst
        # copy sign (if any); init src/dst indices
        self.emitf(
            "xor ecx, ecx",
            "xor edx, edx",
            "cmp dword [ebp-16], 0",
            "je ._gd_no_copy_sign",
        )
        self.emitf(
            "mov esi, [ebp-4]",
            "mov edi, [ebp-36]",
            "mov al, [esi]",
            "mov [edi], al",
            "mov ecx, 1",
            "mov edx, 1",
        )
        self.label("._gd_no_copy_sign")
        self.emitf("mov [ebp-40], ecx", "mov [ebp-44], edx")  # src idx, dst idx
        # write first_group_len digits (was r9 -> [ebp-48])
        self.emitf("mov eax, [ebp-32]", "mov [ebp-48], eax")
        self.label("._gd_first_group_loop")
        self.emitf("cmp dword [ebp-48], 0", "jz ._gd_groups_init")
        self.emitf(
            "mov esi, [ebp-4]",
            "mov edi, [ebp-36]",
            "mov ecx, [ebp-40]",
            "mov edx, [ebp-44]",
        )
        self.emitf("mov al, [esi+ecx]", "mov [edi+edx], al", "inc ecx", "inc edx")
        self.emitf(
            "mov [ebp-40], ecx",
            "mov [ebp-44], edx",
            "dec dword [ebp-48]",
            "jmp ._gd_first_group_loop",
        )
        self.label("._gd_groups_init")
        # was r10 -> [ebp-52]
        self.emitf("mov eax, [ebp-28]", "mov [ebp-52], eax")
        self.label("._gd_groups_loop")
        self.emitf("cmp dword [ebp-52], 0", "jz ._gd_copy_rest")
        # write separator
        self.emitf(
            "mov edi, [ebp-36]",
            "mov edx, [ebp-44]",
            "mov al, [ebp-8]",
            "mov [edi+edx], al",
            "inc edx",
            "mov [ebp-44], edx",
        )
        # write next 3 digits (was r9 -> [ebp-48])
        self.emitf("mov dword [ebp-48], 3")
        self.label("._gd_group3_loop")
        self.emitf("cmp dword [ebp-48], 0", "jz ._gd_group3_done")
        self.emitf(
            "mov esi, [ebp-4]",
            "mov edi, [ebp-36]",
            "mov ecx, [ebp-40]",
            "mov edx, [ebp-44]",
        )
        self.emitf("mov al, [esi+ecx]", "mov [edi+edx], al", "inc ecx", "inc edx")
        self.emitf(
            "mov [ebp-40], ecx", "mov [ebp-44], edx", "dec dword [ebp-48]", "jmp ._gd_group3_loop"
        )
        self.label("._gd_group3_done")
        self.emitf("dec dword [ebp-52]", "jmp ._gd_groups_loop")
        # copy remaining chars (decimal point + fraction, if any) + nul
        self.label("._gd_copy_rest")
        self.emitf(
            "mov esi, [ebp-4]",
            "mov edi, [ebp-36]",
            "mov ecx, [ebp-40]",
            "mov edx, [ebp-44]",
        )
        self.emitf(
            "mov al, [esi+ecx]", "mov [edi+edx], al", "test al, al", "jz ._gd_copy_done"
        )
        self.emitf(
            "inc ecx",
            "inc edx",
            "mov [ebp-40], ecx",
            "mov [ebp-44], edx",
            "jmp ._gd_copy_rest",
        )
        self.label("._gd_copy_done")
        self.emitf("mov eax, [ebp-36]", "leave", "ret")

        # ---- _runtime_group_digits_zeropad ----------------------------------------
        # eax = numeric string ptr (e.g. "1234567" or "-1234567.89"), ebx =
        # target total width, ecx = separator byte -> eax = newly-allocated
        # string: the integer part is zero-padded (on the left) to the
        # smallest digit count `ndigits` such that the *grouped* result
        # reaches at least `width` chars total (matching CPython's
        # zero-pad+grouping combo, e.g. f"{n:015,}" -> "000,001,234,567"),
        # then grouped via _runtime_group_digits. An optional leading '-' and
        # any fractional part (for floats) are preserved/counted but not
        # padded. i386 port -- r8-r11 scratch slots become real [ebp-N] locals.
        self.label("_runtime_group_digits_zeropad")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 48")
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], ebx", "mov [ebp-12], ecx")
        # sign_len = (src[0] == '-') ? 1 : 0
        self.emitf(
            "mov esi, [ebp-4]",
            "xor edx, edx",
            "cmp byte [esi], 45",
            "jne ._gdz_no_sign",
            "mov edx, 1",
        )
        self.label("._gdz_no_sign")
        self.emitf("mov [ebp-16], edx")  # sign_len
        # intpart_len = count of ASCII-digit chars starting at sign_len (was r9)
        self.emitf("mov ecx, edx", "mov dword [ebp-20], 0")
        self.label("._gdz_scan")
        self.emitf(
            "mov al, [esi+ecx]",
            "cmp al, 48",
            "jl ._gdz_scan_done",
            "cmp al, 57",
            "jg ._gdz_scan_done",
            "inc dword [ebp-20]",
            "inc ecx",
            "jmp ._gdz_scan",
        )
        self.label("._gdz_scan_done")
        self.emitf("mov eax, [ebp-20]", "mov [ebp-24], eax")  # intpart_len
        # frac_len = strlen(src) - sign_len - intpart_len
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strlen()
        self.emitf(
            "mov ecx, [ebp-16]",
            "add ecx, [ebp-24]",
            "sub eax, ecx",
            "mov [ebp-28], eax",  # frac_len
        )
        # ndigits = intpart_len; while sign_len+ndigits+(ndigits-1)//3+frac_len
        # < width: ndigits += 1  (ndigits >= 1 always, so ndigits-1 >= 0)
        # (was r10 -> [ebp-32])
        self.emitf("mov eax, [ebp-24]", "mov [ebp-32], eax")
        self.label("._gdz_loop")
        self.emitf(
            "mov eax, [ebp-32]",
            "dec eax",
            "mov ecx, 3",
            "xor edx, edx",
            "div ecx",
            "add eax, [ebp-32]",
            "add eax, [ebp-16]",
            "add eax, [ebp-28]",
            "cmp eax, [ebp-8]",
            "jge ._gdz_loop_done",
            "inc dword [ebp-32]",
            "jmp ._gdz_loop",
        )
        self.label("._gdz_loop_done")
        # pad_count = ndigits - intpart_len
        self.emitf("mov eax, [ebp-32]", "sub eax, [ebp-24]", "mov [ebp-40], eax")
        # allocate sign_len + ndigits + frac_len + 1 bytes
        self.emitf(
            "mov eax, [ebp-16]",
            "add eax, [ebp-32]",
            "add eax, [ebp-28]",
            "add eax, 1",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-44], eax")  # dst (highest slot used; frame is 48, fits)
        # write sign (if any); ecx = dst write index
        self.emitf("xor ecx, ecx", "cmp dword [ebp-16], 0", "je ._gdz_no_sign2")
        self.emitf(
            "mov esi, [ebp-4]",
            "mov edi, [ebp-44]",
            "mov al, [esi]",
            "mov [edi], al",
            "mov ecx, 1",
        )
        self.label("._gdz_no_sign2")
        # write pad_count zero digits (was r9 -> reuse [ebp-20], dead by now)
        self.emitf("mov eax, [ebp-40]", "mov [ebp-20], eax")
        self.label("._gdz_pad_loop")
        self.emitf("cmp dword [ebp-20], 0", "jz ._gdz_pad_done")
        self.emitf(
            "mov edi, [ebp-44]",
            "mov byte [edi+ecx], 48",
            "inc ecx",
            "dec dword [ebp-20]",
            "jmp ._gdz_pad_loop",
        )
        self.label("._gdz_pad_done")
        # copy intpart digits: src[sign_len .. sign_len+intpart_len)
        # (was r9/r11 -> reuse [ebp-20]/[ebp-28]-shadowing is unsafe since
        # frac_len at [ebp-28] is dead by this point in the reference too --
        # confirmed: frac_len's last read was the width-loop above)
        self.emitf("mov eax, [ebp-24]", "mov [ebp-20], eax")  # intpart_len ctr
        self.emitf("mov eax, [ebp-16]", "mov [ebp-28], eax")  # src read idx
        self.label("._gdz_int_loop")
        self.emitf("cmp dword [ebp-20], 0", "jz ._gdz_int_done")
        self.emitf(
            "mov esi, [ebp-4]",
            "mov edi, [ebp-44]",
            "mov edx, [ebp-28]",
            "mov al, [esi+edx]",
            "mov [edi+ecx], al",
            "inc edx",
            "mov [ebp-28], edx",
            "inc ecx",
            "dec dword [ebp-20]",
            "jmp ._gdz_int_loop",
        )
        self.label("._gdz_int_done")
        # copy remaining chars (fraction, if any) + NUL terminator
        self.label("._gdz_frac_loop")
        self.emitf(
            "mov esi, [ebp-4]",
            "mov edi, [ebp-44]",
            "mov edx, [ebp-28]",
            "mov al, [esi+edx]",
            "mov [edi+ecx], al",
            "test al, al",
            "jz ._gdz_frac_done",
            "inc edx",
            "mov [ebp-28], edx",
            "inc ecx",
            "jmp ._gdz_frac_loop",
        )
        self.label("._gdz_frac_done")
        # group the zero-padded digit string
        self.emitf(
            "mov eax, [ebp-44]", "mov ebx, [ebp-12]", "call _runtime_group_digits"
        )
        self.emitf("leave", "ret")

        # ---- _runtime_divmod ---------------------------------------------------
        # eax = a, ebx = b (signed ints) -> eax = 2-tuple (q, r) in the list
        # [cap,len,buf] layout, where q = a // b and r = a % b using Python's
        # floor semantics (mirrors the adjustment in _emit_binop_inline).
        # i386 port -- `cqo`/`idiv rbx` (64-by-64) narrows to `cdq`/`idiv ebx`
        # (32-by-32): this target's own int-narrowing design decision (module
        # docstring) makes this a genuine, not just mechanical, width choice.
        self.label("_runtime_divmod")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf(
            "test ebx, ebx",
            "jnz ._dm_nonzero",
            "mov eax, _runtime_zerodiv_msg",
            f"mov ebx, {self._exc_type_id('ZeroDivisionError')}",
            "call _runtime_raise",
        )
        self.label("._dm_nonzero")
        self.emitf("cdq", "idiv ebx")
        self.emitf(
            "test edx, edx",
            "jz ._dm_done",
            "mov ecx, edx",
            "xor ecx, ebx",
            "test ecx, ecx",
            "jns ._dm_done",
            "dec eax",
            "add edx, ebx",
        )
        self.label("._dm_done")
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], edx")
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            "mov [ebp-12], eax",
            f"mov dword [eax+{self.LIST_CAP_OFF}], 2",
            f"mov dword [eax+{self.LIST_LEN_OFF}], 2",
        )
        self._emit_malloc(8)  # 2 slots * 4-byte pointer width on this target
        self.emitf(
            "mov ecx, [ebp-12]",
            f"mov [ecx+{self.LIST_BUF_OFF}], eax",
            "mov edx, [ebp-4]",
            "mov [eax], edx",
            "mov edx, [ebp-8]",
            "mov [eax+4], edx",
            "mov eax, [ebp-12]",
            "leave",
            "ret",
        )

        # ---- _runtime_str_repeat ---------------------------------------------
        # eax = a (str ptr), ebx = n (int count) -> eax = newly allocated string.
        # Negative or zero n returns an empty string.
        self.label("_runtime_str_repeat")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        # [ebp-4] = a, [ebp-8] = n, [ebp-12] = la, [ebp-16] = new ptr, [ebp-20] = i
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], ebx")
        # If n <= 0, return empty string.
        self.emitf(
            "mov eax, [ebp-8]", "test eax, eax", "jg ._sr_compute_len", "mov eax, 1"
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov byte [eax], 0", "leave", "ret")
        self.label("._sr_compute_len")
        # la = strlen(a)
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-12], eax")
        # total = la * n + 1
        self.emitf("mov eax, [ebp-12]", "mov ebx, [ebp-8]", "imul eax, ebx", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-16], eax", "mov dword [ebp-20], 0")
        self.label("._sr_loop")
        self.emitf("mov eax, [ebp-20]", "cmp eax, [ebp-8]", "jge ._sr_done")
        # dst = new + i * la
        self.emitf(
            "mov eax, [ebp-16]",
            "mov ecx, [ebp-20]",
            "imul ecx, [ebp-12]",
            "add eax, ecx",
            "mov ebx, [ebp-4]",
            "mov ecx, [ebp-12]",
        )
        self._emit_libc_memcpy()
        self.emitf("inc dword [ebp-20]", "jmp ._sr_loop")
        self.label("._sr_done")
        # nul-terminate at new + n*la
        self.emitf(
            "mov eax, [ebp-16]",
            "mov ecx, [ebp-8]",
            "imul ecx, [ebp-12]",
            "mov byte [eax+ecx], 0",
            "leave",
            "ret",
        )

        # ---- _runtime_str_eq -------------------------------------------------
        # eax = a, ebx = b -> eax = 1 if strcmp(a,b)==0 else 0.
        # NULL-safe: None lowers to the 0 slot value, and `x == "lit"` where x
        # is None is ordinary Python (False, not a crash). Both NULL compares
        # equal (None == None); exactly one NULL compares unequal.
        self.label("_runtime_str_eq")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 16")
        self.emitf("test eax, eax", "jnz ._se_a_ok")
        # a == NULL: equal iff b is NULL too.
        self.emitf("test ebx, ebx", "sete al", "movzx eax, al", "leave", "ret")
        self.label("._se_a_ok")
        self.emitf("test ebx, ebx", "jnz ._se_b_ok")
        self.emitf("xor eax, eax", "leave", "ret")
        self.label("._se_b_ok")
        self._emit_libc_strcmp()
        self.emitf("test eax, eax", "sete al", "movzx eax, al", "leave", "ret")

        # ---- _runtime_str_cmp ------------------------------------------------
        # eax = a, ebx = b -> eax = -1/0/+1 (signed compare result).
        self.label("_runtime_str_cmp")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 16")
        self._emit_libc_strcmp()
        # Normalize result to -1/0/+1.
        self.emitf(
            "test eax, eax",
            "jz ._sc_zero",
            "js ._sc_neg",
            "mov eax, 1",
            "jmp ._sc_done",
        )
        self.label("._sc_zero")
        self.emitf("xor eax, eax", "jmp ._sc_done")
        self.label("._sc_neg")
        self.emitf("mov eax, -1")
        self.label("._sc_done")
        self.emitf("leave", "ret")

        # ---- _runtime_str_char_at --------------------------------------------
        # eax = s, ebx = index -> eax = newly-allocated 1-char string.
        # Negative indices count from the end. Out-of-range raises (panic).
        self.label("_runtime_str_char_at")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], ebx")
        # len = strlen(s)
        self._emit_libc_strlen()
        self.emitf("mov [ebp-12], eax")
        # Handle negative index.
        self.emitf(
            "mov eax, [ebp-8]",
            "test eax, eax",
            "jns ._sca_check",
            "add eax, [ebp-12]",
            "mov [ebp-8], eax",
        )
        self.label("._sca_check")
        self.emitf(
            "mov eax, [ebp-8]",
            "test eax, eax",
            "js ._sca_oob",
            "cmp eax, [ebp-12]",
            "jge ._sca_oob",
        )
        # Allocate 2-byte buffer.
        self._emit_malloc(2)
        # buf[0] = s[idx]; buf[1] = 0
        self.emitf(
            "mov ebx, [ebp-4]",
            "mov ecx, [ebp-8]",
            "mov dl, [ebx+ecx]",
            "mov [eax], dl",
            "mov byte [eax+1], 0",
            "leave",
            "ret",
        )
        self.label("._sca_oob")
        self.emitf(
            "mov eax, _runtime_str_oob_msg",
            f"mov ebx, {self._exc_type_id('IndexError')}",
            "call _runtime_raise",
        )
        self.emitf("leave", "ret")  # unreachable

        # ---- _runtime_str_slice ----------------------------------------------
        # eax = s, ebx = start, ecx = stop -> eax = newly-allocated substring.
        # Python semantics: negative indices count from end, clamped to [0, len].
        # Stop < start returns empty.
        self.label("_runtime_str_slice")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf(
            "mov [ebp-4], eax",
            "mov [ebp-8], ebx",  # start
            "mov [ebp-12], ecx",
        )  # stop
        # len = strlen(s)
        self._emit_libc_strlen()
        self.emitf("mov [ebp-16], eax")
        # Normalize start: if negative, add len. Clamp to [0, len].
        self.emitf(
            "mov eax, [ebp-8]",
            "test eax, eax",
            "jns ._sl_start_pos",
            "add eax, [ebp-16]",
        )
        self.label("._sl_start_pos")
        self.emitf("test eax, eax", "jns ._sl_start_ok", "xor eax, eax")
        self.label("._sl_start_ok")
        self.emitf("cmp eax, [ebp-16]", "jle ._sl_start_done", "mov eax, [ebp-16]")
        self.label("._sl_start_done")
        self.emitf("mov [ebp-8], eax")
        # Normalize stop the same way.
        self.emitf(
            "mov eax, [ebp-12]",
            "test eax, eax",
            "jns ._sl_stop_pos",
            "add eax, [ebp-16]",
        )
        self.label("._sl_stop_pos")
        self.emitf("test eax, eax", "jns ._sl_stop_ok", "xor eax, eax")
        self.label("._sl_stop_ok")
        self.emitf("cmp eax, [ebp-16]", "jle ._sl_stop_done", "mov eax, [ebp-16]")
        self.label("._sl_stop_done")
        self.emitf("mov [ebp-12], eax")
        # n = stop - start. If <= 0, return empty.
        self.emitf("mov eax, [ebp-12]", "sub eax, [ebp-8]")
        self.emitf("test eax, eax", "jg ._sl_alloc", "mov eax, 1")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov byte [eax], 0", "leave", "ret")
        self.label("._sl_alloc")
        # Save n in [ebp-20], malloc(n+1).
        self.emitf("mov [ebp-20], eax", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-24], eax")
        # memcpy(new, s+start, n)
        self.emitf(
            "mov eax, [ebp-24]",
            "mov ebx, [ebp-4]",
            "add ebx, [ebp-8]",
            "mov ecx, [ebp-20]",
        )
        self._emit_libc_memcpy()
        # NUL-terminate.
        self.emitf(
            "mov eax, [ebp-24]",
            "mov ecx, [ebp-20]",
            "mov byte [eax+ecx], 0",
            "leave",
            "ret",
        )

        self._emit_str_slice_step_helper()

        # ---- _runtime_str_contains -------------------------------------------
        # eax = haystack, ebx = needle -> eax = 1 if needle is a substring.
        # Uses libc strstr.
        self.label("_runtime_str_contains")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 16")
        self._emit_libc_strstr()
        self.emitf("test eax, eax", "setne al", "movzx eax, al", "leave", "ret")

        # ---- _runtime_str_index_of -------------------------------------------
        # eax = haystack, ebx = needle -> eax = index (or -1 if not found).
        self.label("_runtime_str_index_of")
        self.emitf(
            "push ebp", "mov ebp, esp", "sub esp, 16", "mov [ebp-4], eax"
        )  # save haystack
        self._emit_libc_strstr()
        # eax = pointer in haystack or NULL.
        self.emitf(
            "test eax, eax", "jz ._sio_notfound", "sub eax, [ebp-4]", "leave", "ret"
        )
        self.label("._sio_notfound")
        self.emitf("mov eax, -1", "leave", "ret")

        # ---- _runtime_str_index_of_start ------------------------------------
        # eax = haystack, ebx = needle, ecx = start_pos -> eax = index or -1.
        # Advances haystack by start_pos bytes before calling strstr, then adds
        # start_pos back to the returned offset so the result is absolute.
        self.label("_runtime_str_index_of_start")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 16",
            "mov [ebp-4], eax",  # save original haystack base
            "mov [ebp-8], ecx",  # save start_pos
            "add eax, ecx",  # eax = haystack + start_pos
        )
        self._emit_libc_strstr()
        self.emitf(
            "test eax, eax",
            "jz ._siost_notfound",
            "sub eax, [ebp-4]",  # absolute index from base
            "leave",
            "ret",
        )
        self.label("._siost_notfound")
        self.emitf("mov eax, -1", "leave", "ret")

        # ---- _runtime_str_rindex_of -----------------------------------------
        # eax = haystack, ebx = needle -> eax = last index or -1.
        # Scans forward keeping track of the latest match found.
        self.label("_runtime_str_rindex_of")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 32",
            "mov [ebp-4], eax",  # cursor
            "mov [ebp-8], ebx",  # needle
            "mov dword [ebp-12], -1",  # best = -1
            "mov [ebp-16], eax",  # base
        )
        # nlen = strlen(needle); if 0, return -1
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-20], eax", "test eax, eax", "jz ._srif_done")
        self.label("._srif_loop")
        self.emitf("mov eax, [ebp-4]", "mov ebx, [ebp-8]")
        self._emit_libc_strstr()
        self.emitf(
            "test eax, eax",
            "jz ._srif_done",
            "mov ebx, eax",
            "sub ebx, [ebp-16]",
            "mov [ebp-12], ebx",  # best = current index
            "mov ebx, [ebp-20]",
            "add eax, ebx",  # advance cursor past this match
            "mov [ebp-4], eax",
            "jmp ._srif_loop",
        )
        self.label("._srif_done")
        self.emitf("mov eax, [ebp-12]", "leave", "ret")

        # ---- _runtime_str_expandtabs ----------------------------------------
        # eax = str, ebx = tabsize -> eax = new str with tabs expanded.
        # Scans source; copies non-tab chars; for each \t emits spaces to align
        # to next tabstop (col rounded up to next multiple of tabsize).
        # i386 port -- edi/esi (copy-loop pointers) preserved via push/pop
        # since they're callee-saved under this target's own cdecl
        # convention; the x86-64 original's r12/r13/r14 (output length,
        # current col, out cursor) become real [ebp-N] stack slots (no
        # r8-r15 exist on i386 at all).
        self.label("_runtime_str_expandtabs")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 48",
            "push edi",
            "push esi",
            "mov [ebp-4], eax",  # src ptr
            "mov [ebp-8], ebx",  # tabsize
        )
        # Compute output length: walk src, count spaces needed for tabs
        self.emitf(
            "mov edi, eax",  # edi = src cursor
            "mov dword [ebp-12], 0",  # output length (was r12)
            "mov dword [ebp-16], 0",  # current col (was r13)
        )
        etab_slen_loop = self.fresh("etab_slen")
        etab_slen_end = self.fresh("etab_slen_end")
        etab_slen_tab = self.fresh("etab_slen_tab")
        self.label(etab_slen_loop)
        self.emitf(
            "movzx eax, byte [edi]",
            "test al, al",
            f"jz {etab_slen_end}",
            "cmp al, 9",
            f"je {etab_slen_tab}",
            # normal char
            "inc dword [ebp-12]",
            "inc dword [ebp-16]",
            "inc edi",
            f"jmp {etab_slen_loop}",
        )
        self.label(etab_slen_tab)
        # spaces = tabsize - (col % tabsize); if tabsize==0, drop the tab
        etab_slen_skip = self.fresh("etab_slen_skip")
        self.emitf(
            "mov eax, [ebp-8]",
            "test eax, eax",
            f"jz {etab_slen_skip}",  # tabsize==0: drop tab, continue
        )
        self.emitf(
            "mov edx, 0",
            "mov eax, [ebp-16]",
            "div dword [ebp-8]",
            # edx = col % tabsize; spaces = tabsize - edx
            "mov eax, [ebp-8]",
            "sub eax, edx",
            "add [ebp-12], eax",  # output_len += spaces
            "add [ebp-16], eax",  # col += spaces
        )
        self.label(etab_slen_skip)
        self.emitf("inc edi", f"jmp {etab_slen_loop}")
        self.label(etab_slen_end)
        # malloc(output_len + 1): size in eax for _emit_libc_malloc_size_in_rax
        self.emitf("mov eax, [ebp-12]", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [ebp-20], eax",  # out buffer
            "mov edi, [ebp-4]",  # reset src cursor
            "mov esi, eax",  # out cursor (was r14)
            "mov dword [ebp-16], 0",  # col = 0
        )
        etab_copy_loop = self.fresh("etab_copy")
        etab_copy_end = self.fresh("etab_copy_end")
        etab_copy_tab = self.fresh("etab_copy_tab")
        etab_copy_sp = self.fresh("etab_copy_sp")
        self.label(etab_copy_loop)
        self.emitf(
            "movzx eax, byte [edi]",
            "test al, al",
            f"jz {etab_copy_end}",
            "cmp al, 9",
            f"je {etab_copy_tab}",
            "mov [esi], al",
            "inc esi",
            "inc dword [ebp-16]",
            "inc edi",
            f"jmp {etab_copy_loop}",
        )
        self.label(etab_copy_tab)
        # emit spaces to fill to next tabstop; if tabsize==0, drop the tab
        self.emitf(
            "inc edi",  # advance past \t
            "mov eax, [ebp-8]",
            "test eax, eax",
            f"jz {etab_copy_loop}",
            "mov edx, 0",
            "mov eax, [ebp-16]",
            "div dword [ebp-8]",
            "mov eax, [ebp-8]",
            "sub eax, edx",  # eax = spaces needed
            "mov [ebp-24], eax",  # save count
        )
        self.label(etab_copy_sp)
        self.emitf(
            "cmp dword [ebp-24], 0",
            f"jle {etab_copy_loop}",
            "mov byte [esi], 0x20",
            "inc esi",
            "inc dword [ebp-16]",
            "dec dword [ebp-24]",
            f"jmp {etab_copy_sp}",
        )
        self.label(etab_copy_end)
        self.emitf(
            "mov byte [esi], 0",  # NUL-terminate
            "mov eax, [ebp-20]",
            "pop esi",
            "pop edi",
            "leave",
            "ret",
        )

        # ---- _runtime_str_count ----------------------------------------------
        # eax = haystack, ebx = needle -> eax = non-overlapping occurrence count.
        # Empty needle returns 0 (CPython would return len+1; we simplify).
        self.label("_runtime_str_count")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 32",
            "mov [ebp-4], eax",  # haystack cursor
            "mov [ebp-8], ebx",  # needle
            "mov dword [ebp-12], 0",
        )  # count = 0
        # nlen = strlen(needle); if 0 -> return 0
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-16], eax", "test eax, eax", "jz ._sco_done")
        self.label("._sco_loop")
        self.emitf("mov eax, [ebp-4]", "mov ebx, [ebp-8]")
        self._emit_libc_strstr()
        self.emitf(
            "test eax, eax",
            "jz ._sco_done",
            "inc dword [ebp-12]",
            "add eax, [ebp-16]",
            "mov [ebp-4], eax",
            "jmp ._sco_loop",
        )
        self.label("._sco_done")
        self.emitf("mov eax, [ebp-12]", "leave", "ret")

        # ---- _runtime_str_starts_with ----------------------------------------
        # eax = s, ebx = prefix -> eax = 1 if memcmp(s, prefix, plen) == 0
        # and len(s) >= plen, else 0.
        self.label("_runtime_str_starts_with")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 16",
            "mov [ebp-4], eax",  # s
            "mov [ebp-8], ebx",
        )  # prefix
        # plen = strlen(prefix)
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-12], eax")
        # slen = strlen(s)
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strlen()
        self.emitf("cmp eax, [ebp-12]", "jl ._ssw_no")
        # Compare bytes via a byte-loop (avoids needing memcmp in libc list).
        self.emitf(
            "mov eax, [ebp-4]", "mov ebx, [ebp-8]", "mov ecx, [ebp-12]", "xor edx, edx"
        )
        self.label("._ssw_loop")
        self.emitf(
            "test ecx, ecx",
            "jz ._ssw_yes",
            "mov dl, [eax]",
            "cmp dl, [ebx]",
            "jne ._ssw_no",
            "inc eax",
            "inc ebx",
            "dec ecx",
            "jmp ._ssw_loop",
        )
        self.label("._ssw_yes")
        self.emitf("mov eax, 1", "leave", "ret")
        self.label("._ssw_no")
        self.emitf("xor eax, eax", "leave", "ret")

        # ---- _runtime_str_ends_with ------------------------------------------
        # eax = s, ebx = suffix -> eax = 1 if s ends with suffix else 0.
        self.label("_runtime_str_ends_with")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 16",
            "mov [ebp-4], eax",
            "mov [ebp-8], ebx",
        )
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-12], eax")  # suflen
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strlen()
        self.emitf(
            "mov [ebp-16], eax",  # slen (NOTE: uses the last free slot in a
            # 16-byte-locals frame -- safe here since suflen at [ebp-12] is
            # read again right below before anything could overwrite it)
            "cmp eax, [ebp-12]",
            "jl ._sew_no",
        )
        # offset = slen - suflen
        self.emitf(
            "mov eax, [ebp-16]",
            "sub eax, [ebp-12]",
            "add eax, [ebp-4]",  # s + offset
            "mov ebx, [ebp-8]",
            "mov ecx, [ebp-12]",
            "xor edx, edx",
        )
        self.label("._sew_loop")
        self.emitf(
            "test ecx, ecx",
            "jz ._sew_yes",
            "mov dl, [eax]",
            "cmp dl, [ebx]",
            "jne ._sew_no",
            "inc eax",
            "inc ebx",
            "dec ecx",
            "jmp ._sew_loop",
        )
        self.label("._sew_yes")
        self.emitf("mov eax, 1", "leave", "ret")
        self.label("._sew_no")
        self.emitf("xor eax, eax", "leave", "ret")

        # ---- _runtime_str_removeprefix -----------------------------------------
        # eax = s, ebx = prefix -> eax = s[len(prefix):] if s.startswith(prefix)
        # else a copy of s.
        self.label("_runtime_str_removeprefix")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 16",
            "mov [ebp-4], eax",
            "mov [ebp-8], ebx",
        )
        self.emitf("call _runtime_str_starts_with", "test eax, eax", "jz ._srmp_no")
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-12], eax")  # plen
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strlen()
        self.emitf(
            "mov ecx, eax",
            "mov ebx, [ebp-12]",
            "mov eax, [ebp-4]",
            "call _runtime_str_slice",
            "leave",
            "ret",
        )
        self.label("._srmp_no")
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strdup()
        self.emitf("leave", "ret")

        # ---- _runtime_str_removesuffix -----------------------------------------
        # eax = s, ebx = suffix -> eax = s[:len(s)-len(suffix)] if
        # s.endswith(suffix) else a copy of s.
        self.label("_runtime_str_removesuffix")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 16",
            "mov [ebp-4], eax",
            "mov [ebp-8], ebx",
        )
        self.emitf("call _runtime_str_ends_with", "test eax, eax", "jz ._srms_no")
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-12], eax")  # suflen
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strlen()
        self.emitf(
            "sub eax, [ebp-12]",
            "mov ecx, eax",
            "xor ebx, ebx",
            "mov eax, [ebp-4]",
            "call _runtime_str_slice",
            "leave",
            "ret",
        )
        self.label("._srms_no")
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strdup()
        self.emitf("leave", "ret")

        # ---- _runtime_str_upper ----------------------------------------------
        # eax = s -> eax = newly-allocated upper-case copy. ASCII only.
        # Saves ESI/EDI too: both are callee-saved under this target's cdecl
        # convention (module docstring / _emit_list_reverse_helper's own
        # precedent) and this helper uses both as copy-loop pointers.
        self.label("_runtime_str_upper")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 32",
            "push esi",
            "push edi",
            "mov [ebp-4], eax",
        )
        self._emit_libc_strlen()
        self.emitf(
            "mov [ebp-8], eax",  # len
            "inc eax",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-12], eax")  # dst
        # Copy + transform.
        self.emitf(
            "mov ecx, [ebp-8]", "mov esi, [ebp-4]", "mov edi, [ebp-12]", "xor edx, edx"
        )
        self.label("._sup_loop")
        self.emitf(
            "test ecx, ecx",
            "jz ._sup_done",
            "mov dl, [esi]",
            "cmp dl, 97",  # 'a'
            "jl ._sup_keep",
            "cmp dl, 122",  # 'z'
            "jg ._sup_keep",
            "sub dl, 32",
        )
        self.label("._sup_keep")
        self.emitf("mov [edi], dl", "inc esi", "inc edi", "dec ecx", "jmp ._sup_loop")
        self.label("._sup_done")
        self.emitf(
            "mov byte [edi], 0",
            "mov eax, [ebp-12]",
            "pop edi",
            "pop esi",
            "leave",
            "ret",
        )

        # ---- _runtime_str_lower ----------------------------------------------
        # eax = s -> eax = newly-allocated lower-case copy. ASCII only.
        # Same nonvolatile-register fix as _runtime_str_upper just above.
        self.label("_runtime_str_lower")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 32",
            "push esi",
            "push edi",
            "mov [ebp-4], eax",
        )
        self._emit_libc_strlen()
        self.emitf("mov [ebp-8], eax", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [ebp-12], eax",
            "mov ecx, [ebp-8]",
            "mov esi, [ebp-4]",
            "mov edi, [ebp-12]",
            "xor edx, edx",
        )
        self.label("._slo_loop")
        self.emitf(
            "test ecx, ecx",
            "jz ._slo_done",
            "mov dl, [esi]",
            "cmp dl, 65",  # 'A'
            "jl ._slo_keep",
            "cmp dl, 90",  # 'Z'
            "jg ._slo_keep",
            "add dl, 32",
        )
        self.label("._slo_keep")
        self.emitf("mov [edi], dl", "inc esi", "inc edi", "dec ecx", "jmp ._slo_loop")
        self.label("._slo_done")
        self.emitf(
            "mov byte [edi], 0",
            "mov eax, [ebp-12]",
            "pop edi",
            "pop esi",
            "leave",
            "ret",
        )

        # ---- _runtime_str_capitalize ------------------------------------------
        # eax = s -> eax = newly-allocated copy with the first character
        # upper-cased and every other character lower-cased. ASCII only.
        # i386 port -- the x86-64 original's r8 (index/first-char flag)
        # becomes a real [ebp-N] stack slot (no r8-r15 on i386).
        self.label("_runtime_str_capitalize")
        self.emitf(
            "push ebp", "mov ebp, esp", "sub esp, 32", "push esi", "push edi",
            "mov [ebp-4], eax",
        )
        self._emit_libc_strlen()
        self.emitf("mov [ebp-8], eax", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [ebp-12], eax",
            "mov ecx, [ebp-8]",
            "mov esi, [ebp-4]",
            "mov edi, [ebp-12]",
            "mov dword [ebp-16], 0",  # was r8: index-0 flag
        )
        self.label("._scap_loop")
        self.emitf(
            "test ecx, ecx",
            "jz ._scap_done",
            "mov al, [esi]",
            "cmp dword [ebp-16], 0",
            "jnz ._scap_rest",
        )
        # Index 0: lower-case letter -> upper-case it.
        self.emitf(
            "cmp al, 97",
            "jl ._scap_store",
            "cmp al, 122",
            "jg ._scap_store",
            "sub al, 32",
            "jmp ._scap_store",
        )
        self.label("._scap_rest")
        # Index > 0: upper-case letter -> lower-case it.
        self.emitf(
            "cmp al, 65",
            "jl ._scap_store",
            "cmp al, 90",
            "jg ._scap_store",
            "add al, 32",
        )
        self.label("._scap_store")
        self.emitf(
            "mov [edi], al",
            "inc esi",
            "inc edi",
            "inc dword [ebp-16]",
            "dec ecx",
            "jmp ._scap_loop",
        )
        self.label("._scap_done")
        self.emitf(
            "mov byte [edi], 0", "mov eax, [ebp-12]", "pop edi", "pop esi", "leave", "ret"
        )

        # ---- _runtime_str_swapcase ---------------------------------------------
        # eax = s -> eax = newly-allocated copy with upper/lower case swapped.
        # ASCII only.
        self.label("_runtime_str_swapcase")
        self.emitf(
            "push ebp", "mov ebp, esp", "sub esp, 32", "push esi", "push edi",
            "mov [ebp-4], eax",
        )
        self._emit_libc_strlen()
        self.emitf("mov [ebp-8], eax", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [ebp-12], eax",
            "mov ecx, [ebp-8]",
            "mov esi, [ebp-4]",
            "mov edi, [ebp-12]",
        )
        self.label("._sswap_loop")
        self.emitf("test ecx, ecx", "jz ._sswap_done", "mov al, [esi]")
        self.emitf(
            "cmp al, 97",
            "jl ._sswap_upper",
            "cmp al, 122",
            "jg ._sswap_store",
            "sub al, 32",
            "jmp ._sswap_store",
        )
        self.label("._sswap_upper")
        self.emitf(
            "cmp al, 65",
            "jl ._sswap_store",
            "cmp al, 90",
            "jg ._sswap_store",
            "add al, 32",
        )
        self.label("._sswap_store")
        self.emitf("mov [edi], al", "inc esi", "inc edi", "dec ecx", "jmp ._sswap_loop")
        self.label("._sswap_done")
        self.emitf(
            "mov byte [edi], 0", "mov eax, [ebp-12]", "pop edi", "pop esi", "leave", "ret"
        )

        # ---- _runtime_str_title -------------------------------------------------
        # eax = s -> eax = newly-allocated copy with the first letter of each
        # run of letters upper-cased and the rest lower-cased. A run ends at
        # any non-letter byte (matches CPython's ASCII str.title()). i386
        # port -- the x86-64 original's r9 (in_word flag) becomes a real
        # [ebp-N] stack slot.
        self.label("_runtime_str_title")
        self.emitf(
            "push ebp", "mov ebp, esp", "sub esp, 32", "push esi", "push edi",
            "mov [ebp-4], eax",
        )
        self._emit_libc_strlen()
        self.emitf("mov [ebp-8], eax", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [ebp-12], eax",
            "mov ecx, [ebp-8]",
            "mov esi, [ebp-4]",
            "mov edi, [ebp-12]",
            "mov dword [ebp-16], 0",  # in_word flag (was r9)
        )
        self.label("._stit_loop")
        self.emitf("test ecx, ecx", "jz ._stit_done", "mov al, [esi]")
        self.emitf(
            "cmp al, 97", "jl ._stit_check_upper", "cmp al, 122", "jg ._stit_notalpha"
        )
        # Lower-case letter: start-of-word -> upper-case; mid-word -> keep.
        self.emitf(
            "cmp dword [ebp-16], 0", "jnz ._stit_setword", "sub al, 32", "jmp ._stit_setword"
        )
        self.label("._stit_check_upper")
        self.emitf(
            "cmp al, 65", "jl ._stit_notalpha", "cmp al, 90", "jg ._stit_notalpha"
        )
        # Upper-case letter: start-of-word -> keep; mid-word -> lower-case.
        self.emitf(
            "cmp dword [ebp-16], 0", "jz ._stit_setword", "add al, 32", "jmp ._stit_setword"
        )
        self.label("._stit_notalpha")
        self.emitf("mov dword [ebp-16], 0", "jmp ._stit_store")
        self.label("._stit_setword")
        self.emitf("mov dword [ebp-16], 1")
        self.label("._stit_store")
        self.emitf("mov [edi], al", "inc esi", "inc edi", "dec ecx", "jmp ._stit_loop")
        self.label("._stit_done")
        self.emitf(
            "mov byte [edi], 0", "mov eax, [ebp-12]", "pop edi", "pop esi", "leave", "ret"
        )

        # ---- _runtime_str_lstrip ---------------------------------------------
        # eax = s -> eax = newly-allocated copy with leading ASCII whitespace
        # (space, tab, newline, carriage-return) removed.
        self.label("_runtime_str_lstrip")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32", "mov [ebp-4], eax")
        # Advance start past whitespace.
        self.emitf("mov esi, [ebp-4]")
        self.label("._slst_skip")
        self.emitf(
            "mov dl, [esi]",
            "cmp dl, 32",
            "je ._slst_adv",
            "cmp dl, 9",
            "je ._slst_adv",
            "cmp dl, 10",
            "je ._slst_adv",
            "cmp dl, 13",
            "je ._slst_adv",
            "jmp ._slst_copy",
        )
        self.label("._slst_adv")
        self.emitf("inc esi", "jmp ._slst_skip")
        self.label("._slst_copy")
        self.emitf(
            "mov [ebp-8], esi",  # start ptr
            "mov eax, esi",
        )
        self._emit_libc_strlen()
        self.emitf(
            "mov [ebp-12], eax",  # n
            "inc eax",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-16], eax", "mov ebx, [ebp-8]", "mov ecx, [ebp-12]")
        self._emit_libc_memcpy()
        self.emitf(
            "mov eax, [ebp-16]",
            "mov ecx, [ebp-12]",
            "mov byte [eax+ecx], 0",
            "leave",
            "ret",
        )

        # ---- _runtime_str_rstrip ---------------------------------------------
        # eax = s -> eax = newly-allocated copy with trailing ASCII whitespace
        # removed.
        self.label("_runtime_str_rstrip")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32", "mov [ebp-4], eax")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-8], eax")  # n
        # Walk back from the end while whitespace.
        self.label("._srst_back")
        self.emitf(
            "mov ecx, [ebp-8]",
            "test ecx, ecx",
            "jz ._srst_alloc",
            "mov esi, [ebp-4]",
            "dec ecx",
            "mov dl, [esi+ecx]",
            "cmp dl, 32",
            "je ._srst_dec",
            "cmp dl, 9",
            "je ._srst_dec",
            "cmp dl, 10",
            "je ._srst_dec",
            "cmp dl, 13",
            "je ._srst_dec",
            "jmp ._srst_alloc",
        )
        self.label("._srst_dec")
        self.emitf("mov [ebp-8], ecx", "jmp ._srst_back")
        self.label("._srst_alloc")
        self.emitf("mov eax, [ebp-8]", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-12], eax", "mov ebx, [ebp-4]", "mov ecx, [ebp-8]")
        self._emit_libc_memcpy()
        self.emitf(
            "mov eax, [ebp-12]",
            "mov ecx, [ebp-8]",
            "mov byte [eax+ecx], 0",
            "leave",
            "ret",
        )

        # ---- _runtime_str_strip ----------------------------------------------
        # eax = s -> lstrip(rstrip(s)). Two passes; allocates twice.
        self.label("_runtime_str_strip")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 16",
            "call _runtime_str_rstrip",
            "call _runtime_str_lstrip",
            "leave",
            "ret",
        )

        # ---- _runtime_str_zfill -----------------------------------------------
        # eax = s, ebx = width -> eax = newly-allocated copy of s left-padded
        # with '0' to at least `width` bytes. A leading '+'/'-' sign (if
        # present) stays first; zeros are inserted after it. i386 port --
        # the x86-64 original's r8/r9 (sign-present flag, pad count) become
        # real [ebp-N] stack slots.
        self.label("_runtime_str_zfill")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 32",
            "push esi",
            "push edi",
            "mov [ebp-4], eax",
            "mov [ebp-8], ebx",
        )
        self._emit_libc_strlen()
        self.emitf("mov [ebp-12], eax")  # len
        self.emitf(
            "mov ecx, [ebp-8]",  # width
            "cmp ecx, eax",
            "jge ._szf_tot_done",
            "mov ecx, eax",
        )  # total = len (width < len)
        self.label("._szf_tot_done")
        self.emitf("mov [ebp-16], ecx")  # total
        self.emitf("mov eax, ecx", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-20], eax")  # dst
        self.emitf(
            "mov esi, [ebp-4]",
            "mov edi, [ebp-20]",
            "mov dword [ebp-24], 0",  # sign-present flag (was r8)
            "mov al, [esi]",
            "cmp al, 43",  # '+'
            "je ._szf_sign",
            "cmp al, 45",  # '-'
            "jne ._szf_nosign",
        )
        self.label("._szf_sign")
        self.emitf("mov [edi], al", "inc esi", "inc edi", "mov dword [ebp-24], 1")
        self.label("._szf_nosign")
        self.emitf("mov ecx, [ebp-16]", "sub ecx, [ebp-12]", "mov [ebp-28], ecx")  # pad (was r9)
        self.label("._szf_padloop")
        self.emitf(
            "cmp dword [ebp-28], 0",
            "jz ._szf_copyrest",
            "mov byte [edi], 48",
            "inc edi",
            "dec dword [ebp-28]",
            "jmp ._szf_padloop",
        )
        self.label("._szf_copyrest")
        self.emitf("mov ecx, [ebp-12]", "sub ecx, [ebp-24]")  # remaining = len - sign
        self.label("._szf_cploop")
        self.emitf(
            "test ecx, ecx",
            "jz ._szf_done",
            "mov al, [esi]",
            "mov [edi], al",
            "inc esi",
            "inc edi",
            "dec ecx",
            "jmp ._szf_cploop",
        )
        self.label("._szf_done")
        self.emitf(
            "mov byte [edi], 0", "mov eax, [ebp-20]", "pop edi", "pop esi", "leave", "ret"
        )

        # ---- _runtime_str_ljust ------------------------------------------------
        # eax = s, ebx = width, ecx = fill byte -> eax = newly-allocated copy of
        # s, right-padded with the fill byte to at least `width` bytes.
        self.label("_runtime_str_ljust")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 32",
            "push esi",
            "push edi",
            "mov [ebp-4], eax",
            "mov [ebp-8], ebx",
            "mov [ebp-12], ecx",
        )
        self._emit_libc_strlen()
        self.emitf("mov [ebp-16], eax")  # len
        self.emitf(
            "mov ecx, [ebp-8]",
            "cmp ecx, eax",
            "jge ._slj_tot_done",
            "mov ecx, eax",
        )
        self.label("._slj_tot_done")
        self.emitf("mov [ebp-20], ecx")  # total
        self.emitf("mov eax, ecx", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-24], eax")  # dst
        self.emitf("mov esi, [ebp-4]", "mov edi, [ebp-24]", "mov ecx, [ebp-16]")
        self.label("._slj_cp")
        self.emitf(
            "test ecx, ecx",
            "jz ._slj_pad",
            "mov al, [esi]",
            "mov [edi], al",
            "inc esi",
            "inc edi",
            "dec ecx",
            "jmp ._slj_cp",
        )
        self.label("._slj_pad")
        self.emitf("mov ecx, [ebp-20]", "sub ecx, [ebp-16]", "mov al, [ebp-12]")
        self.label("._slj_padloop")
        self.emitf(
            "test ecx, ecx",
            "jz ._slj_done",
            "mov [edi], al",
            "inc edi",
            "dec ecx",
            "jmp ._slj_padloop",
        )
        self.label("._slj_done")
        self.emitf(
            "mov byte [edi], 0", "mov eax, [ebp-24]", "pop edi", "pop esi", "leave", "ret"
        )

        # ---- _runtime_str_rjust ------------------------------------------------
        # eax = s, ebx = width, ecx = fill byte -> eax = newly-allocated copy of
        # s, left-padded with the fill byte to at least `width` bytes.
        self.label("_runtime_str_rjust")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 32",
            "push esi",
            "push edi",
            "mov [ebp-4], eax",
            "mov [ebp-8], ebx",
            "mov [ebp-12], ecx",
        )
        self._emit_libc_strlen()
        self.emitf("mov [ebp-16], eax")  # len
        self.emitf(
            "mov ecx, [ebp-8]",
            "cmp ecx, eax",
            "jge ._srj_tot_done",
            "mov ecx, eax",
        )
        self.label("._srj_tot_done")
        self.emitf("mov [ebp-20], ecx")  # total
        self.emitf("mov eax, ecx", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-24], eax")  # dst
        self.emitf(
            "mov edi, [ebp-24]",
            "mov ecx, [ebp-20]",
            "sub ecx, [ebp-16]",
            "mov al, [ebp-12]",
        )
        self.label("._srj_padloop")
        self.emitf(
            "test ecx, ecx",
            "jz ._srj_cp",
            "mov [edi], al",
            "inc edi",
            "dec ecx",
            "jmp ._srj_padloop",
        )
        self.label("._srj_cp")
        self.emitf("mov esi, [ebp-4]", "mov ecx, [ebp-16]")
        self.label("._srj_cploop")
        self.emitf(
            "test ecx, ecx",
            "jz ._srj_done",
            "mov al, [esi]",
            "mov [edi], al",
            "inc esi",
            "inc edi",
            "dec ecx",
            "jmp ._srj_cploop",
        )
        self.label("._srj_done")
        self.emitf(
            "mov byte [edi], 0", "mov eax, [ebp-24]", "pop edi", "pop esi", "leave", "ret"
        )

        # ---- _runtime_str_center -----------------------------------------------
        # eax = s, ebx = width, ecx = fill byte -> eax = newly-allocated copy of
        # s, centered within `width` bytes using the fill byte. Matches
        # CPython's split: left = marg/2 + (marg & width & 1), right = marg-left.
        self.label("_runtime_str_center")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 48",
            "push esi",
            "push edi",
            "mov [ebp-4], eax",
            "mov [ebp-8], ebx",
            "mov [ebp-12], ecx",
        )
        self._emit_libc_strlen()
        self.emitf("mov [ebp-16], eax")  # len
        self.emitf(
            "mov ecx, [ebp-8]",
            "cmp ecx, eax",
            "jge ._scn_tot_done",
            "mov ecx, eax",
        )
        self.label("._scn_tot_done")
        self.emitf("mov [ebp-20], ecx")  # total
        self.emitf("mov eax, ecx", "sub eax, [ebp-16]", "mov [ebp-24], eax")  # marg
        self.emitf(
            "mov eax, [ebp-24]",
            "shr eax, 1",
            "mov edx, [ebp-24]",
            "and edx, [ebp-8]",
            "and edx, 1",
            "add eax, edx",
            "mov [ebp-28], eax",  # left
        )
        self.emitf(
            "mov eax, [ebp-24]", "sub eax, [ebp-28]", "mov [ebp-32], eax"
        )  # right
        self.emitf("mov eax, [ebp-20]", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-36], eax")  # dst
        self.emitf("mov edi, [ebp-36]", "mov al, [ebp-12]", "mov ecx, [ebp-28]")
        self.label("._scn_lpad")
        self.emitf(
            "test ecx, ecx",
            "jz ._scn_cp",
            "mov [edi], al",
            "inc edi",
            "dec ecx",
            "jmp ._scn_lpad",
        )
        self.label("._scn_cp")
        self.emitf("mov esi, [ebp-4]", "mov ecx, [ebp-16]")
        self.label("._scn_cploop")
        self.emitf(
            "test ecx, ecx",
            "jz ._scn_rpad",
            "mov al, [esi]",
            "mov [edi], al",
            "inc esi",
            "inc edi",
            "dec ecx",
            "jmp ._scn_cploop",
        )
        self.label("._scn_rpad")
        self.emitf("mov al, [ebp-12]", "mov ecx, [ebp-32]")
        self.label("._scn_rpadloop")
        self.emitf(
            "test ecx, ecx",
            "jz ._scn_done",
            "mov [edi], al",
            "inc edi",
            "dec ecx",
            "jmp ._scn_rpadloop",
        )
        self.label("._scn_done")
        self.emitf(
            "mov byte [edi], 0", "mov eax, [ebp-36]", "pop edi", "pop esi", "leave", "ret"
        )

        # ---- _runtime_str_truncate ----------------------------------------------
        # eax = s, ebx = max length n -> eax = newly-allocated copy of s,
        # truncated to min(len(s), n) bytes (for f-string `.precision` on str).
        self.label("_runtime_str_truncate")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 32",
            "push esi",
            "push edi",
            "mov [ebp-4], eax",
            "mov [ebp-8], ebx",
        )
        self._emit_libc_strlen()
        self.emitf(
            "mov ecx, [ebp-8]", "cmp ecx, eax", "jle ._strn_tot_done", "mov ecx, eax"
        )
        self.label("._strn_tot_done")
        self.emitf("mov [ebp-12], ecx")  # copy_len
        self.emitf("mov eax, ecx", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-16], eax")  # dst
        self.emitf("mov esi, [ebp-4]", "mov edi, [ebp-16]", "mov ecx, [ebp-12]")
        self.label("._strn_cp")
        self.emitf(
            "test ecx, ecx",
            "jz ._strn_done",
            "mov al, [esi]",
            "mov [edi], al",
            "inc esi",
            "inc edi",
            "dec ecx",
            "jmp ._strn_cp",
        )
        self.label("._strn_done")
        self.emitf(
            "mov byte [edi], 0", "mov eax, [ebp-16]", "pop edi", "pop esi", "leave", "ret"
        )

        # ---- _runtime_str_replace --------------------------------------------
        # eax = s, ebx = old, ecx = new -> eax = newly-allocated copy of s with
        # every non-overlapping occurrence of `old` replaced by `new`. Empty
        # `old` returns a duplicate of s (no replacement). i386 port -- the
        # x86-64 original's r12/r13 (src/dst walk cursors) become real
        # [ebp-N] stack slots here (no r8-r15 on i386), matching the
        # original's own comment about why it avoided plain esi/edi there
        # (Win64 nonvolatile-register concerns don't apply to THIS target's
        # cdecl convention, but using stack slots throughout keeps this
        # method simple and consistent with the rest of this port).
        self.label("_runtime_str_replace")
        self.emitf(
            "push ebp",
            "mov ebp, esp",
            "sub esp, 64",
            "mov [ebp-4], eax",  # s
            "mov [ebp-8], ebx",  # old
            "mov [ebp-12], ecx",
        )  # new
        # Lengths: slen, olen, nlen.
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-16], eax")
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-20], eax", "test eax, eax", "jz ._srep_dup")
        self.emitf("mov eax, [ebp-12]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-24], eax")
        # Count occurrences.
        self.emitf(
            "mov eax, [ebp-4]",
            "mov ebx, [ebp-8]",
            "call _runtime_str_count",
            "mov [ebp-28], eax",
        )  # cnt
        # outlen = slen + cnt * (nlen - olen)
        self.emitf(
            "mov eax, [ebp-24]",
            "sub eax, [ebp-20]",
            "imul eax, [ebp-28]",
            "add eax, [ebp-16]",
            "mov [ebp-32], eax",  # outlen
            "inc eax",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-36], eax")  # out ptr
        # Walk: src cursor at [ebp-40], dst cursor at [ebp-44] (the x86-64
        # original keeps these in r12/r13; here they're plain stack slots).
        self.emitf("mov eax, [ebp-4]", "mov [ebp-40], eax")  # src
        self.emitf("mov eax, [ebp-36]", "mov [ebp-44], eax")  # dst
        self.label("._srep_loop")
        # Find next occurrence at-or-after src.
        self.emitf("mov eax, [ebp-40]", "mov ebx, [ebp-8]")
        self._emit_libc_strstr()
        self.emitf("test eax, eax", "jz ._srep_tail")
        # Save match ptr; compute chunk_len = match - src; copy chunk to dst.
        self.emitf("mov [ebp-48], eax")  # match ptr
        self.emitf("sub eax, [ebp-40]")
        # Skip the memcpy if the chunk is empty (calling memcpy with size 0
        # is fine but we avoid the call overhead).
        self.emitf("test eax, eax", "jz ._srep_no_chunk")
        # memcpy(dst, src, chunk_len)
        self.emitf(
            "mov ecx, eax",  # ecx = chunk_len
            "mov ebx, [ebp-40]",  # ebx = src
            "mov eax, [ebp-44]",
        )  # eax = dst
        self._emit_libc_memcpy()
        # Advance dst by chunk_len. memcpy clobbered scratch regs, so re-derive
        # chunk_len from (match - src) using the stable slots.
        self.emitf(
            "mov eax, [ebp-48]",
            "sub eax, [ebp-40]",
            "add eax, [ebp-44]",
            "mov [ebp-44], eax",
        )
        self.label("._srep_no_chunk")
        # Append `new` to dst.
        self.emitf("mov eax, [ebp-44]", "mov ebx, [ebp-12]", "mov ecx, [ebp-24]")
        self._emit_libc_memcpy()
        # Advance dst by nlen; advance src to match + olen.
        self.emitf(
            "mov eax, [ebp-44]",
            "add eax, [ebp-24]",
            "mov [ebp-44], eax",
            "mov eax, [ebp-48]",
            "add eax, [ebp-20]",
            "mov [ebp-40], eax",
            "jmp ._srep_loop",
        )
        self.label("._srep_tail")
        # Copy remaining tail bytes from src to dst.
        self.emitf(
            "mov eax, [ebp-36]",
            "add eax, [ebp-32]",  # = out + outlen
            "sub eax, [ebp-44]",
        )  # eax = tail_len
        self.emitf("test eax, eax", "jz ._srep_term")
        self.emitf("mov ecx, eax", "mov ebx, [ebp-40]", "mov eax, [ebp-44]")
        self._emit_libc_memcpy()
        self.label("._srep_term")
        self.emitf(
            "mov eax, [ebp-36]",
            "mov ecx, [ebp-32]",
            "mov byte [eax+ecx], 0",
            "leave",
            "ret",
        )
        self.label("._srep_dup")
        # Empty old: just strdup the input.
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strdup()
        self.emitf("leave", "ret")

        self._emit_str_split_helper()
        self._emit_str_split_ws_helper()
        self._emit_str_join_helper()
        self._emit_str_splitlines_helper()
        self._emit_str_partition_helper()
        self._emit_str_rpartition_helper()
        self._emit_str_rsplit_helper()
        self._emit_chr_helper()
        self._emit_str_predicate_helpers()
        self._emit_list_repr_helper32()

        self.emit("section .rodata")
        self.emit('_runtime_str_oob_msg: db "string index out of range",0')
        self.emit('_runtime_list_oob_msg: db "list index out of range",0')
        # CPython (3.13+) uses the same message "division by zero" for all
        # of int //, int %, float /, float //, float % and divmod().
        self.emit('_runtime_zerodiv_msg: db "division by zero",0')
        self.emit("_runtime_nl_str: db 10,0")  # "\n" for splitlines
        self.emit("_runtime_empty_str: db 0")  # "" for partition's not-found arms
        self.emit('_runtime_lbrack_str: db "[",0')
        self.emit('_runtime_rbrack_str: db "]",0')
        self.emit('_runtime_comma_str: db ", ",0')
        self.emit("_runtime_quote_str: db 39,0")  # single quote for str elements
        self.emit('_runtime_lbrace_str: db "{",0')
        self.emit('_runtime_rbrace_str: db "}",0')
        self.emit('_runtime_colon_str: db ": ",0')
        self.emit('_runtime_emptyset_str: db "set()",0')
        self.emit('_runtime_lparen_str: db "(",0')
        self.emit('_runtime_rparen_str: db ")",0')
        self.emit('_runtime_comma_rparen_str: db ",)",0')
        self.emit('_runtime_true_str: db "True",0')
        self.emit('_runtime_false_str: db "False",0')
        self.emit('_runtime_none_str: db "None",0')

    def _emit_str_slice_step_helper(self) -> None:
        """`_runtime_str_slice_step`: full s[start:stop:step].

        In: eax = s, ebx = start, ecx = stop, edx = step. Caller passes
        sentinels for missing endpoints: start missing -> INT32_MIN
        (0x80000000); stop missing -> INT32_MIN when step < 0, INT32_MAX
        otherwise. Out: eax = newly-allocated substring.

        i386 port -- the x86-64 original's 4th argument (step) lives in r8;
        this target has no r8, so step is passed in edx instead, following
        the exact precedent _runtime_list_slice_step already established in
        this same file (its own docstring: "In: eax=src, ebx=start,
        ecx=stop, edx=step"). INT64_MIN narrows to INT32_MIN (module
        docstring's own int-range design decision). Step must be non-zero;
        we don't raise on step=0, matching the x86-64 original's own
        comment (sema rejects literal 0; runtime 0 falls through and
        returns an empty string).
        """
        INT32_MIN = "0x80000000"

        self.label("_runtime_str_slice_step")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 48")
        self.emitf(
            "mov [ebp-4], eax",  # s
            "mov [ebp-8], ebx",  # start (raw)
            "mov [ebp-12], ecx",  # stop  (raw)
            "mov [ebp-16], edx",
        )  # step
        # len = strlen(s)
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-20], eax")  # len

        # Normalize start.
        # if step > 0: default = 0, clamp to [0, len]
        # if step < 0: default = len - 1, clamp to [-1, len - 1]
        step_pos = self.fresh("ssl_step_pos")
        step_neg = self.fresh("ssl_step_neg")
        start_done = self.fresh("ssl_start_done")
        self.emitf(
            "mov eax, [ebp-16]", "test eax, eax", f"jg {step_pos}", f"jmp {step_neg}"
        )

        self.label(step_pos)
        # start (missing sentinel -> 0; else normalize + clamp to [0, len])
        self.emitf("mov eax, [ebp-8]", f"cmp eax, {INT32_MIN}")
        sp_have = self.fresh("ssl_sp_have")
        self.emitf(f"jne {sp_have}", "xor eax, eax")  # default 0
        self.label(sp_have)
        sp_pos = self.fresh("ssl_sp_pos")
        self.emitf("test eax, eax", f"jns {sp_pos}", "add eax, [ebp-20]")
        self.label(sp_pos)
        self.emitf("test eax, eax")
        sp_ge0 = self.fresh("ssl_sp_ge0")
        self.emitf(f"jns {sp_ge0}", "xor eax, eax")
        self.label(sp_ge0)
        self.emitf("cmp eax, [ebp-20]")
        sp_lel = self.fresh("ssl_sp_lel")
        self.emitf(f"jle {sp_lel}", "mov eax, [ebp-20]")
        self.label(sp_lel)
        self.emitf("mov [ebp-24], eax")  # effective start
        # stop (missing sentinel INT32_MIN -> len; else normalize + clamp)
        self.emitf("mov eax, [ebp-12]", f"cmp eax, {INT32_MIN}")
        st_have_p = self.fresh("ssl_st_have_p")
        st_have_p_done = self.fresh("ssl_st_have_p_done")
        self.emitf(f"jne {st_have_p}", "mov eax, [ebp-20]", f"jmp {st_have_p_done}")
        self.label(st_have_p)
        st_pos = self.fresh("ssl_st_pos")
        self.emitf("test eax, eax", f"jns {st_pos}", "add eax, [ebp-20]")
        self.label(st_pos)
        self.emitf("test eax, eax")
        st_ge0 = self.fresh("ssl_st_ge0")
        self.emitf(f"jns {st_ge0}", "xor eax, eax")
        self.label(st_ge0)
        self.emitf("cmp eax, [ebp-20]")
        st_lel = self.fresh("ssl_st_lel")
        self.emitf(f"jle {st_lel}", "mov eax, [ebp-20]")
        self.label(st_lel)
        self.label(st_have_p_done)
        self.emitf("mov [ebp-28], eax")  # effective stop
        self.emitf(f"jmp {start_done}")

        self.label(step_neg)
        # start: missing -> len - 1
        self.emitf("mov eax, [ebp-8]", f"cmp eax, {INT32_MIN}")
        sn_have = self.fresh("ssl_sn_have")
        self.emitf(f"jne {sn_have}", "mov eax, [ebp-20]", "dec eax")
        self.label(sn_have)
        sn_pos = self.fresh("ssl_sn_pos")
        self.emitf("test eax, eax", f"jns {sn_pos}", "add eax, [ebp-20]")
        self.label(sn_pos)
        # Clamp to [-1, len-1]
        self.emitf("cmp eax, -1")
        sn_gem1 = self.fresh("ssl_sn_gem1")
        self.emitf(f"jge {sn_gem1}", "mov eax, -1")
        self.label(sn_gem1)
        self.emitf("mov ebx, [ebp-20]", "dec ebx", "cmp eax, ebx")
        sn_lel = self.fresh("ssl_sn_lel")
        self.emitf(f"jle {sn_lel}", "mov eax, ebx")
        self.label(sn_lel)
        self.emitf("mov [ebp-24], eax")
        # stop: missing -> -1
        self.emitf("mov eax, [ebp-12]", f"cmp eax, {INT32_MIN}")
        tn_have = self.fresh("ssl_tn_have")
        tn_have_done = self.fresh("ssl_tn_have_done")
        self.emitf(f"jne {tn_have}", "mov eax, -1", f"jmp {tn_have_done}")
        self.label(tn_have)
        tn_pos = self.fresh("ssl_tn_pos")
        self.emitf("test eax, eax", f"jns {tn_pos}", "add eax, [ebp-20]")
        self.label(tn_pos)
        # Clamp stop to [-1, len-1] (CPython does this for neg step).
        self.emitf("cmp eax, -1")
        tn_gem1 = self.fresh("ssl_tn_gem1")
        self.emitf(f"jge {tn_gem1}", "mov eax, -1")
        self.label(tn_gem1)
        self.emitf("mov ebx, [ebp-20]", "dec ebx", "cmp eax, ebx")
        tn_lel = self.fresh("ssl_tn_lel")
        self.emitf(f"jle {tn_lel}", "mov eax, ebx")
        self.label(tn_lel)
        self.label(tn_have_done)
        self.emitf("mov [ebp-28], eax")

        self.label(start_done)
        # Compute output length n = max(0, ceil_div(|stop - start|, |step|)).
        # As in the x86-64 original: allocate (len + 1) bytes -- generous
        # but safe -- and fill via a counted loop.
        self.emitf("mov eax, [ebp-20]", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-32], eax")  # output buffer

        # i = effective_start, w = 0
        self.emitf("mov eax, [ebp-24]", "mov [ebp-36], eax")
        self.emitf("mov dword [ebp-40], 0")  # write index

        loop = self.fresh("ssl_loop")
        done = self.fresh("ssl_done")
        # Loop condition depends on step sign.
        self.label(loop)
        self.emitf("mov eax, [ebp-16]", "test eax, eax")
        lp_neg = self.fresh("ssl_lp_neg")
        self.emitf(f"js {lp_neg}")
        # step > 0: while i < stop
        self.emitf("mov eax, [ebp-36]", "cmp eax, [ebp-28]", f"jge {done}")
        skip_neg = self.fresh("ssl_skipneg")
        self.emitf(f"jmp {skip_neg}")
        self.label(lp_neg)
        # step < 0: while i > stop
        self.emitf("mov eax, [ebp-36]", "cmp eax, [ebp-28]", f"jle {done}")
        self.label(skip_neg)
        # buf[w] = s[i]
        self.emitf(
            "mov ebx, [ebp-4]",
            "mov ecx, [ebp-36]",
            "movzx edx, byte [ebx+ecx]",
            "mov ebx, [ebp-32]",
            "mov ecx, [ebp-40]",
            "mov [ebx+ecx], dl",
            "inc dword [ebp-40]",
            # i += step
            "mov eax, [ebp-36]",
            "add eax, [ebp-16]",
            "mov [ebp-36], eax",
            f"jmp {loop}",
        )
        self.label(done)
        # nul-terminate at w, return buffer.
        self.emitf(
            "mov eax, [ebp-32]",
            "mov ecx, [ebp-40]",
            "mov byte [eax+ecx], 0",
            "leave",
            "ret",
        )

    def _emit_str_split_helper(self) -> None:
        """`_runtime_str_split`: `s.split(sep[, maxsplit])` -> list[str].

        In: eax = s, ebx = sep, ecx = maxsplit (0 = no limit).
        Out: eax = list header.

        i386 port -- 4-byte pointer-width list-buffer slots throughout
        (shl by 2, not 3); every raw Windows-x64-fastcall-style `call
        malloc`/`call realloc` in the x86-64 original (args in rcx/rdx)
        becomes this target's own real cdecl `push`/`call`/`add esp,N`
        sequence, matching every other malloc/realloc call already in
        this file -- NOT a register rename, since cdecl's argument-
        passing convention (stack, not registers) is genuinely different
        from the fastcall-style raw calls the x86-64 reference happens to
        use here.
        """
        self.label("_runtime_str_split")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 96")
        self.emitf(
            "mov [ebp-4], eax",  # s
            "mov [ebp-8], ebx",  # sep
            "mov [ebp-72], ecx",  # maxsplit (0 = unlimited)
        )
        # sep_len
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-12], eax")  # sep_len
        # n_parts = (sep_len > 0) ? count(s, sep) + 1 : 1
        empty_sep = self.fresh("ssp_empty")
        count_done = self.fresh("ssp_count_done")
        self.emitf("test eax, eax", f"jz {empty_sep}")
        self.emitf("mov eax, [ebp-4]", "mov ebx, [ebp-8]")
        self.emitf("call _runtime_str_count", "inc eax", f"jmp {count_done}")
        self.label(empty_sep)
        self.emitf("mov eax, 1")
        self.label(count_done)
        # Cap n_parts at maxsplit+1 when maxsplit > 0
        ms_cap_skip = self.fresh("ssp_ms_skip")
        self.emitf(
            "mov ecx, [ebp-72]",
            "test ecx, ecx",
            f"jz {ms_cap_skip}",
            "inc ecx",  # ecx = maxsplit + 1
            "cmp eax, ecx",
            f"jle {ms_cap_skip}",
            "mov eax, ecx",
        )  # n_parts = min(n_parts, maxsplit+1)
        self.label(ms_cap_skip)
        self.emitf("mov [ebp-16], eax")  # n_parts
        # cap = max(n_parts, 4)
        cap_ok = self.fresh("ssp_cap_ok")
        self.emitf("cmp eax, 4", f"jge {cap_ok}", "mov eax, 4")
        self.label(cap_ok)
        self.emitf("mov [ebp-20], eax")  # cap
        # Allocate list header (24)
        self._emit_malloc(self.LIST_HEADER)
        self.emitf("mov [ebp-24], eax")
        # Initialize header: cap, len = n_parts, buf set below.
        self.emitf(
            "mov edx, [ebp-24]",
            "mov eax, [ebp-20]",
            f"mov [edx+{self.LIST_CAP_OFF}], eax",
            "mov eax, [ebp-16]",
            f"mov [edx+{self.LIST_LEN_OFF}], eax",
        )
        # Allocate buffer cap * 4 (4-byte pointer-width slots on this target)
        self.emitf("mov eax, [ebp-20]", "shl eax, 2")
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov [ebp-28], eax",  # list buf
            "mov edx, [ebp-24]",
            f"mov [edx+{self.LIST_BUF_OFF}], eax",
        )
        # Walk: cursor = s, w = 0
        self.emitf(
            "mov eax, [ebp-4]",
            "mov [ebp-32], eax",  # cursor
            "mov dword [ebp-36], 0",
        )  # w
        # If sep is empty: emit the whole string as one element and return.
        empty_branch = self.fresh("ssp_empty_done")
        self.emitf("cmp dword [ebp-12], 0", f"jne {empty_branch}")
        # Single element: strdup(s) and append.
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strdup()
        self.emitf(
            "mov edx, [ebp-28]",
            "mov [edx], eax",
            "mov eax, [ebp-24]",
            "leave",
            "ret",
        )
        self.label(empty_branch)

        loop = self.fresh("ssp_loop")
        last = self.fresh("ssp_last")
        self.label(loop)
        # If maxsplit > 0 and w >= maxsplit, treat rest of string as final segment.
        ms_loop_skip = self.fresh("ssp_ms_loop_skip")
        self.emitf(
            "mov ecx, [ebp-72]",
            "test ecx, ecx",
            f"jz {ms_loop_skip}",
            "cmp [ebp-36], ecx",
            f"jge {last}",
        )
        self.label(ms_loop_skip)
        # Find next sep occurrence in cursor
        self.emitf("mov eax, [ebp-32]", "mov ebx, [ebp-8]")
        self._emit_libc_strstr()
        self.emitf("test eax, eax", f"jz {last}")
        self.emitf("mov [ebp-40], eax")  # match ptr
        # seg_len = match - cursor
        self.emitf("sub eax, [ebp-32]", "mov [ebp-44], eax")
        # malloc(seg_len + 1)
        self.emitf("inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-48], eax")
        # memcpy(new, cursor, seg_len)
        self.emitf(
            "mov eax, [ebp-48]",
            "mov ebx, [ebp-32]",
            "mov ecx, [ebp-44]",
        )
        self._emit_libc_memcpy()
        # nul-terminate
        self.emitf(
            "mov eax, [ebp-48]",
            "mov ecx, [ebp-44]",
            "mov byte [eax+ecx], 0",
        )
        # list_buf[w*4] = new_str
        self.emitf(
            "mov eax, [ebp-28]",
            "mov ecx, [ebp-36]",
            "shl ecx, 2",
            "add eax, ecx",
            "mov ebx, [ebp-48]",
            "mov [eax], ebx",
            "inc dword [ebp-36]",
            # cursor = match + sep_len
            "mov eax, [ebp-40]",
            "add eax, [ebp-12]",
            "mov [ebp-32], eax",
            f"jmp {loop}",
        )
        # Last segment: from cursor to end-of-string.
        self.label(last)
        self.emitf("mov eax, [ebp-32]")
        self._emit_libc_strdup()
        self.emitf(
            "mov ebx, eax",
            "mov eax, [ebp-28]",
            "mov ecx, [ebp-36]",
            "shl ecx, 2",
            "add eax, ecx",
            "mov [eax], ebx",
        )
        self.emitf("mov eax, [ebp-24]", "leave", "ret")

    def _emit_str_split_ws_helper(self) -> None:
        """`_runtime_str_split_ws`: `s.split()` -- split on runs of whitespace.

        In: eax = s. Out: eax = list header.

        Algorithm: single pass, grow list dynamically (initial cap 4). For
        each word: save cur to word_start, scan to end, malloc(word_len+1)
        and copy (never mutate the source, which may be read-only), append
        ptr to list, grow list buf if needed.

        i386 port -- 4-byte pointer-width list-buffer slots (shl by 2, not
        3); the x86-64 original's raw fastcall-style `call malloc`/`call
        realloc` (args in rcx/rdx, no push) become this target's real
        cdecl push/call/add-esp sequences; edi/esi (copy-loop pointers,
        saved/restored via push/pop in the x86-64 original because
        they're Win64-nonvolatile) are saved/restored here too since
        they're callee-saved under this target's own cdecl convention
        (module docstring / _emit_list_reverse_helper's own precedent).
        """

        # whitespace chars: space(0x20) tab(0x09) newline(0x0A) CR(0x0D) FF(0x0C) VT(0x0B)
        def _is_ws_jmp(jmp_if_ws: str, jmp_if_not_ws: str) -> None:
            # eax holds the byte value (zero-extended)
            self.emitf(
                "cmp eax, 0x20",
                f"je {jmp_if_ws}",
                "cmp eax, 0x09",
                f"je {jmp_if_ws}",
                "cmp eax, 0x0A",
                f"je {jmp_if_ws}",
                "cmp eax, 0x0D",
                f"je {jmp_if_ws}",
                "cmp eax, 0x0C",
                f"je {jmp_if_ws}",
                "cmp eax, 0x0B",
                f"je {jmp_if_ws}",
                f"jmp {jmp_if_not_ws}",
            )

        self.label("_runtime_str_split_ws")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 64")
        self.emitf("mov [ebp-4], eax")

        # Allocate initial list: header + buf of 4 slots
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            "mov [ebp-8], eax",
            f"mov dword [eax+{self.LIST_CAP_OFF}], 4",
            f"mov dword [eax+{self.LIST_LEN_OFF}], 0",
        )
        self._emit_malloc(16)  # 4 slots * 4-byte pointer width
        self.emitf(
            "mov [ebp-12], eax",
            "mov edx, [ebp-8]",
            f"mov [edx+{self.LIST_BUF_OFF}], eax",
        )
        self.emitf(
            "mov dword [ebp-16], 0",  # len
            "mov dword [ebp-20], 4",  # cap
            "mov eax, [ebp-4]",
            "mov [ebp-24], eax",  # cursor = s
        )

        top = self.fresh("ssw_top")
        end = self.fresh("ssw_end")
        found_word = self.fresh("ssw_word")
        skip_ws_lbl = self.fresh("ssw_skipws")
        not_ws1 = self.fresh("ssw_nws1")
        scan_word_lbl = self.fresh("ssw_scan")
        not_ws2 = self.fresh("ssw_nws2")
        is_ws2 = self.fresh("ssw_ws2")

        # Loop: skip whitespace, then extract word
        self.label(top)
        self.emitf(
            "mov eax, [ebp-24]",
            "movzx eax, byte [eax]",
            "test eax, eax",
            f"jz {end}",
        )
        _is_ws_jmp(skip_ws_lbl, not_ws1)
        self.label(skip_ws_lbl)
        self.emitf(
            "mov eax, [ebp-24]",
            "inc eax",
            "mov [ebp-24], eax",
            f"jmp {top}",
        )
        # Not whitespace: start of word
        self.label(not_ws1)
        # word_start = cursor
        self.emitf(
            "mov eax, [ebp-24]",
            "mov [ebp-28], eax",
        )
        # Scan to end of word (stop at ws or NUL)
        self.label(scan_word_lbl)
        self.emitf(
            "mov eax, [ebp-24]",
            "inc eax",
            "mov [ebp-24], eax",
            "movzx eax, byte [eax]",
            "test eax, eax",
            f"jz {found_word}",
        )
        _is_ws_jmp(is_ws2, not_ws2)
        self.label(is_ws2)
        self.emitf(f"jmp {found_word}")
        self.label(not_ws2)
        self.emitf(f"jmp {scan_word_lbl}")

        self.label(found_word)
        # cursor points to the byte after the last word char (ws or NUL).
        # Allocate word_len+1 bytes and copy -- do NOT write into the source
        # string, which may be in read-only .rodata memory.
        # Layout: [ebp-28]=word_start(src), [ebp-24]=cursor(end), [ebp-32]=word_len
        copy_lp = self.fresh("ssw_cp")
        copy_end = self.fresh("ssw_cpend")
        self.emitf(
            # word_len = cursor - word_start
            "mov eax, [ebp-24]",  # eax = cursor (one past last word char)
            "sub eax, [ebp-28]",  # eax = word_len
            "mov [ebp-32], eax",  # [ebp-32] = word_len
            # malloc(word_len + 1) for NUL
            "inc eax",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            # Save dst; use callee-saved EDI and ESI (preserved across calls
            # by our own convention -- see this method's own docstring).
            "push edi",
            "push esi",
            "mov edi, eax",  # edi = dst (advancing)
            "mov esi, [ebp-28]",  # esi = src = word_start
            "mov ecx, [ebp-32]",  # ecx = word_len
        )
        self.label(copy_lp)
        self.emitf(
            "test ecx, ecx",
            f"jz {copy_end}",
            "movzx eax, byte [esi]",
            "mov [edi], al",
            "inc esi",
            "inc edi",
            "dec ecx",
            f"jmp {copy_lp}",
        )
        self.label(copy_end)
        self.emitf(
            "mov byte [edi], 0",  # NUL terminate
            # dst_base = edi - word_len = &buf[0]
            "sub edi, [ebp-32]",
            "mov [ebp-28], edi",  # [ebp-28] = word dup ptr
            "pop esi",  # restore esi
            "pop edi",  # restore edi
        )
        # Grow list if len == cap
        no_grow = self.fresh("ssw_nogrow")
        self.emitf(
            "mov eax, [ebp-16]",
            "cmp eax, [ebp-20]",
            f"jl {no_grow}",
        )
        # Double cap: new_cap = cap*2; realloc buf (4-byte slots on this target)
        self.emitf(
            "mov eax, [ebp-20]",
            "shl eax, 1",
            "mov [ebp-20], eax",  # save new cap
            "shl eax, 2",  # bytes = new_cap * 4
        )
        self.emitf("push eax", "push dword [ebp-12]", "call realloc", "add esp, 8")
        self.emitf(
            "mov [ebp-12], eax",  # save new buf
            "mov edx, [ebp-8]",
            f"mov [edx+{self.LIST_BUF_OFF}], eax",
            "mov eax, [ebp-20]",
            "mov edx, [ebp-8]",
            f"mov [edx+{self.LIST_CAP_OFF}], eax",
        )
        self.label(no_grow)
        # buf[len] = word_dup; len++
        self.emitf(
            "mov eax, [ebp-16]",
            "shl eax, 2",
            "mov edx, [ebp-12]",
            "add edx, eax",
            "mov ecx, [ebp-28]",
            "mov [edx], ecx",
            "inc dword [ebp-16]",
        )
        self.emitf(f"jmp {top}")

        self.label(end)
        # Set list len in header
        self.emitf(
            "mov eax, [ebp-16]",
            "mov edx, [ebp-8]",
            f"mov [edx+{self.LIST_LEN_OFF}], eax",
            "mov eax, edx",
            "leave",
            "ret",
        )

    def _emit_str_splitlines_helper(self) -> None:
        """`_runtime_str_splitlines`: `s.splitlines()` -> list[str].

        In: eax = s. Out: eax = list header.

        Implemented as split on '\\n' followed by dropping a single
        trailing empty element (so `"a\\nb\\n".splitlines()` -> ["a","b"],
        matching CPython for LF-terminated text). Bare CR / CRLF aren't
        special-cased. i386 port -- 4-byte pointer-width list slots.
        """
        self.label("_runtime_str_splitlines")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 16")
        # split(s, "\n")
        self.emitf(
            "mov ebx, _runtime_nl_str",
            "xor ecx, ecx",  # maxsplit = 0 (no limit)
            "call _runtime_str_split",
            "mov [ebp-4], eax",  # list header
        )
        # If len > 0 and the last element is "" (strlen 0), drop it.
        done = self.fresh("splitlines_done")
        self.emitf(
            f"mov ecx, [eax+{self.LIST_LEN_OFF}]",
            "test ecx, ecx",
            f"jz {done}",  # empty list -> nothing to trim
            # last element ptr = buf[(len-1)*4]
            f"mov edx, [eax+{self.LIST_BUF_OFF}]",
            "dec ecx",
            "mov eax, [edx+ecx*4]",  # eax = last element str ptr
        )
        # strlen(last) -> if 0, decrement the list length.
        self._emit_libc_strlen()  # eax = length of last element
        self.emitf(
            "test eax, eax",
            f"jnz {done}",
            "mov edx, [ebp-4]",
            f"mov ecx, [edx+{self.LIST_LEN_OFF}]",
            "dec ecx",
            f"mov [edx+{self.LIST_LEN_OFF}], ecx",
        )
        self.label(done)
        self.emitf("mov eax, [ebp-4]", "leave", "ret")

    def _emit_str_join_helper(self) -> None:
        """`_runtime_str_join`: `sep.join(parts)` -> str.

        In: eax = sep, ebx = list[str] header.
        Out: eax = newly-allocated concatenation.

        Two-pass: first sums total length (sep * (n-1) +
        sum(len(parts[i]))), then mallocs and copies each part with the
        separator between. i386 port -- 4-byte pointer-width list slots
        (shl by 2, not 3); the x86-64 original's raw fastcall-style `call
        malloc` (arg in rcx) becomes this target's real cdecl push/call/
        add-esp sequence.
        """
        self.label("_runtime_str_join")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 80")
        self.emitf(
            "mov [ebp-4], eax",  # sep
            "mov [ebp-8], ebx",  # list header
        )
        # n = list.len
        self.emitf(f"mov eax, [ebx+{self.LIST_LEN_OFF}]", "mov [ebp-12], eax")
        # sep_len
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-16], eax")  # sep_len
        # total = sep_len * max(0, n-1)
        sep_zero = self.fresh("sj_sep_zero")
        sep_zero_done = self.fresh("sj_sep_zero_done")
        self.emitf("mov ecx, [ebp-12]", "test ecx, ecx", f"jz {sep_zero}")
        self.emitf(
            "dec ecx", "mov eax, [ebp-16]", "imul eax, ecx", f"jmp {sep_zero_done}"
        )
        self.label(sep_zero)
        self.emitf("xor eax, eax")
        self.label(sep_zero_done)
        self.emitf("mov [ebp-20], eax")  # total
        # Add sum(strlen(parts[i]))
        self.emitf("mov dword [ebp-24], 0")  # i = 0
        sum_loop = self.fresh("sj_sum_loop")
        sum_done = self.fresh("sj_sum_done")
        self.label(sum_loop)
        self.emitf(
            "mov eax, [ebp-24]",
            "cmp eax, [ebp-12]",
            f"jge {sum_done}",
            "mov ebx, [ebp-8]",
            f"mov ebx, [ebx+{self.LIST_BUF_OFF}]",
            "mov ecx, [ebp-24]",
            "shl ecx, 2",
            "mov eax, [ebx+ecx]",
        )
        self._emit_libc_strlen()
        self.emitf("add [ebp-20], eax", "inc dword [ebp-24]", f"jmp {sum_loop}")
        self.label(sum_done)
        # malloc(total + 1)
        self.emitf("mov eax, [ebp-20]", "inc eax")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-28], eax")  # output buffer
        # Walk parts again, copying each. After the first, prepend separator.
        self.emitf(
            "mov dword [ebp-24], 0",  # i
            "mov eax, [ebp-28]",
            "mov [ebp-32], eax",  # write cursor
        )
        cp_loop = self.fresh("sj_cp_loop")
        cp_done = self.fresh("sj_cp_done")
        not_first = self.fresh("sj_not_first")
        self.label(cp_loop)
        self.emitf(
            "mov eax, [ebp-24]",
            "cmp eax, [ebp-12]",
            f"jge {cp_done}",
            "test eax, eax",
            f"jz {not_first}",
        )
        # Copy separator first.
        self.emitf(
            "mov eax, [ebp-32]",
            "mov ebx, [ebp-4]",
            "mov ecx, [ebp-16]",
        )
        self._emit_libc_memcpy()
        self.emitf(
            "mov eax, [ebp-32]",
            "add eax, [ebp-16]",
            "mov [ebp-32], eax",
        )
        self.label(not_first)
        # part = list.buf[i*4]
        self.emitf(
            "mov ebx, [ebp-8]",
            f"mov ebx, [ebx+{self.LIST_BUF_OFF}]",
            "mov ecx, [ebp-24]",
            "shl ecx, 2",
            "mov edx, [ebx+ecx]",
            "mov [ebp-36], edx",  # part ptr
            "mov eax, edx",
        )
        self._emit_libc_strlen()
        # memcpy(write, part, plen)
        self.emitf(
            "mov ecx, eax",
            "mov ebx, [ebp-36]",
            "mov eax, [ebp-32]",
            "push ecx",
        )
        self._emit_libc_memcpy()
        self.emitf(
            "pop ecx",
            "mov eax, [ebp-32]",
            "add eax, ecx",
            "mov [ebp-32], eax",
            "inc dword [ebp-24]",
            f"jmp {cp_loop}",
        )
        self.label(cp_done)
        # nul-terminate at write cursor
        self.emitf("mov eax, [ebp-32]", "mov byte [eax], 0")
        self.emitf("mov eax, [ebp-28]", "leave", "ret")

    def _emit_str_partition_helper(self) -> None:
        """`_runtime_str_partition`: `s.partition(sep)` -> 3-tuple.

        In: eax = s, ebx = sep.
        Out: eax = a 3-slot tuple in the list [cap,len,buf] layout:
             (before, sep, after) at the first occurrence of sep, or
             (s, "", "") when sep doesn't occur (Python semantics).

        Composes existing helpers: index_of locates sep, str_slice carves
        the prefix, strdup copies sep and the suffix. i386 port -- 4-byte
        pointer-width tuple-buffer slots (12 bytes for 3 slots, not 24).
        """
        self.label("_runtime_str_partition")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf(
            "mov [ebp-4], eax",  # s
            "mov [ebp-8], ebx",  # sep
            "call _runtime_str_index_of",
            "mov [ebp-12], eax",  # idx (or -1)
            "cmp eax, -1",
            "jne ._spar_found",
        )
        # Not found: (strdup(s), "", "").
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strdup()
        self.emitf(
            "mov [ebp-16], eax",  # before = copy of s
            "mov eax, _runtime_empty_str",
            "mov [ebp-20], eax",  # mid = ""
            "mov [ebp-24], eax",  # after = ""
            "jmp ._spar_build",
        )
        self.label("._spar_found")
        # before = s[0:idx]
        self.emitf(
            "mov eax, [ebp-4]",
            "xor ebx, ebx",
            "mov ecx, [ebp-12]",
            "call _runtime_str_slice",
            "mov [ebp-16], eax",
        )
        # mid = strdup(sep)
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strdup()
        self.emitf("mov [ebp-20], eax")
        # after = strdup(s + idx + strlen(sep))
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strlen()
        self.emitf("add eax, [ebp-12]", "add eax, [ebp-4]")
        self._emit_libc_strdup()
        self.emitf("mov [ebp-24], eax")
        self.label("._spar_build")
        # Tuple header (24 bytes): cap=3, len=3, then a 3-slot buffer.
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            "mov [ebp-28], eax",
            f"mov dword [eax+{self.LIST_CAP_OFF}], 3",
            f"mov dword [eax+{self.LIST_LEN_OFF}], 3",
        )
        self._emit_malloc(12)  # 3 slots * 4-byte pointer width on this target
        self.emitf(
            "mov ecx, [ebp-28]",
            f"mov [ecx+{self.LIST_BUF_OFF}], eax",
            "mov edx, [ebp-16]",
            "mov [eax], edx",
            "mov edx, [ebp-20]",
            "mov [eax+4], edx",
            "mov edx, [ebp-24]",
            "mov [eax+8], edx",
            "mov eax, [ebp-28]",
            "leave",
            "ret",
        )

    def _emit_str_rpartition_helper(self) -> None:
        """`_runtime_str_rpartition`: `s.rpartition(sep)` -> 3-tuple.

        In: eax = s, ebx = sep.
        Out: eax = a 3-slot tuple in the list [cap,len,buf] layout:
             (before, sep, after) at the LAST occurrence of sep, or
             ("", "", s) when sep doesn't occur (Python semantics -- note
             this is the mirror image of partition's not-found case).

        Finds the last occurrence with a forward strstr scan advancing
        past each hit (same approach as rsplit). i386 port -- 4-byte
        pointer-width tuple-buffer slots.
        """
        self.label("_runtime_str_rpartition")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 40")
        self.emitf(
            "mov [ebp-4], eax",  # s
            "mov [ebp-8], ebx",  # sep
            "mov dword [ebp-12], -1",  # last index
            "mov [ebp-16], eax",  # scan cursor
        )
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-20], eax", "test eax, eax", "jz ._srp_notfound")
        self.label("._srp_scan")
        self.emitf("mov eax, [ebp-16]", "mov ebx, [ebp-8]")
        self._emit_libc_strstr()
        self.emitf(
            "test eax, eax",
            "jz ._srp_scandone",
            "mov ebx, eax",
            "sub ebx, [ebp-4]",
            "mov [ebp-12], ebx",  # last = hit - s
            "add eax, [ebp-20]",  # cursor = hit + seplen (non-overlapping)
            "mov [ebp-16], eax",
            "jmp ._srp_scan",
        )
        self.label("._srp_scandone")
        self.emitf("cmp dword [ebp-12], -1", "je ._srp_notfound")
        # before = s[0:last]
        self.emitf(
            "mov eax, [ebp-4]",
            "xor ebx, ebx",
            "mov ecx, [ebp-12]",
            "call _runtime_str_slice",
            "mov [ebp-24], eax",
        )
        # mid = strdup(sep)
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strdup()
        self.emitf("mov [ebp-28], eax")
        # after = strdup(s + last + seplen)
        self.emitf("mov eax, [ebp-4]", "add eax, [ebp-12]", "add eax, [ebp-20]")
        self._emit_libc_strdup()
        self.emitf("mov [ebp-32], eax", "jmp ._srp_build")
        self.label("._srp_notfound")
        # ("", "", strdup(s))
        self.emitf(
            "mov eax, _runtime_empty_str",
            "mov [ebp-24], eax",  # before = ""
            "mov [ebp-28], eax",  # mid = ""
            "mov eax, [ebp-4]",
        )
        self._emit_libc_strdup()
        self.emitf("mov [ebp-32], eax")  # after = copy of s
        self.label("._srp_build")
        # Tuple header (24 bytes): cap=3, len=3, then a 3-slot buffer.
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            "mov [ebp-36], eax",
            f"mov dword [eax+{self.LIST_CAP_OFF}], 3",
            f"mov dword [eax+{self.LIST_LEN_OFF}], 3",
        )
        self._emit_malloc(12)
        self.emitf(
            "mov ecx, [ebp-36]",
            f"mov [ecx+{self.LIST_BUF_OFF}], eax",
            "mov edx, [ebp-24]",
            "mov [eax], edx",
            "mov edx, [ebp-28]",
            "mov [eax+4], edx",
            "mov edx, [ebp-32]",
            "mov [eax+8], edx",
            "mov eax, [ebp-36]",
            "leave",
            "ret",
        )

    def _emit_str_rsplit_helper(self) -> None:
        """`_runtime_str_rsplit`: `s.rsplit(sep, 1)` -> list[str].

        In: eax = s, ebx = sep, ecx = maxsplit (sema pins it to 1; ignored).
        Out: eax = list header: [before, after] split at the LAST
             occurrence of sep, or [s-copy] when sep doesn't occur / is
             empty.

        Finds the last occurrence with a forward strstr scan advancing
        past each hit. i386 port -- 4-byte pointer-width list-buffer slots
        (shl by 2, not 3).
        """
        self.label("_runtime_str_rsplit")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 40")
        self.emitf(
            "mov [ebp-4], eax",  # s
            "mov [ebp-8], ebx",  # sep
            "mov dword [ebp-12], -1",  # last index
            "mov [ebp-16], eax",  # scan cursor
        )
        # seplen = strlen(sep); empty sep -> single-element result.
        self.emitf("mov eax, [ebp-8]")
        self._emit_libc_strlen()
        self.emitf("mov [ebp-20], eax", "test eax, eax", "jz ._srs_one")
        self.label("._srs_scan")
        self.emitf("mov eax, [ebp-16]", "mov ebx, [ebp-8]")
        self._emit_libc_strstr()
        self.emitf(
            "test eax, eax",
            "jz ._srs_scandone",
            "mov ebx, eax",
            "sub ebx, [ebp-4]",
            "mov [ebp-12], ebx",  # last = hit - s
            "add eax, [ebp-20]",  # cursor = hit + seplen (non-overlapping)
            "mov [ebp-16], eax",
            "jmp ._srs_scan",
        )
        self.label("._srs_scandone")
        self.emitf("cmp dword [ebp-12], -1", "je ._srs_one")
        # before = s[0:last]
        self.emitf(
            "mov eax, [ebp-4]",
            "xor ebx, ebx",
            "mov ecx, [ebp-12]",
            "call _runtime_str_slice",
            "mov [ebp-24], eax",
        )
        # after = strdup(s + last + seplen)
        self.emitf("mov eax, [ebp-4]", "add eax, [ebp-12]", "add eax, [ebp-20]")
        self._emit_libc_strdup()
        self.emitf("mov [ebp-28], eax", "mov dword [ebp-32], 2", "jmp ._srs_build")
        self.label("._srs_one")
        # No split: a single-element list holding a copy of s.
        self.emitf("mov eax, [ebp-4]")
        self._emit_libc_strdup()
        self.emitf("mov [ebp-24], eax", "mov dword [ebp-32], 1")
        self.label("._srs_build")
        # header: cap = len = n; buf = n*4.
        self._emit_malloc(self.LIST_HEADER)
        self.emitf(
            "mov [ebp-36], eax",
            "mov edx, [ebp-32]",
            f"mov [eax+{self.LIST_CAP_OFF}], edx",
            f"mov [eax+{self.LIST_LEN_OFF}], edx",
            "mov eax, [ebp-32]",
            "shl eax, 2",
        )
        self._emit_libc_malloc_size_in_rax()
        self.emitf(
            "mov ecx, [ebp-36]",
            f"mov [ecx+{self.LIST_BUF_OFF}], eax",
            "mov edx, [ebp-24]",
            "mov [eax], edx",
            "cmp dword [ebp-32], 2",
            "jne ._srs_ret",
            "mov edx, [ebp-28]",
            "mov [eax+4], edx",
        )
        self.label("._srs_ret")
        self.emitf("mov eax, [ebp-36]", "leave", "ret")

    def _emit_chr_helper(self) -> None:
        """`_runtime_chr`: chr(n) -> a fresh 1-char string (byte n, NUL).

        In: eax = int (0..255 meaningful). Out: eax = 2-byte heap string.
        """
        self.label("_runtime_chr")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 16", "mov [ebp-4], eax")
        self._emit_malloc(2)
        self.emitf(
            "mov edx, [ebp-4]",
            "mov [eax], dl",
            "mov byte [eax+1], 0",
            "leave",
            "ret",
        )

    def _emit_str_predicate_helpers(self) -> None:
        """Character-class predicates: isdigit/isalpha/isalnum/isspace/
        isupper/islower. Each takes eax = s and returns eax = 0/1.

        Python semantics: the empty string is False for all of these, and
        the result is True only if *every* character satisfies the class
        (for the cased predicates, additionally at least one cased
        character must be present). ASCII-only -- matches the rest of the
        str runtime.

        i386 port -- pure byte-level scanning with small-integer
        comparisons, no 64-bit-specific arithmetic at all, so the core
        logic is a mechanical register substitution (esi/edx unchanged;
        edi replaces the x86-64 original's r8 as the cased-predicate
        "saw a good char" flag, since this architecture has no r8-r15 at
        all). One REAL, non-mechanical difference from the x86-64
        original: these functions use esi (and, for isupper/islower, edi
        too) as scratch with a bare `ret` and no save/restore at all in
        the x86-64 reference -- safe there only because rsi is a SysV-
        AMD64 CALLER-saved register, so clobbering it needs no special
        announcement. On THIS target, esi/edi are genuinely CALLEE-saved
        under the real i386 cdecl convention this file's other helpers
        already establish and rely on (see _emit_list_reverse_helper's
        own docstring, and every _runtime_str_upper/_zfill/_ljust/etc.
        helper already ported above, all of which explicitly push/pop
        esi/edi around their own scratch use) -- so every exit path here
        pushes esi (and edi, for the cased predicates) on entry and pops
        them back before each `ret`, which the x86-64 original does not
        need to do. Skipping this would silently corrupt a caller's live
        esi/edi across a `"abc".isdigit()`-style call, a real correctness
        bug distinct from anything in the reference.
        """
        # The membership predicates (digit/alpha/alnum/space): non-empty and
        # every char passes. Each `checks` entry is a list of inclusive byte
        # ranges; a char passes if it falls in any range.
        membership = {
            "_runtime_str_isdigit": [(48, 57)],  # 0-9
            "_runtime_str_isalpha": [(65, 90), (97, 122)],  # A-Z a-z
            "_runtime_str_isalnum": [(48, 57), (65, 90), (97, 122)],
            "_runtime_str_isspace": [(9, 13), (32, 32)],  # \t\n\v\f\r and space
        }
        for sym, ranges in membership.items():
            tag = sym.rsplit("_", 1)[1]  # e.g. "isdigit"
            loop = f"._{tag}_loop"
            ok = f"._{tag}_char_ok"
            no = f"._{tag}_no"
            yes_empty = f"._{tag}_empty"
            self.label(sym)
            self.emitf("push esi", "mov esi, eax", "mov dl, [esi]")
            # Empty string -> 0.
            self.emitf("test dl, dl", f"jz {yes_empty}")
            self.label(loop)
            self.emitf("mov dl, [esi]", "test dl, dl", f"jz ._{tag}_yes")
            # char passes if in any range; otherwise -> no.
            for lo, hi in ranges:
                lo_s = str(lo)
                hi_s = str(hi)
                if lo == hi:
                    self.emitf("cmp dl, " + lo_s, "je " + ok)
                else:
                    skip = self.fresh(tag + "_rng")
                    self.emitf(
                        "cmp dl, " + lo_s,
                        "jl " + skip,
                        "cmp dl, " + hi_s,
                        "jle " + ok,
                    )
                    self.label(skip)
            self.emitf(f"jmp {no}")
            self.label(ok)
            self.emitf("inc esi", f"jmp {loop}")
            self.label(f"._{tag}_yes")
            self.emitf("mov eax, 1", "pop esi", "ret")
            self.label(no)
            self.label(yes_empty)
            self.emitf("xor eax, eax", "pop esi", "ret")

        # Cased predicates. isupper: non-empty, no lowercase char, and at least
        # one uppercase char. islower symmetric. Use edi as the "saw a cased
        # char of the right case" flag (was r8 in the x86-64 original).
        for sym, (good_lo, good_hi, bad_lo, bad_hi) in {
            "_runtime_str_isupper": (65, 90, 97, 122),  # good=upper, bad=lower
            "_runtime_str_islower": (97, 122, 65, 90),  # good=lower, bad=upper
        }.items():
            tag = sym.rsplit("_", 1)[1]
            loop = f"._{tag}_loop"
            chk_bad = f"._{tag}_chk_bad"
            nxt = f"._{tag}_next"
            glo_s = str(good_lo)
            ghi_s = str(good_hi)
            blo_s = str(bad_lo)
            bhi_s = str(bad_hi)
            self.label(sym)
            self.emitf(
                "push esi", "push edi", "mov esi, eax", "xor edi, edi"
            )  # edi = saw a good cased char
            self.label(loop)
            self.emitf("mov dl, [esi]", "test dl, dl", f"jz ._{tag}_done")
            # A char in the bad-case range fails immediately.
            self.emitf(
                "cmp dl, " + glo_s,
                "jl " + chk_bad,
                "cmp dl, " + ghi_s,
                "jg " + chk_bad,
                "mov edi, 1",
                "jmp " + nxt,
            )
            self.label(chk_bad)
            self.emitf(
                "cmp dl, " + blo_s,
                "jl " + nxt,
                "cmp dl, " + bhi_s,
                "jg " + nxt,
                "jmp ._" + tag + "_no",
            )
            self.label(nxt)
            self.emitf("inc esi", f"jmp {loop}")
            self.label(f"._{tag}_done")
            # Result = edi (true only if we saw >=1 good cased char and no bad).
            self.emitf("mov eax, edi", "pop edi", "pop esi", "ret")
            self.label(f"._{tag}_no")
            self.emitf("xor eax, eax", "pop edi", "pop esi", "ret")

    def _emit_list_repr_helper32(self) -> None:
        """Container repr helpers and the shared per-element formatter.

        Emits:
          _runtime_fmt_elem  - format one value by kind -> repr string
          _runtime_list_repr - `[e0, e1, ...]` for a list/tuple
          _runtime_dict_repr - `{k0: v0, k1: v1, ...}` for a dict
          _runtime_set_repr  - `{e0, e1, ...}` for a set
          _runtime_range_list - materialize range(start, stop, step) -> list
          _runtime_str_concat_dup - fresh copy of a string

        i386 port -- 4-byte pointer-width list/tuple/dict-order buffer
        slots throughout (shl by 2, not 3; LIST_HEADER/DICT_* offsets
        unchanged per the module docstring's own layout-compatibility
        note). All loop state lives in stack slots so the int/float/str
        conversion helpers (which clobber registers freely) can be called
        mid-iteration, exactly as in the x86-64 original.

        KNOWN GAP -- _runtime_fmt_elem's float branch (kind base value 2)
        is intentionally left as a loud, documented failure rather than a
        faithful port. The x86-64 original's contract for this case is
        "rax holds the full 64-bit bit pattern of the double; movq xmm0,
        rax recovers it" -- which only works there because rax IS 64 bits
        wide. On this target eax is 32 bits, so "the value" cannot
        physically arrive in a single eax the way the ABI comment for
        every OTHER kind here promises. Tracing the actual call graph
        (codegen.py's own _gen_list_lit, the base-class, unmodified-per-
        target method that fills a list literal's buffer) shows this
        target has no working story yet for storing a float into a list/
        dict buffer slot AT ALL: _gen_list_lit stores every float element
        via `movsd [rcx+i*8], xmm0` (an 8-byte-stride store) into a
        buffer this target's own _runtime_list_append/_runtime_list_repr
        treat as 4-byte-strided (`shl ecx, 2` throughout this file) --
        that mismatch belongs to _gen_list_lit/_gen_comprehension (base-
        class methods, not overridden per-target anywhere, confirmed by
        this session's own research), entirely outside emit_string_
        runtime's scope. Inventing a "low 32 bits only" convention here
        would silently produce a wrong (truncated) repr for float
        containers the moment that separate, out-of-scope gap is ever
        fixed -- worse than refusing loudly. So this one kind raises via
        _runtime_raise (a real, visible failure) instead of guessing.
        """
        # ---- _runtime_fmt_elem ------------------------------------------------
        # In: eax = value, ebx = kind. Low nibble = base kind (0 = int,
        # 1 = str-quoted, 2 = float, 3 = list, 4 = dict); for base kinds 3/4,
        # the high nibble is the element/value kind one level down (see
        # _composite_repr_kind).
        # Out: eax = repr string for that value.
        self.label("_runtime_fmt_elem")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf(
            "mov ecx, ebx",  # save full kind (incl. inner-kind bits)
            "and ebx, 0xF",  # base kind
            "cmp ebx, 1",
            "je ._fe_str",
            "cmp ebx, 2",
            "je ._fe_float",
            "cmp ebx, 3",
            "je ._fe_list",
            "cmp ebx, 4",
            "je ._fe_dict",
            "cmp ebx, 5",
            "je ._fe_items_tuple",
            "cmp ebx, 7",
            "je ._fe_bool",
        )
        self._emit_int_to_str()
        self.emitf("leave", "ret")
        # kind 7 -- an all-bool list, whose elements are raw 0/1 in the slot
        # (bool IS int here) and differ only in rendering. Unlike this target's
        # float gap above there is nothing 64-bit about a bool, so the x86-64
        # arm ports across unchanged apart from register width.
        self.label("._fe_bool")
        self.emitf(
            "test eax, eax",
            "jz ._fe_bool_false",
            "mov eax, _runtime_true_str",
            "call _runtime_str_concat_dup",
            "leave",
            "ret",
        )
        self.label("._fe_bool_false")
        self.emitf(
            "mov eax, _runtime_false_str",
            "call _runtime_str_concat_dup",
            "leave",
            "ret",
        )
        self.label("._fe_str")
        # wrap in single quotes -> "'" + elem + "'"
        self.emitf(
            "mov ebx, eax",
            "mov eax, _runtime_quote_str",
            "call _runtime_str_concat",
            "mov ebx, _runtime_quote_str",
            "call _runtime_str_concat",
            "leave",
            "ret",
        )
        self.label("._fe_float")
        # KNOWN GAP: see this method's own docstring. A single eax cannot
        # carry a double's full 64-bit bit pattern on this target the way
        # rax does on x86-64, and nothing on this target yet produces a
        # coherent 8-byte float value in a 4-byte-strided list/dict slot
        # to even feed this path with real data -- fail loudly rather
        # than silently truncate/misformat.
        _fe_float_msg, _ = self.intern_string(
            "repr() of a float list/dict element is not yet supported on the"
            " x86-32 backend"
        )
        self.emitf(
            f"mov eax, {_fe_float_msg}",
            f"mov ebx, {self._exc_type_id('NotImplementedError')}",
            "leave",
            "jmp _runtime_raise",
        )
        self.label("._fe_list")
        # eax = nested list ptr; ecx>>4 = element kind for _runtime_list_repr.
        self.emitf(
            "shr ecx, 4",
            "mov ebx, ecx",
            "call _runtime_list_repr",
            "leave",
            "ret",
        )
        self.label("._fe_dict")
        # eax = nested dict ptr; keys are str (ebx=1), ecx>>4 = value kind.
        self.emitf(
            "shr ecx, 4",
            "mov ebx, 1",
            "call _runtime_dict_repr",
            "leave",
            "ret",
        )
        self.label("._fe_items_tuple")
        # eax = tuple header ptr (cap=2, len=2, buf_ptr -> [key_str, val_int]).
        # Output: ('key', val) as a string. Frame: [ebp-4]=tup [ebp-8]=acc.
        self.emitf(
            "mov [ebp-4], eax",
            f"mov eax, [eax+{self.LIST_BUF_OFF}]",  # buf_ptr
            "mov ebx, [eax]",  # key str ptr
            "mov eax, _runtime_lparen_str",
            "call _runtime_str_concat_dup",
            "mov [ebp-8], eax",  # acc = "("
            # quote the key: "'" + key + "'"
            "mov eax, _runtime_quote_str",
            "mov ebx, [ebp-8]",
            "xchg eax, ebx",
            "call _runtime_str_concat",
            "mov [ebp-8], eax",
            "mov eax, [ebp-4]",
            f"mov eax, [eax+{self.LIST_BUF_OFF}]",
            "mov ebx, [eax]",  # key str ptr
            "mov eax, [ebp-8]",
            "call _runtime_str_concat",
            "mov [ebp-8], eax",
            "mov ebx, _runtime_quote_str",
            "call _runtime_str_concat",
            "mov [ebp-8], eax",
            # append ", "
            "mov ebx, _runtime_comma_str",
            "call _runtime_str_concat",
            "mov [ebp-8], eax",
            # append value (int): convert via _int_to_str
            "mov eax, [ebp-4]",
            f"mov eax, [eax+{self.LIST_BUF_OFF}]",
            "mov eax, [eax+4]",  # value int (4-byte field on this target)
        )
        self._emit_int_to_str()
        self.emitf(
            "mov ebx, eax",
            "mov eax, [ebp-8]",
            "call _runtime_str_concat",
            "mov [ebp-8], eax",
            # append ")"
            "mov ebx, _runtime_rparen_str",
            "call _runtime_str_concat",
            "leave",
            "ret",
        )

        # ---- _runtime_list_repr ----------------------------------------------
        # In: eax = list/tuple ptr, ebx = element kind. Out: eax = string.
        # [ebp-4]=list ptr [ebp-8]=kind [ebp-12]=len [ebp-16]=i
        # [ebp-20]=accumulator [ebp-24]=buf ptr
        self.label("_runtime_list_repr")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], ebx")
        self.emitf(
            "mov eax, _runtime_lbrack_str",
            "call _runtime_str_concat_dup",
            "mov [ebp-20], eax",
        )
        self.emitf(
            "mov eax, [ebp-4]",
            f"mov ebx, [eax+{self.LIST_LEN_OFF}]",
            "mov [ebp-12], ebx",
            f"mov ebx, [eax+{self.LIST_BUF_OFF}]",
            "mov [ebp-24], ebx",
            "mov dword [ebp-16], 0",
        )
        self.label("._lr_loop")
        self.emitf("mov eax, [ebp-16]", "cmp eax, [ebp-12]", "jge ._lr_done")
        self.emitf("mov eax, [ebp-16]", "test eax, eax", "jz ._lr_no_sep")
        self.emitf(
            "mov eax, [ebp-20]",
            "mov ebx, _runtime_comma_str",
            "call _runtime_str_concat",
            "mov [ebp-20], eax",
        )
        self.label("._lr_no_sep")
        self.emitf(
            "mov eax, [ebp-24]",
            "mov ecx, [ebp-16]",
            "mov eax, [eax+ecx*4]",
            "mov ebx, [ebp-8]",
            "call _runtime_fmt_elem",
        )
        self.emitf(
            "mov ebx, eax",
            "mov eax, [ebp-20]",
            "call _runtime_str_concat",
            "mov [ebp-20], eax",
        )
        self.emitf("inc dword [ebp-16]", "jmp ._lr_loop")
        self.label("._lr_done")
        self.emitf(
            "mov eax, [ebp-20]",
            "mov ebx, _runtime_rbrack_str",
            "call _runtime_str_concat",
            "leave",
            "ret",
        )

        # ---- _runtime_dict_repr ----------------------------------------------
        # In: eax = dict ptr, ebx = key kind, ecx = value kind.
        # Out: eax = `{k: v, ...}`. Walks order_buf[0..len) (insertion order,
        # CPython 3.7+ ordering).
        # [ebp-4]=dict [ebp-8]=keykind [ebp-12]=valkind [ebp-16]=i
        # [ebp-20]=acc [ebp-24]=key
        self.label("_runtime_dict_repr")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], ebx", "mov [ebp-12], ecx")
        self.emitf(
            "mov eax, _runtime_lbrace_str",
            "call _runtime_str_concat_dup",
            "mov [ebp-20], eax",
            "mov dword [ebp-16], 0",
        )
        self.label("._dr_loop")
        self.emitf(
            "mov eax, [ebp-4]",
            f"mov ebx, [eax+{self.DICT_LEN_OFF}]",
            "mov ecx, [ebp-16]",
            "cmp ecx, ebx",
            "jge ._dr_done",
            f"mov edx, [eax+{self.DICT_ORDER_OFF}]",
            "mov eax, [edx+ecx*4]",  # key (4-byte pointer-width order slots)
            "mov [ebp-24], eax",
        )
        # separator if not the first entry
        self.emitf("cmp dword [ebp-16], 0", "jz ._dr_no_sep")
        self.emitf(
            "mov eax, [ebp-20]",
            "mov ebx, _runtime_comma_str",
            "call _runtime_str_concat",
            "mov [ebp-20], eax",
        )
        self.label("._dr_no_sep")
        # format key
        self.emitf(
            "mov eax, [ebp-24]",
            "mov ebx, [ebp-8]",
            "call _runtime_fmt_elem",
            "mov ebx, eax",
            "mov eax, [ebp-20]",
            "call _runtime_str_concat",
            "mov ebx, _runtime_colon_str",
            "call _runtime_str_concat",
            "mov [ebp-20], eax",
        )
        # fetch and format value via lookup_slot(dict, key)
        self.emitf(
            "mov eax, [ebp-4]",
            "mov ebx, [ebp-24]",
            "call _runtime_dict_lookup_slot",
            "mov eax, [eax+4]",  # value (4-byte field on this target)
            "mov ebx, [ebp-12]",
            "call _runtime_fmt_elem",
            "mov ebx, eax",
            "mov eax, [ebp-20]",
            "call _runtime_str_concat",
            "mov [ebp-20], eax",
        )
        self.emitf("inc dword [ebp-16]", "jmp ._dr_loop")
        self.label("._dr_done")
        self.emitf(
            "mov eax, [ebp-20]",
            "mov ebx, _runtime_rbrace_str",
            "call _runtime_str_concat",
            "leave",
            "ret",
        )

        # ---- _runtime_set_repr -----------------------------------------------
        # In: eax = set ptr (dict layout, keys only), ebx = element kind.
        # Out: eax = `{e0, e1, ...}` (or `set()` when empty).
        # [ebp-4]=set [ebp-8]=kind [ebp-12]=i [ebp-16]=acc
        # [ebp-20]=cap [ebp-24]=buf [ebp-28]=count
        self.label("_runtime_set_repr")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 32")
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], ebx")
        # empty set -> "set()"
        self.emitf(
            "mov eax, [ebp-4]",
            f"mov eax, [eax+{self.DICT_LEN_OFF}]",
            "test eax, eax",
            "jnz ._sr_build",
            "mov eax, _runtime_emptyset_str",
            "call _runtime_str_concat_dup",
            "leave",
            "ret",
        )
        self.label("._sr_build")
        self.emitf(
            "mov eax, _runtime_lbrace_str",
            "call _runtime_str_concat_dup",
            "mov [ebp-16], eax",
        )
        self.emitf(
            "mov eax, [ebp-4]",
            f"mov ebx, [eax+{self.DICT_CAP_OFF}]",
            "mov [ebp-20], ebx",
            f"mov ebx, [eax+{self.DICT_BUF_OFF}]",
            "mov [ebp-24], ebx",
            "mov dword [ebp-12], 0",
            "mov dword [ebp-28], 0",
        )
        self.label("._srp_loop")
        self.emitf("mov eax, [ebp-12]", "cmp eax, [ebp-20]", "jge ._srp_done")
        self.emitf(
            "mov eax, [ebp-24]",
            "mov ecx, [ebp-12]",
            "shl ecx, 4",  # DICT_SLOT_SIZE = 16 bytes (array stride, unchanged)
            "add eax, ecx",
            "mov edx, [eax]",
            "cmp edx, 1",
            "jbe ._srp_next",
        )
        self.emitf("mov eax, [ebp-28]", "test eax, eax", "jz ._srp_no_sep")
        self.emitf(
            "mov eax, [ebp-16]",
            "mov ebx, _runtime_comma_str",
            "call _runtime_str_concat",
            "mov [ebp-16], eax",
        )
        self.label("._srp_no_sep")
        self.emitf(
            "mov eax, [ebp-24]",
            "mov ecx, [ebp-12]",
            "shl ecx, 4",
            "add eax, ecx",
            "mov eax, [eax]",
            "mov ebx, [ebp-8]",
            "call _runtime_fmt_elem",
            "mov ebx, eax",
            "mov eax, [ebp-16]",
            "call _runtime_str_concat",
            "mov [ebp-16], eax",
            "inc dword [ebp-28]",
        )
        self.label("._srp_next")
        self.emitf("inc dword [ebp-12]", "jmp ._srp_loop")
        self.label("._srp_done")
        self.emitf(
            "mov eax, [ebp-16]",
            "mov ebx, _runtime_rbrace_str",
            "call _runtime_str_concat",
            "leave",
            "ret",
        )

        # ---- _runtime_range_list ---------------------------------------------
        # In: eax = start, ebx = stop, ecx = step. Out: eax = list[int] header.
        # Materializes range(start, stop, step). Two passes: count elements,
        # malloc exactly (header + count*4) once, then fill -- avoids
        # realloc so only the portable malloc helper is needed. step == 0
        # -> empty list. i386 port -- 4-byte pointer-width buffer slots.
        # [ebp-4]=start [ebp-8]=stop [ebp-12]=step [ebp-16]=count
        # [ebp-20]=hdr [ebp-24]=buf [ebp-28]=cur [ebp-32]=i
        self.label("_runtime_range_list")
        self.emitf("push ebp", "mov ebp, esp", "sub esp, 40")
        self.emitf("mov [ebp-4], eax", "mov [ebp-8], ebx", "mov [ebp-12], ecx")
        # Pass 1: count.
        self.emitf("mov dword [ebp-16], 0")
        self.emitf("mov eax, [ebp-4]", "mov [ebp-28], eax")  # cur = start
        self.label("._rl_cloop")
        self.emitf(
            "mov eax, [ebp-12]",
            "test eax, eax",
            "jz ._rl_cdone",  # step 0 -> empty
            "jg ._rl_cpos",
            "mov eax, [ebp-28]",
            "cmp eax, [ebp-8]",
            "jle ._rl_cdone",
            "jmp ._rl_ccount",
        )
        self.label("._rl_cpos")
        self.emitf("mov eax, [ebp-28]", "cmp eax, [ebp-8]", "jge ._rl_cdone")
        self.label("._rl_ccount")
        self.emitf(
            "inc dword [ebp-16]",
            "mov eax, [ebp-28]",
            "add eax, [ebp-12]",
            "mov [ebp-28], eax",
            "jmp ._rl_cloop",
        )
        self.label("._rl_cdone")
        # Allocate header (24) and buffer (count*4, min 4 bytes so malloc(0)
        # is safe).
        self._emit_malloc(self.LIST_HEADER)
        self.emitf("mov [ebp-20], eax")
        self.emitf(
            "mov eax, [ebp-16]",
            "shl eax, 2",
            "test eax, eax",
            "jnz ._rl_haspos",
            "mov eax, 4",
        )
        self.label("._rl_haspos")
        self._emit_libc_malloc_size_in_rax()
        self.emitf("mov [ebp-24], eax")
        # Fill: cur = start; i = 0.
        self.emitf("mov eax, [ebp-4]", "mov [ebp-28], eax", "mov dword [ebp-32], 0")
        self.label("._rl_floop")
        self.emitf("mov eax, [ebp-32]", "cmp eax, [ebp-16]", "jge ._rl_fdone")
        self.emitf(
            "mov eax, [ebp-24]",
            "mov ecx, [ebp-32]",
            "mov edx, [ebp-28]",
            "mov [eax+ecx*4], edx",
            "mov eax, [ebp-28]",
            "add eax, [ebp-12]",
            "mov [ebp-28], eax",
            "inc dword [ebp-32]",
            "jmp ._rl_floop",
        )
        self.label("._rl_fdone")
        # finalize header: cap = len = count; buf.
        self.emitf(
            "mov eax, [ebp-20]",
            "mov ecx, [ebp-16]",
            f"mov [eax+{self.LIST_CAP_OFF}], ecx",
            f"mov [eax+{self.LIST_LEN_OFF}], ecx",
            "mov ecx, [ebp-24]",
            f"mov [eax+{self.LIST_BUF_OFF}], ecx",
            "leave",
            "ret",
        )

        # _runtime_str_concat_dup: eax = src -> eax = "" + src (a fresh copy).
        # Lets callers seed an accumulator without aliasing a .rodata literal.
        self.label("_runtime_str_concat_dup")
        self.emitf(
            "mov ebx, eax",
            "mov eax, _runtime_empty_str",
            "jmp _runtime_str_concat",
        )

    def emit_print_impls(self) -> None:
        """i386 port of LinuxCodegen's own emit_print_impls -- the real
        top-level entry point generate_runtime_only()/gen.generate()
        actually calls, in turn calling emit_dict_runtime()/
        emit_string_runtime()/emit_exception_runtime().
        """
        if not self.use_runtime_lib:
            self.emit("section .bss")
            self.emit(f"itoa_str_buf: resb {self.itoa_buf_bytes}")
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
            self.emit_string_runtime()
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
        self.emit_string_runtime()
        self.emit_exception_runtime()
