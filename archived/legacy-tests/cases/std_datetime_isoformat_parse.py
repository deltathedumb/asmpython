# probes: date.fromisoformat inverts isoformat
# expect:
# 2020-02-29
import datetime

print(datetime.date.fromisoformat("2020-02-29").isoformat())
