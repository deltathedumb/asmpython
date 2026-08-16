# probes: a format spec applies to a Decimal
# expect:
# 2.500
# 1,234.5
from decimal import Decimal

print(format(Decimal("2.5"), ".3f"))
print(format(Decimal("1234.5"), ","))
