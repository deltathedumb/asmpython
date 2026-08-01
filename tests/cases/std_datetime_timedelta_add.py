# probes: date + timedelta advances the date
# expect:
# 2021-01-01
import datetime

print((datetime.date(2020, 12, 31) + datetime.timedelta(days=1)).isoformat())
