# expect:
# 2024
# 6
# 15
# 3
# 2024-06-15
# 2024-06-18

import datetime

d = datetime.date(2024, 6, 15)
print(d.year)
print(d.month)
print(d.day)

td = datetime.timedelta(days=3)
print(td.days)
print(d.isoformat())

d2 = d.__add__(td)
print(d2.isoformat())
