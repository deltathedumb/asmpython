# expect:
# 4
# 6
# 2
# 2
# 3
# 6

class Vec:
    def __init__(self, x: int, y: int) -> None:
        self.x: int = x
        self.y: int = y

    def __add__(self, other: Vec) -> Vec:
        return Vec(self.x + other.x, self.y + other.y)

    def __sub__(self, other: Vec) -> Vec:
        return Vec(self.x - other.x, self.y - other.y)

    def __mul__(self, n: int) -> Vec:
        return Vec(self.x * n, self.y * n)

a = Vec(1, 2)
b = Vec(3, 4)
c = a + b
print(c.x)
print(c.y)
d = b - a
print(d.x)
print(d.y)
e = a * 3
print(e.x)
print(e.y)
