"""Correctly-exact decimal arithmetic (IBM's General Decimal Arithmetic
Specification), the general-purpose subset.

COVERAGE: `Decimal` construction from an `int`, a `str` (CPython's own
grammar -- optional sign, digits with an optional `.`, an optional `E`
exponent, `Infinity`/`Inf`, `NaN`/`sNaN` with an optional diagnostic
payload, all case-insensitive, underscores stripped BLINDLY the way
CPython's own constructor strips them before handing the text to its
regex), a `float` via `Decimal(x)` (exact -- goes through `from_float`,
which is also public and exact), a `(sign, digit_tuple, exponent)` tuple
(`as_tuple`'s own shape), and another `Decimal`. Arithmetic `+ - * / //
% **` computed EXACTLY on the decimal digits and rounded only ONCE, at
the end, to `getcontext().prec` significant digits -- so
`Decimal("0.1") + Decimal("0.2") == Decimal("0.3")` exactly, and
`Decimal("1.1") * Decimal("2.2")` lands on `2.42` rather than a binary
approximation of it. `//` and `%` follow the DECIMAL spec's rule
(truncate toward zero, remainder takes the DIVIDEND's sign) rather than
Python's own floor rule for `int`/`float` -- `Decimal(-7) // Decimal(2)
== -3`, not `-4`. `**` with an integer-valued exponent (`Decimal`, `int`,
or a `Decimal` like `Decimal("2.00")` whose digits are exactly integral)
on either side. Comparisons `< <= > >= == !=` against `int`, `float` and
`Decimal`, matching CPython's rule for a `NaN` operand (`==`/`!=` answer
False/True quietly; the four orderings raise `InvalidOperation`, because
the DEFAULT context traps it -- see below). `__hash__` agreeing with
`hash(int)` and `hash(float)` for an equal value, by the same
`_PyHASH_MODULUS`/`_PyHASH_INF` numeric-hash rule `fractions.Fraction`
already implements -- see its module docstring; `__hash__` here is a
direct restatement of CPython's own `Decimal.__hash__` rather than
`Fraction`'s denominator-inverse trick, because a `Decimal`'s
denominator is always a power of ten and CPython's own formula is
already the simpler one for that case. `__str__`/`__repr__` are a
faithful, field-for-field port of CPython's own `_pydecimal.Decimal.
__str__` (scientific-vs-plain threshold, trailing zeros preserved per
the operand's own exponent, `E+`/`E-` with a leading `+` on a
nonnegative exponent) -- so `str(Decimal("1.50") + Decimal("0"))` is
`"1.50"`, not `"1.5"`, exactly as CPython's is. `quantize`,
`to_integral_value` (and its old name `to_integral`), `is_nan`,
`is_snan`, `is_infinite`, `is_finite`, `is_zero`, `is_signed`,
`as_tuple`, `as_integer_ratio`, `adjusted`. A `Context` object with
`prec` and `rounding`, reached through `getcontext()`/`setcontext()`/
`localcontext()` exactly as CPython's are (a `with localcontext():`
block restores the outer context on exit; `with localcontext(ctx,
prec=6):` starts from a copy of `ctx` with `prec` already overridden).
ALL EIGHT rounding modes are implemented, not just the default:
`ROUND_HALF_EVEN` (the default), `ROUND_HALF_UP`, `ROUND_HALF_DOWN`,
`ROUND_UP`, `ROUND_DOWN`, `ROUND_CEILING`, `ROUND_FLOOR`, `ROUND_05UP`
-- each is the exact digit-string test CPython's own `_pydecimal` uses,
not a reimplementation. `DecimalException` (an `ArithmeticError`),
`InvalidOperation`, `ConversionSyntax`, `DivisionByZero`,
`DivisionImpossible`, `DivisionUndefined`, `InvalidContext`, `Inexact`,
`Rounded`, `Clamped`, `Subnormal`, `Overflow`, `Underflow`,
`FloatOperation` all exist as real classes with real inheritance among
THEMSELVES; `InvalidOperation` and `DivisionByZero` are the two that are
genuinely RAISED, with the same real-world triggers CPython's default
(trapped) context raises them for: bad string syntax, `0/0`, `x/0`,
`0**0`, `(+-)INF + (-+)INF`, `INF * 0`, `x % 0`, ordering a `NaN`, a
quotient with more than `prec` digits, and so on -- `ConversionSyntax`,
`DivisionImpossible` and `DivisionUndefined` exist and are real
`InvalidOperation` subclasses for `isinstance` purposes, but the
exception object actually raised for each of their triggers is plain
`InvalidOperation` itself, never the more specific subclass; that is
what CPython's OWN default context does too (checked directly against
the actually-installed, C-accelerated module, which the more commonly
read `_pydecimal.py` source explains: its `_raise_error` maps each of
those four "condition" classes to `InvalidOperation` before raising).

NOT COVERED, and why each one is a deliberate line rather than an
oversight:

`numbers` is not built (it is later in this same tier), so there is no
`numbers.Number`/`numbers.Real` registration -- observably invisible to
every piece of code this compiler can run, since nothing here can name
`numbers` to check against it anyway.

NO FLAGS/TRAPS DICTIONARY, no `Context.Emin`/`Emax`/`clamp`, no
subnormals, no `Overflow`/`Underflow`/`Clamped`/`Rounded`/`Inexact`/
`FloatOperation` ever actually SIGNALLED (the classes exist, for
`isinstance`/`except` completeness, but nothing here raises them) --
this is the IEEE-854 context machinery the task brief says to leave out
by name, and it is the right thing to leave out here specifically
because arithmetic is already computed EXACTLY before the one rounding
step, so `Overflow` (an adjusted exponent past `Emax`) essentially never
arises for the ordinary magnitudes this module is for. `Context()`
silently accepts and ignores `Emin=`/`Emax=`/`clamp=`/`flags=`/`traps=`
keywords for constructor-compatibility rather than raising on them.
Mixing a `Decimal` with a `float` in an ARITHMETIC operator
(`Decimal("1.5") + 1.0`) is refused exactly the way real `decimal`
refuses it -- returns `NotImplemented` on both sides, so Python raises
`TypeError` -- because that mixing was never allowed in the first
place, only comparisons and construction accept a `float`; this is
NOT a scoping choice, it is what CPython's own module does. Setting an
unsupported string into `Context.rounding` directly (bypassing the
constructor's check) is not validated until the next arithmetic
operation reaches it, where it raises `KeyError` rather than CPython's
`TypeError` at assignment time.

`Decimal ** Decimal` with a genuinely FRACTIONAL exponent (`Decimal(2)
** Decimal("0.5")`) is refused with `InvalidOperation` naming the
reason -- real CPython computes this via a correctly-rounded `ln`/`exp`
algorithm with guard digits, which is its own large piece of numerical
work and is exactly the kind of thing this module's scope excludes.
`.sqrt()`, `.ln()`, `.exp()`, `.log10()`, `remainder_near`,
`same_quantum`, `compare`, `compare_total`, `copy_sign`, `copy_abs`,
`copy_negate`, `to_eng_string`/engineering notation, `radix`, `logb`,
`canonical`, `rotate`, `shift`, the LOGICAL operand class (`logical_and`
and friends, which treat digit strings as base-2), `__format__`
(CPython's own covers a small format-spec language of its own --
`{:.2f}`, `{:+,.3f}`, `{:%}` -- that is its own piece of work, exactly
the reason `fractions.py` scoped it out too), `__floor__`/`__ceil__`
(`to_integral_value` and two-argument `round`/`quantize` already cover
rounding to an integer or to a place), pickling hooks, `from_number`,
and `IEEEContext` are all simply absent -- calling one is an ordinary
`AttributeError`, the same shape every other bundled module leaves an
unimplemented method in.

A STRING'S DIGITS ARE ASCII `0`-`9` ONLY, not the wider set of Unicode
decimal digits CPython's own regex (`\\d` without `re.ASCII`) accepts --
the same simplification `fractions.py`'s hand parser already made for
the same reason: nothing an ordinary program writes into a literal
needs it.

COMPILER FINDING, left unfixed and worked around: a bundled exception
class with TWO bases -- one of this module's own (`DecimalException`,
itself an `ArithmeticError`) and a second BUILTIN exception
(`ZeroDivisionError`) -- registers `issubclass` correctly but NOT
`isinstance`, and an `except ZeroDivisionError:` around code raising
such a class does not catch it at all (falls through as an unmatched
exception), even though `issubclass(TheClass, ZeroDivisionError)`
reports True. Checked directly with a two-line probe class outside this
module entirely, so it is not particular to `decimal`'s own hierarchy.
Real CPython's `decimal.DivisionByZero` is `(DecimalException,
ZeroDivisionError)` and `decimal.FloatOperation` is `(DecimalException,
TypeError)`; here `DivisionByZero`, `DivisionUndefined` and
`FloatOperation` inherit ONLY from this module's own hierarchy, never
combined with a second builtin base. `except decimal.DivisionByZero:`
and `except ArithmeticError:` both catch a `DivisionByZero` correctly
here (ordinary single-inheritance chains work, checked the same way);
only a bare `except ZeroDivisionError:` around a `decimal` division by
zero would not, and that is the one thing this workaround costs.

A SECOND, LARGER COMPILER FINDING, also left unfixed and worked around:
the two-argument `divmod()` BUILTIN is broken on this runtime's
interpreter path, for ORDINARY PLAIN INTS -- nothing to do with this
module's own bignum handling. `divmod(17, 5)` answers `(10, 11)`
instead of `(3, 2)`; `divmod(7, 7)` and `divmod(9, 9)` both answer `(9,
11)`, identically, despite different operands -- the result looks
unrelated to the arguments given, rather than merely rounded or
truncated wrong. Checked directly with several minimal probe scripts
outside this module entirely (small ints, big ints, swapped operand
sizes), so it is not particular to decimal's own coefficients. The
SEPARATE two-operator spelling -- `a // b` and `a % b` -- is unaffected
and answers correctly for every one of the same probes, including
values past 2**64; this module's own `_int_divide` and `__truediv__`
use exactly that spelling and NEVER call `divmod()` themselves, which
is the whole workaround. Root-caused only partway: `_TABLE["apy_divmod"]`
genuinely is bound to the interpreter's own `_apy_divmod`
(`objects/host.py`), and that function's own body is an ordinary,
correct three-line `divmod(x, y)` on the host's real Python ints -- so
the fault is upstream of it, in how the CALL reaches that function or
how its result is read back, not in the arithmetic itself; that upstream
half was not pinned down further, so no fix was attempted here rather
than guessing at one. A SEPARATE, further symptom of the same builtin:
calling `divmod()` on two instances of a user-defined class does not
attempt `__divmod__` at all (`TypeError: unsupported operand type(s)
for divmod(): 'Instance' and 'Instance'`, for a class that defines the
method) -- unlike `+`, `<` and every other binary operator here, which
do fall back to the user's dunder. Consequently this module's own
`Decimal.__divmod__` is real and correct, but the differential test
below reaches it by calling `x.__divmod__(y)` directly rather than
through Python's `divmod(x, y)`, and any program on this compiler that
wants `divmod` on a `Decimal` needs the same workaround today.
"""
import collections
import math

