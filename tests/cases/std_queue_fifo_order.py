# probes: queue.Queue is first-in first-out
# expect:
# a
# b
# True
import queue

q = queue.Queue()
q.put("a")
q.put("b")
print(q.get())
print(q.get())
print(q.empty())
