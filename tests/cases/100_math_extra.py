# expect:
# 3
# -5.0
# 9.0
# 2.0
# 1024.0
# 5.0
# 60.0
import math
import time
print(math.trunc(3.7))
print(math.copysign(5.0, -1.0))
print(math.fmax(2.0, 9.0))
print(math.fmin(2.0, 9.0))
print(math.exp2(10.0))
print(math.hypot(3.0, 4.0))
print(time.difftime(100, 40))
