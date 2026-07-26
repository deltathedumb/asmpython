# expect:
# [0, 1, 2, 3, 4]
from collections import deque
q = deque([1, 2, 3])
q.appendleft(0)
q.append(4)
print(list(q))
# asmpython (beta/3.14.0) rejects at compile: [E022] list() requires a list, tuple, dict, or string
