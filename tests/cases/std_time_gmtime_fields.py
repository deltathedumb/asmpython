# probes: struct_time exposes named fields
# expect:
# 1970
# 1
# 1
import time

t = time.gmtime(0)
print(t.tm_year)
print(t.tm_mon)
print(t.tm_mday)
