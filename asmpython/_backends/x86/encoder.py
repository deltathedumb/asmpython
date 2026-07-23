"""
x86 (32-bit / IA-32) instruction encoder.

All encode_* functions return bytes. Caller concatenates them into a flat
byte stream. Branch fixups are handled by codegen.py using placeholder zeros.

Differences from the x86-64 encoder this is modeled on:
  - No REX prefix exists in 32-bit mode -- every encode_* function here is
    correspondingly shorter (no _rex() calls at all).
  - Only 8 GP registers (EAX-EDI) and 8 XMM registers (XMM0-XMM7) exist;
    there is no REX.R/X/B extension bit, so ModRM/SIB register fields never
    need the "high" 4th bit x86-64's _hi() helper computes.
  - 32-bit operand size is the *default* encoding (what x86-64 spells with
    REX.W=0); there is no operand-size-64 form at all in this mode.
  - No RIP-relative addressing mode exists in 32-bit protected mode -- mod=00
    rm=101 means "[disp32]" (absolute), not "[RIP+disp32]". Position-
    independent code on this backend uses the classic
    call/pop-EBX-then-GOT-relative trick instead (handled in codegen.py,
    not here).
"""

import struct
from enum import IntEnum
from typing import NamedTuple


class Reg(IntEnum):
    EAX = 0;  ECX = 1;  EDX = 2;  EBX = 3
    ESP = 4;  EBP = 5;  ESI = 6;  EDI = 7


class XmmReg(IntEnum):
    XMM0 = 0; XMM1 = 1; XMM2 = 2; XMM3 = 3
    XMM4 = 4; XMM5 = 5; XMM6 = 6; XMM7 = 7


class CC(IntEnum):
    """Condition codes for Jcc / SETcc / CMOVcc (identical encoding to x86-64)."""
    O  = 0;  NO = 1;  B  = 2;  AE = 3
    E  = 4;  NE = 5;  BE = 6;  A  = 7
    S  = 8;  NS = 9;  P  = 10; NP = 11
    L  = 12; GE = 13; LE = 14; G  = 15


# Caller-saved (cdecl/stdcall, both System V i386 and Windows x86):
#   EAX ECX EDX            XMM0-XMM7
# Callee-saved:
#   EBX EBP ESI EDI
CALLEE_SAVED = (Reg.EBX, Reg.EBP, Reg.ESI, Reg.EDI)

# cdecl/stdcall/fastcall all pass GP/float args on the stack by default in
# this backend's baseline ABI (fastcall's first-two-args-in-ECX/EDX variant
# is a --abi option handled by regalloc.py, not a fact about the encoder).


class Mem(NamedTuple):
    """Memory operand: [base + index*scale + disp]"""
    base:  "Reg | None"   # None means absolute/disp32-only addressing
    disp:  int            = 0
    index: "Reg | None"   = None
    scale: int             = 1   # 1, 2, 4, or 8


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _modrm(mod: int, reg: int, rm: int) -> int:
    return ((mod & 3) << 6) | ((reg & 7) << 3) | (rm & 7)


def _sib(scale: int, index: int, base: int) -> int:
    ss = {1: 0, 2: 1, 4: 2, 8: 3}[scale]
    return (ss << 6) | ((index & 7) << 3) | (base & 7)


