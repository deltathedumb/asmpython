# tier: spec
# ref: library/fractions.html
# expect:
# 1/2
# True
# False
# True
# 3/2
# 0.25
# True
from fractions import Fraction

print(Fraction(1, 3) + Fraction(1, 6))
print(Fraction(1, 3) * 3 == 1)
print(0.1 + 0.2 == 0.3)
print(Fraction("0.1") + Fraction("0.2") == Fraction("0.3"))
print(Fraction(6, 4))
print(float(Fraction(1, 4)))
print(Fraction(1, 2) < Fraction(2, 3))
