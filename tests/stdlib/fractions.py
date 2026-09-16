# COVERAGE: construction from (numerator, denominator), a single int, a
# single float (exact binary conversion), a str (CPython's grammar,
# including underscores and surrounding whitespace), and copying another
# Fraction; reduction to lowest terms with a positive denominator;
# .numerator, .denominator, .as_integer_ratio(), .is_integer(),
# .limit_denominator(); arithmetic + - * / // % ** with int/float/Fraction
# on either side; comparisons < <= > >= == != against int/float, including
# nan/inf; __hash__ agreeing with hash(int) and hash(float) for an equal
# value; repr (different from str); __abs__, __neg__, __pos__, __bool__,
# __float__, __int__, __trunc__, __floor__, __ceil__ (called directly AND
# through math.floor/ceil/trunc, which consult them since this module found
# that they did not -- see bundled/fractions.py's docstring);
# __round__ with half-to-even and ndigits=. NOT covered: numbers.Rational
# interop, complex, __format__, Decimal interop, pickling.
#
# Run under CPython and under uasm; the outputs must be identical, so
# every assertion below is written against the SPECIFICATION rather than
# against whatever uasm currently prints.
import math
import fractions
from fractions import Fraction

# ── construction ─────────────────────────────────────────────────────────

print("== construction ==")
print(Fraction(3, 4))
print(Fraction(-3, 4))
print(Fraction(3, -4))
print(Fraction(-3, -4))
print(Fraction(10, -8))          # reduces AND flips: -5/4
print(Fraction(6, 8))            # reduces: 3/4
print(Fraction(0, 5))            # zero always shows as 0/1
print(Fraction())                # no arguments at all: 0
print(Fraction(5))               # a single int
print(Fraction(-5))
print(Fraction(True))            # bool is Rational too: 1

# from another Fraction -- a copy, not a fresh reduction.
print(Fraction(Fraction(6, 8)))
print(Fraction(Fraction(1, 7), 5))
print(Fraction(Fraction(1, 7), Fraction(2, 3)))

# from a float: EXACT binary-to-rational conversion, not decimal rounding.
print(Fraction(0.5))
print(Fraction(0.25))
print(Fraction(-0.5))
print(Fraction(2.0))
print(Fraction(0.1))             # the long exact rational 0.1 really is
print(Fraction(1.5))

# from a string: CPython's own grammar.
print(Fraction("3/4"))
print(Fraction("-35/4"))
print(Fraction("314"))
print(Fraction("1.5"))
print(Fraction("3.1415"))
print(Fraction("-47e-2"))
print(Fraction("  -3/7  "))
print(Fraction("3 / 4"))
print(Fraction("1_000/2_000"))
print(Fraction("1_0.0_1e0_1"))
print(Fraction(".5"))
print(Fraction("5."))
print(Fraction("+5"))
for bad in ("", "  ", "abc", "1//2", "1.2.3", "3/-4", "1_", "nan", "inf"):
    try:
        Fraction(bad)
        print("NO ERROR for", repr(bad))
    except ValueError as e:
        print("ValueError", repr(bad))
try:
    Fraction(1, 0)
except ZeroDivisionError as e:
    print("ZeroDivisionError", e)
try:
    Fraction(1, 2.0)
except TypeError as e:
    print("TypeError", e)
try:
    Fraction([1, 2])
except TypeError as e:
    print("TypeError", e)

# ── attributes ────────────────────────────────────────────────────────────

print("== attributes ==")
f = Fraction(3, 4)
print(f.numerator, f.denominator)
print(f.as_integer_ratio())
print(Fraction(4, 2).is_integer(), Fraction(3, 2).is_integer())
print(Fraction(0.5).as_integer_ratio())

# ── repr and str: DIFFERENT ────────────────────────────────────────────────

print("== repr/str ==")
print(str(Fraction(3, 4)))
print(repr(Fraction(3, 4)))
print(str(Fraction(5, 1)))
print(repr(Fraction(5, 1)))
print(str(Fraction(-3, 4)))
print(repr(Fraction(-3, 4)))

# ── arithmetic: Fraction, int and float on both sides ──────────────────────

