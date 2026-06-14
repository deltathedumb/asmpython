# expect:
# 0
# 1
# 0

from threading import Lock, Event, active_count

print(active_count() - 1)

lk = Lock()
print(lk.acquire())
lk.release()
print(lk.locked())
