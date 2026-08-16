"""Decimal fixed and floating point arithmetic.

A `Decimal` is an integer of digits plus an exponent: `0.1` is exactly
(1, -1), never the binary fraction nearest to it. That is the whole point --
`Decimal("0.1") + Decimal("0.2") == Decimal("0.3")` is True and the float
version is not -- so nothing here ever converts to `float` to compute.

WHAT `prec` MEANS, and where it does and does not apply: addition,
subtraction and multiplication are EXACT here, because their exact result
needs no more digits than the operands carry. Division cannot be -- one third
has no finite decimal form -- so it is the operation that consults the
context, rounds to `prec` significant digits, and is the one a program sees
change when it sets `getcontext().prec`.

NOT HERE: the signalling/trap machinery, subnormals, and the full rounding
menu. ROUND_HALF_EVEN is what `prec` applies, which is the default and the
only one the arithmetic below can reach.
"""


class Context:
    def __init__(self, prec=28):
        self.prec = prec

    def copy(self):
        return Context(self.prec)


#: THE ONE CONTEXT. Real `decimal` keeps one per thread; there is one thread
#: here, so a module-level object is the same observable thing.
_CONTEXT = Context()


def getcontext():
    return _CONTEXT


def setcontext(ctx):
    _CONTEXT.prec = ctx.prec


def localcontext(ctx=None):
    raise NotImplementedError("localcontext needs a context manager stack")


