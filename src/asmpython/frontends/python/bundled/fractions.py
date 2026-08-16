"""Exact rational arithmetic.

A `Fraction` is two integers and a promise: the value is exactly the quotient
and no rounding ever enters. That is the whole reason it exists -- `0.1 + 0.2
== 0.3` is False for floats and True here -- so every operation below reduces
by the greatest common divisor and never touches a float.

NORMALISED AT CONSTRUCTION, always: `Fraction(6, 4)` is `3/2`, not a pair that
happens to mean it. Two fractions are equal when their reduced forms match,
which is only a cheap comparison because the reduction already happened.
"""


def _gcd(a, b):
    a = a if a >= 0 else -a
    b = b if b >= 0 else -b
    while b:
        a, b = b, a % b
    return a


class Fraction:
    def __init__(self, numerator=0, denominator=None):
        if denominator is None:
            if isinstance(numerator, Fraction):
                num, den = numerator._numerator, numerator._denominator
            elif isinstance(numerator, str):
                num, den = _parse(numerator)
            elif isinstance(numerator, float):
                num, den = _from_float(numerator)
            else:
                num, den = numerator, 1
        else:
            if isinstance(numerator, Fraction) or isinstance(denominator,
                                                             Fraction):
                left = numerator if isinstance(numerator, Fraction) \
                    else Fraction(numerator)
                right = denominator if isinstance(denominator, Fraction) \
                    else Fraction(denominator)
                num = left._numerator * right._denominator
                den = left._denominator * right._numerator
            else:
                num, den = numerator, denominator
        if den == 0:
            raise ZeroDivisionError("Fraction(" + str(num) + ", 0)")
        # THE SIGN LIVES IN THE NUMERATOR. `1/-2` and `-1/2` are one value,
        # and keeping the minus in the denominator would make two reduced
        # forms of it that compare unequal.
        if den < 0:
            num, den = -num, -den
        g = _gcd(num, den)
        if g > 1:
            num = num // g
            den = den // g
        self._numerator = num
        self._denominator = den

    @property
    def numerator(self):
        return self._numerator

    @property
    def denominator(self):
        return self._denominator

    def __repr__(self):
        return "Fraction(" + str(self._numerator) + ", " \
               + str(self._denominator) + ")"

    def __str__(self):
        if self._denominator == 1:
            return str(self._numerator)
        return str(self._numerator) + "/" + str(self._denominator)

    def __hash__(self):
        # EQUAL VALUES HASH EQUAL, including across types: `Fraction(1, 1)`
        # and `1` are the same number, so a whole fraction hashes as its int.
        if self._denominator == 1:
            return hash(self._numerator)
        return hash((self._numerator, self._denominator))

    def __bool__(self):
        return self._numerator != 0

    def __float__(self):
        return self._numerator / self._denominator

    def __int__(self):
        # TOWARD ZERO, as `int()` on a float is -- not floor, which differs
        # for a negative value.
        whole = abs(self._numerator) // self._denominator
        return whole if self._numerator >= 0 else -whole

    def __neg__(self):
        return Fraction(-self._numerator, self._denominator)

    def __pos__(self):
        return self

    def __abs__(self):
        return Fraction(abs(self._numerator), self._denominator)

    def limit_denominator(self, max_denominator=1000000):
        """The closest fraction whose denominator is no larger, by Stern-Brocot
        descent -- which is the algorithm that makes the answer the CLOSEST
        one rather than merely a near one."""
        if self._denominator <= max_denominator:
            return self
        p0, q0, p1, q1 = 0, 1, 1, 0
        n, d = self._numerator, self._denominator
        while True:
            a = n // d
            q2 = q0 + a * q1
            if q2 > max_denominator:
                break
            p0, q0, p1, q1 = p1, q1, p0 + a * p1, q2
            n, d = d, n - a * d
            if d == 0:
                break
        k = (max_denominator - q0) // q1 if q1 else 0
        one = Fraction(p0 + k * p1, q0 + k * q1)
        two = Fraction(p1, q1)
        if abs(two - self) <= abs(one - self):
            return two
        return one

    def _pair(self, other):
        """`other` as a numerator and denominator, or None if it is not a
        number this can be exact about. A FLOAT IS NOT: mixing one in gives a
        float answer, which is CPython's rule and the honest one."""
        if isinstance(other, Fraction):
            return (other._numerator, other._denominator)
        if isinstance(other, int) and not isinstance(other, bool):
            return (other, 1)
        if isinstance(other, bool):
            return (1 if other else 0, 1)
        return None

    def __add__(self, other):
        pair = self._pair(other)
        if pair is None:
            if isinstance(other, float):
                return float(self) + other
            return NotImplemented
        return Fraction(self._numerator * pair[1] + pair[0] * self._denominator,
                        self._denominator * pair[1])

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        pair = self._pair(other)
        if pair is None:
            if isinstance(other, float):
                return float(self) - other
            return NotImplemented
        return Fraction(self._numerator * pair[1] - pair[0] * self._denominator,
                        self._denominator * pair[1])

    def __rsub__(self, other):
        pair = self._pair(other)
        if pair is None:
            if isinstance(other, float):
                return other - float(self)
            return NotImplemented
        return Fraction(pair[0] * self._denominator - self._numerator * pair[1],
                        self._denominator * pair[1])

    def __mul__(self, other):
        pair = self._pair(other)
        if pair is None:
            if isinstance(other, float):
                return float(self) * other
            return NotImplemented
        return Fraction(self._numerator * pair[0], self._denominator * pair[1])

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        pair = self._pair(other)
        if pair is None:
            if isinstance(other, float):
                return float(self) / other
            return NotImplemented
        return Fraction(self._numerator * pair[1], self._denominator * pair[0])

    def __rtruediv__(self, other):
        pair = self._pair(other)
        if pair is None:
            if isinstance(other, float):
                return other / float(self)
            return NotImplemented
        return Fraction(pair[0] * self._denominator, pair[1] * self._numerator)

    def __floordiv__(self, other):
        pair = self._pair(other)
        if pair is None:
            return NotImplemented
        return (self._numerator * pair[1]) // (self._denominator * pair[0])

    def __mod__(self, other):
        return self - (self // other) * (other if isinstance(other, Fraction)
                                         else Fraction(other))

    def __pow__(self, exponent):
        if isinstance(exponent, int) and not isinstance(exponent, bool):
            if exponent >= 0:
                return Fraction(self._numerator ** exponent,
                                self._denominator ** exponent)
            return Fraction(self._denominator ** -exponent,
                            self._numerator ** -exponent)
        return float(self) ** exponent

    def _cmp(self, other):
        """-1, 0 or 1. CROSS-MULTIPLIED, so no division and no rounding: the
        denominators are positive by construction, which is what makes the
        comparison of the products the comparison of the values."""
        pair = self._pair(other)
        if pair is None:
            return None
        left = self._numerator * pair[1]
        right = pair[0] * self._denominator
        if left < right:
            return -1
        return 0 if left == right else 1

    def __eq__(self, other):
        if isinstance(other, float):
            return float(self) == other
        got = self._cmp(other)
        return NotImplemented if got is None else got == 0

    def __ne__(self, other):
        got = self.__eq__(other)
        return got if got is NotImplemented else not got

    def __lt__(self, other):
        if isinstance(other, float):
            return float(self) < other
        got = self._cmp(other)
        return NotImplemented if got is None else got < 0

    def __le__(self, other):
        if isinstance(other, float):
            return float(self) <= other
        got = self._cmp(other)
        return NotImplemented if got is None else got <= 0

    def __gt__(self, other):
        if isinstance(other, float):
            return float(self) > other
        got = self._cmp(other)
        return NotImplemented if got is None else got > 0

    def __ge__(self, other):
        if isinstance(other, float):
            return float(self) >= other
        got = self._cmp(other)
        return NotImplemented if got is None else got >= 0


def _parse(text):
    """`"3/4"`, `"0.1"`, `"-2"` -- the spellings `Fraction(str)` accepts.

    A DECIMAL STRING IS EXACT: `Fraction("0.1")` is one tenth, not the float
    nearest it, which is the whole reason a program writes the string form.
    """
    text = text.strip()
    if "/" in text:
        parts = text.split("/")
        return (int(parts[0]), int(parts[1]))
    if "." in text:
        neg = text.startswith("-")
        if neg or text.startswith("+"):
            text = text[1:]
        parts = text.split(".")
        digits = parts[1]
        scale = 10 ** len(digits)
        whole = int(parts[0]) if parts[0] else 0
        num = whole * scale + (int(digits) if digits else 0)
        return (-num if neg else num, scale)
    return (int(text), 1)


def _from_float(value):
    """A float's EXACT value as a ratio. Every float is a dyadic rational, so
    doubling until the fraction is whole terminates and loses nothing."""
    num = value
    den = 1
    while num != int(num):
        num = num * 2
        den = den * 2
    return (int(num), den)