def _encode_mem(reg_field: int, m: Mem) -> bytes:
    """ModRM + optional SIB + displacement bytes for a memory operand."""
    if m.base is None:
        # mod=00 rm=101 -> disp32-only absolute addressing (no base register
        # at all); this is the 32-bit-mode encoding RIP-relative occupies in
        # x86-64 -- there is no RIP-relative form here, just a plain 32-bit
        # absolute address, matched by a relocation at link time.
        out = bytes([_modrm(0b00, reg_field & 7, 0b101)])
        return out + struct.pack("<i", m.disp)

    base  = int(m.base)
    disp  = m.disp
    index = m.index

    # ESP (base&7==4) always needs SIB to avoid mis-encoding
    use_sib = (index is not None) or (base & 7) == 4

    # EBP (base&7==5) with mod=00 means [disp32] with no base at all, so a
    # zero-displacement access through EBP must force mod=01 disp8=0 instead.
    if disp == 0 and (base & 7) != 5:
        mod = 0b00
    elif -128 <= disp <= 127:
        mod = 0b01
    else:
        mod = 0b10

    rm  = 0b100 if use_sib else (base & 7)
    out = bytes([_modrm(mod, reg_field & 7, rm)])

    if use_sib:
        idx = int(index) if index is not None else 4  # ESP == "no index"
        sc  = m.scale if index is not None else 1
        out += bytes([_sib(sc, idx, base)])

    if mod == 0b01:
        out += struct.pack("b", disp)
    elif mod == 0b10:
        out += struct.pack("<i", disp)

    return out


# ── MOV (32-bit GP) ───────────────────────────────────────────────────────────

def encode_mov_rr(dst: Reg, src: Reg) -> bytes:
    return bytes([0x8B, _modrm(0b11, int(dst), int(src))])


def encode_mov_ri(dst: Reg, imm: int) -> bytes:
    return bytes([0xB8 | int(dst)]) + struct.pack("<I", imm & 0xFFFF_FFFF)


def encode_mov_rm(dst: Reg, src: Mem) -> bytes:
    return bytes([0x8B]) + _encode_mem(int(dst), src)


def encode_mov_mr(dst: Mem, src: Reg) -> bytes:
    return bytes([0x89]) + _encode_mem(int(src), dst)


def encode_lea(dst: Reg, src: Mem) -> bytes:
    return bytes([0x8D]) + _encode_mem(int(dst), src)


# ── ALU (32-bit, reg-reg) ─────────────────────────────────────────────────────

def _alu_rr(op: int, dst: Reg, src: Reg) -> bytes:
    return bytes([op, _modrm(0b11, int(dst), int(src))])


def encode_add_rr(dst: Reg, src: Reg) -> bytes:  return _alu_rr(0x03, dst, src)
def encode_sub_rr(dst: Reg, src: Reg) -> bytes:  return _alu_rr(0x2B, dst, src)
def encode_and_rr(dst: Reg, src: Reg) -> bytes:  return _alu_rr(0x23, dst, src)
def encode_or_rr (dst: Reg, src: Reg) -> bytes:  return _alu_rr(0x0B, dst, src)
def encode_xor_rr(dst: Reg, src: Reg) -> bytes:  return _alu_rr(0x33, dst, src)
def encode_cmp_rr(a:   Reg, b:   Reg) -> bytes:  return _alu_rr(0x3B, a,   b  )
# ADC/SBB (add/subtract-with-carry) -- needed for register-pair 64-bit
# add/sub: the low dwords add/subtract normally, then the high dwords use
# these to fold in the low half's own carry/borrow (CF). Opcode/ModRM
# direction verified against real NASM output before use (`adc ecx, eax`
# -> `13 c8`, same reg<-r/m convention _alu_rr's own ADD/SUB/etc. already
# use, not the r/m<-reg direction NASM happens to pick by default for a
# bare `adc eax, ecx` mnemonic).
def encode_adc_rr(dst: Reg, src: Reg) -> bytes:  return _alu_rr(0x13, dst, src)
def encode_sbb_rr(dst: Reg, src: Reg) -> bytes:  return _alu_rr(0x1B, dst, src)
def encode_test_rr(a:  Reg, b:   Reg) -> bytes:
    # TEST r/m, r  (opcode 0x85; operand order flipped vs standard ALU)
    return bytes([0x85, _modrm(0b11, int(b), int(a))])


def encode_imul_rr(dst: Reg, src: Reg) -> bytes:
    return bytes([0x0F, 0xAF, _modrm(0b11, int(dst), int(src))])


def encode_idiv_r(src: Reg) -> bytes:
    return bytes([0xF7, _modrm(0b11, 7, int(src))])


def encode_div_r(src: Reg) -> bytes:
    return bytes([0xF7, _modrm(0b11, 6, int(src))])


