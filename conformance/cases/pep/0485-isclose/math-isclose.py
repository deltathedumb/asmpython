# tier: spec
# ref: library/math.html#math.isclose
# expect:
# True
# False
# True
# False
# True
import math

print(math.isclose(0.1 + 0.2, 0.3))
print(0.1 + 0.2 == 0.3)
print(math.isclose(1, 1.000001, rel_tol=1e-5))
print(math.isclose(1, 2))
print(math.isclose(0, 1e-12, abs_tol=1e-9))
