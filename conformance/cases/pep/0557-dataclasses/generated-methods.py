# tier: spec
# ref: library/dataclasses.html
# expect:
# P(x=1, y=0, tags=[])
# True
# 1 0 []
# ['t'] []
from dataclasses import dataclass, field

@dataclass
class P:
    x: int
    y: int = 0
    tags: list = field(default_factory=list)

a = P(1)
b = P(1)
print(a)
print(a == b)
print(a.x, a.y, a.tags)
a.tags.append("t")
print(a.tags, b.tags)
