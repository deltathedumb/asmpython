# probes: math.isclose accepts rel_tol/abs_tol keywords
# expect:
# True
# False
# True
import math

print(math.isclose(1.0, 1.000001, rel_tol=1e-3))
print(math.isclose(1.0, 1.5, rel_tol=1e-3))
print(math.isclose(0.0, 1e-9, abs_tol=1e-6))
