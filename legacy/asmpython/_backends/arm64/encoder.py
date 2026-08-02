"""
AArch64 (ARM64) instruction encoder — Stage 1 of the ARM64 backend
(see roadmap.md's "ARM64 support" section for Stage 0/Stage 1 scope).

Every encode_* function returns exactly 4 bytes (AArch64's instruction
width is always fixed, unlike x86-64's variable-length encoding — no
prefix/REX/ModRM machinery is needed here). Caller concatenates the
returned bytes into a flat instruction stream. Branch target fixups are
the caller's job, mirroring x86_64/encoder.py's convention: encode with a
placeholder offset of 0, patch the 4-byte instruction word in place once
the real target is known.

Reference: ARM Architecture Reference Manual for A-profile architecture
(DDI 0487), specifically the A64 instruction set encoding tables. Every
encoding below was checked bit-for-bit against a real `aarch64-linux-gnu-as`
+ `objdump -d` round trip (see tests/backends/test_arm64_encoder.py) — this
is not "encoding by inference," each instruction's exact bit pattern was
independently confirmed against the real GNU binutils assembler's own
output before being hardcoded here.
"""
from __future__ import annotations

import struct
from enum import IntEnum


class Reg(IntEnum):
    """General-purpose registers X0-X30, plus the two special encodings
    (31) can mean depending on context: SP (stack pointer) in a handful of
    instructions (ADD/SUB immediate, loads/stores), or XZR (the hardwired
    zero register) everywhere else. Callers must pick the right constant
    (SP vs XZR) for the instruction they're encoding — the bit pattern is
    identical (11111) either way; only the architectural *meaning* differs
    per-instruction, exactly as the ARM ARM specifies."""
    X0 = 0;   X1 = 1;   X2 = 2;   X3 = 3
    X4 = 4;   X5 = 5;   X6 = 6;   X7 = 7
    X8 = 8;   X9 = 9;   X10 = 10; X11 = 11
    X12 = 12; X13 = 13; X14 = 14; X15 = 15
    X16 = 16; X17 = 17; X18 = 18; X19 = 19
    X20 = 20; X21 = 21; X22 = 22; X23 = 23
    X24 = 24; X25 = 25; X26 = 26; X27 = 27
    X28 = 28; X29 = 29  # X29 = frame pointer (FP) by AAPCS64 convention
    X30 = 30            # link register (LR) by AAPCS64 convention
    SP  = 31
    XZR = 31


class VReg(IntEnum):
    """SIMD/FP registers V0-V31. Only the double-precision (D<n>, low 64
    bits) and single-precision (S<n>, low 32 bits) forms are used by this
    backend — no NEON vector-lane instructions are emitted."""
    V0 = 0;   V1 = 1;   V2 = 2;   V3 = 3
    V4 = 4;   V5 = 5;   V6 = 6;   V7 = 7
    V8 = 8;   V9 = 9;   V10 = 10; V11 = 11
    V12 = 12; V13 = 13; V14 = 14; V15 = 15
    V16 = 16; V17 = 17; V18 = 18; V19 = 19
    V20 = 20; V21 = 21; V22 = 22; V23 = 23
    V24 = 24; V25 = 25; V26 = 26; V27 = 27
    V28 = 28; V29 = 29; V30 = 30; V31 = 31


class Cond(IntEnum):
    """Condition codes for B.cond / CSEL / CSET, AArch64's 4-bit encoding
    (ARM ARM C1.2.4). Distinct numbering from x86's CC -- not reusable."""
    EQ = 0x0; NE = 0x1; CS = 0x2; CC = 0x3
    MI = 0x4; PL = 0x5; VS = 0x6; VC = 0x7
    HI = 0x8; LS = 0x9; GE = 0xA; LT = 0xB
    GT = 0xC; LE = 0xD; AL = 0xE
    HS = 0x2  # alias: CS (unsigned >=)
    LO = 0x3  # alias: CC (unsigned <)


# AAPCS64 (the Linux/standard ARM64 calling convention -- also what
# Windows ARM64's ABI uses for the integer/FP argument registers, unlike
# x86-64 where SysV and Win64 genuinely diverge on argument register
# choice; the platforms differ elsewhere -- red zone, varargs -- not here).
ARG_REGS = (Reg.X0, Reg.X1, Reg.X2, Reg.X3, Reg.X4, Reg.X5, Reg.X6, Reg.X7)
RET_REG = Reg.X0
FP_ARG_REGS = (VReg.V0, VReg.V1, VReg.V2, VReg.V3, VReg.V4, VReg.V5, VReg.V6, VReg.V7)
RET_FP = VReg.V0

