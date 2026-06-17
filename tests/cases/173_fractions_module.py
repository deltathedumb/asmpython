# expect:
# 1/2
# 3/4
# 5/4
# -1/4
# 3/8
# 2/3
# 0
# True

from fractions import Fraction

a = Fraction(1, 2)
b = Fraction(3, 4)
print(a)
print(b)
print(a._add(b))
print(a._sub(b))
print(a._mul(b))
print(a._truediv(b))
print(a.eq_int(0))
print(a.eq_int(0) == 0)
