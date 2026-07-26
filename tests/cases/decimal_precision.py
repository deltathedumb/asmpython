# expect:
# 0.3333
from decimal import Decimal, getcontext
getcontext().prec = 4
print(Decimal(1) / Decimal(3))
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
