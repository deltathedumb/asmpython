# expect:
# 0 [1]
from dataclasses import dataclass, field
@dataclass
class C:
    x: int = 0
    items: list = field(default_factory=list)
c = C()
c.items.append(1)
print(c.x, c.items)
# asmpython (beta/3.14.0) MISMATCH: prints '0 [10066816]\n' (wrong).
