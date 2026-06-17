# expect:
# 2024-01-15
# 2024-01-16
# 0
# 2024-01-15T10:30:00
# 10:30:00

from datetime import date, time, datetime, timedelta

d = date(2024, 1, 15)
print(d)

d2 = d.__add__(timedelta(days=1))
print(d2)

print(d.weekday())

dt = datetime(2024, 1, 15, 10, 30, 0)
print(dt)

t = time(10, 30, 0)
print(t)
