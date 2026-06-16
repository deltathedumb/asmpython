class Vector:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def __neg__(self) -> "Vector":
        return Vector(-self.x, -self.y)

    def __str__(self) -> str:
        return f"({self.x}, {self.y})"

v1 = Vector(1, 2)
print(str(-v1))   # (-1, -2)
