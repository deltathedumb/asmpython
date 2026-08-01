# tier: spec
# ref: library/math.html#math.tau
# expect:
# True
# 6.28319
# True
# float
import math

print(round(math.tau, 10) == round(2 * math.pi, 10))
print(round(math.tau, 5))
print(math.tau > math.pi)
print(type(math.tau).__name__)
