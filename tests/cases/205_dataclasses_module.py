# expect:
# 3
# 4
# 0

from dataclasses import dataclass, field

@dataclass
class Point:
    x: int = 0
    y: int = 0

p = Point(3, 4)
print(p.x)
print(p.y)
q = Point()
print(q.x)
