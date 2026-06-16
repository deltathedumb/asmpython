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

    def __str__(self) -> str:
        return f"({self.x}, {self.y})"

v1 = Vector(1, 2)
v2 = Vector(3, 4)
print(str(v1 + v2))    # (4, 6)
v1 += v2
print(str(v1))   # (4, 6)
