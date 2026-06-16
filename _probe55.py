# probe55: class special methods
class Vector:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def __add__(self, other: "Vector") -> "Vector":
        return Vector(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vector") -> "Vector":
        return Vector(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: int) -> "Vector":
        return Vector(self.x * scalar, self.y * scalar)

    def __eq__(self, other: "Vector") -> bool:
        return self.x == other.x and self.y == other.y

    def __str__(self) -> str:
        return f"({self.x}, {self.y})"

    def magnitude_sq(self) -> int:
        return self.x * self.x + self.y * self.y

a = Vector(1, 2)
b = Vector(3, 4)

c = a + b
print(c)   # (4, 6)

d = b - a
print(d)   # (2, 2)

e = a * 3
print(e)   # (3, 6)

print(a == a)  # True
print(a == b)  # False

print(a.magnitude_sq())  # 5
