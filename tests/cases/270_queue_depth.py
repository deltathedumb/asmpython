# expect:
# 0
# 3
# 0
# 0
# 5
# 10
# 20

import queue

q = queue.Queue()
print(q.qsize())
q.put(5)
q.put(10)
q.put(20)
print(q.qsize())
print(q.empty())
print(q.full())
print(q.get())
print(q.get())
print(q.get())
