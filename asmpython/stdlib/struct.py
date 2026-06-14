"""struct module: pack/unpack binary data.

In asmpython, we implement struct as a pure-Python source module that handles
the most common format characters. Data is stored as a list of integers.
The actual bytes are represented as a list[int] where each element is a byte.

Supported format chars: B (uint8), b (int8), H (uint16), h (int16),
I (uint32), i (int32), Q (uint64), q (int64), x (pad), ? (bool),
< > ! = @ (byte order, no-op).
"""
from __future__ import annotations


class _FmtSpec:
    """Parsed format specifier: count and char code (ord of the format char)."""

    def __init__(self, repeat: int, code: int) -> None:
        self.repeat: int = repeat
        self.code: int = code


def _parse_fmt(fmt: str) -> list:
    """Parse format string into list of _FmtSpec objects."""
    result: list = []
    i: int = 0
    n: int = len(fmt)
    while i < n:
        c: str = fmt[i]
        if c == "<" or c == ">" or c == "!" or c == "=" or c == "@":
            i = i + 1
            continue
        if c >= "0" and c <= "9":
            repeat: int = 0
            while i < n and fmt[i] >= "0" and fmt[i] <= "9":
                repeat = repeat * 10 + ord(fmt[i]) - 48
                i = i + 1
            if i < n:
                result.append(_FmtSpec(repeat, ord(fmt[i])))
                i = i + 1
        else:
            result.append(_FmtSpec(1, ord(c)))
            i = i + 1
    return result


def calcsize(fmt: str) -> int:
    """Return the size in bytes of the struct described by format string."""
    specs: list = _parse_fmt(fmt)
    total: int = 0
    idx: int = 0
    while idx < len(specs):
        spec: _FmtSpec = specs[idx]
        repeat: int = spec.repeat
        code: int = spec.code
        # B=66 b=98 x=120 s=115 c=99 ?=63
        if code == 66 or code == 98 or code == 120 or code == 115 or code == 99 or code == 63:
            total = total + repeat
        elif code == 72 or code == 104:  # H=72 h=104
            total = total + repeat * 2
        elif code == 73 or code == 105 or code == 102:  # I=73 i=105 f=102
            total = total + repeat * 4
        elif code == 81 or code == 113 or code == 100:  # Q=81 q=113 d=100
            total = total + repeat * 8
        idx = idx + 1
    return total


def pack(fmt: str, *args) -> list:
    """Pack values into a list of bytes according to format string."""
    specs: list = _parse_fmt(fmt)
    result: list = []
    arg_idx: int = 0
    spec_idx: int = 0
    while spec_idx < len(specs):
        spec: _FmtSpec = specs[spec_idx]
        repeat: int = spec.repeat
        code: int = spec.code
        rep: int = 0
        while rep < repeat:
            if code == 120:  # x = pad byte
                result.append(0)
            elif code == 66:  # B = uint8
                v: int = args[arg_idx]
                result.append(v & 0xFF)
                arg_idx = arg_idx + 1
            elif code == 98:  # b = int8
                v2: int = args[arg_idx]
                if v2 < 0:
                    v2 = v2 + 256
                result.append(v2 & 0xFF)
                arg_idx = arg_idx + 1
            elif code == 63:  # ? = bool
                vb: int = 1 if args[arg_idx] else 0
                result.append(vb)
                arg_idx = arg_idx + 1
            elif code == 72:  # H = uint16
                vh: int = args[arg_idx] & 0xFFFF
                result.append(vh & 0xFF)
                result.append((vh >> 8) & 0xFF)
                arg_idx = arg_idx + 1
            elif code == 104:  # h = int16
                vh2: int = args[arg_idx]
                if vh2 < 0:
                    vh2 = vh2 + 65536
                vh2 = vh2 & 0xFFFF
                result.append(vh2 & 0xFF)
                result.append((vh2 >> 8) & 0xFF)
                arg_idx = arg_idx + 1
            elif code == 73:  # I = uint32
                vi: int = args[arg_idx] & 0xFFFFFFFF
                result.append(vi & 0xFF)
                result.append((vi >> 8) & 0xFF)
                result.append((vi >> 16) & 0xFF)
                result.append((vi >> 24) & 0xFF)
                arg_idx = arg_idx + 1
            elif code == 105:  # i = int32
                vi2: int = args[arg_idx]
                if vi2 < 0:
                    vi2 = vi2 + 4294967296
                vi2 = vi2 & 0xFFFFFFFF
                result.append(vi2 & 0xFF)
                result.append((vi2 >> 8) & 0xFF)
                result.append((vi2 >> 16) & 0xFF)
                result.append((vi2 >> 24) & 0xFF)
                arg_idx = arg_idx + 1
            elif code == 81:  # Q = uint64
                vq: int = args[arg_idx]
                result.append(vq & 0xFF)
                result.append((vq >> 8) & 0xFF)
                result.append((vq >> 16) & 0xFF)
                result.append((vq >> 24) & 0xFF)
                result.append((vq >> 32) & 0xFF)
                result.append((vq >> 40) & 0xFF)
                result.append((vq >> 48) & 0xFF)
                result.append((vq >> 56) & 0xFF)
                arg_idx = arg_idx + 1
            elif code == 113:  # q = int64
                vq2: int = args[arg_idx]
                result.append(vq2 & 0xFF)
                result.append((vq2 >> 8) & 0xFF)
                result.append((vq2 >> 16) & 0xFF)
                result.append((vq2 >> 24) & 0xFF)
                result.append((vq2 >> 32) & 0xFF)
                result.append((vq2 >> 40) & 0xFF)
                result.append((vq2 >> 48) & 0xFF)
                result.append((vq2 >> 56) & 0xFF)
                arg_idx = arg_idx + 1
            elif code == 115 or code == 99:  # s or c = char (stored as byte value)
                vchar: int = args[arg_idx]
                result.append(vchar & 0xFF)
                arg_idx = arg_idx + 1
            rep = rep + 1
        spec_idx = spec_idx + 1
    return result


