# probes: Fraction.limit_denominator approximates
# expect:
# 311/99
from fractions import Fraction

print(Fraction(3141592, 1000000).limit_denominator(100))
