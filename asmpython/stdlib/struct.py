"""struct module: interpret bytes as packed binary data.

Mirrors CPython: `pack` returns `bytes`, `unpack` returns a `tuple`, byte-order
prefixes are honoured, and out-of-range values / bad formats / wrong buffer
sizes raise `struct.error` rather than silently producing wrong bytes.

Format characters: x c b B ? h H i I l L q Q n N e f d s p P
Byte order:       @ (native, aligned)  = (native, standard sizes)
                  < (little)  > (big)  ! (network, big)

Floats are encoded from first principles via frexp/ldexp rather than by
delegating to a C `pack`, so `e` (half), `f` (single) and `d` (double) all round
the way IEEE-754 requires without needing a helper the compiler does not have.
"""
from __future__ import annotations

import math


class error(Exception):
    """Raised on a bad format, a value that will not fit, or a size mismatch."""


# Standard sizes, used for every byte order except '@'.
_STD_SIZE = {
    "x": 1, "c": 1, "b": 1, "B": 1, "?": 1,
    "h": 2, "H": 2, "e": 2,
    "i": 4, "I": 4, "l": 4, "L": 4, "f": 4,
    "q": 8, "Q": 8, "d": 8, "n": 8, "N": 8, "P": 8,
    "s": 1, "p": 1,
}

# Native sizes/alignments for '@'. C `long` is 4 bytes here, not 8: Windows is
# LLP64, and the standard-size table agrees, so `@l` and `<l` match. (A LP64
# Unix target widens `long` to 8; `q`/`Q` are the portable 64-bit codes.)
_NAT_SIZE = {
    "x": 1, "c": 1, "b": 1, "B": 1, "?": 1,
    "h": 2, "H": 2, "e": 2,
    "i": 4, "I": 4, "f": 4, "l": 4, "L": 4,
    "q": 8, "Q": 8, "d": 8, "n": 8, "N": 8, "P": 8,
    "s": 1, "p": 1,
}

# Signed/unsigned integer codes and their bit widths, for range checking.
_SIGNED = {"b": 8, "h": 16, "i": 32, "l": 32, "q": 64, "n": 64}
_UNSIGNED = {"B": 8, "H": 16, "I": 32, "L": 32, "Q": 64, "N": 64, "P": 64}

# Exponent/mantissa widths, kept as two int-valued dicts rather than one dict
# of tuples: a dict value must be a scalar here, and a tuple value silently
# read back as zero.
_FLOAT_EBITS = {"e": 5, "f": 8, "d": 11}
_FLOAT_MBITS = {"e": 10, "f": 23, "d": 52}

_INF = float("inf")
_NAN = float("nan")


class _Field:
    """One `code` repeated `repeat` times. A class, not a [code, repeat] list:
    asmpython has no tagged-value runtime, so a list may not mix str and int."""

    def __init__(self, code: str, repeat: int) -> None:
        self.code = code
        self.repeat = repeat


class _Format:
    """A parsed format: its byte order, its alignment mode, and its fields."""

    def __init__(self, big: bool, native: bool, fields: list) -> None:
        self.big = big
        self.native = native
        self.fields = fields


def _is_format_char(c: str) -> bool:
    return c in _STD_SIZE or c in _FLOAT_EBITS


def _parse(fmt: str) -> _Format:
    """Parse a format into its byte order, alignment mode, and field list.

    `s` and `p` keep their repeat count as a byte count rather than a
    multiplicity, exactly as CPython reads them.
    """
    if len(fmt) == 0:
        return _Format(False, True, [])
    order = fmt[0]
    start = 0
    big = False
    native = True
    if order == "<":
        big = False
        native = False
        start = 1
    elif order == ">" or order == "!":
        big = True
        native = False
        start = 1
    elif order == "=":
        big = False
        native = False
        start = 1
    elif order == "@":
        big = False
        native = True
        start = 1
    fields: list = []
    i = start
    n = len(fmt)
    while i < n:
        c = fmt[i]
        if c == " ":
            i = i + 1
            continue
        repeat = -1
        if c >= "0" and c <= "9":
            repeat = 0
            while i < n and fmt[i] >= "0" and fmt[i] <= "9":
                repeat = repeat * 10 + (ord(fmt[i]) - 48)
                i = i + 1
            if i >= n:
                raise error("repeat count given without format specifier")
            c = fmt[i]
        if repeat < 0:
            repeat = 1
        if not _is_format_char(c):
            raise error("bad char in struct format: '" + c + "'")
        if not native and (c == "n" or c == "N" or c == "P"):
            # CPython offers ssize_t/size_t/void* only in native mode: their
            # width is the platform's, which a fixed byte order does not pin.
            raise error("bad char in struct format: '" + c + "'")
        fields.append(_Field(c, repeat))
        i = i + 1
    return _Format(big, native, fields)


