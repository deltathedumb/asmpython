# expect:
# 1
from collections import namedtuple
P = namedtuple('P', 'x y')
p = P(1, 2)
print(p._asdict()['x'])
# asmpython (beta/3.14.0) rejects at compile: [E021] type() takes 1 argument(s), got 3
