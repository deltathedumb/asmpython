"""
x86-64 code generator.

Translates one IRFunc to a flat byte stream using the register allocation
produced by regalloc.py.  Internal branch targets are fixed up in a second
pass; unresolved external calls are returned as relocation records so the
object-file emitter (elf.py / coff.py) can build a proper relocation table.

NOTE: Reg.R10/Reg.R11 and XmmReg.XMM15 are reserved as scratch registers and
      must not be present in the regalloc pool. Remove them from _GP_POOL and
      _XMM_POOL in regalloc.py if they ever appear there.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any, Callable

from .encoder import (
    encode_popcnt_rr, encode_bswap_r,
    Reg, XmmReg, CC, Mem,
    ARG_REGS_SYSV, ARG_REGS_WIN64,
    XMM_ARG_SYSV, XMM_ARG_WIN64,
    encode_mov_rr, encode_mov_ri, encode_mov_rm, encode_mov_mr, encode_lea, encode_lea_rip,
    encode_add_rr, encode_sub_rr, encode_and_rr, encode_or_rr, encode_xor_rr,
    encode_imul_rr, encode_idiv_r, encode_div_r, encode_neg, encode_not,
    encode_cmp_rr, encode_test_rr, encode_xor_zero,
    encode_add_ri, encode_sub_ri,
    encode_shl_ri, encode_shr_ri, encode_sar_ri,
    encode_shl_cl, encode_shr_cl, encode_sar_cl,
    encode_movsx, encode_movzx, encode_cqo,
    encode_push, encode_pop, encode_ret,
    encode_call_rel32, encode_call_r,
    encode_jmp_rel32, encode_jcc_rel32,
    encode_setcc, encode_nop,
    encode_movsd_rr, encode_addsd, encode_subsd, encode_mulsd, encode_divsd,
    encode_movsd_rm, encode_movsd_mr, encode_ucomisd,
    encode_cvtsi2sd, encode_cvttsd2si,
    encode_movq_xmm_gp, encode_movq_gp_xmm,
    encode_movss_rr, encode_addss, encode_subss, encode_mulss, encode_divss,
    encode_movss_rm, encode_movss_mr, encode_ucomiss,
    encode_cvtsi2ss, encode_cvttss2si,
    # typed byte/dword memory
    encode_movzx_rm8, encode_mov_mr8, encode_movzx_rm16,
    encode_mov_rm32, encode_mov_mr32,
    # TLS
    encode_mov_tls_rm,
    # SIMD packed float
    encode_addps, encode_subps, encode_mulps, encode_divps,
    encode_maxps, encode_minps, encode_andps, encode_orps, encode_xorps,
    encode_movaps_rr, encode_shufps,
    encode_addpd, encode_subpd, encode_mulpd, encode_divpd,
    encode_maxpd, encode_minpd, encode_andpd, encode_orpd, encode_xorpd,
    encode_movapd_rr,
    # SIMD packed int
    encode_paddb, encode_paddw, encode_paddd, encode_paddq,
    encode_psubb, encode_psubw, encode_psubd, encode_psubq,
    encode_pand, encode_por, encode_pxor,
    encode_pcmpeqb, encode_pcmpeqw, encode_pcmpeqd,
    encode_movdqa_rr, encode_movdqu_rr,
    encode_pmulld, encode_pshufd,
    # SIMD memory
    encode_movdqu_rm, encode_movdqu_mr,
    encode_movdqa_rm, encode_movdqa_mr,
    encode_movaps_rm, encode_movaps_mr,
)
from .regalloc import AllocResult, RegLoc, XmmLoc, StackLoc, Location

# ELF reloc types used by the codegen (imported by elf.py too, but defined here
# to avoid circular import — elf.py should import from here or define its own).
R_X86_64_PC32    = 2
R_X86_64_PLT32   = 4
R_X86_64_TPOFF32 = 23


# ── Output ────────────────────────────────────────────────────────────────────

@dataclass
class FuncCode:
    name:       str
    code:       bytes
    # (offset_of_rel32_field, symbol_name, reloc_type) — external relocations
    relocs:     list[tuple[int, str, int]] = field(default_factory=list)
    visibility: str | None = None   # "public" | "private" | "global" | None → default global
    # (code_offset, filename, line) — from debug_loc IR ops
    debug_locs: list[tuple[int, str, int]] = field(default_factory=list)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _hi(r: "Reg | XmmReg") -> int:
    return 1 if int(r) >= 8 else 0


def _gp_to_xmm(dst: XmmReg, src: Reg) -> bytes:
    """MOVQ xmm, r64 — move 64-bit GP value into XMM (bit-for-bit).

    Needs REX.W set: MOVD (32-bit) and MOVQ (64-bit) share the exact same
    opcode bytes (66 0F 6E /r) and differ *only* in that bit. Without it,
    this silently truncated to the low 32 bits -- harmless for f32 values
    (which fit there already) but for f64 it dropped the entire exponent
    and most of the mantissa for any value whose low 32 bits happen to be
    zero (any "round" double like 2.5, 0.5, 4.0, 100.0, ... loads as 0.0),
    and for fneg's f64 sign-bit mask (1<<63, also zero in its low 32 bits)
    it made the XOR a no-op -- `-x` on a float silently returned `x`.
    """
    rex = bytes([0x48 | (_hi(dst) << 2) | _hi(src)])
    return bytes([0x66]) + rex + bytes([0x0F, 0x6E, 0xC0 | ((int(dst) & 7) << 3) | (int(src) & 7)])


def _cvtss2sd(dst: XmmReg, src: XmmReg) -> bytes:
    """CVTSS2SD — widen f32 to f64."""
    rex = bytes([0x40 | (_hi(dst) << 2) | _hi(src)]) if (_hi(dst) or _hi(src)) else b""
    return bytes([0xF3]) + rex + bytes([0x0F, 0x5A, 0xC0 | ((int(dst) & 7) << 3) | (int(src) & 7)])


def _cvtsd2ss(dst: XmmReg, src: XmmReg) -> bytes:
    """CVTSD2SS — narrow f64 to f32."""
    rex = bytes([0x40 | (_hi(dst) << 2) | _hi(src)]) if (_hi(dst) or _hi(src)) else b""
    return bytes([0xF2]) + rex + bytes([0x0F, 0x5A, 0xC0 | ((int(dst) & 7) << 3) | (int(src) & 7)])


def _is_float(type_name: str) -> bool:
    return type_name in ("f32", "f64")


def _is_xmm(type_name: str) -> bool:
    """True if this type lives in an XMM register (floats or v128 SIMD)."""
    return type_name in ("f32", "f64", "v128")


# ── Code generator ────────────────────────────────────────────────────────────

class FuncCodegen:
    _SCRATCH     = Reg.R11        # reserved GP scratch — must not be in regalloc pool
    _SCRATCH2    = Reg.R10        # second GP scratch for addr/value alias cases
    _SCRATCH_XMM  = XmmReg.XMM15  # reserved XMM scratch
    _SCRATCH_XMM2 = XmmReg.XMM14  # second XMM scratch, mirrors _SCRATCH2 -- see _xmm's alt_scratch docstring

    def __init__(self, func: Any, alloc: AllocResult, abi: str) -> None:
        self.func  = func
        self.alloc = alloc
        self.abi   = abi
        self.buf   = bytearray()
        self.block_off: dict[str, int]          = {}
        # (patch_offset, label_or_symbol, reloc_type)
        # reloc_type=0 means internal branch (resolved in same pass, no ELF reloc)
        self.fixups: list[tuple[int, str, int]] = []
        self._debug_locs: list[tuple[int, str, int]] = []

    # ── Emit helpers ──────────────────────────────────────────────────────────

    def _emit(self, b: bytes) -> None:
        self.buf.extend(b)

    def _pos(self) -> int:
        return len(self.buf)

    # ── Location helpers ──────────────────────────────────────────────────────

    def _loc(self, val: Any) -> Location:
        return self.alloc.locs[val.name]

    def _gp(self, val: Any, alt_scratch: bool = False) -> tuple[Reg, bytes]:
        """Get a GP register holding val. Emits a load from stack if spilled.

        `alt_scratch`: use R10 (_SCRATCH2) instead of R11 (_SCRATCH) for the
        spilled/alloca case. Every two-operand helper below (`_binop_gp`/
        `_binop_simd`/`_cmp_set`/`_div`) calls `_gp` independently for each
        operand -- when BOTH operands are simultaneously stack-spilled (no
        real register), both calls return the SAME shared scratch register
        and the second `_gp`'s load silently clobbers the first's before
        either is read, corrupting the op into effectively `b OP b`.
        Confirmed via a real repro: `self._coef // some_call()` inside an
        instance method (the field read stays live across the call, gets
        spilled; the call's return value is also stack-homed once past the
        callee-saved-register restore) computed `b // b` (always 1) instead
        of `a // b`, disassembly showing two back-to-back `mov r11, [slot]`
        loads with only the second surviving. Passing `alt_scratch=True` for
        the SECOND operand of every such helper guarantees the two operands
        can never collide, without needing per-call-site collision
        detection.
        """
        scratch = self._SCRATCH2 if alt_scratch else self._SCRATCH
        slot = self.alloc.alloca_slots.get(val.name)
        if slot is not None:
            # alloca'd pointers never occupy a register or stack spill slot
            # of their own -- their "value" is simply the fixed rbp-relative
            # address regalloc.py recorded for them, recomputed fresh via
            # lea on every read instead of being cached in a register for
            # the value's whole (often function-spanning, e.g. a loop's
            # var/stop/step) lifetime. See the alloca opcode handler.
            return scratch, encode_lea(scratch, Mem(Reg.RBP, slot))
        loc = self._loc(val)
        if isinstance(loc, RegLoc):
            return loc.reg, b""
        return scratch, encode_mov_rm(scratch, Mem(Reg.RBP, loc.offset))

    def _xmm(self, val: Any, alt_scratch: bool = False) -> tuple[XmmReg, bytes]:
        """Get an XMM register holding val (f64 / v128). Emits load if spilled.

        `alt_scratch`: use XMM14 (_SCRATCH_XMM2) instead of XMM15
        (_SCRATCH_XMM) for the spilled case -- mirrors `_gp`'s own
        `alt_scratch` exactly, and exists for the identical reason: every
        two-operand XMM helper (`_binop_xmm`/`_fcmp_set`) calls `_xmm`
        independently for each operand, and when BOTH operands are
        simultaneously stack-spilled (no real register), both calls used
        to return the SAME shared scratch register (XMM15) -- the second
        call's load then silently clobbered the first's before either was
        read, corrupting e.g. `a * b` into `b * b`. Confirmed via a real
        repro: `round(f(), n)` for any call `f` -- the call's own return
        value gets spilled once it's forced to cross the LATER `pow(10, n)`
        call, and `pow`'s own float return value is also stack-homed;
        `_binop_xmm`'s `fmul(x, scale)` then loaded both operands into
        XMM15 back-to-back, disassembly showing `movsd -0x8(%rbp),%xmm15`
        immediately followed by `movsd -0x10(%rbp),%xmm15` with only the
        second value surviving to the actual `mulsd`. Passing
        `alt_scratch=True` for the SECOND operand of every such helper
        guarantees the two operands can never collide, exactly like the
        GP-side fix this one was missing."""
        loc = self._loc(val)
        if isinstance(loc, XmmLoc):
            return loc.reg, b""
        scratch = self._SCRATCH_XMM2 if alt_scratch else self._SCRATCH_XMM
        if isinstance(loc, RegLoc):
            # The f64 value lives in a GP register, not an XMM one -- regalloc
            # can home a float-typed IRValue in a GP RegLoc when it was produced
            # as raw 64-bit bits (e.g. an `any`-typed value loaded/stored through
            # the GP path, then reinterpreted as a float). Move the raw bits into
            # the scratch XMM with movq, mirroring `_gp`'s own RegLoc case (which
            # returns the GP register directly). Previously this fell through to
            # the StackLoc branch and read `loc.offset` on a RegLoc -- an
            # AttributeError that aborted the whole compile (hit compiling
            # Somnia's provider model, whose classmethod dispatch routes a float
            # transform component through a GP-homed `any` value).
            return scratch, encode_movq_xmm_gp(scratch, loc.reg)
        return scratch, encode_movsd_rm(scratch, Mem(Reg.RBP, loc.offset))

    def _xmm_f32(self, val: Any, alt_scratch: bool = False) -> tuple[XmmReg, bytes]:
        """Get an XMM register holding val (f32). Emits load if spilled.
        See `_xmm`'s `alt_scratch` docstring -- identical rationale/fix."""
        loc = self._loc(val)
        if isinstance(loc, XmmLoc):
            return loc.reg, b""
        scratch = self._SCRATCH_XMM2 if alt_scratch else self._SCRATCH_XMM
        if isinstance(loc, RegLoc):
            # f32 value homed in a GP register -- see `_xmm`'s RegLoc branch for
            # the full rationale. The raw bits are moved in via movq; the low 32
            # bits are the single-precision value the caller's movss-based ops
            # read.
            return scratch, encode_movq_xmm_gp(scratch, loc.reg)
        return scratch, encode_movss_rm(scratch, Mem(Reg.RBP, loc.offset))

    def _dst_gp(self, result: Any) -> Reg:
        """GP register to compute `result` into. Asserts a RegLoc -- use
        `_dst_gp_spillable` at any call site that must also tolerate a
        StackLoc (regalloc legitimately spills under real register
        pressure; this assert-only form is for call sites already proven
        safe, or being incrementally migrated)."""
        loc = self._loc(result)
        assert isinstance(loc, RegLoc), f"GP result expected for {result.name}"
        return loc.reg

    def _dst_xmm(self, result: Any) -> XmmReg:
        """XMM register to compute `result` into. See `_dst_gp`'s
        docstring -- `_dst_xmm_spillable` is the StackLoc-tolerant form."""
        loc = self._loc(result)
        assert isinstance(loc, XmmLoc), f"XMM result expected for {result.name}"
        return loc.reg

    def _dst_gp_spillable(self, result: Any, alt_scratch: bool = False) -> "tuple[Reg, Callable[[], None]]":
        """Like `_dst_gp`, but tolerates regalloc having spilled `result`
        to a StackLoc (a real, legitimate outcome under high register
        pressure -- confirmed via a real crash on 196_hashlib_module.py's
        MD5 block processing, 400+ live temporaries in one function).
        Returns `(reg_to_compute_into, spill)`: `reg_to_compute_into` is
        the real assigned register for a RegLoc, or a scratch register
        for a StackLoc; the caller MUST call `spill()` immediately after
        finishing the computation that writes into that register (a
        no-op for the RegLoc case, an actual store for the StackLoc
        case). Mirrors the existing correct global_addr/str_global
        pattern (this file, `_instr`'s `op in ("global_addr",
        "str_global")` branch) generalized into a reusable helper.

        `alt_scratch=True` routes the spilled case through `_SCRATCH2`
        instead of `_SCRATCH` -- required whenever an operand was ALSO
        loaded via `_gp`'s own (default `_SCRATCH`) spill path earlier
        in the same instruction, or the two would alias the same
        register and the destination write would clobber the still-
        needed operand. Same collision this session already fixed once
        for `_binop_gp`/`_cmp_set`/`_div`'s own operand-vs-operand
        spills -- this is the destination-vs-operand version of the
        identical hazard."""
        loc = self._loc(result)
        if isinstance(loc, RegLoc):
            return loc.reg, lambda: None
        assert isinstance(loc, StackLoc), f"GP result expected for {result.name}"
        scratch = self._SCRATCH2 if alt_scratch else self._SCRATCH
        return scratch, lambda: self._emit(encode_mov_mr(Mem(Reg.RBP, loc.offset), scratch))

    def _dst_xmm_spillable(self, result: Any, alt_scratch: bool = False) -> "tuple[XmmReg, Callable[[], None]]":
        """XMM counterpart of `_dst_gp_spillable` -- see its docstring.

        `alt_scratch=True` routes the spilled case through `_SCRATCH_XMM2`
        (XMM14) instead of `_SCRATCH_XMM` (XMM15) -- required at any site
        whose own operand load may already occupy `_SCRATCH_XMM` (a spilled
        XMM operand read via `_xmm`, or an op like `fneg` that stakes out
        `_SCRATCH_XMM` for a constant it builds), so destination and operand
        don't alias the one scratch. Same hazard `_dst_gp_spillable`'s
        `alt_scratch` guards against on the GP side.

        Emits a `movsd` (8-byte) store, so this is for SCALAR f32/f64 results
        only; a v128 result would need a 16-byte `movdqu` and must not be
        routed through here."""
        loc = self._loc(result)
        if isinstance(loc, XmmLoc):
            return loc.reg, lambda: None
        assert isinstance(loc, StackLoc), f"XMM result expected for {result.name}"
        scratch = self._SCRATCH_XMM2 if alt_scratch else self._SCRATCH_XMM
        return scratch, lambda: self._emit(encode_movsd_mr(Mem(Reg.RBP, loc.offset), scratch))

    # ── Prologue / epilogue ───────────────────────────────────────────────────

    def _prologue(self) -> None:
        for r in self.alloc.callee_saved:
            self._emit(encode_push(r))
        self._emit(encode_push(Reg.RBP))
        self._emit(encode_mov_rr(Reg.RBP, Reg.RSP))
        if self.alloc.stack_bytes:
            # Windows: probe the stack if frame > 4096 bytes to grow guard pages.
            if self.abi == "win64" and self.alloc.stack_bytes > 4096:
                self._emit(encode_mov_ri(Reg.RAX, self.alloc.stack_bytes))
                call_off = self._pos()
                self._emit(encode_call_rel32(0))
                self.fixups.append((call_off + 1, "__chkstk", R_X86_64_PLT32))
            self._emit(encode_sub_ri(Reg.RSP, self.alloc.stack_bytes))

        # Copy every call-crossing parameter out of its (caller-saved) incoming
        # ABI argument register into the stack slot regalloc assigned it. Must
        # be the first thing after the frame exists and before any user code,
        # since the argument registers are live only on entry -- and must run
        # even when stack_bytes is 0 for the frame-less case to stay correct.
        # See regalloc.py's `_home_param` for why these parameters cannot stay
        # in their argument registers.
        for reg, offset, type_name in self.alloc.param_spills:
            mem = Mem(Reg.RBP, offset)
            if type_name == "f64":
                self._emit(encode_movsd_mr(mem, reg))
            elif type_name == "f32":
                self._emit(encode_movss_mr(mem, reg))
            else:
                self._emit(encode_mov_mr(mem, reg))

    def _epilogue(self) -> None:
        if self.alloc.stack_bytes:
            self._emit(encode_add_ri(Reg.RSP, self.alloc.stack_bytes))
        self._emit(encode_pop(Reg.RBP))
        for r in reversed(self.alloc.callee_saved):
            self._emit(encode_pop(r))
        self._emit(encode_ret())

    # ── Pattern helpers ───────────────────────────────────────────────────────

    def _binop_gp(self, result: Any, a: Any, b: Any, rr_fn) -> None:
        """dst = rr_fn(a, b) — loads a into dst first, then applies op with b.

        `dst` is `result`'s own register when regalloc gave it a RegLoc
        (the common case). When `result` is spilled to a StackLoc
        instead (a real, legitimate outcome under high register
        pressure -- confirmed crash on 196_hashlib_module.py's SHA/MD5
        processing), the op is computed in place on `a_r` ONLY when
        `a_r` is safe to clobber -- i.e. `a` ITSELF was already spilled,
        so `a_r` is a throwaway scratch copy of `a`'s value, not `a`'s
        real backing register. If `a` has a genuine RegLoc (so `a_r` IS
        that live register, needed by any later instruction that reads
        `a` again), the op is computed into `_SCRATCH2` instead, leaving
        `a`'s real register untouched.

        A real bug lived here previously: an earlier version ALWAYS
        computed `rr_fn(a_r, b_r)` in place unconditionally, before even
        checking `result`'s own location -- this corrupts `a`'s live
        register whenever `a` is reused after this instruction (e.g.
        `x = a + b; y = a + c`), regardless of whether `result` was
        spilled at all. Confirmed via a real regression this introduced:
        102_list_methods.py and several other previously-passing tests
        hung or mis-executed once that version landed.
        """
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
            # a_r IS a's real, possibly-still-live register -- must not
            # mutate it. Can't just reuse _SCRATCH2 as a third register
            # either: b_r may already alias _SCRATCH2 (b's own spill-load
            # used alt_scratch=True), so writing a_r's copy there first
            # would clobber b_r before rr_fn reads it. push/pop a real
            # stack slot instead of trying to find a third GP scratch
            # register (this backend only reserves two).
            self._emit(encode_push(a_r))
            self._emit(rr_fn(a_r, b_r))
            self._emit(encode_mov_mr(Mem(Reg.RBP, loc.offset), a_r))
            self._emit(encode_pop(a_r))
        else:
            # a was itself spilled -- a_r is a throwaway scratch copy,
            # safe to mutate in place.
            self._emit(rr_fn(a_r, b_r))
            self._emit(encode_mov_mr(Mem(Reg.RBP, loc.offset), a_r))

    def _binop_xmm(self, result: Any, a: Any, b: Any, rr_fn, f32: bool = False) -> None:
        get = self._xmm_f32 if f32 else self._xmm
        mov = encode_movss_rr if f32 else encode_movsd_rr
        # Spillable dst: a scalar float result can be homed on the stack when
        # it lives across a call (see regalloc's crosses-call spill). `dst`
        # defaults to _SCRATCH_XMM when spilled -- the same scratch `a` uses,
        # which is fine: if `a` is also spilled the `a_x != dst` guard skips a
        # redundant self-move, and `b` reads through the alt scratch below.
        dst, spill = self._dst_xmm_spillable(result)
        a_x, a_ld = get(a)
        b_x, b_ld = get(b, alt_scratch=True)
        self._emit(a_ld + b_ld)
        if a_x != dst:
            self._emit(mov(dst, a_x))
        self._emit(rr_fn(dst, b_x))
        spill()

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
        # setcc must immediately follow the flag-setting cmp -- zeroing dst
        # with `xor dst, dst` in between (the old order) clobbers the very
        # flags setcc reads, since XOR is itself flag-setting. setcc only
        # writes the low byte, so zero-extend dst *after* via movzx instead;
        # movzx doesn't touch flags, so it's safe to sequence post-setcc.
        #
        # Spillable dst (see _div's own comment on why): encode_setcc only
        # ever takes a register operand, never memory, so a spilled result
        # still needs a real scratch register to setcc/movzx into before
        # being stored out. alt_scratch=True: `b`'s own `_gp` load already
        # uses alt_scratch (R10) to avoid colliding with `a`'s load (R11)
        # when both are spilled -- the destination must pick a THIRD
        # register distinct from both, but this encoder only reserves two
        # GP scratches total (R10/R11), so instead sequence the
        # destination's own spill-load AFTER cmp/setcc have already
        # consumed a_r/b_r (nothing later needs them), making plain
        # _SCRATCH (R11) safe to reuse for the destination write.
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
        # Spillable dst, same rationale as _cmp_set -- a_x/b_x are XMM
        # registers, entirely disjoint from the GP scratch _dst_gp_spillable
        # would use, so no alt_scratch collision risk here either.
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

    # ── Division ──────────────────────────────────────────────────────────────

    def _div(self, instr: Any) -> None:
        op = instr.op
        a, b = instr.operands[0], instr.operands[1]
        a_r, a_ld = self._gp(a)
        # `a_in_rax` below ALSO needs R10 (_SCRATCH2) to preserve `a` when
        # a's permanent home IS RAX -- computed before loading b so b's own
        # scratch choice can avoid colliding with it. If both a and b are
        # simultaneously stack-spilled, _gp would otherwise hand both the
        # SAME shared scratch (R11), making b's load clobber a's before
        # either is read (see _gp's own docstring for the confirmed
        # repro/disassembly) -- alt_scratch routes b through R10 instead.
        # But when a_in_rax is ALSO true, R10 is already claimed for a's
        # save, so b must fall back to R11 in that specific combination
        # (safe there: a_in_rax means a_r == RAX, a REAL register, so a's
        # own load never touched R11 to begin with).
        a_in_rax = a_r == Reg.RAX
        b_r, b_ld = self._gp(b, alt_scratch=not a_in_rax)
        self._emit(a_ld + b_ld)

        # `idiv`/`div` unconditionally clobber RAX:RDX as scratch, regardless
        # of where `a` started. If regalloc happened to give `a` a PERMANENT
        # home of RAX (a real possibility -- RAX is an ordinary pool
        # register here, not reserved), skipping the `mov RAX, a_r` "because
        # it's already there" leaves nothing preserving `a`'s value: any
        # later read of `a` trusts regalloc's decision that it still lives
        # in RAX, but the division has already overwritten it. This is
        # silent, not a crash -- confirmed via a real repro (`n % 2 == 0`
        # lowers to idiv then irem on the same dividend `n`; when `n`'s
        # home is RAX, the second division reads the FIRST division's
        # quotient instead of `n`, corrupting the remainder) and a gdb
        # single-step trace showing RAX going 2 -> 1 (idiv's quotient) ->
        # read as if it were still 2 for the following irem. Preserve `a`
        # in the second scratch register before the divide, and restore it
        # into RAX afterward so any later `_gp(a)` call (which trusts
        # regalloc's RAX-resident answer) still finds the right value.
        if a_in_rax:
            self._emit(encode_mov_rr(self._SCRATCH2, Reg.RAX))
        else:
            self._emit(encode_mov_rr(Reg.RAX, a_r))
        if b_r == Reg.RDX:
            self._emit(encode_mov_rr(self._SCRATCH, b_r))
            b_r = self._SCRATCH

        if op in ("idiv", "irem"):
            self._emit(encode_cqo())
            self._emit(encode_idiv_r(b_r))
        else:
            self._emit(encode_xor_zero(Reg.RDX))
            self._emit(encode_div_r(b_r))

        # Tolerate `instr.result` being spilled to a StackLoc, not just a
        # RegLoc -- a real, legitimate outcome under high register
        # pressure (confirmed via a real crash: sum(xs)'s loop, once a
        # separate liveness-tracking fix correctly kept more values alive
        # across the loop's back edge, legitimately pushed one more value
        # here into a stack spill than this function previously ever
        # tolerated). `alt_scratch=False` (plain _SCRATCH/R11), NOT True:
        # _SCRATCH2/R10 is already claimed by `a_in_rax`'s own preserved-
        # value slot below (line ~472) -- reusing it here for the
        # destination write would clobber `a`'s saved value before the
        # `a_in_rax` restore reads it back. R11 is safe: `b_r` may have
        # transiently used it (line ~476), but only ever as the divisor
        # operand to `idiv`/`div`, which has already consumed it by this
        # point -- nothing later reads `b_r` again.
        dst, spill = self._dst_gp_spillable(instr.result)
        result_reg = Reg.RAX if op in ("idiv", "udiv") else Reg.RDX
        if dst != result_reg:
            self._emit(encode_mov_rr(dst, result_reg))
        if a_in_rax and dst != Reg.RAX:
            self._emit(encode_mov_rr(Reg.RAX, self._SCRATCH2))
        spill()

    # ── Shifts ────────────────────────────────────────────────────────────────

    def _shift(self, instr: Any) -> None:
        # Spillable dst (see _div's own comment on why): shl/shr/sar only
        # ever encode a register operand, never memory, so a spilled
        # result still needs a real scratch to compute into before being
        # stored out. alt_scratch=True: `instr.operands[0]`'s own `_gp`
        # load may already occupy plain `_SCRATCH`/R11 if it too is
        # spilled -- the destination must use R10 instead to avoid
        # clobbering it before the mov into dst reads it.
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
            if cnt_r != Reg.RCX:
                self._emit(encode_mov_rr(Reg.RCX, cnt_r))
            self._emit({"shl": encode_shl_cl, "shr": encode_shr_cl, "sar": encode_sar_cl}[op](dst))
        spill()

    # ── Call ──────────────────────────────────────────────────────────────────

    def _sequence_gp_moves(self, pairs: list[tuple[Reg, Reg]]) -> None:
        """Emit `mov dst, src` for each (dst, src) pair, safe against pairs
        whose dst is another pending pair's src (moving in arrival order
        would clobber a value before it's read). Same parallel-copy problem
        phi_elim.py solves for predecessor blocks."""
        pending = [(d, s) for d, s in pairs if d != s]
        while pending:
            srcs_needed = {s for _, s in pending}
            for idx, (d, s) in enumerate(pending):
                if d not in srcs_needed:
                    self._emit(encode_mov_rr(d, s))
                    del pending[idx]
                    break
            else:
                # Every remaining dst is also needed as a source: a cycle.
                # Stash one dst's about-to-be-overwritten value in the
                # scratch register, move it, then redirect any pair that
                # still needs to read it to read the stash instead.
                d0, s0 = pending[0]
                self._emit(encode_mov_rr(self._SCRATCH, d0))
                self._emit(encode_mov_rr(d0, s0))
                del pending[0]
                pending = [(d, self._SCRATCH if s == d0 else s) for d, s in pending]

    def _sequence_xmm_moves(self, pairs: list[tuple[XmmReg, XmmReg]]) -> None:
        pending = [(d, s) for d, s in pairs if d != s]
        while pending:
            srcs_needed = {s for _, s in pending}
            for idx, (d, s) in enumerate(pending):
                if d not in srcs_needed:
                    self._emit(encode_movsd_rr(d, s))
                    del pending[idx]
                    break
            else:
                d0, s0 = pending[0]
                self._emit(encode_movsd_rr(self._SCRATCH_XMM, d0))
                self._emit(encode_movsd_rr(d0, s0))
                del pending[0]
                pending = [(d, self._SCRATCH_XMM if s == d0 else s) for d, s in pending]

    #: Reserved symbols the machine implements directly. Measured against the
    #: loop they replace: 2M popcounts took 5.5s as a called loop.
    _INTRINSICS = {
        "__lllib_popcount": "popcnt",
        "__lllib_bswap64":  "bswap",
    }

    def _emit_intrinsic(self, symbol: str, instr: Any, args: list) -> bool:
        """Emit `symbol` as an instruction. False if it is not one of ours."""
        kind = self._INTRINSICS.get(symbol)
        if kind is None or len(args) != 1 or instr.result is None:
            return False
        if _is_xmm(instr.result.type.name):
            return False

        src_r, load = self._gp(args[0])
        dst, spill = self._dst_gp_spillable(instr.result, alt_scratch=True)
        self._emit(load)
        if kind == "popcnt":
            self._emit(encode_popcnt_rr(dst, src_r))
        else:                                   # bswap is in-place
            if src_r != dst:
                self._emit(encode_mov_rr(dst, src_r))
            self._emit(encode_bswap_r(dst))
        spill()
        return True

    def _call(self, instr: Any) -> None:
        target_op = instr.operands[0]
        arg_vals  = instr.operands[1:]

        # lllib intrinsics: a reserved symbol the machine can do in one
        # instruction, emitted inline instead of called. A backend that does
        # not recognize the symbol simply emits the call, which links against
        # the portable implementation -- so this is an optimization, never a
        # requirement, and the three rungs degrade cleanly.
        if not (hasattr(target_op, "name") and hasattr(target_op, "type")):
            if self._emit_intrinsic(str(target_op), instr, arg_vals):
                return

        # Detect indirect call: first operand is an IRValue (function pointer),
        # not a bare string symbol name.
        is_indirect = hasattr(target_op, "name") and hasattr(target_op, "type")

        int_args = ARG_REGS_WIN64 if self.abi == "win64" else ARG_REGS_SYSV
        xmm_args = XMM_ARG_WIN64  if self.abi == "win64" else XMM_ARG_SYSV

        # Collect (dst, src) pairs first; load any spilled operand right
        # away (loads don't touch argument registers so they're safe to
        # interleave), but DON'T move src->dst here. Two or more args can
        # currently live in registers that are each other's destinations
        # (e.g. arg0's value sits in the register arg1 needs to land in,
        # and vice versa) -- moving them one at a time in arrival order
        # clobbers a not-yet-read source. Sequence the moves like
        # phi_elim's parallel-copy handling: only move into a destination
        # once nothing later still needs to read it, breaking cycles with
        # the scratch register.
        # Win64's variadic ABI (printf/sprintf -- the only variadic externs
        # this backend calls) shares ONE positional counter across both
        # register classes (arg i -> rcx/rdx/r8/r9[i] AND xmm0-3[i]), unlike
        # ordinary Win64 calls which keep separate int/float counters. A
        # float vararg must additionally be duplicated, bit-for-bit, into
        # its positional GP register -- the callee can't tell at compile
        # time which varargs are floats, so MSVC's printf reads the GP slot
        # for any arg it doesn't already know is a float from the format
        # string's own position tracking on this ABI. Round-trips through
        # the (unused-by-the-caller-at-this-point) shadow space below RSP
        # since there's no GP<->XMM raw-bit-move encoder in this backend.
        is_win64_vararg = (
            not is_indirect and self.abi == "win64" and str(target_op) in ("printf", "sprintf")
        )
        is_sysv_vararg = (
            not is_indirect and self.abi == "sysv" and str(target_op) in ("printf", "sprintf")
        )

        gp_reg_args: list[tuple[Reg, Any, int]] = []
        xmm_reg_args: list[tuple[XmmReg, Any, str, int]] = []
        gp_dup_slots: list[tuple[Reg, int, str]] = []
        stack_args: list[tuple[Any, int]] = []
        # `_abi_setjmp` is the one call in this backend that can "return
        # twice": once normally (falling through), and once more via a
        # LATER `_abi_raise` -> `_runtime_longjmp` jumping directly back
        # to this call's own return address, arbitrarily far in the
        # future, after arbitrarily many other calls have executed inside
        # the try body in between. This ephemeral win64_saved_regs save
        # area (an RSP-relative scratch region below the function's own
        # permanent frame, "returned" to the free pool the instant this
        # call's normal epilogue restores from it) is only ever safe
        # because ordinary call/return is strictly nested -- but setjmp's
        # SECOND "return" via longjmp violates that: any call made from
        # inside the try body after the first return (e.g. the `_abi_
        # raise` call itself, or anything the body/handler does) is free
        # to reuse this exact same stack memory for its OWN temp-save
        # area, since nothing marks it "still needed" once the first,
        # normal return already consumed it once. Confirmed via a
        # hardware watchpoint: the exact address holding this call's
        # saved RDI got overwritten by `_abi_raise`'s own argument-
        # marshaling temp area moments before the longjmp fired, so the
        # "restored" RDI after longjmp was actually raise's leftover
        # scratch data (0 in the repro) -- corrupting whatever value
        # (e.g. a for-loop's own loop-variable address) was live across
        # the try in a register _call's convention assumes it's
        # protecting. `_runtime_setjmp`/`_runtime_longjmp` already save
        # every register that matters (rbx/rbp/r12-r15/rsi/rdi/rsp) into
        # the DURABLE jmp_buf itself (see abi_shims.asm), so this outer
        # ephemeral save is pure redundancy for this one call site --
        # and actively unsafe. Skip it here; every other call site keeps
        # the normal protection.
        skip_saved_regs = not is_indirect and str(target_op) == "_abi_setjmp"
        win64_saved_regs: list[Reg] = (
            [Reg.RBX, Reg.RSI, Reg.RDI, Reg.R12, Reg.R13, Reg.R14, Reg.R15]
            if self.abi == "win64" and not skip_saved_regs
            else []
        )
        int_i = xmm_i = 0
        temp_slots = 0
        for arg_i, av in enumerate(arg_vals):
            typ = av.type.name if hasattr(av, "type") else "i64"
            if self.abi == "win64":
                if _is_float(typ):
                    if arg_i < len(xmm_args):
                        dst_x = xmm_args[arg_i]
                        xmm_reg_args.append((dst_x, av, typ, temp_slots))
                        temp_slots += 1
                        if is_win64_vararg and arg_i < len(int_args):
                            gp_dup_slots.append((int_args[arg_i], temp_slots - 1, typ))
                    else:
                        stack_args.append((av, len(stack_args)))
                else:
                    if arg_i < len(int_args):
                        dst_r = int_args[arg_i]
                        gp_reg_args.append((dst_r, av, temp_slots))
                        temp_slots += 1
                    else:
                        stack_args.append((av, len(stack_args)))
            else:
                if _is_float(typ):
                    if xmm_i < len(xmm_args):
                        dst_x = xmm_args[xmm_i]
                        xmm_i += 1
                        xmm_reg_args.append((dst_x, av, typ, temp_slots))
                        temp_slots += 1
                    else:
                        stack_args.append((av, len(stack_args)))
                else:
                    if int_i < len(int_args):
                        dst_r = int_args[int_i]
                        int_i += 1
                        gp_reg_args.append((dst_r, av, temp_slots))
                        temp_slots += 1
                    else:
                        stack_args.append((av, len(stack_args)))

        target_temp_slot: int | None = None
        if is_indirect:
            target_temp_slot = temp_slots
            temp_slots += 1

        shadow_bytes = 32 if self.abi == "win64" else 0
        stack_arg_bytes = 8 * len(stack_args)
        temp_bytes = 8 * temp_slots
        save_base = shadow_bytes + stack_arg_bytes + temp_bytes
        save_bytes = 8 * len(win64_saved_regs)
        stack_bytes = shadow_bytes + stack_arg_bytes + temp_bytes + save_bytes
        if stack_bytes & 0xF:
            stack_bytes += 8
        if stack_bytes:
            self._emit(encode_sub_ri(Reg.RSP, stack_bytes))

        stack_base = shadow_bytes
        temp_base = shadow_bytes + stack_arg_bytes

        for i, reg in enumerate(win64_saved_regs):
            self._emit(encode_mov_mr(Mem(Reg.RSP, save_base + 8 * i), reg))

        for av, stack_i in stack_args:
            off = stack_base + stack_i * 8
            typ = av.type.name if hasattr(av, "type") else "i64"
            if _is_float(typ):
                get = self._xmm_f32 if typ == "f32" else self._xmm
                src_x, ld = get(av)
                self._emit(ld)
                if typ == "f32":
                    self._emit(encode_movss_mr(Mem(Reg.RSP, off), src_x))
                else:
                    self._emit(encode_movsd_mr(Mem(Reg.RSP, off), src_x))
            else:
                src_r, ld = self._gp(av)
                self._emit(ld)
                self._emit(encode_mov_mr(Mem(Reg.RSP, off), src_r))

        # Materialize every register-passed argument into its own temporary
        # call-frame slot first, then load the ABI registers from those slots.
        # This avoids every scratch-register alias hazard at once: multiple
        # spilled args no longer fight over R11/XMM15, and register args whose
        # current homes overlap their eventual ABI destinations can't clobber
        # each other before they've all been captured.
        for dst_r, av, temp_i in gp_reg_args:
            src_r, ld = self._gp(av)
            self._emit(ld)
            self._emit(encode_mov_mr(Mem(Reg.RSP, temp_base + 8 * temp_i), src_r))
        for dst_x, av, typ, temp_i in xmm_reg_args:
            get = self._xmm_f32 if typ == "f32" else self._xmm
            src_x, ld = get(av)
            self._emit(ld)
            if typ == "f32":
                self._emit(encode_movss_mr(Mem(Reg.RSP, temp_base + 8 * temp_i), src_x))
            else:
                self._emit(encode_movsd_mr(Mem(Reg.RSP, temp_base + 8 * temp_i), src_x))
        if target_temp_slot is not None:
            ptr_r, ld = self._gp(target_op)
            self._emit(ld)
            self._emit(encode_mov_mr(Mem(Reg.RSP, temp_base + 8 * target_temp_slot), ptr_r))

        for dst_r, _av, temp_i in gp_reg_args:
            self._emit(encode_mov_rm(dst_r, Mem(Reg.RSP, temp_base + 8 * temp_i)))
        for dst_x, _av, typ, temp_i in xmm_reg_args:
            if typ == "f32":
                self._emit(encode_movss_rm(dst_x, Mem(Reg.RSP, temp_base + 8 * temp_i)))
            else:
                self._emit(encode_movsd_rm(dst_x, Mem(Reg.RSP, temp_base + 8 * temp_i)))

        for dst_r, temp_i, typ in gp_dup_slots:
            if typ == "f32":
                self._emit(encode_mov_rm32(dst_r, Mem(Reg.RSP, temp_base + 8 * temp_i)))
            else:
                self._emit(encode_mov_rm(dst_r, Mem(Reg.RSP, temp_base + 8 * temp_i)))

        if is_sysv_vararg:
            # SysV AMD64 passes the number of live vector arguments in AL for
            # variadic calls such as printf/sprintf. Do this only after every
            # argument has been captured and loaded: register allocation may
            # place an argument (including the format pointer) in RAX, and an
            # earlier write to EAX would silently replace that value with null.
            self._emit(encode_mov_ri(Reg.RAX, xmm_i))

        if is_indirect:
            self._emit(encode_mov_rm(self._SCRATCH, Mem(Reg.RSP, temp_base + 8 * target_temp_slot)))
            self._emit(encode_call_r(self._SCRATCH))
        else:
            target   = str(target_op)
            call_off = self._pos()
            self._emit(encode_call_rel32(0))
            self.fixups.append((call_off + 1, target, R_X86_64_PLT32))

        for i, reg in enumerate(win64_saved_regs):
            self._emit(encode_mov_rm(reg, Mem(Reg.RSP, save_base + 8 * i)))

        if stack_bytes:
            self._emit(encode_add_ri(Reg.RSP, stack_bytes))

        if instr.result is not None:
            rtyp = instr.result.type.name
            if _is_float(rtyp):
                loc = self._loc(instr.result)
                if isinstance(loc, XmmLoc):
                    if loc.reg != XmmReg.XMM0:
                        self._emit(encode_movsd_rr(loc.reg, XmmReg.XMM0))
                else:
                    if rtyp == "f32":
                        self._emit(encode_movss_mr(Mem(Reg.RBP, loc.offset), XmmReg.XMM0))
                    else:
                        self._emit(encode_movsd_mr(Mem(Reg.RBP, loc.offset), XmmReg.XMM0))
            elif _is_xmm(rtyp):
                loc = self._loc(instr.result)
                if isinstance(loc, XmmLoc):
                    if loc.reg != XmmReg.XMM0:
                        self._emit(encode_movdqa_rr(loc.reg, XmmReg.XMM0))
                else:
                    self._emit(encode_movdqu_mr(Mem(Reg.RBP, loc.offset), XmmReg.XMM0))
            else:
                loc = self._loc(instr.result)
                if isinstance(loc, RegLoc):
                    if loc.reg != Reg.RAX:
                        self._emit(encode_mov_rr(loc.reg, Reg.RAX))
                else:
                    self._emit(encode_mov_mr(Mem(Reg.RBP, loc.offset), Reg.RAX))

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

    # SIMD packed ops: op_name → (packed_float32_fn, packed_float64_fn, packed_int_fn)
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
            self._binop_gp(r, ops[0], ops[1], self._IOPS[op])
            return

        if op in ("idiv", "irem", "udiv", "urem"):
            self._div(instr); return

        if op == "ineg":
            # Spillable dst (see _div's own comment on why this class of
            # site needs it): a result forced to a stack slot under high
            # register pressure is legitimate, not a bug. alt_scratch=True
            # on the destination: `ops[0]`'s own `_gp` load may already
            # occupy `_SCRATCH`/R11 if it too is spilled -- the same
            # operand-vs-destination collision `_dst_gp_spillable`'s own
            # docstring describes, so the destination must use R10 instead.
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
            self._emit(ld)
            if src_r != dst: self._emit(encode_mov_rr(dst, src_r))
            self._emit(encode_neg(dst)); spill(); return

        if op == "inot":
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
            self._emit(ld)
            if src_r != dst: self._emit(encode_mov_rr(dst, src_r))
            self._emit(encode_not(dst)); spill(); return

        # ── shifts ────────────────────────────────────────────────────────────
        if op in ("shl", "shr", "sar"):
            self._shift(instr); return

        # ── integer compare ───────────────────────────────────────────────────
        if op in self._ICMP:
            self._cmp_set(r, ops[0], ops[1], self._ICMP[op]); return

        # ── float binary ──────────────────────────────────────────────────────
        if op in self._FOPS_F64:
            f32 = ops[0].type.name == "f32"
            tbl = self._FOPS_F32 if f32 else self._FOPS_F64
            self._binop_xmm(r, ops[0], ops[1], tbl[op], f32=f32); return

        if op == "fneg":
            # dst uses the ALT xmm scratch: this op stakes out _SCRATCH_XMM
            # (XMM15) to build the sign-bit mask below, so a spilled dst must
            # not alias it. The src->dst copy is also emitted BEFORE the mask
            # is materialized, so that even a `src` loaded into _SCRATCH_XMM
            # (spilled operand) is safely captured in dst before the mask
            # overwrites _SCRATCH_XMM.
            dst, spill = self._dst_xmm_spillable(r, alt_scratch=True)
            f32 = ops[0].type.name == "f32"
            src_x, ld = (self._xmm_f32 if f32 else self._xmm)(ops[0])
            self._emit(ld)
            mov = encode_movss_rr if f32 else encode_movsd_rr
            if src_x != dst: self._emit(mov(dst, src_x))
            sign_bits = 0x80000000 if f32 else (1 << 63)
            self._emit(encode_mov_ri(self._SCRATCH, sign_bits))
            self._emit(_gp_to_xmm(self._SCRATCH_XMM, self._SCRATCH))
            xor_op = 0x57
            pfx    = b"" if f32 else bytes([0x66])
            rex_b  = bytes([0x40 | (_hi(dst) << 2) | _hi(self._SCRATCH_XMM)]) \
                     if (_hi(dst) or _hi(self._SCRATCH_XMM)) else b""
            self._emit(pfx + rex_b + bytes([0x0F, xor_op,
                        0xC0 | ((int(dst) & 7) << 3) | (int(self._SCRATCH_XMM) & 7)]))
            spill()
            return

        # ── float compare ─────────────────────────────────────────────────────
        if op in self._FCMP:
            self._fcmp_set(r, ops[0], ops[1], self._FCMP[op],
                           f32=ops[0].type.name == "f32"); return

        # ── SIMD packed ops ───────────────────────────────────────────────────
        if op in self._SIMD_OPS:
            ps_fn, pd_fn, pi_fn = self._SIMD_OPS[op]
            # Determine lane type from an operand annotation or op suffix
            # Convention: ops[0].type.name is "v128" for generic packed int,
            # or a float type for float-flavored SIMD
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
            if r is not None and _is_float(r.type.name):
                bits = struct.pack("<f" if r.type.name == "f32" else "<d", float(v))
                imm  = int.from_bytes(bits, "little")
                loc = self._loc(r)
                self._emit(encode_mov_ri(self._SCRATCH, imm))
                if isinstance(loc, XmmLoc):
                    self._emit(_gp_to_xmm(loc.reg, self._SCRATCH))
                else:
                    self._emit(_gp_to_xmm(self._SCRATCH_XMM, self._SCRATCH))
                    self._emit(encode_movsd_mr(Mem(Reg.RBP, loc.offset), self._SCRATCH_XMM))
            elif r is not None and _is_xmm(r.type.name):
                # Zero-initialize a v128 register
                loc = self._loc(r)
                self._emit(encode_pxor(self._SCRATCH_XMM, self._SCRATCH_XMM))
                if isinstance(loc, XmmLoc):
                    if loc.reg != self._SCRATCH_XMM:
                        self._emit(encode_movdqa_rr(loc.reg, self._SCRATCH_XMM))
                else:
                    self._emit(encode_movdqu_mr(Mem(Reg.RBP, loc.offset), self._SCRATCH_XMM))
            else:
                loc = self._loc(r)
                self._emit(encode_mov_ri(self._SCRATCH, int(v)))
                if isinstance(loc, RegLoc):
                    if loc.reg != self._SCRATCH:
                        self._emit(encode_mov_rr(loc.reg, self._SCRATCH))
                else:
                    self._emit(encode_mov_mr(Mem(Reg.RBP, loc.offset), self._SCRATCH))
            return

        # ── memory ────────────────────────────────────────────────────────────
        if op == "load":
            ptr_r, ld = self._gp(ops[0])
            self._emit(ld)
            tname = r.type.name
            if tname == "f64":
                loc = self._loc(r)
                if isinstance(loc, XmmLoc):
                    self._emit(encode_movsd_rm(loc.reg, Mem(ptr_r)))
                else:
                    self._emit(encode_movsd_rm(self._SCRATCH_XMM, Mem(ptr_r)))
                    self._emit(encode_movsd_mr(Mem(Reg.RBP, loc.offset), self._SCRATCH_XMM))
            elif tname == "f32":
                loc = self._loc(r)
                if isinstance(loc, XmmLoc):
                    self._emit(encode_movss_rm(loc.reg, Mem(ptr_r)))
                else:
                    self._emit(encode_movss_rm(self._SCRATCH_XMM, Mem(ptr_r)))
                    self._emit(encode_movss_mr(Mem(Reg.RBP, loc.offset), self._SCRATCH_XMM))
            elif tname == "v128":
                loc = self._loc(r)
                if isinstance(loc, XmmLoc):
                    self._emit(encode_movdqu_rm(loc.reg, Mem(ptr_r)))
                else:
                    self._emit(encode_movdqu_rm(self._SCRATCH_XMM, Mem(ptr_r)))
                    self._emit(encode_movdqu_mr(Mem(Reg.RBP, loc.offset), self._SCRATCH_XMM))
            elif tname in ("i8", "u8"):
                loc = self._loc(r)
                if isinstance(loc, RegLoc):
                    self._emit(encode_movzx_rm8(loc.reg, Mem(ptr_r)))
                else:
                    self._emit(encode_movzx_rm8(self._SCRATCH, Mem(ptr_r)))
                    self._emit(encode_mov_mr(Mem(Reg.RBP, loc.offset), self._SCRATCH))
            elif tname in ("i16", "u16"):
                loc = self._loc(r)
                if isinstance(loc, RegLoc):
                    self._emit(encode_movzx_rm16(loc.reg, Mem(ptr_r)))
                else:
                    self._emit(encode_movzx_rm16(self._SCRATCH, Mem(ptr_r)))
                    self._emit(encode_mov_mr(Mem(Reg.RBP, loc.offset), self._SCRATCH))
            elif tname in ("i32", "u32"):
                loc = self._loc(r)
                if isinstance(loc, RegLoc):
                    self._emit(encode_mov_rm32(loc.reg, Mem(ptr_r)))
                else:
                    self._emit(encode_mov_rm32(self._SCRATCH, Mem(ptr_r)))
                    self._emit(encode_mov_mr(Mem(Reg.RBP, loc.offset), self._SCRATCH))
            else:  # i64, u64, ptr — full 64-bit
                loc = self._loc(r)
                if isinstance(loc, RegLoc):
                    self._emit(encode_mov_rm(loc.reg, Mem(ptr_r)))
                else:
                    self._emit(encode_mov_rm(self._SCRATCH, Mem(ptr_r)))
                    self._emit(encode_mov_mr(Mem(Reg.RBP, loc.offset), self._SCRATCH))
            return

        if op == "store":
            val = ops[0]
            ptr = ops[1]
            ptr_r, p_ld = self._gp(ptr)
            self._emit(p_ld)
            tname = val.type.name if hasattr(val, "type") else "i64"
            if tname == "f64":
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
            elif tname in ("i32", "u32"):
                val_r, v_ld = self._gp(val)
                store_ptr_r = ptr_r
                if ptr_r == self._SCRATCH and v_ld:
                    self._emit(encode_mov_rr(self._SCRATCH2, ptr_r))
                    store_ptr_r = self._SCRATCH2
                self._emit(v_ld)
                self._emit(encode_mov_mr32(Mem(store_ptr_r), val_r))
            else:  # i64, u64, ptr
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
            # No instruction needed here: alloca_slots already records this
            # name's fixed rbp-relative address (set by regalloc.py), and
            # every later read of it goes through `_gp`'s alloca special
            # case (a fresh lea each time) instead of a cached register --
            # see that method's comment for why.
            return

        # ── type conversions ──────────────────────────────────────────────────
        if op == "sext":
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
            self._emit(ld)
            bits = int(ops[0].type.name[1:])
            self._emit(encode_movsx(dst, src_r, bits))
            spill()
            return

        if op == "zext":
            # Spillable dst -- see _div's/sext's own comment on why.
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
            self._emit(ld)
            bits = int(ops[0].type.name[1:])
            if bits == 32:
                self._emit(encode_mov_rr(dst, src_r))
            else:
                self._emit(encode_movzx(dst, src_r, bits))
            spill()
            return

        if op == "trunc":
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
            self._emit(ld)
            if src_r != dst: self._emit(encode_mov_rr(dst, src_r))
            spill()
            return

        if op == "sitofp":
            src_r, ld = self._gp(ops[0])
            self._emit(ld)
            # Spillable dst (see regalloc's crosses-call float spill). The
            # operand is a GP register, disjoint from the XMM scratch, so no
            # alt-scratch aliasing to guard against.
            dst, spill = self._dst_xmm_spillable(r)
            if r.type.name == "f32":
                self._emit(encode_cvtsi2ss(dst, src_r))
            else:
                self._emit(encode_cvtsi2sd(dst, src_r))
            spill()
            return

        if op == "fptosi":
            # Spillable dst -- see _div's own comment on why. No
            # alt_scratch collision risk: the operand load is into an XMM
            # register (src_x), entirely disjoint from the GP scratch
            # _dst_gp_spillable would use.
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
            dst, spill = self._dst_xmm_spillable(r)
            self._emit(ld); self._emit(_cvtss2sd(dst, src_x))
            spill()
            return

        if op == "fptrunc":
            src_x, ld = self._xmm(ops[0])
            dst, spill = self._dst_xmm_spillable(r)
            self._emit(ld); self._emit(_cvtsd2ss(dst, src_x))
            spill()
            return

        if op == "bitcast_i2f":
            # Raw 64-bit reinterpret, not a numeric conversion (unlike
            # sitofp): the exact bits of a GP-typed IR value become an f64.
            # Needed wherever a float value has been carried through an
            # int-only storage slot (dict/list cell, generic "call" arg
            # marshaling) and must be read back as a real double.
            src_r, ld = self._gp(ops[0])
            dst, spill = self._dst_xmm_spillable(r)
            self._emit(ld); self._emit(encode_movq_xmm_gp(dst, src_r))
            spill()
            return

        if op == "bitcast_f2i":
            # Reverse of bitcast_i2f -- an f64's raw bits into a GP
            # register, for storing a float into one of those int-only
            # slots. Spillable dst -- see _div's own comment on why; no
            # alt_scratch collision risk, src_x is an XMM register.
            src_x, ld = self._xmm(ops[0])
            dst, spill = self._dst_gp_spillable(r)
            self._emit(ld); self._emit(encode_movq_gp_xmm(dst, src_x))
            spill()
            return

        # ── control flow ──────────────────────────────────────────────────────
        if op == "ret":
            if ops:
                val = ops[0]
                if _is_xmm(val.type.name):
                    get = self._xmm_f32 if val.type.name == "f32" else self._xmm
                    src_x, ld = get(val)
                    self._emit(ld)
                    if src_x != XmmReg.XMM0:
                        self._emit(encode_movsd_rr(XmmReg.XMM0, src_x))
                else:
                    src_r, ld = self._gp(val)
                    self._emit(ld)
                    if src_r != Reg.RAX:
                        self._emit(encode_mov_rr(Reg.RAX, src_r))
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
            # Explicit register copy (from phi elimination). Spillable
            # dst on the GP side -- see _div's own comment on why;
            # alt_scratch=True since ops[0]'s own _gp load may already
            # occupy plain _SCRATCH/R11 if it too is spilled.
            if r is None:
                return
            if _is_xmm(r.type.name):
                loc = self._loc(r)
                src_x, ld = self._xmm(ops[0])
                self._emit(ld)
                if isinstance(loc, XmmLoc):
                    if src_x != loc.reg: self._emit(encode_movdqa_rr(loc.reg, src_x))
                else:
                    # A scalar f32/f64 phi copy homed on the stack because it
                    # lives across a call (regalloc's crosses-call spill). Only
                    # the low 64 bits matter for a scalar, so store via movsd
                    # into the 8-byte slot; v128 phi results never reach this
                    # branch (they keep a register, so `loc` is an XmmLoc above).
                    assert isinstance(loc, StackLoc), f"XMM mov dst expected for {r.name}"
                    self._emit(encode_movsd_mr(Mem(Reg.RBP, loc.offset), src_x))
            else:
                src_r, ld = self._gp(ops[0])
                dst, spill = self._dst_gp_spillable(r, alt_scratch=True)
                self._emit(ld)
                if src_r != dst: self._emit(encode_mov_rr(dst, src_r))
                spill()
            return

        if op in ("global_addr", "str_global"):
            loc     = self._loc(r)
            dst     = loc.reg if isinstance(loc, RegLoc) else self._SCRATCH
            lea_off = self._pos()
            self._emit(encode_lea_rip(dst, 0))
            # disp32 is at byte 3 (REX + 0x8D + ModRM); R_X86_64_PC32 for data refs
            self.fixups.append((lea_off + 3, str(ops[0]), R_X86_64_PC32))
            if isinstance(loc, StackLoc):
                self._emit(encode_mov_mr(Mem(Reg.RBP, loc.offset), dst))
            return

        if op == "tls_addr":
            # MOV dst, QWORD PTR FS:[tpoff32]  — local-exec TLS model (Linux x86-64)
            # Spillable dst -- see _div's own comment on why.
            dst, spill = self._dst_gp_spillable(r)
            tls_off = self._pos()
            self._emit(encode_mov_tls_rm(dst, 0))
            # disp32 is at byte 5 (0x64 + REX + 0x8B + ModRM + SIB); R_X86_64_TPOFF32
            self.fixups.append((tls_off + 5, str(ops[0]), R_X86_64_TPOFF32))
            spill()
            return

        if op == "stack_probe":
            # Explicit stack-page probe (for large dynamic alloca).
            # Windows: call __chkstk with RAX = bytes to probe.
            # Linux: touch pages manually (simplified single-page touch).
            size = int(ops[0]) if ops else 4096
            if self.abi == "win64":
                self._emit(encode_mov_ri(Reg.RAX, size))
                call_off = self._pos()
                self._emit(encode_call_rel32(0))
                self.fixups.append((call_off + 1, "__chkstk", R_X86_64_PLT32))
            else:
                # Touch every page between RSP and RSP-size
                n_pages = max(1, (size + 4095) // 4096)
                for i in range(n_pages):
                    touch_off = (i + 1) * 4096
                    self._emit(encode_mov_ri(self._SCRATCH, 0))
                    # MOV [RSP - touch_off], scratch (byte store is sufficient)
                    self._emit(encode_mov_mr(Mem(Reg.RSP, -touch_off), self._SCRATCH))
            return

        if op == "debug_loc":
            # Record source location at current code position for .debug_line.
            fname = str(ops[0]) if ops else "<unknown>"
            line  = int(ops[1]) if len(ops) > 1 else 0
            self._debug_locs.append((self._pos(), fname, line))
            return

        self._emit(encode_nop())  # unknown op — keep offsets consistent

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


def compile_func(func: Any, alloc: AllocResult, abi: str = "sysv") -> FuncCode:
    return FuncCodegen(func, alloc, abi).compile()