def _size_of(code: str, native: bool) -> int:
    if native:
        return _NAT_SIZE[code]
    return _STD_SIZE[code]


def _pad_for(code: str, native: bool, offset: int) -> int:
    """Bytes of padding '@' inserts before `code` to satisfy its alignment."""
    if not native:
        return 0
    if code == "s" or code == "p" or code == "x" or code == "c":
        return 0
    align = _NAT_SIZE[code]
    rem = offset % align
    if rem == 0:
        return 0
    return align - rem


def calcsize(fmt: str) -> int:
    """Size in bytes of the struct described by `fmt`."""
    parsed = _parse(fmt)
    big = parsed.big
    native = parsed.native
    items = parsed.fields
    total = 0
    idx = 0
    while idx < len(items):
        code = items[idx].code
        repeat = items[idx].repeat
        if code == "s" or code == "p":
            total = total + repeat
        else:
            unit = _size_of(code, native)
            rep = 0
            while rep < repeat:
                total = total + _pad_for(code, native, total) + unit
                rep = rep + 1
        idx = idx + 1
    del big
    return total


# -- IEEE-754 ---------------------------------------------------------------


def _float_to_bits(value: float, ebits: int, mbits: int, code: str = "d") -> int:
    """Encode `value` into an `ebits`+`mbits` IEEE-754 field, ties to even.

    A finite value too large for the field raises `OverflowError`, which is
    what CPython does -- silently saturating to infinity would turn a range
    mistake into plausible-looking output.
    """
    bias = (1 << (ebits - 1)) - 1
    sign = 0
    v = float(value)
    if math.copysign(1.0, v) < 0.0:
        sign = 1
        v = -v
    top = (1 << ebits) - 1
    if math.isnan(v):
        return (sign << (ebits + mbits)) | (top << mbits) | (1 << (mbits - 1))
    if math.isinf(v):
        return (sign << (ebits + mbits)) | (top << mbits)
    if v == 0.0:
        return sign << (ebits + mbits)
    exp = math.frexp_exponent(v)
    mant = math.frexp_mantissa(v)
    unbiased = exp - 1
    if unbiased < 1 - bias:
        # Subnormal: no implicit leading 1, fixed exponent.
        scaled = math.ldexp(v, mbits + bias - 1)
        bits = int(round(scaled))
        if bits >= (1 << mbits):
            return (sign << (ebits + mbits)) | (1 << mbits)
        return (sign << (ebits + mbits)) | bits
    if unbiased > bias:
        raise OverflowError("float too large to pack with " + code + " format")
    frac = mant * 2.0 - 1.0
    bits = int(round(math.ldexp(frac, mbits)))
    if bits == (1 << mbits):
        bits = 0
        unbiased = unbiased + 1
        if unbiased > bias:
            raise OverflowError("float too large to pack with " + code + " format")
    return (sign << (ebits + mbits)) | ((unbiased + bias) << mbits) | bits


def _bits_to_float(bits: int, ebits: int, mbits: int) -> float:
    """Decode an `ebits`+`mbits` IEEE-754 field."""
    bias = (1 << (ebits - 1)) - 1
    top = (1 << ebits) - 1
    sign = (bits >> (ebits + mbits)) & 1
    exp = (bits >> mbits) & top
    mant = bits & ((1 << mbits) - 1)
    if exp == top:
        if mant != 0:
            return _NAN
        if sign == 1:
            return -_INF
        return _INF
    if exp == 0:
        out = math.ldexp(float(mant), 1 - bias - mbits)
    else:
        out = math.ldexp(float(mant) + float(1 << mbits), exp - bias - mbits)
    if sign == 1:
        return -out
    return out


