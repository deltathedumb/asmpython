"""
x86-64 instruction encoder.

All encode_* functions return bytes. Caller concatenates them into a flat
byte stream. Branch fixups are handled by codegen.py using placeholder zeros.
"""

import struct
from enum import IntEnum
from typing import NamedTuple


class Reg(IntEnum):
    RAX = 0;  RCX = 1;  RDX = 2;  RBX = 3
    RSP = 4;  RBP = 5;  RSI = 6;  RDI = 7
    R8  = 8;  R9  = 9;  R10 = 10; R11 = 11
    R12 = 12; R13 = 13; R14 = 14; R15 = 15


class XmmReg(IntEnum):
    XMM0  = 0;  XMM1  = 1;  XMM2  = 2;  XMM3  = 3
    XMM4  = 4;  XMM5  = 5;  XMM6  = 6;  XMM7  = 7
    XMM8  = 8;  XMM9  = 9;  XMM10 = 10; XMM11 = 11
    XMM12 = 12; XMM13 = 13; XMM14 = 14; XMM15 = 15


class CC(IntEnum):
    """Condition codes for Jcc / SETcc / CMOVcc."""
    O  = 0;  NO = 1;  B  = 2;  AE = 3
    E  = 4;  NE = 5;  BE = 6;  A  = 7
    S  = 8;  NS = 9;  P  = 10; NP = 11
    L  = 12; GE = 13; LE = 14; G  = 15


# Integer argument registers
ARG_REGS_SYSV  = (Reg.RDI, Reg.RSI, Reg.RDX, Reg.RCX, Reg.R8, Reg.R9)
ARG_REGS_WIN64 = (Reg.RCX, Reg.RDX, Reg.R8,  Reg.R9)
RET_REG        = Reg.RAX
RET_XMM        = XmmReg.XMM0

# XMM argument registers
XMM_ARG_SYSV  = tuple(XmmReg(i) for i in range(8))   # XMM0-XMM7
XMM_ARG_WIN64 = tuple(XmmReg(i) for i in range(4))   # XMM0-XMM3

# Caller-saved under SysV:  RAX RCX RDX RSI RDI R8-R11  XMM0-XMM15
# Callee-saved under SysV:  RBX RBP R12-R15
CALLEE_SAVED_SYSV  = (Reg.RBX, Reg.RBP, Reg.R12, Reg.R13, Reg.R14, Reg.R15)
# Caller-saved under Win64: RAX RCX RDX R8-R11            XMM0-XMM5
# Callee-saved under Win64: RBX RBP RDI RSI R12-R15       XMM6-XMM15
CALLEE_SAVED_WIN64 = (Reg.RBX, Reg.RBP, Reg.RDI, Reg.RSI,
                      Reg.R12, Reg.R13, Reg.R14, Reg.R15)


class Mem(NamedTuple):
    """Memory operand: [base + index*scale + disp]"""
    base:  Reg
    disp:  int           = 0
    index: "Reg | None"  = None
    scale: int           = 1   # 1, 2, 4, or 8


# ── Low-level helpers ─────────────────────────────────────────────────────────

def _rex(w: int = 0, r: int = 0, x: int = 0, b: int = 0) -> bytes:
    if not (w or r or x or b):
        return b""
    return bytes([0x40 | (w << 3) | (r << 2) | (x << 1) | b])


def _modrm(mod: int, reg: int, rm: int) -> int:
    return ((mod & 3) << 6) | ((reg & 7) << 3) | (rm & 7)


def _sib(scale: int, index: int, base: int) -> int:
    ss = {1: 0, 2: 1, 4: 2, 8: 3}[scale]
    return (ss << 6) | ((index & 7) << 3) | (base & 7)


def _hi(r: "Reg | XmmReg") -> int:
    return 1 if int(r) >= 8 else 0


def _encode_mem(reg_field: int, m: Mem) -> bytes:
    """ModRM + optional SIB + displacement bytes for a memory operand."""
    base  = int(m.base)
    disp  = m.disp
    index = m.index

    # RSP/R12 (base&7==4) always need SIB to avoid mis-encoding
    use_sib = (index is not None) or (base & 7) == 4

    # RBP/R13 (base&7==5) with mod=00 means [RIP+disp32], force mod=01
    if disp == 0 and (base & 7) != 5:
        mod = 0b00
    elif -128 <= disp <= 127:
        mod = 0b01
    else:
        mod = 0b10

    rm  = 0b100 if use_sib else (base & 7)
    out = bytes([_modrm(mod, reg_field & 7, rm)])

    if use_sib:
        idx = int(index) if index is not None else 4  # RSP == "no index"
        sc  = m.scale if index is not None else 1
        out += bytes([_sib(sc, idx, base)])

    if mod == 0b01:
        out += struct.pack("b", disp)
    elif mod == 0b10:
        out += struct.pack("<i", disp)

    return out


