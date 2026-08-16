# expect:
# 5/6
# 1/6
# 1/6
# 3/2
# -1/2
# 3/4
# 4/9
from fractions import Fraction

a = Fraction(1, 2)
b = Fraction(1, 3)
c = a + b
print(c)
d = a - b
print(d)
e = a * b
print(e)
f = a / b
print(f)
g = -a
print(g)
print(abs(Fraction(-3, 4)))
print(Fraction(2, 3) ** 2)
