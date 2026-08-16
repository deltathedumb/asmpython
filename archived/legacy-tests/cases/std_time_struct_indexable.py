# probes: struct_time is indexable like a tuple
# expect:
# 1970
# 2
import time

t = time.gmtime(86400)
print(t[0])
print(t[2])