# ── MOV (64-bit GP) ───────────────────────────────────────────────────────────

def encode_mov_rr(dst: Reg, src: Reg) -> bytes:
    return _rex(w=1, r=_hi(dst), b=_hi(src)) + bytes([0x8B, _modrm(0b11, int(dst), int(src))])


def encode_mov_ri(dst: Reg, imm: int) -> bytes:
    if 0 <= imm <= 0xFFFF_FFFF:
        # MOV r32, imm32 — zero-extends; skip REX.W
        return _rex(b=_hi(dst)) + bytes([0xB8 | (int(dst) & 7)]) + struct.pack("<I", imm)
    return (_rex(w=1, b=_hi(dst))
            + bytes([0xB8 | (int(dst) & 7)])
            + struct.pack("<Q", imm & 0xFFFF_FFFF_FFFF_FFFF))


def encode_mov_rm(dst: Reg, src: Mem) -> bytes:
    return (_rex(w=1, r=_hi(dst), b=_hi(src.base))
            + bytes([0x8B]) + _encode_mem(int(dst), src))


def encode_mov_mr(dst: Mem, src: Reg) -> bytes:
    return (_rex(w=1, r=_hi(src), b=_hi(dst.base))
            + bytes([0x89]) + _encode_mem(int(src), dst))


def encode_lea(dst: Reg, src: Mem) -> bytes:
    return (_rex(w=1, r=_hi(dst), b=_hi(src.base))
            + bytes([0x8D]) + _encode_mem(int(dst), src))


def encode_lea_rip(dst: Reg, disp32: int = 0) -> bytes:
    """LEA dst, [RIP + disp32] — RIP-relative address (for global symbol references)."""
    modrm = _modrm(0b00, int(dst) & 7, 0b101)
    return (_rex(w=1, r=_hi(dst))
            + bytes([0x8D, modrm])
            + struct.pack("<i", disp32))


# ── ALU (64-bit, reg-reg) ─────────────────────────────────────────────────────

def _alu_rr(op: int, dst: Reg, src: Reg) -> bytes:
    return _rex(w=1, r=_hi(dst), b=_hi(src)) + bytes([op, _modrm(0b11, int(dst), int(src))])


def encode_add_rr(dst: Reg, src: Reg) -> bytes:  return _alu_rr(0x03, dst, src)
def encode_sub_rr(dst: Reg, src: Reg) -> bytes:  return _alu_rr(0x2B, dst, src)
def encode_and_rr(dst: Reg, src: Reg) -> bytes:  return _alu_rr(0x23, dst, src)
def encode_or_rr (dst: Reg, src: Reg) -> bytes:  return _alu_rr(0x0B, dst, src)
def encode_xor_rr(dst: Reg, src: Reg) -> bytes:  return _alu_rr(0x33, dst, src)
def encode_cmp_rr(a:   Reg, b:   Reg) -> bytes:  return _alu_rr(0x3B, a,   b  )
def encode_test_rr(a:  Reg, b:   Reg) -> bytes:
    # TEST r/m, r  (opcode 0x85; operand order flipped vs standard ALU)
    return _rex(w=1, r=_hi(b), b=_hi(a)) + bytes([0x85, _modrm(0b11, int(b), int(a))])


def encode_imul_rr(dst: Reg, src: Reg) -> bytes:
    return (_rex(w=1, r=_hi(dst), b=_hi(src))
            + bytes([0x0F, 0xAF, _modrm(0b11, int(dst), int(src))]))


def encode_idiv_r(src: Reg) -> bytes:
    return _rex(w=1, b=_hi(src)) + bytes([0xF7, _modrm(0b11, 7, int(src))])


def encode_div_r(src: Reg) -> bytes:
    return _rex(w=1, b=_hi(src)) + bytes([0xF7, _modrm(0b11, 6, int(src))])


