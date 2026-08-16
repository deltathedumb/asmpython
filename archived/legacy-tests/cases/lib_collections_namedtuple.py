# expect:
# 1 2 1
from collections import namedtuple
Pt = namedtuple('Pt', ['x', 'y'])
p = Pt(1, 2)
print(p.x, p.y, p[0])
# asmpython (beta/3.14.0) rejects at compile: [E021] type() takes 1 argument(s), got 3
