"""Rational numbers.

COVERAGE: `Fraction` -- construction from `(numerator, denominator=1)`, a
single `int`, a single `float` (an EXACT conversion, via `as_integer_ratio`),
a `str` (CPython's own grammar: `"3/4"`, `"1.5"`, `"-47e-2"`, underscore
digit grouping, surrounding whitespace), and copying from another `Fraction`
or anything with `numerator`/`denominator` or `as_integer_ratio`. Always
reduced to lowest terms with a positive denominator. `.numerator`,
`.denominator`, `.as_integer_ratio()`, `.is_integer()`, `.limit_denominator`.
Arithmetic `+ - * / // % **` with `int`, `float` and `Fraction` on EITHER
side. Comparisons `< <= > >= == !=` against `int` and `float`, matching
CPython's exact rule for `nan`/`inf`. `__hash__` agreeing with `hash(int)`
and `hash(float)` for an equal value -- see `_hash_algorithm`. `__repr__`
(`"Fraction(3, 4)"`) and `__str__` (`"3/4"`), which differ. `__abs__`,
`__neg__`, `__pos__`, `__bool__`, `__float__`, `__int__`, `__trunc__`,
`__floor__`, `__ceil__`, `__round__` with `ndigits=` -- half-to-even, the
same tie rule `round()` uses everywhere else.

NOT COVERED: `numbers` is not built, so a "Rational" argument is duck-typed
(`Fraction`, or anything with matching `numerator`/`denominator` attributes)
rather than checked against `numbers.Rational` -- observably the same for
every type this compiler can construct. No `complex` arithmetic (the
frontend cannot name `complex` as a value at all). No `__format__` -- CPython's
covers a small language of its own (`{:.3f}`, `{:#x}`-style flags for a
ratio) that is its own piece of work. No `Decimal` interop
(`from_decimal`/`Fraction(Decimal(...))`) -- `decimal` is not yet built. No
pickling hooks (`__reduce__`) or `__copy__`/`__deepcopy__` -- an ordinary
attribute-for-attribute copy already gets the right answer for an immutable
value with no cycle to break.

`math.floor(f)`, `math.ceil(f)` AND `math.trunc(f)` NOW WORK, and this
module is why they do. They used to accept only `int`/`float`/`bool` and
never ask the object anything -- so `round(x)`, `abs(x)`, `-x`, `+x` and
`int(x)` all reached a user's dunder while `math.floor(x)` answered
`TypeError: must be real number, not <kind>` for the exact same object, and
the only rounding a `Fraction` could get was by calling `f.__floor__()`
directly. Writing this module is what found it; the fix went where it
belonged, to `math`'s three functions (`objects/c/_math.py`,
`runtime/mathints.py` and `objects/host.py`'s `_math1`), so every module
with a real number of its own gets it. `bundled/decimal.py` gained
`__floor__`/`__ceil__` at the same time and for the same reason.

`math.gcd` IS used, and safely: the reduction below and every arithmetic
operator lean on it rather than a hand-written Euclidean algorithm.
`runtime/mathints.py`'s OWN docstring warns that its ported `apy_math_gcd`
"reads a big[int] as a machine word" and is "wrong for a big" -- but that
warning is about the SELF-HOSTED runtime a compiled build links in
(`--object-runtime ir`), not the path `asmpython run` actually takes here.
Checked rather than assumed: `math.gcd` on operands past 2**63 (a random
30-digit pair, and `2**100`) answers identically to CPython under `-P`,
because that path's native calls are the INTERPRETER's own
(`objects/host.py`), which hands `math.gcd` two genuine Python ints on
the host and takes whatever host Python answers.
"""
import math

#: The characters CPython's own `_RATIONAL_FORMAT` treats as `\s` in
#: practice for the strings this module is asked to parse. Not full Unicode
#: whitespace -- ASCII is what a program actually writes into a literal.
_WHITESPACE = " \t\n\r\v\f"


