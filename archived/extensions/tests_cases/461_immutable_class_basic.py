# ext: immutable
# expect:
# 3
# 4

@immutable
class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

p = Point(3, 4)
print(p.x)
print(p.y)
