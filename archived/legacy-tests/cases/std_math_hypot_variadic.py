# probes: math.hypot accepts more than two coordinates
# expect:
# 5.0
# 3.0
import math

print(math.hypot(3.0, 4.0))
print(math.hypot(1.0, 2.0, 2.0))