def _is_digit(ch):
    return "0" <= ch <= "9"


def _digit_run(text, i, n):
    """`\\d+(_\\d+)*` starting at `text[i]` -- ONE OR MORE digits, optionally
    split into underscore-separated groups the way an int literal is.

    Returns `(digits, new_i)` with the underscores already stripped, or
    `(None, i)` if `text[i]` is not a digit at all. AN UNDERSCORE THAT ISN'T
    FOLLOWED BY A DIGIT IS NOT CONSUMED -- `"1_"` stops after `"1"`, leaving
    the `_` for whatever comes next to fail on, which is how CPython's regex
    (via backtracking) rejects it too.
    """
    if i >= n or not _is_digit(text[i]):
        return None, i
    j = i
    while j < n and _is_digit(text[j]):
        j += 1
    out = text[i:j]
    while j < n and text[j] == "_" and j + 1 < n and _is_digit(text[j + 1]):
        k = j + 1
        while k < n and _is_digit(text[k]):
            k += 1
        out += text[j + 1:k]
        j = k
    return out, j


def _digit_run_opt(text, i, n):
    """The same, but ZERO digits is not an error -- `\\d*|\\d+(_\\d+)*` in
    CPython's grammar, for the numerator and the fractional part, either of
    which may be empty (`".5"` has no digits before the point)."""
    got, j = _digit_run(text, i, n)
    return (got if got is not None else ""), j


def _parse_fraction_string(text):
    """`Fraction("3/4")`, `Fraction("1.5")`, `Fraction("-3/7")`,
    `Fraction("1e-5")`, `Fraction("  -3/7  ")` -- CPython's own grammar
    (`fractions._RATIONAL_FORMAT`), matched BY HAND rather than with `re`:
    optional surrounding whitespace, an optional sign, then EITHER
    `num/denom` OR a decimal point and an exponent, both optional.

    WHITESPACE IS ONLY LEGAL AROUND THE SLASH. `"3 /4"` and `"3/ 4"` are
    both `3/4`; `"3 .5"` and `"3 e5"` are both malformed. Checked against
    CPython directly rather than assumed from the docstring, because the
    grammar admits it in one place and nowhere near the other two.

    Returns `(numerator, denominator)`, with the sign already folded into
    `numerator` and NEITHER reduced -- the caller's shared reduction code
    does that once, the same way for every constructor path.
    """
    n = len(text)
    i = 0
    while i < n and text[i] in _WHITESPACE:
        i += 1
    sign = ""
    if i < n and text[i] in "+-":
        sign = text[i]
        i += 1
    # THE LOOKAHEAD: a digit next, or a point immediately followed by one.
    # Without it a bare sign, a bare point, or `".e5"` would each read a
    # numerator of "" and a scale of one and quietly answer 0.
    starts_num = i < n and _is_digit(text[i])
    starts_dot_digit = (i < n and text[i] == "."
                        and i + 1 < n and _is_digit(text[i + 1]))
    if not (starts_num or starts_dot_digit):
        raise ValueError("Invalid literal for Fraction: " + repr(text))
    numtext, i = _digit_run_opt(text, i, n)
    numerator = int(numtext) if numtext else 0
    denominator = 1
    j = i
    while j < n and text[j] in _WHITESPACE:
        j += 1
    if j < n and text[j] == "/":
        j += 1
        while j < n and text[j] in _WHITESPACE:
            j += 1
        denomtext, j2 = _digit_run(text, j, n)
        if denomtext is None:
            raise ValueError("Invalid literal for Fraction: " + repr(text))
        denominator = int(denomtext)
        i = j2
    else:
        # NOT `j`: the decimal point and the exponent marker both have to
        # follow the numerator IMMEDIATELY, with no whitespace admitted --
        # unlike the slash above. Using `j` here would accept `"3 .5"`.
        if i < n and text[i] == ".":
            i += 1
            dectext, i = _digit_run_opt(text, i, n)
            if dectext:
                scale = 10 ** len(dectext)
                numerator = numerator * scale + int(dectext)
                denominator *= scale
        if i < n and text[i] in "Ee":
            i += 1
            expsign = ""
            if i < n and text[i] in "+-":
                expsign = text[i]
                i += 1
            exptext, i2 = _digit_run(text, i, n)
            if exptext is None:
                raise ValueError("Invalid literal for Fraction: " + repr(text))
            i = i2
            exp = int(exptext)
            if expsign == "-":
                exp = -exp
            if exp >= 0:
                numerator *= 10 ** exp
            else:
                denominator *= 10 ** (-exp)
    while i < n and text[i] in _WHITESPACE:
        i += 1
    if i != n:
        raise ValueError("Invalid literal for Fraction: " + repr(text))
    if sign == "-":
        numerator = -numerator
    return numerator, denominator


