# expect:
# 3
# Point(1, 2)
# origin: Point(0, 0)

class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    @staticmethod
    def add(a: int, b: int) -> int:
        return a + b

    @classmethod
    def origin(cls) -> "Point":
        return Point(0, 0)

    def __str__(self) -> str:
        return "Point(" + str(self.x) + ", " + str(self.y) + ")"

print(Point.add(1, 2))
p = Point(1, 2)
print(str(p))
o = Point.origin()
print("origin: " + str(o))
