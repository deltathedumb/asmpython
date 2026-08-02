"""fractions module: rational number arithmetic.

Pure-Python implementation of exact rational numbers. A Fraction holds
a numerator and denominator kept in lowest terms (denominator always > 0).
"""
from __future__ import annotations


def _gcd(a: int, b: int) -> int:
    """Return GCD of a and b (Euclidean algorithm)."""
    if a < 0:
        a = -a
    if b < 0:
        b = -b
    while b != 0:
        tmp: int = b
        b = a % b
        a = tmp
    return a


class Fraction:
    """Rational number: numerator / denominator, always in lowest terms."""

    def __init__(self, numerator: int = 0, denominator: int = 1) -> None:
        if denominator == 0:
            denominator = 1
        g: int = _gcd(numerator, denominator)
        if g == 0:
            g = 1
        if denominator < 0:
            numerator = -numerator
            denominator = -denominator
        self.numerator: int = numerator // g
        self.denominator: int = denominator // g

    def _add(self, other: Fraction) -> Fraction:
        return Fraction(
            self.numerator * other.denominator + other.numerator * self.denominator,
            self.denominator * other.denominator,
        )

    def _sub(self, other: Fraction) -> Fraction:
        return Fraction(
            self.numerator * other.denominator - other.numerator * self.denominator,
            self.denominator * other.denominator,
        )

    def _mul(self, other: Fraction) -> Fraction:
        return Fraction(
            self.numerator * other.numerator,
            self.denominator * other.denominator,
        )

    def _truediv(self, other: Fraction) -> Fraction:
        return Fraction(
            self.numerator * other.denominator,
            self.denominator * other.numerator,
        )

    def _neg(self) -> Fraction:
        return Fraction(-self.numerator, self.denominator)

    def _abs(self) -> Fraction:
        if self.numerator < 0:
            return Fraction(-self.numerator, self.denominator)
        return Fraction(self.numerator, self.denominator)

    def add_int(self, n: int) -> Fraction:
        """Return self + n (integer)."""
        return Fraction(self.numerator + n * self.denominator, self.denominator)

    def sub_int(self, n: int) -> Fraction:
        """Return self - n (integer)."""
        return Fraction(self.numerator - n * self.denominator, self.denominator)

    def mul_int(self, n: int) -> Fraction:
        """Return self * n (integer)."""
        return Fraction(self.numerator * n, self.denominator)

    def div_int(self, n: int) -> Fraction:
        """Return self / n (integer)."""
        return Fraction(self.numerator, self.denominator * n)

    def __eq__(self, other: Fraction) -> int:
        return 1 if self.numerator == other.numerator and self.denominator == other.denominator else 0

    def eq_int(self, n: int) -> int:
        """Return True if self == n (integer)."""
        return 1 if self.numerator == n * self.denominator else 0

    def __lt__(self, other: Fraction) -> int:
        return 1 if self.numerator * other.denominator < other.numerator * self.denominator else 0

    def __le__(self, other: Fraction) -> int:
        return 1 if self.numerator * other.denominator <= other.numerator * self.denominator else 0

    def __gt__(self, other: Fraction) -> int:
        return 1 if self.numerator * other.denominator > other.numerator * self.denominator else 0

    def __ge__(self, other: Fraction) -> int:
        return 1 if self.numerator * other.denominator >= other.numerator * self.denominator else 0

    def lt_int(self, n: int) -> int:
        return 1 if self.numerator < n * self.denominator else 0

    def gt_int(self, n: int) -> int:
        return 1 if self.numerator > n * self.denominator else 0

    def to_float(self) -> float:
        """Return the fraction as a float."""
        return float(self.numerator) / float(self.denominator)

    def limit_denominator(self, max_denominator: int = 1000000) -> Fraction:
        """Return the closest Fraction with denominator <= max_denominator."""
        if self.denominator <= max_denominator:
            return Fraction(self.numerator, self.denominator)
        p0: int = 0
        q0: int = 1
        p1: int = 1
        q1: int = 0
        n: int = self.numerator
        d: int = self.denominator
        while True:
            a: int = n // d
            q2: int = q0 + a * q1
            if q2 > max_denominator:
                break
            p2: int = p0 + a * p1
            p0 = p1
            q0 = q1
            p1 = p2
            q1 = q2
            n2: int = d
            d = n - a * d
            n = n2
            if d == 0:
                break
        k: int = (max_denominator - q0) // q1
        b1: Fraction = Fraction(p0 + k * p1, q0 + k * q1)
        b2: Fraction = Fraction(p1, q1)
        diff1: int = b1.numerator * self.denominator - self.numerator * b1.denominator
        if diff1 < 0:
            diff1 = -diff1
        diff2: int = b2.numerator * self.denominator - self.numerator * b2.denominator
        if diff2 < 0:
            diff2 = -diff2
        if diff1 * b2.denominator <= diff2 * b1.denominator:
            return b1
        return b2

    def __str__(self) -> str:
        if self.denominator == 1:
            return str(self.numerator)
        return str(self.numerator) + "/" + str(self.denominator)

    def __repr__(self) -> str:
        return "Fraction(" + str(self.numerator) + ", " + str(self.denominator) + ")"

    def __hash__(self) -> int:
        return self.numerator * 1000003 ^ self.denominator

    def __int__(self) -> int:
        return self.numerator // self.denominator

    def __float__(self) -> float:
        return self.to_float()

    def __bool__(self) -> int:
        return 1 if self.numerator != 0 else 0

    def __pos__(self) -> Fraction:
        return Fraction(self.numerator, self.denominator)

    def __neg__(self) -> Fraction:
        return self._neg()

    def __abs__(self) -> Fraction:
        return self._abs()

    def __add__(self, other: Fraction) -> Fraction:
        return self._add(other)

    def __sub__(self, other: Fraction) -> Fraction:
        return self._sub(other)

    def __mul__(self, other: Fraction) -> Fraction:
        return self._mul(other)

    def __truediv__(self, other: Fraction) -> Fraction:
        return self._truediv(other)

    def __floordiv__(self, other: Fraction) -> int:
        r: Fraction = self._truediv(other)
        return r.numerator // r.denominator

    def __mod__(self, other: Fraction) -> Fraction:
        fl: int = self.__floordiv__(other)
        return self._sub(other.mul_int(fl))

    def __pow__(self, exp: int) -> Fraction:
        if exp >= 0:
            return Fraction(self.numerator ** exp, self.denominator ** exp)
        return Fraction(self.denominator ** (-exp), self.numerator ** (-exp))