def encode_mul_r(src: Reg) -> bytes:
    """MUL r/m32 -- unsigned EDX:EAX = EAX * src. The building block for
    64x64 multiplication: (a_hi:a_lo) * (b_hi:b_lo)'s low 64 bits =
    a_lo*b_lo (this instruction's full EDX:EAX result) plus
    (a_lo*b_hi + a_hi*b_lo) shifted left 32 (each of THOSE cross products
    only needs the LOW 32 bits kept, computed with plain `imul reg, reg`,
    since the shift-left-32 already pushes anything above bit 31 out of
    the final 64-bit result entirely). Verified against real NASM output
    (`mul ecx` -> `f7 e1`, /4 within the F7 opcode group -- same group
    idiv=/7, div=/6, neg=/3, not=/2 already use)."""
    return bytes([0xF7, _modrm(0b11, 4, int(src))])


def encode_neg(dst: Reg) -> bytes:
    return bytes([0xF7, _modrm(0b11, 3, int(dst))])


def encode_not(dst: Reg) -> bytes:
    return bytes([0xF7, _modrm(0b11, 2, int(dst))])


def encode_add_ri(dst: Reg, imm: int) -> bytes:
    if -128 <= imm <= 127:
        return bytes([0x83, _modrm(0b11, 0, int(dst)), imm & 0xFF])
    return bytes([0x81, _modrm(0b11, 0, int(dst))]) + struct.pack("<i", imm)


def encode_sub_ri(dst: Reg, imm: int) -> bytes:
    if -128 <= imm <= 127:
        return bytes([0x83, _modrm(0b11, 5, int(dst)), imm & 0xFF])
    return bytes([0x81, _modrm(0b11, 5, int(dst))]) + struct.pack("<i", imm)


def encode_cmp_ri(reg: Reg, imm: int) -> bytes:
    if -128 <= imm <= 127:
        return bytes([0x83, _modrm(0b11, 7, int(reg)), imm & 0xFF])
    return bytes([0x81, _modrm(0b11, 7, int(reg))]) + struct.pack("<i", imm)


def encode_xor_zero(dst: Reg) -> bytes:
    """XOR dst, dst — zeroes the register."""
    return bytes([0x33, _modrm(0b11, int(dst), int(dst))])


# ── Shifts ────────────────────────────────────────────────────────────────────

def encode_shl_ri(dst: Reg, n: int) -> bytes:
    if n == 1:
        return bytes([0xD1, _modrm(0b11, 4, int(dst))])
    return bytes([0xC1, _modrm(0b11, 4, int(dst)), n & 0xFF])


def encode_shr_ri(dst: Reg, n: int) -> bytes:
    if n == 1:
        return bytes([0xD1, _modrm(0b11, 5, int(dst))])
    return bytes([0xC1, _modrm(0b11, 5, int(dst)), n & 0xFF])


def encode_sar_ri(dst: Reg, n: int) -> bytes:
    if n == 1:
        return bytes([0xD1, _modrm(0b11, 7, int(dst))])
    return bytes([0xC1, _modrm(0b11, 7, int(dst)), n & 0xFF])


def encode_shl_cl(dst: Reg) -> bytes:
    return bytes([0xD3, _modrm(0b11, 4, int(dst))])


def encode_shr_cl(dst: Reg) -> bytes:
    return bytes([0xD3, _modrm(0b11, 5, int(dst))])


def encode_sar_cl(dst: Reg) -> bytes:
    return bytes([0xD3, _modrm(0b11, 7, int(dst))])


