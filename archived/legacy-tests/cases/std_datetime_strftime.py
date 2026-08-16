# probes: datetime.strftime honours its format string
# expect:
# 2021/07/04 13:05:09
import datetime

print(datetime.datetime(2021, 7, 4, 13, 5, 9).strftime("%Y/%m/%d %H:%M:%S"))
