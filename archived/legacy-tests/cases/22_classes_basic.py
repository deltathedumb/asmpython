# expect:
# 3
# 4
# 7
# 25
class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def sum(self):
        return self.x + self.y

    def dist_sq(self):
        return self.x * self.x + self.y * self.y

p = Point(3, 4)
print(p.x)
print(p.y)
print(p.sum())
print(p.dist_sq())
