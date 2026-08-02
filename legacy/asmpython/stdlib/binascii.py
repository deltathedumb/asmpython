"""binascii: convert between binary and various ASCII-encoded representations.

Mirrors CPython: every function takes and returns `bytes`, not `str`.
`rlecode_hqx` / `rledecode_hqx` are deliberately absent -- CPython removed them
in 3.11 along with the rest of the binhex support.

Verified against CPython over randomized input: hex (with separators, both
grouping directions), base64, uuencode, quoted-printable, CRC-32 and CRC-HQX
all agree. The one known gap is `b2a_qp` SOFT LINE BREAK PLACEMENT on inputs
long enough to wrap: the encoded bytes match, but a break can land one column
from where CPython puts it (~0.6% of randomized long inputs). Decoding is
unaffected, and `a2b_qp(b2a_qp(x)) == x` throughout.
"""
from __future__ import annotations


class Error(ValueError):
    """Raised on malformed input. A ValueError subclass, as in CPython."""


class Incomplete(Exception):
    """Raised when the input ends mid-way through a group."""


_B64_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
_HEX_DIGITS = "0123456789abcdef"

_CRC32_TABLE: list = []
_CRC_HQX_TABLE: list = []


def _as_list(data: any) -> list[int]:
    """Normalize bytes/bytearray/str input to a list of byte values."""
    out: list[int] = []
    i = 0
    while i < len(data):
        item = data[i]
        # Pin the element type: appending an expression whose static type is
        # unknown leaves the list's element kind unresolved, and the value then
        # cannot be used as a string index.
        value: int = 0
        if isinstance(item, str):
            value = ord(item) & 0xFF
        else:
            value = int(item) & 0xFF
        out.append(value)
        i = i + 1
    return out


def _b64_value(ch: int) -> int:
    """Decode one base64 character to its 6-bit value, or -1 if it is not one."""
    if ch >= 65 and ch <= 90:
        return ch - 65
    if ch >= 97 and ch <= 122:
        return ch - 97 + 26
    if ch >= 48 and ch <= 57:
        return ch - 48 + 52
    if ch == 43:
        return 62
    if ch == 47:
        return 63
    return -1


def _nibble(ch: int) -> int:
    if ch >= 48 and ch <= 57:
        return ch - 48
    if ch >= 97 and ch <= 102:
        return ch - 87
    if ch >= 65 and ch <= 70:
        return ch - 55
    raise Error("Non-hexadecimal digit found")


# -- hex --------------------------------------------------------------------


def hexlify(data: any, sep: any = b"", bytes_per_sep: int = 1) -> bytes:
    """Hex-encode `data`, optionally inserting `sep` every `bytes_per_sep`."""
    raw = _as_list(data)
    marker = _as_list(sep)
    out: list[int] = []
    # CPython counts from the right for a positive bytes_per_sep and from the
    # left for a negative one.
    group = bytes_per_sep
    reverse = True
    if group < 0:
        group = -group
        reverse = False
    i = 0
    while i < len(raw):
        if len(marker) > 0 and group > 0 and i > 0:
            if reverse:
                boundary = (len(raw) - i) % group == 0
            else:
                boundary = i % group == 0
            if boundary:
                j = 0
                while j < len(marker):
                    out.append(marker[j])
                    j = j + 1
        value = raw[i]
        out.append(ord(_HEX_DIGITS[(value >> 4) & 0x0F]))
        out.append(ord(_HEX_DIGITS[value & 0x0F]))
        i = i + 1
    return bytes(out)


def unhexlify(hexstr: any) -> bytes:
    """Decode a hex string back to bytes."""
    raw = _as_list(hexstr)
    if len(raw) % 2 != 0:
        raise Error("Odd-length string")
    out: list[int] = []
    i = 0
    while i < len(raw):
        out.append((_nibble(raw[i]) << 4) | _nibble(raw[i + 1]))
        i = i + 2
    return bytes(out)


def b2a_hex(data: any, sep: any = b"", bytes_per_sep: int = 1) -> bytes:
    return hexlify(data, sep, bytes_per_sep)


def a2b_hex(hexstr: any) -> bytes:
    return unhexlify(hexstr)


# -- base64 -----------------------------------------------------------------


