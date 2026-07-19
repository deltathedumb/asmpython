"""Stack-memory access extensions for the in-progress AArch64 backend.

The base encoder intentionally exposes one-instruction helpers. Its ordinary
``ldr_imm``/``str_imm`` functions use A64's unsigned, scaled offset form, which
cannot encode the negative X29-relative offsets produced by ``regalloc.py``.
This module installs codegen-facing wrappers which:

* use LDUR/STUR's signed, unscaled 9-bit form for nearby negative stack slots;
* materialize an address through IP0/X16 for larger negative offsets; and
* expand oversized frame-record pre/post-index operations into an explicit
  SP adjustment plus an offset-zero STP/LDP.

Only the names imported by ``codegen.py`` are wrapped. The underlying encoder
primitives remain available and continue to return exactly one instruction.
"""
from __future__ import annotations

from . import encoder as _encoder


# X16/IP0 is reserved by AAPCS64 for intra-procedure-call scratch use and is
# deliberately absent from regalloc.py's allocation pool. It is safe for the
# short, branch-free address-materialization sequences emitted here.
_ADDR_SCRATCH = _encoder.Reg.X16

_ORIG_LDR_IMM = _encoder.ldr_imm
_ORIG_STR_IMM = _encoder.str_imm
_ORIG_LDR_IMM_D = _encoder.ldr_imm_d
_ORIG_STR_IMM_D = _encoder.str_imm_d
_ORIG_LDP = _encoder.ldp
_ORIG_STP = _encoder.stp


def ldur(rt: _encoder.Reg, rn: _encoder.Reg, offset: int = 0) -> bytes:
    """Encode ``LDUR Xt, [Xn, #simm9]``."""
    imm9 = _encoder._check_imm(offset, 9, signed=True, name="ldur offset")
    return _encoder._u32(
        0xF8400000 | (imm9 << 12) | (int(rn) << 5) | int(rt)
    )


def stur(rt: _encoder.Reg, rn: _encoder.Reg, offset: int = 0) -> bytes:
    """Encode ``STUR Xt, [Xn, #simm9]``."""
    imm9 = _encoder._check_imm(offset, 9, signed=True, name="stur offset")
    return _encoder._u32(
        0xF8000000 | (imm9 << 12) | (int(rn) << 5) | int(rt)
    )


def ldur_d(vt: _encoder.VReg, rn: _encoder.Reg, offset: int = 0) -> bytes:
    """Encode ``LDUR Dt, [Xn, #simm9]``."""
    imm9 = _encoder._check_imm(offset, 9, signed=True, name="ldur d offset")
    return _encoder._u32(
        0xFC400000 | (imm9 << 12) | (int(rn) << 5) | int(vt)
    )


def stur_d(vt: _encoder.VReg, rn: _encoder.Reg, offset: int = 0) -> bytes:
    """Encode ``STUR Dt, [Xn, #simm9]``."""
    imm9 = _encoder._check_imm(offset, 9, signed=True, name="stur d offset")
    return _encoder._u32(
        0xFC000000 | (imm9 << 12) | (int(rn) << 5) | int(vt)
    )


def _adjust_reg(dst: _encoder.Reg, src: _encoder.Reg, offset: int) -> bytes:
    """Materialize ``dst = src + offset`` for signed offsets below 2**24."""
    magnitude = abs(offset)
    if magnitude >= (1 << 24):
        raise ValueError(
            f"stack offset {offset} exceeds ARM64 backend's 24-bit range"
        )

    high = (magnitude >> 12) & 0xFFF
    low = magnitude & 0xFFF
    add = offset >= 0
    out = bytearray()
    current = src

    if high:
        fn = _encoder.add_imm_lsl12 if add else _encoder.sub_imm_lsl12
        out.extend(fn(dst, current, high))
        current = dst
    if low or not high:
        fn = _encoder.add_imm if add else _encoder.sub_imm
        out.extend(fn(dst, current, low))
    elif current != dst:
        out.extend(_encoder.mov_reg(dst, current))
    return bytes(out)