# Callee-saved per AAPCS64 6.1.1 (Table): X19-X28, plus FP(X29)/LR(X30)
# which this backend always saves/restores explicitly in the prologue
# rather than treating as a general allocatable pool.
CALLEE_SAVED = (Reg.X19, Reg.X20, Reg.X21, Reg.X22, Reg.X23,
                Reg.X24, Reg.X25, Reg.X26, Reg.X27, Reg.X28)
# D8-D15's low 64 bits are callee-saved (the upper 64 bits of V8-V15 are
# NOT, since this backend never touches them, that distinction is moot).
CALLEE_SAVED_FP = (VReg.V8, VReg.V9, VReg.V10, VReg.V11,
                    VReg.V12, VReg.V13, VReg.V14, VReg.V15)


def _u32(word: int) -> bytes:
    return struct.pack("<I", word & 0xFFFFFFFF)


def _check_imm(value: int, bits: int, *, signed: bool = False, name: str = "immediate") -> int:
    lo = -(1 << (bits - 1)) if signed else 0
    hi = (1 << (bits - 1)) - 1 if signed else (1 << bits) - 1
    if not (lo <= value <= hi):
        raise ValueError(f"{name} {value} out of range for {bits}-bit field ({lo}..{hi})")
    return value & ((1 << bits) - 1)


# ── Data processing: register-register ───────────────────────────────────────

def _dp_reg_3src(base: int, rd: Reg, rn: Reg, rm: Reg) -> bytes:
    """Common shape for ADD/SUB/AND/ORR/EOR (shifted-register, shift=0)
    and MOV-via-ORR's Rd/Rn/Rm slots: bits [4:0]=Rd [9:5]=Rn [20:16]=Rm."""
    return _u32(base | (int(rm) << 16) | (int(rn) << 5) | int(rd))


def add_reg(rd: Reg, rn: Reg, rm: Reg) -> bytes:
    """ADD Xd, Xn, Xm (64-bit, no shift). Encoding: sf=1 op=0 S=0 shift=00."""
    return _dp_reg_3src(0x8B000000, rd, rn, rm)


def sub_reg(rd: Reg, rn: Reg, rm: Reg) -> bytes:
    """SUB Xd, Xn, Xm (64-bit, no shift)."""
    return _dp_reg_3src(0xCB000000, rd, rn, rm)


def subs_reg(rd: Reg, rn: Reg, rm: Reg) -> bytes:
    """SUBS Xd, Xn, Xm -- SUB that also sets NZCV (used for CMP := SUBS
    with Rd=XZR, and for ordinary subtraction that also needs flags)."""
    return _dp_reg_3src(0xEB000000, rd, rn, rm)


def cmp_reg(rn: Reg, rm: Reg) -> bytes:
    """CMP Xn, Xm := SUBS XZR, Xn, Xm."""
    return subs_reg(Reg.XZR, rn, rm)


def and_reg(rd: Reg, rn: Reg, rm: Reg) -> bytes:
    return _dp_reg_3src(0x8A000000, rd, rn, rm)


def orr_reg(rd: Reg, rn: Reg, rm: Reg) -> bytes:
    return _dp_reg_3src(0xAA000000, rd, rn, rm)


def eor_reg(rd: Reg, rn: Reg, rm: Reg) -> bytes:
    return _dp_reg_3src(0xCA000000, rd, rn, rm)


def mul(rd: Reg, rn: Reg, rm: Reg) -> bytes:
    """MUL Xd, Xn, Xm := MADD Xd, Xn, Xm, XZR."""
    return _u32(0x9B007C00 | (int(rm) << 16) | (int(rn) << 5) | int(rd))


def sdiv(rd: Reg, rn: Reg, rm: Reg) -> bytes:
    return _u32(0x9AC00C00 | (int(rm) << 16) | (int(rn) << 5) | int(rd))


def udiv(rd: Reg, rn: Reg, rm: Reg) -> bytes:
    return _u32(0x9AC00800 | (int(rm) << 16) | (int(rn) << 5) | int(rd))


