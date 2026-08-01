# tier: spec
# ref: reference/simple_stmts.html#the-import-statement
# expect:
# 3.14159
# 3.14159
# 3.14159 3.14159
# True
import math
import math as m
from math import pi
from math import pi as PI

print(round(math.pi, 5))
print(round(m.pi, 5))
print(round(pi, 5), round(PI, 5))
print(math is m)
