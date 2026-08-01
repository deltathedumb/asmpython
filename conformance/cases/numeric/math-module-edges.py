# tier: spec
# ref: library/math.html
# expect:
# -3 -2
# -2 2
# 6 12
# 4
# 120
# 1.0
# True True
import math

print(math.floor(-2.5), math.ceil(-2.5))
print(math.trunc(-2.5), math.trunc(2.5))
print(math.gcd(12, 18), math.lcm(4, 6))
print(math.isqrt(17))
print(math.factorial(5))
print(round(math.log(math.e), 10))
print(math.inf > 0, math.isnan(math.nan))
