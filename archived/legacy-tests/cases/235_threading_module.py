# expect:
# 1
# 0
# 1
# 0

import threading

lk = threading.Lock()
print(lk.acquire())
print(lk.locked() - 1)
lk.release()
print(lk.locked() + 1)
e = threading.Event()
e.set()
print(e.is_set() - 1)
