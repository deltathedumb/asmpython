# tier: spec
# ref: library/stdtypes.html#typesnumeric
# expect:
# -0.0
# True
# -1.0
# 1.0
# 0.0 -0.0
# -0.0
import math

z = -0.0
print(z)
print(z == 0.0)
print(math.copysign(1, z))
print(math.copysign(1, 0.0))
print(str(0.0), str(-0.0))
print(repr(-0.0))
