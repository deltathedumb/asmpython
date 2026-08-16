# expect:
# -1
# -2
# 1
# 2

class Vec:
    def __init__(self, x: int, y: int) -> None:
        self.x: int = x
        self.y: int = y

    def __neg__(self) -> Vec:
        return Vec(-self.x, -self.y)

    def __pos__(self) -> Vec:
        return Vec(self.x, self.y)

a = Vec(1, 2)
n = -a
print(n.x)
print(n.y)
p = +a
print(p.x)
print(p.y)