DecimalTuple = collections.namedtuple("DecimalTuple", ["sign", "digits", "exponent"])

# ── rounding modes ──────────────────────────────────────────────────────

ROUND_CEILING = "ROUND_CEILING"
ROUND_DOWN = "ROUND_DOWN"
ROUND_FLOOR = "ROUND_FLOOR"
ROUND_HALF_DOWN = "ROUND_HALF_DOWN"
ROUND_HALF_EVEN = "ROUND_HALF_EVEN"
ROUND_HALF_UP = "ROUND_HALF_UP"
ROUND_UP = "ROUND_UP"
ROUND_05UP = "ROUND_05UP"

_ROUNDING_MODES = (ROUND_DOWN, ROUND_UP, ROUND_HALF_UP, ROUND_HALF_DOWN,
                   ROUND_HALF_EVEN, ROUND_CEILING, ROUND_FLOOR, ROUND_05UP)

# ── exceptions -- see the module docstring's COMPILER FINDING for why  ──
# ── DivisionByZero/DivisionUndefined/FloatOperation are single-based   ──

class DecimalException(ArithmeticError):
    """Base of every exception this module raises."""


class Clamped(DecimalException):
    """Never signalled here -- see NOT COVERED."""


class Rounded(DecimalException):
    """Never signalled here -- see NOT COVERED."""


class Subnormal(DecimalException):
    """Never signalled here -- see NOT COVERED."""


class Inexact(DecimalException):
    """Never signalled here -- see NOT COVERED."""


class Overflow(Inexact):
    """Never signalled here -- see NOT COVERED. Real CPython's also
    derives from `Rounded`; dropped rather than given a second base,
    for the reason the module docstring's COMPILER FINDING explains --
    this one is simply never raised, so it costs nothing."""


class Underflow(Inexact):
    """Never signalled here -- see NOT COVERED."""


class InvalidOperation(DecimalException):
    """A real, commonly-raised exception -- bad conversions, `0**0`,
    `(+-)INF + (-+)INF`, `INF * 0`, `x % 0`, ordering a `NaN`, and more.
    """


