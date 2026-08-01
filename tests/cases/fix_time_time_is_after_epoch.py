# probes: time() is a positive offset from the epoch
# expect:
# True
import time

print(time.time() > 1000000000.0)
