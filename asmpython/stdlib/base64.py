"""base64 module: RFC 4648 binary-to-text encodings.

Data is represented as `list[int]` (each element a byte, 0-255), the same
convention `hashlib` uses for digests/messages. Encoded output is also
`list[int]` of ASCII codes (mirroring CPython's `bytes` in / `bytes` out
signatures); use `"".join([chr(b) for b in out])` to get a `str`.

Implements:
  - b64encode / b64decode (standard alphabet, `+`/`/`, `=` padding)
  - standard_b64encode / standard_b64decode (aliases, per CPython)
  - urlsafe_b64encode / urlsafe_b64decode (`-`/`_` alphabet)
  - b32encode / b32decode (RFC 4648 base32, `=` padding)
  - b16encode / b16decode (uppercase hex, matching CPython)

Limitations vs CPython:
  - No `a85encode`/`a85decode` or `b85encode`/`b85decode` (ascii85/base85):
    rarely used and significantly more complex (ascii85's "z" run-length
    shortcut); not implemented.
  - `b64decode`/`b32decode`/`b16decode` always behave like CPython's
    `validate=False`/casefold=False: invalid characters raise `ValueError`
    rather than being silently discarded.
"""
from __future__ import annotations

_B64_STD = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_B64_URLSAFE = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
_B32_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
_HEX_UPPER = "0123456789ABCDEF"


def _b64encode_with(data: list[int], alphabet: str) -> list[int]:
    out: list[int] = []
    n = len(data)
    i = 0
    while i + 3 <= n:
        triple = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
        out.append(ord(alphabet[(triple >> 18) & 0x3F]))
        out.append(ord(alphabet[(triple >> 12) & 0x3F]))
        out.append(ord(alphabet[(triple >> 6) & 0x3F]))
        out.append(ord(alphabet[triple & 0x3F]))
        i = i + 3
    rem = n - i
    if rem == 1:
        triple = data[i] << 16
        out.append(ord(alphabet[(triple >> 18) & 0x3F]))
        out.append(ord(alphabet[(triple >> 12) & 0x3F]))
        out.append(ord("="))
        out.append(ord("="))
    elif rem == 2:
        triple = (data[i] << 16) | (data[i + 1] << 8)
        out.append(ord(alphabet[(triple >> 18) & 0x3F]))
        out.append(ord(alphabet[(triple >> 12) & 0x3F]))
        out.append(ord(alphabet[(triple >> 6) & 0x3F]))
        out.append(ord("="))
    return out


def _b64decode_with(data: list[int], alphabet: str) -> list[int]:
    vals: list[int] = []
    for c in data:
        ch = chr(c)
        if ch == "=":
            continue
        idx = alphabet.find(ch)
        if idx < 0:
            raise ValueError("Invalid base64-encoded string")
        vals.append(idx)
    n = len(vals)
    if n % 4 == 1:
        raise ValueError("Invalid base64-encoded string: invalid length")
    out: list[int] = []
    i = 0
    while i + 4 <= n:
        v = (vals[i] << 18) | (vals[i + 1] << 12) | (vals[i + 2] << 6) | vals[i + 3]
        out.append((v >> 16) & 0xFF)
        out.append((v >> 8) & 0xFF)
        out.append(v & 0xFF)
        i = i + 4
    rem = n - i
    if rem == 2:
        v = (vals[i] << 18) | (vals[i + 1] << 12)
        out.append((v >> 16) & 0xFF)
    elif rem == 3:
        v = (vals[i] << 18) | (vals[i + 1] << 12) | (vals[i + 2] << 6)
        out.append((v >> 16) & 0xFF)
        out.append((v >> 8) & 0xFF)
    return out


def b64encode(data: list[int]) -> list[int]:
    """Encode `data` using the standard base64 alphabet (`+`/`/`)."""
    return _b64encode_with(data, _B64_STD)


def b64decode(data: list[int]) -> list[int]:
    """Decode standard base64 (`+`/`/`) back to bytes."""
    return _b64decode_with(data, _B64_STD)


def standard_b64encode(data: list[int]) -> list[int]:
    return b64encode(data)


def standard_b64decode(data: list[int]) -> list[int]:
    return b64decode(data)


