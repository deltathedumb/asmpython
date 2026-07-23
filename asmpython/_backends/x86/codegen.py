"""
x86 (32-bit) code generator.

Translates one IRFunc to a flat byte stream using the register allocation
produced by regalloc.py. Internal branch targets are fixed up in a second
pass; unresolved external calls/global references are returned as
relocation records so the object-file emitter (elf.py) can build a proper
relocation table.

Adapted from the x86-64 backend's codegen.py, which this is modeled on --
three real, substantial pieces of new design here, not adaptation of
existing x86-64 logic:

  - cdecl calling convention: EVERY call argument goes on the stack
    (pushed right-to-left, matching real cdecl), never in a register --
    x86-64's own _call is built entirely around SysV/Win64's register-
    based argument passing (ARG_REGS_SYSV/WIN64, Win64 shadow space,
    Win64-vararg GP/XMM duplication, a setjmp-safety carve-out), none of
    which has any cdecl equivalent. This file's own _call is a from-
    scratch, much simpler design: push every stack-passed argument
    (right to left, per real cdecl), call, then `add esp, N` to clean up
    (the CALLER cleans cdecl's stack, unlike stdcall/fastcall).

  - i64 register-pair arithmetic: asmpython's shared IR (_compiler/ir.py)
    only ever produces three non-float value types -- I64, F64, PTR --
    with every plain Python int/bool mapping to I64. regalloc.py always
    gives an i64 value a permanent 8-byte EBP-relative stack slot (never
    a register -- see that file's _is_wide_int), so every integer
    arithmetic opcode here (iadd/isub/imul/idiv/shifts/compares/bitcasts)
    loads both 32-bit halves into transient GP registers, computes using
    the low-half op plus ADC/SBB to propagate carry/borrow into the high
    half (add/sub), a 3-partial-product sequence (multiply), or SHLD/SHRD
    (shifts less than 32 bits -- the same primitive the existing
    __udivdi64/__umoddi64 runtime helpers already use internally), then
    stores both halves back. A full register-pair ALLOCATION strategy
    (an i64 value living in two real registers when there's room) is
    real, explicitly-tracked follow-up work once this correctness-first
    version is working end-to-end -- not built yet.

  - Real 32-bit PIC (position-independent code): there is no RIP-relative
    addressing mode in 32-bit protected mode at all (encoder.py's own
    module docstring), so a function that references any global/string/
    external symbol runs the classic call/pop-EBX-then-GOT-relative
    trick instead: `call $+5` / `pop ebx` / `add ebx, OFFSET
    _GLOBAL_OFFSET_TABLE_` once at function entry (an R_386_GOTPC
    relocation on the `add`'s immediate), then `lea dst, [ebx +
    symbol@GOTOFF]` (an R_386_GOTOFF relocation) for each later
    reference. External function calls go through the PLT (R_386_PLT32)
    rather than a direct PC32-relative call, matching real PIC
    convention. regalloc.py's own `needs_pic` parameter (this file
    computes it via a pre-scan before calling allocate()) excludes EBX
    from the allocatable pool for exactly this reason -- it holds a
    function-scoped invariant the prologue sets up once, not an ordinary
    value.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Callable

from .encoder import (
    Reg, XmmReg, CC, Mem,
    encode_mov_rr, encode_mov_ri, encode_mov_rm, encode_mov_mr, encode_lea,
    encode_add_rr, encode_sub_rr, encode_and_rr, encode_or_rr, encode_xor_rr,
    encode_adc_rr, encode_sbb_rr,
    encode_imul_rr, encode_idiv_r, encode_div_r, encode_mul_r,
    encode_neg, encode_not,
    encode_cmp_rr, encode_test_rr, encode_xor_zero,
    encode_add_ri, encode_sub_ri, encode_cmp_ri,
    encode_shl_ri, encode_shr_ri, encode_sar_ri,
    encode_shl_cl, encode_shr_cl, encode_sar_cl,
    encode_shld_ri, encode_shld_cl, encode_shrd_ri, encode_shrd_cl,
    encode_movsx, encode_movzx,
    encode_push, encode_pop, encode_push_i, encode_push_m,
    encode_ret,
    encode_call_rel32, encode_call_r,
    encode_jmp_rel32, encode_jcc_rel32,
    encode_setcc, encode_nop,
    encode_movsd_rr, encode_addsd, encode_subsd, encode_mulsd, encode_divsd,
    encode_movsd_rm, encode_movsd_mr, encode_ucomisd,
    encode_cvtsi2sd, encode_cvttsd2si,
    encode_movss_rr, encode_addss, encode_subss, encode_mulss, encode_divss,
    encode_movss_rm, encode_movss_mr, encode_ucomiss,
    encode_cvtsi2ss, encode_cvttss2si,
    encode_movzx_rm8, encode_mov_mr8, encode_movzx_rm16,
    encode_mov_rm32, encode_mov_mr32,
    encode_mov_tls_rm,
    encode_addps, encode_subps, encode_mulps, encode_divps,
    encode_maxps, encode_minps, encode_andps, encode_orps, encode_xorps,
    encode_movaps_rr, encode_shufps,
    encode_addpd, encode_subpd, encode_mulpd, encode_divpd,
    encode_maxpd, encode_minpd, encode_andpd, encode_orpd, encode_xorpd,
    encode_movapd_rr,
    encode_paddb, encode_paddw, encode_paddd, encode_paddq,
    encode_psubb, encode_psubw, encode_psubd, encode_psubq,
    encode_pand, encode_por, encode_pxor,
    encode_pcmpeqb, encode_pcmpeqw, encode_pcmpeqd,
    encode_movdqa_rr, encode_movdqu_rr,
    encode_pmulld, encode_pshufd,
    encode_movdqu_rm, encode_movdqu_mr,
    encode_movdqa_rm, encode_movdqa_mr,
    encode_movaps_rm, encode_movaps_mr,
)
from .regalloc import AllocResult, RegLoc, XmmLoc, StackLoc, Location, allocate

# ELF32 reloc types used by the codegen (also imported by elf.py) -- real,
# standard i386 SysV ABI supplement values, cross-checked against
# pyelftools' own ENUM_RELOC_TYPE_i386 before use (not just recalled from
# memory): R_386_32=1 (absolute), R_386_PC32=2, R_386_GOT32=3,
# R_386_PLT32=4, R_386_GOTOFF=9, R_386_GOTPC=10, R_386_TLS_TPOFF=14
# ("negative offset in static TLS block" -- the local-exec TLS model's own
# relocation, matching x86-64's R_X86_64_TPOFF32 one segment register over).
R_386_32        = 1
R_386_PC32      = 2
R_386_GOT32     = 3
R_386_PLT32     = 4
R_386_GOTOFF    = 9
R_386_GOTPC     = 10
R_386_TLS_TPOFF = 14


# ── Output ────────────────────────────────────────────────────────────────────

@dataclass
class FuncCode:
    name:       str
    code:       bytes
    # (offset_of_imm32_field, symbol_name, reloc_type) — external relocations
    relocs:     list[tuple[int, str, int]] = field(default_factory=list)
    visibility: str | None = None   # "public" | "private" | "global" | None → default global
    # (code_offset, filename, line) — from debug_loc IR ops
    debug_locs: list[tuple[int, str, int]] = field(default_factory=list)


@dataclass
class _SyntheticCallInstr:
    """Minimal stand-in for a real IRInstr, used ONLY to route i64 idiv/
    irem/udiv/urem through this file's own already-verified _call --
    _call reads exactly `.result` and `.operands` (never `.op`), so this
    is the entire shape it needs. Building a real `call` IR instruction
    at the ir_lower.py level instead (calling __udivdi64 etc. directly
    from the IR, the way ir_lower.py already does for other runtime
    helpers) was considered and rejected: idiv/irem/udiv/urem are
    generic opcodes shared with every OTHER backend's native divide
    instruction, and inserting a backend-specific runtime-call lowering
    at the shared IR level would leak x86-32-only concerns into code
    every other backend also consumes. Confining the substitution to
    this one file, at codegen time, keeps it local to the exact backend
    that actually needs it."""
    result:   Any
    operands: list


# ── Helpers ───────────────────────────────────────────────────────────────────

def _is_float(type_name: str) -> bool:
    return type_name in ("f32", "f64")


def _is_xmm(type_name: str) -> bool:
    """True if this type lives in an XMM register (floats or v128 SIMD)."""
    return type_name in ("f32", "f64", "v128")


def _is_wide_int(type_name: str) -> bool:
    """True for i64 -- see regalloc.py's own _is_wide_int docstring for
    why this is the universal case for integer arithmetic on this
    backend, not an edge case, and why it's ALWAYS stack-resident."""
    return type_name == "i64"


def _scan_needs_pic(func: Any) -> bool:
    """True if `func` references any global/string/external symbol and
    must therefore run as real position-independent code (see this
    module's own docstring for why). Computed by a pre-scan BEFORE
    regalloc.allocate() is called -- allocate()'s own needs_pic parameter
    must be known before allocation starts (it changes which GP registers
    are even allocatable), so this can't be decided lazily while walking
    instructions during code generation the way most per-instruction
    decisions in this file are.

    A call instruction targeting a NAME (not an indirect function-pointer
    value) is always an external symbol reference in this compiler's IR
    (a same-module direct call is represented as a plain `br`/fallthrough
    within the merged whole-program IR, not a `call` -- every `call`
    instruction's non-indirect target is a real cross-module or runtime-
    helper symbol), so it needs the same GOT-aware treatment as global_
    addr/str_global/tls_addr.
    """
    for block in func.blocks:
        for instr in block.instrs:
            if instr.op in ("global_addr", "str_global", "tls_addr"):
                return True
            if instr.op == "call":
                target_op = instr.operands[0]
                is_indirect = hasattr(target_op, "name") and hasattr(target_op, "type")
                if not is_indirect:
                    return True
    return False


# ── Code generator ────────────────────────────────────────────────────────────