def encode_neg(dst: Reg) -> bytes:
    return _rex(w=1, b=_hi(dst)) + bytes([0xF7, _modrm(0b11, 3, int(dst))])


def encode_not(dst: Reg) -> bytes:
    return _rex(w=1, b=_hi(dst)) + bytes([0xF7, _modrm(0b11, 2, int(dst))])


def encode_add_ri(dst: Reg, imm: int) -> bytes:
    rex = _rex(w=1, b=_hi(dst))
    if -128 <= imm <= 127:
        return rex + bytes([0x83, _modrm(0b11, 0, int(dst)), imm & 0xFF])
    return rex + bytes([0x81, _modrm(0b11, 0, int(dst))]) + struct.pack("<i", imm)


def encode_sub_ri(dst: Reg, imm: int) -> bytes:
    rex = _rex(w=1, b=_hi(dst))
    if -128 <= imm <= 127:
        return rex + bytes([0x83, _modrm(0b11, 5, int(dst)), imm & 0xFF])
    return rex + bytes([0x81, _modrm(0b11, 5, int(dst))]) + struct.pack("<i", imm)


def encode_cmp_ri(reg: Reg, imm: int) -> bytes:
    rex = _rex(w=1, b=_hi(reg))
    if -128 <= imm <= 127:
        return rex + bytes([0x83, _modrm(0b11, 7, int(reg)), imm & 0xFF])
    return rex + bytes([0x81, _modrm(0b11, 7, int(reg))]) + struct.pack("<i", imm)


def encode_xor_zero(dst: Reg) -> bytes:
    """XOR dst, dst — 32-bit form; zero-extends to 64 bits."""
    return _rex(r=_hi(dst), b=_hi(dst)) + bytes([0x33, _modrm(0b11, int(dst), int(dst))])


# ── Shifts ────────────────────────────────────────────────────────────────────

def encode_shl_ri(dst: Reg, n: int) -> bytes:
    rex = _rex(w=1, b=_hi(dst))
    if n == 1:
        return rex + bytes([0xD1, _modrm(0b11, 4, int(dst))])
    return rex + bytes([0xC1, _modrm(0b11, 4, int(dst)), n & 0xFF])


def encode_shr_ri(dst: Reg, n: int) -> bytes:
    rex = _rex(w=1, b=_hi(dst))
    if n == 1:
        return rex + bytes([0xD1, _modrm(0b11, 5, int(dst))])
    return rex + bytes([0xC1, _modrm(0b11, 5, int(dst)), n & 0xFF])


def encode_sar_ri(dst: Reg, n: int) -> bytes:
    rex = _rex(w=1, b=_hi(dst))
    if n == 1:
        return rex + bytes([0xD1, _modrm(0b11, 7, int(dst))])
    return rex + bytes([0xC1, _modrm(0b11, 7, int(dst)), n & 0xFF])


def encode_shl_cl(dst: Reg) -> bytes:
    return _rex(w=1, b=_hi(dst)) + bytes([0xD3, _modrm(0b11, 4, int(dst))])


def encode_shr_cl(dst: Reg) -> bytes:
    return _rex(w=1, b=_hi(dst)) + bytes([0xD3, _modrm(0b11, 5, int(dst))])


def encode_sar_cl(dst: Reg) -> bytes:
    return _rex(w=1, b=_hi(dst)) + bytes([0xD3, _modrm(0b11, 7, int(dst))])


# ── Sign / zero extension ─────────────────────────────────────────────────────

def encode_movsx(dst: Reg, src: Reg, src_bits: int) -> bytes:
    """MOVSX/MOVSXD — sign-extend src_bits (8/16/32) into 64-bit dst."""
    rex = _rex(w=1, r=_hi(dst), b=_hi(src))
    rm  = _modrm(0b11, int(dst), int(src))
    if src_bits == 8:
        return rex + bytes([0x0F, 0xBE, rm])
    if src_bits == 16:
        return rex + bytes([0x0F, 0xBF, rm])
    return rex + bytes([0x63, rm])   # MOVSXD r64, r/m32


def encode_movzx(dst: Reg, src: Reg, src_bits: int) -> bytes:
    """MOVZX — zero-extend src_bits (8 or 16) into dst."""
    rex = _rex(w=1, r=_hi(dst), b=_hi(src))
    rm  = _modrm(0b11, int(dst), int(src))
    if src_bits == 8:
        return rex + bytes([0x0F, 0xB6, rm])
    return rex + bytes([0x0F, 0xB7, rm])