class ConversionSyntax(InvalidOperation):
    """A string handed to the `Decimal` constructor did not parse."""


class DivisionImpossible(InvalidOperation):
    """The exact integer quotient of `//`, `%` or `divmod` would need
    more than `context.prec` digits."""


class DivisionUndefined(InvalidOperation):
    """`0 / 0`, `0 // 0`, `divmod(0, 0)` or `0 % 0` -- real CPython's
    also derives from `ZeroDivisionError`; see the COMPILER FINDING."""


class InvalidContext(InvalidOperation):
    """Never signalled here -- see NOT COVERED."""


class DivisionByZero(DecimalException):
    """A nonzero dividend divided by zero. Real CPython's also derives
    from `ZeroDivisionError`; see the COMPILER FINDING."""


class FloatOperation(DecimalException):
    """Never signalled here -- see NOT COVERED. Real CPython's also
    derives from `TypeError`; see the COMPILER FINDING."""


# ── numeric-hash constants, the same rule `fractions.Fraction.__hash__` ──
# ── implements -- see that module's docstring for where these numbers  ──
# ── come from (`sys.hash_info` on the 64-bit build this runs on).      ──

_PyHASH_MODULUS = 2305843009213693951
_PyHASH_INF = 314159
_PyHASH_10INV = pow(10, -1, _PyHASH_MODULUS)


# ── digit-string rounding decisions ─────────────────────────────────────
#
# Each function below answers the same question CPython's own
# `_pydecimal` rounding functions do, for `digits[:keep]` kept and
# `digits[keep:]` about to be discarded: -1 (truncate; the discarded
# digits were NOT all zero, so the truncation loses information), 0
# (truncate; the discarded digits WERE all zero, so nothing is lost),
# or 1 (round the kept portion up, away from zero). `sign` is 0 for a
# nonnegative value and 1 for a negative one -- only `_round_ceiling`
# and `_round_floor` consult it, but every function takes it so one
# dispatch table can hold all eight.

def _all_zeros(digits, pos):
    return not digits[pos:].strip("0")


def _exact_half(digits, pos):
    return pos < len(digits) and digits[pos] == "5" and _all_zeros(digits, pos + 1)


def _round_down(digits, keep, sign):
    return 0 if _all_zeros(digits, keep) else -1


def _round_up(digits, keep, sign):
    return -_round_down(digits, keep, sign)


def _round_half_up(digits, keep, sign):
    if digits[keep] in "56789":
        return 1
    if _all_zeros(digits, keep):
        return 0
    return -1


def _round_half_down(digits, keep, sign):
    if _exact_half(digits, keep):
        return -1
    return _round_half_up(digits, keep, sign)


def _round_half_even(digits, keep, sign):
    if _exact_half(digits, keep) and (keep == 0 or digits[keep - 1] in "02468"):
        return -1
    return _round_half_up(digits, keep, sign)


def _round_ceiling(digits, keep, sign):
    if sign:
        return _round_down(digits, keep, sign)
    return -_round_down(digits, keep, sign)


def _round_floor(digits, keep, sign):
    if not sign:
        return _round_down(digits, keep, sign)
    return -_round_down(digits, keep, sign)


def _round_05up(digits, keep, sign):
    if keep and digits[keep - 1] not in "05":
        return _round_down(digits, keep, sign)
    return -_round_down(digits, keep, sign)


_ROUNDING_FUNCS = {
    ROUND_DOWN: _round_down,
    ROUND_UP: _round_up,
    ROUND_HALF_UP: _round_half_up,
    ROUND_HALF_DOWN: _round_half_down,
    ROUND_HALF_EVEN: _round_half_even,
    ROUND_CEILING: _round_ceiling,
    ROUND_FLOOR: _round_floor,
    ROUND_05UP: _round_05up,
}


# ── string parsing ───────────────────────────────────────────────────────

def _is_ascii_digit(ch):
    return "0" <= ch <= "9"


def _digit_run(text, i, n):
    """The longest run of ASCII digits starting at `text[i]` -- possibly
    empty. Returns `(digits, new_i)`."""
    j = i
    while j < n and _is_ascii_digit(text[j]):
        j += 1
    return text[i:j], j


def _parse_decimal_string(text):
    """CPython's own `_parser` grammar for the `Decimal(str)`
    constructor, matched by hand rather than with `re` -- the same
    approach `fractions.py`'s `_parse_fraction_string` takes for a
    smaller grammar, applied here to a bigger one: optional sign, then
    EITHER a number (digits, an optional `.` and more digits, an
    optional `E` exponent) OR `Inf`/`Infinity` OR an optional `s`
    (signaling) followed by `NaN` and an optional all-digit diagnostic
    payload -- all case-insensitive except the digits themselves, which
    have no case. Underscores are stripped BLINDLY first, exactly as
    CPython's own constructor strips them before handing the text to
    its regex (`Decimal("1_2_") == Decimal("12")`, even though that
    split would not be a legal `int` literal) -- checked directly
    against CPython.

    Returns `("finite", sign, digit_string, exponent)`,
    `("F", sign)` for an infinity, `("n" or "N", sign, payload_string)`
    for a quiet/signaling NaN (`self._exp`'s own special-value spelling,
    reused here so the caller can drop it straight into `self._exp`),
    or `None` if `text` is not a valid Decimal literal at all.
    """
    text = text.strip().replace("_", "")
    n = len(text)
    i = 0
    sign = 0
    if i < n and text[i] in "+-":
        sign = 1 if text[i] == "-" else 0
        i += 1
    rest = text[i:]
    low = rest.lower()
    if low == "inf" or low == "infinity":
        return ("F", sign)
    signaling = False
    offset = 0
    if low[:1] == "s":
        signaling = True
        offset = 1
    if low[offset:offset + 3] == "nan":
        diag = rest[offset + 3:]
        if diag == "" or all(_is_ascii_digit(c) for c in diag):
            payload = diag.lstrip("0")
            return ("N" if signaling else "n", sign, payload)
        return None
    # A finite number needs a digit next, or a point immediately
    # followed by one -- without this, a bare sign or a lone "." would
    # otherwise read a zero-digit numerator and quietly answer 0.
    j = i
    if not (j < n and (_is_ascii_digit(text[j])
                        or (text[j] == "." and j + 1 < n
                            and _is_ascii_digit(text[j + 1])))):
        return None
    intpart, j = _digit_run(text, j, n)
    fracpart = ""
    if j < n and text[j] == ".":
        j += 1
        fracpart, j = _digit_run(text, j, n)
    exp = 0
    if j < n and text[j] in "Ee":
        j += 1
        expsign = 1
        if j < n and text[j] in "+-":
            if text[j] == "-":
                expsign = -1
            j += 1
        expdigits, j = _digit_run(text, j, n)
        if expdigits == "":
            return None
        exp = expsign * int(expdigits)
    if j != n:
        return None
    digits = str(int(intpart + fracpart)) if (intpart or fracpart) else "0"
    return ("finite", sign, digits, exp - len(fracpart))


