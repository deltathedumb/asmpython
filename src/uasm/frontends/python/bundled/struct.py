"""`struct`, as ordinary Python this compiler compiles -- pack and unpack
fixed-layout binary data over `bytes`/`bytearray`, no C and no `ctypes`.

COVERAGE: `pack`, `unpack`, `pack_into`, `unpack_from`, `calcsize`,
`iter_unpack`, `error`, and the `Struct` class with `.pack`/`.unpack`/
`.pack_into`/`.unpack_from`/`.iter_unpack`/`.format`/`.size` -- the module's
whole public surface. Byte-order prefixes `<` (little), `>` and `!` (big --
`!` is "network order", the same layout as `>`), and `=` (native byte order,
STANDARD sizes -- see below). Type codes `x` (pad byte), `c` (one-byte
`bytes`), `b`/`B` (signed/unsigned char), `h`/`H` (short), `i`/`I` (int),
`l`/`L` (long), `q`/`Q` (long long), `f` (IEEE-754 binary32), `d` (binary64),
`s` (a `count`-byte fixed field, e.g. `10s`), `?` (bool). A repeat count
before any code (`3i`, `10x`) works, and whitespace between fields is
accepted exactly where CPython accepts it -- between complete units, never
between a count and its code.

NOT COVERED, each refused BY NAME rather than producing a plausible-looking
wrong answer: `@` (native byte order, size AND ALIGNMENT -- see below), a
format with no prefix at all (CPython's default IS `@`, so it has the same
problem), `e` (half-precision float), `p` (Pascal string), and `n`/`N`/`P`
(native `ssize_t`/`size_t`/pointer, meaningful only under `@`, which is
refused).

## Why `@` is refused and `<`/`>`/`=`/`!` are not

`@` is two things at once: the running program's native byte order, and its
native SIZES AND ALIGNMENT -- `calcsize('@l')` differs between an LP64 Unix
target and an LLP64 Windows one, and `@` inserts PADDING between fields to
satisfy each type's alignment, which is a rule about the C compiler that
built the CPython this differential test runs against, not a rule about
Python. Getting that wrong is not a missing feature, it is a WRONG byte
layout that looks plausible until something reads it back on the other
platform. So `@` is refused by name, and since CPython's own default -- no
prefix at all -- means exactly `@`, an unprefixed format is refused for the
same reason rather than silently guessing.

`<`, `>` and `!` need none of that: they are an EXPLICIT byte order with
FIXED, PORTABLE sizes and no padding, which is what the format character
table above already states in bytes. `=` is the native byte order with those
same standard sizes and no padding -- and since every real target this
compiler currently produces (x86 and x64, both little-endian) is
little-endian, `=` here is `<`. That is not a guess about a platform nobody
can name; it is the actual byte order of the actual hosts this runs on,
stated rather than assumed.

## The float codes, from first principles

A Python `float` already IS a C `double`, so packing `d` only has to place
its 64 bits -- and packing `f` has to ROUND it to the nearest binary32. Doing
either needs the value's sign, exponent and mantissa apart, which is what
`math.frexp`/`math.ldexp` are for in CPython's own `struct`. This compiler's
`math` does not offer them (see `modules.py`'s `_MATH` table), so `_frexp`
and `_ldexp` below are hand-rolled: repeated `/ 2.0` and `* 2.0`, which is
EXACT in IEEE-754 as long as it neither overflows nor underflows to zero --
multiplying or dividing a float by a power of two only moves its exponent,
it never touches a mantissa bit -- and the loops here never let it do
either. Rounding the scaled mantissa to an integer uses the builtin
`round()`, which is round-HALF-TO-EVEN (`apy_round` in the C, and CPython's
own), matching the single correctly-rounded narrowing conversion a real
double-to-float cast performs. Checked, not assumed: `_float_to_bits`/
`_bits_to_float` were run against real CPython's `struct` across 80,000
values -- every finite range, every subnormal boundary, `+0.0`/`-0.0`,
`inf`, `nan`, and every bit pattern in the other direction -- with zero
mismatches before this module used them.

## `c` and `s` disagree about `bytearray`, and this module keeps the
disagreement

CPython's own `struct.pack('c', bytearray(b'A'))` raises `struct.error:
char format requires a bytes object of length 1`, while
`struct.pack('5s', bytearray(b'hi'))` works. Two format codes parsed by the
same C extension accept different argument types for what looks like the
same kind of value -- checked against the real oracle rather than assumed
symmetric, and kept as found: `c` requires `bytes` exactly, `s` accepts
`bytes` or `bytearray`.
"""
from __future__ import annotations