class Decimal:
    """A value as DIGITS and an EXPONENT: the number is `digits * 10**exp`."""

    def __init__(self, value="0"):
        if isinstance(value, Decimal):
            self._digits = value._digits
            self._exp = value._exp
            return
        if isinstance(value, int) and not isinstance(value, bool):
            self._digits = value
            self._exp = 0
            return
        if isinstance(value, float):
            # REFUSED, as CPython's `Decimal(float)` used to be and as every
            # program that cares should want: the float is already not the
            # number that was written, so converting it exactly preserves the
            # error rather than the intent.
            raise TypeError("conversion from float to Decimal is not "
                            "supported here; use Decimal(str(value))")
        text = str(value).strip()
        neg = text.startswith("-")
        if neg or text.startswith("+"):
            text = text[1:]
        exp = 0
        if "e" in text or "E" in text:
            text = text.replace("E", "e")
            parts = text.split("e")
            text = parts[0]
            exp = int(parts[1])
        if "." in text:
            parts = text.split(".")
            frac = parts[1]
            exp = exp - len(frac)
            text = parts[0] + frac
        digits = int(text) if text else 0
        self._digits = -digits if neg else digits
        self._exp = exp

    def _align(self, other):
        """Both values on the SAME exponent, so they can be added as integers.

        Scaling UP the one with the larger exponent, never down: shifting the
        other way would drop digits, which is the rounding this type exists to
        avoid.
        """
        exp = self._exp if self._exp < other._exp else other._exp
        left = self._digits * 10 ** (self._exp - exp)
        right = other._digits * 10 ** (other._exp - exp)
        return (left, right, exp)

    def _coerce(self, other):
        if isinstance(other, Decimal):
            return other
        if isinstance(other, int) and not isinstance(other, bool):
            return Decimal(other)
        return None

    def __add__(self, other):
        right = self._coerce(other)
        if right is None:
            return NotImplemented
        a, b, exp = self._align(right)
        return _make(a + b, exp)

    def __radd__(self, other):
        return self.__add__(other)

    def __sub__(self, other):
        right = self._coerce(other)
        if right is None:
            return NotImplemented
        a, b, exp = self._align(right)
        return _make(a - b, exp)

    def __rsub__(self, other):
        left = self._coerce(other)
        if left is None:
            return NotImplemented
        return left.__sub__(self)

    def __mul__(self, other):
        right = self._coerce(other)
        if right is None:
            return NotImplemented
        return _make(self._digits * right._digits, self._exp + right._exp)

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        right = self._coerce(other)
        if right is None:
            return NotImplemented
        if right._digits == 0:
            raise ZeroDivisionError("division by zero")
        # THE ONLY OPERATION THAT ROUNDS. `prec` is a count of SIGNIFICANT
        # digits, so the numerator is scaled until the integer quotient has
        # exactly that many and the exponent carries the scaling back.
        prec = _CONTEXT.prec
        num, den = self._digits, right._digits
        if num == 0:
            return _make(0, 0)
        neg = (num < 0) != (den < 0)
        num, den = abs(num), abs(den)
        exp = self._exp - right._exp
        # Bring the quotient up to `prec` digits, one power of ten at a time.
        while num // den < 10 ** (prec - 1):
            num = num * 10
            exp = exp - 1
        while num // den >= 10 ** prec:
            den = den * 10
            exp = exp + 1
        whole = num // den
        rest = num - whole * den
        # ROUND HALF EVEN, the default: exactly half goes to the even digit,
        # which is what keeps a long run of roundings from drifting upward.
        twice = rest * 2
        if twice > den or (twice == den and whole % 2 == 1):
            whole = whole + 1
        return _make(-whole if neg else whole, exp)

    def __neg__(self):
        return _make(-self._digits, self._exp)

    def __pos__(self):
        return self

    def __abs__(self):
        return _make(abs(self._digits), self._exp)

    def __int__(self):
        if self._exp >= 0:
            return self._digits * 10 ** self._exp
        whole = abs(self._digits) // 10 ** -self._exp
        return -whole if self._digits < 0 else whole

    def __float__(self):
        return float(self.__str__())

    def __bool__(self):
        return self._digits != 0

    def __hash__(self):
        a, b = _reduced(self._digits, self._exp)
        return hash((a, b))

    def _cmp(self, other):
        right = self._coerce(other)
        if right is None:
            return None
        a, b, _ = self._align(right)
        if a < b:
            return -1
        return 0 if a == b else 1

    def __eq__(self, other):
        got = self._cmp(other)
        return NotImplemented if got is None else got == 0

    def __ne__(self, other):
        got = self.__eq__(other)
        return got if got is NotImplemented else not got

    def __lt__(self, other):
        got = self._cmp(other)
        return NotImplemented if got is None else got < 0

    def __le__(self, other):
        got = self._cmp(other)
        return NotImplemented if got is None else got <= 0

    def __gt__(self, other):
        got = self._cmp(other)
        return NotImplemented if got is None else got > 0

    def __ge__(self, other):
        got = self._cmp(other)
        return NotImplemented if got is None else got >= 0

    def as_integer_ratio(self):
        if self._exp >= 0:
            return (self._digits * 10 ** self._exp, 1)
        return _lowest(self._digits, 10 ** -self._exp)

    def __str__(self):
        digits = self._digits
        neg = digits < 0
        text = str(abs(digits))
        exp = self._exp
        if exp == 0:
            out = text
        elif exp > 0:
            out = text + "0" * exp
        else:
            place = -exp
            if len(text) <= place:
                text = "0" * (place - len(text) + 1) + text
            out = text[:len(text) - place] + "." + text[len(text) - place:]
        return "-" + out if neg else out

    def __repr__(self):
        return "Decimal('" + self.__str__() + "')"


def _make(digits, exp):
    made = Decimal(0)
    made._digits = digits
    made._exp = exp
    return made


def _reduced(digits, exp):
    """The same value with no trailing zeros in the digits -- so that two
    spellings of one number hash alike."""
    while digits and digits % 10 == 0 and exp < 0:
        digits = digits // 10
        exp = exp + 1
    return (digits, exp)


def _lowest(num, den):
    a, b = abs(num), den
    while b:
        a, b = b, a % b
    g = a if a else 1
    return (num // g, den // g)


class DecimalException(ArithmeticError):
    pass


class InvalidOperation(DecimalException):
    pass


class DivisionByZero(DecimalException, ZeroDivisionError):
    pass


class Inexact(DecimalException):
    pass


class Rounded(DecimalException):
    pass


ROUND_HALF_EVEN = "ROUND_HALF_EVEN"
ROUND_HALF_UP = "ROUND_HALF_UP"
ROUND_DOWN = "ROUND_DOWN"
ROUND_UP = "ROUND_UP"
ROUND_CEILING = "ROUND_CEILING"
ROUND_FLOOR = "ROUND_FLOOR"
