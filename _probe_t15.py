# Dataclasses
from dataclasses import dataclass, field

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
class Config:
    name: str
    values: list = field(default_factory=list)
    max_size: int = 100

c = Config("test")
print(c.name)
print(c.max_size)
