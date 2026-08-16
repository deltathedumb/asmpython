# expect:
# 5
# 25
# Square area = 25
# Rectangle area = 24
class Shape:
    def __init__(self, name):
        self.name = name

    def area(self):
        # Default implementation; subclasses override.
        return 0

class Square(Shape):
    def __init__(self, side):
        self.side = side

    def area(self):
        return self.side * self.side

class Rectangle(Shape):
    def __init__(self, w, h):
        self.w = w
        self.h = h

    def area(self):
        return self.w * self.h

sq = Square(5)
print(sq.side)
print(sq.area())

# Static dispatch: `sq.area()` resolves to Square.area at compile time.
total = sq.area()
print("Square area =", total)

r = Rectangle(4, 6)
print("Rectangle area =", r.area())