# -- integer helpers --------------------------------------------------------


def _check_range(code: str, value: int) -> int:
    """Validate `value` against `code`'s range, as CPython's struct does."""
    if code in _SIGNED:
        width = _SIGNED[code]
        low = -(1 << (width - 1))
        high = (1 << (width - 1)) - 1
        if value < low or value > high:
            raise error("argument out of range")
        if value < 0:
            return value + (1 << width)
        return value
    width = _UNSIGNED[code]
    if value < 0:
        raise error("argument out of range")
    if value > (1 << width) - 1:
        raise error("argument out of range")
    return value


def _emit(out: list[int], raw: int, width: int, big: bool) -> None:
    """Append `raw` as `width` bytes in the requested order."""
    i = 0
    while i < width:
        if big:
            shift = (width - 1 - i) * 8
        else:
            shift = i * 8
        out.append((raw >> shift) & 0xFF)
        i = i + 1


def _read(data: list[int], pos: int, width: int, big: bool) -> int:
    value = 0
    i = 0
    while i < width:
        if big:
            value = (value << 8) | data[pos + i]
        else:
            value = (value << 8) | data[pos + width - 1 - i]
        i = i + 1
    return value


def _as_bytes(value) -> list:
    """Normalize a bytes/bytearray/str argument for the `s` and `p` codes."""
    out: list[int] = []
    i = 0
    while i < len(value):
        item = value[i]
        if isinstance(item, str):
            out.append(ord(item) & 0xFF)
        else:
            out.append(item & 0xFF)
        i = i + 1
    return out


# -- the public surface -----------------------------------------------------


def pack(fmt: str, *args) -> bytes:
    """Return `args` packed according to `fmt`, as `bytes`."""
    parsed = _parse(fmt)
    big = parsed.big
    native = parsed.native
    items = parsed.fields
    out: list[int] = []
    argi = 0
    idx = 0
    while idx < len(items):
        code = items[idx].code
        repeat = items[idx].repeat
        if code == "x":
            rep = 0
            while rep < repeat:
                out.append(0)
                rep = rep + 1
            idx = idx + 1
            continue
        if code == "s":
            if argi >= len(args):
                raise error("pack expected more arguments")
            raw = _as_bytes(args[argi])
            argi = argi + 1
            i = 0
            while i < repeat:
                if i < len(raw):
                    out.append(raw[i])
                else:
                    out.append(0)
                i = i + 1
            idx = idx + 1
            continue
        if code == "p":
            if argi >= len(args):
                raise error("pack expected more arguments")
            raw = _as_bytes(args[argi])
            argi = argi + 1
            count = len(raw)
            if count > repeat - 1:
                count = repeat - 1
            if count < 0:
                count = 0
            out.append(count & 0xFF)
            i = 0
            while i < repeat - 1:
                if i < count:
                    out.append(raw[i])
                else:
                    out.append(0)
                i = i + 1
            idx = idx + 1
            continue
        unit = _size_of(code, native)
        rep = 0
        while rep < repeat:
            pad = _pad_for(code, native, len(out))
            p = 0
            while p < pad:
                out.append(0)
                p = p + 1
            if argi >= len(args):
                raise error("pack expected more arguments")
            value = args[argi]
            argi = argi + 1
            if code == "c":
                raw = _as_bytes(value)
                if len(raw) != 1:
                    raise error("char format requires a bytes object of length 1")
                out.append(raw[0])
            elif code == "?":
                if value:
                    out.append(1)
                else:
                    out.append(0)
            elif code in _FLOAT_EBITS:
                fbits = _float_to_bits(
                    float(value), _FLOAT_EBITS[code], _FLOAT_MBITS[code], code
                )
                _emit(out, fbits, unit, big)
            else:
                _emit(out, _check_range(code, int(value)), unit, big)
            rep = rep + 1
        idx = idx + 1
    if argi != len(args):
        raise error("pack expected fewer arguments")
    return bytes(out)


