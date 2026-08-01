# probes: time.strftime formats a struct_time
# expect:
# 1970-01-01
import time

print(time.strftime("%Y-%m-%d", time.gmtime(0)))
