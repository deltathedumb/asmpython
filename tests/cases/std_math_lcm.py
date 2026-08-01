# probes: math.lcm exists and is variadic
# expect:
# 12
# 12
import math

print(math.lcm(4, 6))
print(math.lcm(2, 3, 4))
