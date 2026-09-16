"""`long double` at compile time: exact, and encoded as x87 sees it.

WHY PYTHON'S FLOAT IS NOT ENOUGH, which is the whole reason this file is
here. A `long double` has a 64-bit significand and a Python float has 53
bits, so folding `3.14159265358979323846L` through one loses eleven bits
before the value ever reaches the initialiser -- and a program that printed
it with `%.20Lf` would see the difference. Every constant of this type is
therefore carried as a `Fraction`, which is exact, and ROUNDED TO THE FORMAT
after each operation, which is what C says an operation does.

THE ENCODING IS x87's, because the size, the alignment and the layout are
x86-64's ABI: eight bytes of significand with the leading bit EXPLICIT, then
a sixteen-bit word holding the sign and a 15-bit exponent biased by 16383,
then six bytes of padding. `support.py`'s `ldouble` unit does the same
arithmetic at run time and agrees with this by construction: both round to
nearest, ties to even, at 64 significant bits.
"""
from __future__ import annotations

import math
from fractions import Fraction

#: The significand's width, and the exponent's bias. The two numbers that
#: make this format what it is.
BITS = 64
BIAS = 16383
#: The exponent a subnormal is written with: the smallest normal's, because
#: the two encodings scale identically and only the leading bit differs.
MIN_EXP = 1 - BIAS

#: The value a program can name, for `<float.h>` and for overflow.
MAX = Fraction(2 ** BITS - 1, 2 ** BITS) * Fraction(2) ** 16384
TRUE_MIN = Fraction(1, 2 ** (BITS - 1)) * Fraction(2) ** MIN_EXP
MIN_NORMAL = Fraction(2) ** MIN_EXP
EPSILON = Fraction(1, 2 ** (BITS - 1))


def _floor_log2(value: Fraction) -> int:
    """The integer e with 2^e <= value < 2^(e+1), exactly.

    NOT `math.log2`, which goes through a float and is wrong by one at the
    ends of the range -- and the ends of the range are exactly where a
    `long double` is used.
    """
    num, den = value.numerator, value.denominator
    e = num.bit_length() - den.bit_length()
    if Fraction(num, den) < Fraction(2) ** e:
        e -= 1
    elif Fraction(num, den) >= Fraction(2) ** (e + 1):
        e += 1
    return e


def round_to(value) -> Fraction | float:
    """`value` rounded to the format: nearest, ties to even.

    Answers a `Fraction` for anything representable, and a float infinity
    for what overflows -- which is what the arithmetic produces and what
    `encode` writes. A NaN never arrives here: the folder gives up on the
    operations that make one.
    """
    if isinstance(value, float):
        if math.isinf(value) or math.isnan(value):
            return value
        if _is_negative_zero(value):
            return value
        value = Fraction(value)
    value = Fraction(value)
    if value == 0:
        return Fraction(0)
    sign = -1 if value < 0 else 1
    v = abs(value)
    e = _floor_log2(v)
    if e < MIN_EXP:
        e = MIN_EXP                 # subnormal: the exponent stops here
    scaled = v / (Fraction(2) ** (e - BITS + 1))
    m = _round_half_even(scaled)
    if m >= 2 ** BITS:
        m //= 2
        e += 1
    if e > 16383:
        return math.inf * sign
    return sign * Fraction(m) * (Fraction(2) ** (e - BITS + 1))


def _round_half_even(x: Fraction) -> int:
    floor = x.numerator // x.denominator
    rest = x - floor
    if rest > Fraction(1, 2):
        return floor + 1
    if rest < Fraction(1, 2):
        return floor
    return floor + (floor & 1)


def _is_negative_zero(value) -> bool:
    """NEGATIVE ZERO IS A FLOAT AND NOT A `Fraction`, which is the one place
    the exact form is not enough: `Fraction(-0.0)` is `Fraction(0)`, and the
    two are different `long double` objects with different sign bits. A
    value that is a Python float and compares equal to zero with a negative
    sign is that number; nothing else in this module is ever a float except
    an infinity or a NaN."""
    return (isinstance(value, float) and value == 0.0
            and math.copysign(1.0, value) < 0)


def encode(value) -> bytes:
    """The sixteen bytes a `long double` object holds."""
    if _is_negative_zero(value):
        return bytes(8) + (0x8000).to_bytes(2, "little") + bytes(6)
    if isinstance(value, float) and (math.isinf(value) or math.isnan(value)):
        if math.isnan(value):
            return (((1 << 63) | (1 << 62)).to_bytes(8, "little")
                    + (0x7fff).to_bytes(2, "little") + bytes(6))
        se = 0x7fff | (0x8000 if value < 0 else 0)
        return ((1 << 63).to_bytes(8, "little") + se.to_bytes(2, "little")
                + bytes(6))
    value = Fraction(value)
    if value == 0:
        return bytes(16)
    sign = 1 if value < 0 else 0
    v = abs(value)
    e = _floor_log2(v)
    if e < MIN_EXP:
        e = MIN_EXP
    m = int(v / (Fraction(2) ** (e - BITS + 1)))
    if m >= 2 ** BITS:                  # a rounded-up significand
        m //= 2
        e += 1
    if e > 16383:
        se = 0x7fff | (sign << 15)
        return ((1 << 63).to_bytes(8, "little") + se.to_bytes(2, "little")
                + bytes(6))
    # A SUBNORMAL IS WRITTEN WITH EXPONENT ZERO, which is the same scale as
    # exponent one and is how the format says "no leading bit".
    biased = 0 if m < 2 ** (BITS - 1) else e + BIAS
    if biased < 0:
        return bytes(16)
    se = (biased & 0x7fff) | (sign << 15)
    return (m.to_bytes(8, "little") + se.to_bytes(2, "little") + bytes(6))


def from_decimal(text: str) -> Fraction:
    """A decimal or hexadecimal floating constant, exactly.

    `float(text)` would round it to a double first, which is eleven bits
    short of what this type holds -- and the constants people write at this
    width are exactly the ones where those bits matter.
    """
    t = text.strip()
    if t[:2].lower() == "0x":
        body = t[2:]
        exp = 0
        if "p" in body.lower():
            at = body.lower().index("p")
            exp = int(body[at + 1:])
            body = body[:at]
        if "." in body:
            whole, _, frac = body.partition(".")
        else:
            whole, frac = body, ""
        digits = int(whole + frac, 16) if (whole + frac) else 0
        return Fraction(digits) * Fraction(2) ** (exp - 4 * len(frac))
    return Fraction(t)
