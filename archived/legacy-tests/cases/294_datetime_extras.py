# expect:
# 2023-01-15
# 2023-01-15 12:30:00
# 2023-01-15T12:30:00

from datetime import date, datetime, timedelta

d = date(2023, 1, 15)
print(d.fromisoformat("2023-01-15").isoformat())

dt = datetime(2023, 1, 15, 12, 30, 0)
print(str(dt.year) + "-" + str(dt.month).zfill(2) + "-" + str(dt.day).zfill(2) + " " +
      str(dt.hour).zfill(2) + ":" + str(dt.minute).zfill(2) + ":" + str(dt.second).zfill(2))

dt2 = datetime(0, 0, 0).fromisoformat("2023-01-15T12:30:00")
print(dt2.isoformat())
