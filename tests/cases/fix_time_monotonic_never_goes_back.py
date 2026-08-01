# probes: monotonic() never decreases
# expect:
# True
import time

first = time.monotonic()
second = time.monotonic()
print(second >= first)
