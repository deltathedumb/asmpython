# probes: a Lock works as a context manager
# expect:
# inside
# False
import threading

lock = threading.Lock()
with lock:
    print("inside")
print(lock.locked())
