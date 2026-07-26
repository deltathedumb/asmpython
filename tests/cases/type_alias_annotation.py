# expect:
# 6.0
from typing import List
Vector = List[float]
def f(v: Vector) -> float:
    return sum(v)
print(f([1.0, 2.0, 3.0]))
# asmpython (beta/3.14.0) runtime failure: exit 0x1