def _load_gp(rt: _encoder.Reg, rn: _encoder.Reg, offset: int = 0) -> bytes:
    if offset >= 0:
        return _ORIG_LDR_IMM(rt, rn, offset)
    if offset >= -256:
        return ldur(rt, rn, offset)
    return _adjust_reg(_ADDR_SCRATCH, rn, offset) + _ORIG_LDR_IMM(
        rt, _ADDR_SCRATCH, 0
    )


def _store_gp(rt: _encoder.Reg, rn: _encoder.Reg, offset: int = 0) -> bytes:
    if offset >= 0:
        return _ORIG_STR_IMM(rt, rn, offset)
    if offset >= -256:
        return stur(rt, rn, offset)
    return _adjust_reg(_ADDR_SCRATCH, rn, offset) + _ORIG_STR_IMM(
        rt, _ADDR_SCRATCH, 0
    )


def _load_fp(vt: _encoder.VReg, rn: _encoder.Reg, offset: int = 0) -> bytes:
    if offset >= 0:
        return _ORIG_LDR_IMM_D(vt, rn, offset)
    if offset >= -256:
        return ldur_d(vt, rn, offset)
    return _adjust_reg(_ADDR_SCRATCH, rn, offset) + _ORIG_LDR_IMM_D(
        vt, _ADDR_SCRATCH, 0
    )


def _store_fp(vt: _encoder.VReg, rn: _encoder.Reg, offset: int = 0) -> bytes:
    if offset >= 0:
        return _ORIG_STR_IMM_D(vt, rn, offset)
    if offset >= -256:
        return stur_d(vt, rn, offset)
    return _adjust_reg(_ADDR_SCRATCH, rn, offset) + _ORIG_STR_IMM_D(
        vt, _ADDR_SCRATCH, 0
    )


def _stp(
    rt1: _encoder.Reg,
    rt2: _encoder.Reg,
    rn: _encoder.Reg,
    offset: int = 0,
    *,
    writeback: str = "none",
) -> bytes:
    if offset % 8:
        return _ORIG_STP(rt1, rt2, rn, offset, writeback=writeback)
    scaled = offset // 8
    if -64 <= scaled <= 63:
        return _ORIG_STP(rt1, rt2, rn, offset, writeback=writeback)
    if writeback == "pre" and rn == _encoder.Reg.SP and offset < 0:
        return _adjust_reg(_encoder.Reg.SP, _encoder.Reg.SP, offset) + _ORIG_STP(
            rt1, rt2, _encoder.Reg.SP, 0
        )
    return _ORIG_STP(rt1, rt2, rn, offset, writeback=writeback)


def _ldp(
    rt1: _encoder.Reg,
    rt2: _encoder.Reg,
    rn: _encoder.Reg,
    offset: int = 0,
    *,
    writeback: str = "none",
) -> bytes:
    if offset % 8:
        return _ORIG_LDP(rt1, rt2, rn, offset, writeback=writeback)
    scaled = offset // 8
    if -64 <= scaled <= 63:
        return _ORIG_LDP(rt1, rt2, rn, offset, writeback=writeback)
    if writeback == "post" and rn == _encoder.Reg.SP and offset > 0:
        return _ORIG_LDP(rt1, rt2, _encoder.Reg.SP, 0) + _adjust_reg(
            _encoder.Reg.SP, _encoder.Reg.SP, offset
        )
    return _ORIG_LDP(rt1, rt2, rn, offset, writeback=writeback)


def install() -> None:
    """Install the codegen-facing wrappers exactly once."""
    if getattr(_encoder, "_stack_access_extensions_installed", False):
        return

    _encoder.ldur = ldur
    _encoder.stur = stur
    _encoder.ldur_d = ldur_d
    _encoder.stur_d = stur_d
    _encoder.ldr_imm = _load_gp
    _encoder.str_imm = _store_gp
    _encoder.ldr_imm_d = _load_fp
    _encoder.str_imm_d = _store_fp
    _encoder.stp = _stp
    _encoder.ldp = _ldp
    _encoder._stack_access_extensions_installed = True
