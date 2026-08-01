# probes: Decimal.quantize rounds to a fixed exponent
# expect:
# 2.34
from decimal import Decimal

print(Decimal("2.345").quantize(Decimal("0.01")))