# SHLD/SHRD (double-precision shift) -- the real primitive for shifting a
# 64-bit register-pair value by a constant/variable amount less than 32:
# `shld dst, src, n` sets dst = (dst << n) | (src >> (32-n)), i.e. shifts
# `n` bits of `src`'s TOP into `dst`'s bottom while dst shifts left --
# exactly the operation needed to shift a 64-bit value's high dword left
# while pulling in bits vacated from the low dword shifting left
# alongside it (and the reverse, SHRD, for right shifts). The existing
# __udivdi64/__umoddi64 runtime helpers already use SHLD internally for
# their own 64-bit shift-and-subtract division loop (see abi_shims_
# x86_32.asm) -- this is the same primitive, exposed here for codegen.py's
# own inline shl/shr/sar handling of i64 values. Verified against real
# NASM output before use: `shld eax, ecx, 5` -> `0f a4 c8 05`, `shld eax,
# ecx, cl` -> `0f a5 c8`, `shrd eax, ecx, 5` -> `0f ac c8 05`, `shrd eax,
# ecx, cl` -> `0f ad c8`.
def encode_shld_ri(dst: Reg, src: Reg, n: int) -> bytes:
    return bytes([0x0F, 0xA4, _modrm(0b11, int(src), int(dst)), n & 0xFF])


def encode_shld_cl(dst: Reg, src: Reg) -> bytes:
    return bytes([0x0F, 0xA5, _modrm(0b11, int(src), int(dst))])


def encode_shrd_ri(dst: Reg, src: Reg, n: int) -> bytes:
    return bytes([0x0F, 0xAC, _modrm(0b11, int(src), int(dst)), n & 0xFF])


def encode_shrd_cl(dst: Reg, src: Reg) -> bytes:
    return bytes([0x0F, 0xAD, _modrm(0b11, int(src), int(dst))])


# ── Sign / zero extension ─────────────────────────────────────────────────────

def encode_movsx(dst: Reg, src: Reg, src_bits: int) -> bytes:
    """MOVSX — sign-extend src_bits (8/16) into 32-bit dst.

    Unlike x86-64's encode_movsx, there is no src_bits == 32 case here: a
    32-bit source is already the destination's full width, so a plain
    encode_mov_rr covers that case (matching how x86-64's MOVSXD r64,r/m32
    has no 32-bit-mode equivalent needed).
    """
    rm = _modrm(0b11, int(dst), int(src))
    if src_bits == 8:
        return bytes([0x0F, 0xBE, rm])
    return bytes([0x0F, 0xBF, rm])


def encode_movzx(dst: Reg, src: Reg, src_bits: int) -> bytes:
    """MOVZX — zero-extend src_bits (8 or 16) into dst."""
    rm = _modrm(0b11, int(dst), int(src))
    if src_bits == 8:
        return bytes([0x0F, 0xB6, rm])
    return bytes([0x0F, 0xB7, rm])


# ── Sign-extend EAX into EDX:EAX (before IDIV) ───────────────────────────────

def encode_cdq() -> bytes:
    return bytes([0x99])


# ── Stack ─────────────────────────────────────────────────────────────────────

def encode_push(reg: Reg) -> bytes:
    return bytes([0x50 | int(reg)])


def encode_pop(reg: Reg) -> bytes:
    return bytes([0x58 | int(reg)])


def encode_push_i(imm: int) -> bytes:
    """PUSH imm32 — used to push stack-passed call arguments (cdecl/stdcall)."""
    return bytes([0x68]) + struct.pack("<i", imm)


def encode_push_m(src: Mem) -> bytes:
    return bytes([0xFF]) + _encode_mem(6, src)


# ── Control flow ──────────────────────────────────────────────────────────────

def encode_ret() -> bytes:
    return bytes([0xC3])


def encode_ret_n(n: int) -> bytes:
    """RET imm16 — pop n bytes of stack-passed args on return (stdcall/fastcall's
    callee-cleanup convention; cdecl always uses plain encode_ret and lets the
    caller clean up instead)."""
    return bytes([0xC2]) + struct.pack("<H", n & 0xFFFF)


def encode_call_rel32(rel32: int) -> bytes:
    """CALL rel32; rel32 = target - (call_site + 5)."""
    return bytes([0xE8]) + struct.pack("<i", rel32)


def encode_call_r(reg: Reg) -> bytes:
    return bytes([0xFF, _modrm(0b11, 2, int(reg))])


