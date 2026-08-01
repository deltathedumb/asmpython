# tier: spec
# ref: library/collections.html#collections.namedtuple
# expect:
# P(x=1, y=2) 1 2
# {'x': 1, 'y': 2}
# P(x=9, y=2)
# True 2
# ('x', 'y')
# 1 2
from collections import namedtuple

P = namedtuple("P", "x y")
p = P(1, 2)
print(p, p.x, p[1])
print(p._asdict())
print(p._replace(x=9))
print(isinstance(p, tuple), len(p))
print(P._fields)
a, b = p
print(a, b)
