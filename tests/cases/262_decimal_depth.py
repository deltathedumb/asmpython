# expect:
# 3
# -2
# 0
# 1
# 1
# 0
# 4

import decimal

d1 = decimal.Decimal("3")
d2 = decimal.Decimal("-2")
print(d1.__int__())
print(d2.__int__())

d3 = decimal.Decimal("0")
print(d3.__int__())
print(d3.is_zero())

d4 = decimal.Decimal("1")
d5 = decimal.Decimal("3")
d6 = d4.__add__(d5)
print(d4.__int__())
print(d4.is_zero())
print(d6.__int__())
