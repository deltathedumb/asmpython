# expect:
# 9
from datetime import date
d1 = date(2020, 1, 1)
d2 = date(2020, 1, 10)
print((d2 - d1).days)
# asmpython (beta/3.14.0) runtime failure: exit 0xc0000005
