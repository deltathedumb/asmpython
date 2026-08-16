# expect:
# 3 4
from collections import namedtuple
Point = namedtuple('Point', 'x y')
p = Point(3, 4)
x, y = p
print(x, y)
# asmpython (beta/3.14.0) rejects at compile: [E021] type() takes 1 argument(s), got 3
