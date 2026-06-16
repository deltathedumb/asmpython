class Vector:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def __mul__(self, scalar: int) -> "Vector":
        return Vector(self.x * scalar, self.y * scalar)

    def __rmul__(self, scalar: int) -> "Vector":
        return Vector(self.x * scalar, self.y * scalar)

    def __str__(self) -> str:
        return f"({self.x}, {self.y})"

v1 = Vector(1, 2)
v2 = Vector(3, 4)
print(str(v1 * 3))      # (3, 6)
print(str(3 * v2))      # (9, 12)