# ── the class itself ─────────────────────────────────────────────────────
#
# Represented exactly as CPython's own `_pydecimal.Decimal` is:
# `self._sign` (0 or 1), `self._int` (the coefficient's digits, as a
# string with no leading zeros -- "0" for zero itself), and `self._exp`
# -- an ordinary int for a finite value, giving `(-1)**sign * int(_int)
# * 10**_exp`, or one of the strings "F" (infinity), "n" (a quiet NaN)
# or "N" (a signaling NaN) for a special value, in which case `_int`
# holds a NaN's diagnostic payload digits and is unused (always "0")
# for an infinity. `self._is_special` says which case applies, so
# ordinary arithmetic never has to test `self._exp`'s TYPE.
#
# ORDINARY ATTRIBUTES, exactly as `fractions.Fraction`'s are, for the
# same reason its docstring gives: this frontend's dynamic classes do
# not enforce `__slots__`-style immutability, and nothing here depends
# on that enforcement -- only on these four never changing after
# construction, which no method here breaks.

class Decimal:
    def __init__(self, value="0"):
        if isinstance(value, Decimal):
            self._sign = value._sign
            self._int = value._int
            self._exp = value._exp
            self._is_special = value._is_special
            return
        if isinstance(value, bool):
            value = int(value)
        if isinstance(value, int):
            self._sign = 0 if value >= 0 else 1
            self._int = str(abs(value))
            self._exp = 0
            self._is_special = False
            return
        if isinstance(value, str):
            parsed = _parse_decimal_string(value)
            if parsed is None:
                # `InvalidOperation`, NOT `ConversionSyntax` -- real
                # CPython's own default (trapped) context always raises
                # the SIGNAL class, never the more specific "condition"
                # class (`ConversionSyntax`/`DivisionImpossible`/
                # `DivisionUndefined`/`InvalidContext` exist for
                # `context.traps`/`.flags` bookkeeping only); checked
                # directly against a real interpreter rather than
                # against `_pydecimal.py`'s own source, which does NOT
                # match the C-accelerated module actually installed.
                raise InvalidOperation(
                    "Invalid literal for Decimal: " + repr(value))
            kind = parsed[0]
            if kind == "finite":
                _, sign, digits, exp = parsed
                self._sign, self._int, self._exp = sign, digits, exp
                self._is_special = False
            elif kind == "F":
                _, sign = parsed
                self._sign, self._int, self._exp = sign, "0", "F"
                self._is_special = True
            else:
                _, sign, payload = parsed
                self._sign, self._int, self._exp = sign, payload, kind
                self._is_special = True
            return
        if isinstance(value, float):
            other = Decimal.from_float(value)
            self._sign = other._sign
            self._int = other._int
            self._exp = other._exp
            self._is_special = other._is_special
            return
        if isinstance(value, (tuple, list)):
            if len(value) != 3:
                raise ValueError(
                    "Invalid tuple size in creation of Decimal from list "
                    "or tuple. The list or tuple should have exactly "
                    "three elements.")
            sign, digit_part, exp_part = value
            if not (isinstance(sign, int) and sign in (0, 1)):
                raise ValueError(
                    "Invalid sign. The first value in the tuple should "
                    "be an integer; either 0 for a positive number or 1 "
                    "for a negative number.")
            self._sign = sign
            if exp_part == "F":
                self._int = "0"
                self._exp = "F"
                self._is_special = True
                return
            digits = []
            for d in digit_part:
                if isinstance(d, int) and 0 <= d <= 9:
                    if digits or d != 0:
                        digits.append(d)
                else:
                    raise ValueError(
                        "The second value in the tuple must be composed "
                        "of integers in the range 0 through 9.")
            if exp_part in ("n", "N"):
                self._int = "".join(str(d) for d in digits)
                self._exp = exp_part
                self._is_special = True
            elif isinstance(exp_part, int):
                self._int = "".join(str(d) for d in digits) or "0"
                self._exp = exp_part
                self._is_special = False
            else:
                raise ValueError(
                    "The third value in the tuple must be an integer, "
                    "or one of the strings 'F', 'n', 'N'.")
            return
        raise TypeError("Cannot convert " + repr(value) + " to Decimal")

    @classmethod
    def from_float(cls, f):
        """Exact -- `Decimal.from_float(0.1)` is the full binary value
        `0.1000000000000000055511151231257827021181583404541015625`,
        not `Decimal("0.1")`."""
        if isinstance(f, bool):
            f = int(f)
        if isinstance(f, int):
            sign = 0 if f >= 0 else 1
            return _dec_from_triple(sign, str(abs(f)), 0)
        if isinstance(f, float):
            if math.isnan(f) or math.isinf(f):
                # Reuses the string parser, exactly as CPython's own
                # `from_float` does: `repr(float('nan'))` is `'nan'`,
                # `repr(float('-inf'))` is `'-inf'`.
                return cls(repr(f))
            sign = 0 if math.copysign(1.0, f) == 1.0 else 1
            numer, denom = abs(f).as_integer_ratio()
            k = denom.bit_length() - 1  # denom is a power of two
            coeff = str(numer * 5 ** k)
            result = _dec_from_triple(sign, coeff, -k)
            return result if cls is Decimal else cls(result)
        raise TypeError("argument must be int or float.")

    # ── predicates and introspection ────────────────────────────────────

    def is_nan(self):
        return self._is_special and self._exp in ("n", "N")

    def is_qnan(self):
        return self._is_special and self._exp == "n"

    def is_snan(self):
        return self._is_special and self._exp == "N"

    def is_infinite(self):
        return self._is_special and self._exp == "F"

    def is_finite(self):
        return not self._is_special

    def is_zero(self):
        return not self._is_special and self._int == "0"

    def is_signed(self):
        return self._sign == 1

    def as_tuple(self):
        return DecimalTuple(self._sign, tuple(int(c) for c in self._int), self._exp)

    def as_integer_ratio(self):
        if self._is_special:
            if self.is_nan():
                raise ValueError("cannot convert NaN to integer ratio")
            raise OverflowError("cannot convert Infinity to integer ratio")
        if self._int == "0":
            return (0, 1)
        numer = int(self._int)
        if self._exp >= 0:
            numer, denom = numer * 10 ** self._exp, 1
        else:
            fives = -self._exp
            while fives > 0 and numer % 5 == 0:
                numer //= 5
                fives -= 1
            twos = -self._exp
            shift = min((numer & -numer).bit_length() - 1, twos)
            if shift:
                numer >>= shift
                twos -= shift
            denom = 5 ** fives << twos
        if self._sign:
            numer = -numer
        return (numer, denom)

    def adjusted(self):
        """The exponent of the leftmost digit -- `Decimal("314.5")
        .adjusted()` is 2. 0 for a special value."""
        if self._is_special:
            return 0
        return self._exp + len(self._int) - 1

    # ── string form -- a field-for-field port of CPython's own         ──
    # ── `_pydecimal.Decimal.__str__`; see the module docstring.        ──

    def __repr__(self):
        return "Decimal('" + str(self) + "')"

    def __str__(self):
        sign = "-" if self._sign else ""
        if self._is_special:
            if self._exp == "F":
                return sign + "Infinity"
            if self._exp == "n":
                return sign + "NaN" + self._int
            return sign + "sNaN" + self._int

        leftdigits = self._exp + len(self._int)
        if self._exp <= 0 and leftdigits > -6:
            dotplace = leftdigits
        else:
            dotplace = 1

        if dotplace <= 0:
            intpart = "0"
            fracpart = "." + "0" * (-dotplace) + self._int
        elif dotplace >= len(self._int):
            intpart = self._int + "0" * (dotplace - len(self._int))
            fracpart = ""
        else:
            intpart = self._int[:dotplace]
            fracpart = "." + self._int[dotplace:]

        if leftdigits == dotplace:
            exp = ""
        else:
            power = leftdigits - dotplace
            letter = "E" if getcontext().capitals else "e"
            exp = letter + ("+" if power >= 0 else "") + str(power)

        return sign + intpart + fracpart + exp

    # ── truthiness and hashing ──────────────────────────────────────────

    def __bool__(self):
        return self._is_special or self._int != "0"

    def __hash__(self):
        """Agrees with `hash(int)` and `hash(float)` for an equal value
        -- a direct restatement of CPython's own `Decimal.__hash__`
        (which is the simpler formula for a base-10 rational, since a
        `Decimal`'s implied denominator is always a power of ten);
        `fractions.Fraction.__hash__` implements the same
        `_PyHASH_MODULUS`/`_PyHASH_INF` rule by the general
        denominator-inverse route, for comparison."""
        if self._is_special:
            if self._exp == "N":
                raise TypeError("Cannot hash a signaling NaN value.")
            if self._exp == "n":
                return object.__hash__(self)
            return -_PyHASH_INF if self._sign else _PyHASH_INF
        if self._exp >= 0:
            exp_hash = pow(10, self._exp, _PyHASH_MODULUS)
        else:
            exp_hash = pow(_PyHASH_10INV, -self._exp, _PyHASH_MODULUS)
        hash_ = int(self._int) * exp_hash % _PyHASH_MODULUS
        ans = hash_ if self >= 0 else -hash_
        return -2 if ans == -1 else ans

    # ── comparisons ──────────────────────────────────────────────────────

    def __eq__(self, other):
        o = _coerce_compare(other)
        if o is None:
            return NotImplemented
        if self.is_nan() or o.is_nan():
            return False
        return _cmp_values(self, o) == 0

    def __ne__(self, other):
        result = self.__eq__(other)
        return result if result is NotImplemented else not result

    def __lt__(self, other):
        o = _coerce_compare(other)
        if o is None:
            return NotImplemented
        if self.is_nan() or o.is_nan():
            raise InvalidOperation("comparison involving NaN")
        return _cmp_values(self, o) < 0

    def __le__(self, other):
        o = _coerce_compare(other)
        if o is None:
            return NotImplemented
        if self.is_nan() or o.is_nan():
            raise InvalidOperation("comparison involving NaN")
        return _cmp_values(self, o) <= 0

    def __gt__(self, other):
        o = _coerce_compare(other)
        if o is None:
            return NotImplemented
        if self.is_nan() or o.is_nan():
            raise InvalidOperation("comparison involving NaN")
        return _cmp_values(self, o) > 0

    def __ge__(self, other):
        o = _coerce_compare(other)
        if o is None:
            return NotImplemented
        if self.is_nan() or o.is_nan():
            raise InvalidOperation("comparison involving NaN")
        return _cmp_values(self, o) >= 0

    # ── sign, rounding to context precision ─────────────────────────────

    def __neg__(self):
        context = getcontext()
        if self._is_special:
            nan = _nan_result(self)
            if nan is not None:
                return nan
            return _dec_from_triple(0 if self._sign else 1, "0", "F", True)
        if not self and context.rounding != ROUND_FLOOR:
            result = _dec_from_triple(0, self._int, self._exp)
        else:
            result = _dec_from_triple(0 if self._sign else 1, self._int, self._exp)
        return result._fix(context)

    def __pos__(self):
        context = getcontext()
        if self._is_special:
            nan = _nan_result(self)
            if nan is not None:
                return nan
            return Decimal(self)
        return _dec_from_triple(self._sign, self._int, self._exp)._fix(context)

    def __abs__(self):
        return self.__neg__() if self._sign else self.__pos__()

    # ── arithmetic ───────────────────────────────────────────────────────

    def __add__(self, other):
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        nan = _nan_result(self, o)
        if nan is not None:
            return nan
        context = getcontext()
        si, oi = _inf_sign(self), _inf_sign(o)
        if si or oi:
            if si and oi:
                if si != oi:
                    raise InvalidOperation("-INF + INF")
                return _dec_from_triple(0 if si > 0 else 1, "0", "F", True)
            winner = self if si else o
            return _dec_from_triple(0 if _inf_sign(winner) > 0 else 1, "0", "F", True)
        exp = min(self._exp, o._exp)
        a = int(self._int) * 10 ** (self._exp - exp)
        if self._sign:
            a = -a
        b = int(o._int) * 10 ** (o._exp - exp)
        if o._sign:
            b = -b
        total = a + b
        if total == 0:
            sign = 1 if (context.rounding == ROUND_FLOOR
                        and self._sign != o._sign) else 0
            coeff = "0"
        else:
            sign = 1 if total < 0 else 0
            coeff = str(abs(total))
        return _dec_from_triple(sign, coeff, exp)._fix(context)

    def __radd__(self, other):
        # NOT `__radd__ = __add__`: this frontend does not resolve a
        # class-body alias to a method defined earlier in the SAME
        # class body (`NameError: name '__add__' is not defined` at
        # runtime) -- checked directly, and no other bundled module
        # uses that spelling either. Addition is commutative here
        # (both operands go through the same exponent-alignment path
        # regardless of which side called in), so a one-line forward
        # is exact, not an approximation of the real thing.
        return self.__add__(other)

    def __sub__(self, other):
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        return self.__add__(_negated(o))

    def __rsub__(self, other):
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        return o.__add__(_negated(self))

    def __mul__(self, other):
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        nan = _nan_result(self, o)
        if nan is not None:
            return nan
        context = getcontext()
        result_sign = self._sign ^ o._sign
        si, oi = _inf_sign(self), _inf_sign(o)
        if si:
            if not o:
                raise InvalidOperation("(+-)INF * 0")
            return _dec_from_triple(result_sign, "0", "F", True)
        if oi:
            if not self:
                raise InvalidOperation("0 * (+-)INF")
            return _dec_from_triple(result_sign, "0", "F", True)
        coeff = int(self._int) * int(o._int)
        exp = self._exp + o._exp
        return _dec_from_triple(
            result_sign, str(coeff) if coeff else "0", exp)._fix(context)

    def __rmul__(self, other):
        return self.__mul__(other)

    def __truediv__(self, other):
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        nan = _nan_result(self, o)
        if nan is not None:
            return nan
        context = getcontext()
        sign = self._sign ^ o._sign
        si, oi = _inf_sign(self), _inf_sign(o)
        if si and oi:
            raise InvalidOperation("(+-)INF/(+-)INF")
        if si:
            return _dec_from_triple(sign, "0", "F", True)
        if oi:
            return _dec_from_triple(sign, "0", self._exp - o._exp)
        if not o:
            if not self:
                raise InvalidOperation("0 / 0")
            raise DivisionByZero("x / 0")
        if not self:
            return _dec_from_triple(sign, "0", self._exp - o._exp)._fix(context)
        # Enough extra digits that the ONE final rounding step below is
        # correctly rounded, with a "sticky bit" nudge (`coeff % 5 ==
        # 0`) standing in for the discarded remainder when it is
        # nonzero -- straight from CPython's own `__truediv__`.
        shift = len(o._int) - len(self._int) + context.prec + 1
        exp = self._exp - o._exp - shift
        a, b = int(self._int), int(o._int)
        # `//` and `%` SEPARATELY, not the `divmod()` BUILTIN -- see the
        # module docstring's COMPILER FINDING. `numer // b, numer % b`
        # computes the same pair `divmod` would, correctly, on this
        # runtime.
        if shift >= 0:
            numer = a * 10 ** shift
            coeff, remainder = numer // b, numer % b
        else:
            denom = b * 10 ** -shift
            coeff, remainder = a // denom, a % denom
        if remainder:
            if coeff % 5 == 0:
                coeff += 1
        else:
            ideal_exp = self._exp - o._exp
            while exp < ideal_exp and coeff % 10 == 0:
                coeff //= 10
                exp += 1
        return _dec_from_triple(sign, str(coeff), exp)._fix(context)

    def __rtruediv__(self, other):
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        return o.__truediv__(self)

    def _int_divide(self, other, context):
        """`(self // other, self % other)`, EXACT and then truncated
        toward zero -- the decimal-spec rule, not Python's floor rule
        -- for two finite operands with a nonzero `other`. Raises
        `DivisionImpossible` if the exact quotient would need more than
        `context.prec` digits, the one precision bound the spec places
        on integer division."""
        ideal_exp = min(self._exp, other._exp)
        ca = int(self._int) * 10 ** (self._exp - ideal_exp)
        cb = int(other._int) * 10 ** (other._exp - ideal_exp)
        # `//` and `%` separately -- not the `divmod()` builtin, see the
        # module docstring's COMPILER FINDING. Both nonnegative here, so
        # this already truncates.
        q, r = ca // cb, ca % cb
        if q >= 10 ** context.prec:
            raise InvalidOperation(
                "quotient too large in //, % or divmod")
        quotient = _dec_from_triple(self._sign ^ other._sign, str(q) if q else "0", 0)
        remainder = _dec_from_triple(self._sign, str(r) if r else "0", ideal_exp)
        return quotient, remainder

    def __floordiv__(self, other):
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        nan = _nan_result(self, o)
        if nan is not None:
            return nan
        context = getcontext()
        if _inf_sign(self):
            if _inf_sign(o):
                raise InvalidOperation("INF // INF")
            return _dec_from_triple(self._sign ^ o._sign, "0", "F", True)
        if not o:
            if not self:
                raise InvalidOperation("0 // 0")
            raise DivisionByZero("x // 0")
        if _inf_sign(o):
            return _dec_from_triple(self._sign ^ o._sign, "0", 0)
        quotient, _remainder = self._int_divide(o, context)
        return quotient

    def __rfloordiv__(self, other):
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        return o.__floordiv__(self)

    def __mod__(self, other):
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        nan = _nan_result(self, o)
        if nan is not None:
            return nan
        context = getcontext()
        if _inf_sign(self):
            raise InvalidOperation("INF % x")
        if not o:
            if self:
                raise InvalidOperation("x % 0")
            raise InvalidOperation("0 % 0")
        if _inf_sign(o):
            return _dec_from_triple(self._sign, self._int, self._exp)._fix(context)
        _quotient, remainder = self._int_divide(o, context)
        return remainder._fix(context)

    def __rmod__(self, other):
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        return o.__mod__(self)

    def __divmod__(self, other):
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        nan = _nan_result(self, o)
        if nan is not None:
            return (nan, nan)
        context = getcontext()
        if _inf_sign(self):
            raise InvalidOperation(
                "divmod(INF, INF)" if _inf_sign(o) else "INF % x")
        if not o:
            if not self:
                raise InvalidOperation("divmod(0, 0)")
            raise DivisionByZero("x // 0")
        if _inf_sign(o):
            quotient = _dec_from_triple(self._sign ^ o._sign, "0", 0)
            remainder = _dec_from_triple(
                self._sign, self._int, self._exp)._fix(context)
            return (quotient, remainder)
        quotient, remainder = self._int_divide(o, context)
        return (quotient, remainder._fix(context))

    def __rdivmod__(self, other):
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        return o.__divmod__(self)

    def __pow__(self, other, modulo=None):
        if modulo is not None:
            return NotImplemented
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        nan = _nan_result(self, o)
        if nan is not None:
            return nan
        if self._is_special or o._is_special:
            raise InvalidOperation(
                "power involving an infinite operand is not supported")
        context = getcontext()
        if o._exp >= 0:
            n = int(o._int) * 10 ** o._exp
        else:
            scale = 10 ** -o._exp
            coeff = int(o._int)
            if coeff % scale != 0:
                raise InvalidOperation(
                    "Decimal ** Decimal with a non-integer exponent "
                    "needs correctly-rounded ln/exp and is not "
                    "supported -- see the module docstring")
            n = coeff // scale
        if o._sign:
            n = -n
        if n == 0:
            if not self:
                raise InvalidOperation("0 ** 0")
            return _dec_from_triple(0, "1", 0)._fix(context)
        if not self:
            if n < 0:
                raise DivisionByZero("0 ** negative power")
            sign = self._sign & (abs(n) % 2)
            return _dec_from_triple(sign, "0", 0)
        coeff = int(self._int) ** abs(n)
        exp = self._exp * abs(n)
        sign = self._sign & (abs(n) % 2)
        result = _dec_from_triple(sign, str(coeff), exp)
        if n > 0:
            return result._fix(context)
        return Decimal(1).__truediv__(result)

    def __rpow__(self, other):
        o = _coerce_operand(other)
        if o is None:
            return NotImplemented
        return o.__pow__(self)

    # ── conversion to Python's own numeric types ────────────────────────

    def __int__(self):
        if self._is_special:
            if self.is_nan():
                raise ValueError("cannot convert NaN to integer")
            raise OverflowError("cannot convert Infinity to integer")
        s = -1 if self._sign else 1
        if self._exp >= 0:
            return s * int(self._int) * 10 ** self._exp
        return s * int(self._int[:self._exp] or "0")

    def __trunc__(self):
        return self.__int__()

    def __floor__(self):
        """PEP 3141, and `math.floor(Decimal("3.5"))` is how it is reached.

        TOWARD MINUS INFINITY, which is what makes this different from
        `__trunc__`: `__int__` rounds toward zero, so a negative number
        with a fraction has to go one further down. CPython's own
        `__floor__` is exactly this pair of lines.
        """
        floored = self.__int__()
        if self._sign and floored != self:
            return floored - 1
        return floored

    def __ceil__(self):
        """The other half, toward plus infinity."""
        ceiled = self.__int__()
        if not self._sign and ceiled != self:
            return ceiled + 1
        return ceiled

    def __float__(self):
        if self._is_special:
            if self.is_nan():
                if self.is_snan():
                    raise ValueError("Cannot convert signaling NaN to float")
                return float("-nan") if self._sign else float("nan")
            return float("-inf") if self._sign else float("inf")
        return float(str(self))

    def __round__(self, ndigits=None):
        if ndigits is not None:
            if not isinstance(ndigits, int):
                raise TypeError("Second argument to round should be integral")
            return self.quantize(_dec_from_triple(0, "1", -ndigits))
        if self._is_special:
            if self.is_nan():
                raise ValueError("cannot round a NaN")
            raise OverflowError("cannot round an infinity")
        return int(self._rescale(0, ROUND_HALF_EVEN))

    # ── rounding and rescaling to an explicit exponent ──────────────────

    def _rescale(self, exp, rounding):
        """Self, re-expressed with exponent `exp` -- padding with zeros
        if `exp` is smaller (in magnitude terms, no information lost)
        or rounding with `rounding` if `exp` is larger."""
        if self._is_special:
            return Decimal(self)
        if not self:
            return _dec_from_triple(self._sign, "0", exp)
        if self._exp >= exp:
            return _dec_from_triple(
                self._sign, self._int + "0" * (self._exp - exp), exp)
        keep = len(self._int) + self._exp - exp
        working = self
        if keep < 0:
            working = _dec_from_triple(self._sign, "1", exp - 1)
            keep = 0
        changed = _ROUNDING_FUNCS[rounding](working._int, keep, working._sign)
        coeff = working._int[:keep] or "0"
        if changed == 1:
            coeff = str(int(coeff) + 1)
        return _dec_from_triple(self._sign, coeff, exp)

    def _fix_nan(self, context):
        payload = self._int
        if len(payload) > context.prec:
            payload = payload[len(payload) - context.prec:].lstrip("0")
        return _dec_from_triple(self._sign, payload, self._exp, True)

    def _fix(self, context):
        """Round `self` to `context.prec` significant digits if it has
        more -- the ONE rounding step every arithmetic result passes
        through, after being computed exactly. No `Emin`/`Emax`
        clamping or `Overflow`/`Subnormal` signalling -- see NOT
        COVERED in the module docstring."""
        if self._is_special:
            return self._fix_nan(context) if self.is_nan() else self
        if self._int == "0" or len(self._int) <= context.prec:
            return self
        exp_min = len(self._int) + self._exp - context.prec
        keep = context.prec
        changed = _ROUNDING_FUNCS[context.rounding](self._int, keep, self._sign)
        coeff = self._int[:keep] or "0"
        if changed == 1:
            coeff = str(int(coeff) + 1)
            if len(coeff) > context.prec:
                coeff = coeff[:-1]
                exp_min += 1
        return _dec_from_triple(self._sign, coeff, exp_min)

    def quantize(self, exp, rounding=None, context=None):
        """Re-express `self` with the same exponent as `exp` (another
        `Decimal`, or anything `int`/`float`/`Decimal` construction
        accepts). `Decimal("3.14159").quantize(Decimal("1.00"))` is
        `Decimal("3.14")`."""
        target = exp if isinstance(exp, Decimal) else Decimal(exp)
        if context is None:
            context = getcontext()
        if rounding is None:
            rounding = context.rounding
        nan = _nan_result(self, target)
        if nan is not None:
            return nan
        if self._is_special or target._is_special:
            if _inf_sign(self) and _inf_sign(target):
                return Decimal(self)
            raise InvalidOperation("quantize with one INF")
        if not self:
            return _dec_from_triple(self._sign, "0", target._exp)._fix(context)
        if self.adjusted() - target._exp + 1 > context.prec:
            raise InvalidOperation(
                "quantize result has too many digits for current context")
        result = self._rescale(target._exp, rounding)
        if len(result._int) > context.prec:
            raise InvalidOperation(
                "quantize result has too many digits for current context")
        return result._fix(context)

    def to_integral_value(self, rounding=None, context=None):
        """Round to the nearest integer, WITHOUT the loss-of-precision
        an ordinary rounding operation would otherwise be flagged for
        (moot here, since nothing is flagged -- kept as a separate
        method because CPython's is, and because it reads better than
        `quantize(Decimal(1))` at a call site)."""
        if context is None:
            context = getcontext()
        if rounding is None:
            rounding = context.rounding
        if self._is_special:
            nan = _nan_result(self)
            return nan if nan is not None else Decimal(self)
        if self._exp >= 0:
            return Decimal(self)
        return self._rescale(0, rounding)

    def to_integral(self, rounding=None, context=None):
        return self.to_integral_value(rounding, context)