# ── Sign-extend RAX into RDX:RAX (before IDIV) ───────────────────────────────

def encode_cqo() -> bytes:
    return bytes([0x48, 0x99])


# ── Stack ─────────────────────────────────────────────────────────────────────

def encode_push(reg: Reg) -> bytes:
    return _rex(b=_hi(reg)) + bytes([0x50 | (int(reg) & 7)])


def encode_pop(reg: Reg) -> bytes:
    return _rex(b=_hi(reg)) + bytes([0x58 | (int(reg) & 7)])


# ── Control flow ──────────────────────────────────────────────────────────────

def encode_ret() -> bytes:
    return bytes([0xC3])


def encode_call_rel32(rel32: int) -> bytes:
    """CALL rel32; rel32 = target - (call_site + 5)."""
    return bytes([0xE8]) + struct.pack("<i", rel32)


def encode_call_r(reg: Reg) -> bytes:
    return _rex(b=_hi(reg)) + bytes([0xFF, _modrm(0b11, 2, int(reg))])


def encode_jmp_rel8(rel8: int) -> bytes:
    return bytes([0xEB, rel8 & 0xFF])


def encode_jmp_rel32(rel32: int) -> bytes:
    return bytes([0xE9]) + struct.pack("<i", rel32)


def encode_jmp_r(reg: Reg) -> bytes:
    return _rex(b=_hi(reg)) + bytes([0xFF, _modrm(0b11, 4, int(reg))])


def encode_jcc_rel8(cc: CC, rel8: int) -> bytes:
    return bytes([0x70 | int(cc), rel8 & 0xFF])


def encode_jcc_rel32(cc: CC, rel32: int) -> bytes:
    return bytes([0x0F, 0x80 | int(cc)]) + struct.pack("<i", rel32)


def encode_setcc(cc: CC, dst: Reg) -> bytes:
    """SETcc dst8 — writes 0 or 1 into the low byte of dst.

    Registers 4-7 (RSP/RBP/RSI/RDI) need a REX prefix to select their
    low-byte form (SPL/BPL/SIL/DIL) -- without one, that same 3-bit
    encoding means the legacy high-byte registers (AH/CH/DH/BH) instead,
    silently writing the comparison result into the wrong register
    whenever the allocator picks RSI/RDI/RBP/RSP for the destination
    (RSI/RDI are in _GP_POOL, so this isn't a rare allocation outcome).
    """
    rex = _rex(b=_hi(dst))
    if not rex and (int(dst) & 7) in (4, 5, 6, 7):
        rex = bytes([0x40])
    return rex + bytes([0x0F, 0x90 | int(cc), _modrm(0b11, 0, int(dst))])


def encode_nop() -> bytes:
    return bytes([0x90])


# ── SSE2 scalar double (f64) ──────────────────────────────────────────────────

def _sse(prefix: "int | None", opcode: int, dst: XmmReg, src: XmmReg,
         w: int = 0) -> bytes:
    rex = _rex(w=w, r=_hi(dst), b=_hi(src))
    rm  = _modrm(0b11, int(dst), int(src))
    pfx = bytes([prefix]) if prefix is not None else b""
    return pfx + rex + bytes([0x0F, opcode, rm])


def encode_movsd_rr(dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF2, 0x10, dst, src)
def encode_addsd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF2, 0x58, dst, src)
def encode_subsd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF2, 0x5C, dst, src)
def encode_mulsd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF2, 0x59, dst, src)
def encode_divsd   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF2, 0x5E, dst, src)
def encode_ucomisd (a:   XmmReg, b:   XmmReg) -> bytes: return _sse(0x66, 0x2E, a,   b  )


def encode_movsd_rm(dst: XmmReg, src: Mem) -> bytes:
    rex = _rex(r=_hi(dst), b=_hi(src.base))
    return bytes([0xF2]) + rex + bytes([0x0F, 0x10]) + _encode_mem(int(dst), src)


def encode_movsd_mr(dst: Mem, src: XmmReg) -> bytes:
    rex = _rex(r=_hi(src), b=_hi(dst.base))
    return bytes([0xF2]) + rex + bytes([0x0F, 0x11]) + _encode_mem(int(src), dst)


