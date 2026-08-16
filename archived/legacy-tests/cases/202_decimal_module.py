# expect:
# 5.14
# 1.14
# 6.28
# 5.000000000000000
# True
# 0.3
# 7

from decimal import Decimal

a = Decimal('3.14')
b = Decimal('2')
print(a + b)
print(a - b)
print(a * b)
print(Decimal('10') / Decimal('2'))
print(Decimal('1.5') > Decimal('1.0'))
print(Decimal('0.1') + Decimal('0.2'))
print(Decimal('7.8').__int__())