def _dec_from_triple(sign, digits, exp, special=False):
    """Build a `Decimal` directly from already-normalized parts, with
    NO validation and no digit-string trimming -- the internal fast
    path every arithmetic result above goes through, mirroring
    CPython's own `_dec_from_triple`. Bypasses `__init__` entirely
    (`object.__new__` with no constructor call, the same pattern
    `enum.py`'s member construction and `copy.py`'s `deepcopy` already
    use in this codebase) rather than routing through
    `Decimal(0)`-then-overwrite, since this runs on every arithmetic
    operation."""
    self = object.__new__(Decimal)
    self._sign = sign
    self._int = digits
    self._exp = exp
    self._is_special = special
    return self


def _negated(x):
    """A copy of `x` with its sign bit flipped -- works for a finite
    value, an infinity or a NaN alike, since only `_sign` changes."""
    return _dec_from_triple(0 if x._sign else 1, x._int, x._exp, x._is_special)


def _coerce_operand(other):
    """`other` as a `Decimal` for an ARITHMETIC operator -- `Decimal`
    or `int`/`bool` only. Deliberately does NOT accept `float`: real
    `decimal` never mixes arithmetic with `float`, only construction
    and comparison do -- see the module docstring. `None` means "not
    usable here", for the caller to turn into `NotImplemented`."""
    if isinstance(other, Decimal):
        return other
    if isinstance(other, bool):
        return Decimal(int(other))
    if isinstance(other, int):
        return Decimal(other)
    return None


