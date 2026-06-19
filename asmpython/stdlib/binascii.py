"""binascii module: conversions between binary data and ASCII representations.

Implemented in pure asmpython (no C extension). Operates on strings rather
than bytes objects (asmpython has no bytes type — strings carry raw octets).

Hex operations:
  hexlify(data)       -> hex string (2 chars per byte)
  unhexlify(hexstr)   -> raw string (1 char per hex pair)
  b2a_hex(data)       -> alias for hexlify
  a2b_hex(hexstr)     -> alias for unhexlify

Base-64 operations (delegates to the base64 stdlib module):
  b2a_base64(data)    -> base64-encoded string (with trailing newline)
  a2b_base64(data)    -> decoded raw string

CRC / checksum:
  crc32(data, crc=0)  -> signed 32-bit CRC
  crc_hqx(data, crc)  -> CRC-CCITT-16 checksum used by binhex
"""
from __future__ import annotations

import base64 as _base64


class Error(Exception):
    """Exception raised on encoding/decoding errors."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


class Incomplete(Exception):
    """Exception raised when data is incomplete (partial byte groups)."""

    def __init__(self, msg: str = "") -> None:
        self.msg: str = msg

    def __str__(self) -> str:
        return self.msg


_HEX: str = "0123456789abcdef"
_HEX_UPPER: str = "0123456789ABCDEF"


def _nibble(ch: str) -> int:
    """Convert one hex character to its 4-bit value, or -1 on error."""
    c: str = ch
    if c >= "0" and c <= "9":
        return ord(c) - ord("0")
    if c >= "a" and c <= "f":
        return ord(c) - ord("a") + 10
    if c >= "A" and c <= "F":
        return ord(c) - ord("A") + 10
    return -1


def hexlify(data: str) -> str:
    """Return the hexadecimal representation of the binary data.

    Every byte becomes two lowercase hex characters.
    """
    result: str = ""
    i: int = 0
    n: int = len(data)
    while i < n:
        byte: int = ord(data[i])
        hi: int = (byte >> 4) & 0xF
        lo: int = byte & 0xF
        result = result + _HEX[hi] + _HEX[lo]
        i = i + 1
    return result


def b2a_hex(data: str) -> str:
    """Alias for hexlify."""
    return hexlify(data)


def unhexlify(hexstr: str) -> str:
    """Return the binary data represented by the hexadecimal string *hexstr*.

    Input must have an even number of characters; each pair is one byte.
    """
    n: int = len(hexstr)
    if n % 2 != 0:
        raise Error("Odd-length string")
    result: str = ""
    i: int = 0
    while i < n:
        hi: int = _nibble(hexstr[i])
        lo: int = _nibble(hexstr[i + 1])
        if hi < 0 or lo < 0:
            raise Error("Non-hexadecimal digit found")
        result = result + chr((hi << 4) | lo)
        i = i + 2
    return result


def a2b_hex(hexstr: str) -> str:
    """Alias for unhexlify."""
    return unhexlify(hexstr)


def b2a_base64(data: str) -> str:
    """Encode *data* in Base64 and append a newline, like base64.b64encode."""
    return _base64.b64encode(data) + "\n"


def a2b_base64(data: str) -> str:
    """Decode a Base64-encoded string (whitespace is ignored)."""
    # Strip whitespace before decoding.
    cleaned: str = ""
    i: int = 0
    n: int = len(data)
    while i < n:
        c: str = data[i]
        if c != " " and c != "\t" and c != "\n" and c != "\r":
            cleaned = cleaned + c
        i = i + 1
    return _base64.b64decode(cleaned)


def b2a_uu(data: str) -> str:
    """Encode data using uuencode (6-bit groups + 0x20 bias, 45-byte lines)."""
    n: int = len(data)
    line_len: int = 45 if n >= 45 else n
    result: str = chr(line_len + 0x20)
    i: int = 0
    while i < n:
        b0: int = ord(data[i]) if i < n else 0
        b1: int = ord(data[i + 1]) if i + 1 < n else 0
        b2: int = ord(data[i + 2]) if i + 2 < n else 0
        combined: int = (b0 << 16) | (b1 << 8) | b2
        c0: int = ((combined >> 18) & 0x3F) + 0x20
        c1: int = ((combined >> 12) & 0x3F) + 0x20
        c2: int = ((combined >> 6) & 0x3F) + 0x20
        c3: int = (combined & 0x3F) + 0x20
        result = result + chr(c0) + chr(c1) + chr(c2) + chr(c3)
        i = i + 3
    return result + "\n"


def a2b_uu(data: str) -> str:
    """Decode a uuencoded line."""
    if len(data) == 0:
        return ""
    line_len: int = (ord(data[0]) - 0x20) & 0x3F
    result: str = ""
    i: int = 1
    out: int = 0
    while out < line_len and i + 3 < len(data):
        c0: int = (ord(data[i]) - 0x20) & 0x3F
        c1: int = (ord(data[i + 1]) - 0x20) & 0x3F
        c2: int = (ord(data[i + 2]) - 0x20) & 0x3F
        c3: int = (ord(data[i + 3]) - 0x20) & 0x3F
        combined: int = (c0 << 18) | (c1 << 12) | (c2 << 6) | c3
        if out < line_len:
            result = result + chr((combined >> 16) & 0xFF)
            out = out + 1
        if out < line_len:
            result = result + chr((combined >> 8) & 0xFF)
            out = out + 1
        if out < line_len:
            result = result + chr(combined & 0xFF)
            out = out + 1
        i = i + 4
    return result


# CRC-32 table (ISO 3309 / ITU-T V.42 polynomial 0xEDB88320).
_CRC32_TABLE: list = []


def _build_crc32_table() -> None:
    global _CRC32_TABLE
    _CRC32_TABLE = []
    i: int = 0
    while i < 256:
        crc: int = i
        j: int = 0
        while j < 8:
            if crc & 1:
                crc = (crc >> 1) ^ 0xEDB88320
            else:
                crc = crc >> 1
            j = j + 1
        _CRC32_TABLE.append(crc)
        i = i + 1


_build_crc32_table()


def crc32(data: str, crc: int = 0) -> int:
    """Compute CRC-32 checksum of *data* (compatible with zlib.crc32).

    Returns a signed 32-bit integer.  Pass the result of a previous
    crc32() call as *crc* to chain multiple data chunks.
    """
    value: int = crc ^ 0xFFFFFFFF
    i: int = 0
    n: int = len(data)
    while i < n:
        byte: int = ord(data[i])
        idx: int = (value ^ byte) & 0xFF
        value = (value >> 8) ^ _CRC32_TABLE[idx]
        i = i + 1
    value = value ^ 0xFFFFFFFF
    # Convert to signed 32-bit.
    if value >= 0x80000000:
        value = value - 0x100000000
    return value


# CRC-HQAS table (CCITT-16, polynomial 0x1021).
_CRC_HQX_TABLE: list = []


def _build_crc_hqx_table() -> None:
    global _CRC_HQX_TABLE
    _CRC_HQX_TABLE = []
    i: int = 0
    while i < 256:
        crc: int = i << 8
        j: int = 0
        while j < 8:
            if crc & 0x8000:
                crc = (crc << 1) ^ 0x1021
            else:
                crc = crc << 1
            crc = crc & 0xFFFF
            j = j + 1
        _CRC_HQX_TABLE.append(crc)
        i = i + 1


_build_crc_hqx_table()


def crc_hqx(data: str, crc: int) -> int:
    """Compute the CCITT-16 checksum (used by binhex4)."""
    value: int = crc & 0xFFFF
    i: int = 0
    n: int = len(data)
    while i < n:
        byte: int = ord(data[i])
        idx: int = ((value >> 8) ^ byte) & 0xFF
        value = ((value << 8) ^ _CRC_HQX_TABLE[idx]) & 0xFFFF
        i = i + 1
    return value


def rlecode_hqx(data: str) -> str:
    """Perform run-length encoding for binhex4 (stub: returns data unchanged)."""
    return data


def rledecode_hqx(data: str) -> str:
    """Decode binhex4 run-length encoding (stub: returns data unchanged)."""
    return data
