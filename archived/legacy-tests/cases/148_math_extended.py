# expect:
# True
# False
# True
# True
# False
# True
# False
# False
# 180.0
# 3.141592653589793
# 4
# 5
# 12
# 120
# 1
# 120
# 20
# 2.0
# 8.0
# 0.75
# 3.0
# 0.5
# 4
# 0.842701
# 24.0

import math

print(math.isnan(float("nan")))
print(math.isnan(1.0))
print(math.isinf(float("inf")))
print(math.isinf(-float("inf")))
print(math.isinf(1.0))
print(math.isfinite(1.0))
print(math.isfinite(float("nan")))
print(math.isfinite(float("inf")))

print(math.degrees(math.pi))
print(math.radians(180.0))

print(math.gcd(12, 8))
print(math.gcd(0, 5))
print(math.lcm(4, 6))
print(math.factorial(5))
print(math.factorial(0))
print(math.comb(10, 3))
print(math.perm(5, 2))

print(round(math.log_base(math.e * math.e, math.e), 10))
print(math.ldexp(1.0, 3))

print(math.modf_frac(3.75))
print(math.modf_int(3.75))
print(math.frexp_mantissa(8.0))
print(math.frexp_exponent(8.0))

print(round(math.erf(1.0), 6))
print(round(math.gamma(5.0), 1))