print("== arithmetic ==")
a = Fraction(1, 2)
b = Fraction(1, 3)
print(a + b, a - b, a * b, a / b, a // b, a % b, a ** 2)
print(a + 1, a - 1, a * 2, a / 2, a // 1, a % 1, a ** -1)
print(1 + a, 1 - a, 2 * a, 2 / a, 1 // a, 1 % a, 2 ** Fraction(-1, 1))
print(a + 0.5, a - 0.5, a * 0.5, a / 0.5)
print(0.5 + a, 0.5 - a, 0.5 * a, 0.5 / a)
print(Fraction(7, 2) // Fraction(3, 2), Fraction(7, 2) % Fraction(3, 2))
print(Fraction(-7, 2) // 2, Fraction(-7, 2) % 2)
print(Fraction(2, 3) ** 3, Fraction(2, 3) ** -2)
print(Fraction(-2, 3) ** 3)
print(Fraction(3, 1) ** Fraction(2, 1))     # integer-valued exponent: exact
print(2 ** Fraction(3, 1))                  # __rpow__, integer exponent
try:
    a / Fraction(0, 5)
except ZeroDivisionError as e:
    print("ZeroDivisionError", e)
try:
    a / 0
except ZeroDivisionError as e:
    print("ZeroDivisionError", e)
try:
    a // Fraction(0, 1)
except ZeroDivisionError as e:
    print("ZeroDivisionError", e)

# ── comparisons ─────────────────────────────────────────────────────────

print("== comparisons ==")
print(Fraction(1, 2) < Fraction(2, 3))
print(Fraction(1, 2) <= Fraction(1, 2))
print(Fraction(2, 3) > Fraction(1, 2))
print(Fraction(1, 2) >= Fraction(2, 3))
print(Fraction(1, 2) == Fraction(2, 4))
print(Fraction(1, 2) != Fraction(1, 3))
print(Fraction(1, 2) == 0.5, Fraction(1, 2) < 0.75, Fraction(1, 2) > 0.25)
print(Fraction(2, 1) == 2, Fraction(3, 2) < 2, Fraction(3, 2) > 1)
print(Fraction(1, 2) == float("nan"), Fraction(1, 2) < float("nan"))
print(Fraction(1, 2) < float("inf"), Fraction(1, 2) > float("-inf"))
print(1 < Fraction(3, 2), 0.4 < Fraction(1, 2))

# ── hashing: MUST agree with hash(int) / hash(float) for an equal value ────

print("== hashing ==")
print(hash(Fraction(4, 2)) == hash(2))
print(hash(Fraction(-6, 3)) == hash(-2))
print(hash(Fraction(1, 2)) == hash(0.5))
print(hash(Fraction(1, 4)) == hash(0.25))
print(hash(Fraction(3, 2)) == hash(1.5))
print(Fraction(2) in {2})
print(Fraction(1, 2) in {0.5})
d = {0.5: "half"}
print(d[Fraction(1, 2)])

# ── unary and rounding ──────────────────────────────────────────────────

print("== unary / rounding ==")
print(abs(Fraction(-3, 4)), abs(Fraction(3, 4)))
print(-Fraction(3, 4), -Fraction(-3, 4))
print(+Fraction(-3, 4))
print(bool(Fraction(0, 5)), bool(Fraction(1, 5)))
print(int(Fraction(7, 2)), int(Fraction(-7, 2)))
print(Fraction(7, 2).__trunc__(), Fraction(-7, 2).__trunc__())
print(Fraction(7, 2).__floor__(), Fraction(-7, 2).__floor__())
print(Fraction(7, 2).__ceil__(), Fraction(-7, 2).__ceil__())
print(float(Fraction(1, 4)), float(Fraction(-3, 2)))

# half-to-even: the classic banker's-rounding cases.
print(round(Fraction(1, 2)), round(Fraction(3, 2)), round(Fraction(5, 2)))
print(round(Fraction(-1, 2)), round(Fraction(-3, 2)))
print(round(Fraction(7, 2)), round(Fraction(9, 2)))
print(round(Fraction(11, 4), 1))
print(round(Fraction(355, 113), 3))
print(round(Fraction(1, 3), 4))
print(round(Fraction(25, 10), 0))

# ── limit_denominator, approximating an irrational ─────────────────────────

print("== limit_denominator ==")
pi = Fraction(math.pi)
print(pi.limit_denominator(10))
print(pi.limit_denominator(100))
print(pi.limit_denominator(1000))
print(Fraction(4321, 8765).limit_denominator(10000))
try:
    Fraction(1, 2).limit_denominator(0)
except ValueError as e:
    print("ValueError", e)

# ── sorting mixed Fraction / int / float ────────────────────────────────

print("== sorting ==")
mixed = [Fraction(3, 2), 1, 0.5, Fraction(-1, 4), 2, Fraction(7, 4), -1.5]
print(sorted(mixed))
print(sorted(mixed, reverse=True))
print(min(mixed), max(mixed))
