"""Bit-level primitives.

Portable Python implementations that a backend may replace with something it
can do in one instruction. Every function here is written twice: once in this
file, in the compiled-language subset, so it works on any backend at all; and
once per architecture where the machine has an instruction for it (x86's
POPCNT/LZCNT/TZCNT/BSWAP, AArch64's CNT/CLZ/RBIT/REV). ``lllib`` picks the
second when it exists -- see ``lllib/__init__.py``.

Width matters, so every function takes one. A 32-bit ``clz`` of 1 is 31; a
64-bit ``clz`` of 1 is 63. Passing the width rather than inferring it from the
value is the only way to get that right, and it is what the hardware
instructions take too.

Values are treated as unsigned in ``width`` bits. Anything wider is masked
down, so callers never have to think about Python's arbitrary-precision ints
leaking into a fixed-width answer.
"""
from __future__ import annotations


def mask(width: int) -> int:
    """The all-ones value for `width` bits."""
    return (1 << width) - 1


def popcount(value: int, width: int = 64) -> int:
    """Number of set bits (x86 POPCNT, AArch64 CNT+ADDV)."""
    v = value & mask(width)
    count = 0
    while v:
        v = v & (v - 1)      # clears the lowest set bit
        count = count + 1
    return count


def clz(value: int, width: int = 64) -> int:
    """Leading zeros. `clz(0)` is `width` (x86 LZCNT, AArch64 CLZ)."""
    v = value & mask(width)
    if v == 0:
        return width
    n = 0
    bit = 1 << (width - 1)
    while (v & bit) == 0:
        n = n + 1
        bit = bit >> 1
    return n


def ctz(value: int, width: int = 64) -> int:
    """Trailing zeros. `ctz(0)` is `width` (x86 TZCNT, AArch64 RBIT+CLZ)."""
    v = value & mask(width)
    if v == 0:
        return width
    n = 0
    while (v & 1) == 0:
        n = n + 1
        v = v >> 1
    return n


def bit_length(value: int, width: int = 64) -> int:
    """Position of the highest set bit, 1-based; 0 for zero."""
    return width - clz(value, width)


def parity(value: int, width: int = 64) -> int:
    """1 if an odd number of bits are set, else 0."""
    return popcount(value, width) & 1


def rotl(value: int, amount: int, width: int = 64) -> int:
    """Rotate left (x86 ROL, AArch64 via ROR)."""
    m = mask(width)
    v = value & m
    n = amount % width
    if n == 0:
        return v
    return ((v << n) | (v >> (width - n))) & m


def rotr(value: int, amount: int, width: int = 64) -> int:
    """Rotate right (x86 ROR, AArch64 ROR)."""
    m = mask(width)
    v = value & m
    n = amount % width
    if n == 0:
        return v
    return ((v >> n) | (v << (width - n))) & m


def byteswap(value: int, width: int = 64) -> int:
    """Reverse byte order (x86 BSWAP, AArch64 REV).

    `width` must be a whole number of bytes.
    """
    v = value & mask(width)
    out = 0
    i = 0
    n = width // 8
    while i < n:
        out = (out << 8) | (v & 0xFF)
        v = v >> 8
        i = i + 1
    return out


def reverse_bits(value: int, width: int = 64) -> int:
    """Reverse bit order (AArch64 RBIT; x86 has no single instruction)."""
    v = value & mask(width)
    out = 0
    i = 0
    while i < width:
        out = (out << 1) | (v & 1)
        v = v >> 1
        i = i + 1
    return out


def sign_extend(value: int, from_width: int, to_width: int = 64) -> int:
    """Sign-extend a `from_width` value, returned as `to_width` unsigned bits."""
    v = value & mask(from_width)
    sign = 1 << (from_width - 1)
    if v & sign:
        v = v - (1 << from_width)
    return v & mask(to_width)


def align_up(value: int, alignment: int) -> int:
    """Round up to a multiple of `alignment`, which must be a power of two."""
    return (value + alignment - 1) & ~(alignment - 1)


def align_down(value: int, alignment: int) -> int:
    return value & ~(alignment - 1)


def is_power_of_two(value: int) -> bool:
    return value > 0 and (value & (value - 1)) == 0
