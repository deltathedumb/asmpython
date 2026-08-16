# probes: date.weekday is Monday-zero
# expect:
# 0
import datetime

print(datetime.date(2020, 1, 6).weekday())
