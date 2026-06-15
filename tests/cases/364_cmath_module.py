# expect:
# 1
# 1

import cmath

r: int = 1 if cmath.pi > 3.14 else 0
s: int = 1 if cmath.e > 2.71 else 0
print(r)
print(s)