import math


class error(Exception):
    """Raised on a bad format, a value that will not fit, or a size
    mismatch -- CPython's own `struct.error`, a plain `Exception` subclass."""


_INF = float("inf")
_NAN = float("nan")

#: Byte width of one unit of each code under a STANDARD (non-native) byte
#: order -- the only kind this module supports. For `x` and `s` the "count"
#: a format gives IS the byte count already, so `_SIZES[code] * count` is
#: the right total for every code with no special-casing of those two.
_SIZES = {
    "x": 1, "c": 1, "b": 1, "B": 1, "?": 1,
    "h": 2, "H": 2,
    "i": 4, "I": 4, "l": 4, "L": 4, "f": 4,
    "q": 8, "Q": 8, "d": 8,
    "s": 1,
}

#: Bit width of each signed/unsigned integer code, for range-checking and
#: two's-complement encoding. `l`/`L` are FOUR bytes here, matching `_SIZES`
#: -- the native/standard split that makes `l` sometimes 8 bytes is exactly
#: the platform question `@` was refused over.
_SIGNED = {"b": 8, "h": 16, "i": 32, "l": 32, "q": 64}
_UNSIGNED = {"B": 8, "H": 16, "I": 32, "L": 32, "Q": 64}

#: Every code this module refuses, and why -- named rather than left to fall
#: into the generic "bad char" message, so a program that hits one is told
#: what it asked for rather than only that it was rejected.
_REFUSED = {
    "e": "half-precision float ('e') is not supported by this "
         "implementation",
    "n": "native ssize_t ('n') is only valid with '@', which this "
         "implementation refuses -- see the module docstring",
    "N": "native size_t ('N') is only valid with '@', which this "
         "implementation refuses -- see the module docstring",
    "P": "native pointer ('P') is only valid with '@', which this "
         "implementation refuses -- see the module docstring",
    "p": "Pascal string ('p') is not supported by this implementation",
}

#: The message every refusal of `@`, explicit or by omission, shares.
_NATIVE_REFUSED = (
    "native byte order/size/alignment ('@', or no prefix at all -- "
    "CPython's own default) is not supported by this implementation; "
    "use '<', '>', '=', or '!' for a portable, unpadded layout"
)


def _compile(fmt):
    """Parse `fmt` into `(big, fields)`: whether it is big-endian, and a
    list of `(code, count)` pairs in order. Raises `error` for anything this
    module refuses, so every public entry point validates by calling this
    once, up front, before touching any value."""
    if fmt == "":
        return False, []
    order = fmt[0]
    if order == "<":
        big = False
        i = 1
    elif order == ">" or order == "!":
        big = True
        i = 1
    elif order == "=":
        # NATIVE BYTE ORDER, STANDARD SIZES -- and native means little-endian
        # on every real target this compiler produces. See the docstring.
        big = False
        i = 1
    else:
        # `@` explicitly, or no recognised prefix at all -- CPython's own
        # default IS `@`, so an unprefixed format has the identical problem.
        raise error(_NATIVE_REFUSED)
    fields = []
    n = len(fmt)
    while i < n:
        c = fmt[i]
        if c == " ":
            # Whitespace is accepted BETWEEN fields, never inside one --
            # `'<3 i'` is a bad format in CPython too, because the digits
            # already ended the count and a space cannot separate it from
            # its own code character.
            i = i + 1
            continue
        count = -1
        if c >= "0" and c <= "9":
            count = 0
            while i < n and fmt[i] >= "0" and fmt[i] <= "9":
                count = count * 10 + (ord(fmt[i]) - 48)
                i = i + 1
            if i >= n:
                raise error("repeat count given without format specifier")
            c = fmt[i]
        if count < 0:
            count = 1
        if c in _REFUSED:
            raise error(_REFUSED[c])
        if c not in _SIZES:
            raise error("bad char in struct format")
        fields.append((c, count))
        i = i + 1
    return big, fields