def _coerce_compare(other):
    """The same, but for a COMPARISON operator, which -- unlike
    arithmetic -- does accept a `float`, exactly (via `from_float`)."""
    if isinstance(other, Decimal):
        return other
    if isinstance(other, bool):
        return Decimal(int(other))
    if isinstance(other, int):
        return Decimal(other)
    if isinstance(other, float):
        return Decimal.from_float(other)
    return None


def _nan_result(a, b=None):
    """If `a` or `b` is a NaN, the quiet-NaN result a binary (or,
    with `b` omitted, unary) operation on it should produce --
    propagating a signaling operand's payload, after raising
    `InvalidOperation` for it, exactly as encountering a signaling NaN
    always does. Returns `None` when neither operand is a NaN at all,
    for the caller to fall through to its ordinary computation."""
    a_signaling = a.is_snan()
    b_signaling = b is not None and b.is_snan()
    if a_signaling:
        raise InvalidOperation("signaling NaN encountered: " + str(a))
    if b_signaling:
        raise InvalidOperation("signaling NaN encountered: " + str(b))
    if a.is_nan():
        return _dec_from_triple(a._sign, a._int, "n", True)
    if b is not None and b.is_nan():
        return _dec_from_triple(b._sign, b._int, "n", True)
    return None


def _inf_sign(x):
    """0 if `x` is finite or a NaN, 1 if `x` is `+Infinity`, -1 if
    `x` is `-Infinity`."""
    if x._is_special and x._exp == "F":
        return -1 if x._sign else 1
    return 0


