# probe39: typing/annotation features, dataclass
from dataclasses import dataclass

@dataclass
class Point:
    x: int
    y: int

p = Point(3, 4)
print(p.x)   # 3
print(p.y)   # 4

@dataclass
class Rectangle:
    p1: Point
    p2: Point

    def area(self) -> int:
        dx = self.p2.x - self.p1.x
        dy = self.p2.y - self.p1.y
        return dx * dy

r = Rectangle(Point(0, 0), Point(5, 3))
print(r.area())   # 15

# dataclass with default
@dataclass
class Config:
    width: int = 80
    height: int = 24
    name: str = "default"

c1 = Config()
c2 = Config(100, 50, "custom")
print(c1.width)    # 80
print(c2.name)     # custom