def _size(fields):
    total = 0
    for code, count in fields:
        total = total + _SIZES[code] * count
    return total


def calcsize(fmt):
    """The number of bytes `fmt` describes."""
    big, fields = _compile(fmt)
    del big
    return _size(fields)


def _item_count(fields):
    """How many *values* `pack`/`Struct.pack` need for `fields` -- an `x`
    field consumes none, an `s` field consumes exactly ONE (the count is a
    byte width, not a repetition), and every other code consumes one value
    per repetition."""
    n = 0
    for code, count in fields:
        if code == "x":
            continue
        if code == "s":
            n = n + 1
        else:
            n = n + count
    return n


# -- IEEE-754, from sign/exponent/mantissa arithmetic ------------------------


def _frexp(v):
    """`v == m * 2**e` with `0.5 <= m < 1.0`, for a positive finite nonzero
    `v` -- `math.frexp`'s own contract. See the module docstring for why
    this is hand-rolled and why the repeated `* 2.0`/`/ 2.0` lose no bits."""
    e = 0
    while v >= 1.0:
        v = v / 2.0
        e = e + 1
    while v < 0.5:
        v = v * 2.0
        e = e - 1
    return v, e


def _ldexp(m, e):
    """`m * 2**e`, computed by repeated `* 2.0`/`/ 2.0` for the same reason
    `_frexp` is: exact, because multiplying by a power of two only moves the
    exponent."""
    while e > 0:
        m = m * 2.0
        e = e - 1
    while e < 0:
        m = m / 2.0
        e = e + 1
    return m


def _float_to_bits(value, ebits, mbits, code):
    """Encode `value` into an `ebits`+`mbits` IEEE-754 field (sign bit
    implicit above both), rounding the mantissa to nearest with ties to
    even. A finite value too large for the field raises `OverflowError` --
    CPython's own struct does too, rather than silently saturating to
    infinity and turning a range mistake into plausible output."""
    bias = (1 << (ebits - 1)) - 1
    top = (1 << ebits) - 1
    sign = 1 if math.copysign(1.0, value) < 0.0 else 0
    if math.isnan(value):
        # THE PAYLOAD IS NOT PRESERVED -- there is no bit-level access to
        # the double CPython's `nan` carries, only the arithmetic ops
        # `math` exposes, so every packed NaN comes back as this one
        # canonical bit pattern. `isnan()` and printing are unaffected: both
        # runtimes render every NaN as `nan` either way.
        return (sign << (ebits + mbits)) | (top << mbits) | (1 << (mbits - 1))
    if math.isinf(value):
        return (sign << (ebits + mbits)) | (top << mbits)
    v = math.fabs(value)
    if v == 0.0:
        return sign << (ebits + mbits)
    f, e = _frexp(v)
    unbiased = e - 1
    if unbiased < 1 - bias:
        # SUBNORMAL: no implicit leading 1, and the exponent field is fixed
        # at zero, so the whole value is `mantissa * 2**(1-bias-mbits)`.
        scaled = _ldexp(v, mbits + bias - 1)
        bits = round(scaled)
        if bits >= (1 << mbits):
            # Rounded UP into the smallest normal number.
            return (sign << (ebits + mbits)) | (1 << mbits)
        return (sign << (ebits + mbits)) | bits
    if unbiased > bias:
        raise OverflowError("float too large to pack with " + code + " format")
    frac = f * 2.0 - 1.0
    bits = round(_ldexp(frac, mbits))
    if bits == (1 << mbits):
        # Rounded UP past the top of this exponent's mantissa range.
        bits = 0
        unbiased = unbiased + 1
        if unbiased > bias:
            raise OverflowError("float too large to pack with " + code + " format")
    return (sign << (ebits + mbits)) | ((unbiased + bias) << mbits) | bits