def unpack_from(fmt: str, buffer: list, offset: int = 0) -> tuple:
    """Unpack from `buffer` starting at `offset`."""
    parsed = _parse(fmt)
    big = parsed.big
    native = parsed.native
    items = parsed.fields
    need = calcsize(fmt)
    if offset < 0:
        offset = len(buffer) + offset
    if offset < 0 or len(buffer) - offset < need:
        raise error("unpack_from requires a buffer of at least " + str(need) + " bytes")
    out: list[object] = []
    pos = offset
    base = offset
    idx = 0
    while idx < len(items):
        code = items[idx].code
        repeat = items[idx].repeat
        if code == "x":
            pos = pos + repeat
            idx = idx + 1
            continue
        if code == "s":
            chunk: list[int] = []
            i = 0
            while i < repeat:
                chunk.append(buffer[pos + i])
                i = i + 1
            out.append(bytes(chunk))
            pos = pos + repeat
            idx = idx + 1
            continue
        if code == "p":
            count = buffer[pos]
            if count > repeat - 1:
                count = repeat - 1
            chunk = []
            i = 0
            while i < count:
                chunk.append(buffer[pos + 1 + i])
                i = i + 1
            out.append(bytes(chunk))
            pos = pos + repeat
            idx = idx + 1
            continue
        unit = _size_of(code, native)
        rep = 0
        while rep < repeat:
            pos = pos + _pad_for(code, native, pos - base)
            raw = _read(buffer, pos, unit, big)
            if code == "c":
                out.append(bytes([raw]))
            elif code == "?":
                out.append(raw != 0)
            elif code in _FLOAT_EBITS:
                out.append(
                    _bits_to_float(raw, _FLOAT_EBITS[code], _FLOAT_MBITS[code])
                )
            elif code in _SIGNED:
                width = _SIGNED[code]
                if raw >= (1 << (width - 1)):
                    raw = raw - (1 << width)
                out.append(raw)
            else:
                out.append(raw)
            pos = pos + unit
            rep = rep + 1
        idx = idx + 1
    return tuple(out)


def unpack(fmt: str, buffer) -> tuple:
    """Unpack `buffer`, which must be exactly `calcsize(fmt)` bytes."""
    need = calcsize(fmt)
    if len(buffer) != need:
        raise error(
            "unpack requires a buffer of " + str(need) + " bytes"
        )
    return unpack_from(fmt, buffer, 0)


def pack_into(fmt: str, buffer: list, offset: int, *args) -> None:
    """Pack `args` into a writable `buffer` at `offset`, in place."""
    packed = pack(fmt, *args)
    if offset < 0:
        offset = len(buffer) + offset
    if offset < 0 or len(buffer) - offset < len(packed):
        raise error(
            "pack_into requires a buffer of at least "
            + str(len(packed) + offset)
            + " bytes"
        )
    i = 0
    while i < len(packed):
        buffer[offset + i] = packed[i]
        i = i + 1


def iter_unpack(fmt: str, buffer) -> list:
    """Unpack repeatedly, yielding one tuple per `calcsize(fmt)` chunk."""
    size = calcsize(fmt)
    if size == 0:
        raise error("cannot iteratively unpack with a struct of length 0")
    if len(buffer) % size != 0:
        raise error("iter_unpack requires a buffer of a multiple of " + str(size))
    out: list[int] = []
    pos = 0
    while pos < len(buffer):
        out.append(unpack_from(fmt, buffer, pos))
        pos = pos + size
    return out


class Struct:
    """A compiled format, matching CPython's `struct.Struct`."""

    def __init__(self, format: str) -> None:
        self.format = format
        self.size = calcsize(format)

    def pack(self, *args) -> bytes:
        return pack(self.format, *args)

    def unpack(self, buffer) -> tuple:
        return unpack(self.format, buffer)

    def unpack_from(self, buffer, offset: int = 0) -> tuple:
        return unpack_from(self.format, buffer, offset)

    def pack_into(self, buffer, offset: int, *args) -> None:
        pack_into(self.format, buffer, offset, *args)

    def iter_unpack(self, buffer) -> list:
        return iter_unpack(self.format, buffer)
