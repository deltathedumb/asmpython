# tier: spec
# ref: library/collections.html#collections.deque
# expect:
# [0, 1, 2, 3, 4]
# 0 4
# [3, 1, 2]
# [2, 3]
from collections import deque

d = deque([1, 2, 3])
d.appendleft(0)
d.append(4)
print(list(d))
print(d.popleft(), d.pop())
d.rotate(1)
print(list(d))
bounded = deque(maxlen=2)
bounded.extend([1, 2, 3])
print(list(bounded))
