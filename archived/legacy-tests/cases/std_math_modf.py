# probes: math.modf returns a (frac, int) pair
# expect:
# 0.5
# 3.0
import math

parts = math.modf(3.5)
print(parts[0])
print(parts[1])