def encode_cvtsi2sd(dst: XmmReg, src: Reg) -> bytes:
    """CVTSI2SD dst, src64 — GP integer → XMM double."""
    rex = _rex(w=1, r=_hi(dst), b=_hi(src))
    return bytes([0xF2]) + rex + bytes([0x0F, 0x2A, _modrm(0b11, int(dst), int(src))])


def encode_cvttsd2si(dst: Reg, src: XmmReg) -> bytes:
    """CVTTSD2SI dst64, src — XMM double → GP integer (truncated)."""
    rex = _rex(w=1, r=_hi(dst), b=_hi(src))
    return bytes([0xF2]) + rex + bytes([0x0F, 0x2C, _modrm(0b11, int(dst), int(src))])


def encode_movq_xmm_gp(dst: XmmReg, src: Reg) -> bytes:
    """MOVQ xmm, r64 — raw 64-bit bit-move GP -> XMM (no numeric
    conversion, unlike CVTSI2SD): the exact bits of an integer register
    become the exact bits of a double. Used to bitcast a float's already-
    integer-typed IR value (e.g. read out of a dict/list slot that only
    stores 8-byte cells) back into a real double, and the reverse
    (encode_movq_gp_xmm) to store a float into one of those slots."""
    rex = _rex(w=1, r=_hi(dst), b=_hi(src))
    return bytes([0x66]) + rex + bytes([0x0F, 0x6E, _modrm(0b11, int(dst), int(src))])


def encode_movq_gp_xmm(dst: Reg, src: XmmReg) -> bytes:
    """MOVQ r64, xmm — raw 64-bit bit-move XMM -> GP (see encode_movq_xmm_gp)."""
    rex = _rex(w=1, r=_hi(src), b=_hi(dst))
    return bytes([0x66]) + rex + bytes([0x0F, 0x7E, _modrm(0b11, int(src), int(dst))])


# ── SSE scalar float (f32) ────────────────────────────────────────────────────

def encode_movss_rr(dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF3, 0x10, dst, src)
def encode_addss   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF3, 0x58, dst, src)
def encode_subss   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF3, 0x5C, dst, src)
def encode_mulss   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF3, 0x59, dst, src)
def encode_divss   (dst: XmmReg, src: XmmReg) -> bytes: return _sse(0xF3, 0x5E, dst, src)
def encode_ucomiss (a:   XmmReg, b:   XmmReg) -> bytes: return _sse(None,  0x2E, a,   b  )


def encode_movss_rm(dst: XmmReg, src: Mem) -> bytes:
    rex = _rex(r=_hi(dst), b=_hi(src.base))
    return bytes([0xF3]) + rex + bytes([0x0F, 0x10]) + _encode_mem(int(dst), src)


def encode_movss_mr(dst: Mem, src: XmmReg) -> bytes:
    rex = _rex(r=_hi(src), b=_hi(dst.base))
    return bytes([0xF3]) + rex + bytes([0x0F, 0x11]) + _encode_mem(int(src), dst)


def encode_cvtsi2ss(dst: XmmReg, src: Reg) -> bytes:
    rex = _rex(w=1, r=_hi(dst), b=_hi(src))
    return bytes([0xF3]) + rex + bytes([0x0F, 0x2A, _modrm(0b11, int(dst), int(src))])


def encode_cvttss2si(dst: Reg, src: XmmReg) -> bytes:
    rex = _rex(w=1, r=_hi(dst), b=_hi(src))
    return bytes([0xF3]) + rex + bytes([0x0F, 0x2C, _modrm(0b11, int(dst), int(src))])


# ── Typed byte / dword memory loads & stores ──────────────────────────────────

def encode_movzx_rm8(dst: Reg, src: Mem) -> bytes:
    """MOVZX r64, byte [src] — zero-extend byte load."""
    return (_rex(w=1, r=_hi(dst), b=_hi(src.base))
            + bytes([0x0F, 0xB6]) + _encode_mem(int(dst), src))


def encode_mov_mr8(dst: Mem, src: Reg) -> bytes:
    """MOV byte [dst], src8 — byte store (low 8 bits of src)."""
    # Always emit REX so we can encode SPL/BPL/SIL/DIL (prevents AH/BH/CH/DH ambiguity).
    rex = bytes([0x40 | (_hi(src) << 2) | _hi(dst.base)])
    return rex + bytes([0x88]) + _encode_mem(int(src), dst)


