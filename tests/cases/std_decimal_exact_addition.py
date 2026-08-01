# probes: Decimal addition is exact where float is not
# expect:
# 0.3
# True
from decimal import Decimal

print(Decimal("0.1") + Decimal("0.2"))
print(Decimal("0.1") + Decimal("0.2") == Decimal("0.3"))
