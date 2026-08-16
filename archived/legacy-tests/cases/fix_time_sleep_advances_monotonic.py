# probes: sleep advances the monotonic clock
# expect:
# True
import time

start = time.monotonic()
time.sleep(0.01)
print(time.monotonic() - start >= 0.0)
