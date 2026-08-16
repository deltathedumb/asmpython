# probes: date.isoformat renders YYYY-MM-DD
# expect:
# 2020-01-02
import datetime

print(datetime.date(2020, 1, 2).isoformat())