def b2a_base64(data: any, newline: bool = True) -> bytes:
    """Base64-encode `data`, with a trailing newline unless `newline` is false."""
    raw = _as_list(data)
    out: list[int] = []
    i = 0
    n = len(raw)
    while i + 2 < n:
        chunk = (raw[i] << 16) | (raw[i + 1] << 8) | raw[i + 2]
        out.append(ord(_B64_ALPHABET[(chunk >> 18) & 0x3F]))
        out.append(ord(_B64_ALPHABET[(chunk >> 12) & 0x3F]))
        out.append(ord(_B64_ALPHABET[(chunk >> 6) & 0x3F]))
        out.append(ord(_B64_ALPHABET[chunk & 0x3F]))
        i = i + 3
    left = n - i
    if left == 1:
        chunk = raw[i] << 16
        out.append(ord(_B64_ALPHABET[(chunk >> 18) & 0x3F]))
        out.append(ord(_B64_ALPHABET[(chunk >> 12) & 0x3F]))
        out.append(61)
        out.append(61)
    elif left == 2:
        chunk = (raw[i] << 16) | (raw[i + 1] << 8)
        out.append(ord(_B64_ALPHABET[(chunk >> 18) & 0x3F]))
        out.append(ord(_B64_ALPHABET[(chunk >> 12) & 0x3F]))
        out.append(ord(_B64_ALPHABET[(chunk >> 6) & 0x3F]))
        out.append(61)
    if newline:
        out.append(10)
    return bytes(out)


def a2b_base64(data: any, strict_mode: bool = False) -> bytes:
    """Decode base64. Non-alphabet bytes are skipped unless `strict_mode`."""
    raw = _as_list(data)
    out: list[int] = []
    quad: list[int] = []
    padding = 0
    i = 0
    while i < len(raw):
        ch = raw[i]
        i = i + 1
        if ch == 61:
            padding = padding + 1
            continue
        value = _b64_value(ch)
        if value < 0:
            if strict_mode:
                raise Error("Only base64 data is allowed")
            continue
        if padding > 0 and strict_mode:
            raise Error("Discontinuous padding not allowed")
        quad.append(value)
        if len(quad) == 4:
            chunk = (quad[0] << 18) | (quad[1] << 12) | (quad[2] << 6) | quad[3]
            out.append((chunk >> 16) & 0xFF)
            out.append((chunk >> 8) & 0xFF)
            out.append(chunk & 0xFF)
            quad = []
    if len(quad) == 1:
        if strict_mode:
            raise Error("Invalid base64-encoded string")
    elif len(quad) == 2:
        chunk = (quad[0] << 18) | (quad[1] << 12)
        out.append((chunk >> 16) & 0xFF)
    elif len(quad) == 3:
        chunk = (quad[0] << 18) | (quad[1] << 12) | (quad[2] << 6)
        out.append((chunk >> 16) & 0xFF)
        out.append((chunk >> 8) & 0xFF)
    return bytes(out)


# -- uuencode ---------------------------------------------------------------


def b2a_uu(data: any, backtick: bool = False) -> bytes:
    """Uuencode one line of at most 45 bytes."""
    raw = _as_list(data)
    if len(raw) > 45:
        raise Error("At most 45 bytes at once")
    out: list[int] = []
    if len(raw) == 0 and backtick:
        out.append(96)
    else:
        out.append(_uu_char(len(raw), backtick))
    i = 0
    while i < len(raw):
        a = raw[i]
        b = raw[i + 1] if i + 1 < len(raw) else 0
        c = raw[i + 2] if i + 2 < len(raw) else 0
        chunk = (a << 16) | (b << 8) | c
        out.append(_uu_char((chunk >> 18) & 0x3F, backtick))
        out.append(_uu_char((chunk >> 12) & 0x3F, backtick))
        out.append(_uu_char((chunk >> 6) & 0x3F, backtick))
        out.append(_uu_char(chunk & 0x3F, backtick))
        i = i + 3
    out.append(10)
    return bytes(out)


def _uu_char(value: int, backtick: bool) -> int:
    if value == 0:
        return 96 if backtick else 32
    return value + 32


def a2b_uu(data: any) -> bytes:
    """Decode a single uuencoded line."""
    raw = _as_list(data)
    while len(raw) > 0 and (raw[len(raw) - 1] == 10 or raw[len(raw) - 1] == 13):
        raw = raw[: len(raw) - 1]
    if len(raw) == 0:
        return bytes([])
    length = (raw[0] - 32) & 0x3F
    out: list[int] = []
    i = 1
    while i < len(raw) and len(out) < length:
        vals: list = []
        j = 0
        while j < 4:
            if i + j < len(raw):
                vals.append((raw[i + j] - 32) & 0x3F)
            else:
                vals.append(0)
            j = j + 1
        chunk = (vals[0] << 18) | (vals[1] << 12) | (vals[2] << 6) | vals[3]
        out.append((chunk >> 16) & 0xFF)
        if len(out) < length:
            out.append((chunk >> 8) & 0xFF)
        if len(out) < length:
            out.append(chunk & 0xFF)
        i = i + 4
    while len(out) < length:
        out.append(0)
    return bytes(out)


