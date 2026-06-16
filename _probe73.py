# probe73: dataclass-style patterns, __repr__, __hash__, __lt__/__gt__ for sorting
class Point:
    def __init__(self, x: int, y: int) -> None:
        self.x = x
        self.y = y

    def __repr__(self) -> str:
        return "Point(" + str(self.x) + ", " + str(self.y) + ")"

    def __eq__(self, other: "Point") -> bool:
        return self.x == other.x and self.y == other.y

    def __lt__(self, other: "Point") -> bool:
        if self.x != other.x:
            return self.x < other.x
        return self.y < other.y

    def dist_sq(self) -> int:
        return self.x * self.x + self.y * self.y

p1 = Point(1, 2)
p2 = Point(3, 4)
p3 = Point(1, 2)

print(str(p1))       # Point(1, 2)
print(p1 == p3)      # True
print(p1 == p2)      # False
print(p1 < p2)       # True
print(p2 < p1)       # False
print(p1.dist_sq())  # 5
print(p2.dist_sq())  # 25

# Sort list of points
pts = [Point(3, 1), Point(1, 5), Point(1, 2), Point(2, 0)]
pts.sort()
print(str(pts[0]))   # Point(1, 2)
print(str(pts[1]))   # Point(1, 5)
print(str(pts[2]))   # Point(2, 0)
print(str(pts[3]))   # Point(3, 1)
