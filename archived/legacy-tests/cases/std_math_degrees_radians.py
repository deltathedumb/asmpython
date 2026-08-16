# probes: math.degrees/radians convert angles
# expect:
# 180.0
# True
import math

print(math.degrees(math.pi))
print(round(math.radians(180.0), 6) == round(math.pi, 6))