def encode_movzx_rm16(dst: Reg, src: Mem) -> bytes:
    """MOVZX r64, word [src] — zero-extend 16-bit load."""
    return (_rex(w=1, r=_hi(dst), b=_hi(src.base))
            + bytes([0x0F, 0xB7]) + _encode_mem(int(dst), src))


def encode_mov_rm32(dst: Reg, src: Mem) -> bytes:
    """MOV r32, [src] — 32-bit load, zero-extends to r64."""
    return (_rex(r=_hi(dst), b=_hi(src.base))
            + bytes([0x8B]) + _encode_mem(int(dst), src))


def encode_mov_mr32(dst: Mem, src: Reg) -> bytes:
    """MOV [dst], r32 — 32-bit store."""
    return (_rex(r=_hi(src), b=_hi(dst.base))
            + bytes([0x89]) + _encode_mem(int(src), dst))


# ── TLS (Thread-Local Storage) access via FS segment ─────────────────────────

def encode_mov_tls_rm(dst: Reg, tpoff32: int = 0) -> bytes:
    """MOV dst, QWORD PTR FS:[tpoff32] — load TLS slot at offset tpoff32."""
    # 64 (FS prefix) REX.W 8B /r  with SIB = disp32-only addressing
    rex  = _rex(w=1, r=_hi(dst))          # always 1 byte (REX.W always set)
    modrm = _modrm(0b00, int(dst), 0b100)  # rm=100 → SIB follows
    sib   = _sib(1, 4, 5)                  # index=RSP(none), base=RBP(disp32)
    return bytes([0x64]) + rex + bytes([0x8B, modrm, sib]) + struct.pack("<i", tpoff32)


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
    rex = _rex(r=_hi(dst), b=_hi(src))
    rm  = _modrm(0b11, int(dst), int(src))
    return bytes([0x66]) + rex + bytes([0x0F, 0x38, 0x40, rm])


def encode_pshufd(dst: XmmReg, src: XmmReg, imm8: int) -> bytes:
    """PSHUFD dst, src, imm8 — shuffle packed 32-bit ints."""
    return _sse(0x66, 0x70, dst, src) + bytes([imm8 & 0xFF])


# ── SIMD memory loads / stores ────────────────────────────────────────────────

def encode_movdqu_rm(dst: XmmReg, src: Mem) -> bytes:
    """MOVDQU xmm, [src] — unaligned 128-bit load from memory."""
    rex = _rex(r=_hi(dst), b=_hi(src.base))
    return bytes([0xF3]) + rex + bytes([0x0F, 0x6F]) + _encode_mem(int(dst), src)


def encode_movdqu_mr(dst: Mem, src: XmmReg) -> bytes:
    """MOVDQU [dst], xmm — unaligned 128-bit store to memory."""
    rex = _rex(r=_hi(src), b=_hi(dst.base))
    return bytes([0xF3]) + rex + bytes([0x0F, 0x7F]) + _encode_mem(int(src), dst)


def encode_movdqa_rm(dst: XmmReg, src: Mem) -> bytes:
    """MOVDQA xmm, [src] — aligned 128-bit load from memory."""
    rex = _rex(r=_hi(dst), b=_hi(src.base))
    return bytes([0x66]) + rex + bytes([0x0F, 0x6F]) + _encode_mem(int(dst), src)


def encode_movdqa_mr(dst: Mem, src: XmmReg) -> bytes:
    """MOVDQA [dst], xmm — aligned 128-bit store to memory."""
    rex = _rex(r=_hi(src), b=_hi(dst.base))
    return bytes([0x66]) + rex + bytes([0x0F, 0x7F]) + _encode_mem(int(src), dst)


def encode_movaps_rm(dst: XmmReg, src: Mem) -> bytes:
    """MOVAPS xmm, [src] — aligned packed-float load."""
    rex = _rex(r=_hi(dst), b=_hi(src.base))
    return rex + bytes([0x0F, 0x28]) + _encode_mem(int(dst), src)


def encode_movaps_mr(dst: Mem, src: XmmReg) -> bytes:
    """MOVAPS [dst], xmm — aligned packed-float store."""
    rex = _rex(r=_hi(src), b=_hi(dst.base))
    return rex + bytes([0x0F, 0x29]) + _encode_mem(int(src), dst)
