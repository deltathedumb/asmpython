# probe129: __iadd__, __isub__, __imul__ (in-place operators)
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

print(str(v1 + v2))    # (4, 6)
print(str(v1 - v2))    # (-2, -2)
print(str(v1 * 3))     # (3, 6)
print(str(3 * v2))     # (9, 12)
print(str(-v1))        # (-1, -2)
print(v1 == Vector(1, 2))  # True
print(v1 == v2)            # False

v1 += v2
print(str(v1))   # (4, 6)

v3 = Vector(0, 0)
for v in [Vector(1, 0), Vector(0, 1), Vector(2, 3)]:
    v3 += v
print(str(v3))   # (3, 4)
