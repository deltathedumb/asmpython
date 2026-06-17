# expect:
# 3
# 0
# 1
# 2

import queue

q = queue.Queue()
q.put(1)
q.put(2)
q.put(3)
print(q.qsize())
print(q.empty())
print(q.get())
print(q.get())