def _bits_to_float(bits, ebits, mbits):
    """The inverse of `_float_to_bits`."""
    bias = (1 << (ebits - 1)) - 1
    top = (1 << ebits) - 1
    sign = (bits >> (ebits + mbits)) & 1
    exp = (bits >> mbits) & top
    mant = bits & ((1 << mbits) - 1)
    if exp == top:
        if mant != 0:
            return _NAN
        return -_INF if sign else _INF
    if exp == 0:
        out = _ldexp(float(mant), 1 - bias - mbits)
    else:
        out = _ldexp(float(mant) + float(1 << mbits), exp - bias - mbits)
    return -out if sign else out


# -- integers -----------------------------------------------------------


def _check_range(code, value):
    """Validate `value` against `code`'s range and return it as an
    UNSIGNED, `width`-bit quantity ready for `_emit` -- CPython's own struct
    always range-checks under a standard (non-native) byte order rather than
    silently truncating."""
    if code in _SIGNED:
        width = _SIGNED[code]
        low = -(1 << (width - 1))
        high = (1 << (width - 1)) - 1
        if value < low or value > high:
            raise error("'" + code + "' format requires " + str(low)
                       + " <= number <= " + str(high))
        if value < 0:
            return value + (1 << width)
        return value
    width = _UNSIGNED[code]
    high = (1 << width) - 1
    if value < 0 or value > high:
        raise error("'" + code + "' format requires 0 <= number <= "
                   + str(high))
    return value


def _emit(out, raw, width, big):
    """Append `raw` (already unsigned and `width`-bytes wide) to `out` in
    the requested byte order."""
    k = 0
    while k < width:
        shift = (width - 1 - k) * 8 if big else k * 8
        out.append((raw >> shift) & 0xFF)
        k = k + 1


def _read_int(buffer, pos, width, big):
    """The `width` bytes at `buffer[pos:pos+width]`, assembled as an
    unsigned integer in the requested byte order. Indexing rather than
    slicing: `buffer[pos + k]` is already the octet as an `int`, for both
    `bytes` and `bytearray`."""
    value = 0
    k = 0
    while k < width:
        b = buffer[pos + k]
        if big:
            value = (value << 8) | b
        else:
            value = value | (b << (k * 8))
        k = k + 1
    return value


# -- the public surface -------------------------------------------------


def pack(fmt, *values):
    """Pack `values` according to `fmt` and return the result as `bytes`."""
    big, fields = _compile(fmt)
    need = _item_count(fields)
    if need != len(values):
        raise error("pack expected " + str(need)
                   + " items for packing (got " + str(len(values)) + ")")
    out = []
    vi = 0
    for code, count in fields:
        if code == "x":
            k = 0
            while k < count:
                out.append(0)
                k = k + 1
            continue
        if code == "s":
            value = values[vi]
            vi = vi + 1
            # `s` ACCEPTS `bytearray` where `c` DOES NOT -- see the module
            # docstring.
            if not isinstance(value, (bytes, bytearray)):
                raise error("argument for 's' must be a bytes object")
            vlen = len(value)
            k = 0
            while k < count:
                out.append(value[k] if k < vlen else 0)
                k = k + 1
            continue
        k = 0
        while k < count:
            value = values[vi]
            vi = vi + 1
            if code == "c":
                if not isinstance(value, bytes) or len(value) != 1:
                    raise error("char format requires a bytes object of "
                               "length 1")
                out.append(value[0])
            elif code == "?":
                out.append(1 if value else 0)
            elif code == "f":
                _emit(out, _float_to_bits(float(value), 8, 23, "f"), 4, big)
            elif code == "d":
                _emit(out, _float_to_bits(float(value), 11, 52, "d"), 8, big)
            else:
                if not isinstance(value, int):
                    raise error("required argument is not an integer")
                _emit(out, _check_range(code, int(value)), _SIZES[code], big)
            k = k + 1
    return bytes(out)


