"""Explicit byte order.

Nothing here reads the host's endianness. A format that says "little-endian
u32" means that on every machine, and code that silently agrees with the host
is code that breaks the first time it is cross-compiled. ``HOST`` is available
for when you genuinely need to ask, and `lllib.COMPILE_DATA.endian` answers it
at compile time.

Functions take and return plain ints and `bytes`, so they work identically on
a host under CPython and in a compiled program.
"""
from __future__ import annotations

from . import bits

LITTLE = "little"
BIG = "big"

#: Byte order of the machine being compiled FOR, decided at compile time.
#: Every target asmpython supports today is little-endian; MIPS is the first
#: candidate backend that can be either, which is why this is a named constant
#: rather than an assumption spelled out in each function.
HOST = LITTLE


def pack(value: int, width: int, order: str = LITTLE) -> bytes:
    """`width` bytes holding `value` in `order`."""
    v = value & bits.mask(width * 8)
    out = bytearray(width)
    i = 0
    while i < width:
        out[i] = v & 0xFF
        v = v >> 8
        i = i + 1
    if order == BIG:
        out.reverse()
    return bytes(out)


def unpack(data: bytes, offset: int, width: int, order: str = LITTLE) -> int:
    """Read `width` bytes at `offset` as an unsigned integer."""
    value = 0
    i = 0
    while i < width:
        index = offset + (width - 1 - i) if order == BIG else offset + i
        value = value | (data[index] << (8 * i))
        i = i + 1
    return value


def unpack_signed(data: bytes, offset: int, width: int,
                  order: str = LITTLE) -> int:
    """Read `width` bytes as a two's-complement signed integer."""
    raw = unpack(data, offset, width, order)
    sign = 1 << (width * 8 - 1)
    if raw & sign:
        return raw - (1 << (width * 8))
    return raw


def to_little(value: int, width: int = 64) -> int:
    """Reinterpret `value` as little-endian, byte-swapping if the host is big."""
    if HOST == LITTLE:
        return value & bits.mask(width)
    return bits.byteswap(value, width)


def to_big(value: int, width: int = 64) -> int:
    if HOST == BIG:
        return value & bits.mask(width)
    return bits.byteswap(value, width)


def swap_if(condition: bool, value: int, width: int = 64) -> int:
    """Byte-swap only when `condition` -- the shape a format reader wants."""
    if condition:
        return bits.byteswap(value, width)
    return value & bits.mask(width)
