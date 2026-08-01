# probes: queue.LifoQueue reverses the order
# expect:
# b
import queue

q = queue.LifoQueue()
q.put("a")
q.put("b")
print(q.get())
