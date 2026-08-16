# expect:
# 25
# 97

class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x: int = x
        self.y: int = y

    def __abs__(self) -> int:
        return self.x * self.x + self.y * self.y

    def __hash__(self) -> int:
        return self.x * 31 + self.y

p = Point(3, 4)
print(abs(p))
print(hash(p))
