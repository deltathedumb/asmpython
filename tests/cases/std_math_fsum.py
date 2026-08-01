# probes: math.fsum sums without accumulating float error
# expect:
# 1.0
import math

print(math.fsum([0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1]))
