from dataclasses import dataclass

@dataclass
class IntPair:
    x: int
    y: int

p = IntPair(3, 4)
print(p.x)
print(p.y)