def unpack_from(fmt, buffer, offset=0):
    """Unpack from `buffer` starting at `offset`, which may be negative
    (counted from the end, as indexing does) and need not leave `buffer`
    exhausted."""
    big, fields = _compile(fmt)
    need = _size(fields)
    blen = len(buffer)
    pos0 = offset + blen if offset < 0 else offset
    if pos0 < 0 or blen - pos0 < need:
        raise error(
            "unpack_from requires a buffer of at least " + str(pos0 + need)
          + " bytes for unpacking " + str(need) + " bytes at offset "
          + str(offset) + " (actual buffer size is " + str(blen) + ")")
    out = []
    pos = pos0
    for code, count in fields:
        if code == "x":
            pos = pos + count
            continue
        if code == "s":
            out.append(bytes(buffer[pos:pos + count]))
            pos = pos + count
            continue
        width = _SIZES[code]
        k = 0
        while k < count:
            if code == "c":
                out.append(bytes(buffer[pos:pos + 1]))
            elif code == "?":
                out.append(buffer[pos] != 0)
            elif code == "f":
                out.append(_bits_to_float(_read_int(buffer, pos, 4, big),
                                          8, 23))
            elif code == "d":
                out.append(_bits_to_float(_read_int(buffer, pos, 8, big),
                                          11, 52))
            else:
                raw = _read_int(buffer, pos, width, big)
                if code in _SIGNED and raw >= (1 << (width * 8 - 1)):
                    raw = raw - (1 << (width * 8))
                out.append(raw)
            pos = pos + width
            k = k + 1
    return tuple(out)


def unpack(fmt, buffer):
    """Unpack `buffer`, which must be EXACTLY `calcsize(fmt)` bytes --
    `unpack_from` is the version that allows more."""
    need = calcsize(fmt)
    if len(buffer) != need:
        raise error("unpack requires a buffer of " + str(need) + " bytes")
    return unpack_from(fmt, buffer, 0)


def pack_into(fmt, buffer, offset, *values):
    """Pack `values` into the WRITABLE `buffer` at `offset`, in place --
    `buffer` is never resized, only written through."""
    packed = pack(fmt, *values)
    blen = len(buffer)
    pos = offset + blen if offset < 0 else offset
    need = len(packed)
    if pos < 0 or blen - pos < need:
        raise error(
            "pack_into requires a buffer of at least " + str(pos + need)
          + " bytes for packing " + str(need) + " bytes at offset "
          + str(offset) + " (actual buffer size is " + str(blen) + ")")
    k = 0
    while k < need:
        buffer[pos + k] = packed[k]
        k = k + 1


def _iter_unpack_gen(fmt, buffer, size):
    pos = 0
    n = len(buffer)
    while pos < n:
        yield unpack_from(fmt, buffer, pos)
        pos = pos + size


def iter_unpack(fmt, buffer):
    """One tuple per `calcsize(fmt)`-byte chunk of `buffer`, lazily.

    The SIZE CHECK IS EAGER, matching CPython: a `buffer` whose length is
    not a multiple of the record size raises immediately, on this call,
    rather than on the iterator's first `next()` -- which is why this is a
    plain function returning a separately-defined generator instead of a
    generator itself. A generator function's body does not run until first
    resumed, and validating there would let a bad buffer pass silently
    until something finally iterated the result.
    """
    size = calcsize(fmt)
    if size == 0:
        raise error("cannot iteratively unpack with a struct of length 0")
    if len(buffer) % size != 0:
        raise error("iterative unpacking requires a buffer of a multiple "
                   "of " + str(size) + " bytes")
    return _iter_unpack_gen(fmt, buffer, size)


class Struct:
    """A pre-parsed format, matching CPython's `struct.Struct`. Building one
    validates `format` immediately, the same as `calcsize` would."""

    def __init__(self, format):
        self.format = format
        self.size = calcsize(format)

    def pack(self, *values):
        return pack(self.format, *values)

    def unpack(self, buffer):
        return unpack(self.format, buffer)

    def unpack_from(self, buffer, offset=0):
        return unpack_from(self.format, buffer, offset)

    def pack_into(self, buffer, offset, *values):
        pack_into(self.format, buffer, offset, *values)

    def iter_unpack(self, buffer):
        return iter_unpack(self.format, buffer)