def mov_reg(rd: Reg, rm: Reg) -> bytes:
    """MOV Xd, Xm := ORR Xd, XZR, Xm (the canonical AArch64 register-move
    idiom -- there is no dedicated MOV-register opcode)."""
    return orr_reg(rd, Reg.XZR, rm)


def neg_reg(rd: Reg, rm: Reg) -> bytes:
    """NEG Xd, Xm := SUB Xd, XZR, Xm."""
    return sub_reg(rd, Reg.XZR, rm)


# ── Data processing: immediate ────────────────────────────────────────────────

def _dp_imm12(base: int, rd: Reg, rn: Reg, imm: int) -> bytes:
    imm12 = _check_imm(imm, 12, name="add/sub immediate")
    return _u32(base | (imm12 << 10) | (int(rn) << 5) | int(rd))


def add_imm(rd: Reg, rn: Reg, imm: int) -> bytes:
    """ADD Xd, Xn, #imm (imm: 0..4095, unshifted form only)."""
    return _dp_imm12(0x91000000, rd, rn, imm)


def sub_imm(rd: Reg, rn: Reg, imm: int) -> bytes:
    """SUB Xd, Xn, #imm (imm: 0..4095, unshifted form only)."""
    return _dp_imm12(0xD1000000, rd, rn, imm)


def subs_imm(rd: Reg, rn: Reg, imm: int) -> bytes:
    return _dp_imm12(0xF1000000, rd, rn, imm)


def cmp_imm(rn: Reg, imm: int) -> bytes:
    """CMP Xn, #imm := SUBS XZR, Xn, #imm."""
    return subs_imm(Reg.XZR, rn, imm)


def movz(rd: Reg, imm16: int, *, shift: int = 0) -> bytes:
    """MOVZ Xd, #imm16, LSL #shift (shift in {0,16,32,48})."""
    if shift not in (0, 16, 32, 48):
        raise ValueError(f"movz shift must be 0/16/32/48, got {shift}")
    imm = _check_imm(imm16, 16, name="movz immediate")
    hw = shift // 16
    return _u32(0xD2800000 | (hw << 21) | (imm << 5) | int(rd))


def movk(rd: Reg, imm16: int, *, shift: int = 0) -> bytes:
    """MOVK Xd, #imm16, LSL #shift -- merges into Xd without clearing
    other 16-bit lanes, unlike MOVZ."""
    if shift not in (0, 16, 32, 48):
        raise ValueError(f"movk shift must be 0/16/32/48, got {shift}")
    imm = _check_imm(imm16, 16, name="movk immediate")
    hw = shift // 16
    return _u32(0xF2800000 | (hw << 21) | (imm << 5) | int(rd))


def mov_imm64(rd: Reg, value: int) -> list[bytes]:
    """Materialize an arbitrary 64-bit constant via MOVZ + up to 3 MOVK
    instructions (only the non-zero 16-bit lanes need a MOVK; an all-zero
    value is a single MOVZ). Returns a list of 1-4 encoded instructions in
    emission order -- the caller concatenates them."""
    value &= 0xFFFFFFFFFFFFFFFF
    lanes = [(value >> s) & 0xFFFF for s in (0, 16, 32, 48)]
    if all(l == 0 for l in lanes):
        return [movz(rd, 0, shift=0)]
    out: list[bytes] = []
    first = True
    for shift, lane in zip((0, 16, 32, 48), lanes):
        if lane == 0 and not first:
            continue
        if first:
            out.append(movz(rd, lane, shift=shift))
            first = False
        else:
            out.append(movk(rd, lane, shift=shift))
    return out


# ── Loads / stores (unsigned immediate offset form, 64-bit) ──────────────────

