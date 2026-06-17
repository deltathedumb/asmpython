# expect:
# 3.0
# 4.0
# 5.0
# 6.0
# 8.0
# 10.0

import math


class Vector:
    def __init__(self, x: float, y: float) -> None:
        self.x = x
        self.y = y

    def length(self) -> float:
        return math.sqrt(self.x * self.x + self.y * self.y)

    def scale(self, factor: float) -> None:
        self.x *= factor
        self.y *= factor


v = Vector(3.0, 4.0)
print(v.x)
print(v.y)
print(v.length())
v.scale(2.0)
print(v.x)
print(v.y)
print(v.length())
