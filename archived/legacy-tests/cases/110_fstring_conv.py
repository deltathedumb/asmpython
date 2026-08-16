# expect:
# 'Bob'
# Bob
# 5
# Point(1, 2)
# Point(1, 2)
# Bob is 5

class Point:
    def __init__(self, x, y):
        self.x = x
        self.y = y

    def __repr__(self):
        return "Point(" + str(self.x) + ", " + str(self.y) + ")"


name = "Bob"
n = 5
print(f"{name!r}")
print(f"{name!s}")
print(f"{n!r}")
p = Point(1, 2)
print(f"{p!r}")
print(f"{p!a}")
print(f"{name} is {n!r}")