def unpack(fmt: str, data: list) -> list:
    """Unpack bytes from data list according to format string. Returns list[int]."""
    specs: list = _parse_fmt(fmt)
    result: list = []
    pos: int = 0
    spec_idx: int = 0
    while spec_idx < len(specs):
        spec: _FmtSpec = specs[spec_idx]
        repeat: int = spec.repeat
        code: int = spec.code
        rep: int = 0
        while rep < repeat:
            if code == 120:  # x = skip
                pos = pos + 1
            elif code == 66:  # B = uint8
                result.append(data[pos])
                pos = pos + 1
            elif code == 98:  # b = int8
                bval: int = data[pos]
                if bval >= 128:
                    bval = bval - 256
                result.append(bval)
                pos = pos + 1
            elif code == 63:  # ? = bool
                result.append(1 if data[pos] != 0 else 0)
                pos = pos + 1
            elif code == 72:  # H = uint16
                hval: int = data[pos] | (data[pos + 1] << 8)
                result.append(hval)
                pos = pos + 2
            elif code == 104:  # h = int16
                hval2: int = data[pos] | (data[pos + 1] << 8)
                if hval2 >= 32768:
                    hval2 = hval2 - 65536
                result.append(hval2)
                pos = pos + 2
            elif code == 73:  # I = uint32
                ival: int = data[pos] | (data[pos+1] << 8) | (data[pos+2] << 16) | (data[pos+3] << 24)
                result.append(ival)
                pos = pos + 4
            elif code == 105:  # i = int32
                ival2: int = data[pos] | (data[pos+1] << 8) | (data[pos+2] << 16) | (data[pos+3] << 24)
                if ival2 >= 2147483648:
                    ival2 = ival2 - 4294967296
                result.append(ival2)
                pos = pos + 4
            elif code == 81:  # Q = uint64
                qval: int = (data[pos] | (data[pos+1] << 8) | (data[pos+2] << 16) |
                             (data[pos+3] << 24) | (data[pos+4] << 32) |
                             (data[pos+5] << 40) | (data[pos+6] << 48) | (data[pos+7] << 56))
                result.append(qval)
                pos = pos + 8
            elif code == 113:  # q = int64
                qval2: int = (data[pos] | (data[pos+1] << 8) | (data[pos+2] << 16) |
                              (data[pos+3] << 24) | (data[pos+4] << 32) |
                              (data[pos+5] << 40) | (data[pos+6] << 48) | (data[pos+7] << 56))
                if qval2 >= 9223372036854775808:
                    qval2 = qval2 - 18446744073709551616
                result.append(qval2)
                pos = pos + 8
            elif code == 115 or code == 99:  # s or c = byte value
                result.append(data[pos])
                pos = pos + 1
            rep = rep + 1
        spec_idx = spec_idx + 1
    return result


def unpack_from(fmt: str, buf: list, offset: int = 0) -> list:
    """Unpack from buf starting at offset."""
    size: int = calcsize(fmt)
    sliced: list = []
    i: int = 0
    while i < size:
        sliced.append(buf[offset + i])
        i = i + 1
    return unpack(fmt, sliced)


class Struct:
    """Pre-compiled struct format object for repeated unpack operations."""

    def __init__(self, fmt: str) -> None:
        self.format: str = fmt
        self.size: int = calcsize(fmt)

    def unpack(self, data: list) -> list:
        return unpack(self.format, data)

    def unpack_from(self, buf: list, offset: int = 0) -> list:
        return unpack_from(self.format, buf, offset)
