# probes: date - date yields a timedelta in days
# expect:
# 29
import datetime

delta = datetime.date(2020, 3, 1) - datetime.date(2020, 2, 1)
print(delta.days)
