# probe: more patterns

# 1. __repr__ / __str__ on classes
class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y
    def __repr__(self) -> str:
        return "Point(" + str(self.x) + ", " + str(self.y) + ")"

p = Point(3, 4)
print(repr(p))
print(str(p))

# 2. __eq__ on classes
class Vec:
    def __init__(self, x: int) -> None:
        self.x = x
    def __eq__(self, other: "Vec") -> bool:
        return self.x == other.x
    def __add__(self, other: "Vec") -> "Vec":
        return Vec(self.x + other.x)

v1 = Vec(5)
v2 = Vec(5)
v3 = Vec(6)
print(v1 == v2)
print(v1 == v3)
v4 = v1 + v3
print(v4.x)

# 3. Class with list field
class Stack:
    def __init__(self) -> None:
        self.items: list[int] = []
    def push(self, x: int) -> None:
        self.items.append(x)
    def pop(self) -> int:
        return self.items.pop()
    def size(self) -> int:
        return len(self.items)

s = Stack()
s.push(10)
s.push(20)
s.push(30)
print(s.size())
print(s.pop())
print(s.size())
