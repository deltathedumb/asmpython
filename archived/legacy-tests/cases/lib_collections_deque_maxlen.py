# expect:
# [2, 3]
from collections import deque
d = deque(maxlen=2)
d.append(1)
d.append(2)
d.append(3)
print(list(d))
# asmpython (beta/3.14.0) rejects at compile: [E021] deque() got an unexpected keyword argument 'maxlen'
