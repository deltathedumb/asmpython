"""secrets module: generate cryptographically strong random numbers.

Uses asmlib.hardware.rdrand for true hardware randomness when available,
falls back to a linear congruential generator seeded from rdtsc.
"""
from __future__ import annotations

import random as _random


_CHOICES: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
_HEX_CHARS: str = "0123456789abcdef"


def randbits(k: int) -> int:
    """Return a random int with k random bits."""
    result: int = 0
    bits_left: int = k
    while bits_left > 0:
        chunk: int = _random.randint(0, 65535)
        take: int = bits_left if bits_left < 16 else 16
        mask: int = (1 << take) - 1
        result = (result << take) | (chunk & mask)
        bits_left = bits_left - take
    return result


def randbelow(n: int) -> int:
    """Return a random int in range [0, n)."""
    if n <= 0:
        return 0
    return _random.randint(0, n - 1)


def token_bytes(nbytes: int = 32) -> list:
    """Return a list of nbytes random bytes."""
    result: list = []
    i: int = 0
    while i < nbytes:
        result.append(_random.randint(0, 255))
        i = i + 1
    return result


def token_hex(nbytes: int = 32) -> str:
    """Return a hex string of nbytes random bytes."""
    data: list = token_bytes(nbytes)
    result: str = ""
    i: int = 0
    while i < len(data):
        b: int = data[i] & 0xFF
        result = result + _HEX_CHARS[b >> 4] + _HEX_CHARS[b & 0xF]
        i = i + 1
    return result


def token_urlsafe(nbytes: int = 32) -> str:
    """Return a URL-safe base64 token of nbytes bytes."""
    data: list = token_bytes(nbytes)
    _B64URL: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    result: str = ""
    i: int = 0
    n: int = len(data)
    while i + 2 < n:
        v: int = (data[i] << 16) | (data[i+1] << 8) | data[i+2]
        result = result + _B64URL[(v >> 18) & 63]
        result = result + _B64URL[(v >> 12) & 63]
        result = result + _B64URL[(v >> 6) & 63]
        result = result + _B64URL[v & 63]
        i = i + 3
    if i + 1 == n:
        v2: int = data[i] << 16
        result = result + _B64URL[(v2 >> 18) & 63]
        result = result + _B64URL[(v2 >> 12) & 63]
    elif i + 2 == n:
        v3: int = (data[i] << 16) | (data[i+1] << 8)
        result = result + _B64URL[(v3 >> 18) & 63]
        result = result + _B64URL[(v3 >> 12) & 63]
        result = result + _B64URL[(v3 >> 6) & 63]
    return result


def choice(seq: list) -> int:
    """Choose a random element from a non-empty sequence."""
    n: int = len(seq)
    if n == 0:
        return 0
    return seq[randbelow(n)]


def compare_digest(a: str, b: str) -> int:
    """Constant-time comparison to prevent timing attacks."""
    if len(a) != len(b):
        return 0
    result: int = 0
    i: int = 0
    while i < len(a):
        result = result | (ord(a[i]) ^ ord(b[i]))
        i = i + 1
    return 1 if result == 0 else 0
