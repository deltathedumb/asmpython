# expect:
# 1
# 2
# 5
# 4
# -1
# 4

import fractions

f = fractions.Fraction(1, 2)
print(f.numerator)
print(f.denominator)

g = fractions.Fraction(3, 4)
h = f.__add__(g)
print(h.numerator)
print(h.denominator)

d = f.__sub__(g)
print(d.numerator)
print(d.denominator)
