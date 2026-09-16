# COVERAGE: construction from an int, a str (CPython's grammar, including
# underscores, whitespace, Infinity/NaN/sNaN with a payload), a float via
# Decimal(x)/Decimal.from_float (exact binary conversion), a
# (sign, digits, exponent) tuple, and copying another Decimal; exact decimal
# arithmetic + - * / // % ** (0.1+0.2 == 0.3 exactly, // and % truncate
# toward zero with the DIVIDEND's sign per the decimal spec rather than
# Python's floor rule); comparisons < <= > >= == != against int/float/
# Decimal; __hash__ agreeing with hash(int)/hash(float) for an equal value;
# str/repr across magnitudes (small, large, scientific threshold,
# trailing-zero preservation); Context/getcontext().prec affecting a
# computation; all eight rounding modes, exercised via quantize;
# to_integral_value; is_nan/is_snan/is_infinite/is_finite/is_zero/is_signed;
# as_tuple; DivisionByZero/InvalidOperation as real, raised exceptions.
# NOT covered: numbers interop, Emin/Emax/traps/flags, sqrt/ln/exp,
# non-integer ** exponents, __format__, copy_sign and friends -- see
# bundled/decimal.py's module docstring for the full, honest list, including
# two compiler findings: a bundled exception with a second BUILTIN base
# loses isinstance/except-matching for that base, and the divmod() BUILTIN
# is unreliable on this compiler for plain ints (worked around throughout
# by never calling it -- `//`/`%` separately, or `.__divmod__()` directly).
#
# Run under CPython and under uasm; the outputs must be identical, so
# every assertion below is written against the SPECIFICATION rather than
# against whatever uasm currently prints.
import decimal
from decimal import (Decimal, Context, getcontext, setcontext, localcontext,
                     ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_HALF_DOWN,
                     ROUND_UP, ROUND_DOWN, ROUND_CEILING, ROUND_FLOOR,
                     ROUND_05UP, InvalidOperation, DivisionByZero)

# ── construction ─────────────────────────────────────────────────────────

print("== construction ==")
print(Decimal(3))
print(Decimal(-3))
print(Decimal(0))
print(Decimal())
print(Decimal(True))
print(Decimal("3.14"))
print(Decimal("-3.14"))
print(Decimal("  3.14  "))
print(Decimal(".5"))
print(Decimal("5."))
print(Decimal("+5"))
print(Decimal("123.45e10"))
print(Decimal("123.45E-10"))
print(Decimal("1_000.5_00"))
print(Decimal("0.00"))
print(Decimal("-0"))
print(Decimal(Decimal("3.14")))

# from a float: exact binary conversion, not decimal rounding.
print(Decimal(1.5))
print(Decimal(0.1))
print(Decimal(0.5))
print(Decimal.from_float(2.0))
print(Decimal.from_float(-0.0))
print(Decimal.from_float(10))

# from a (sign, digits, exponent) tuple.
print(Decimal((0, (3, 1, 4), -2)))
print(Decimal((1, (1, 2, 3), 0)))
print(Decimal((0, (0,), 0)))

# special values
print(Decimal("inf"))
print(Decimal("-Infinity"))
print(Decimal("nan"))
print(Decimal("-nan123"))
print(Decimal("snan"))

print(Decimal("1_"))             # blind underscore-stripping: same as "1"
for bad in ("", "  ", "abc", "1.2.3", "e5", "."):
    try:
        Decimal(bad)
        print("NO ERROR for", repr(bad))
    except InvalidOperation as e:
        print(type(e).__name__, repr(bad))
try:
    Decimal([1, 2])
except ValueError as e:
    print(type(e).__name__)
try:
    Decimal(object())
except TypeError as e:
    print(type(e).__name__)

# ── exact decimal arithmetic -- the entire point of the module ─────────────

