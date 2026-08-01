# tier: spec
# ref: library/zoneinfo.html
# expect:
# no-tzdata
import datetime
from zoneinfo import ZoneInfo

try:
    tz = ZoneInfo("UTC")
except Exception:
    print("no-tzdata")
else:
    d = datetime.datetime(2020, 6, 1, 12, 0, tzinfo=tz)
    print(d.tzname())
    print(d.utcoffset().total_seconds())
    print(d.isoformat())