def _ldst_uimm(base_op: int, size_shift: int, rt: "Reg | VReg", rn: Reg, offset: int) -> bytes:
    """LDR/STR Xt, [Xn, #offset] -- unsigned scaled 12-bit immediate; offset
    must be a multiple of the access size (8 for X-regs/D-regs, 4 for
    W-regs/S-regs) and encodes as offset/size in the instruction."""
    size = 1 << size_shift
    if offset % size != 0:
        raise ValueError(f"load/store offset {offset} not aligned to {size}")
    imm12 = _check_imm(offset // size, 12, name="load/store offset")
    return _u32(base_op | (imm12 << 10) | (int(rn) << 5) | int(rt))


def ldr_imm(rt: Reg, rn: Reg, offset: int = 0) -> bytes:
    """LDR Xt, [Xn, #offset] (64-bit GP)."""
    return _ldst_uimm(0xF9400000, 3, rt, rn, offset)


def str_imm(rt: Reg, rn: Reg, offset: int = 0) -> bytes:
    """STR Xt, [Xn, #offset] (64-bit GP)."""
    return _ldst_uimm(0xF9000000, 3, rt, rn, offset)


def ldr_imm_w(rt: Reg, rn: Reg, offset: int = 0) -> bytes:
    """LDR Wt, [Xn, #offset] (32-bit GP, zero-extends into Xt)."""
    return _ldst_uimm(0xB9400000, 2, rt, rn, offset)


def str_imm_w(rt: Reg, rn: Reg, offset: int = 0) -> bytes:
    """STR Wt, [Xn, #offset] (32-bit GP)."""
    return _ldst_uimm(0xB9000000, 2, rt, rn, offset)


def ldr_imm_d(vt: VReg, rn: Reg, offset: int = 0) -> bytes:
    """LDR Dt, [Xn, #offset] (64-bit FP/double)."""
    return _ldst_uimm(0xFD400000, 3, vt, rn, offset)


def str_imm_d(vt: VReg, rn: Reg, offset: int = 0) -> bytes:
    """STR Dt, [Xn, #offset] (64-bit FP/double)."""
    return _ldst_uimm(0xFD000000, 3, vt, rn, offset)


def ldp(rt1: Reg, rt2: Reg, rn: Reg, offset: int = 0, *, writeback: str = "none") -> bytes:
    """LDP Xt1, Xt2, [Xn, #offset]{!} -- load pair, offset is a signed
    multiple of 8 in range -512..504. `writeback`: "none" (offset form,
    used for reading saved callee-saved pairs), "pre" (pre-indexed,
    Xn updated before the access -- used to grow the stack while loading
    is never needed by this backend, only "post" is), or "post"
    (post-indexed, used for the epilogue's stack-deallocating pop)."""
    imm7 = _check_imm(offset // 8, 7, signed=True, name="ldp offset")
    if offset % 8 != 0:
        raise ValueError(f"ldp offset {offset} not 8-aligned")
    opc = {"none": 0b10, "post": 0b01, "pre": 0b11}[writeback]
    word = (0b10 << 30) | (0b101 << 27) | (opc << 23) | (1 << 22)
    word |= (imm7 << 15) | (int(rt2) << 10) | (int(rn) << 5) | int(rt1)
    return _u32(word)


def stp(rt1: Reg, rt2: Reg, rn: Reg, offset: int = 0, *, writeback: str = "none") -> bytes:
    """STP Xt1, Xt2, [Xn, #offset]{!} -- store pair. `writeback="pre"` with
    a negative offset is this backend's standard "push a frame" idiom:
    STP X29, X30, [SP, #-N]!  (allocates N bytes AND stores in one insn)."""
    imm7 = _check_imm(offset // 8, 7, signed=True, name="stp offset")
    if offset % 8 != 0:
        raise ValueError(f"stp offset {offset} not 8-aligned")
    opc = {"none": 0b10, "post": 0b01, "pre": 0b11}[writeback]
    word = (0b10 << 30) | (0b101 << 27) | (opc << 23) | (0 << 22)
    word |= (imm7 << 15) | (int(rt2) << 10) | (int(rn) << 5) | int(rt1)
    return _u32(word)


# ── Branches ──────────────────────────────────────────────────────────────────

def b(offset_words: int) -> bytes:
    """B <label> -- unconditional branch, PC-relative in units of 4-byte
    instructions (the caller passes (target_byte_offset - here) // 4)."""
    imm26 = _check_imm(offset_words, 26, signed=True, name="b offset")
    return _u32(0x14000000 | imm26)


def bl(offset_words: int) -> bytes:
    """BL <label> -- branch with link (call), same PC-relative encoding as B."""
    imm26 = _check_imm(offset_words, 26, signed=True, name="bl offset")
    return _u32(0x94000000 | imm26)


def blr(rn: Reg) -> bytes:
    """BLR Xn -- indirect call through a register."""
    return _u32(0xD63F0000 | (int(rn) << 5))


def br(rn: Reg) -> bytes:
    """BR Xn -- indirect unconditional branch through a register."""
    return _u32(0xD61F0000 | (int(rn) << 5))


def ret(rn: Reg = Reg.X30) -> bytes:
    """RET {Xn} -- return via link register (X30/LR) by default."""
    return _u32(0xD65F0000 | (int(rn) << 5))


def b_cond(cond: Cond, offset_words: int) -> bytes:
    """B.cond <label> -- PC-relative conditional branch, 19-bit immediate."""
    imm19 = _check_imm(offset_words, 19, signed=True, name="b.cond offset")
    return _u32(0x54000000 | (imm19 << 5) | (int(cond) & 0xF))


def cbz(rt: Reg, offset_words: int) -> bytes:
    """CBZ Xt, <label> -- branch if Xt == 0."""
    imm19 = _check_imm(offset_words, 19, signed=True, name="cbz offset")
    return _u32(0xB4000000 | (imm19 << 5) | int(rt))


def cbnz(rt: Reg, offset_words: int) -> bytes:
    """CBNZ Xt, <label> -- branch if Xt != 0."""
    imm19 = _check_imm(offset_words, 19, signed=True, name="cbnz offset")
    return _u32(0xB5000000 | (imm19 << 5) | int(rt))


# ── Address materialization ───────────────────────────────────────────────────

def adrp(rd: Reg, page_offset_words: int) -> bytes:
    """ADRP Xd, <label> -- load the 4KB-page address of a PC-relative
    symbol into Xd (bits [32:12] of the offset, i.e. the offset is already
    in units of 4096-byte pages relative to this instruction's own page).
    Paired with `add_imm(rd, rd, :lo12:label)` to form the full address,
    exactly as this backend's own Stage-0 probe program does by hand."""
    imm = _check_imm(page_offset_words, 21, signed=True, name="adrp page offset")
    immlo = imm & 0x3
    immhi = (imm >> 2) & 0x7FFFF
    return _u32(0x90000000 | (immlo << 29) | (immhi << 5) | int(rd))


def adr(rd: Reg, offset_bytes: int) -> bytes:
    """ADR Xd, <label> -- byte-precise PC-relative address (±1MB range),
    for a symbol known to live within range without needing a page split."""
    imm = _check_imm(offset_bytes, 21, signed=True, name="adr offset")
    immlo = imm & 0x3
    immhi = (imm >> 2) & 0x7FFFF
    return _u32(0x10000000 | (immlo << 29) | (immhi << 5) | int(rd))


# ── Conditional select / set ──────────────────────────────────────────────────

def csel(rd: Reg, rn: Reg, rm: Reg, cond: Cond) -> bytes:
    """CSEL Xd, Xn, Xm, cond -- Xd = cond ? Xn : Xm."""
    return _u32(0x9A800000 | (int(rm) << 16) | (int(cond) << 12) | (int(rn) << 5) | int(rd))


def cset(rd: Reg, cond: Cond) -> bytes:
    """CSET Xd, cond := CSINC Xd, XZR, XZR, invert(cond) -- Xd = cond ? 1 : 0."""
    inv = int(cond) ^ 1  # AArch64 condition codes invert via the low bit
    return _u32(0x9A9F07E0 | (inv << 12) | int(rd))


# ── Misc ──────────────────────────────────────────────────────────────────────

def nop() -> bytes:
    return _u32(0xD503201F)


def svc(imm16: int = 0) -> bytes:
    """SVC #imm16 -- supervisor call (syscall trap on Linux)."""
    imm = _check_imm(imm16, 16, name="svc immediate")
    return _u32(0xD4000001 | (imm << 5))


def brk(imm16: int = 0) -> bytes:
    """BRK #imm16 -- breakpoint trap."""
    imm = _check_imm(imm16, 16, name="brk immediate")
    return _u32(0xD4200000 | (imm << 5))


# ── Scalar floating-point (double precision, D<n>) ────────────────────────────
# Only the double-precision forms are emitted -- asmpython's runtime float
# type is always a 64-bit IEEE-754 double (mirrors the x86-64 backend's own
# XMM usage: no single-precision path exists there either).

def _fp_dp2(base: int, vd: VReg, vn: VReg, vm: VReg) -> bytes:
    return _u32(base | (int(vm) << 16) | (int(vn) << 5) | int(vd))


def fadd(vd: VReg, vn: VReg, vm: VReg) -> bytes:
    """FADD Dd, Dn, Dm."""
    return _fp_dp2(0x1E602800, vd, vn, vm)


def fsub(vd: VReg, vn: VReg, vm: VReg) -> bytes:
    """FSUB Dd, Dn, Dm."""
    return _fp_dp2(0x1E603800, vd, vn, vm)


def fmul(vd: VReg, vn: VReg, vm: VReg) -> bytes:
    """FMUL Dd, Dn, Dm."""
    return _fp_dp2(0x1E600800, vd, vn, vm)


def fdiv(vd: VReg, vn: VReg, vm: VReg) -> bytes:
    """FDIV Dd, Dn, Dm."""
    return _fp_dp2(0x1E601800, vd, vn, vm)


def fneg(vd: VReg, vn: VReg) -> bytes:
    """FNEG Dd, Dn."""
    return _u32(0x1E614000 | (int(vn) << 5) | int(vd))


def fabs_(vd: VReg, vn: VReg) -> bytes:
    """FABS Dd, Dn (trailing underscore: `abs` shadows a builtin)."""
    return _u32(0x1E60C000 | (int(vn) << 5) | int(vd))


def fsqrt(vd: VReg, vn: VReg) -> bytes:
    """FSQRT Dd, Dn."""
    return _u32(0x1E61C000 | (int(vn) << 5) | int(vd))


def fmov_reg(vd: VReg, vn: VReg) -> bytes:
    """FMOV Dd, Dn (register-to-register float move)."""
    return _u32(0x1E604000 | (int(vn) << 5) | int(vd))


def fmov_from_gp(vd: VReg, rn: Reg) -> bytes:
    """FMOV Dd, Xn -- move the raw 64 bits of a GP register into a D-reg
    (bit-reinterpret, not a numeric conversion -- mirrors x86-64's
    MOVQ xmm, r64 usage for bitcast-style float/int reinterpretation)."""
    return _u32(0x9E670000 | (int(rn) << 5) | int(vd))


def fmov_to_gp(rd: Reg, vn: VReg) -> bytes:
    """FMOV Xd, Dn -- move the raw 64 bits of a D-reg into a GP register."""
    return _u32(0x9E660000 | (int(vn) << 5) | int(rd))


def fcmp(vn: VReg, vm: VReg) -> bytes:
    """FCMP Dn, Dm -- sets NZCV for a subsequent B.cond/CSEL/CSET."""
    return _u32(0x1E602000 | (int(vm) << 16) | (int(vn) << 5))


def scvtf(vd: VReg, rn: Reg) -> bytes:
    """SCVTF Dd, Xn -- signed 64-bit integer to double conversion."""
    return _u32(0x9E620000 | (int(rn) << 5) | int(vd))


def fcvtzs(rd: Reg, vn: VReg) -> bytes:
    """FCVTZS Xd, Dn -- double to signed 64-bit integer, round-toward-zero
    (Python's `int(float)` truncation semantics)."""
    return _u32(0x9E780000 | (int(vn) << 5) | int(rd))


# ── Shifted-immediate ADD/SUB (for stack frames/offsets beyond 12 bits) ──────

def add_imm_lsl12(rd: Reg, rn: Reg, imm12: int) -> bytes:
    """ADD Xd, Xn, #imm12, LSL #12 -- adds imm12 * 4096. Combined with
    add_imm's own unshifted form, expresses any 24-bit-aligned-enough
    immediate as two instructions (mirrors how a stack frame larger than
    4095 bytes -- rare, but not impossible for a function with many
    spills -- must be split across two ADD/SUB immediates, since AArch64
    has no single instruction encoding an arbitrary 24-bit add)."""
    imm = _check_imm(imm12, 12, name="add lsl12 immediate")
    return _u32(0x91400000 | (imm << 10) | (int(rn) << 5) | int(rd))


def sub_imm_lsl12(rd: Reg, rn: Reg, imm12: int) -> bytes:
    """SUB Xd, Xn, #imm12, LSL #12."""
    imm = _check_imm(imm12, 12, name="sub lsl12 immediate")
    return _u32(0xD1400000 | (imm << 10) | (int(rn) << 5) | int(rd))
