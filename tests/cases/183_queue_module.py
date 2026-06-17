# expect:
# 0
# 3
# 10
# 20
# 30

from queue import Queue, PriorityQueue

q = Queue()
print(q.qsize())
q.put(10)
q.put(20)
q.put(30)
print(q.qsize())
print(q.get())
print(q.get())
print(q.get())
