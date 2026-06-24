"""
x86-64 code generator.

Translates one IRFunc to a flat byte stream using the register allocation
produced by regalloc.py.  Internal branch targets are fixed up in a second
pass; unresolved external calls are returned as relocation records so the
object-file emitter (elf.py / coff.py) can build a proper relocation table.

NOTE: Reg.R11 and XmmReg.XMM15 are reserved as scratch registers and must
      not be present in the regalloc pool.  Remove them from _GP_POOL and
      _XMM_POOL in regalloc.py if they ever appear there.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

from .encoder import (
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
    """MOVQ xmm, r64 — move 64-bit GP value into XMM (bit-for-bit)."""
    rex = bytes([0x40 | (_hi(dst) << 2) | _hi(src)]) if (_hi(dst) or _hi(src)) else b""
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
    _SCRATCH_XMM = XmmReg.XMM15  # reserved XMM scratch

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

    def _gp(self, val: Any) -> tuple[Reg, bytes]:
        """Get a GP register holding val. Emits a load from stack if spilled."""
        loc = self._loc(val)
        if isinstance(loc, RegLoc):
            return loc.reg, b""
        return self._SCRATCH, encode_mov_rm(self._SCRATCH, Mem(Reg.RBP, loc.offset))

    def _xmm(self, val: Any) -> tuple[XmmReg, bytes]:
        """Get an XMM register holding val (f64 / v128). Emits load if spilled."""
        loc = self._loc(val)
        if isinstance(loc, XmmLoc):
            return loc.reg, b""
        return self._SCRATCH_XMM, encode_movsd_rm(self._SCRATCH_XMM, Mem(Reg.RBP, loc.offset))

    def _xmm_f32(self, val: Any) -> tuple[XmmReg, bytes]:
        """Get an XMM register holding val (f32). Emits load if spilled."""
        loc = self._loc(val)
        if isinstance(loc, XmmLoc):
            return loc.reg, b""
        return self._SCRATCH_XMM, encode_movss_rm(self._SCRATCH_XMM, Mem(Reg.RBP, loc.offset))

    def _dst_gp(self, result: Any) -> Reg:
        loc = self._loc(result)
        assert isinstance(loc, RegLoc), f"GP result expected for {result.name}"
        return loc.reg

    def _dst_xmm(self, result: Any) -> XmmReg:
        loc = self._loc(result)
        assert isinstance(loc, XmmLoc), f"XMM result expected for {result.name}"
        return loc.reg

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

    def _epilogue(self) -> None:
        if self.alloc.stack_bytes:
            self._emit(encode_add_ri(Reg.RSP, self.alloc.stack_bytes))
        self._emit(encode_pop(Reg.RBP))
        for r in reversed(self.alloc.callee_saved):
            self._emit(encode_pop(r))
        self._emit(encode_ret())

    # ── Pattern helpers ───────────────────────────────────────────────────────

    def _binop_gp(self, result: Any, a: Any, b: Any, rr_fn) -> None:
        """dst = rr_fn(a, b) — loads a into dst first, then applies op with b."""
        dst       = self._dst_gp(result)
        a_r, a_ld = self._gp(a)
        b_r, b_ld = self._gp(b)
        self._emit(a_ld + b_ld)
        if a_r != dst:
            self._emit(encode_mov_rr(dst, a_r))
        self._emit(rr_fn(dst, b_r))

    def _binop_xmm(self, result: Any, a: Any, b: Any, rr_fn, f32: bool = False) -> None:
        get = self._xmm_f32 if f32 else self._xmm
        mov = encode_movss_rr if f32 else encode_movsd_rr
        dst       = self._dst_xmm(result)
        a_x, a_ld = get(a)
        b_x, b_ld = get(b)
        self._emit(a_ld + b_ld)
        if a_x != dst:
            self._emit(mov(dst, a_x))
        self._emit(rr_fn(dst, b_x))

    def _binop_simd(self, result: Any, a: Any, b: Any, rr_fn) -> None:
        """Packed XMM binary op (v128 / SIMD)."""
        dst       = self._dst_xmm(result)
        a_x, a_ld = self._xmm(a)
        b_x, b_ld = self._xmm(b)
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
        dst       = self._dst_gp(result)
        a_r, a_ld = self._gp(a)
        b_r, b_ld = self._gp(b)
        self._emit(a_ld + b_ld)
        self._emit(encode_cmp_rr(a_r, b_r))
        self._emit(encode_setcc(cc, dst))
        self._emit(encode_movzx(dst, dst, 8))

    def _fcmp_set(self, result: Any, a: Any, b: Any, cc: CC, f32: bool) -> None:
        dst = self._dst_gp(result)
        if f32:
            a_x, a_ld = self._xmm_f32(a)
            b_x, b_ld = self._xmm_f32(b)
            self._emit(a_ld + b_ld)
            self._emit(encode_ucomiss(a_x, b_x))
        else:
            a_x, a_ld = self._xmm(a)
            b_x, b_ld = self._xmm(b)
            self._emit(a_ld + b_ld)
            self._emit(encode_ucomisd(a_x, b_x))
        self._emit(encode_setcc(cc, dst))
        self._emit(encode_movzx(dst, dst, 8))

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
        b_r, b_ld = self._gp(b)
        self._emit(a_ld + b_ld)

        if a_r != Reg.RAX:
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

        dst        = self._dst_gp(instr.result)
        result_reg = Reg.RAX if op in ("idiv", "udiv") else Reg.RDX
        if dst != result_reg:
            self._emit(encode_mov_rr(dst, result_reg))

    # ── Shifts ────────────────────────────────────────────────────────────────

    def _shift(self, instr: Any) -> None:
        op = instr.op
        val_r, val_ld = self._gp(instr.operands[0])
        dst = self._dst_gp(instr.result)
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

    def _call(self, instr: Any) -> None:
        target_op = instr.operands[0]
        arg_vals  = instr.operands[1:]

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
        gp_pairs: list[tuple[Reg, Reg]] = []
        xmm_pairs: list[tuple[XmmReg, XmmReg]] = []
        int_i = xmm_i = 0
        for av in arg_vals:
            typ = av.type.name if hasattr(av, "type") else "i64"
            if _is_float(typ):
                if xmm_i < len(xmm_args):
                    dst_x = xmm_args[xmm_i]; xmm_i += 1
                    get = self._xmm_f32 if typ == "f32" else self._xmm
                    src_x, ld = get(av)
                    self._emit(ld)
                    xmm_pairs.append((dst_x, src_x))
            else:
                if int_i < len(int_args):
                    dst_r = int_args[int_i]; int_i += 1
                    src_r, ld = self._gp(av)
                    self._emit(ld)
                    gp_pairs.append((dst_r, src_r))
        self._sequence_gp_moves(gp_pairs)
        self._sequence_xmm_moves(xmm_pairs)

        if is_indirect:
            ptr_r, ld = self._gp(target_op)
            self._emit(ld)
            # Use scratch so we don't clobber an argument register
            if ptr_r != self._SCRATCH:
                self._emit(encode_mov_rr(self._SCRATCH, ptr_r))
            self._emit(encode_call_r(self._SCRATCH))
        else:
            target   = str(target_op)
            call_off = self._pos()
            self._emit(encode_call_rel32(0))
            self.fixups.append((call_off + 1, target, R_X86_64_PLT32))

        if instr.result is not None:
            rtyp = instr.result.type.name
            if _is_float(rtyp):
                dst_x = self._dst_xmm(instr.result)
                if dst_x != XmmReg.XMM0:
                    self._emit(encode_movsd_rr(dst_x, XmmReg.XMM0))
            else:
                dst = self._dst_gp(instr.result)
                if dst != Reg.RAX:
                    self._emit(encode_mov_rr(dst, Reg.RAX))

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
            dst = self._dst_gp(r); src_r, ld = self._gp(ops[0])
            self._emit(ld)
            if src_r != dst: self._emit(encode_mov_rr(dst, src_r))
            self._emit(encode_neg(dst)); return

        if op == "inot":
            dst = self._dst_gp(r); src_r, ld = self._gp(ops[0])
            self._emit(ld)
            if src_r != dst: self._emit(encode_mov_rr(dst, src_r))
            self._emit(encode_not(dst)); return

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
            dst = self._dst_xmm(r)
            f32 = ops[0].type.name == "f32"
            src_x, ld = (self._xmm_f32 if f32 else self._xmm)(ops[0])
            self._emit(ld)
            sign_bits = 0x80000000 if f32 else (1 << 63)
            self._emit(encode_mov_ri(self._SCRATCH, sign_bits))
            self._emit(_gp_to_xmm(self._SCRATCH_XMM, self._SCRATCH))
            mov = encode_movss_rr if f32 else encode_movsd_rr
            if src_x != dst: self._emit(mov(dst, src_x))
            xor_op = 0x57
            pfx    = b"" if f32 else bytes([0x66])
            rex_b  = bytes([0x40 | (_hi(dst) << 2) | _hi(self._SCRATCH_XMM)]) \
                     if (_hi(dst) or _hi(self._SCRATCH_XMM)) else b""
            self._emit(pfx + rex_b + bytes([0x0F, xor_op,
                        0xC0 | ((int(dst) & 7) << 3) | (int(self._SCRATCH_XMM) & 7)]))
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
            b_x, b_ld = self._xmm(ops[1])
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
                self._emit(encode_mov_ri(self._SCRATCH, imm))
                self._emit(_gp_to_xmm(self._dst_xmm(r), self._SCRATCH))
            elif r is not None and _is_xmm(r.type.name):
                # Zero-initialize a v128 register
                dst = self._dst_xmm(r)
                self._emit(encode_pxor(dst, dst))
            else:
                self._emit(encode_mov_ri(self._dst_gp(r), int(v)))
            return

        # ── memory ────────────────────────────────────────────────────────────
        if op == "load":
            ptr_r, ld = self._gp(ops[0])
            self._emit(ld)
            tname = r.type.name
            if tname == "f64":
                self._emit(encode_movsd_rm(self._dst_xmm(r), Mem(ptr_r)))
            elif tname == "f32":
                self._emit(encode_movss_rm(self._dst_xmm(r), Mem(ptr_r)))
            elif tname == "v128":
                self._emit(encode_movdqu_rm(self._dst_xmm(r), Mem(ptr_r)))
            elif tname in ("i8", "u8"):
                self._emit(encode_movzx_rm8(self._dst_gp(r), Mem(ptr_r)))
            elif tname in ("i16", "u16"):
                self._emit(encode_movzx_rm16(self._dst_gp(r), Mem(ptr_r)))
            elif tname in ("i32", "u32"):
                self._emit(encode_mov_rm32(self._dst_gp(r), Mem(ptr_r)))
            else:  # i64, u64, ptr — full 64-bit
                self._emit(encode_mov_rm(self._dst_gp(r), Mem(ptr_r)))
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
                self._emit(v_ld)
                self._emit(encode_mov_mr8(Mem(ptr_r), val_r))
            elif tname in ("i32", "u32"):
                val_r, v_ld = self._gp(val)
                self._emit(v_ld)
                self._emit(encode_mov_mr32(Mem(ptr_r), val_r))
            else:  # i64, u64, ptr
                val_r, v_ld = self._gp(val)
                self._emit(v_ld)
                self._emit(encode_mov_mr(Mem(ptr_r), val_r))
            return

        if op == "gep":
            ptr_r, p_ld = self._gp(ops[0])
            dst = self._dst_gp(r)
            self._emit(p_ld)
            if isinstance(ops[1], int):
                self._emit(encode_lea(dst, Mem(ptr_r, ops[1])))
            else:
                idx_r, i_ld = self._gp(ops[1])
                self._emit(i_ld)
                if ptr_r != dst: self._emit(encode_mov_rr(dst, ptr_r))
                self._emit(encode_add_rr(dst, idx_r))
            return

        if op == "alloca":
            slot = self.alloc.alloca_slots[r.name]
            self._emit(encode_lea(self._dst_gp(r), Mem(Reg.RBP, slot)))
            return

        # ── type conversions ──────────────────────────────────────────────────
        if op == "sext":
            src_r, ld = self._gp(ops[0])
            dst = self._dst_gp(r)
            self._emit(ld)
            bits = int(ops[0].type.name[1:])
            self._emit(encode_movsx(dst, src_r, bits))
            return

        if op == "zext":
            src_r, ld = self._gp(ops[0])
            dst = self._dst_gp(r)
            self._emit(ld)
            bits = int(ops[0].type.name[1:])
            if bits == 32:
                self._emit(encode_mov_rr(dst, src_r))
            else:
                self._emit(encode_movzx(dst, src_r, bits))
            return

        if op == "trunc":
            src_r, ld = self._gp(ops[0])
            dst = self._dst_gp(r)
            self._emit(ld)
            if src_r != dst: self._emit(encode_mov_rr(dst, src_r))
            return

        if op == "sitofp":
            src_r, ld = self._gp(ops[0])
            self._emit(ld)
            if r.type.name == "f32":
                self._emit(encode_cvtsi2ss(self._dst_xmm(r), src_r))
            else:
                self._emit(encode_cvtsi2sd(self._dst_xmm(r), src_r))
            return

        if op == "fptosi":
            dst = self._dst_gp(r)
            if ops[0].type.name == "f32":
                src_x, ld = self._xmm_f32(ops[0])
                self._emit(ld); self._emit(encode_cvttss2si(dst, src_x))
            else:
                src_x, ld = self._xmm(ops[0])
                self._emit(ld); self._emit(encode_cvttsd2si(dst, src_x))
            return

        if op == "fpext":
            src_x, ld = self._xmm_f32(ops[0])
            dst = self._dst_xmm(r)
            self._emit(ld); self._emit(_cvtss2sd(dst, src_x))
            return

        if op == "fptrunc":
            src_x, ld = self._xmm(ops[0])
            dst = self._dst_xmm(r)
            self._emit(ld); self._emit(_cvtsd2ss(dst, src_x))
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
            # Explicit register copy (from phi elimination)
            if r is None:
                return
            if _is_xmm(r.type.name):
                dst = self._dst_xmm(r)
                src_x, ld = self._xmm(ops[0])
                self._emit(ld)
                if src_x != dst: self._emit(encode_movdqa_rr(dst, src_x))
            else:
                dst = self._dst_gp(r)
                src_r, ld = self._gp(ops[0])
                self._emit(ld)
                if src_r != dst: self._emit(encode_mov_rr(dst, src_r))
            return

        if op in ("global_addr", "str_global"):
            dst     = self._dst_gp(r)
            lea_off = self._pos()
            self._emit(encode_lea_rip(dst, 0))
            # disp32 is at byte 3 (REX + 0x8D + ModRM); R_X86_64_PC32 for data refs
            self.fixups.append((lea_off + 3, str(ops[0]), R_X86_64_PC32))
            return

        if op == "tls_addr":
            # MOV dst, QWORD PTR FS:[tpoff32]  — local-exec TLS model (Linux x86-64)
            dst    = self._dst_gp(r)
            tls_off = self._pos()
            self._emit(encode_mov_tls_rm(dst, 0))
            # disp32 is at byte 5 (0x64 + REX + 0x8B + ModRM + SIB); R_X86_64_TPOFF32
            self.fixups.append((tls_off + 5, str(ops[0]), R_X86_64_TPOFF32))
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