def _cmp_values(a, b):
    """-1/0/1 for `a` compared to `b`, for two NON-NAN operands --
    exact rational comparison via a common exponent, using arbitrary-
    precision ints rather than CPython's own digit-string padding, but
    answering the same question. Infinities rank by `_inf_sign`
    (-1/0/1), which already places `-Infinity` below every finite value
    and `+Infinity` above it."""
    ai, bi = _inf_sign(a), _inf_sign(b)
    if ai or bi:
        if ai == bi:
            return 0
        return -1 if ai < bi else 1
    e = min(a._exp, b._exp)
    av = int(a._int) * 10 ** (a._exp - e)
    if a._sign:
        av = -av
    bv = int(b._int) * 10 ** (b._exp - e)
    if b._sign:
        bv = -bv
    if av == bv:
        return 0
    return -1 if av < bv else 1


# ── context ───────────────────────────────────────────────────────────

class Context:
    """`prec` (significant digits kept after rounding) and `rounding`
    (one of the eight `ROUND_*` constants) -- see the module docstring
    for what a full `decimal.Context` has that this one deliberately
    does not (`Emin`/`Emax`/`clamp`/`flags`/`traps`)."""

    def __init__(self, prec=28, rounding=ROUND_HALF_EVEN, capitals=1, **_ignored):
        if not isinstance(prec, int) or isinstance(prec, bool) or prec < 1:
            raise ValueError("prec must be an integer >= 1")
        if rounding not in _ROUNDING_MODES:
            raise TypeError(repr(rounding) + ": invalid rounding mode")
        self.prec = prec
        self.rounding = rounding
        self.capitals = capitals

    def copy(self):
        return Context(prec=self.prec, rounding=self.rounding, capitals=self.capitals)

    def __repr__(self):
        return ("Context(prec=" + repr(self.prec) + ", rounding="
                + repr(self.rounding) + ", capitals=" + repr(self.capitals) + ")")


