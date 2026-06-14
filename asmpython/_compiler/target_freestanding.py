"""Freestanding (bare-metal) codegen — Multiboot1 flat binary.

The output is a single flat binary that can be loaded by QEMU directly:

    asmpython hello.py --target freestanding -o hello.bin
    qemu-system-x86_64 -kernel hello.bin

The binary begins with a Multiboot1 AOUT-kludge header so GRUB/QEMU
recognise it.  _start runs in 32-bit protected mode (as Multiboot1 requires),
switches the CPU to 64-bit long mode, then calls the compiled kernel_main.

print() outputs to the VGA text buffer at 0xB8000 (standard PC text mode).
No libc, no linker required — NASM -f bin produces the final binary.
"""

from __future__ import annotations

from .codegen import Codegen, FuncInfo
from . import ast_nodes as A

SYSV_ARG_REGS = ["rdi", "rsi", "rdx", "rcx", "r8", "r9"]

_HEAP_BYTES  = 1024 * 256   # 256 KB
_STACK_BYTES = 1024 * 64    # 64 KB kernel stack


class FreestandingCodegen(Codegen):
    target_name   = "FreestandingCodegen"
    section_text   = "section .text"
    section_data   = "section .data"
    section_rodata = "section .rodata"
    section_bss    = "section .bss nobits"
    label_main     = "kernel_main"

    # ------------------------------------------------------------------ ABI --

    def emit_externs(self) -> None:
        pass  # flat binary: everything is defined here

    def _arg_reg(self, i: int):
        return SYSV_ARG_REGS[i] if i < len(SYSV_ARG_REGS) else None

    def _int_arg_regs(self):
        return list(SYSV_ARG_REGS)

    def _needs_xmm_mirror_to_int(self):
        return False

    def _incoming_stack_arg_offset(self, stack_index: int) -> int:
        return 16 + 8 * stack_index

    def _caller_shadow_space(self) -> int:
        return 0

    def emit_call(self, target: str) -> None:
        self.emitf(f"call {target}")

    # --------------------------------------------------------- entry / exit --

    def emit_entry_prologue(self, info: FuncInfo) -> None:
        # kernel_main is called with no argv; sys.argv is always [].
        self.emitf("mov qword [rel _prog_argc], 0", "mov qword [rel _prog_argv], 0")
        self.emitf("push rbp", "mov rbp, rsp")
        frame = info.frame_size
        if frame % 16 != 0:
            frame += 16 - (frame % 16)
        info.frame_size = frame
        if frame:
            self.emitf(f"sub rsp, {frame}")

    def emit_entry_epilogue(self, info: FuncInfo) -> None:
        self.emit(".__halt:")
        self.emitf("hlt", "jmp .__halt")

    def emit_func_prologue(self, info: FuncInfo) -> None:
        self.emitf("push rbp", "mov rbp, rsp")
        frame = info.frame_size
        if frame % 16 != 0:
            frame += 16 - (frame % 16)
        info.frame_size = frame
        if frame:
            self.emitf(f"sub rsp, {frame}")

    def emit_func_epilogue(self, info: FuncInfo) -> None:
        self.emitf("mov rsp, rbp", "pop rbp", "ret")

    # ------------------------------------------------------ print helpers ---

    def _emit_print_int_no_newline(self) -> None:
        self.emitf("call _runtime_int_to_str", "mov rdi, rax", "call _runtime_print_str")

    def _emit_print_str_ptr_no_newline(self) -> None:
        self.emitf("mov rdi, rax", "call _runtime_print_str")

    def _emit_print_space(self) -> None:
        self.emitf("mov rdi, 32", "call _vga_putchar")

    def _emit_print_newline(self) -> None:
        self.emitf("mov rdi, 10", "call _vga_putchar")

    def _emit_print_float_no_newline(self) -> None:
        self.emitf("call _runtime_float_to_str", "mov rdi, rax", "call _runtime_print_str")

    # ------------------------------------------ string / conversion helpers --

    def _emit_strlen(self) -> None:
        self.emitf("mov rdi, rax", "call _runtime_strlen")

    def _emit_int_to_str(self) -> None:
        self.emitf("call _runtime_int_to_str")

    def _emit_str_to_int(self) -> None:
        self.emitf("mov rdi, rax", "call _runtime_atoi64")

    def _emit_str_to_int_base(self) -> None:
        self._emit_normalize_0b_prefix()
        self.emitf("mov rdx, rbx", "xor rsi, rsi", "mov rdi, rax",
                   "call _runtime_strtoll")

    def _emit_float_to_str(self) -> None:
        self.emitf("call _runtime_float_to_str")

    def _emit_str_to_float(self) -> None:
        self.emitf("mov rdi, rax", "call _runtime_strtod")

    def _emit_call_libc_double_double(self, fn: str) -> None:
        self.emitf(f"call _runtime_math_{fn}")

    def _emit_input_line(self) -> None:
        self.emitf("call _runtime_input")

    # ---------------------------------------------------- memory helpers ----

    def _emit_malloc(self, n: int) -> None:
        self.emitf(f"mov rdi, {n}", "call _runtime_malloc")

    def _emit_libc_malloc_size_in_rax(self) -> None:
        self.emitf("mov rdi, rax", "call _runtime_malloc")

    def _emit_libc_memset_zero(self) -> None:
        self.emitf("mov rdi, rax", "xor rsi, rsi", "mov rdx, rbx",
                   "call _runtime_memset")

    def _emit_libc_strcmp(self) -> None:
        self.emitf("mov rdi, rax", "mov rsi, rbx", "call _runtime_strcmp",
                   "movsxd rax, eax")

    def _emit_libc_strdup(self) -> None:
        self.emitf("mov rdi, rax", "call _runtime_strdup")

    def _emit_libc_strlen(self) -> None:
        self.emitf("mov rdi, rax", "call _runtime_strlen")

    def _emit_libc_memcpy(self) -> None:
        self.emitf("mov rdx, rcx", "mov rsi, rbx", "mov rdi, rax",
                   "call _runtime_memcpy")

    def _emit_libc_strstr(self) -> None:
        self.emitf("mov rsi, rbx", "mov rdi, rax", "call _runtime_strstr")

    def _emit_libc_free(self) -> None:
        self.emitf("mov rdi, rax", "call _runtime_free")

    def _emit_exit_one(self) -> None:
        self.emitf("call _runtime_panic")

    def _emit_call_setjmp(self, buf_off: int) -> None:
        self.emitf(f"lea rax, [rbp{buf_off:+d}]", "call _runtime_setjmp")

    def _emit_call_longjmp_with_buf_in_rax(self) -> None:
        self.emitf("mov rbx, 1", "call _runtime_longjmp")

    # -------------------------------------------- top-level code generation --

    def generate(self) -> str:
        self.emit("; asmpython freestanding kernel (Multiboot1, x86-64)")
        self.emit("; Boot: qemu-system-x86_64 -kernel <output.bin>")
        self.emit("")
        self.emit("ORG 0x100000")
        self.emit("BITS 32")
        self.emit("default rel")
        self.emit("")

        # 1. 32-bit boot stub (Multiboot1 header + long-mode switch)
        self._emit_boot_stub()

        # 2. 64-bit kernel code
        self.emit("")
        self.emit("BITS 64")
        self.label("start64")
        self._emit_kernel_init()

        # Compiled entry point (top-level module body = kernel_main)
        self.emit_entry()

        # User-defined functions
        for f in self.mod.funcs:
            self.emit_function(f)

        # Class methods (mangled names)
        for cls in self.mod.classes:
            for m in cls.methods:
                mangled = A.FuncDef(
                    name=self._method_symbol(cls.name, m.name),
                    params=list(m.params),
                    body=list(m.body),
                    pos=m.pos,
                    defaults=list(m.defaults),
                    param_types=list(m.param_types),
                    asm_body=m.asm_body,
                    asm_symbol=(
                        self._method_symbol(cls.name, m.name)
                        if m.asm_body is not None else None
                    ),
                )
                self.emit_function(mangled)

        # Assembly packages
        self.emit_asm_packages()

        # 3. Runtime helpers + data + BSS
        self.emit_print_impls()
        self.emit_data_sections()

        return "\n".join(self.lines) + "\n"

    # ---------------------------------------------------------------- boot --

    def _emit_boot_stub(self) -> None:
        """Multiboot1 AOUT header, GDT, page tables, and 32-bit _start."""
        # Header must be within the first 8 KB of the file.
        self.emit("align 4")
        self.label("_mb_header")
        self.emitf("dd 0x1BADB002")
        self.emitf("dd 0x00010003")                   # flags: align | meminfo | aout
        self.emitf("dd -(0x1BADB002 + 0x00010003)")   # checksum
        # AOUT kludge fields (required by flags bit 16)
        self.emitf("dd _mb_header")                   # header_addr
        self.emitf("dd 0x100000")                     # load_addr
        self.emitf("dd _load_end")                    # load_end_addr (forward ref)
        self.emitf("dd _bss_end")                     # bss_end_addr  (forward ref)
        self.emitf("dd _start")                       # entry_addr

        # GDT: null / 64-bit code (sel 0x08) / data (sel 0x10)
        self.emit("align 8")
        self.label("_gdt")
        self.emitf("dq 0x0000000000000000")
        self.emitf("dq 0x00AF9A000000FFFF")   # 64-bit code, ring-0
        self.emitf("dq 0x00CF92000000FFFF")   # data, ring-0
        self.label("_gdt_end")
        self.label("_gdt_ptr")
        self.emitf("dw _gdt_end - _gdt - 1")
        self.emitf("dd _gdt")

        # Identity-map first 16 MB via 2 MB huge pages (PML4→PDPT→PD)
        self.emit("align 4096")
        self.label("_pml4")
        self.emitf("dq _pdpt + 3")
        self.emitf("times 511 dq 0")
        self.label("_pdpt")
        self.emitf("dq _pd + 3")
        self.emitf("times 511 dq 0")
        self.label("_pd")
        for i in range(8):
            self.emitf(f"dq 0x{i * 0x200000:07X} | 0x83")  # present+writable+huge
        self.emitf("times 504 dq 0")

        # Small 32-bit boot stack (only used during the mode switch)
        self.emit("align 16")
        self.label("_boot_stack_mem")
        self.emitf("times 4096 db 0")
        self.label("_boot_stack_top")

        # Multiboot1 entry point (32-bit protected mode)
        self.emit("global _start")
        self.label("_start")
        self.emitf("cli")
        self.emitf("mov esp, _boot_stack_top")
        self.emitf("lgdt [_gdt_ptr]")
        # Enable PAE (CR4 bit 5)
        self.emitf("mov eax, cr4", "or eax, 0x20", "mov cr4, eax")
        # Load PML4 address into CR3
        self.emitf("mov eax, _pml4", "mov cr3, eax")
        # Enable long mode (EFER.LME = 1)
        self.emitf("mov ecx, 0xC0000080", "rdmsr", "or eax, 0x100", "wrmsr")
        # Enable paging + protected mode
        self.emitf("mov eax, cr0", "or eax, 0x80000001", "mov cr0, eax")
        # Far jump into 64-bit code segment
        self.emitf("jmp dword 0x08:start64")

    def _emit_kernel_init(self) -> None:
        """64-bit init: reload segments, set up stack and heap, clear screen."""
        self.emitf("mov ax, 0x10")
        self.emitf("mov ds, ax", "mov es, ax", "mov ss, ax",
                   "mov fs, ax", "mov gs, ax")
        self.emitf("xor eax, eax")
        self.emitf("mov rsp, _kernel_stack_top")
        self.emitf("lea rax, [rel _heap_data]", "mov [rel _heap_ptr], rax")
        self.emitf("call _vga_clear")
        self.emitf("call _serial_init")
        self.emitf("call kernel_main")
        self.label("._ki_halt")
        self.emitf("hlt", "jmp ._ki_halt")

    # ----------------------------------------------- emit_print_impls ------

    def emit_print_impls(self) -> None:  # noqa: C901
        # ---- BSS state (not written to binary; zeroed by Multiboot loader) --
        self.emit("")
        self.emit("section .bss nobits")
        self.emit("_vga_col:         resq 1")
        self.emit("_vga_row:         resq 1")
        self.emit("_vga_attr:        resb 1")   # current attribute byte (fg|bg<<4)
        self.emit("_heap_ptr:        resq 1")
        self.emit("itoa_str_buf:     resb 64")
        self.emit("ftoa_str_buf:     resb 64")
        self.emit("input_buf:        resb 4")    # input stub; not supported
        self.emit(f"_heap_data:       resb {_HEAP_BYTES}")
        self.emit("_heap_data_end:")
        self.emit(f"_kernel_stack:    resb {_STACK_BYTES}")
        self.emit("_kernel_stack_top:")

        # Establish .rodata encounter order BEFORE .data so that when base-class
        # helpers (emit_dict_runtime etc.) later write to .rodata, those bytes
        # land before .data in the flat binary — keeping _load_end truly last.
        self.emit("")
        self.emit("section .rodata")

        # ---- Static data strings ------------------------------------------
        self.emit("")
        self.emit("section .data")
        self.emit('_str_oom:    db "OUT OF MEMORY",10,0')
        self.emit('_str_panic:  db "KERNEL PANIC",10,0')
        self.emit('_str_nan:    db "nan",0')
        self.emit('_str_inf:    db "inf",0')
        self.emit('_str_neginf: db "-inf",0')
        self.emit("_flt_1e6:    dq 1000000.0")
        self.emit("_flt_10:     dq 10.0")

        # ---- Runtime functions (64-bit, in .text) -------------------------
        self.emit("")
        self.emit("section .text")

        # --- _vga_clear: fill 80x25 with space (attr 0x07), reset cursor ---
        self.label("_vga_clear")
        self.emitf("push rbx", "mov rbx, 0xB8000", "mov ecx, 2000")
        self.label("._vc_loop")
        self.emitf("mov word [rbx], 0x0720", "add rbx, 2",
                   "dec ecx", "jnz ._vc_loop")
        self.emitf("xor rax, rax",
                   "mov [rel _vga_col], rax", "mov [rel _vga_row], rax")
        self.emitf("pop rbx", "ret")

        # --- _vga_putchar(rdi = char) --------------------------------------
        self.label("_vga_putchar")
        self.emitf("push rbx", "push rcx", "push rdx")
        self.emitf("cmp rdi, 10", "je ._vp_nl")
        # Normal char
        self.emitf(
            "mov rax, [rel _vga_row]",
            "imul rax, 80",
            "add rax, [rel _vga_col]",
            "shl rax, 1",
            "add rax, 0xB8000",
            "mov dl, dil",
            "mov byte [rax], dl",
        )
        # Use _vga_attr if non-zero, else default 0x07 (light-grey on black)
        self.emitf("movzx ecx, byte [rel _vga_attr]",
                   "test ecx, ecx", "jnz ._vp_attr",
                   "mov ecx, 0x07")
        self.label("._vp_attr")
        self.emitf("mov byte [rax+1], cl",
            "inc qword [rel _vga_col]",
            "cmp qword [rel _vga_col], 80",
            "jl ._vp_done",
        )
        self.label("._vp_nl")
        self.emitf(
            "mov qword [rel _vga_col], 0",
            "inc qword [rel _vga_row]",
            "cmp qword [rel _vga_row], 25",
            "jl ._vp_done",
        )
        # Scroll: copy rows 1-24 up to 0-23
        self.emitf("mov rbx, 0xB8000", "mov ecx, 24 * 80")
        self.label("._vp_sc")
        self.emitf("movzx edx, word [rbx + 160]", "mov [rbx], dx",
                   "add rbx, 2", "dec ecx", "jnz ._vp_sc")
        # Clear last row
        self.emitf("mov ecx, 80")
        self.label("._vp_clr")
        self.emitf("mov word [rbx], 0x0720", "add rbx, 2",
                   "dec ecx", "jnz ._vp_clr")
        self.emitf("mov qword [rel _vga_row], 24")
        self.label("._vp_done")
        # Mirror to COM1 serial; rdi still holds the original char throughout.
        # For \n send \r\n so terminal emulators render the break correctly.
        self.emitf("cmp rdi, 10", "jne ._vp_serial")
        self.emitf("push rdi", "mov rdi, 13", "call _serial_putchar", "pop rdi")
        self.label("._vp_serial")
        self.emitf("call _serial_putchar")
        self.emitf("pop rdx", "pop rcx", "pop rbx", "ret")

        # --- _serial_init: set COM1 (0x3F8) to 38400 8N1 --------------------
        self.label("_serial_init")
        self.emitf(
            "mov dx, 0x3F9", "mov al, 0x00", "out dx, al",  # disable interrupts
            "mov dx, 0x3FB", "mov al, 0x80", "out dx, al",  # DLAB on
            "mov dx, 0x3F8", "mov al, 0x03", "out dx, al",  # baud divisor lo (38400)
            "mov dx, 0x3F9", "mov al, 0x00", "out dx, al",  # baud divisor hi
            "mov dx, 0x3FB", "mov al, 0x03", "out dx, al",  # 8N1, DLAB off
            "mov dx, 0x3FC", "mov al, 0x03", "out dx, al",  # RTS+DTR on
            "ret",
        )

        # --- _serial_putchar(rdi = char) — spin-waits TX empty, then sends --
        self.label("_serial_putchar")
        self.emitf("push rdx")
        self.label("._sp_wait")
        self.emitf("mov dx, 0x3FD", "in al, dx",  # line status register
                   "and al, 0x20", "jz ._sp_wait")  # bit5 = TX empty
        self.emitf("mov dx, 0x3F8", "mov al, dil", "out dx, al")
        self.emitf("pop rdx", "ret")

        # --- _runtime_print_str(rdi = null-terminated ptr) ----------------
        # _vga_putchar handles both VGA and COM1 serial output.
        self.label("_runtime_print_str")
        self.emitf("push rbx", "mov rbx, rdi")
        self.label("._rps_loop")
        self.emitf("movzx edi, byte [rbx]", "test edi, edi", "jz ._rps_done",
                   "call _vga_putchar", "inc rbx", "jmp ._rps_loop")
        self.label("._rps_done")
        self.emitf("pop rbx", "ret")

        # --- _runtime_panic: print panic message and halt ------------------
        self.label("_runtime_panic")
        self.emitf("lea rdi, [rel _str_panic]", "call _runtime_print_str")
        self.label("._panic_halt")
        self.emitf("hlt", "jmp ._panic_halt")

        # --- _runtime_input: stub — returns empty string -------------------
        self.label("_runtime_input")
        self.emitf("lea rax, [rel input_buf]",
                   "mov byte [rax], 0", "ret")

        # --- _runtime_int_to_str: rax=int64 -> rax=ptr to itoa_str_buf ----
        self.label("_runtime_int_to_str")
        self.emitf(
            "push rbx", "push rcx", "push rdx", "push rdi",
            "mov rbx, rax",
            "test rax, rax",
            "jns ._its_abs",
            "neg rax",
        )
        self.label("._its_abs")
        self.emitf(
            "lea rdi, [rel itoa_str_buf]",
            "add rdi, 62",
            "mov byte [rdi+1], 0",
            "mov rcx, 10",
        )
        self.label("._its_loop")
        self.emitf(
            "xor rdx, rdx",
            "div rcx",
            "add dl, 0x30",
            "mov [rdi], dl",
            "dec rdi",
            "test rax, rax",
            "jnz ._its_loop",
        )
        self.emitf("test rbx, rbx", "jns ._its_ret",
                   "mov byte [rdi], '-'", "dec rdi")
        self.label("._its_ret")
        self.emitf("lea rax, [rdi+1]",
                   "pop rdi", "pop rdx", "pop rcx", "pop rbx", "ret")

        # --- _runtime_float_to_str: xmm0=double -> rax=ptr to ftoa_str_buf -
        # Strategy: scale by 1e6, split into int + frac parts, format.
        self.label("_runtime_float_to_str")
        self.emitf("push rbx", "push r12", "push r13", "push r14")
        # NaN check
        self.emitf("ucomisd xmm0, xmm0", "jnp ._fts_not_nan",
                   "lea rax, [rel _str_nan]", "jmp ._fts_exit")
        self.label("._fts_not_nan")
        self.emitf("lea r12, [rel ftoa_str_buf]")
        # Sign
        self.emitf("pxor xmm2, xmm2", "ucomisd xmm0, xmm2")
        self.emitf("jae ._fts_pos")
        self.emitf("mov byte [r12], '-'", "inc r12",
                   "pxor xmm1, xmm1", "subsd xmm1, xmm0", "movapd xmm0, xmm1")
        self.label("._fts_pos")
        # Scale by 1e6 and truncate to int64
        self.emitf("mulsd xmm0, [rel _flt_1e6]",
                   "cvtsd2si rax, xmm0")
        # Split int_part / frac_part
        self.emitf("xor rdx, rdx", "mov rbx, 1000000",
                   "div rbx")           # rax = int_part, rdx = frac_part
        self.emitf("mov r13, rax", "mov r14, rdx")
        # Format integer part
        self.emitf("push r12", "push r14",
                   "mov rax, r13", "call _runtime_int_to_str",
                   "mov rsi, rax", "pop r14", "pop r12")
        self.label("._fts_ic")
        self.emitf("movzx ecx, byte [rsi]", "test ecx, ecx", "jz ._fts_dot",
                   "mov [r12], cl", "inc r12", "inc rsi", "jmp ._fts_ic")
        self.label("._fts_dot")
        self.emitf("mov byte [r12], '.'", "inc r12")
        # Format fractional part (6 digits, zero-padded on the left)
        self.emitf("push r12", "push r14",
                   "mov rax, r14", "call _runtime_int_to_str",
                   "mov rsi, rax",
                   "mov rdi, rsi", "call _runtime_strlen",
                   "pop r14", "pop r12")
        # rax = digit count; prepend (6-rax) zeros
        self.emitf("mov rcx, 6", "sub rcx, rax", "jle ._fts_no_pad")
        self.label("._fts_pad")
        self.emitf("mov byte [r12], '0'", "inc r12", "dec rcx", "jnz ._fts_pad")
        self.label("._fts_no_pad")
        self.label("._fts_fc")
        self.emitf("movzx ecx, byte [rsi]", "test ecx, ecx", "jz ._fts_trim",
                   "mov [r12], cl", "inc r12", "inc rsi", "jmp ._fts_fc")
        # Trim trailing zeros (keep at least one digit after '.')
        self.label("._fts_trim")
        self.emitf("dec r12")
        self.label("._fts_trz")
        self.emitf("cmp byte [r12], '0'", "jne ._fts_trz_done",
                   "cmp byte [r12-1], '.'", "je ._fts_trz_done",
                   "dec r12", "jmp ._fts_trz")
        self.label("._fts_trz_done")
        self.emitf("inc r12", "mov byte [r12], 0",
                   "lea rax, [rel ftoa_str_buf]")
        self.label("._fts_exit")
        self.emitf("pop r14", "pop r13", "pop r12", "pop rbx", "ret")

        # --- _runtime_strlen(rdi=ptr) -> rax=length -----------------------
        self.label("_runtime_strlen")
        self.emitf("push rcx", "push rdi",
                   "xor al, al", "mov rcx, -1", "repne scasb",
                   "not rcx", "dec rcx", "mov rax, rcx",
                   "pop rdi", "pop rcx", "ret")

        # --- _runtime_strcmp(rdi=a, rsi=b) -> eax (signed) ----------------
        self.label("_runtime_strcmp")
        self.emitf("push rbx", "push rcx")
        self.label("._sc_loop")
        self.emitf("movzx eax, byte [rdi]", "movzx ecx, byte [rsi]",
                   "cmp al, cl", "jne ._sc_diff",
                   "test al, al", "jz ._sc_eq",
                   "inc rdi", "inc rsi", "jmp ._sc_loop")
        self.label("._sc_diff")
        self.emitf("sub eax, ecx")
        self.label("._sc_eq")
        self.emitf("pop rcx", "pop rbx", "ret")

        # --- _runtime_strdup(rdi=src) -> rax=copy -------------------------
        self.label("_runtime_strdup")
        self.emitf("push rbx", "push r12", "mov r12, rdi",
                   "call _runtime_strlen",
                   "inc rax", "mov rdi, rax",
                   "push rax",
                   "call _runtime_malloc",
                   "pop rdx",
                   "mov rdi, rax", "mov rsi, r12",
                   "call _runtime_memcpy",   # rax = dst
                   "pop r12", "pop rbx", "ret")

        # --- _runtime_strstr(rdi=hay, rsi=ndl) -> rax=ptr or 0 -----------
        self.label("_runtime_strstr")
        self.emitf("push rbx", "push r12", "push r13", "push r14",
                   "mov r12, rdi", "mov r13, rsi")
        self.emitf("mov rdi, r13", "call _runtime_strlen", "mov rbx, rax")  # needle len
        self.emitf("mov rdi, r12", "call _runtime_strlen", "mov r14, rax")  # hay len
        self.emitf("test rbx, rbx", "jz ._ss_hay")    # empty needle → haystack
        self.label("._ss_outer")
        self.emitf("cmp r14, rbx", "jb ._ss_none")
        self.emitf("mov rcx, rbx", "mov rdi, r12", "mov rsi, r13")
        self.label("._ss_cmp")
        self.emitf("movzx eax, byte [rdi]", "movzx edx, byte [rsi]",
                   "cmp al, dl", "jne ._ss_miss",
                   "inc rdi", "inc rsi", "dec rcx", "jnz ._ss_cmp",
                   "mov rax, r12", "jmp ._ss_ret")
        self.label("._ss_miss")
        self.emitf("inc r12", "dec r14", "jmp ._ss_outer")
        self.label("._ss_none")
        self.emitf("xor rax, rax", "jmp ._ss_ret")
        self.label("._ss_hay")
        self.emitf("mov rax, r12")
        self.label("._ss_ret")
        self.emitf("pop r14", "pop r13", "pop r12", "pop rbx", "ret")

        # --- _runtime_malloc(rdi=size) -> rax=ptr [8-byte header] ---------
        self.label("_runtime_malloc")
        self.emitf("push rbx", "push rcx",
                   "add rdi, 7", "and rdi, ~7",     # align to 8
                   "mov rax, [rel _heap_ptr]",
                   "mov [rax], rdi",                # store size in header
                   "mov rbx, rax",
                   "add rbx, 8", "add rbx, rdi",   # next free ptr
                   "lea rcx, [rel _heap_data_end]",
                   "cmp rbx, rcx", "jae ._ma_oom",
                   "mov [rel _heap_ptr], rbx",
                   "add rax, 8",                    # return ptr past header
                   "pop rcx", "pop rbx", "ret")
        self.label("._ma_oom")
        self.emitf("lea rdi, [rel _str_oom]", "call _runtime_print_str",
                   "call _runtime_panic")

        # --- _runtime_realloc(rdi=old, rsi=new_size) -> rax=ptr -----------
        self.label("_runtime_realloc")
        self.emitf("push rbx", "push r12", "push r13",
                   "mov r12, rdi", "mov r13, rsi",
                   "mov rdi, r13", "call _runtime_malloc",
                   "test r12, r12", "jz ._ra_done",
                   "mov rcx, [r12-8]",     # old size from header
                   "cmp rcx, r13", "jbe ._ra_cpy",
                   "mov rcx, r13")
        self.label("._ra_cpy")
        self.emitf("push rax",
                   "mov rdi, rax", "mov rsi, r12", "mov rdx, rcx",
                   "call _runtime_memcpy",
                   "pop rax")
        self.label("._ra_done")
        self.emitf("pop r13", "pop r12", "pop rbx", "ret")

        # --- _runtime_free(rdi=ptr) → noop --------------------------------
        self.label("_runtime_free")
        self.emitf("ret")

        # --- _runtime_memset(rdi=ptr, rsi=val, rdx=n) -> rax=ptr ----------
        self.label("_runtime_memset")
        self.emitf("push rbx", "mov rbx, rdi",
                   "test rdx, rdx", "jz ._ms_done",
                   "mov al, sil", "mov rcx, rdx", "rep stosb")
        self.label("._ms_done")
        self.emitf("mov rax, rbx", "pop rbx", "ret")

        # --- _runtime_memcpy(rdi=dst, rsi=src, rdx=n) -> rax=dst ----------
        self.label("_runtime_memcpy")
        self.emitf("push rbx", "mov rbx, rdi",
                   "test rdx, rdx", "jz ._mc_done",
                   "mov rcx, rdx", "rep movsb")
        self.label("._mc_done")
        self.emitf("mov rax, rbx", "pop rbx", "ret")

        # --- _runtime_atoi64(rdi=str) -> rax=int64 ------------------------
        self.label("_runtime_atoi64")
        self.emitf("push rbx", "push rcx", "push rsi",
                   "mov rsi, rdi", "xor rax, rax", "xor rbx, rbx")
        self.label("._ai_ws")
        self.emitf("movzx ecx, byte [rsi]",
                   "cmp cl, ' '", "jne ._ai_sgn", "inc rsi", "jmp ._ai_ws")
        self.label("._ai_sgn")
        self.emitf("cmp cl, '-'", "jne ._ai_loop",
                   "inc rbx", "inc rsi", "movzx ecx, byte [rsi]")
        self.label("._ai_loop")
        self.emitf("cmp cl, '0'", "jb ._ai_done",
                   "cmp cl, '9'", "ja ._ai_done",
                   "imul rax, 10", "sub cl, '0'", "movzx ecx, cl",
                   "add rax, rcx", "inc rsi", "movzx ecx, byte [rsi]",
                   "jmp ._ai_loop")
        self.label("._ai_done")
        self.emitf("test rbx, rbx", "jz ._ai_ret", "neg rax")
        self.label("._ai_ret")
        self.emitf("pop rsi", "pop rcx", "pop rbx", "ret")

        # --- _runtime_strtoll(rdi=str, rsi=ignored, rdx=base) -> rax ------
        self.label("_runtime_strtoll")
        self.emitf("push rbx", "push rcx", "push r12", "push r13",
                   "mov r12, rdi", "mov r13, rdx",
                   "xor rbx, rbx")
        self.label("._stl_ws")
        self.emitf("movzx eax, byte [r12]",
                   "cmp al, ' '", "jne ._stl_sgn", "inc r12", "jmp ._stl_ws")
        self.label("._stl_sgn")
        self.emitf("cmp al, '-'", "jne ._stl_auto",
                   "inc rbx", "inc r12", "movzx eax, byte [r12]")
        self.label("._stl_auto")
        # If base==0, auto-detect
        self.emitf("test r13, r13", "jnz ._stl_main",
                   "mov r13, 10",
                   "cmp al, '0'", "jne ._stl_main",
                   "movzx ecx, byte [r12+1]",
                   "or cl, 0x20",
                   "cmp cl, 'x'", "je ._stl_hex",
                   "cmp cl, 'b'", "je ._stl_bin",
                   "mov r13, 8", "jmp ._stl_main")
        self.label("._stl_hex")
        self.emitf("mov r13, 16", "add r12, 2", "movzx eax, byte [r12]",
                   "jmp ._stl_main")
        self.label("._stl_bin")
        self.emitf("mov r13, 2", "add r12, 2", "movzx eax, byte [r12]")
        self.label("._stl_main")
        self.emitf("xor rdx, rdx")  # accumulator
        self.label("._stl_loop")
        # Digit value: '0'-'9' → 0-9, 'a'-'f'/'A'-'F' → 10-15
        self.emitf("movzx eax, byte [r12]",
                   "cmp al, '0'", "jb ._stl_done",
                   "cmp al, '9'", "jbe ._stl_num")
        self.emitf("or al, 0x20",   # lowercase
                   "cmp al, 'a'", "jb ._stl_done",
                   "cmp al, 'f'", "ja ._stl_done",
                   "sub al, 'a' - 10", "jmp ._stl_digit")
        self.label("._stl_num")
        self.emitf("sub al, '0'")
        self.label("._stl_digit")
        self.emitf("movzx eax, al",
                   "cmp rax, r13", "jae ._stl_done",  # digit >= base
                   "imul rdx, r13", "add rdx, rax",
                   "inc r12", "jmp ._stl_loop")
        self.label("._stl_done")
        self.emitf("mov rax, rdx",
                   "test rbx, rbx", "jz ._stl_ret", "neg rax")
        self.label("._stl_ret")
        self.emitf("pop r13", "pop r12", "pop rcx", "pop rbx", "ret")

        # --- _runtime_strtod(rdi=str) -> xmm0=double ----------------------
        self.label("_runtime_strtod")
        self.emitf("push rbx", "push rcx", "push rsi",
                   "push r12",
                   "mov r12, rdi", "xor rbx, rbx")
        self.label("._sfd_sgn")
        self.emitf("movzx eax, byte [r12]",
                   "cmp al, '-'", "jne ._sfd_int",
                   "inc rbx", "inc r12", "movzx eax, byte [r12]")
        self.label("._sfd_int")
        self.emitf("xor rdx, rdx")
        self.label("._sfd_iloop")
        self.emitf("movzx eax, byte [r12]",
                   "cmp al, '0'", "jb ._sfd_dot",
                   "cmp al, '9'", "ja ._sfd_dot",
                   "imul rdx, 10", "sub al, '0'", "movzx eax, al",
                   "add rdx, rax", "inc r12", "jmp ._sfd_iloop")
        self.label("._sfd_dot")
        self.emitf("cvtsi2sd xmm0, rdx")
        self.emitf("movzx eax, byte [r12]",
                   "cmp al, '.'", "jne ._sfd_done", "inc r12")
        # Fractional part
        self.emitf("pxor xmm1, xmm1", "movsd xmm3, [rel _flt_10]")
        self.label("._sfd_floop")
        self.emitf("movzx eax, byte [r12]",
                   "cmp al, '0'", "jb ._sfd_frac_done",
                   "cmp al, '9'", "ja ._sfd_frac_done",
                   "sub al, '0'", "movzx eax, al",
                   "cvtsi2sd xmm4, rax", "divsd xmm4, xmm3",
                   "addsd xmm1, xmm4", "mulsd xmm3, [rel _flt_10]",
                   "inc r12", "jmp ._sfd_floop")
        self.label("._sfd_frac_done")
        self.emitf("addsd xmm0, xmm1")
        self.label("._sfd_done")
        self.emitf("test rbx, rbx", "jz ._sfd_ret",
                   "pxor xmm1, xmm1", "subsd xmm1, xmm0", "movapd xmm0, xmm1")
        self.label("._sfd_ret")
        self.emitf("pop r12", "pop rsi", "pop rcx", "pop rbx", "ret")

        # --- Math stubs (libm not available; fmod and sqrt work) -----------
        self.label("_runtime_math_fmod")
        self.emitf("movapd xmm2, xmm0", "divsd xmm2, xmm1",
                   "cvttsd2si rax, xmm2", "cvtsi2sd xmm2, rax",
                   "mulsd xmm2, xmm1", "subsd xmm0, xmm2", "ret")
        self.label("_runtime_math_sqrt")
        self.emitf("sqrtsd xmm0, xmm0", "ret")
        for _stub in ("sin", "cos", "tan", "asin", "acos", "atan",
                      "atan2", "exp", "log", "log2", "log10",
                      "pow", "ceil", "floor", "round", "abs"):
            self.label(f"_runtime_math_{_stub}")
            self.emitf("xorpd xmm0, xmm0", "ret")

        # --- List append / pop (same layout as hosted targets) -------------
        self.label("_runtime_list_append")
        self.emitf("push rbp", "mov rbp, rsp", "sub rsp, 32",
                   "mov [rbp-8], rax", "mov [rbp-16], rbx")
        self.emitf("mov rcx, [rax+8]", "cmp rcx, [rax]", "jl ._la_store")
        self.emitf("mov rcx, [rax]", "shl rcx, 1",
                   "cmp rcx, 4", "jge ._la_grow", "mov rcx, 4")
        self.label("._la_grow")
        self.emitf("mov [rbp-24], rcx",
                   "shl rcx, 3",
                   "mov rsi, rcx",
                   "mov rax, [rbp-8]",
                   "mov rdi, [rax+16]",
                   "call _runtime_realloc")
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

        # list_del(header_in_rax, index_in_rbx): remove element at index,
        # shifting later elements down by one slot and decrementing length.
        # Negative indices are normalized relative to length.
        self.label("_runtime_list_del")
        self.emitf("mov rcx, [rax+8]",  # length
                   "test rbx, rbx",
                   "jns ._ld_pos",
                   "add rbx, rcx")
        self.label("._ld_pos")
        self.emitf("mov rdx, [rax+16]")  # buffer ptr
        self.label("._ld_loop")
        self.emitf("lea r8, [rbx+1]",
                   "cmp r8, rcx",
                   "jge ._ld_done",
                   "mov r9, [rdx+r8*8]",
                   "mov [rdx+rbx*8], r9",
                   "mov rbx, r8",
                   "jmp ._ld_loop")
        self.label("._ld_done")
        self.emitf("dec qword [rax+8]", "ret")

        # Shared runtimes (dict, string methods, exceptions)
        self.emit_dict_runtime()
        self.emit_string_runtime()
        self.emit_exception_runtime()

        # --- Windows-ABI shims for shared-runtime direct `call` sites --------
        # emit_dict_runtime / emit_string_runtime call malloc/memcpy/etc using
        # Windows x64 ABI (first arg in RCX).  These thunks convert to SysV.
        self.label("malloc")
        self.emitf("mov rdi, rcx", "jmp _runtime_malloc")
        self.label("realloc")
        self.emitf("mov rdi, rcx", "mov rsi, rdx", "jmp _runtime_realloc")
        self.label("free")
        self.emitf("ret")   # bump allocator never frees
        self.label("strlen")
        self.emitf("mov rdi, rcx", "jmp _runtime_strlen")
        self.label("strcmp")
        self.emitf("mov rdi, rcx", "mov rsi, rdx", "jmp _runtime_strcmp")
        self.label("strdup")
        self.emitf("mov rdi, rcx", "jmp _runtime_strdup")
        self.label("strstr")
        self.emitf("mov rdi, rcx", "mov rsi, rdx", "jmp _runtime_strstr")
        self.label("memset")
        self.emitf("mov rdi, rcx", "mov rsi, rdx", "mov rdx, r8",
                   "jmp _runtime_memset")
        self.label("memcpy")
        self.emitf("mov rdi, rcx", "mov rsi, rdx", "mov rdx, r8",
                   "jmp _runtime_memcpy")
        self.label("sprintf")
        self.emitf("xor rax, rax", "ret")   # unused; stub
        self.label("exit")
        self.emitf("jmp _runtime_panic")

        # ---- asmlib.hardware runtime symbols --------------------------------
        # Port I/O
        self.label("_hw_in_byte")
        self.emitf("mov dx, di", "in al, dx", "movzx rax, al", "ret")
        self.label("_hw_out_byte")
        self.emitf("mov dx, di", "mov al, sil", "out dx, al", "xor rax, rax", "ret")
        self.label("_hw_in_word")
        self.emitf("mov dx, di", "in ax, dx", "movzx rax, ax", "ret")
        self.label("_hw_out_word")
        self.emitf("mov dx, di", "mov ax, si", "out dx, ax", "xor rax, rax", "ret")
        self.label("_hw_in_dword")
        self.emitf("mov dx, di", "in eax, dx", "mov eax, eax", "ret")
        self.label("_hw_out_dword")
        self.emitf("mov dx, di", "mov eax, esi", "out dx, eax", "xor rax, rax", "ret")

        # MMIO — treat address as a pointer
        self.label("_hw_mmio_read8")
        self.emitf("movzx rax, byte [rdi]", "ret")
        self.label("_hw_mmio_write8")
        self.emitf("mov byte [rdi], sil", "xor rax, rax", "ret")
        self.label("_hw_mmio_read32")
        self.emitf("mov eax, dword [rdi]", "ret")
        self.label("_hw_mmio_write32")
        self.emitf("mov dword [rdi], esi", "xor rax, rax", "ret")

        # CPU utilities
        self.label("_hw_rdtsc")
        self.emitf("rdtsc", "shl rdx, 32", "or rax, rdx", "ret")
        self.label("_hw_cpuid")
        self.emitf("push rbx", "mov eax, edi", "cpuid",
                   "pop rbx", "movsx rax, eax", "ret")
        self.label("_hw_halt")
        self.emitf("hlt", "xor rax, rax", "ret")
        self.label("_hw_cli")
        self.emitf("cli", "xor rax, rax", "ret")
        self.label("_hw_sti")
        self.emitf("sti", "xor rax, rax", "ret")

        # rdrand — retry until the CPU reports a valid random value (CF=1).
        self.label("_hw_rdrand")
        self.label("._rdr_retry")
        self.emitf("rdrand rax", "jnc ._rdr_retry", "ret")

        # io_wait — write to the unused diagnostic port 0x80 for a short delay.
        self.label("_hw_io_wait")
        self.emitf("xor eax, eax", "out 0x80, al", "xor rax, rax", "ret")

        # Control registers.
        self.label("_hw_read_cr0")
        self.emitf("mov rax, cr0", "ret")
        self.label("_hw_read_cr2")
        self.emitf("mov rax, cr2", "ret")
        self.label("_hw_read_cr3")
        self.emitf("mov rax, cr3", "ret")
        self.label("_hw_read_cr4")
        self.emitf("mov rax, cr4", "ret")
        self.label("_hw_write_cr3")
        self.emitf("mov cr3, rdi", "xor rax, rax", "ret")

        # MSRs: read_msr(index)->value ; write_msr(index, value).
        self.label("_hw_read_msr")
        self.emitf("mov ecx, edi", "rdmsr",
                   "shl rdx, 32", "or rax, rdx", "ret")
        self.label("_hw_write_msr")
        self.emitf("mov ecx, edi",
                   "mov rax, rsi", "mov rdx, rsi", "shr rdx, 32",
                   "wrmsr", "xor rax, rax", "ret")

        # invlpg(addr) — flush one TLB entry.
        self.label("_hw_invlpg")
        self.emitf("invlpg [rdi]", "xor rax, rax", "ret")

        # lidt(ptr) — load IDTR from a 6-byte (limit:base) descriptor.
        self.label("_hw_lidt")
        self.emitf("lidt [rdi]", "xor rax, rax", "ret")

        # PIC (8259A) — master at 0x20/0x21, slave at 0xA0/0xA1
        self.label("_hw_pic_eoi")
        self.emitf("cmp rdi, 8", "jl ._piceoi_master",
                   "mov al, 0x20", "out 0xA0, al")
        self.label("._piceoi_master")
        self.emitf("mov al, 0x20", "out 0x20, al", "xor rax, rax", "ret")

        # _hw_pic_mask(irq): set bit (1<<irq) in the appropriate IMR port
        self.label("_hw_pic_mask")
        self.emitf("push rcx", "push rdx",
                   "cmp rdi, 8", "jge ._picmsk_slave",
                   "mov cl, dil", "mov dl, 1", "shl dl, cl",
                   "in al, 0x21", "or al, dl", "out 0x21, al",
                   "pop rdx", "pop rcx", "xor rax, rax", "ret")
        self.label("._picmsk_slave")
        self.emitf("sub edi, 8",
                   "mov cl, dil", "mov dl, 1", "shl dl, cl",
                   "in al, 0xA1", "or al, dl", "out 0xA1, al",
                   "pop rdx", "pop rcx", "xor rax, rax", "ret")

        # _hw_pic_unmask(irq): clear bit (1<<irq) in the appropriate IMR port
        self.label("_hw_pic_unmask")
        self.emitf("push rcx", "push rdx",
                   "cmp rdi, 8", "jge ._picumsk_slave",
                   "mov cl, dil", "mov dl, 1", "shl dl, cl", "not dl",
                   "in al, 0x21", "and al, dl", "out 0x21, al",
                   "pop rdx", "pop rcx", "xor rax, rax", "ret")
        self.label("._picumsk_slave")
        self.emitf("sub edi, 8",
                   "mov cl, dil", "mov dl, 1", "shl dl, cl", "not dl",
                   "in al, 0xA1", "and al, dl", "out 0xA1, al",
                   "pop rdx", "pop rcx", "xor rax, rax", "ret")

        # PIT (channel 0, mode 3 square wave)
        # Reload = 1193182 / hz
        self.label("_hw_pit_set_freq")
        self.emitf("push rbx",
                   "mov rax, 1193182", "xor rdx, rdx", "div rdi",
                   "mov rbx, rax",
                   "mov al, 0x36", "out 0x43, al",   # channel 0, mode 3, binary
                   "mov al, bl",   "out 0x40, al",   # low byte
                   "mov al, bh",   "out 0x40, al",   # high byte
                   "pop rbx", "xor rax, rax", "ret")

        # Keyboard PS/2 (port 0x60)
        self.label("_hw_keyboard_read")
        self.label("._kbr_wait")
        self.emitf("in al, 0x64", "test al, 1", "jz ._kbr_wait",
                   "in al, 0x60", "movzx rax, al", "ret")
        self.label("_hw_keyboard_poll")
        self.emitf("in al, 0x64", "test al, 1", "jz ._kbp_none",
                   "in al, 0x60", "movzx rax, al", "ret")
        self.label("._kbp_none")
        self.emitf("xor rax, rax", "ret")

        # VGA text-mode color / cursor helpers
        self.label("_hw_vga_set_color")
        # rdi=fg (0-15), rsi=bg (0-7); attribute = (bg<<4)|fg; stored in BSS word
        self.emitf("push rdi",
                   "shl rsi, 4", "or rdi, rsi",
                   "mov [rel _vga_attr], dil",
                   "pop rdi", "xor rax, rax", "ret")
        self.label("_hw_vga_set_cursor")
        self.emitf("mov [rel _vga_row], rdi",
                   "mov [rel _vga_col], rsi",
                   "xor rax, rax", "ret")
        self.label("_hw_vga_get_row")
        self.emitf("mov rax, [rel _vga_row]", "ret")
        self.label("_hw_vga_get_col")
        self.emitf("mov rax, [rel _vga_col]", "ret")

        # ---- High-level console: thin wrappers around the VGA text-mode
        # helpers print() already uses (_vga_clear/_vga_putchar/
        # _runtime_print_str), and aliases of the vga_* cursor/color
        # primitives above.
        self.label("_hw_console_clear")
        self.emitf("call _vga_clear", "xor rax, rax", "ret")
        self.label("_hw_console_putc")
        self.emitf("call _vga_putchar", "xor rax, rax", "ret")
        self.label("_hw_console_write")
        self.emitf("call _runtime_print_str", "xor rax, rax", "ret")
        self.label("_hw_console_set_color")
        self.emitf("jmp _hw_vga_set_color")
        self.label("_hw_console_set_cursor")
        self.emitf("jmp _hw_vga_set_cursor")
        self.label("_hw_console_get_row")
        self.emitf("jmp _hw_vga_get_row")
        self.label("_hw_console_get_col")
        self.emitf("jmp _hw_vga_get_col")


    # --------------------------------------------- emit_data_sections -----

    def emit_data_sections(self) -> None:
        # Everything goes in .data so _load_end is truly after all loaded bytes.
        # (.rodata would appear after .data in the binary since .data is encountered
        # first in emit_print_impls, causing user strings to fall outside _load_end.)
        if self.strings or self.floats:
            self.emit("")
            self.emit("section .data")
            for label, body in self.strings:
                self.emit(f"{label}: db {body},0")
            for label, val in self.floats:
                self.emit(f"{label}: dq {repr(val)}")

        # Module-level global variables + class-level variable constants
        self.emit("")
        self.emit("section .bss nobits")
        for name in self.global_vars:
            self.emit(f"{self._global_label(name)}: resq 1")
        for label in self.class_var_labels.values():
            self.emit(f"{label}: resq 1")

        # _load_end must be the last byte-producing address, _bss_end after BSS.
        self.emit("")
        self.emit("section .data")
        self.emit("_load_end:")

        self.emit("")
        self.emit("section .bss nobits")
        self.emit("_bss_end:")