def encode_jmp_rel8(rel8: int) -> bytes:
    return bytes([0xEB, rel8 & 0xFF])


def encode_jmp_rel32(rel32: int) -> bytes:
    return bytes([0xE9]) + struct.pack("<i", rel32)


def encode_jmp_r(reg: Reg) -> bytes:
    return bytes([0xFF, _modrm(0b11, 4, int(reg))])


def encode_jcc_rel8(cc: CC, rel8: int) -> bytes:
    return bytes([0x70 | int(cc), rel8 & 0xFF])


def encode_jcc_rel32(cc: CC, rel32: int) -> bytes:
    return bytes([0x0F, 0x80 | int(cc)]) + struct.pack("<i", rel32)


def encode_setcc(cc: CC, dst: Reg) -> bytes:
    """SETcc dst8 — writes 0 or 1 into the low byte of dst.

    Unlike x86-64, every one of the 8 GP registers has a valid low-byte
    form (AL/CL/DL/BL/AH/CH/DH/BH via mod=11) with no REX needed and no
    ambiguity to guard against -- 32-bit mode's SETcc always addresses
    AL/CL/DL/BL for regs 0-3, and the classic high-byte registers AH/CH/DH/BH
    for regs 4-7 (there is no way to reach SPL/BPL/SIL/DIL at all in this
    mode, since that REX-gated encoding doesn't exist here). The register
    allocator must not treat ESP/EBP/ESI/EDI's SETcc destination as if it
    wrote their low byte -- see regalloc.py's SETCC_SAFE_REGS restriction.
    """
    return bytes([0x0F, 0x90 | int(cc), _modrm(0b11, 0, int(dst))])


def encode_nop() -> bytes:
    return bytes([0x90])


# ── SSE2 scalar double (f64) ──────────────────────────────────────────────────

def _sse(prefix: "int | None", opcode: int, dst: XmmReg, src: XmmReg) -> bytes:
    rm  = _modrm(0b11, int(dst), int(src))
    pfx = bytes([prefix]) if prefix is not None else b""
    return pfx + bytes([0x0F, opcode, rm])


def encode_movsd_rr(dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF2, 0x10, dst, src)
def encode_addsd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF2, 0x58, dst, src)
def encode_subsd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF2, 0x5C, dst, src)
def encode_mulsd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF2, 0x59, dst, src)
def encode_divsd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF2, 0x5E, dst, src)
def encode_ucomisd (a:   XmmReg, b:   XmmReg) -> bytes: return _sse(0x66, 0x2E, a,   b  )


def encode_movsd_rm(dst: XmmReg, src: Mem) -> bytes:
    return bytes([0xF2, 0x0F, 0x10]) + _encode_mem(int(dst), src)


def encode_movsd_mr(dst: Mem, src: XmmReg) -> bytes:
    return bytes([0xF2, 0x0F, 0x11]) + _encode_mem(int(src), dst)


def encode_cvtsi2sd(dst: XmmReg, src: Reg) -> bytes:
    """CVTSI2SD dst, src32 — GP integer → XMM double."""
    return bytes([0xF2, 0x0F, 0x2A, _modrm(0b11, int(dst), int(src))])


def encode_cvttsd2si(dst: Reg, src: XmmReg) -> bytes:
    """CVTTSD2SI dst32, src — XMM double → GP integer (truncated)."""
    return bytes([0xF2, 0x0F, 0x2C, _modrm(0b11, int(dst), int(src))])


# There is no encode_movq_xmm_gp/encode_movq_gp_xmm here: those bitcast a
# full 64-bit GP register's raw bits into/out of an XMM register on x86-64.
# A 32-bit GP register is only half that width, so this backend's IR lowering
# must route float<->int-slot storage through a different, two-32-bit-halves
# scheme (see codegen.py) rather than a single-instruction 64-bit MOVQ.


# ── SSE scalar float (f32) ────────────────────────────────────────────────────

