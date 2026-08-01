# probes: a format spec applies to a Fraction
# expect:
# 0.333
from fractions import Fraction

print(format(Fraction(1, 3), ".3f"))