# A one-element list rather than a rebound module-level name -- the
# same "mutate in place, never rebind" convention `warnings.py`'s own
# `filters` list uses for its module-level state, and it sidesteps
# needing the `global` statement inside `getcontext`/`setcontext`/
# `_ContextManager` entirely (no bundled module uses `global` anywhere
# today, so this stays on ground already proven rather than new ground).
_context_box = [None]


def getcontext():
    """This "thread"'s current context -- in practice just THE current
    context, since this runtime has no threads to keep separate ones
    for (real CPython's is genuinely thread-local, via `contextvars`)."""
    if _context_box[0] is None:
        _context_box[0] = Context()
    return _context_box[0]


def setcontext(context):
    _context_box[0] = context


class _ContextManager:
    def __init__(self, new_context):
        self.new_context = new_context.copy()
        self.saved_context = None

    def __enter__(self):
        self.saved_context = getcontext()
        _context_box[0] = self.new_context
        return self.new_context

    def __exit__(self, exc_type, exc_value, traceback):
        _context_box[0] = self.saved_context


def localcontext(ctx=None, **kwargs):
    """`with localcontext(): ...` runs the block against a COPY of the
    current context, restored on exit; `with localcontext(prec=6):
    ...` starts that copy with `prec` already overridden."""
    if ctx is None:
        ctx = getcontext()
    manager = _ContextManager(ctx)
    for key, value in kwargs.items():
        setattr(manager.new_context, key, value)
    return manager


DefaultContext = Context(prec=28, rounding=ROUND_HALF_EVEN, capitals=1)