def encode_movss_rr(dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF3, 0x10, dst, src)
def encode_addss   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF3, 0x58, dst, src)
def encode_subss   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF3, 0x5C, dst, src)
def encode_mulss   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF3, 0x59, dst, src)
def encode_divss   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF3, 0x5E, dst, src)
def encode_ucomiss (a:   XmmReg, b:   XmmReg) -> bytes: return _sse(None,  0x2E, a,   b  )


def encode_movss_rm(dst: XmmReg, src: Mem) -> bytes:
    return bytes([0xF3, 0x0F, 0x10]) + _encode_mem(int(dst), src)


def encode_movss_mr(dst: Mem, src: XmmReg) -> bytes:
    return bytes([0xF3, 0x0F, 0x11]) + _encode_mem(int(src), dst)


def encode_cvtsi2ss(dst: XmmReg, src: Reg) -> bytes:
    return bytes([0xF3, 0x0F, 0x2A, _modrm(0b11, int(dst), int(src))])


def encode_cvttss2si(dst: Reg, src: XmmReg) -> bytes:
    return bytes([0xF3, 0x0F, 0x2C, _modrm(0b11, int(dst), int(src))])


# ── Typed byte / word memory loads & stores ───────────────────────────────────

def encode_movzx_rm8(dst: Reg, src: Mem) -> bytes:
    """MOVZX r32, byte [src] — zero-extend byte load."""
    return bytes([0x0F, 0xB6]) + _encode_mem(int(dst), src)


def encode_mov_mr8(dst: Mem, src: Reg) -> bytes:
    """MOV byte [dst], src8 — byte store (low 8 bits of src).

    Only EAX/ECX/EDX/EBX (regs 0-3) have an addressable low byte without
    any prefix trickery in 32-bit mode; codegen.py must keep any value
    destined for a byte store in one of those four (there is no REX-gated
    SPL/BPL/SIL/DIL escape hatch the way x86-64 has one).
    """
    return bytes([0x88]) + _encode_mem(int(src), dst)


def encode_movzx_rm16(dst: Reg, src: Mem) -> bytes:
    """MOVZX r32, word [src] — zero-extend 16-bit load."""
    return bytes([0x0F, 0xB7]) + _encode_mem(int(dst), src)


def encode_mov_rm32(dst: Reg, src: Mem) -> bytes:
    """MOV r32, [src] — 32-bit load (this backend's only GP load width)."""
    return bytes([0x8B]) + _encode_mem(int(dst), src)


def encode_mov_mr32(dst: Mem, src: Reg) -> bytes:
    """MOV [dst], r32 — 32-bit store."""
    return bytes([0x89]) + _encode_mem(int(src), dst)


# ── TLS (Thread-Local Storage) access via GS segment ─────────────────────────

def encode_mov_tls_rm(dst: Reg, tpoff32: int = 0) -> bytes:
    """MOV dst, DWORD PTR GS:[tpoff32] — load TLS slot at offset tpoff32.

    32-bit Linux's variant-II TLS ABI addresses thread-local storage through
    the GS segment (0x65 prefix), mirroring x86-64's FS-segment convention
    (see encode_mov_tls_rm in the x86-64 encoder) one segment register over.
    """
    modrm = _modrm(0b00, int(dst), 0b101)  # rm=101 -> disp32-only addressing
    return bytes([0x65, 0x8B, modrm]) + struct.pack("<i", tpoff32)


# ── SIMD packed float (SSE) ───────────────────────────────────────────────────

def encode_addps   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(None, 0x58, dst, src)
def encode_subps   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(None, 0x5C, dst, src)
def encode_mulps   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(None, 0x59, dst, src)
def encode_divps   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(None, 0x5E, dst, src)
def encode_maxps   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(None, 0x5F, dst, src)
def encode_minps   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(None, 0x5D, dst, src)
def encode_andps   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(None, 0x54, dst, src)
def encode_orps    (dst: XmmReg, src: XmmReg) -> bytes: return _sse(None, 0x56, dst, src)
def encode_xorps   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(None, 0x57, dst, src)
def encode_movaps_rr(dst: XmmReg, src: XmmReg) -> bytes: return _sse(None, 0x28, dst, src)


