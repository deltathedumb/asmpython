# probes: cmath.sqrt handles a negative real
# expect:
# 1j
import cmath

print(cmath.sqrt(-1))