print("== exact arithmetic ==")
print(Decimal("0.1") + Decimal("0.2"))
print(Decimal("0.1") + Decimal("0.2") == Decimal("0.3"))
print(Decimal("1.1") * Decimal("1.1"))
print(Decimal("1.30") // Decimal("0.3"))
print(Decimal("1.30") % Decimal("0.3"))
print(Decimal("1.33") + Decimal("1.27"))
print(Decimal("12.34") + Decimal("3.87") - Decimal("18.41"))
print(Decimal(3) / Decimal(4))
print(Decimal(1) / Decimal(4))
print(Decimal(2) ** 10)
print(Decimal(2) ** -3)
print(Decimal(10) ** -2)
print(Decimal("2.50") ** 2)
print(Decimal("-2") ** 3)
print(Decimal(5) ** 0)

# // and % truncate toward zero with the DIVIDEND's sign -- NOT Python's
# floor rule for int/float.
print(Decimal(7) // Decimal(2), Decimal(7) % Decimal(2))
print(Decimal(-7) // Decimal(2), Decimal(-7) % Decimal(2))
print(Decimal(7) // Decimal(-2), Decimal(7) % Decimal(-2))
print(Decimal(8).__divmod__(Decimal(3)))   # NOT divmod(...) -- see the
                                            # module docstring's COMPILER
                                            # FINDING on the builtin

print(-Decimal("3.14"), +Decimal("-3.14"), abs(Decimal("-3.14")))
print(bool(Decimal(0)), bool(Decimal("0.0")), bool(Decimal("1")))

try:
    Decimal(1) / Decimal(0)
except DivisionByZero as e:
    print(type(e).__name__)
try:
    Decimal(0) / Decimal(0)
except InvalidOperation as e:
    print(type(e).__name__)
try:
    Decimal(1) // Decimal(0)
except DivisionByZero as e:
    print(type(e).__name__)
try:
    Decimal(1) % Decimal(0)
except InvalidOperation as e:
    print(type(e).__name__)
try:
    Decimal(0) ** 0
except InvalidOperation as e:
    print(type(e).__name__)
try:
    Decimal("1.5") + 1.0
except TypeError as e:
    print(type(e).__name__)

# ── comparisons ─────────────────────────────────────────────────────────

print("== comparisons ==")
print(Decimal("0.1") < Decimal("0.2"))
print(Decimal("0.30") == Decimal("0.3"))
print(Decimal(2) == 2, Decimal(2) == 2.0, Decimal("1.5") == 1.5)
print(Decimal("1.5") < 2, Decimal("1.5") < 2.0)
print(Decimal(3) > Decimal(2), Decimal(3) >= Decimal(3))
print(Decimal("1.50") == Decimal("1.5"))   # equal despite different exponent
n = Decimal("nan")
print(n == n, n != n)
try:
    n < Decimal(1)
except InvalidOperation as e:
    print(type(e).__name__)
print(sorted([Decimal(3), Decimal(1), Decimal(2)]))

# ── hashing: MUST agree with hash(int)/hash(float) for an equal value ──────

print("== hashing ==")
print(hash(Decimal(2)) == hash(2))
print(hash(Decimal(-7)) == hash(-7))
print(hash(Decimal("0.5")) == hash(0.5))
print(hash(Decimal("0.25")) == hash(0.25))
print(hash(Decimal("100")) == hash(100))
print(Decimal(2) in {2})
print(Decimal("0.5") in {0.5})
d = {0.5: "half"}
print(d[Decimal("0.5")])

# ── str/repr across magnitudes ──────────────────────────────────────────

print("== str/repr ==")
print(str(Decimal("3.14")), repr(Decimal("3.14")))
print(str(Decimal(5)), repr(Decimal(5)))
print(str(Decimal("-3.14")))
print(str(Decimal("1.50") + Decimal("0")))   # trailing zero preserved
print(str(Decimal("0.00")))
print(str(Decimal("100.00")))
print(str(Decimal("0.0000001")))             # small: switches to scientific
print(str(Decimal("0.000001")))              # small: still plain
print(str(Decimal("123456789012345678")))    # large exact integer
print(str(Decimal("123.45e10")))
print(str(Decimal("123.45e-10")))
print(str(Decimal("1E+5")))
print(str(Decimal("-0")))
print(str(Decimal("inf")), str(Decimal("-inf")))
print(str(Decimal("nan")), str(Decimal("nan123")))

# ── Context: prec affects a computation ─────────────────────────────────

print("== context prec ==")
print(getcontext().prec)
c = getcontext()
c.prec = 6
print(Decimal(1) / Decimal(3))
c.prec = 28
print(Decimal(1) / Decimal(3))

with localcontext() as lc:
    lc.prec = 3
    print(Decimal(1) / Decimal(3))
    print(getcontext().prec)
print(getcontext().prec)      # restored on exit

with localcontext(prec=4):
    print(Decimal(10) / Decimal(3))
print(getcontext().prec)

ctx2 = Context(prec=5, rounding=ROUND_HALF_UP)
print(ctx2.prec, ctx2.rounding)

# ── rounding modes ───────────────────────────────────────────────────────

print("== rounding modes ==")
half = Decimal("2.5")
target = Decimal("1")
for mode in (ROUND_HALF_EVEN, ROUND_HALF_UP, ROUND_HALF_DOWN, ROUND_UP,
            ROUND_DOWN, ROUND_CEILING, ROUND_FLOOR, ROUND_05UP):
    print(mode, half.quantize(target, rounding=mode))
neg_half = Decimal("-2.5")
for mode in (ROUND_HALF_EVEN, ROUND_CEILING, ROUND_FLOOR):
    print(mode, neg_half.quantize(target, rounding=mode))
# the classic banker's-rounding spread for ROUND_HALF_EVEN specifically
for value in ("0.5", "1.5", "2.5", "3.5", "-0.5", "-1.5"):
    print(value, Decimal(value).quantize(target))

# ── quantize / to_integral_value ────────────────────────────────────────

print("== quantize / to_integral_value ==")
print(Decimal("3.14159").quantize(Decimal("1.00")))
print(Decimal("3.14159").quantize(Decimal("1.0000")))
print(Decimal("5").quantize(Decimal("1.00")))
print(Decimal("3.6").to_integral_value())
print(Decimal("-3.6").to_integral_value())
print(Decimal("3.0").to_integral_value())
print(Decimal("5").to_integral())
print(round(Decimal("3.14159"), 2))
print(round(Decimal("2.5")))
print(round(Decimal("3.5")))

# ── predicates and as_tuple ──────────────────────────────────────────────

print("== predicates ==")
nan = Decimal("nan")
inf = Decimal("inf")
neg_inf = Decimal("-inf")
finite = Decimal("3.14")
zero = Decimal("0")
print(nan.is_nan(), inf.is_nan(), finite.is_nan())
print(inf.is_infinite(), neg_inf.is_infinite(), finite.is_infinite())
print(finite.is_finite(), inf.is_finite(), nan.is_finite())
print(zero.is_zero(), finite.is_zero())
print(Decimal("-3.14").is_signed(), Decimal("3.14").is_signed())
print(Decimal("snan").is_snan(), nan.is_snan())
print(Decimal("3.14").as_tuple())
print(Decimal("-3.14").as_tuple())
print(Decimal("100").as_tuple())
print(Decimal("3.14").as_integer_ratio())
print(Decimal("0.25").as_integer_ratio())
print(Decimal("-123e5").as_integer_ratio())
print(Decimal("3.14").adjusted())
print(Decimal("0.00314").adjusted())

# ── int / float conversion ───────────────────────────────────────────────

print("== conversions ==")
print(int(Decimal("3.9")), int(Decimal("-3.9")))
print(float(Decimal("3.14")))
print(float(Decimal("0.1")))
