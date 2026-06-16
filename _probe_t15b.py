from dataclasses import dataclass

@dataclass
class Point:
    x: float
    y: float

p = Point(3.0, 4.0)
print(p.x)
print(p.y)

q = Point(1.0, 2.0)
print(q.x)
print(q.y)
