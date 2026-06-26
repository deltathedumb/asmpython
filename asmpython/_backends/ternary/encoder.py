"""
Balanced ternary arithmetic and instruction encoding for the ternary ISA
(cpu.py / ternary_1 instruction set).

Encoding layout per instruction — matches cpu.py encode_instruction exactly:
  Word 0 (header): 16 trits
      trits  0-5  : opcode (6 trits)
      trits  6-7  : operand-count field = (n_operands - 4) in balanced ternary
      trits  8-15 : reftype (one trit per operand slot, -1=register, 0=immediate)
  Words 1..N: one 16-trit word per operand (register number or immediate value)

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


def encode_instruction(opcode: str, operands: list[tuple[int, bool]]) -> list[int]:
    """
    Encode one ternary instruction to a list of memory-cell integers.

    opcode:   6-char string of '0', '+', '-'
    operands: list of (value, is_register)
              is_register True  → reftype trit -1 (Trit.LO / register)
              is_register False → reftype trit  0 (Trit.MID / immediate)

    Returns a list of 16-trit integers: [header, op0, op1, ...].
    The header packs opcode (6) + count (2) + reftype (8) = 16 trits into one
    word, exactly matching cpu.py's encode_instruction and decode step.
    """
    assert len(opcode) == 6, f"opcode must be 6 chars: {opcode!r}"
    n = len(operands)

    op_trits    = [{"0": 0, "+": 1, "-": -1}[c] for c in opcode]   # 6 trits
    count_trits = bt_from_int(n - 4, 2)                              # 2 trits
    ref_trits   = [(-1 if is_reg else 0) for (_, is_reg) in operands]
    ref_trits  += [0] * (8 - len(ref_trits))                         # pad to 8

    header = bt_to_int(op_trits + count_trits + ref_trits)           # 16 trits

    words = [header]
    for value, _ in operands:
        words.append(bt_to_int(bt_from_int(value, 16)))
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
# Opcodes match cpu.py's @ternary_1.instruction decorators.

def halt():                         return _e("000000")
def nop():                          return _e("000-++")
def mov(dst: int, src: int):        return _e("00000-", R(dst), R(src))
def movi(dst: int, imm: int):       return _e("00++0+", R(dst), I(imm))
def load_i(addr: int, dst: int):    return _e("00000+", I(addr), R(dst))
def load_r(base: int, dst: int):    return _e("00000+", R(base), R(dst))
def store_i(addr: int, src: int):   return _e("0000-0", I(addr), R(src))
def store_r(base: int, src: int):   return _e("0000-0", R(base), R(src))
def add(src: int, dst: int):        return _e("0000--", R(src), R(dst))
def sub(src: int, dst: int):        return _e("0000-+", R(src), R(dst))
def mul(src: int, dst: int):        return _e("000+00", R(src), R(dst))
def div_(src: int, dst: int):       return _e("000+0-", R(src), R(dst))
def mod_(src: int, dst: int):       return _e("000+0+", R(src), R(dst))
def iand(src: int, dst: int):       return _e("0000+0", R(src), R(dst))
def ior(src: int, dst: int):        return _e("0000+-", R(src), R(dst))
def ixor(src: int, dst: int):       return _e("0000++", R(src), R(dst))
def shl(src: int, dst: int):        return _e("000++-", R(src), R(dst))
def shr(src: int, dst: int):        return _e("000+++", R(src), R(dst))
def neg(r: int):                    return _e("000+-0", R(r))
def inc(r: int):                    return _e("000+--", R(r))
def dec(r: int):                    return _e("000+-+", R(r))
def cmp_rr(a: int, b: int):         return _e("00+0-0", R(a), R(b))   # FLAGS = b-a
def cmp_ir(imm: int, b: int):       return _e("00+0-0", I(imm), R(b)) # FLAGS = b-imm
def jmp(addr: int):                 return _e("000-0-", I(addr))
def jmp_r(reg: int):                return _e("000-0-", R(reg))        # JMP register
def jz(addr: int):                  return _e("000-0+", I(addr))
def jnz(addr: int):                 return _e("000--0", I(addr))
def jl(addr: int):                  return _e("00+0-+", I(addr))       # jump if NEGATIVE
def jg(addr: int):                  return _e("00+0+0", I(addr))       # jump if ~(NEG|ZERO)
def jle(addr: int):                 return _e("00+0+-", I(addr))       # jump if NEG|ZERO
def jge(addr: int):                 return _e("00+0++", I(addr))       # jump if ~NEG
def push(r: int):                   return _e("000---", R(r))
def pop(r: int):                    return _e("000--+", R(r))
def pushi(imm: int):                return _e("00++00", I(imm))
def adjsp(delta: int):              return _e("00++0-", I(delta))
def call_abs(addr: int):            return _e("000-+0", I(addr))
def call_r(reg: int):               return _e("000-+0", R(reg))        # CALL register
def ret_():                         return _e("000-+-")
def out_r(port: int, src: int):     return _e("00++--", I(port), R(src))
def in_r(port: int, dst: int):      return _e("00++-+", I(port), R(dst))

# ── Disk I/O instructions (new opcodes added to cpu.py) ───────────────────────
def diskread(dst: int, sector: int):  return _e("0+000+", R(dst), R(sector))
def diskwrite(src: int, sector: int): return _e("0+00-0", R(src), R(sector))
def disksize(dst: int):               return _e("0+00-+", R(dst))


def words_to_bytes(words: list[int]) -> bytes:
    """Serialize memory-cell integers to a flat binary (4 bytes each, LE signed)."""
    return b"".join(struct.pack("<i", w) for w in words)
