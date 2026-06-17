# expect:
# 2024-03-15
# 2024-03-16
# Friday
# 2024-03-15T10:30:00
# 10:30:00
# 10:31:00
# 86400.0
# 2024-03-14T10:30:00

from datetime import date, time, datetime, timedelta

d = date(2024, 3, 15)
print(d.isoformat())

d2 = d.__add__(timedelta(days=1))
print(d2.isoformat())

print(d.strftime("%A"))

dt = datetime(2024, 3, 15, 10, 30, 0)
print(dt.isoformat())

t = time(10, 30, 0)
print(t.isoformat())

t2 = t.replace(minute=31)
print(t2.isoformat())

td = timedelta(days=1)
print(td.total_seconds())

neg: timedelta = timedelta(days=-1)
dt2 = dt.__add__(neg)
print(dt2.isoformat())