class FuncCodegen:
    _SCRATCH      = Reg.EDX        # reserved GP scratch (see class docstring)
    _SCRATCH2     = Reg.ECX        # second GP scratch for addr/value alias cases
    _SCRATCH_XMM  = XmmReg.XMM6    # reserved XMM scratch
    _SCRATCH_XMM2 = XmmReg.XMM7    # second XMM scratch, mirrors _SCRATCH2

    # Reserved-scratch rationale: x86-64's codegen reserves R10/R11/XMM14/
    # XMM15 as scratch, entirely SEPARATE from its regalloc pool -- this
    # backend has only 6 GP registers total (vs. 16 on x86-64), too few to
    # spare two as a standing reservation on top of what regalloc.py's own
    # _GP_POOL already allocates from. Instead, EDX/ECX double as the
    # multiply/divide-instruction-mandated registers (MUL/IMUL/DIV/IDIV
    # already unconditionally clobber EDX:EAX; shl/shr/sar's variable-count
    # form already unconditionally clobbers ECX) AND as this file's general
    # scratch registers -- regalloc.py's own crosses_call/crosses_var_shift
    # analysis already keeps a still-live value out of EAX/EDX (call
    # results) and ECX (variable-shift counts) at exactly the instructions
    # where this file needs them as scratch, so no value regalloc.py
    # legitimately homed there is ever still needed at those points. XMM6/
    # XMM7 are simply the two 32-bit-mode XMM registers regalloc.py's own
    # _XMM_POOL already excludes (see that file), for the identical reason
    # on the float side.

    def __init__(self, func: Any, alloc: AllocResult, needs_pic: bool) -> None:
        self.func      = func
        self.alloc     = alloc
        self.needs_pic = needs_pic
        self.buf       = bytearray()
        self.block_off: dict[str, int]          = {}
        # (patch_offset, label_or_symbol, reloc_type)
        # reloc_type=0 means internal branch (resolved in same pass, no ELF reloc)
        self.fixups: list[tuple[int, str, int]] = []
        self._debug_locs: list[tuple[int, str, int]] = []
        # Set once, right after the PIC prologue sequence runs (see
        # _prologue) -- every later global_addr/str_global/tls_addr/
        # external-call site reads this to know EBX already holds the
        # real GOT base, matching how the x86-64 backend's own R10/R11
        # scratch reservations are simply always available (this one
        # needs the one-time setup cost paid first).
        self._pic_ready = False

    # ── Emit helpers ──────────────────────────────────────────────────────────

    def _emit(self, b: bytes) -> None:
        self.buf.extend(b)

    def _pos(self) -> int:
        return len(self.buf)

    # ── Location helpers ──────────────────────────────────────────────────────

    def _loc(self, val: Any) -> Location:
        return self.alloc.locs[val.name]

    def _gp(self, val: Any, alt_scratch: bool = False) -> tuple[Reg, bytes]:
        """Get a GP register holding val (a 32-bit-native value: PTR, or
        any narrower load/store-width int). Emits a load from stack if
        spilled. NEVER call this for an i64 value -- see _gp_pair below,
        the always-two-halves counterpart every i64 opcode handler uses
        instead.

        `alt_scratch`: use _SCRATCH2 instead of _SCRATCH for the spilled/
        alloca case -- see the x86-64 backend's own _gp docstring for the
        full collision rationale (both operands of a two-operand op
        simultaneously spilled would otherwise share one scratch register,
        the second load silently clobbering the first before either is
        read).
        """
        scratch = self._SCRATCH2 if alt_scratch else self._SCRATCH
        slot = self.alloc.alloca_slots.get(val.name)
        if slot is not None:
            return scratch, encode_lea(scratch, Mem(Reg.EBP, slot))
        loc = self._loc(val)
        if isinstance(loc, RegLoc):
            return loc.reg, b""
        return scratch, encode_mov_rm(scratch, Mem(Reg.EBP, loc.offset))

    def _gp_pair(self, val: Any) -> tuple[Reg, Reg, bytes]:
        """Load an i64 value's (lo, hi) 32-bit halves into (_SCRATCH,
        _SCRATCH2). i64 values are ALWAYS StackLoc (see regalloc.py's
        _is_wide_int) -- there is no register-resident case to check,
        unlike _gp's RegLoc/StackLoc branch."""
        loc = self._loc(val)
        assert isinstance(loc, StackLoc), f"i64 value expected StackLoc for {val.name}"
        ld = (
            encode_mov_rm(self._SCRATCH, Mem(Reg.EBP, loc.offset))
            + encode_mov_rm(self._SCRATCH2, Mem(Reg.EBP, loc.offset + 4))
        )
        return self._SCRATCH, self._SCRATCH2, ld

    def _store_pair(self, val: Any, lo: Reg, hi: Reg) -> bytes:
        """Store an i64 result's (lo, hi) halves to its (always-StackLoc)
        home."""
        loc = self._loc(val)
        assert isinstance(loc, StackLoc), f"i64 result expected StackLoc for {val.name}"
        return (
            encode_mov_mr(Mem(Reg.EBP, loc.offset), lo)
            + encode_mov_mr(Mem(Reg.EBP, loc.offset + 4), hi)
        )

    def _xmm(self, val: Any, alt_scratch: bool = False) -> tuple[XmmReg, bytes]:
        """Get an XMM register holding val (f64 / v128). Emits load if
        spilled. See the x86-64 backend's own _xmm docstring for the
        alt_scratch collision rationale -- identical here."""
        loc = self._loc(val)
        if isinstance(loc, XmmLoc):
            return loc.reg, b""
        scratch = self._SCRATCH_XMM2 if alt_scratch else self._SCRATCH_XMM
        return scratch, encode_movsd_rm(scratch, Mem(Reg.EBP, loc.offset))

    def _xmm_f32(self, val: Any, alt_scratch: bool = False) -> tuple[XmmReg, bytes]:
        """Get an XMM register holding val (f32). Emits load if spilled."""
        loc = self._loc(val)
        if isinstance(loc, XmmLoc):
            return loc.reg, b""
        scratch = self._SCRATCH_XMM2 if alt_scratch else self._SCRATCH_XMM
        return scratch, encode_movss_rm(scratch, Mem(Reg.EBP, loc.offset))

    def _dst_gp(self, result: Any) -> Reg:
        loc = self._loc(result)
        assert isinstance(loc, RegLoc), f"GP result expected for {result.name}"
        return loc.reg

    def _dst_xmm(self, result: Any) -> XmmReg:
        loc = self._loc(result)
        assert isinstance(loc, XmmLoc), f"XMM result expected for {result.name}"
        return loc.reg

    def _dst_gp_spillable(self, result: Any, alt_scratch: bool = False) -> "tuple[Reg, Callable[[], None]]":
        """Like `_dst_gp`, but tolerates regalloc having spilled `result`
        to a StackLoc. See the x86-64 backend's own docstring for the
        full rationale -- identical here, just with this backend's own
        two scratch registers."""
        loc = self._loc(result)
        if isinstance(loc, RegLoc):
            return loc.reg, lambda: None
        assert isinstance(loc, StackLoc), f"GP result expected for {result.name}"
        scratch = self._SCRATCH2 if alt_scratch else self._SCRATCH
        return scratch, lambda: self._emit(encode_mov_mr(Mem(Reg.EBP, loc.offset), scratch))

    def _dst_xmm_spillable(self, result: Any) -> "tuple[XmmReg, Callable[[], None]]":
        loc = self._loc(result)
        if isinstance(loc, XmmLoc):
            return loc.reg, lambda: None
        assert isinstance(loc, StackLoc), f"XMM result expected for {result.name}"
        scratch = self._SCRATCH_XMM
        return scratch, lambda: self._emit(encode_movsd_mr(Mem(Reg.EBP, loc.offset), scratch))

    # ── Prologue / epilogue ───────────────────────────────────────────────────

    def _prologue(self) -> None:
        self._emit(encode_push(Reg.EBP))
        self._emit(encode_mov_rr(Reg.EBP, Reg.ESP))
        if self.needs_pic:
            # Classic 32-bit PIC GOT-base setup, matching real GCC/GAS
            # output exactly (verified against a real `gcc -m32 -fPIC -S`
            # compile of a global-variable access, then hand-assembled and
            # linked for real ground truth -- see the investigation this
            # replaced, below).
            #
            # `call get_pc_thunk` transfers to a tiny local helper that
            # does `mov ebx, [esp]; ret` -- this reads the return address
            # (the address of the instruction right after the `call`,
            # i.e. this function's own EIP at the call site) into EBX
            # *without moving ESP*, then returns control to exactly that
            # same address (the `ret` re-pops the same value as a real
            # return). Control resumes IMMEDIATELY at the `add`, with
            # EBX already holding that `add` instruction's own address --
            # `add ebx, OFFSET _GLOBAL_OFFSET_TABLE_` (an R_386_GOTPC
            # relocation on the immediate) then turns that into the real
            # GOT base.
            #
            # THIS IS NOT THE SAME AS `call $+5` / `pop ebx` followed
            # directly by the `add`: that sequence is subtly WRONG here,
            # confirmed by direct Unicorn execution of real, ld-linked
            # binaries. `pop ebx` loads EBX with the call's return address
            # (correct), but `pop` is itself a 1-byte instruction -- so by
            # the time the following `add` actually executes, EIP has
            # moved 1 byte past the value EBX holds. The linker's own
            # R_386_GOTPC relocation resolution (`GOT + A - field_address`)
            # assumes EBX == the `add` instruction's OWN address (true
            # when a callee's `ret` delivers control exactly there, as
            # here; false when an inline `pop` sits between the "read
            # EIP" step and the `add`, silently shifting EBX one byte
            # away from where the `add` actually starts). Confirmed by
            # building minimal test binaries both ways with real `as`/`ld`
            # and single-stepping the real linked bytes in Unicorn: the
            # separate-thunk form reconstructs the exact real GOT address
            # (and a real GOTOFF-relative `lea` off it lands on the exact
            # real symbol address); the inline-`pop` form is off by
            # exactly one byte every time. This mirrors real GCC output
            # byte-for-byte (`__x86.get_pc_thunk.bx: mov ebx, [esp]; ret`),
            # which is why this now matches that pattern instead of the
            # naive inline version.
            #
            # `push ebx` FIRST saves the caller's own EBX (this function
            # is about to permanently repurpose it as the GOT-base
            # register for its own duration) -- regalloc.py's own
            # needs_pic=True already excluded EBX from the allocatable
            # pool for exactly this reason, so nothing else in this
            # function's own body ever expects to find a live value
            # there once this sequence runs.
            self._emit(encode_push(Reg.EBX))
            call_off = self._pos()
            self._emit(encode_call_rel32(0))
            # Patched once the thunk's real offset is known, right below
            # (the thunk is emitted immediately after this call, at a
            # fixed +N byte distance -- always resolvable in this same
            # pass, never deferred to compile()'s block-label fixup pass
            # or to an ELF relocation).
            thunk_call_patch_off = call_off + 1
            gotpc_off = self._pos()
            # ADD EBX, imm32 -- emitted directly (not through encode_add_ri,
            # which always chooses the imm8 form when it fits) to guarantee
            # a real 4-byte field for the relocation to patch, regardless
            # of what value ends up there.
            #
            # Placeholder is 2: this matches real `as` output exactly for
            # this instruction encoding (`81 c3` ModRM opcode form, so the
            # imm32 field starts 2 bytes after the add's own opcode byte).
            # R_386_GOTPC resolves as `GOT + A - field_address`; real `as`
            # bakes A = (field_address - instruction_address) so that,
            # given EBX == this add instruction's own address (guaranteed
            # by the thunk above), `EBX + resolved_value == GOT` exactly.
            # Verified against a real `gcc -m32 -fPIC -S` reference
            # (`add $0x2fec,%eax` unlinked placeholder was `1` for that
            # instruction's 1-byte-opcode accumulator-form encoding,
            # confirming A tracks the field/instruction-start gap, not a
            # fixed constant) and against this exact 2-byte-opcode form
            # hand-assembled and linked for real (`ld -m elf_i386`),
            # single-stepped in Unicorn to confirm the reconstructed EBX
            # matches the real linked GOT address exactly.
            self._emit(bytes([0x81, 0xC3]) + struct.pack("<i", 2))  # ADD EBX, imm32
            self.fixups.append((gotpc_off + 2, "_GLOBAL_OFFSET_TABLE_", R_386_GOTPC))
            self._pic_ready = True
            # Skip over the thunk (never falls through to it) and emit it
            # right after -- a tiny, local, non-relocatable helper:
            #   get_pc_thunk: mov ebx, [esp] ; ret
            skip_off = self._pos()
            self._emit(encode_jmp_rel32(0))
            thunk_off = self._pos()
            struct.pack_into("<i", self.buf, thunk_call_patch_off, thunk_off - (call_off + 5))
            self._emit(bytes([0x8B, 0x1C, 0x24]))  # MOV EBX, [ESP]
            self._emit(encode_ret())
            struct.pack_into("<i", self.buf, skip_off + 1, self._pos() - (skip_off + 5))
        for r in self.alloc.callee_saved:
            self._emit(encode_push(r))
        if self.alloc.stack_bytes:
            self._emit(encode_sub_ri(Reg.ESP, self.alloc.stack_bytes))

    def _epilogue(self) -> None:
        if self.alloc.stack_bytes:
            self._emit(encode_add_ri(Reg.ESP, self.alloc.stack_bytes))
        for r in reversed(self.alloc.callee_saved):
            self._emit(encode_pop(r))
        if self.needs_pic:
            self._emit(encode_pop(Reg.EBX))
        self._emit(encode_pop(Reg.EBP))
        self._emit(encode_ret())

    # ── Pattern helpers (native 32-bit-wide GP/XMM values) ───────────────────

    def _binop_gp(self, result: Any, a: Any, b: Any, rr_fn) -> None:
        """dst = rr_fn(a, b) -- see the x86-64 backend's own docstring for
        the full result-spilled-vs-a-spilled rationale; identical here,
        just EBP-relative instead of RBP-relative. NEVER call this for an
        i64 operand/result -- see _iadd64/_isub64 etc., the register-pair
        counterparts every wide-int opcode handler uses instead."""
        a_r, a_ld = self._gp(a)
        b_r, b_ld = self._gp(b, alt_scratch=True)
        self._emit(a_ld + b_ld)
        loc = self._loc(result)
        if isinstance(loc, RegLoc):
            dst = loc.reg
            if a_r != dst:
                self._emit(encode_mov_rr(dst, a_r))
            self._emit(rr_fn(dst, b_r))
            return
        assert isinstance(loc, StackLoc), f"GP result expected for {result.name}"
        a_loc = self._loc(a)
        if isinstance(a_loc, RegLoc):
            self._emit(encode_push(a_r))
            self._emit(rr_fn(a_r, b_r))
            self._emit(encode_mov_mr(Mem(Reg.EBP, loc.offset), a_r))
            self._emit(encode_pop(a_r))
        else:
            self._emit(rr_fn(a_r, b_r))
            self._emit(encode_mov_mr(Mem(Reg.EBP, loc.offset), a_r))

    def _binop_xmm(self, result: Any, a: Any, b: Any, rr_fn, f32: bool = False) -> None:
        get = self._xmm_f32 if f32 else self._xmm
        mov = encode_movss_rr if f32 else encode_movsd_rr
        dst       = self._dst_xmm(result)
        a_x, a_ld = get(a)
        b_x, b_ld = get(b, alt_scratch=True)
        self._emit(a_ld + b_ld)
        if a_x != dst:
            self._emit(mov(dst, a_x))
        self._emit(rr_fn(dst, b_x))

    def _binop_simd(self, result: Any, a: Any, b: Any, rr_fn) -> None:
        """Packed XMM binary op (v128 / SIMD)."""
        dst       = self._dst_xmm(result)
        a_x, a_ld = self._xmm(a)
        b_x, b_ld = self._xmm(b, alt_scratch=True)
        self._emit(a_ld + b_ld)
        if a_x != dst:
            self._emit(encode_movdqa_rr(dst, a_x))
        self._emit(rr_fn(dst, b_x))

    def _cmp_set(self, result: Any, a: Any, b: Any, cc: CC) -> None:
        """NEVER call this for an i64 operand -- see _icmp64, the register-
        pair-aware two-step (hi-then-lo) counterpart."""
        a_r, a_ld = self._gp(a)
        b_r, b_ld = self._gp(b, alt_scratch=True)
        self._emit(a_ld + b_ld)
        self._emit(encode_cmp_rr(a_r, b_r))
        dst, spill = self._dst_gp_spillable(result)
        self._emit(encode_setcc(cc, dst))
        self._emit(encode_movzx(dst, dst, 8))
        spill()

    def _fcmp_set(self, result: Any, a: Any, b: Any, cc: CC, f32: bool) -> None:
        if f32:
            a_x, a_ld = self._xmm_f32(a)
            b_x, b_ld = self._xmm_f32(b, alt_scratch=True)
            self._emit(a_ld + b_ld)
            self._emit(encode_ucomiss(a_x, b_x))
        else:
            a_x, a_ld = self._xmm(a)
            b_x, b_ld = self._xmm(b, alt_scratch=True)
            self._emit(a_ld + b_ld)
            self._emit(encode_ucomisd(a_x, b_x))
        dst, spill = self._dst_gp_spillable(result)
        self._emit(encode_setcc(cc, dst))
        self._emit(encode_movzx(dst, dst, 8))
        spill()

    # ── Branch helpers ────────────────────────────────────────────────────────

    def _jmp(self, label: str) -> None:
        off = self._pos()
        self._emit(encode_jmp_rel32(0))
        self.fixups.append((off + 1, label, 0))

    def _jcc(self, cc: CC, label: str) -> None:
        off = self._pos()
        self._emit(encode_jcc_rel32(cc, 0))
        self.fixups.append((off + 2, label, 0))

    # ── Native 32-bit division (i32/u32 -- NOT i64, see _idiv64) ─────────────

    def _div(self, instr: Any) -> None:
        op = instr.op
        a, b = instr.operands[0], instr.operands[1]
        a_r, a_ld = self._gp(a)
        a_in_eax = a_r == Reg.EAX
        b_r, b_ld = self._gp(b, alt_scratch=not a_in_eax)
        self._emit(a_ld + b_ld)

        if a_in_eax:
            self._emit(encode_mov_rr(self._SCRATCH2, Reg.EAX))
        else:
            self._emit(encode_mov_rr(Reg.EAX, a_r))
        if b_r == Reg.EDX:
            self._emit(encode_mov_rr(self._SCRATCH, b_r))
            b_r = self._SCRATCH

        if op in ("idiv", "irem"):
            self._emit(encode_cdq())
            self._emit(encode_idiv_r(b_r))
        else:
            self._emit(encode_xor_zero(Reg.EDX))
            self._emit(encode_div_r(b_r))

        dst, spill = self._dst_gp_spillable(instr.result)
        result_reg = Reg.EAX if op in ("idiv", "udiv") else Reg.EDX
        if dst != result_reg:
            self._emit(encode_mov_rr(dst, result_reg))
        if a_in_eax and dst != Reg.EAX:
            self._emit(encode_mov_rr(Reg.EAX, self._SCRATCH2))
        spill()

    # ── Native 32-bit shifts (i32/u32 -- NOT i64, see _shift64) ──────────────

    def _shift(self, instr: Any) -> None:
        op = instr.op
        val_r, val_ld = self._gp(instr.operands[0])
        dst, spill = self._dst_gp_spillable(instr.result, alt_scratch=True)
        self._emit(val_ld)
        if val_r != dst:
            self._emit(encode_mov_rr(dst, val_r))

        count = instr.operands[1]
        if isinstance(count, int):
            self._emit({"shl": encode_shl_ri, "shr": encode_shr_ri, "sar": encode_sar_ri}[op](dst, count))
        else:
            cnt_r, cnt_ld = self._gp(count)
            self._emit(cnt_ld)
            if cnt_r != Reg.ECX:
                self._emit(encode_mov_rr(Reg.ECX, cnt_r))
            self._emit({"shl": encode_shl_cl, "shr": encode_shr_cl, "sar": encode_sar_cl}[op](dst))
        spill()

    # ── i64 register-pair arithmetic ──────────────────────────────────────────
    #
    # Every i64 value is ALWAYS StackLoc (regalloc.py's _is_wide_int -- see
    # that file's own docstring for why this is the universal case on this
    # backend, not an edge case). Each opcode here loads both 32-bit halves
    # of each operand via _gp_pair (always (_SCRATCH, _SCRATCH2) = (EDX,
    # ECX)), computes using EAX/EBX as the only other free GP registers
    # (EDX/ECX are already claimed by the just-loaded operand halves), and
    # stores the result's two halves back via _store_pair. This means an
    # i64 op can only ever have ONE i64 operand loaded via _gp_pair at a
    # time before its halves must be consumed/moved -- loading a SECOND
    # i64 operand the same way would try to reuse EDX/ECX for the second
    # operand's halves too, silently clobbering the first. Every handler
    # below loads operand A into EDX:ECX, immediately moves those into
    # EAX:EBX (freeing EDX:ECX), THEN loads operand B into EDX:ECX -- never
    # the reverse order, and never both live in EDX:ECX simultaneously.

    def _iadd64(self, result: Any, a: Any, b: Any, sub: bool = False) -> None:
        a_lo, a_hi, a_ld = self._gp_pair(a)
        self._emit(a_ld)
        self._emit(encode_mov_rr(Reg.EAX, a_lo))
        self._emit(encode_mov_rr(Reg.EBX, a_hi))
        b_lo, b_hi, b_ld = self._gp_pair(b)
        self._emit(b_ld)
        if sub:
            self._emit(encode_sub_rr(Reg.EAX, b_lo))
            self._emit(encode_sbb_rr(Reg.EBX, b_hi))
        else:
            self._emit(encode_add_rr(Reg.EAX, b_lo))
            self._emit(encode_adc_rr(Reg.EBX, b_hi))
        self._emit(self._store_pair(result, Reg.EAX, Reg.EBX))

    def _imul64(self, result: Any, a: Any, b: Any) -> None:
        """low64(a * b) -- the only width this backend's IR ever needs
        (asmpython has no 128-bit integer type). Full-width 64x64
        multiplication only requires the LOW 64 bits of the true 128-bit
        product: writing a = a_hi:a_lo, b = b_hi:b_lo (each a 32-bit
        half), the exact low 64 bits of a*b are
            lo  = low32(a_lo * b_lo)
            hi  = high32(a_lo * b_lo) + low32(a_lo * b_hi) + low32(a_hi * b_lo)
        (the a_hi*b_hi term only ever contributes to bits 64 and above,
        entirely outside the low-64-bit result -- correctly dropped, not
        an approximation).

        MUL unconditionally clobbers EDX:EAX, and this needs three
        separate MULs -- too many live values (a_lo, a_hi, b_lo, b_hi,
        plus a running hi-accumulator) for EAX/EBX/ECX/EDX to hold
        uninterrupted throughout. Spills all four operand halves onto
        the stack up front in a fixed layout, then does each of the
        three partial products as an isolated EAX/EBX/EDX-only step,
        reloading whichever halves that step needs from the stack --
        never trusting a value to still be live in a register across a
        MUL it didn't itself just produce.
        """
        a_lo, a_hi, a_ld = self._gp_pair(a)
        self._emit(a_ld)
        self._emit(encode_push(a_hi))
        self._emit(encode_push(a_lo))
        b_lo, b_hi, b_ld = self._gp_pair(b)
        self._emit(b_ld)
        self._emit(encode_push(b_hi))
        self._emit(encode_push(b_lo))
        # Stack layout from here on (ESP-relative, fixed for the rest of
        # this sequence): [esp+0]=b_lo [esp+4]=b_hi [esp+8]=a_lo [esp+12]=a_hi

        # Step 1: EDX:EAX = a_lo * b_lo. EAX is the final result_lo;
        # EDX is the first term of the hi-accumulator.
        self._emit(encode_mov_rm(Reg.EAX, Mem(Reg.ESP, 8)))
        self._emit(encode_mov_rm(Reg.EBX, Mem(Reg.ESP, 0)))
        self._emit(encode_mul_r(Reg.EBX))
        self._emit(encode_push(Reg.EAX))   # save result_lo
        self._emit(encode_push(Reg.EDX))   # save running hi-accumulator (high32(a_lo*b_lo))
        # Stack layout now: [esp+0]=hi_acc [esp+4]=result_lo
        #                   [esp+8]=b_lo [esp+12]=b_hi [esp+16]=a_lo [esp+20]=a_hi

        # Step 2: EDX:EAX = a_lo * b_hi -- add its low32 into hi_acc.
        self._emit(encode_mov_rm(Reg.EAX, Mem(Reg.ESP, 16)))
        self._emit(encode_mov_rm(Reg.EBX, Mem(Reg.ESP, 12)))
        self._emit(encode_mul_r(Reg.EBX))
        self._emit(encode_mov_rm(Reg.EBX, Mem(Reg.ESP, 0)))   # reload hi_acc
        self._emit(encode_add_rr(Reg.EBX, Reg.EAX))
        self._emit(encode_mov_mr(Mem(Reg.ESP, 0), Reg.EBX))   # store updated hi_acc

        # Step 3: EDX:EAX = a_hi * b_lo -- add its low32 into hi_acc.
        self._emit(encode_mov_rm(Reg.EAX, Mem(Reg.ESP, 20)))
        self._emit(encode_mov_rm(Reg.EBX, Mem(Reg.ESP, 8)))
        self._emit(encode_mul_r(Reg.EBX))
        self._emit(encode_mov_rm(Reg.EBX, Mem(Reg.ESP, 0)))   # reload hi_acc
        self._emit(encode_add_rr(Reg.EBX, Reg.EAX))

        self._emit(encode_mov_rm(Reg.EAX, Mem(Reg.ESP, 4)))   # reload result_lo
        # EAX=result_lo, EBX=result_hi
        self._emit(encode_add_ri(Reg.ESP, 24))                # discard all 6 pushed dwords
        self._emit(self._store_pair(result, Reg.EAX, Reg.EBX))

    # CC -> its unsigned-comparison equivalent, for the low-word tie-break
    # below: two i64 values with EQUAL high halves are ordered purely by
    # their low 32 bits taken as unsigned magnitudes, regardless of
    # whether the overall 64-bit comparison is signed or unsigned (the
    # sign of the whole value is carried entirely by the high half).
    _CC_TO_UNSIGNED: dict[CC, CC] = {
        CC.L: CC.B, CC.LE: CC.BE, CC.G: CC.A, CC.GE: CC.AE,
        CC.B: CC.B, CC.BE: CC.BE, CC.A: CC.A, CC.AE: CC.AE,
        CC.E: CC.E, CC.NE: CC.NE,
    }

    def _icmp64(self, result: Any, a: Any, b: Any, cc: CC) -> None:
        """i64 ordered/equality compare via a real two-step branch:
        compare high halves first (using the REQUESTED cc's own
        signedness -- CC.L/LE/G/GE for signed relations, CC.B/BE/A/AE
        for unsigned, CC.E/NE for equality, exactly as the scalar case
        would); if the high halves differ, that comparison alone decides
        the whole 64-bit relation and its result is used directly. If
        the high halves are EQUAL, the relation is decided by the low
        halves instead -- always compared as UNSIGNED regardless of the
        overall relation's signedness (see _CC_TO_UNSIGNED above).
        EQ/NE need both halves equal/any-half-different rather than a
        magnitude comparison, so they get their own short sequence
        (AND-of-equality / OR-of-inequality) instead of the generic
        two-step ordered path.

        Uses synthetic, locally-resolved jump targets exactly like this
        file's own PIC prologue's internal call/pop-thunk skip jump --
        patched via struct.pack_into within this same method, never
        deferred to compile()'s block-label fixup pass (these labels
        don't exist as real IR blocks).
        """
        a_lo, a_hi, a_ld = self._gp_pair(a)
        self._emit(a_ld)
        self._emit(encode_mov_rr(Reg.EAX, a_lo))
        self._emit(encode_mov_rr(Reg.EBX, a_hi))
        b_lo, b_hi, b_ld = self._gp_pair(b)
        self._emit(b_ld)
        # At this point: EAX=a_lo, EBX=a_hi, EDX=b_lo (a_lo's original
        # _SCRATCH slot, reloaded fresh for b), ECX=b_hi.
        dst, spill = self._dst_gp_spillable(result)

        if cc in (CC.E, CC.NE):
            # eq  <=>  (a_lo == b_lo) AND (a_hi == b_hi)
            # ne  <=>  (a_lo != b_lo) OR  (a_hi != b_hi)
            # Computed via XOR-then-OR: (a_lo^b_lo) | (a_hi^b_hi) == 0 iff
            # every bit of both halves matches -- avoids a second branch
            # entirely for this case.
            self._emit(encode_xor_rr(Reg.EAX, Reg.EDX))
            self._emit(encode_xor_rr(Reg.EBX, Reg.ECX))
            self._emit(encode_or_rr(Reg.EAX, Reg.EBX))
            self._emit(encode_setcc(CC.E if cc == CC.E else CC.NE, dst))
            self._emit(encode_movzx(dst, dst, 8))
            spill()
            return

        low_cc = self._CC_TO_UNSIGNED[cc]
        self._emit(encode_cmp_rr(Reg.EBX, Reg.ECX))  # cmp a_hi, b_hi
        hi_decides_off = self._pos()
        self._emit(encode_jcc_rel32(CC.NE, 0))
        # Fallthrough: high halves equal -> low halves (unsigned) decide.
        self._emit(encode_cmp_rr(Reg.EAX, Reg.EDX))  # cmp a_lo, b_lo
        self._emit(encode_setcc(low_cc, dst))
        self._emit(encode_movzx(dst, dst, 8))
        done_off = self._pos()
        self._emit(encode_jmp_rel32(0))
        hi_decides_target = self._pos()
        struct.pack_into("<i", self.buf, hi_decides_off + 2, hi_decides_target - (hi_decides_off + 6))
        self._emit(encode_setcc(cc, dst))
        self._emit(encode_movzx(dst, dst, 8))
        done_target = self._pos()
        struct.pack_into("<i", self.buf, done_off + 1, done_target - (done_off + 5))
        spill()

    def _shift64(self, instr: Any) -> None:
        """i64 shl/shr/sar via the classic halves-plus-SHLD/SHRD
        algorithm, branching explicitly on `count >= 32` -- SHLD/SHRD's
        count operand (like every CL-based x86 shift) is only ever
        architecturally defined for 0-31 (real hardware masks CL to its
        low 5 bits before use, so a raw count of 32-63 would silently
        wrap to 0-31 instead of behaving as the full 64-bit shift
        semantics require), so this never feeds 32+ into either
        instruction directly -- the >=32 branch computes the answer with
        a single plain 32-bit shift instead, matching real compiler
        output for exactly this reason. `count & 63` first (Python's
        arbitrary-precision int has no shift-amount ceiling the way a
        fixed-width language would enforce at the type level; a shift by
        64+ is defined as "shift out everything" -- zero, or the sign
        bit's own fill value for sar -- and is handled as an extension
        of the >=32 branch's own logic, not a separate third case, since
        shifting by exactly 32-63 already zeroes/sign-fills one half and
        shifting the OTHER half by (n-32) where n-32 could itself be
        0-31 is exactly the right general formula).

        Both operands and the shift count are loaded/held in
        EAX/EBX/ECX/EDX for the whole sequence (never spilling
        mid-computation) -- ECX is the shift instruction family's own
        mandatory count register on real hardware (CL-form SHL/SHR/SAR/
        SHLD/SHRD all hard-code CL as their variable-count source), so
        loading the count anywhere else first and moving it to ECX right
        before use, as the native _shift already does, is required here
        too.
        """
        op = instr.op
        val = instr.operands[0]
        count_op = instr.operands[1]
        val_lo, val_hi, val_ld = self._gp_pair(val)
        self._emit(val_ld)
        self._emit(encode_mov_rr(Reg.EAX, val_lo))
        self._emit(encode_mov_rr(Reg.EBX, val_hi))
        if isinstance(count_op, int):
            n = count_op & 63
            self._emit(encode_mov_ri(Reg.ECX, n))
        else:
            cnt_r, cnt_ld = self._gp(count_op)
            self._emit(cnt_ld)
            if cnt_r != Reg.ECX:
                self._emit(encode_mov_rr(Reg.ECX, cnt_r))
            self._emit(bytes([0x83, 0xE1, 0x3F]))  # AND ECX, 63

        self._emit(bytes([0x83, 0xF9, 0x20]))  # CMP ECX, 32
        ge32_off = self._pos()
        self._emit(encode_jcc_rel32(CC.AE, 0))

        # Fallthrough: 0 <= n < 32.
        if op == "shl":
            self._emit(encode_shld_cl(Reg.EBX, Reg.EAX))   # hi = (hi<<n) | (lo>>(32-n))
            self._emit(encode_shl_cl(Reg.EAX))              # lo = lo<<n
        elif op == "shr":
            self._emit(encode_shrd_cl(Reg.EAX, Reg.EBX))   # lo = (lo>>n) | (hi<<(32-n))
            self._emit(encode_shr_cl(Reg.EBX))              # hi = hi>>n (unsigned/logical)
        else:  # sar
            self._emit(encode_shrd_cl(Reg.EAX, Reg.EBX))   # lo = (lo>>n) | (hi<<(32-n))
            self._emit(encode_sar_cl(Reg.EBX))              # hi = hi>>n (arithmetic, sign-extends)
        done_off = self._pos()
        self._emit(encode_jmp_rel32(0))

        ge32_target = self._pos()
        struct.pack_into("<i", self.buf, ge32_off + 2, ge32_target - (ge32_off + 6))
        # n in [32, 63]: ECX currently holds n; the effective single-half
        # shift amount is (n - 32), which SHL/SHR/SAR's own CL form reads
        # correctly straight out of CL (only the low 5 bits are used --
        # (n-32) already fits in 0-31, and CL's masking is a no-op here
        # since we're intentionally relying on that same masking, not
        # fighting it).
        self._emit(bytes([0x83, 0xE9, 0x20]))  # SUB ECX, 32
        if op == "shl":
            self._emit(encode_mov_rr(Reg.EBX, Reg.EAX))
            self._emit(encode_shl_cl(Reg.EBX))              # hi = lo << (n-32)
            self._emit(encode_mov_ri(Reg.EAX, 0))           # lo = 0
        elif op == "shr":
            self._emit(encode_mov_rr(Reg.EAX, Reg.EBX))
            self._emit(encode_shr_cl(Reg.EAX))              # lo = hi >> (n-32), logical
            self._emit(encode_mov_ri(Reg.EBX, 0))           # hi = 0
        else:  # sar
            self._emit(encode_mov_rr(Reg.EAX, Reg.EBX))
            self._emit(encode_sar_cl(Reg.EAX))              # lo = hi >> (n-32), arithmetic
            # hi = sign-fill: 0 if hi's original sign bit was 0, else
            # all-ones. EBX still holds the ORIGINAL hi at this point
            # (never overwritten above) -- SAR EBX, 31 broadcasts its
            # sign bit across all 32 bits, exactly the fill value
            # needed.
            self._emit(bytes([0xC1, 0xFB, 0x1F]))           # SAR EBX, 31

        done_target = self._pos()
        struct.pack_into("<i", self.buf, done_off + 1, done_target - (done_off + 5))
        self._emit(self._store_pair(instr.result, Reg.EAX, Reg.EBX))

    # ── Call (cdecl) ──────────────────────────────────────────────────────────

    def _push_arg(self, av: Any) -> None:
        """Push one cdecl argument, right value width depending on type
        -- called in REVERSE argument order by _call (real cdecl pushes
        right-to-left, so the first argument ends up at the lowest
        address / [esp+0] once the callee's own prologue looks for it,
        matching the C ABI's `printf(fmt, a, b)` stack layout with `fmt`
        closest to the return address)."""
        typ = av.type.name if hasattr(av, "type") else "i64"
        if typ == "i64":
            # Push hi first, then lo -- lo ends up at the lower address,
            # matching a real little-endian 8-byte value's in-memory
            # layout (this mirrors how regalloc.py's own i64 StackLoc
            # convention stores lo at the lower offset).
            loc = self._loc(av)
            assert isinstance(loc, StackLoc), f"i64 arg expected StackLoc for {av.name}"
            self._emit(encode_push_m(Mem(Reg.EBP, loc.offset + 4)))
            self._emit(encode_push_m(Mem(Reg.EBP, loc.offset)))
        elif typ == "f64":
            # No direct "push xmm" instruction exists -- round-trip
            # through a GP scratch register, pushing the raw 8 bytes as
            # two 32-bit halves (hi first, matching the i64 case above,
            # since cdecl has no float-register argument passing at all;
            # every float argument is pushed as its raw bit pattern).
            x, ld = self._xmm(av)
            self._emit(ld)
            loc = self._loc(av)
            if isinstance(loc, XmmLoc):
                # Value is in a real XMM register -- spill it to a fixed
                # scratch slot first so its two 32-bit halves can be
                # pushed individually (no instruction extracts an XMM
                # register's high 32 bits directly into a GP register
                # without SSE4.1's PEXTRD, which this encoder doesn't
                # implement).
                self._emit(encode_sub_ri(Reg.ESP, 8))
                self._emit(encode_movsd_mr(Mem(Reg.ESP, 0), x))
                self._emit(encode_mov_rm(self._SCRATCH, Mem(Reg.ESP, 4)))
                self._emit(encode_mov_rm(self._SCRATCH2, Mem(Reg.ESP, 0)))
                self._emit(encode_add_ri(Reg.ESP, 8))
                self._emit(encode_push(self._SCRATCH))
                self._emit(encode_push(self._SCRATCH2))
            else:
                self._emit(encode_push_m(Mem(Reg.EBP, loc.offset + 4)))
                self._emit(encode_push_m(Mem(Reg.EBP, loc.offset)))
        elif typ == "f32":
            x, ld = self._xmm_f32(av)
            self._emit(ld)
            loc = self._loc(av)
            if isinstance(loc, XmmLoc):
                self._emit(encode_sub_ri(Reg.ESP, 4))
                self._emit(encode_movss_mr(Mem(Reg.ESP, 0), x))
                self._emit(encode_mov_rm(self._SCRATCH, Mem(Reg.ESP, 0)))
                self._emit(encode_add_ri(Reg.ESP, 4))
                self._emit(encode_push(self._SCRATCH))
            else:
                self._emit(encode_push_m(Mem(Reg.EBP, loc.offset)))
        else:
            slot = self.alloc.alloca_slots.get(av.name)
            if slot is not None:
                self._emit(encode_lea(self._SCRATCH, Mem(Reg.EBP, slot)))
                self._emit(encode_push(self._SCRATCH))
                return
            loc = self._loc(av)
            if isinstance(loc, RegLoc):
                self._emit(encode_push(loc.reg))
            else:
                self._emit(encode_push_m(Mem(Reg.EBP, loc.offset)))

    def _call(self, instr: Any) -> None:
        target_op = instr.operands[0]
        arg_vals  = instr.operands[1:]

        is_indirect = hasattr(target_op, "name") and hasattr(target_op, "type")

        # cdecl: every argument goes on the stack, pushed right-to-left
        # (reverse of argument order) so the FIRST argument ends up
        # closest to the return address -- the only ABI convention this
        # backend supports (no register-argument fast path at all,
        # unlike x86-64's SysV/Win64). The caller cleans the stack
        # afterward (`add esp, N`), unlike stdcall/fastcall where the
        # CALLEE does -- this compiler only ever targets cdecl externs
        # (libc, its own runtime helpers), so there's no stdcall/fastcall
        # case to handle here.
        #
        # If the call target itself is indirect (a function-pointer
        # value, not a bare symbol name), materialize it into a fixed
        # scratch register BEFORE pushing any arguments -- pushing
        # arguments first would be fine too (nothing about argument
        # pushes touches _SCRATCH once each push completes), but
        # resolving it early means a spilled function pointer's own load
        # can't alias any argument's own spill load by accident (each
        # runs to completion, emitting its own instructions, before the
        # next begins -- there's no overlap to alias in the first
        # place, but resolving up front keeps the sequencing obviously
        # safe rather than merely accidentally safe).
        target_reg: Reg | None = None
        if is_indirect:
            target_reg, ld = self._gp(target_op)
            self._emit(ld)
            if target_reg != self._SCRATCH2:
                self._emit(encode_mov_rr(self._SCRATCH2, target_reg))
                target_reg = self._SCRATCH2

        pushed_bytes = 0
        for av in reversed(arg_vals):
            typ = av.type.name if hasattr(av, "type") else "i64"
            pushed_bytes += 8 if typ in ("i64", "f64") else 4
            self._push_arg(av)

        if is_indirect:
            self._emit(encode_call_r(target_reg))
        else:
            target = str(target_op)
            call_off = self._pos()
            self._emit(encode_call_rel32(0))
            if self.needs_pic:
                self.fixups.append((call_off + 1, target, R_386_PLT32))
            else:
                self.fixups.append((call_off + 1, target, R_386_PC32))

        if pushed_bytes:
            self._emit(encode_add_ri(Reg.ESP, pushed_bytes))

        if instr.result is not None:
            rtyp = instr.result.type.name
            if rtyp == "i64":
                # cdecl returns a 64-bit value in EDX:EAX (matching this
                # backend's own division-helper runtime convention --
                # see __udivdi64 etc. -- and real cdecl practice for
                # 64-bit return values on this ABI).
                self._emit(self._store_pair(instr.result, Reg.EAX, Reg.EDX))
            elif _is_float(rtyp):
                loc = self._loc(instr.result)
                if isinstance(loc, XmmLoc):
                    if rtyp == "f32":
                        # Real x87-FPU-return convention: cdecl returns
                        # float/double in ST(0), not XMM0 (no SSE
                        # calling-convention variant for THIS backend's
                        # own runtime helpers/libc target) -- round-trip
                        # through a scratch stack slot exactly like the
                        # x87-to-XMM boundary any SSE-targeting compiler
                        # must cross when calling an x87-returning
                        # function. FSTP stores ST(0) and pops the x87
                        # stack in one step.
                        self._emit(encode_sub_ri(Reg.ESP, 4))
                        self._emit(bytes([0xD9, 0x1C, 0x24]))  # FSTP DWORD PTR [ESP]
                        self._emit(encode_movss_rm(loc.reg, Mem(Reg.ESP, 0)))
                        self._emit(encode_add_ri(Reg.ESP, 4))
                    else:
                        self._emit(encode_sub_ri(Reg.ESP, 8))
                        self._emit(bytes([0xDD, 0x1C, 0x24]))  # FSTP QWORD PTR [ESP]
                        self._emit(encode_movsd_rm(loc.reg, Mem(Reg.ESP, 0)))
                        self._emit(encode_add_ri(Reg.ESP, 8))
                else:
                    if rtyp == "f32":
                        self._emit(bytes([0xD9, 0x5D]) + struct.pack("b", loc.offset) if -128 <= loc.offset <= 127 else bytes([0xD9, 0x9D]) + struct.pack("<i", loc.offset))
                    else:
                        self._emit(bytes([0xDD, 0x5D]) + struct.pack("b", loc.offset) if -128 <= loc.offset <= 127 else bytes([0xDD, 0x9D]) + struct.pack("<i", loc.offset))
            else:
                loc = self._loc(instr.result)
                if isinstance(loc, RegLoc):
                    if loc.reg != Reg.EAX:
                        self._emit(encode_mov_rr(loc.reg, Reg.EAX))
                else:
                    self._emit(encode_mov_mr(Mem(Reg.EBP, loc.offset), Reg.EAX))

    # ── Global/string/TLS addressing (GOTOFF-relative, PIC-only) ─────────────
    #
    # This backend only ever builds real PIC code (see this module's own
    # docstring) -- there is no absolute-addressing fallback path.
    # `global_addr`/`str_global` targets are always LOCAL symbols within
    # this compiler's own whole-program-merged output (every module gets
    # merged into one translation unit before codegen -- see program.py's
    # own merge pass), so the direct `lea dst, [ebx + symbol@GOTOFF]` form
    # is always correct here; the indirect `mov dst, [ebx + symbol@GOT]`
    # form real compilers use for a truly EXTERNAL data symbol (verified
    # via a real `gcc -m32 -fPIC` reference during this backend's PIC
    # design work: `movl global_var@GOT(%eax), %eax` followed by a second
    # dereferencing `movl (%eax), %eax`) is never needed since this
    # compiler has no notion of an external, separately-compiled global
    # variable to import.

    def _lea_gotoff(self, dst: Reg, symbol: str) -> None:
        """LEA dst, [EBX + symbol@GOTOFF] -- always the disp32 encoding
        (mod=10), forced explicitly rather than through encode_lea/Mem
        (which would pick the compact disp8 form for the placeholder
        value 0, leaving no room for the real relocated offset). Mirrors
        this file's own PIC-prologue GOTPC `add`, which forces the same
        kind of always-4-byte field for the identical reason."""
        lea_off = self._pos()
        modrm = 0b10_000_011 | ((int(dst) & 7) << 3)  # mod=10, rm=011(EBX)
        self._emit(bytes([0x8D, modrm]) + struct.pack("<i", 0))
        self.fixups.append((lea_off + 2, symbol, R_386_GOTOFF))

    def _global_addr(self, instr: Any) -> None:
        r = instr.result
        symbol = str(instr.operands[0])
        loc = self._loc(r)
        dst = loc.reg if isinstance(loc, RegLoc) else self._SCRATCH
        self._lea_gotoff(dst, symbol)
        if isinstance(loc, StackLoc):
            self._emit(encode_mov_mr(Mem(Reg.EBP, loc.offset), dst))

    def _tls_addr(self, instr: Any) -> None:
        """Thread-local storage, local-exec model (matching the x86-64
        backend's own tls_addr -- both target the same simplified
        single-TU, statically-linked TLS model). Linux i386's variant-II
        TLS ABI addresses the thread pointer through the GS segment
        (encoder.py's own encode_mov_tls_rm docstring), the 32-bit
        counterpart of x86-64's FS-based convention -- one segment
        register over, not the same one; an earlier draft of this
        comment wrongly said FS here, copied from x86-64's own R_386_TLS_
        TPOFF-adjacent comment without checking the actual encoder.

        disp32 fixup offset is 3, NOT 5 (x86-64's own R_X86_64_TPOFF32
        fixup uses +5 because ITS encode_mov_tls_rm emits a REX prefix
        before the 0x8B opcode+ModRM -- 4 bytes total before the
        displacement). This backend's 32-bit encoding has no REX prefix
        at all (no such concept in 32-bit mode) and needs no SIB byte
        either (ModRM's rm=101 already selects disp32-only addressing on
        its own): `65 8B 05` is the complete 3-byte prefix+opcode+ModRM
        sequence, confirmed directly against encode_mov_tls_rm's own
        real output (`encode_mov_tls_rm(Reg.EAX, 0x12345678).hex()` =
        `658b0578563412` -- 3 prefix/opcode/modrm bytes then the 4-byte
        displacement) rather than assumed by copying x86-64's offset.
        """
        r = instr.result
        symbol = str(instr.operands[0])
        dst, spill = self._dst_gp_spillable(r)
        tls_off = self._pos()
        self._emit(encode_mov_tls_rm(dst, 0))
        self.fixups.append((tls_off + 3, symbol, R_386_TLS_TPOFF))
        spill()

    # ── Bitcasts (i64 <-> f64 raw-bit reinterpretation) ──────────────────────
    #
    # Unlike x86-64 (a single MOVQ moves a full 64-bit value between a GP
    # register and an XMM register directly -- encode_movq_xmm_gp/
    # encode_movq_gp_xmm), no 32-bit GP register can hold more than half
    # of a 64-bit value's bits, so there is no direct-register-move
    # instruction to reuse here at all. But i64 is ALWAYS StackLoc (an
    # 8-byte, contiguous, lo-then-hi EBP-relative slot -- regalloc.py's
    # _is_wide_int) and f64 is a genuine 8-byte IEEE-754 value stored
    # either in a real XMM register or an identically-shaped 8-byte
    # StackLoc -- so bit-for-bit, an i64 value's memory representation
    # and an f64 value's memory representation are ALREADY the same
    # bytes in the same order. The "conversion" therefore never needs to
    # shuffle any bits: it's a plain 8-byte copy between the source's
    # already-correct memory location and the destination's, going
    # through XMM only when the destination happens to be a real XMM
    # register (there's no "load memory directly as the other type"
    # instruction that skips XMM entirely for the f64 side).

    def _bitcast_i2f(self, instr: Any) -> None:
        src = instr.operands[0]
        r = instr.result
        src_loc = self._loc(src)
        assert isinstance(src_loc, StackLoc), f"i64 bitcast source expected StackLoc for {src.name}"
        dst_loc = self._loc(r)
        if isinstance(dst_loc, XmmLoc):
            self._emit(encode_movsd_rm(dst_loc.reg, Mem(Reg.EBP, src_loc.offset)))
        else:
            # Both sides are 8-byte EBP-relative memory -- copy via a GP
            # scratch register, two 32-bit halves (no XMM round-trip
            # needed at all when neither side is ever a register).
            assert isinstance(dst_loc, StackLoc), f"f64 bitcast result expected a Location for {r.name}"
            self._emit(encode_mov_rm(self._SCRATCH, Mem(Reg.EBP, src_loc.offset)))
            self._emit(encode_mov_mr(Mem(Reg.EBP, dst_loc.offset), self._SCRATCH))
            self._emit(encode_mov_rm(self._SCRATCH, Mem(Reg.EBP, src_loc.offset + 4)))
            self._emit(encode_mov_mr(Mem(Reg.EBP, dst_loc.offset + 4), self._SCRATCH))

    def _bitcast_f2i(self, instr: Any) -> None:
        src = instr.operands[0]
        r = instr.result
        dst_loc = self._loc(r)
        assert isinstance(dst_loc, StackLoc), f"i64 bitcast result expected StackLoc for {r.name}"
        src_loc = self._loc(src)
        if isinstance(src_loc, XmmLoc):
            self._emit(encode_movsd_mr(Mem(Reg.EBP, dst_loc.offset), src_loc.reg))
        else:
            assert isinstance(src_loc, StackLoc), f"f64 bitcast source expected a Location for {src.name}"
            self._emit(encode_mov_rm(self._SCRATCH, Mem(Reg.EBP, src_loc.offset)))
            self._emit(encode_mov_mr(Mem(Reg.EBP, dst_loc.offset), self._SCRATCH))
            self._emit(encode_mov_rm(self._SCRATCH, Mem(Reg.EBP, src_loc.offset + 4)))
            self._emit(encode_mov_mr(Mem(Reg.EBP, dst_loc.offset + 4), self._SCRATCH))

    # ── Per-instruction dispatch ──────────────────────────────────────────────

    _IOPS = {
        "iadd": encode_add_rr,  "isub": encode_sub_rr,
        "imul": encode_imul_rr, "iand": encode_and_rr,
        "ior":  encode_or_rr,   "ixor": encode_xor_rr,
    }
    _ICMP: dict[str, CC] = {
        "icmp.eq": CC.E,   "icmp.ne": CC.NE,
        "icmp.lt": CC.L,   "icmp.le": CC.LE,
        "icmp.gt": CC.G,   "icmp.ge": CC.GE,
        "icmp.ult": CC.B,  "icmp.ule": CC.BE,
        "icmp.ugt": CC.A,  "icmp.uge": CC.AE,
    }
    _FCMP: dict[str, CC] = {
        "fcmp.eq": CC.E,  "fcmp.ne": CC.NE,
        "fcmp.lt": CC.B,  "fcmp.le": CC.BE,
        "fcmp.gt": CC.A,  "fcmp.ge": CC.AE,
    }
    _FOPS_F64 = {"fadd": encode_addsd, "fsub": encode_subsd,
                 "fmul": encode_mulsd, "fdiv": encode_divsd}
    _FOPS_F32 = {"fadd": encode_addss, "fsub": encode_subss,
                 "fmul": encode_mulss, "fdiv": encode_divss}

    _SIMD_OPS: dict[str, tuple] = {
        "simd.add":   (encode_addps,   encode_addpd,   encode_paddd),
        "simd.sub":   (encode_subps,   encode_subpd,   encode_psubd),
        "simd.mul":   (encode_mulps,   encode_mulpd,   encode_pmulld),
        "simd.div":   (encode_divps,   encode_divpd,   None),
        "simd.max":   (encode_maxps,   encode_maxpd,   None),
        "simd.min":   (encode_minps,   encode_minpd,   None),
        "simd.and":   (encode_andps,   encode_andpd,   encode_pand),
        "simd.or":    (encode_orps,    encode_orpd,    encode_por),
        "simd.xor":   (encode_xorps,   encode_xorpd,   encode_pxor),
        "simd.cmpeq": (None,           None,           encode_pcmpeqd),
    }

    def _instr(self, instr: Any) -> None:  # noqa: C901
        op  = instr.op
        r   = instr.result
        ops = instr.operands

        # ── integer binary ────────────────────────────────────────────────────
        if op in self._IOPS:
            wide = _is_wide_int(ops[0].type.name) if hasattr(ops[0], "type") else False
            if wide:
                if op == "iadd":
                    self._iadd64(r, ops[0], ops[1], sub=False); return
                if op == "isub":
                    self._iadd64(r, ops[0], ops[1], sub=True); return
                if op == "imul":
                    self._imul64(r, ops[0], ops[1]); return
                # iand/ior/ixor: each 32-bit half is independent -- no
                # carry/borrow propagation needed (unlike add/sub/mul),
                # so a bitwise op on each half separately already IS the
                # correct 64-bit result. Route through the ordinary
                # native _binop_gp twice, once per half, via two
                # synthetic FakeValue-free calls isn't possible (no
                # per-half IRValue exists) -- do it directly here instead.
                a_lo, a_hi, a_ld = self._gp_pair(ops[0])
                self._emit(a_ld)
                self._emit(encode_mov_rr(Reg.EAX, a_lo))
                self._emit(encode_mov_rr(Reg.EBX, a_hi))
                b_lo, b_hi, b_ld = self._gp_pair(ops[1])
                self._emit(b_ld)
                self._emit(self._IOPS[op](Reg.EAX, b_lo))
                self._emit(self._IOPS[op](Reg.EBX, b_hi))
                self._emit(self._store_pair(r, Reg.EAX, Reg.EBX))
                return
            self._binop_gp(r, ops[0], ops[1], self._IOPS[op])
            return

        if op in ("idiv", "irem", "udiv", "urem"):
            if _is_wide_int(ops[0].type.name):
                # Real cdecl calls into this backend's own already-
                # verified runtime helpers (abi_shims_x86_32.asm) --
                # __udivdi64/__divdi64/__umoddi64/__moddi64, each taking
                # (dividend_lo, dividend_hi, divisor_lo, divisor_hi) as
                # four plain cdecl arguments and returning quotient/
                # remainder in EAX:EDX (confirmed directly from that
                # file's own prologue: `[ebp+8]`=dividend_lo,
                # `[ebp+12]`=dividend_hi, `[ebp+16]`=divisor_lo,
                # `[ebp+20]`=divisor_hi -- exactly the layout this
                # backend's own _call already produces for two i64
                # arguments pushed right-to-left). No new marshaling
                # logic needed: construct a synthetic call instruction
                # and hand it straight to the already-verified _call.
                helper = {
                    "idiv": "__divdi64", "irem": "__moddi64",
                    "udiv": "__udivdi64", "urem": "__umoddi64",
                }[op]
                synthetic = _SyntheticCallInstr(result=r, operands=[helper, ops[0], ops[1]])
                self._call(synthetic)
                return
            self._div(instr); return

        if op == "ineg":
            if _is_wide_int(ops[0].type.name):
                # -(x) == 0 - x. A REAL bug lived in an earlier version
                # of this sequence: `NEG EAX; NEG EBX; SBB EBX, 0` looks
                # plausible but is wrong -- NEG EBX sets CF based on
                # EBX's OWN result (whether the original hi was nonzero),
                # clobbering the borrow-out from the EARLIER `NEG EAX`
                # before the following SBB ever reads it. Confirmed via a
                # real Unicorn execution failure: -0x100000000 (lo=0,
                # hi=1) produced 0xfffffffe00000000 instead of the
                # correct 0xffffffff00000000 -- off by exactly one in
                # the high word, the signature of a dropped/wrong borrow.
                # Fixed by computing the SUBTRACTION explicitly (0 - lo,
                # 0 - hi - borrow) via SUB/SBB against a genuinely zeroed
                # register, exactly mirroring _iadd64's own already-
                # verified sub=True carry chain -- SUB's borrow-out is
                # consumed by the VERY NEXT instruction (SBB), with
                # nothing else allowed to touch flags in between, unlike
                # the broken NEG/NEG/SBB version which let a second flag-
                # setting instruction sit between the borrow's source and
                # its consumer.
                lo, hi, ld = self._gp_pair(ops[0])
                self._emit(ld)
                self._emit(encode_mov_ri(Reg.EAX, 0))
                self._emit(encode_sub_rr(Reg.EAX, lo))
                self._emit(encode_mov_ri(Reg.EBX, 0))
                self._emit(encode_sbb_rr(Reg.EBX, hi))
                self._emit(self._store_pair(r, Reg.EAX, Reg.EBX))
                return
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
            self._emit(ld)
            if src_r != dst: self._emit(encode_mov_rr(dst, src_r))
            self._emit(encode_neg(dst)); spill()
            return

        if op == "inot":
            if _is_wide_int(ops[0].type.name):
                # Bitwise NOT on each 32-bit half independently -- no
                # carry/borrow chain involved at all (unlike ineg),
                # exactly like iand/ior/ixor's own per-half independence.
                lo, hi, ld = self._gp_pair(ops[0])
                self._emit(ld)
                self._emit(encode_mov_rr(Reg.EAX, lo))
                self._emit(encode_mov_rr(Reg.EBX, hi))
                self._emit(encode_not(Reg.EAX))
                self._emit(encode_not(Reg.EBX))
                self._emit(self._store_pair(r, Reg.EAX, Reg.EBX))
                return
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
            self._emit(ld)
            if src_r != dst: self._emit(encode_mov_rr(dst, src_r))
            self._emit(encode_not(dst)); spill()
            return

        # ── shifts ────────────────────────────────────────────────────────────
        if op in ("shl", "shr", "sar"):
            if _is_wide_int(instr.operands[0].type.name):
                self._shift64(instr); return
            self._shift(instr); return

        # ── integer compare ───────────────────────────────────────────────────
        if op in self._ICMP:
            if _is_wide_int(ops[0].type.name):
                self._icmp64(r, ops[0], ops[1], self._ICMP[op]); return
            self._cmp_set(r, ops[0], ops[1], self._ICMP[op]); return

        # ── float binary ──────────────────────────────────────────────────────
        if op in self._FOPS_F64:
            f32 = ops[0].type.name == "f32"
            tbl = self._FOPS_F32 if f32 else self._FOPS_F64
            self._binop_xmm(r, ops[0], ops[1], tbl[op], f32=f32); return

        if op == "fneg":
            dst = self._dst_xmm(r)
            f32 = ops[0].type.name == "f32"
            # alt_scratch=True: route the SOURCE operand's spill-load
            # through _SCRATCH_XMM2 (XMM7), not the default _SCRATCH_XMM
            # (XMM6) -- the sign-mask construction below ALSO uses
            # _SCRATCH_XMM as its own scratch, and a plain (non-alt)
            # load here would collide with it. A real bug lived here:
            # an earlier version loaded the source via the default
            # scratch, then the sign-mask construction silently
            # clobbered it before the `mov(dst, src_x)` below ever read
            # it, producing an all-zero-XORed-with-itself result no
            # matter what the real input was (confirmed via a direct
            # Unicorn execution: every fneg call, f32 and f64 alike,
            # returned 0x0 regardless of input, and disassembling the
            # emitted bytes showed the sign-mask's own `movsd xmm6,...`
            # overwriting the source value that an EARLIER `movsd
            # xmm6,[ebp-8]` had just loaded into that exact same
            # register).
            src_x, ld = (self._xmm_f32 if f32 else self._xmm)(ops[0], alt_scratch=True)
            self._emit(ld)
            sign_bits = 0x80000000 if f32 else None
            mov = encode_movss_rr if f32 else encode_movsd_rr
            if f32:
                self._emit(encode_mov_ri(self._SCRATCH, sign_bits))
                self._emit(bytes([0x66, 0x0F, 0x6E]) + bytes([0xC0 | ((int(self._SCRATCH_XMM) & 7) << 3) | (int(self._SCRATCH) & 7)]))
                if src_x != dst: self._emit(mov(dst, src_x))
                self._emit(bytes([0x0F, 0x57, 0xC0 | ((int(dst) & 7) << 3) | (int(self._SCRATCH_XMM) & 7)]))
            else:
                # f64 sign bit is bit 63 -- split across both 32-bit
                # halves (0x80000000 in the HIGH dword, 0 in the low).
                # Load via a scratch stack slot: no single 32-bit GP
                # immediate can express bit 63 directly the way x86-64's
                # single 64-bit MOV RAX,imm64 does.
                self._emit(encode_sub_ri(Reg.ESP, 8))
                self._emit(encode_mov_ri(self._SCRATCH, 0))
                self._emit(encode_mov_mr(Mem(Reg.ESP, 0), self._SCRATCH))
                self._emit(encode_mov_ri(self._SCRATCH, 0x80000000))
                self._emit(encode_mov_mr(Mem(Reg.ESP, 4), self._SCRATCH))
                self._emit(encode_movsd_rm(self._SCRATCH_XMM, Mem(Reg.ESP, 0)))
                self._emit(encode_add_ri(Reg.ESP, 8))
                if src_x != dst: self._emit(mov(dst, src_x))
                self._emit(bytes([0x66, 0x0F, 0x57, 0xC0 | ((int(dst) & 7) << 3) | (int(self._SCRATCH_XMM) & 7)]))
            return

        # ── float compare ─────────────────────────────────────────────────────
        if op in self._FCMP:
            self._fcmp_set(r, ops[0], ops[1], self._FCMP[op],
                           f32=ops[0].type.name == "f32"); return

        # ── SIMD packed ops ───────────────────────────────────────────────────
        if op in self._SIMD_OPS:
            ps_fn, pd_fn, pi_fn = self._SIMD_OPS[op]
            lane = getattr(ops[0], "type", None)
            lane_name = lane.name if lane else "v128"
            if lane_name == "f32" and ps_fn:
                self._binop_simd(r, ops[0], ops[1], ps_fn)
            elif lane_name == "f64" and pd_fn:
                self._binop_simd(r, ops[0], ops[1], pd_fn)
            elif pi_fn:
                self._binop_simd(r, ops[0], ops[1], pi_fn)
            else:
                self._emit(encode_nop())
            return

        if op == "simd.mov":
            dst   = self._dst_xmm(r)
            src_x, ld = self._xmm(ops[0])
            self._emit(ld)
            if src_x != dst: self._emit(encode_movdqa_rr(dst, src_x))
            return

        if op == "simd.shufps":
            dst   = self._dst_xmm(r)
            a_x, a_ld = self._xmm(ops[0])
            b_x, b_ld = self._xmm(ops[1], alt_scratch=True)
            imm8  = int(ops[2]) if len(ops) > 2 else 0
            self._emit(a_ld + b_ld)
            if a_x != dst: self._emit(encode_movaps_rr(dst, a_x))
            self._emit(encode_shufps(dst, b_x, imm8))
            return

        if op == "simd.pshufd":
            dst   = self._dst_xmm(r)
            src_x, ld = self._xmm(ops[0])
            imm8  = int(ops[1]) if len(ops) > 1 else 0
            self._emit(ld)
            if src_x != dst: self._emit(encode_movdqa_rr(dst, src_x))
            self._emit(encode_pshufd(dst, dst, imm8))
            return

        # ── constants ─────────────────────────────────────────────────────────
        if op == "const":
            v = ops[0]
            if r is not None and r.type.name == "i64":
                iv = int(v) & 0xFFFFFFFFFFFFFFFF
                lo, hi = iv & 0xFFFFFFFF, (iv >> 32) & 0xFFFFFFFF
                self._emit(encode_mov_ri(Reg.EAX, lo))
                self._emit(encode_mov_ri(Reg.EBX, hi))
                self._emit(self._store_pair(r, Reg.EAX, Reg.EBX))
            elif r is not None and _is_float(r.type.name):
                loc = self._loc(r)
                if r.type.name == "f32":
                    bits = struct.pack("<f", float(v))
                    imm = int.from_bytes(bits, "little")
                    self._emit(encode_mov_ri(self._SCRATCH, imm))
                    if isinstance(loc, XmmLoc):
                        self._emit(encode_sub_ri(Reg.ESP, 4))
                        self._emit(encode_mov_mr(Mem(Reg.ESP, 0), self._SCRATCH))
                        self._emit(encode_movss_rm(loc.reg, Mem(Reg.ESP, 0)))
                        self._emit(encode_add_ri(Reg.ESP, 4))
                    else:
                        self._emit(encode_mov_mr(Mem(Reg.EBP, loc.offset), self._SCRATCH))
                else:
                    bits = struct.pack("<d", float(v))
                    lo = int.from_bytes(bits[0:4], "little")
                    hi = int.from_bytes(bits[4:8], "little")
                    self._emit(encode_mov_ri(Reg.EAX, lo))
                    self._emit(encode_mov_ri(Reg.EBX, hi))
                    if isinstance(loc, XmmLoc):
                        self._emit(encode_sub_ri(Reg.ESP, 8))
                        self._emit(encode_mov_mr(Mem(Reg.ESP, 0), Reg.EAX))
                        self._emit(encode_mov_mr(Mem(Reg.ESP, 4), Reg.EBX))
                        self._emit(encode_movsd_rm(loc.reg, Mem(Reg.ESP, 0)))
                        self._emit(encode_add_ri(Reg.ESP, 8))
                    else:
                        self._emit(encode_mov_mr(Mem(Reg.EBP, loc.offset), Reg.EAX))
                        self._emit(encode_mov_mr(Mem(Reg.EBP, loc.offset + 4), Reg.EBX))
            elif r is not None and _is_xmm(r.type.name):
                loc = self._loc(r)
                self._emit(encode_pxor(self._SCRATCH_XMM, self._SCRATCH_XMM))
                if isinstance(loc, XmmLoc):
                    if loc.reg != self._SCRATCH_XMM:
                        self._emit(encode_movdqa_rr(loc.reg, self._SCRATCH_XMM))
                else:
                    self._emit(encode_movdqu_mr(Mem(Reg.EBP, loc.offset), self._SCRATCH_XMM))
            else:
                loc = self._loc(r)
                self._emit(encode_mov_ri(self._SCRATCH, int(v)))
                if isinstance(loc, RegLoc):
                    if loc.reg != self._SCRATCH:
                        self._emit(encode_mov_rr(loc.reg, self._SCRATCH))
                else:
                    self._emit(encode_mov_mr(Mem(Reg.EBP, loc.offset), self._SCRATCH))
            return

        # ── memory ────────────────────────────────────────────────────────────
        if op == "load":
            ptr_r, ld = self._gp(ops[0])
            self._emit(ld)
            tname = r.type.name
            if tname == "i64":
                self._emit(encode_mov_rm(self._SCRATCH, Mem(ptr_r, 0)))
                self._emit(encode_mov_rm(self._SCRATCH2, Mem(ptr_r, 4)))
                self._emit(self._store_pair(r, self._SCRATCH, self._SCRATCH2))
            elif tname == "f64":
                loc = self._loc(r)
                if isinstance(loc, XmmLoc):
                    self._emit(encode_movsd_rm(loc.reg, Mem(ptr_r)))
                else:
                    self._emit(encode_movsd_rm(self._SCRATCH_XMM, Mem(ptr_r)))
                    self._emit(encode_movsd_mr(Mem(Reg.EBP, loc.offset), self._SCRATCH_XMM))
            elif tname == "f32":
                loc = self._loc(r)
                if isinstance(loc, XmmLoc):
                    self._emit(encode_movss_rm(loc.reg, Mem(ptr_r)))
                else:
                    self._emit(encode_movss_rm(self._SCRATCH_XMM, Mem(ptr_r)))
                    self._emit(encode_movss_mr(Mem(Reg.EBP, loc.offset), self._SCRATCH_XMM))
            elif tname == "v128":
                loc = self._loc(r)
                if isinstance(loc, XmmLoc):
                    self._emit(encode_movdqu_rm(loc.reg, Mem(ptr_r)))
                else:
                    self._emit(encode_movdqu_rm(self._SCRATCH_XMM, Mem(ptr_r)))
                    self._emit(encode_movdqu_mr(Mem(Reg.EBP, loc.offset), self._SCRATCH_XMM))
            elif tname in ("i8", "u8"):
                loc = self._loc(r)
                if isinstance(loc, RegLoc):
                    self._emit(encode_movzx_rm8(loc.reg, Mem(ptr_r)))
                else:
                    self._emit(encode_movzx_rm8(self._SCRATCH, Mem(ptr_r)))
                    self._emit(encode_mov_mr(Mem(Reg.EBP, loc.offset), self._SCRATCH))
            elif tname in ("i16", "u16"):
                loc = self._loc(r)
                if isinstance(loc, RegLoc):
                    self._emit(encode_movzx_rm16(loc.reg, Mem(ptr_r)))
                else:
                    self._emit(encode_movzx_rm16(self._SCRATCH, Mem(ptr_r)))
                    self._emit(encode_mov_mr(Mem(Reg.EBP, loc.offset), self._SCRATCH))
            else:  # i32, u32, ptr -- full 32-bit, this backend's native width
                loc = self._loc(r)
                if isinstance(loc, RegLoc):
                    self._emit(encode_mov_rm(loc.reg, Mem(ptr_r)))
                else:
                    self._emit(encode_mov_rm(self._SCRATCH, Mem(ptr_r)))
                    self._emit(encode_mov_mr(Mem(Reg.EBP, loc.offset), self._SCRATCH))
            return

        if op == "store":
            val = ops[0]
            ptr = ops[1]
            ptr_r, p_ld = self._gp(ptr)
            self._emit(p_ld)
            tname = val.type.name if hasattr(val, "type") else "i64"
            if tname == "i64":
                store_ptr_r = ptr_r
                if ptr_r == self._SCRATCH:
                    self._emit(encode_mov_rr(Reg.EAX, ptr_r))
                    store_ptr_r = Reg.EAX
                v_lo, v_hi, v_ld = self._gp_pair(val)
                self._emit(v_ld)
                self._emit(encode_mov_mr(Mem(store_ptr_r, 0), v_lo))
                self._emit(encode_mov_mr(Mem(store_ptr_r, 4), v_hi))
            elif tname == "f64":
                src_x, v_ld = self._xmm(val)
                self._emit(v_ld)
                self._emit(encode_movsd_mr(Mem(ptr_r), src_x))
            elif tname == "f32":
                src_x, v_ld = self._xmm_f32(val)
                self._emit(v_ld)
                self._emit(encode_movss_mr(Mem(ptr_r), src_x))
            elif tname == "v128":
                src_x, v_ld = self._xmm(val)
                self._emit(v_ld)
                self._emit(encode_movdqu_mr(Mem(ptr_r), src_x))
            elif tname in ("i8", "u8"):
                val_r, v_ld = self._gp(val)
                store_ptr_r = ptr_r
                if ptr_r == self._SCRATCH and v_ld:
                    self._emit(encode_mov_rr(self._SCRATCH2, ptr_r))
                    store_ptr_r = self._SCRATCH2
                self._emit(v_ld)
                self._emit(encode_mov_mr8(Mem(store_ptr_r), val_r))
            else:  # i32, u32, ptr
                val_r, v_ld = self._gp(val)
                store_ptr_r = ptr_r
                if ptr_r == self._SCRATCH and v_ld:
                    self._emit(encode_mov_rr(self._SCRATCH2, ptr_r))
                    store_ptr_r = self._SCRATCH2
                self._emit(v_ld)
                self._emit(encode_mov_mr(Mem(store_ptr_r), val_r))
            return

        if op == "gep":
            ptr_r, p_ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
            self._emit(p_ld)
            if isinstance(ops[1], int):
                self._emit(encode_lea(dst, Mem(ptr_r, ops[1])))
            else:
                idx_r, i_ld = self._gp(ops[1])
                self._emit(i_ld)
                if ptr_r != dst: self._emit(encode_mov_rr(dst, ptr_r))
                self._emit(encode_add_rr(dst, idx_r))
            spill()
            return

        if op == "alloca":
            return

        # ── type conversions ──────────────────────────────────────────────────
        if op == "sext":
            src_bits = int(ops[0].type.name[1:]) if ops[0].type.name[1:].isdigit() else 32
            if r is not None and r.type.name == "i64":
                # Sign-extend a narrower int into a full i64 register
                # pair: extend to 32 bits first (native width), then
                # broadcast the sign bit across the high dword via an
                # arithmetic right shift by 31.
                src_r, ld = self._gp(ops[0])
                self._emit(ld)
                self._emit(encode_mov_rr(Reg.EAX, src_r))
                if src_bits < 32:
                    self._emit(encode_movsx(Reg.EAX, Reg.EAX, src_bits))
                self._emit(encode_mov_rr(Reg.EBX, Reg.EAX))
                self._emit(bytes([0xC1, 0xFB, 0x1F]))  # SAR EBX, 31
                self._emit(self._store_pair(r, Reg.EAX, Reg.EBX))
                return
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
            self._emit(ld)
            self._emit(encode_movsx(dst, src_r, src_bits))
            spill()
            return

        if op == "zext":
            src_bits = int(ops[0].type.name[1:]) if ops[0].type.name[1:].isdigit() else 32
            if r is not None and r.type.name == "i64":
                src_r, ld = self._gp(ops[0])
                self._emit(ld)
                self._emit(encode_mov_rr(Reg.EAX, src_r))
                if src_bits < 32:
                    self._emit(encode_movzx(Reg.EAX, Reg.EAX, src_bits))
                self._emit(encode_mov_ri(Reg.EBX, 0))
                self._emit(self._store_pair(r, Reg.EAX, Reg.EBX))
                return
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
            self._emit(ld)
            if src_bits == 32:
                self._emit(encode_mov_rr(dst, src_r))
            else:
                self._emit(encode_movzx(dst, src_r, src_bits))
            spill()
            return

        if op == "trunc":
            if ops[0].type.name == "i64":
                # Truncating an i64 down to a native-width int just needs
                # the low 32-bit half -- the high half is discarded.
                lo, hi, ld = self._gp_pair(ops[0])
                dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
                self._emit(ld)
                if lo != dst: self._emit(encode_mov_rr(dst, lo))
                spill()
                return
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
            self._emit(ld)
            if src_r != dst: self._emit(encode_mov_rr(dst, src_r))
            spill()
            return

        if op == "sitofp":
            if ops[0].type.name == "i64":
                lo, hi, ld = self._gp_pair(ops[0])
                self._emit(ld)
                self._emit(encode_sub_ri(Reg.ESP, 8))
                self._emit(encode_mov_mr(Mem(Reg.ESP, 0), lo))
                self._emit(encode_mov_mr(Mem(Reg.ESP, 4), hi))
                self._emit(bytes([0xDF, 0x2C, 0x24]))  # FILD QWORD PTR [ESP]
                dst_loc = self._loc(r)
                if r.type.name == "f32":
                    self._emit(bytes([0xD9, 0x1C, 0x24]))  # FSTP DWORD PTR [ESP]  (narrows on store)
                    if isinstance(dst_loc, XmmLoc):
                        self._emit(encode_movss_rm(dst_loc.reg, Mem(Reg.ESP, 0)))
                    else:
                        self._emit(encode_mov_rm(self._SCRATCH, Mem(Reg.ESP, 0)))
                        self._emit(encode_mov_mr(Mem(Reg.EBP, dst_loc.offset), self._SCRATCH))
                    self._emit(encode_add_ri(Reg.ESP, 8))
                else:
                    self._emit(bytes([0xDD, 0x1C, 0x24]))  # FSTP QWORD PTR [ESP]
                    if isinstance(dst_loc, XmmLoc):
                        self._emit(encode_movsd_rm(dst_loc.reg, Mem(Reg.ESP, 0)))
                        self._emit(encode_add_ri(Reg.ESP, 8))
                    else:
                        self._emit(encode_mov_rm(self._SCRATCH, Mem(Reg.ESP, 0)))
                        self._emit(encode_mov_mr(Mem(Reg.EBP, dst_loc.offset), self._SCRATCH))
                        self._emit(encode_mov_rm(self._SCRATCH, Mem(Reg.ESP, 4)))
                        self._emit(encode_mov_mr(Mem(Reg.EBP, dst_loc.offset + 4), self._SCRATCH))
                        self._emit(encode_add_ri(Reg.ESP, 8))
                return
            src_r, ld = self._gp(ops[0])
            self._emit(ld)
            if r.type.name == "f32":
                self._emit(encode_cvtsi2ss(self._dst_xmm(r), src_r))
            else:
                self._emit(encode_cvtsi2sd(self._dst_xmm(r), src_r))
            return

        if op == "fptosi":
            if r is not None and r.type.name == "i64":
                f32 = ops[0].type.name == "f32"
                src_x, ld = (self._xmm_f32 if f32 else self._xmm)(ops[0])
                self._emit(ld)
                self._emit(encode_sub_ri(Reg.ESP, 8))
                if f32:
                    self._emit(encode_movss_mr(Mem(Reg.ESP, 0), src_x))
                    self._emit(bytes([0xD9, 0x04, 0x24]))  # FLD DWORD PTR [ESP]
                else:
                    self._emit(encode_movsd_mr(Mem(Reg.ESP, 0), src_x))
                    self._emit(bytes([0xDD, 0x04, 0x24]))  # FLD QWORD PTR [ESP]
                self._emit(bytes([0xDF, 0x3C, 0x24]))  # FISTP QWORD PTR [ESP]
                self._emit(encode_mov_rm(Reg.EAX, Mem(Reg.ESP, 0)))
                self._emit(encode_mov_rm(Reg.EBX, Mem(Reg.ESP, 4)))
                self._emit(encode_add_ri(Reg.ESP, 8))
                self._emit(self._store_pair(r, Reg.EAX, Reg.EBX))
                return
            dst, spill = self._dst_gp_spillable(r)
            if ops[0].type.name == "f32":
                src_x, ld = self._xmm_f32(ops[0])
                self._emit(ld); self._emit(encode_cvttss2si(dst, src_x))
            else:
                src_x, ld = self._xmm(ops[0])
                self._emit(ld); self._emit(encode_cvttsd2si(dst, src_x))
            spill()
            return

        if op == "fpext":
            src_x, ld = self._xmm_f32(ops[0])
            dst = self._dst_xmm(r)
            self._emit(ld)
            self._emit(bytes([0xF3, 0x0F, 0x5A, 0xC0 | ((int(dst) & 7) << 3) | (int(src_x) & 7)]))
            return

        if op == "fptrunc":
            src_x, ld = self._xmm(ops[0])
            dst = self._dst_xmm(r)
            self._emit(ld)
            self._emit(bytes([0xF2, 0x0F, 0x5A, 0xC0 | ((int(dst) & 7) << 3) | (int(src_x) & 7)]))
            return

        if op == "bitcast_i2f":
            self._bitcast_i2f(instr); return

        if op == "bitcast_f2i":
            self._bitcast_f2i(instr); return

        # ── control flow ──────────────────────────────────────────────────────
        if op == "ret":
            if ops:
                val = ops[0]
                if val.type.name == "i64":
                    lo, hi, ld = self._gp_pair(val)
                    self._emit(ld)
                    self._emit(encode_mov_rr(Reg.EAX, lo))
                    self._emit(encode_mov_rr(Reg.EDX, hi))
                elif _is_xmm(val.type.name):
                    # cdecl returns floats in ST(0) -- push the value's
                    # raw bits and FLD them, matching real cdecl practice
                    # (verified against a real gcc -m32 reference during
                    # this backend's _call design: a bare `ret` with no
                    # explicit register store leaves the value already
                    # sitting on the x87 stack from the function body's
                    # own arithmetic. Since this codegen's own float ops
                    # stay entirely in XMM registers rather than the x87
                    # stack throughout, load it onto the x87 stack right
                    # before returning instead.)
                    f32 = val.type.name == "f32"
                    get = self._xmm_f32 if f32 else self._xmm
                    src_x, ld = get(val)
                    self._emit(ld)
                    size = 4 if f32 else 8
                    self._emit(encode_sub_ri(Reg.ESP, size))
                    if f32:
                        self._emit(encode_movss_mr(Mem(Reg.ESP, 0), src_x))
                        self._emit(bytes([0xD9, 0x04, 0x24]))  # FLD DWORD PTR [ESP]
                    else:
                        self._emit(encode_movsd_mr(Mem(Reg.ESP, 0), src_x))
                        self._emit(bytes([0xDD, 0x04, 0x24]))  # FLD QWORD PTR [ESP]
                    self._emit(encode_add_ri(Reg.ESP, size))
                else:
                    src_r, ld = self._gp(val)
                    self._emit(ld)
                    if src_r != Reg.EAX:
                        self._emit(encode_mov_rr(Reg.EAX, src_r))
            self._epilogue()
            return

        if op == "br":
            self._jmp(str(ops[0])); return

        if op == "br.t":
            cond_r, ld = self._gp(ops[0])
            self._emit(ld)
            self._emit(encode_test_rr(cond_r, cond_r))
            self._jcc(CC.NE, str(ops[1]))
            self._jmp(str(ops[2]))
            return

        if op == "call":
            self._call(instr); return

        if op == "mov":
            if r is None:
                return
            if r.type.name == "i64":
                lo, hi, ld = self._gp_pair(ops[0])
                self._emit(ld)
                self._emit(self._store_pair(r, lo, hi))
            elif _is_xmm(r.type.name):
                dst = self._dst_xmm(r)
                src_x, ld = self._xmm(ops[0])
                self._emit(ld)
                if src_x != dst: self._emit(encode_movdqa_rr(dst, src_x))
            else:
                src_r, ld = self._gp(ops[0])
                dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
                self._emit(ld)
                if src_r != dst: self._emit(encode_mov_rr(dst, src_r))
                spill()
            return

        if op in ("global_addr", "str_global"):
            self._global_addr(instr); return

        if op == "tls_addr":
            self._tls_addr(instr); return

        if op == "debug_loc":
            fname = str(ops[0]) if ops else "<unknown>"
            line  = int(ops[1]) if len(ops) > 1 else 0
            self._debug_locs.append((self._pos(), fname, line))
            return

        self._emit(encode_nop())  # unknown op -- keep offsets consistent

    # ── Main entry ────────────────────────────────────────────────────────────

    def compile(self) -> FuncCode:
        self._prologue()

        for block in self.func.blocks:
            self.block_off[block.label] = self._pos()
            for instr in block.instrs:
                self._instr(instr)

        # ── Fix up internal branch targets ────────────────────────────────────
        relocs: list[tuple[int, str, int]] = []
        for patch_off, label, rtype in self.fixups:
            if label in self.block_off:
                rel32 = self.block_off[label] - (patch_off + 4)
                self.buf[patch_off:patch_off + 4] = struct.pack("<i", rel32)
            else:
                relocs.append((patch_off, label, rtype))  # external symbol

        return FuncCode(
            name=self.func.name,
            code=bytes(self.buf),
            relocs=relocs,
            visibility=getattr(self.func, "visibility", None),
            debug_locs=self._debug_locs,
        )


def compile_func(func: Any, alloc: AllocResult, needs_pic: bool = False) -> FuncCode:
    return FuncCodegen(func, alloc, needs_pic).compile()