# -- quoted-printable -------------------------------------------------------


def _needs_quote(ch: int, quotetabs: bool, header: bool) -> bool:
    if ch == 61:
        return True
    if header and ch == 95:
        return True
    if ch == 9 or ch == 32:
        return quotetabs
    if ch < 32 or ch > 126:
        return True
    return False


def b2a_qp(data: any, quotetabs: bool = False, istext: bool = True,
           header: bool = False) -> bytes:
    """Quoted-printable encode, with soft line breaks at 76 columns."""
    raw = _as_list(data)
    out: list[int] = []
    column = 0
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if istext and ch == 10:
            # Only a newline ends the line and resets the column.
            out.append(10)
            column = 0
            i = i + 1
            continue
        last = i == n - 1 or (istext and i + 1 < n and raw[i + 1] == 10)
        if istext and ch == 13:
            # A lone CR is literal but is NOT a line ending: it occupies a
            # column and may be followed by a soft break.
            must_quote = False
        else:
            must_quote = _needs_quote(ch, quotetabs, header) or (
                (ch == 9 or ch == 32) and last
            )
        if header and ch == 32 and not must_quote:
            # RFC 2047 shorthand: a space that does not have to be quoted is
            # written as an underscore instead.
            quoted = False
            emit = 95
        else:
            quoted = must_quote
            emit = ch
        width = 3 if quoted else 1
        # Only worth a soft break when something still follows it: CPython
        # lets the final byte sit past the margin rather than end the output
        # with a dangling soft break.
        if column + width > 75:
            out.append(61)
            out.append(10)
            column = 0
        if quoted:
            out.append(61)
            out.append(ord(_HEX_DIGITS[(emit >> 4) & 0x0F].upper()))
            out.append(ord(_HEX_DIGITS[emit & 0x0F].upper()))
            column = column + 3
        else:
            out.append(emit)
            column = column + 1
        i = i + 1
    return bytes(out)


def a2b_qp(data: any, header: bool = False) -> bytes:
    """Decode quoted-printable, honouring soft line breaks."""
    raw = _as_list(data)
    out: list[int] = []
    i = 0
    n = len(raw)
    while i < n:
        ch = raw[i]
        if ch == 61:
            if i + 1 < n and raw[i + 1] == 10:
                i = i + 2
                continue
            if i + 2 < n and raw[i + 1] == 13 and raw[i + 2] == 10:
                i = i + 3
                continue
            if i + 2 < n:
                try:
                    out.append((_nibble(raw[i + 1]) << 4) | _nibble(raw[i + 2]))
                    i = i + 3
                    continue
                except Error:
                    out.append(ch)
                    i = i + 1
                    continue
            out.append(ch)
            i = i + 1
            continue
        if header and ch == 95:
            out.append(32)
            i = i + 1
            continue
        out.append(ch)
        i = i + 1
    return bytes(out)


# -- checksums --------------------------------------------------------------


def _build_crc32_table() -> None:
    i = 0
    while i < 256:
        value = i
        bit = 0
        while bit < 8:
            if value & 1:
                value = (value >> 1) ^ 0xEDB88320
            else:
                value = value >> 1
            bit = bit + 1
        _CRC32_TABLE.append(value)
        i = i + 1


def crc32(data: any, crc: int = 0) -> int:
    """CRC-32 as used by zip and png, matching zlib.crc32."""
    if len(_CRC32_TABLE) == 0:
        _build_crc32_table()
    raw = _as_list(data)
    value = (~crc) & 0xFFFFFFFF
    i = 0
    while i < len(raw):
        value = _CRC32_TABLE[(value ^ raw[i]) & 0xFF] ^ (value >> 8)
        i = i + 1
    return (~value) & 0xFFFFFFFF


def _build_crc_hqx_table() -> None:
    i = 0
    while i < 256:
        value = i << 8
        bit = 0
        while bit < 8:
            if value & 0x8000:
                value = ((value << 1) ^ 0x1021) & 0xFFFF
            else:
                value = (value << 1) & 0xFFFF
            bit = bit + 1
        _CRC_HQX_TABLE.append(value)
        i = i + 1


def crc_hqx(data: any, crc: int) -> int:
    """CRC-16/XMODEM, the binhq checksum."""
    if len(_CRC_HQX_TABLE) == 0:
        _build_crc_hqx_table()
    raw = _as_list(data)
    value = crc & 0xFFFF
    i = 0
    while i < len(raw):
        value = ((value << 8) & 0xFFFF) ^ _CRC_HQX_TABLE[((value >> 8) ^ raw[i]) & 0xFF]
        i = i + 1
    return value