def encode_shufps(dst: XmmReg, src: XmmReg, imm8: int) -> bytes:
    return _sse(None, 0xC6, dst, src) + bytes([imm8 & 0xFF])


# ── SIMD packed double (SSE2) ─────────────────────────────────────────────────

def encode_addpd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x58, dst, src)
def encode_subpd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x5C, dst, src)
def encode_mulpd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x59, dst, src)
def encode_divpd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x5E, dst, src)
def encode_maxpd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x5F, dst, src)
def encode_minpd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x5D, dst, src)
def encode_andpd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x54, dst, src)
def encode_orpd    (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x56, dst, src)
def encode_xorpd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x57, dst, src)
def encode_movapd_rr(dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x28, dst, src)


# ── SIMD packed integer (SSE2 / SSE4.1) ──────────────────────────────────────

def encode_paddb   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0xFC, dst, src)
def encode_paddw   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0xFD, dst, src)
def encode_paddd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0xFE, dst, src)
def encode_paddq   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0xD4, dst, src)
def encode_psubb   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0xF8, dst, src)
def encode_psubw   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0xF9, dst, src)
def encode_psubd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0xFA, dst, src)
def encode_psubq   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0xFB, dst, src)
def encode_pand    (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0xDB, dst, src)
def encode_por     (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0xEB, dst, src)
def encode_pxor    (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0xEF, dst, src)
def encode_pcmpeqb (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x74, dst, src)
def encode_pcmpeqw (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x75, dst, src)
def encode_pcmpeqd (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x76, dst, src)
def encode_movdqa_rr(dst: XmmReg, src: XmmReg) -> bytes: return _sse(0x66, 0x6F, dst, src)
def encode_movdqu_rr(dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF3, 0x6F, dst, src)


def encode_pmulld(dst: XmmReg, src: XmmReg) -> bytes:
    """PMULLD dst, src — packed 32-bit multiply low (SSE4.1, 4-byte opcode)."""
    rm = _modrm(0b11, int(dst), int(src))
    return bytes([0x66, 0x0F, 0x38, 0x40, rm])


def encode_pshufd(dst: XmmReg, src: XmmReg, imm8: int) -> bytes:
    """PSHUFD dst, src, imm8 — shuffle packed 32-bit ints."""
    return _sse(0x66, 0x70, dst, src) + bytes([imm8 & 0xFF])


# ── SIMD memory loads / stores ────────────────────────────────────────────────

def encode_movdqu_rm(dst: XmmReg, src: Mem) -> bytes:
    """MOVDQU xmm, [src] — unaligned 128-bit load from memory."""
    return bytes([0xF3, 0x0F, 0x6F]) + _encode_mem(int(dst), src)


def encode_movdqu_mr(dst: Mem, src: XmmReg) -> bytes:
    """MOVDQU [dst], xmm — unaligned 128-bit store to memory."""
    return bytes([0xF3, 0x0F, 0x7F]) + _encode_mem(int(src), dst)


def encode_movdqa_rm(dst: XmmReg, src: Mem) -> bytes:
    """MOVDQA xmm, [src] — aligned 128-bit load from memory."""
    return bytes([0x66, 0x0F, 0x6F]) + _encode_mem(int(dst), src)


def encode_movdqa_mr(dst: Mem, src: XmmReg) -> bytes:
    """MOVDQA [dst], xmm — aligned 128-bit store to memory."""
    return bytes([0x66, 0x0F, 0x7F]) + _encode_mem(int(src), dst)


def encode_movaps_rm(dst: XmmReg, src: Mem) -> bytes:
    """MOVAPS xmm, [src] — aligned packed-float load."""
    return bytes([0x0F, 0x28]) + _encode_mem(int(dst), src)


def encode_movaps_mr(dst: Mem, src: XmmReg) -> bytes:
    """MOVAPS [dst], xmm — aligned packed-float store."""
    return bytes([0x0F, 0x29]) + _encode_mem(int(src), dst)
