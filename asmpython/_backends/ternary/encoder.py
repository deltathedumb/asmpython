"""
Balanced ternary arithmetic and instruction encoding for the ternary ISA
(cpu.py / ternary_1 instruction set).

Encoding layout per instruction:
  Word 0 (header):  opcode trits 0-5 + operand-count field trits 6-7
  Word 1 (reftype): one trit per operand: 0 = immediate, -1 = register ref
  Words 2+:         two words per operand (lo 8 trits, hi 8 trits)

Every integer here is the mathematical value of its balanced-ternary
representation, matching what int(Trite) returns in cpu.py.
"""

from __future__ import annotations
import struct


def bt_from_int(value: int, n: int = 8) -> list[int]:
    """Python int → balanced ternary coefficient list, LSB first, n trits."""
    trits: list[int] = []
    v = value
    for _ in range(n):
        r = v % 3
        v //= 3
        if r == 2:
            trits.append(-1)
            v += 1
        else:
            trits.append(r)   # 0 or 1
    return trits


def bt_to_int(trits: list[int]) -> int:
    """Balanced ternary coefficient list (LSB first) → Python int."""
    acc = 0
    for i, t in enumerate(trits):
        acc += t * (3 ** i)
    return acc


def encode_operand(value: int) -> tuple[int, int]:
    """Encode a 16-trit value as (lo_cell, hi_cell)."""
    t16 = bt_from_int(value, 16)
    return bt_to_int(t16[:8]), bt_to_int(t16[8:])


def encode_instruction(opcode: str, operands: list[tuple[int, bool]]) -> list[int]:
    """
    Encode one ternary instruction to a list of memory-cell integers.

    opcode:   6-char string of '0', '+', '-'
    operands: list of (value, is_register)
              is_register True  → reftype trit -1 (Trit.LO in cpu.py)
              is_register False → reftype trit  0 (Trit.MID / immediate)
    """
    assert len(opcode) == 6, f"opcode must be 6 chars: {opcode!r}"
    n = len(operands)

    op_trits = [{"0": 0, "+": 1, "-": -1}[c] for c in opcode]
    count_trits = bt_from_int(n - 4, 2)   # 2-trit field; range -4..+4
    header = bt_to_int(op_trits + count_trits)

    rt = [(-1 if is_reg else 0) for (_, is_reg) in operands]
    rt += [0] * (8 - len(rt))             # pad reftype word to 8 trits
    reftype = bt_to_int(rt)

    words = [header, reftype]
    for value, _ in operands:
        lo, hi = encode_operand(value)
        words += [lo, hi]
    return words


# ── Operand builders ──────────────────────────────────────────────────────────

def I(v: int) -> tuple[int, bool]:   # noqa: E741
    """Immediate operand."""
    return (v, False)

def R(r: int) -> tuple[int, bool]:
    """Register operand (r = 0..15)."""
    return (r, True)

def _e(opcode: str, *ops: tuple[int, bool]) -> list[int]:
    return encode_instruction(opcode, list(ops))


# ── Named instruction builders ────────────────────────────────────────────────
# Opcodes from cpu.py's @ternary_1.instruction decorators.

def halt():                       return _e("000000")
def nop():                        return _e("000-++")
def mov(dst: int, src: int):      return _e("00000-", R(dst), R(src))    # dst = src
def movi(dst: int, imm: int):     return _e("00++0+", R(dst), I(imm))    # dst = imm
def load_i(addr: int, dst: int):  return _e("00000+", I(addr), R(dst))   # dst = mem[imm]
def load_r(base: int, dst: int):  return _e("00000+", R(base), R(dst))   # dst = mem[r]
def store_i(addr: int, src: int): return _e("0000-0", I(addr), R(src))   # mem[imm] = src
def store_r(base: int, src: int): return _e("0000-0", R(base), R(src))   # mem[r] = src
def add(src: int, dst: int):      return _e("0000--", R(src), R(dst))    # dst += src
def sub(src: int, dst: int):      return _e("0000-+", R(src), R(dst))    # dst -= src
def mul(src: int, dst: int):      return _e("000+00", R(src), R(dst))    # dst *= src
def div(src: int, dst: int):      return _e("000+0-", R(src), R(dst))    # dst //= src
def mod(src: int, dst: int):      return _e("000+0+", R(src), R(dst))    # dst %= src
def iand(src: int, dst: int):     return _e("0000+0", R(src), R(dst))
def ior(src: int, dst: int):      return _e("0000+-", R(src), R(dst))
def ixor(src: int, dst: int):     return _e("0000++", R(src), R(dst))
def shl(src: int, dst: int):      return _e("000++-", R(src), R(dst))    # dst <<= src
def shr(src: int, dst: int):      return _e("000+++", R(src), R(dst))    # dst >>= src
def neg(r: int):                  return _e("000+-0", R(r))
def cmp_rr(a: int, b: int):       return _e("00+0-0", R(a), R(b))       # FLAGS = b-a
def cmp_ir(imm: int, b: int):     return _e("00+0-0", I(imm), R(b))     # FLAGS = b-imm
def jmp(addr: int):               return _e("000-0-", I(addr))
def jz(addr: int):                return _e("000-0+", I(addr))
def jnz(addr: int):               return _e("000--0", I(addr))
def jl(addr: int):                return _e("00+0-+", I(addr))           # jump if NEGATIVE
def jg(addr: int):                return _e("00+0+0", I(addr))           # jump if ~(NEG|ZERO)
def jle(addr: int):               return _e("00+0+-", I(addr))           # jump if NEG|ZERO
def jge(addr: int):               return _e("00+0++", I(addr))           # jump if ~NEG
def push(r: int):                 return _e("000---", R(r))
def pop(r: int):                  return _e("000--+", R(r))
def pushi(imm: int):              return _e("00++00", I(imm))
def adjsp(delta: int):            return _e("00++0-", I(delta))
def call_abs(addr: int):          return _e("000-+0", I(addr))
def ret_():                       return _e("000-+-")
def out_r(port: int, src: int):   return _e("00++--", I(port), R(src))
def in_r(port: int, dst: int):    return _e("00++-+", I(port), R(dst))


def words_to_bytes(words: list[int]) -> bytes:
    """Serialize memory-cell integers to a flat binary (4 bytes each, LE signed)."""
    return b"".join(struct.pack("<i", w) for w in words)
