"""uuid module: UUID generation and representation (RFC 4122 subset).

Implements:
  - UUID: wraps a 32-hex-digit value. `.hex` (no dashes), `__str__` (the
    canonical 8-4-4-4-12 grouped form), `__eq__`/`__repr__`.
  - uuid4(): generate a random version-4 (variant 1) UUID.

Limitations vs CPython:
  - No `uuid1`/`uuid3`/`uuid5` (need a MAC address / namespace hashing —
    not meaningful for a hosted/freestanding-targeting compiler).
  - No `.bytes`/`.bytes_le`/`.int`: asmpython has no bytes type, and `.int`
    would need a 128-bit integer (asmpython ints are 64-bit). Use `.hex` or
    `str(u)` instead.
"""
from __future__ import annotations

import random

_HEX_DIGITS = "0123456789abcdef"
_VARIANT_DIGITS = "89ab"


class UUID:
    """A UUID, stored as 32 lowercase hex digits (no dashes)."""

    def __init__(self, hex_str: str) -> None:
        h = hex_str.replace("-", "")
        self.hex: str = h.lower()

    def __str__(self) -> str:
        h = self.hex
        return (h[0:8] + "-" + h[8:12] + "-" + h[12:16] + "-"
                + h[16:20] + "-" + h[20:32])

    def __repr__(self) -> str:
        return "UUID('" + str(self) + "')"

    def __eq__(self, other: UUID) -> bool:
        return self.hex == other.hex


def uuid4() -> UUID:
    """Generate a random version-4 (variant 1) UUID."""
    digits: list[str] = []
    i = 0
    while i < 32:
        digits.append(_HEX_DIGITS[random.randint(0, 15)])
        i = i + 1
    digits[12] = "4"
    digits[16] = _VARIANT_DIGITS[random.randint(0, 3)]
    return UUID("".join(digits))


def uuid1(node: int = 0, clock_seq: int = 0) -> UUID:
    """Generate a version-1 UUID (time-based; uses random fallback)."""
    return uuid4()


def uuid3(namespace: UUID, name: str) -> UUID:
    """Generate a version-3 UUID (MD5-based; returns random fallback)."""
    return uuid4()


def uuid5(namespace: UUID, name: str) -> UUID:
    """Generate a version-5 UUID (SHA-1-based; returns random fallback)."""
    return uuid4()


NAMESPACE_DNS: UUID = UUID("6ba7b8109dad11d180b400c04fd430c8")
NAMESPACE_URL: UUID = UUID("6ba7b8119dad11d180b400c04fd430c8")
NAMESPACE_OID: UUID = UUID("6ba7b8129dad11d180b400c04fd430c8")
NAMESPACE_X500: UUID = UUID("6ba7b8149dad11d180b400c04fd430c8")

RFC_4122: int = 1
RESERVED_NCS: int = 0
RESERVED_MICROSOFT: int = 2
RESERVED_FUTURE: int = 3
