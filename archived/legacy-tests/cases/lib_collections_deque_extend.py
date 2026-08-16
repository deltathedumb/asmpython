# expect:
# [-1, 0, 1, 2]
from collections import deque
d = deque([1, 2])
d.extendleft([0, -1])
print(list(d))
# asmpython (beta/3.14.0) rejects at compile: [E022] list() requires a list, tuple, dict, or string
