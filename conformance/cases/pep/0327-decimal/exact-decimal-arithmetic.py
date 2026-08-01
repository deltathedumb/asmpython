# tier: spec
# ref: library/decimal.html
# expect:
# 0.3
# True
# False
# 0.3333333333333333333333333333
# 0.33333
from decimal import Decimal, getcontext

print(Decimal("0.1") + Decimal("0.2"))
print(Decimal("0.1") + Decimal("0.2") == Decimal("0.3"))
print(0.1 + 0.2 == 0.3)
print(Decimal(1) / Decimal(3))
getcontext().prec = 5
print(Decimal(1) / Decimal(3))
