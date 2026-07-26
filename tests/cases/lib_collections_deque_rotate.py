# expect:
# [4, 1, 2, 3]
from collections import deque
q = deque([1, 2, 3, 4])
q.rotate(1)
print(list(q))
# asmpython (beta/3.14.0) rejects at compile: [E022] list() requires a list, tuple, dict, or string
