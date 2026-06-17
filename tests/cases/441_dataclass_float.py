# expect:
# 3.0
# 4.0
# 5.0
# 0.0
# 10.0

from dataclasses import dataclass

@dataclass
class Point:
    x: float
    y: float

    def distance(self) -> float:
        return (self.x * self.x + self.y * self.y) ** 0.5

p = Point(3.0, 4.0)
print(p.x)
print(p.y)
print(p.distance())

@dataclass
class Vector:
    x: float = 0.0
    y: float = 0.0

v = Vector()
print(v.x)
v2 = Vector(10.0, 0.0)
print(v2.x)
