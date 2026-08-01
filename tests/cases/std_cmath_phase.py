# probes: cmath.phase returns the argument angle
# expect:
# 1.5707963267948966
import cmath

print(cmath.phase(complex(0, 1)))
