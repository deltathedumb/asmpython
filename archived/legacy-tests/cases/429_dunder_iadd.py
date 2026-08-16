# expect:
# (4, 6)
# (-2, -2)
# (3, 6)
# (9, 12)
# (-1, -2)
# True
# False
# (4, 6)
# (3, 4)

class Vector:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def __add__(self, other: "Vector") -> "Vector":
        return Vector(self.x + other.x, self.y + other.y)

    def __iadd__(self, other: "Vector") -> "Vector":
        self.x += other.x
        self.y += other.y
        return self

    def __sub__(self, other: "Vector") -> "Vector":
        return Vector(self.x - other.x, self.y - other.y)

    def __mul__(self, scalar: int) -> "Vector":
        return Vector(self.x * scalar, self.y * scalar)

    def __rmul__(self, scalar: int) -> "Vector":
        return Vector(self.x * scalar, self.y * scalar)

    def __neg__(self) -> "Vector":
        return Vector(-self.x, -self.y)

    def __str__(self) -> str:
        return f"({self.x}, {self.y})"

    def __eq__(self, other: "Vector") -> bool:
        return self.x == other.x and self.y == other.y

v1 = Vector(1, 2)
v2 = Vector(3, 4)

print(str(v1 + v2))
print(str(v1 - v2))
print(str(v1 * 3))
print(str(3 * v2))
print(str(-v1))
print(v1 == Vector(1, 2))
print(v1 == v2)

v1 += v2
print(str(v1))

v3 = Vector(0, 0)
for v in [Vector(1, 0), Vector(0, 1), Vector(2, 3)]:
    v3 += v
print(str(v3))
