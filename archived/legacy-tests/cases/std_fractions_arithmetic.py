# probes: Fraction keeps exact rational arithmetic
# expect:
# 5/6
from fractions import Fraction

print(Fraction(1, 2) + Fraction(1, 3))
