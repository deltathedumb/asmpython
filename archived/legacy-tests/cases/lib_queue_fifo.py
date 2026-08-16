# expect:
# 1 2
import queue
q = queue.Queue()
q.put(1)
q.put(2)
print(q.get(), q.get())
# asmpython (beta/3.14.0) MISMATCH: prints '0 0\n' (wrong).