def _num_den(x):
    """`x` as `(numerator, denominator)`, for the two-argument constructor
    and nothing else -- or `None` when `x` is neither a `Fraction` nor a
    plain `int` (`bool` included, since `isinstance(True, int)` is True
    here exactly as it is in CPython).

    `bool` IS CONVERTED, deliberately: `Fraction(True, 2)` must store a
    plain `1` as its numerator, not the bool `True` -- CPython's own
    `bool.numerator` answers a plain `int` and this keeps `repr` agreeing
    with it (`Fraction(1, 2)`, not something that prints `True`).
    """
    if isinstance(x, Fraction):
        return x._numerator, x._denominator
    if isinstance(x, bool):
        return int(x), 1
    if isinstance(x, int):
        return x, 1
    return None


class Fraction:
    """`numerator / denominator`, always in lowest terms, always with a
    positive denominator -- the sign lives on the numerator alone, so
    `Fraction(1, -2) == Fraction(-1, 2)` and both print `-1/2`.

    ORDINARY ATTRIBUTES rather than CPython's `__slots__`: this frontend's
    dynamic classes do not enforce one, and nothing here depends on that
    enforcement -- only on `_numerator` and `_denominator` never changing
    after `__init__`, which no method here breaks.
    """

    def __init__(self, numerator=0, denominator=None):
        if denominator is None:
            if isinstance(numerator, Fraction):
                numerator, denominator = (numerator._numerator,
                                          numerator._denominator)
            elif isinstance(numerator, bool):
                numerator, denominator = int(numerator), 1
            elif isinstance(numerator, int):
                denominator = 1
            elif isinstance(numerator, float):
                # EXACT: `0.1` is not one tenth, and `as_integer_ratio`
                # is the method that says what it really is.
                numerator, denominator = numerator.as_integer_ratio()
            elif isinstance(numerator, str):
                numerator, denominator = _parse_fraction_string(numerator)
            elif hasattr(numerator, "as_integer_ratio"):
                numerator, denominator = numerator.as_integer_ratio()
            else:
                raise TypeError(
                    "argument should be a string or a Rational instance "
                    "or have the as_integer_ratio() method")
        else:
            nd_a = _num_den(numerator)
            nd_b = _num_den(denominator)
            if nd_a is None or nd_b is None:
                raise TypeError("both arguments should be Rational "
                                "instances")
            na, da = nd_a
            nb, db = nd_b
            # CROSS-MULTIPLIED, which handles a plain `(int, int)` pair AND
            # `Fraction(Fraction(1, 7), Fraction(2, 3))` with the same two
            # lines -- for a plain int `da == db == 1` and this reduces to
            # the ordinary case unchanged.
            numerator, denominator = na * db, nb * da
        if denominator == 0:
            raise ZeroDivisionError("Fraction(" + str(numerator) + ", 0)")
        g = math.gcd(numerator, denominator)
        if denominator < 0:
            g = -g
        self._numerator = numerator // g
        self._denominator = denominator // g

    @property
    def numerator(self):
        return self._numerator

    @property
    def denominator(self):
        return self._denominator

    def is_integer(self):
        """Added in 3.12: whether the denominator reduced to 1."""
        return self._denominator == 1

    def as_integer_ratio(self):
        return (self._numerator, self._denominator)

    def limit_denominator(self, max_denominator=1000000):
        """The closest `Fraction` to `self` with a denominator no larger
        than `max_denominator` -- continued-fraction convergents, straight
        from CPython (Knuth, TAOCP vol. 2, 4.5.1). See its own comment
        there for the derivation; this is the same six lines."""
        if max_denominator < 1:
            raise ValueError("max_denominator should be at least 1")
        if self._denominator <= max_denominator:
            return Fraction(self._numerator, self._denominator)
        p0, q0, p1, q1 = 0, 1, 1, 0
        n, d = self._numerator, self._denominator
        while True:
            a = n // d
            q2 = q0 + a * q1
            if q2 > max_denominator:
                break
            p0, q0, p1, q1 = p1, q1, p0 + a * p1, q2
            n, d = d, n - a * d
        k = (max_denominator - q0) // q1
        if 2 * d * (q0 + k * q1) <= self._denominator:
            return Fraction(p1, q1)
        return Fraction(p0 + k * p1, q0 + k * q1)

    def __repr__(self):
        return ("Fraction(" + repr(self._numerator) + ", "
                + repr(self._denominator) + ")")

    def __str__(self):
        if self._denominator == 1:
            return str(self._numerator)
        return str(self._numerator) + "/" + str(self._denominator)

    def __float__(self):
        return self._numerator / self._denominator

    def __bool__(self):
        return self._numerator != 0

    def __abs__(self):
        return Fraction(abs(self._numerator), self._denominator)

    def __neg__(self):
        return Fraction(-self._numerator, self._denominator)

    def __pos__(self):
        return Fraction(self._numerator, self._denominator)

    def __trunc__(self):
        if self._numerator < 0:
            return -((-self._numerator) // self._denominator)
        return self._numerator // self._denominator

    def __int__(self):
        return self.__trunc__()

    def __floor__(self):
        """`math.floor(self)` IN SPIRIT -- see the module docstring for why
        `math.floor` itself does not reach this on asmpython yet. Correct
        on its own, and reached directly (`f.__floor__()`) or via
        `int(f)`/`__trunc__` for a non-negative `f`, where floor and trunc
        agree."""
        return self._numerator // self._denominator

    def __ceil__(self):
        # Negating both operands of a floor division turns it into a
        # ceiling -- CPython's own comment for this line, kept.
        return -(-self._numerator // self._denominator)

    def __round__(self, ndigits=None):
        """Half-to-even, the same tie rule `round()` uses on an `int` or a
        `float` -- `round(Fraction(1, 2))` is `0` and `round(Fraction(3,
        2))` is `2`, not `1` and `2`."""
        if ndigits is None:
            d = self._denominator
            floor = self._numerator // d
            remainder = self._numerator - floor * d
            twice = remainder * 2
            if twice < d:
                return floor
            if twice > d:
                return floor + 1
            if floor % 2 == 0:
                return floor
            return floor + 1
        shift = 10 ** abs(ndigits)
        if ndigits > 0:
            return Fraction(round(self * shift), shift)
        return Fraction(round(self / shift) * shift)

    # ── hashing, matching `int` and `float` for an equal value ──────────
    #
    # `hash(Fraction(2, 1)) == hash(2)` and `hash(Fraction(1, 2)) ==
    # hash(0.5)` are CPython's own guarantee -- the numeric-hash rule every
    # real number shares, so `Fraction(2) in {2}` and `{0.5: "x"}
    # [Fraction(1, 2)]` both work. `_PyHASH_MODULUS` (2**61 - 1) and
    # `_PyHASH_INF` (314159) are `sys.hash_info.modulus`/`.inf` on the
    # 64-bit build this runs on -- not in `sys`'s own bundled table here,
    # so written out rather than read.

    def __hash__(self):
        modulus = 2305843009213693951
        try:
            dinv = pow(self._denominator, -1, modulus)
        except ValueError:
            # No inverse: the denominator and the modulus share a factor,
            # which only happens when the modulus itself divides it -- the
            # rational is, for hashing purposes, unrepresentably large.
            hash_ = 314159
        else:
            hash_ = hash(hash(abs(self._numerator)) * dinv)
        result = hash_ if self._numerator >= 0 else -hash_
        return -2 if result == -1 else result

    # ── equality and ordering ────────────────────────────────────────────

    def __eq__(self, other):
        if isinstance(other, Fraction):
            return (self._numerator == other._numerator
                    and self._denominator == other._denominator)
        if isinstance(other, bool):
            other = int(other)
        if isinstance(other, int):
            return self._denominator == 1 and self._numerator == other
        if isinstance(other, float):
            if other != other or other == math.inf or other == -math.inf:
                # nan or an infinity: never equal to a finite rational.
                return False
            return self == Fraction(other)
        return NotImplemented

    def __ne__(self, other):
        # NOT DERIVED FROM `__eq__` AUTOMATICALLY. See `collections.deque`
        # and every other comparable class in that module for the same
        # explicit pair -- this frontend does not synthesise `!=` the way
        # CPython's `object` does.
        got = self.__eq__(other)
        return got if got is NotImplemented else not got

    def _richcmp(self, other, op):
        if isinstance(other, Fraction):
            return op(self._numerator * other._denominator,
                      self._denominator * other._numerator)
        if isinstance(other, bool):
            other = int(other)
        if isinstance(other, int):
            return op(self._numerator, self._denominator * other)
        if isinstance(other, float):
            if other != other or other == math.inf or other == -math.inf:
                return op(0.0, other)
            return op(self, Fraction(other))
        return NotImplemented

    def __lt__(self, other):
        return self._richcmp(other, lambda x, y: x < y)

    def __le__(self, other):
        return self._richcmp(other, lambda x, y: x <= y)

    def __gt__(self, other):
        return self._richcmp(other, lambda x, y: x > y)

    def __ge__(self, other):
        return self._richcmp(other, lambda x, y: x >= y)

    # ── arithmetic ────────────────────────────────────────────────────────
    #
    # Each `_xxx_core` takes two ALREADY-`Fraction` operands -- the shape
    # CPython's own `_add`/`_sub`/... take -- so an explicit zero check
    # here reports the divisor's own (already-reduced) denominator, which
    # is the wording CPython's `ZeroDivisionError` uses and plain
    # constructor-driven reduction would not reproduce (the unreduced
    # product, not the reduced divisor, would show).

    def _add_core(a, b):
        return Fraction(a._numerator * b._denominator
                        + b._numerator * a._denominator,
                        a._denominator * b._denominator)

    def _sub_core(a, b):
        return Fraction(a._numerator * b._denominator
                        - b._numerator * a._denominator,
                        a._denominator * b._denominator)

    def _mul_core(a, b):
        return Fraction(a._numerator * b._numerator,
                        a._denominator * b._denominator)

    def _div_core(a, b):
        if b._numerator == 0:
            raise ZeroDivisionError(
                "Fraction(" + str(b._denominator) + ", 0)")
        return Fraction(a._numerator * b._denominator,
                        a._denominator * b._numerator)

    def _floordiv_core(a, b):
        # No explicit zero check: `b._numerator == 0` makes the divisor of
        # this `//` zero too, and the plain `int` operator already raises
        # `ZeroDivisionError: division by zero` -- checked to be the same
        # text this runtime gives a bare `5 // 0`.
        return (a._numerator * b._denominator) // (a._denominator
                                                    * b._numerator)

    def _mod_core(a, b):
        da, db = a._denominator, b._denominator
        return Fraction((a._numerator * db) % (b._numerator * da), da * db)

    def __add__(self, other):
        if isinstance(other, Fraction):
            return self._add_core(other)
        if isinstance(other, int):
            return self._add_core(Fraction(other))
        if isinstance(other, float):
            return float(self) + other
        return NotImplemented

    def __radd__(self, other):
        if isinstance(other, int):
            return Fraction(other)._add_core(self)
        if isinstance(other, float):
            return other + float(self)
        return NotImplemented

    def __sub__(self, other):
        if isinstance(other, Fraction):
            return self._sub_core(other)
        if isinstance(other, int):
            return self._sub_core(Fraction(other))
        if isinstance(other, float):
            return float(self) - other
        return NotImplemented

    def __rsub__(self, other):
        if isinstance(other, int):
            return Fraction(other)._sub_core(self)
        if isinstance(other, float):
            return other - float(self)
        return NotImplemented

    def __mul__(self, other):
        if isinstance(other, Fraction):
            return self._mul_core(other)
        if isinstance(other, int):
            return self._mul_core(Fraction(other))
        if isinstance(other, float):
            return float(self) * other
        return NotImplemented

    def __rmul__(self, other):
        if isinstance(other, int):
            return Fraction(other)._mul_core(self)
        if isinstance(other, float):
            return other * float(self)
        return NotImplemented

    def __truediv__(self, other):
        if isinstance(other, Fraction):
            return self._div_core(other)
        if isinstance(other, int):
            return self._div_core(Fraction(other))
        if isinstance(other, float):
            return float(self) / other
        return NotImplemented

    def __rtruediv__(self, other):
        if isinstance(other, int):
            return Fraction(other)._div_core(self)
        if isinstance(other, float):
            return other / float(self)
        return NotImplemented

    def __floordiv__(self, other):
        if isinstance(other, Fraction):
            return self._floordiv_core(other)
        if isinstance(other, int):
            return self._floordiv_core(Fraction(other))
        if isinstance(other, float):
            return float(self) // other
        return NotImplemented

    def __rfloordiv__(self, other):
        if isinstance(other, int):
            return Fraction(other)._floordiv_core(self)
        if isinstance(other, float):
            return other // float(self)
        return NotImplemented

    def __mod__(self, other):
        if isinstance(other, Fraction):
            return self._mod_core(other)
        if isinstance(other, int):
            return self._mod_core(Fraction(other))
        if isinstance(other, float):
            return float(self) % other
        return NotImplemented

    def __rmod__(self, other):
        if isinstance(other, int):
            return Fraction(other)._mod_core(self)
        if isinstance(other, float):
            return other % float(self)
        return NotImplemented

    def __pow__(self, other):
        if isinstance(other, Fraction):
            if other._denominator != 1:
                # A fractional power is generally irrational -- there is
                # no exact rational answer to fall back on.
                return float(self) ** float(other)
            power = other._numerator
        elif isinstance(other, int):
            power = other
        elif isinstance(other, float):
            return float(self) ** other
        else:
            return NotImplemented
        if power >= 0:
            return Fraction(self._numerator ** power,
                            self._denominator ** power)
        if self._numerator == 0:
            raise ZeroDivisionError(
                "Fraction(" + str(self._denominator ** -power) + ", 0)")
        # SWAPPED WITHOUT FIXING THE SIGN BY HAND: `Fraction`'s own
        # constructor always moves a negative sign onto the numerator, so
        # `Fraction(denominator, numerator)` lands on the right answer
        # whichever of the two was negative -- unlike CPython's version,
        # which special-cases the sign itself to skip a redundant `gcd`.
        return Fraction(self._denominator ** -power,
                        self._numerator ** -power)

    def __rpow__(self, other):
        if self._denominator == 1 and self._numerator >= 0:
            return other ** self._numerator
        if isinstance(other, bool):
            other = int(other)
        if isinstance(other, int):
            return Fraction(other) ** self
        if self._denominator == 1:
            return other ** self._numerator
        if isinstance(other, float):
            return other ** float(self)
        return NotImplemented
