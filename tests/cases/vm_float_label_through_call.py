# probes: a float keeps its register class into a call
# expect:
# [1.5, 2.5, 3.25]
# [1, 2, -4]
# ['1.5', '2.5', '-3.25']
# [1.5, 2.5, -3.2]
# 4.0
# 2.5
import math

vals = [1.5, 2.5, -3.25]
print([abs(v) for v in vals])
print([math.floor(v) for v in vals])
print([str(v) for v in vals])
print([round(v, 1) for v in vals])
print(math.sqrt(16))
print(abs(-2.5))