def urlsafe_b64encode(data: list[int]) -> list[int]:
    """Encode using the URL- and filesystem-safe alphabet (`-`/`_`)."""
    return _b64encode_with(data, _B64_URLSAFE)


def urlsafe_b64decode(data: list[int]) -> list[int]:
    """Decode the URL- and filesystem-safe alphabet (`-`/`_`) back to bytes."""
    return _b64decode_with(data, _B64_URLSAFE)


def b32encode(data: list[int]) -> list[int]:
    """Encode `data` using the RFC 4648 base32 alphabet, `=`-padded."""
    out: list[int] = []
    n = len(data)
    i = 0
    while i + 5 <= n:
        chunk = (
            (data[i] << 32) | (data[i + 1] << 24) | (data[i + 2] << 16)
            | (data[i + 3] << 8) | data[i + 4]
        )
        j = 7
        while j >= 0:
            out.append(ord(_B32_ALPHABET[(chunk >> (j * 5)) & 0x1F]))
            j = j - 1
        i = i + 5
    rem = n - i
    if rem > 0:
        chunk = 0
        k = 0
        while k < rem:
            chunk = chunk | (data[i + k] << (32 - 8 * k))
            k = k + 1
        # Number of 5-bit output groups that contain real data for the
        # remaining `rem` input bytes (RFC 4648 padding table).
        if rem == 1:
            n_groups = 2
        elif rem == 2:
            n_groups = 4
        elif rem == 3:
            n_groups = 5
        elif rem == 4:
            n_groups = 7
        j = 7
        while j >= 0:
            if j > 7 - n_groups:
                out.append(ord(_B32_ALPHABET[(chunk >> (j * 5)) & 0x1F]))
            else:
                out.append(ord("="))
            j = j - 1
    return out


def b32decode(data: list[int]) -> list[int]:
    """Decode RFC 4648 base32 (`=`-padded) back to bytes."""
    vals: list[int] = []
    for c in data:
        ch = chr(c)
        if ch == "=":
            continue
        idx = _B32_ALPHABET.find(ch)
        if idx < 0:
            raise ValueError("Invalid base32-encoded string")
        vals.append(idx)
    n = len(vals)
    out: list[int] = []
    i = 0
    while i + 8 <= n:
        chunk = 0
        j = 0
        while j < 8:
            chunk = chunk | (vals[i + j] << (35 - 5 * j))
            j = j + 1
        out.append((chunk >> 32) & 0xFF)
        out.append((chunk >> 24) & 0xFF)
        out.append((chunk >> 16) & 0xFF)
        out.append((chunk >> 8) & 0xFF)
        out.append(chunk & 0xFF)
        i = i + 8
    rem = n - i
    if rem > 0:
        chunk = 0
        j = 0
        while j < rem:
            chunk = chunk | (vals[i + j] << (35 - 5 * j))
            j = j + 1
        # Number of output bytes recoverable from `rem` 5-bit groups.
        if rem == 2:
            n_bytes = 1
        elif rem == 4:
            n_bytes = 2
        elif rem == 5:
            n_bytes = 3
        elif rem == 7:
            n_bytes = 4
        else:
            raise ValueError("Invalid base32-encoded string: invalid length")
        k = 0
        while k < n_bytes:
            out.append((chunk >> (32 - 8 * k)) & 0xFF)
            k = k + 1
    return out


def b16encode(data: list[int]) -> list[int]:
    """Encode `data` as uppercase hex (matching CPython's b16encode)."""
    out: list[int] = []
    for b in data:
        out.append(ord(_HEX_UPPER[(b >> 4) & 0xF]))
        out.append(ord(_HEX_UPPER[b & 0xF]))
    return out


def b16decode(data: list[int]) -> list[int]:
    """Decode uppercase (or lowercase) hex back to bytes."""
    n = len(data)
    if n % 2 != 0:
        raise ValueError("Invalid base16-encoded string: invalid length")
    out: list[int] = []
    i = 0
    while i < n:
        hi = _HEX_UPPER.find(chr(data[i]).upper())
        lo = _HEX_UPPER.find(chr(data[i + 1]).upper())
        if hi < 0 or lo < 0:
            raise ValueError("Invalid base16-encoded string")
        out.append((hi << 4) | lo)
        i = i + 2
    return out
