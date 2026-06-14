"""decimal module: fixed-precision decimal arithmetic.

Implements Decimal as a (coefficient: int, exponent: int) pair where the
value equals coefficient * 10^exponent. Precision defaults to 28 digits.
"""
from __future__ import annotations


_DEFAULT_PREC: int = 28


def _int_pow10(n: int) -> int:
    """Return 10^n for small n."""
    result: int = 1
    i: int = 0
    while i < n:
        result = result * 10
        i = i + 1
    return result


def _int_log10_floor(n: int) -> int:
    """Return floor(log10(abs(n))), minimum 0."""
    if n < 0:
        n = -n
    if n == 0:
        return 0
    count: int = 0
    while n >= 10:
        n = n // 10
        count = count + 1
    return count


def _align(a_coef: int, a_exp: int, b_coef: int, b_exp: int) -> list:
    """Return [a_coef', b_coef', common_exp] with both scaled to same exponent."""
    if a_exp == b_exp:
        return [a_coef, b_coef, a_exp]
    if a_exp < b_exp:
        shift: int = b_exp - a_exp
        return [a_coef, b_coef * _int_pow10(shift), a_exp]
    shift2: int = a_exp - b_exp
    return [a_coef * _int_pow10(shift2), b_coef, b_exp]


class Decimal:
    """Decimal floating-point number."""

    def __init__(self, value: str = "0") -> None:
        self._coef: int = 0
        self._exp: int = 0
        self._sign: int = 0
        self._parse(value)

    def _parse(self, s: str) -> None:
        """Parse a decimal string like '3.14', '-0.5', '1E+2'."""
        s2: str = s
        i: int = 0
        neg: int = 0
        if i < len(s2) and s2[i] == "-":
            neg = 1
            i = i + 1
        elif i < len(s2) and s2[i] == "+":
            i = i + 1
        self._sign = neg
        int_part: str = ""
        frac_part: str = ""
        exp_part: str = ""
        in_frac: int = 0
        in_exp: int = 0
        exp_neg: int = 0
        while i < len(s2):
            c: str = s2[i]
            if c == "." and in_frac == 0 and in_exp == 0:
                in_frac = 1
            elif (c == "e" or c == "E") and in_exp == 0:
                in_exp = 1
                i = i + 1
                if i < len(s2) and s2[i] == "-":
                    exp_neg = 1
                    i = i + 1
                elif i < len(s2) and s2[i] == "+":
                    i = i + 1
                continue
            elif in_exp == 1:
                exp_part = exp_part + c
            elif in_frac == 1:
                frac_part = frac_part + c
            else:
                int_part = int_part + c
            i = i + 1
        combined: str = int_part + frac_part
        coef: int = 0
        j: int = 0
        while j < len(combined):
            d: str = combined[j]
            if d >= "0" and d <= "9":
                coef = coef * 10 + ord(d) - 48
            j = j + 1
        exp_val: int = 0
        k: int = 0
        while k < len(exp_part):
            exp_val = exp_val * 10 + ord(exp_part[k]) - 48
            k = k + 1
        if exp_neg == 1:
            exp_val = -exp_val
        self._exp = exp_val - len(frac_part)
        if neg == 1:
            coef = -coef
        self._coef = coef

    def _from_parts(self, coef: int, exp: int) -> Decimal:
        d: Decimal = Decimal("0")
        d._coef = coef
        d._exp = exp
        d._sign = 1 if coef < 0 else 0
        return d

    def __add__(self, other: Decimal) -> Decimal:
        parts: list = _align(self._coef, self._exp, other._coef, other._exp)
        return self._from_parts(parts[0] + parts[1], parts[2])

    def __sub__(self, other: Decimal) -> Decimal:
        parts: list = _align(self._coef, self._exp, other._coef, other._exp)
        return self._from_parts(parts[0] - parts[1], parts[2])

    def __mul__(self, other: Decimal) -> Decimal:
        return self._from_parts(self._coef * other._coef, self._exp + other._exp)

    def __truediv__(self, other: Decimal) -> Decimal:
        if other._coef == 0:
            return self._from_parts(0, 0)
        scale: int = _DEFAULT_PREC - _int_log10_floor(self._coef) + _int_log10_floor(other._coef) + 1
        if scale < 0:
            scale = 0
        num: int = self._coef * _int_pow10(scale)
        quot: int = num // other._coef
        new_exp: int = self._exp - other._exp - scale
        return self._from_parts(quot, new_exp)

    def __neg__(self) -> Decimal:
        return self._from_parts(-self._coef, self._exp)

    def __pos__(self) -> Decimal:
        return self._from_parts(self._coef, self._exp)

    def __abs__(self) -> Decimal:
        if self._coef < 0:
            return self._from_parts(-self._coef, self._exp)
        return self._from_parts(self._coef, self._exp)

    def __eq__(self, other: Decimal) -> int:
        parts: list = _align(self._coef, self._exp, other._coef, other._exp)
        return 1 if parts[0] == parts[1] else 0

    def __lt__(self, other: Decimal) -> int:
        parts: list = _align(self._coef, self._exp, other._coef, other._exp)
        return 1 if parts[0] < parts[1] else 0

    def __le__(self, other: Decimal) -> int:
        parts: list = _align(self._coef, self._exp, other._coef, other._exp)
        return 1 if parts[0] <= parts[1] else 0

    def __gt__(self, other: Decimal) -> int:
        parts: list = _align(self._coef, self._exp, other._coef, other._exp)
        return 1 if parts[0] > parts[1] else 0

    def __ge__(self, other: Decimal) -> int:
        parts: list = _align(self._coef, self._exp, other._coef, other._exp)
        return 1 if parts[0] >= parts[1] else 0

    def __bool__(self) -> int:
        return 1 if self._coef != 0 else 0

    def __int__(self) -> int:
        if self._exp >= 0:
            return self._coef * _int_pow10(self._exp)
        neg_exp: int = -self._exp
        return self._coef // _int_pow10(neg_exp)

    def __float__(self) -> float:
        if self._exp >= 0:
            return float(self._coef) * float(_int_pow10(self._exp))
        return float(self._coef) / float(_int_pow10(-self._exp))

    def __str__(self) -> str:
        if self._exp >= 0:
            if self._exp == 0:
                return str(self._coef)
            return str(self._coef) + "E+" + str(self._exp)
        coef_s: str = str(self._coef)
        neg: int = 0
        if len(coef_s) > 0 and coef_s[0] == "-":
            neg = 1
            coef_s = coef_s[1:]
        frac_digits: int = -self._exp
        if frac_digits >= len(coef_s):
            pad: int = frac_digits - len(coef_s)
            result: str = "0."
            i: int = 0
            while i < pad:
                result = result + "0"
                i = i + 1
            result = result + coef_s
            if neg == 1:
                result = "-" + result
            return result
        split_at: int = len(coef_s) - frac_digits
        int_part: str = coef_s[:split_at]
        frac_part: str = coef_s[split_at:]
        result2: str = int_part + "." + frac_part
        if neg == 1:
            result2 = "-" + result2
        return result2

    def __repr__(self) -> str:
        return "Decimal('" + self.__str__() + "')"

    def __hash__(self) -> int:
        return self._coef * 1000003 ^ self._exp

    def sqrt(self) -> Decimal:
        """Return the square root using Newton's method."""
        if self._coef <= 0:
            return self._from_parts(0, 0)
        x: float = self.__float__()
        import math
        approx: float = math.sqrt(x)
        return Decimal(str(approx))

    def is_zero(self) -> int:
        return 1 if self._coef == 0 else 0

    def is_signed(self) -> int:
        return 1 if self._coef < 0 else 0

    def is_finite(self) -> int:
        return 1

    def is_nan(self) -> int:
        return 0

    def is_infinite(self) -> int:
        return 0

    def sign(self) -> int:
        if self._coef < 0:
            return -1
        if self._coef > 0:
            return 1
        return 0

    def adjusted(self) -> int:
        """Return adjusted exponent = floor(log10(abs(self))) or 0."""
        if self._coef == 0:
            return 0
        return _int_log10_floor(self._coef) + self._exp

    def quantize(self, exp_place: Decimal) -> Decimal:
        """Round to the same number of decimal places as exp_place."""
        target_exp: int = exp_place._exp
        if self._exp == target_exp:
            return self._from_parts(self._coef, self._exp)
        if self._exp > target_exp:
            scale: int = self._exp - target_exp
            return self._from_parts(self._coef * _int_pow10(scale), target_exp)
        scale2: int = target_exp - self._exp
        divisor: int = _int_pow10(scale2)
        rounded: int = (self._coef + divisor // 2) // divisor
        return self._from_parts(rounded, target_exp)

    def __mod__(self, other: Decimal) -> Decimal:
        quotient: Decimal = self._truediv_trunc(other)
        return self.__sub__(other.__mul__(quotient))

    def _truediv_trunc(self, other: Decimal) -> Decimal:
        """Truncated (floor toward zero) division."""
        parts: list = _align(self._coef, self._exp, other._coef, other._exp)
        if parts[1] == 0:
            return self._from_parts(0, 0)
        return self._from_parts(parts[0] // parts[1], 0)

    def __floordiv__(self, other: Decimal) -> int:
        return self._truediv_trunc(other).__int__()


def getcontext() -> int:
    """Return the current decimal context (stub)."""
    return _DEFAULT_PREC


def setcontext(prec: int) -> None:
    """Set the decimal context precision (stub, no-op)."""
    pass


ROUND_UP: str = "ROUND_UP"
ROUND_DOWN: str = "ROUND_DOWN"
ROUND_CEILING: str = "ROUND_CEILING"
ROUND_FLOOR: str = "ROUND_FLOOR"
ROUND_HALF_UP: str = "ROUND_HALF_UP"
ROUND_HALF_DOWN: str = "ROUND_HALF_DOWN"
ROUND_HALF_EVEN: str = "ROUND_HALF_EVEN"
ROUND_05UP: str = "ROUND_05UP"
