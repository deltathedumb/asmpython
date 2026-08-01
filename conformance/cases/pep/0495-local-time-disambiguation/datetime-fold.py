# tier: spec
# ref: library/datetime.html
# expect:
# 0
# 1
# True
# 2020-01-01T12:00:00
# 0.0
import datetime

d = datetime.datetime(2020, 1, 1, 12, 0)
print(d.fold)
folded = d.replace(fold=1)
print(folded.fold)
print(d == folded)
print(d.isoformat())
print((folded - d).total_seconds())
